"""Composite operations behind the MCP tools: batching, name resolution, trees, describe, PML eval.

Every operation prefers the newer Nexus routes (Batch, Element/Resolve, Element/Tree) and falls back
to the old ones when Nexus answers "Route Not Found" (an older DLL). The answer is remembered for a
few minutes so the probe is not repeated on every call.
"""

import re
import uuid
import time
from collections import Counter

from mcp.server.mcpserver.exceptions import ToolError

import nexus_client
import write_guard
from nexus_client import NexusError

MAX_BATCH = 50
_READ_PATHS = {
    "/api/Health",
    "/api/Session/Info",
    "/api/Element/Query",
    "/api/Element/Get",
    "/api/Element/Children",
    "/api/Element/AttributeList",
    "/api/Element/Resolve",
    "/api/Element/Tree",
}
_RECHECK_SECONDS = 300
_missing_routes: dict[str, float] = {}
_attribute_cache: dict[str, list[str]] = {}
_NAME = re.compile(r"^/?[\w.\-]+$")
_TYPE_ALIASES = {"EQUI": "EQUIPMENT"}


def _is_dbref(ref: str) -> bool:
    return ref.startswith("=")


def _route_known_missing(path: str) -> bool:
    return time.monotonic() - _missing_routes.get(path, -_RECHECK_SECONDS - 1) < _RECHECK_SECONDS


def _try_route(method: str, path: str, **kwargs):
    """Call a new route. Returns (True, data), or (False, None) if this Nexus lacks the route."""
    if _route_known_missing(path):
        return False, None
    try:
        return True, nexus_client.call(method, path, **kwargs)
    except NexusError as error:
        if error.route_missing:
            _missing_routes[path] = time.monotonic()
            return False, None
        raise


def canonical_type(type_: str) -> str:
    return _TYPE_ALIASES.get(type_.strip().upper(), type_.strip())


# --- batch ---------------------------------------------------------------------------------

def _check_request(item) -> dict:
    if not isinstance(item, dict):
        raise ToolError('Each request must be an object like {"method":"GET","path":"/api/Element/Children","query":{...}}.')
    method = str(item.get("method", "GET")).upper()
    path = item.get("path")
    if method not in ("GET", "POST") or path not in _READ_PATHS:
        raise ToolError(f"Batch only runs read routes ({', '.join(sorted(_READ_PATHS))}); got {method} {path}.")
    out = {"method": method, "path": path}
    for key in ("query", "body"):
        if item.get(key) is not None:
            out[key] = item[key]
    return out


def batch(requests: list[dict]) -> list[dict]:
    if not requests:
        raise ToolError("requests is empty.")
    if len(requests) > MAX_BATCH:
        raise ToolError(f"At most {MAX_BATCH} requests per batch (got {len(requests)}).")
    checked = [_check_request(r) for r in requests]
    ok, data = _try_route("POST", "/api/Batch", body={"requests": checked})
    if ok:
        return data
    results = []  # old Nexus: same result shape, one call each
    for item in checked:
        try:
            result = nexus_client.call(item["method"], item["path"], params=item.get("query"), body=item.get("body"))
            results.append({"Status": 200, "Data": result, "Error": None})
        except NexusError as error:
            results.append({"Status": error.status or 0, "Data": None, "Error": str(error)})
    return results


def _data(result: dict):
    """Data of a batch entry, or raise its error."""
    if result.get("Error") or (result.get("Status") or 200) >= 400:
        raise ToolError(result.get("Error") or f"Nexus status {result.get('Status')}")
    return result.get("Data")


# --- resolve -------------------------------------------------------------------------------

def _parse_resolve_log(log: str, names: list[str]) -> list[dict | None]:
    blocks: dict[int, dict] = {}
    current = None
    for line in log.splitlines():
        line = line.strip()
        if m := re.fullmatch(r"MK(\d+)", line):
            current = blocks.setdefault(int(m.group(1)), {})
        elif current is not None and (m := re.match(r"(Ref|Type)\s+(\S+)", line)):
            current[m.group(1)] = m.group(2)
    out = []
    for i, name in enumerate(names):
        b = blocks.get(i, {})
        out.append({"Name": name, "Dbref": b["Ref"], "Type": b.get("Type")} if "Ref" in b else None)
    return out


def resolve_names(names: list[str]) -> list[dict | None]:
    if not names:
        raise ToolError("names is empty.")
    names = [n if n.startswith("/") else "/" + n for n in names]
    bad = [n for n in names if not _NAME.match(n)]
    if bad:
        raise ToolError(f"Invalid element name(s): {bad[:3]}. Use plain names like /PIPE-1 (no spaces, quotes or wildcards).")
    ok, data = _try_route("POST", "/api/Element/Resolve", body={"names": names})
    if ok:
        return data
    lines = []  # old Nexus: ask E3D itself (needs a writable session, where PML can run)
    for i, n in enumerate(names):
        lines += [f"$P MK{i}", f"Q REF OF {n}", f"Q TYPE OF {n}"]
    try:
        log = write_guard.run_query_pml("\n".join(lines))["Log"]
    except NexusError as error:
        if error.status == 403:
            raise ToolError("This Nexus is too old for resolve_names and the read-only session cannot run PML. Use query_elements with name_like instead.") from None
        raise
    return _parse_resolve_log(log, names)


def _to_dbref(ref: str) -> str:
    if _is_dbref(ref):
        return ref
    found = resolve_names([ref])[0]
    if not found:
        raise ToolError(f"Element not found: {ref}")
    return found["Dbref"]


# --- tree ----------------------------------------------------------------------------------

def _same_type(a: str | None, b: str) -> bool:
    return bool(a) and canonical_type(a)[:4].upper() == canonical_type(b)[:4].upper()


def element_tree(ref: str, depth: int = 2, type: str | None = None, limit: int = 500) -> dict:
    depth = max(1, min(depth, 10))
    limit = max(1, min(limit, 5000))
    params = {"dbref": ref, "depth": depth, "limit": limit}
    if type:
        params["type"] = canonical_type(type)
    ok, data = _try_route("GET", "/api/Element/Tree", params=params)
    if ok:
        return data
    root = _to_dbref(ref)  # old Nexus: walk with Children, breadth first
    items, frontier, calls, truncated = [], [(root, 0)], 0, False
    while frontier and not truncated:
        node, level = frontier.pop(0)
        if level >= depth:
            continue
        if calls >= 200:
            truncated = True
            break
        calls += 1
        for child in nexus_client.call("GET", "/api/Element/Children", params={"dbref": node}).get("Children", []):
            if not type or _same_type(child.get("type"), type):
                if len(items) >= limit:
                    truncated = True
                    break
                items.append({"Dbref": child.get("dbref"), "Type": child.get("type"), "Name": child.get("name"),
                              "Depth": level + 1, "Owner": node})
            frontier.append((child.get("dbref"), level + 1))
    return {"Root": root, "Items": items, "Truncated": truncated}


# --- describe ------------------------------------------------------------------------------

_CORE = ["TYPE", "NAME", "OWNER"]
_WANTED = ["DESC", "FUNC", "PURP", "POS", "ORI", "BORE", "HBOR", "TBOR", "PSPEC", "SPREF", "CATREF", "MATREF", "STYP", "LSTU"]


def _valid_attributes(type_: str) -> list[str]:
    if type_ not in _attribute_cache:
        _attribute_cache[type_] = nexus_client.call("GET", "/api/Element/AttributeList", params={"type": type_}) or []
    return _attribute_cache[type_]


def _row(data: dict) -> dict:
    rows = (data or {}).get("Rows") or []
    if not rows:
        raise ToolError("Element not found.")
    return rows[0]


def describe_element(ref: str) -> dict:
    ref = _to_dbref(ref)
    first = batch([
        {"method": "POST", "path": "/api/Element/Get", "body": {"refs": [ref], "attributes": _CORE}},
        {"method": "GET", "path": "/api/Element/Children", "query": {"dbref": ref, "limit": 500}},
    ])
    row = _row(_data(first[0]))
    type_ = row.get("TYPE")
    info = {"dbref": ref, "type": type_, "name": row.get("NAME"), "owner": row.get("OWNER")}
    if type_:
        valid = set(_valid_attributes(type_))
        extra = [a for a in _WANTED if a in valid]
        if extra:
            got = _row(nexus_client.call("POST", "/api/Element/Get", body={"refs": [ref], "attributes": extra}))
            info["attributes"] = {a: got.get(a) for a in extra if got.get(a) not in (None, "unset")}
    kids = _data(first[1]) or {}
    children = kids.get("Children", [])
    info["children"] = {"total": len(children), "by_type": dict(Counter(c.get("type") for c in children)),
                        "truncated": bool(kids.get("Truncated"))}
    return info


# --- PML eval / call -----------------------------------------------------------------------

_MAX_EVALS = 20
_FUNCTION = re.compile(r"^(?:!!)?([A-Za-z][A-Za-z0-9_]*)$")


def _writable_only(error: NexusError):
    if error.status == 403:
        return ToolError("PML is blocked: Nexus is in read-only mode. This tool only works in a writable session (start_e3d with writable=true).")
    return error


def _cleanup(names: list[str]) -> None:
    try:
        write_guard.run_query_pml("\n".join(f"!!{n}.delete()" for n in names))
    except ToolError:
        pass  # best effort: read-only session, or Nexus gone


def pml_eval(expressions: list[str]) -> dict:
    if not expressions:
        raise ToolError("expressions is empty.")
    if len(expressions) > _MAX_EVALS:
        raise ToolError(f"At most {_MAX_EVALS} expressions per call.")
    for e in expressions:
        if not isinstance(e, str) or not e.strip() or len(e) > 1000 or re.search(r"[\r\n;]", e):
            raise ToolError("Each expression must be one non-empty line without ';' (max 1000 chars).")
    tag = uuid.uuid4().hex[:6]  # unique per call: a leftover variable of another type cannot clash
    names = [f"mcpEval{tag}x{i + 1}" for i in range(len(expressions))]
    script = "\n".join(f"!!{n} = {e.strip()}" for n, e in zip(names, expressions))
    try:
        result = write_guard.guarded_run(script, names)
    except NexusError as error:
        raise _writable_only(error) from None
    finally:
        _cleanup(names)
    out = {"values": [result["Values"].get(n) for n in names], "ok": result["Ok"]}
    if not result["Ok"]:
        out["errors"] = result["Errors"]
        out["log"] = result["Log"]
    return out


def pml_literal(value) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            raise ToolError("Numbers must be finite.")
        text = format(value, ".12f").rstrip("0")  # PML does not read exponents such as 1e-05
        return text + "0" if text.endswith(".") else text
    if isinstance(value, str):
        if re.search(r"[|$\r\n]", value):
            raise ToolError("String arguments cannot contain '|', '$' or line breaks.")
        return f"|{value}|"
    raise ToolError(f"Unsupported argument type {type(value).__name__}: use strings, numbers or booleans.")


def call_pml_function(name: str, args: list | None = None, returns: bool = True) -> dict:
    m = _FUNCTION.match(name or "")
    if not m:
        raise ToolError("Invalid function name: use an identifier like mcpecho or !!mcpecho (no spaces, dots or arguments).")
    call = f"!!{m.group(1)}({', '.join(pml_literal(a) for a in (args or []))})"
    var = f"mcpCall{uuid.uuid4().hex[:6]}"  # unique: a PML variable cannot change type
    script = f"!!{var} = {call}" if returns else call
    try:
        result = write_guard.guarded_run(script, [var] if returns else None)
    except NexusError as error:
        raise _writable_only(error) from None
    finally:
        if returns:
            _cleanup([var])
    out = {"ok": result["Ok"], "errors": result["Errors"], "log": result["Log"]}
    if returns:
        out["value"] = result["Values"].get(var)
    return out


# --- query helpers -------------------------------------------------------------------------

def build_query(type_, scope, where, name_like, **rest) -> dict:
    if name_like:
        if re.search(r"[|\r\n]", name_like):
            raise ToolError("name_like cannot contain '|' or line breaks.")
        pattern = name_like if name_like.startswith(("*", "/")) else "/" + name_like  # E3D names start with '/'
        clause = f"MATCHWILD(NAME,|{pattern}|)"
        where = f"({where}) AND {clause}" if where else clause
    body = {"type": canonical_type(type_), "scope": scope, "where": where, **rest}
    return {k: v for k, v in body.items() if v is not None}


def compact_table(data: dict) -> dict:
    cols = data.get("Columns", [])
    out = {"total": data.get("Total"), "offset": data.get("Offset"), "columns": cols,
           "rows": [[row.get(c) for c in cols] for row in data.get("Rows", [])]}
    if data.get("Truncated"):
        out["truncated"] = True
    return out

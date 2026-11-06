r"""Read-only data-quality audit of an E3D model: a small rule engine over Nexus queries.

Rules live in JSON files (``rules/*.json`` next to this module, plus an optional extra file or
folder in NEXUS_AUDIT_RULES for project-specific packs that must stay out of this repository).
Each file is ``{"rules": [ {...}, ... ]}`` and each rule has:

    id, title {"en","es"}, severity (error|warning|info), kind, types [E3D types],
    params {...}, optional "where" (a PML condition passed to Query, narrows the elements checked),
    optional "default": false (only runs when asked for by id).

Kinds and their params:
    required   attributes [..]          each must be set (not null-ref, 'unset' or empty)
    pattern    attribute, regex         the value must match (re.fullmatch); unnamed elements skipped
    unique     attribute                no two elements in scope share a (set) value
    range      attribute, min?, max?    leading number of the value within bounds (units ignored)
    reference  attributes [..], skip_when? {ATTR: [values]}
                                        each reference must not be null (=0/0, Nulref, unset)
    children   child_types? [..]        >= 1 child (of those types, or of any type if omitted)

Execution: one paged Element/Query per (rule, type) with only the attributes the rule needs. The
first page of every query goes out together through /api/Batch (<= 50 per round-trip); further
pages are fetched one by one. A rule stops after ``max_elements`` per type and is flagged
``truncated``. Attribute names are checked once per type with Element/AttributeList; an unknown
attribute skips that check with a warning instead of reporting every element as defective.
Nothing here writes to the model: only read routes are called.
"""

import csv
import json
import os
import re
import time
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

from mcp.server.mcpserver.exceptions import ToolError

import nexus_client
import ops
from nexus_client import NexusError

RULES_DIR = Path(__file__).with_name("rules")
KINDS = ("required", "pattern", "unique", "range", "reference", "children")
SEVERITIES = ("error", "warning", "info")
PAGE = 1000                 # Nexus Query maximum
MAX_ELEMENTS = 20000        # per rule and type
_CACHE_SECONDS = 300
_CACHE_SIZE = 4
_cache: dict[tuple, tuple[float, dict]] = {}

# How E3D/Nexus says "no value". Query keeps 'unset' as text; a null reference has been seen as =0/0.
_NULL_TEXT = {"", "unset", "nulref", "=0/0", "0/0", "null"}
_NUMBER = re.compile(r"^\s*([-+]?\d+(?:\.\d+)?)")


def is_unset(value) -> bool:
    return value is None or str(value).strip().lower() in _NULL_TEXT


def number_of(value) -> float | None:
    """Leading number of an E3D value such as '350mm' or '40degC'; None if there is none."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    m = _NUMBER.match(str(value)) if value is not None else None
    return float(m.group(1)) if m else None


# --- rules ---------------------------------------------------------------------------------

def _rule_files() -> list[Path]:
    files = sorted(RULES_DIR.glob("*.json"))
    extra = os.environ.get("NEXUS_AUDIT_RULES")
    if extra:
        p = Path(extra)
        files += sorted(p.glob("*.json")) if p.is_dir() else [p]
    return files


def validate_rule(rule: dict, source: str = "?") -> dict:
    def bad(msg):
        raise ValueError(f"Rule {rule.get('id', '?')} in {source}: {msg}")

    if not isinstance(rule, dict) or not rule.get("id"):
        bad("missing id")
    if rule.get("kind") not in KINDS:
        bad(f"kind must be one of {KINDS}")
    if rule.get("severity") not in SEVERITIES:
        bad(f"severity must be one of {SEVERITIES}")
    types = rule.get("types")
    if not isinstance(types, list) or not types or not all(isinstance(t, str) and t for t in types):
        bad("types must be a non-empty list of element types")
    title = rule.get("title")
    if not isinstance(title, dict) or not title.get("en"):
        bad('title must be {"en": ..., "es": ...}')
    p = rule.setdefault("params", {})
    kind = rule["kind"]
    if kind in ("required", "reference") and not (isinstance(p.get("attributes"), list) and p["attributes"]):
        bad("params.attributes must be a non-empty list")
    if kind in ("pattern", "unique", "range") and not p.get("attribute"):
        bad("params.attribute is required")
    if kind == "pattern":
        try:
            re.compile(p.get("regex", ""))
        except re.error as error:
            bad(f"invalid regex: {error}")
        if not p.get("regex"):
            bad("params.regex is required")
    if kind == "range" and p.get("min") is None and p.get("max") is None:
        bad("params.min or params.max is required")
    return rule


def load_rules() -> dict[str, dict]:
    rules: dict[str, dict] = {}
    for f in _rule_files():
        data = json.loads(f.read_text(encoding="utf-8"))
        for rule in data.get("rules", []):
            validate_rule(rule, f.name)
            rule["_source"] = f.name
            rules[rule["id"]] = rule   # a later file (NEXUS_AUDIT_RULES) overrides a built-in id
    return rules


def _select(rules: dict, ids: list[str] | None) -> list[dict]:
    if not ids:
        return [r for r in rules.values() if r.get("default", True)]
    unknown = [i for i in ids if i not in rules]
    if unknown:
        raise ToolError(f"Unknown rule id(s): {unknown}. Call audit_rules for the list.")
    return [rules[i] for i in ids]


def rules_summary() -> list[dict]:
    return [{"id": r["id"], "title": r["title"], "severity": r["severity"], "kind": r["kind"],
             "types": r["types"], "params": r["params"], "where": r.get("where"),
             "default": r.get("default", True), "source": r["_source"]}
            for r in load_rules().values()]


# --- reading -------------------------------------------------------------------------------

def _attribute_lists(types: list[str], warnings: list[str]) -> dict[str, set | None]:
    """Valid attribute names per type (upper case), None when they cannot be read (no validation)."""
    out: dict[str, set | None] = {}
    todo = []
    for t in types:
        if t in ops._attribute_cache:
            out[t] = {a.upper() for a in ops._attribute_cache[t]}
        else:
            todo.append(t)
    for i in range(0, len(todo), ops.MAX_BATCH):
        chunk = todo[i:i + ops.MAX_BATCH]
        results = ops.batch([{"method": "GET", "path": "/api/Element/AttributeList", "query": {"type": t}}
                             for t in chunk])
        for t, r in zip(chunk, results):
            if r.get("Error") or (r.get("Status") or 200) >= 400 or not isinstance(r.get("Data"), list):
                warnings.append(f"Attribute list of {t} unavailable ({r.get('Error') or r.get('Status')}); "
                                "its attributes are not validated.")
                out[t] = None
            else:
                ops._attribute_cache[t] = r["Data"]
                out[t] = {a.upper() for a in r["Data"]}
    return out


class _Task:
    """One paged query: elements of `type` in scope with `attributes`."""

    def __init__(self, key, type_, scope, where, attributes, max_elements):
        self.key, self.type, self.scope, self.where = key, type_, scope, where
        self.attributes = list(dict.fromkeys(["NAME", *attributes]))
        self.max = max_elements
        self.rows: list[dict] = []
        self.total = 0
        self.truncated = False
        self.error: str | None = None

    def body(self, offset):
        body = {"type": self.type, "attributes": self.attributes, "limit": min(PAGE, self.max - offset),
                "offset": offset}
        if self.scope:
            body["scope"] = self.scope
        if self.where:
            body["where"] = self.where
        return body

    def take(self, data):
        self.total = data.get("Total") or 0
        self.rows += data.get("Rows") or []

    def more(self):
        return not self.error and len(self.rows) < self.total and len(self.rows) < self.max


def _run_tasks(tasks: list[_Task]) -> None:
    for i in range(0, len(tasks), ops.MAX_BATCH):
        chunk = tasks[i:i + ops.MAX_BATCH]
        results = ops.batch([{"method": "POST", "path": "/api/Element/Query", "body": t.body(0)} for t in chunk])
        for t, r in zip(chunk, results):
            if r.get("Error") or (r.get("Status") or 200) >= 400:
                t.error = str(r.get("Error") or f"Nexus status {r.get('Status')}")
            else:
                t.take(r.get("Data") or {})
    for t in tasks:
        while t.more():
            before = len(t.rows)
            try:
                t.take(nexus_client.call("POST", "/api/Element/Query", body=t.body(len(t.rows))))
            except NexusError as error:
                t.error = str(error)
            if len(t.rows) == before:
                break   # no progress: do not loop forever on an odd answer
        t.truncated = t.total > len(t.rows)


# --- evaluation ----------------------------------------------------------------------------

def _is_dbref(value) -> bool:
    """An unnamed element's NAME comes back as its dbref ("=123/45", seen live on E3D 3.1)."""
    return isinstance(value, str) and value.strip().startswith("=")


def _finding(rule, type_, row, message, value=None):
    return {"rule": rule["id"], "severity": rule["severity"], "dbref": row.get("dbref"),
            "name": None if is_unset(row.get("NAME")) or _is_dbref(row.get("NAME")) else row.get("NAME"), "type": type_,
            "message": message, "value": value}


def _check_rows(rule, type_, rows, attrs) -> list[dict]:
    p, kind, out = rule["params"], rule["kind"], []
    if kind in ("required", "reference"):
        skip = {k.upper(): {str(v).upper() for v in vs} for k, vs in (p.get("skip_when") or {}).items()}
        for row in rows:
            if any(str(row.get(k, "")).strip().upper() in vs for k, vs in skip.items()):
                continue
            for a in attrs:
                if is_unset(row.get(a)):
                    what = "references nothing" if kind == "reference" else "is not set"
                    out.append(_finding(rule, type_, row, f"{a} {what}", row.get(a)))
    elif kind == "pattern":
        a, rx = attrs[0], re.compile(p["regex"])
        for row in rows:
            v = row.get(a)
            if a == "NAME" and _is_dbref(v):
                continue   # unnamed element: no name to check
            if is_unset(v):
                if not p.get("skip_unset", True):
                    out.append(_finding(rule, type_, row, f"{a} is not set", v))
            elif not rx.fullmatch(str(v)):
                out.append(_finding(rule, type_, row, f"{a} '{v}' does not match {p['regex']}", v))
    elif kind == "range":
        a, lo, hi = attrs[0], p.get("min"), p.get("max")
        for row in rows:
            v = row.get(a)
            if is_unset(v):
                continue
            n = number_of(v)
            if n is None:
                out.append(_finding(rule, type_, row, f"{a} '{v}' is not a number", v))
            elif (lo is not None and n < lo) or (hi is not None and n > hi):
                out.append(_finding(rule, type_, row, f"{a} {v} outside [{lo}, {hi}]", v))
    return out


def _check_unique(rule, typed_rows) -> list[dict]:
    a = rule["params"]["attribute"].upper()
    case = rule["params"].get("case_sensitive", False)
    groups = defaultdict(list)
    for type_, row in typed_rows:
        v = row.get(a)
        if not is_unset(v):
            groups[str(v).strip() if case else str(v).strip().upper()].append((type_, row))
    out = []
    for members in groups.values():
        if len(members) > 1:
            for type_, row in members:
                out.append(_finding(rule, type_, row, f"{a} '{row.get(a)}' shared by {len(members)} elements", row.get(a)))
    return out


def _children_findings(rule, type_, parents, scope, max_elements, warnings) -> tuple[list[dict], bool]:
    """Parents with no child (of child_types, if given). Returns (findings, truncated)."""
    child_types = [ops.canonical_type(t) for t in rule["params"].get("child_types") or []]
    has_child: set = set()
    truncated = False
    if child_types:
        # One query per child type in the same scope, reading OWNER: no per-parent call. OWNER may come
        # back as a dbref or a name, so a parent counts as having a child if either matches.
        tasks = [_Task(("child", c), c, scope, None, ["OWNER"], max_elements * 10) for c in child_types]
        _run_tasks(tasks)
        for t in tasks:
            if t.error:
                warnings.append(f"{rule['id']}: query of {t.type} failed ({t.error}); rule skipped for {type_}.")
                return [], False
            truncated |= t.truncated
            has_child.update(str(r.get("OWNER")).strip() for r in t.rows if not is_unset(r.get("OWNER")))
        empty = [r for r in parents if r.get("dbref") not in has_child and str(r.get("NAME")).strip() not in has_child]
    else:
        empty = []
        for i in range(0, len(parents), ops.MAX_BATCH):
            chunk = parents[i:i + ops.MAX_BATCH]
            results = ops.batch([{"method": "GET", "path": "/api/Element/Children",
                                  "query": {"dbref": r["dbref"], "limit": 1}} for r in chunk])
            for row, res in zip(chunk, results):
                if res.get("Error") or (res.get("Status") or 200) >= 400:
                    warnings.append(f"{rule['id']}: children of {row.get('dbref')} unreadable ({res.get('Error')}).")
                elif not (res.get("Data") or {}).get("Children"):
                    empty.append(row)
    wanted = ", ".join(child_types) if child_types else "any type"
    if truncated:
        warnings.append(f"{rule['id']}: child query hit its cap; some parents may be reported as empty wrongly.")
    return [_finding(rule, type_, r, f"has no child ({wanted})") for r in empty], truncated


def _rule_attributes(rule) -> list[str]:
    p = rule["params"]
    names = list(p.get("attributes") or []) + ([p["attribute"]] if p.get("attribute") else [])
    names += list((p.get("skip_when") or {}).keys())
    return [n.upper() for n in names]


def run_audit(scope: str | None, rule_ids: list[str] | None = None, max_elements: int = MAX_ELEMENTS) -> dict:
    max_elements = max(1, int(max_elements))
    selected = _select(load_rules(), rule_ids)
    warnings: list[str] = []
    # Attribute names are NOT pre-checked with Element/AttributeList: on E3D 3.1 it leaves out real,
    # readable attributes (seen live: PSPEC on PIPE/BRAN, SPREF on components, CATREF on NOZZ). Query
    # answers null for an attribute the type does not have (and 'unset'/'Nulref' for one that is not
    # set), so an attribute that is null on every row is reported as unknown after the query instead.
    plan = []   # (rule, type, checked attributes, task)
    for rule in selected:
        needed = _rule_attributes(rule)
        check_attrs = [a for a in needed if a not in {k.upper() for k in (rule["params"].get("skip_when") or {})}]
        for t in (ops.canonical_type(x) for x in rule["types"]):
            attrs = needed
            checked = list(check_attrs)
            if rule["kind"] != "children" and not checked:
                continue
            plan.append((rule, t, checked, _Task((rule["id"], t), t, scope, rule.get("where"), attrs, max_elements)))
    _run_tasks([task for *_, task in plan])

    findings: list[dict] = []
    per_rule: dict[str, dict] = {r["id"]: {"severity": r["severity"], "title": r["title"]["en"], "checked": 0,
                                           "findings": 0, "truncated": False, "ran": False} for r in selected}
    unique_rows: dict[str, list] = defaultdict(list)
    for rule, t, checked, task in plan:
        stats = per_rule[rule["id"]]
        if task.error:
            warnings.append(f"{rule['id']}: query of {t} failed ({task.error}).")
            continue
        stats["ran"] = True
        unknown = [a for a in checked if task.rows and all(r.get(a) is None for r in task.rows)]
        if unknown:
            warnings.append(f"{rule['id']}: {t} has no attribute {', '.join(unknown)} (null on every element); skipped.")
            checked = [a for a in checked if a not in unknown]
            if rule["kind"] not in ("children", "unique") and not checked:
                continue
        stats["checked"] += len(task.rows)
        stats["truncated"] |= task.truncated
        if task.truncated:
            warnings.append(f"{rule['id']}: only the first {len(task.rows)} of {task.total} {t} checked (max_elements).")
        if rule["kind"] == "unique":
            unique_rows[rule["id"]] += [(t, r) for r in task.rows]
        elif rule["kind"] == "children":
            got, trunc = _children_findings(rule, t, task.rows, scope, max_elements, warnings)
            stats["truncated"] |= trunc
            findings += got
        else:
            findings += _check_rows(rule, t, task.rows, checked)
    rules_by_id = {r["id"]: r for r in selected}
    for rid, typed_rows in unique_rows.items():
        findings += _check_unique(rules_by_id[rid], typed_rows)

    order = {s: i for i, s in enumerate(SEVERITIES)}
    findings.sort(key=lambda f: (order[f["severity"]], f["rule"], str(f["name"] or ""), str(f["dbref"])))
    for f in findings:
        per_rule[f["rule"]]["findings"] += 1
    return {"scope": scope, "rules": per_rule, "by_severity": dict(Counter(f["severity"] for f in findings)),
            "findings": findings, "warnings": warnings,
            "truncated": any(s["truncated"] for s in per_rule.values())}


def cached_audit(scope, rule_ids=None, max_elements=MAX_ELEMENTS, refresh=False) -> dict:
    """run_audit, remembered for a few minutes so paging through findings does not re-query E3D."""
    key = (nexus_client.base_url(), scope, tuple(rule_ids or ()), max_elements)
    now = time.monotonic()
    hit = _cache.get(key)
    if hit and not refresh and now - hit[0] < _CACHE_SECONDS:
        return hit[1]
    result = run_audit(scope, rule_ids, max_elements)
    _cache[key] = (now, result)
    while len(_cache) > _CACHE_SIZE:
        _cache.pop(min(_cache, key=lambda k: _cache[k][0]))
    return result


# --- tool bodies ---------------------------------------------------------------------------

def audit_model(scope, rules=None, max_findings=200, offset=0, max_elements=MAX_ELEMENTS, refresh=False) -> dict:
    result = cached_audit(scope, rules, max_elements, refresh)
    max_findings = max(1, min(int(max_findings), 1000))
    offset = max(0, int(offset))
    page = result["findings"][offset:offset + max_findings]
    out = {k: v for k, v in result.items() if k != "findings"}
    out.update(findings_total=len(result["findings"]), offset=offset, findings=page,
               more=offset + len(page) < len(result["findings"]))
    return out


COLUMNS = ["rule", "severity", "type", "name", "dbref", "message", "value"]


def default_export_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / ".local" / "share")
    return Path(base) / "NexusMCP" / "audits"


def audit_export(scope, path=None, rules=None, max_elements=MAX_ELEMENTS, refresh=False) -> dict:
    result = cached_audit(scope, rules, max_elements, refresh)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    target = Path(os.path.expandvars(os.path.expanduser(path))) if path else default_export_dir()
    if not target.suffix:          # a folder
        target = target / f"model-audit-{stamp}.csv"
    if target.suffix.lower() != ".csv":
        raise ToolError("Only CSV export is supported: give a .csv file or a folder.")
    target.parent.mkdir(parents=True, exist_ok=True)
    titles = {r["id"]: r["title"] for r in load_rules().values()}
    with target.open("w", newline="", encoding="utf-8-sig") as f:   # BOM: Excel opens it as UTF-8
        w = csv.writer(f)
        w.writerow(COLUMNS + ["title_en", "title_es"])
        for x in result["findings"]:
            t = titles.get(x["rule"], {})
            w.writerow([x.get(c) if x.get(c) is not None else "" for c in COLUMNS] + [t.get("en", ""), t.get("es", "")])
    return {"path": str(target), "findings_total": len(result["findings"]), "rules": result["rules"],
            "by_severity": result["by_severity"], "warnings": result["warnings"], "truncated": result["truncated"]}

"""Nexus MCP server: exposes AVEVA E3D (through the Nexus HTTP add-in) as MCP tools.

IMPORTANT: this server talks to Claude over stdout, so never use print() here.
Log to stderr instead.
"""

import asyncio
import logging

from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

import audit
import e3d_launcher
import nexus_client
import ops
import pml_dev
import write_guard
import guide
from models import CountResult, DescribeResult, QueryResult, TreeResult

mcp = MCPServer("nexus")
log = logging.getLogger("nexus-mcp")

# Hints for clients (they are hints, not enforcement: the write guard is what enforces).
READ_ONLY = ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=False)
DESTRUCTIVE = ToolAnnotations(read_only_hint=False, destructive_hint=True, idempotent_hint=False, open_world_hint=False)
DESTRUCTIVE_IDEMPOTENT = ToolAnnotations(read_only_hint=False, destructive_hint=True, idempotent_hint=True, open_world_hint=False)
FILE_WRITE = ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=True, open_world_hint=False)
LAUNCH = ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=True, open_world_hint=False)


@mcp.tool(annotations=READ_ONLY)
def session_info() -> dict:
    """Return the active AVEVA E3D session: project, MDB, user, module, E3D version
    and whether Nexus is in read-only mode.

    Call this first to check that E3D is running and which project you are looking at.
    """
    return nexus_client.call("GET", "/api/Session/Info")


# --- resources and prompts (read-only context for the client) ---------------------------------

@mcp.resource("nexus://session", name="session", mime_type="application/json",
              description="Active E3D session: project, MDB, user, module, version, read-only flag.")
def session_resource() -> dict:
    return nexus_client.call("GET", "/api/Session/Info")


@mcp.resource("nexus://projects", name="projects", mime_type="application/json",
              description="E3D projects this machine can start: project, mdb, module, port, auth, login source. Never credentials.")
def projects_resource() -> dict:
    return e3d_launcher.list_projects()


@mcp.resource("nexus://attributes/{type}", name="attributes", mime_type="application/json",
              description="Valid attributes of an E3D element type (cached per process).")
def attributes_resource(type: str) -> dict:
    canonical = ops.canonical_type(type)
    return {"type": canonical, "attributes": ops._valid_attributes(canonical)}


@mcp.resource("nexus://guide/pml", name="guide_pml", mime_type="text/markdown",
              description="Concise PML cheat-sheet for run_pml on this server.")
def guide_pml() -> str:
    return guide.PML_GUIDE


@mcp.resource("nexus://guide/safety", name="guide_safety", mime_type="text/markdown",
              description="Write rules: read-only default, write_dbs, save_work, refused constructs.")
def guide_safety() -> str:
    return guide.SAFETY_GUIDE


@mcp.prompt(description="Explore an E3D site step by step, read-only.")
def explore_site(site: str) -> str:
    return (
        f"Explore the E3D site {site} using read-only tools only.\n"
        "1. session_info to confirm the project and mode.\n"
        f"2. describe_element on {site} for type, owner and children by type.\n"
        f"3. element_tree on {site} with depth 2 to see zones and their main contents.\n"
        "4. count_elements / query_elements (compact) for the types that matter.\n"
        "Summarise what the site contains and suggest what to look at next. Do not write anything."
    )


@mcp.prompt(description="Test a PML function from the sandbox or PMLLIB.")
def test_pml_function(name: str) -> str:
    return (
        f"Test the PML function {name}. First read the resources nexus://guide/pml and nexus://guide/safety.\n"
        "1. session_info: running PML needs a writable session (do not start one without asking the user).\n"
        f"2. list_pml to see whether {name} is in the sandbox; if it is new, run_pml 'PML REHASH' once "
        "(never REHASH ALL).\n"
        "3. call_pml_function with representative arguments, then edge cases. Check ok, errors and log each time.\n"
        "4. If it fails, fix the source with save_pml and call it again. Report what you tested and the results."
    )


@mcp.prompt(description="Plan, apply and verify a model change, asking before saving.")
def safe_edit(task: str) -> str:
    return (
        f"Task: {task}\n\n"
        "Follow this flow and read nexus://guide/safety and nexus://guide/pml first.\n"
        "1. Plan: say which elements change and in which database; check write_dbs allows it (e3d_projects, session_info).\n"
        "2. Apply with run_pml, only inside allowed databases, in small steps. Check Ok and Errors every time.\n"
        "3. Verify with element_tree and get_attributes that the result is what the plan said.\n"
        "4. Show the user what changed and ask for confirmation. Only after a yes call save_work(db) for "
        "each database, telling the user which one and why. Never SAVEWORK inside run_pml."
    )


@mcp.prompt(description="Read-only audit of a zone (generic placeholder).")
def audit_zone(zone: str) -> str:
    return (
        f"Audit the zone {zone} read-only.\n"
        f"1. describe_element on {zone}.\n"
        "2. count_elements per relevant type with scope set to the zone dbref (resolve_names first if needed).\n"
        "3. query_elements (compact) listing elements with NAME, DESC and key attributes; use attribute_list "
        "to pick attribute names and page with next_offset.\n"
        "4. Report anomalies: unnamed elements, unset attributes, unexpected types. Do not modify anything."
    )


def _without_none(**fields):
    """Drop unset optional fields so Nexus applies its own defaults."""
    return {k: v for k, v in fields.items() if v is not None}


@mcp.tool(annotations=READ_ONLY)
def query_elements(
    type: str,
    scope: str | None = None,
    where: str | None = None,
    attributes: list[str] | None = None,
    limit: int | None = None,
    offset: int | None = None,
    name_like: str | None = None,
    compact: bool = True,
) -> QueryResult:
    """Find E3D elements of one type and read some of their attributes in a single call.

    Use this for questions like "list the sites", "pipes with bore over 300 in this zone".
    Returns a table: each row has the element `dbref` plus the requested attributes;
    `Total` is the count before paging.

    - type: E3D element type, e.g. SITE, ZONE, PIPE, EQUIPMENT. EQUI is accepted as EQUIPMENT.
      Unknown types are rejected; use attribute_list to check a type's valid attributes.
    - scope: where to search, as a dbref like "=123/456". Element names containing quotes
      cannot be used; resolve them to a dbref first. Omit to search the whole database.
    - where: a PML condition, e.g. "BORE gt 300". Plain read-only expressions only
      (no semicolons, no method calls, no commands such as DELETE).
    - attributes: attribute names to return, e.g. ["NAME", "BORE"]. A cell is null if the
      element has no such attribute; "unset" means it exists but has no value.
    - name_like: wildcard on the element name (* and ?), case-sensitive, e.g. "*PUMP*" or
      "PIPE-1*" (a leading "/" is added when the pattern does not start with * or /). Combined
      with `where` by AND.
    - limit: rows to return (default 100, max 1000). Use offset to page through more.
    - compact: True (default) returns {total, offset, columns, rows: [[...]], next_offset} instead
      of one object per row; far fewer tokens. False returns the Nexus shape {Total, Rows: [{...}]}
      plus next_offset. next_offset is the offset for the next page, or null when no rows remain.
    """
    data = nexus_client.call(
        "POST",
        "/api/Element/Query",
        body=ops.build_query(type, scope, where, name_like, attributes=attributes, limit=limit, offset=offset),
    )
    if compact:
        return ops.compact_table(data, offset)
    return {**data, "next_offset": ops.next_offset(data, offset)}


@mcp.tool(annotations=READ_ONLY)
def count_elements(type: str, scope: str | None = None, where: str | None = None, name_like: str | None = None) -> CountResult:
    """Count elements of a type without listing them: returns {"total": n}.

    Cheaper than query_elements when you only need "how many". Arguments as in query_elements
    (type, scope dbref, PML where condition, name_like wildcard).
    """
    data = nexus_client.call(
        "POST", "/api/Element/Query", body=ops.build_query(type, scope, where, name_like, limit=1)
    )
    return {"total": data.get("Total")}


@mcp.tool(annotations=READ_ONLY)
def batch(requests: list[dict]) -> list[dict]:
    """Run several READ calls in one round-trip (up to 50). Use it instead of many separate tool calls.

    Each request: {"method": "GET"|"POST", "path": "/api/Element/Children", "query": {...},
    "body": {...}}. Allowed paths: /api/Health, /api/Session/Info, /api/Element/Query, Get,
    Children, AttributeList, Resolve, Tree (anything else, e.g. PML, is refused). `query` holds
    URL parameters (GET), `body` the JSON (POST), with the same fields the matching tool takes
    (e.g. Children: {"dbref": "=1/2"}; Get: {"refs": [...], "attributes": [...]}).

    Returns one entry per request, in order: {"Status", "Data", "Error"}. One failing request does
    not fail the others, so check each Status/Error.
    """
    return ops.batch(requests)


@mcp.tool(annotations=READ_ONLY)
def resolve_names(names: list[str]) -> list[dict | None]:
    """Turn element names into dbrefs and types: [{"Name", "Dbref", "Type"}], null for a name that
    does not exist.

    Use this when the user gives names ("/PUMP-101") and a tool needs a dbref. A missing leading
    "/" is added. Names must be plain (letters, digits, _ . -). On an older Nexus this runs PML,
    which needs a writable session; a read-only one then fails with a clear message.
    """
    return ops.resolve_names(names)


@mcp.tool(annotations=READ_ONLY)
def element_tree(ref: str, depth: int = 2, type: str | None = None, limit: int = 500) -> TreeResult:
    """Return a whole subtree in one call instead of repeated list_children.

    Result: {"Root", "Items": [{"Dbref", "Type", "Name", "Depth", "Owner"}], "Truncated"}.
    Truncated=true means the limit (or, on older Nexus, a cap of 200 child lookups) cut it short:
    narrow with `type` or a smaller depth.

    - ref: dbref ("=1/2") or name ("/SITE-A").
    - depth: levels below ref, 1..10 (default 2).
    - type: only return elements of this type (the walk still passes through the others).
    - limit: max items (default 500, max 5000).
    """
    return ops.element_tree(ref, depth, type, limit)


@mcp.tool(annotations=READ_ONLY)
def describe_element(ref: str) -> DescribeResult:
    """One-stop "what is this element": name, type, owner, the common attributes that exist for
    its type (DESC, FUNC, PURP, POS, ORI, BORE, PSPEC, ...; unset ones omitted) and a children
    summary (count per type, first 500 children).

    Use it as the first look at any element; follow up with get_attributes or element_tree.

    - ref: dbref ("=1/2") or name ("/PUMP-101"). Fails with "Element not found" if it does not exist.
    """
    return ops.describe_element(ref)


@mcp.tool(annotations=DESTRUCTIVE)
def pml_eval(expressions: list[str]) -> dict:
    """Evaluate PML expressions and return their values, e.g. ["1 + 2", "NAME OF /SITE-A",
    "UPCASE(|abc|)"]. Result: {"values": [...], "ok"} (+ "errors", "log" when something failed).

    Needs a WRITABLE session: it runs PML through Nexus, which is blocked in read-only mode
    (the call then fails saying so). Goes through the same write guard as run_pml. Each expression
    is one line (no ';', max 20 per call); values are assigned to temporary !!mcpEvalN variables
    that are deleted afterwards. For several statements or commands use run_pml.
    """
    return ops.pml_eval(expressions)


@mcp.tool(annotations=DESTRUCTIVE)
def call_pml_function(name: str, args: list[str | int | float | bool] | None = None, returns: bool = True) -> dict:
    """Call a PML function (a saved sandbox one such as mcpecho, or one from PMLLIB) and return
    {"ok", "value", "errors", "log"}.

    Needs a WRITABLE session (PML is blocked in read-only mode) and goes through the write guard.
    Arguments are quoted for you: strings become |...| (they cannot contain '|', '$' or line
    breaks), numbers and booleans are passed as such. Arrays/objects are not supported: use
    run_pml for those.

    - name: identifier only, with or without "!!" (e.g. "mcpecho"). The function must be loaded
      (for a new sandbox file run "PML REHASH" first; never "PML REHASH ALL", which rewrites
      the pml.index of every PMLLIB folder, shared server folders included).
    - args: positional arguments.
    - returns: False for functions that return nothing (otherwise assigning the result fails).
    """
    return ops.call_pml_function(name, args, returns)


@mcp.tool(annotations=READ_ONLY)
def get_attributes(refs: list[str], attributes: list[str]) -> dict:
    """Read several attributes of several known elements in one call.

    Use this when you already have dbrefs (from query_elements or list_children) and
    want more detail. Cheaper than one call per element.

    - refs: dbrefs like "=123/456" (names are not resolved). Max 1000. If any ref does
      not exist the whole request fails with "not found".
    - attributes: attribute names to read, e.g. ["NAME", "TYPE", "BORE"].
    """
    return nexus_client.call(
        "POST", "/api/Element/Get", body={"refs": refs, "attributes": attributes}
    )


@mcp.tool(annotations=READ_ONLY)
def list_children(ref: str, type: str | None = None) -> dict:
    """Navigate the E3D hierarchy: return an element's owner and its direct children.

    Use this to explore the model step by step (world -> sites -> zones -> ...).
    Each child has `dbref`, `type` and `name`.

    - ref: dbref of the element, e.g. "=123/456".
    - type: optional filter, only children of this type (e.g. "ZONE").
    """
    return nexus_client.call(
        "GET", "/api/Element/Children", params=_without_none(dbref=ref, type=type)
    )


@mcp.tool(annotations=READ_ONLY)
def attribute_list(type: str) -> dict:
    """List the valid attributes of an E3D element type (system attributes first,
    then user-defined ones starting with ':').

    Use this before query_elements or get_attributes instead of guessing attribute names.

    - type: E3D element type, e.g. PIPE.
    """
    return nexus_client.call("GET", "/api/Element/AttributeList", params={"type": type})


@mcp.tool(annotations=DESTRUCTIVE)
def run_pml(script: str, variables: list[str] | None = None) -> dict:
    """Run a PML script in the live E3D session and read back global variables, with the
    real E3D messages.

    Use this to test PML code or compute something the other tools cannot. Plain lines
    run one by one; complete if/do/handle blocks run as a whole. A line that fails does not
    stop the rest, so always check `Ok` and `Errors`.

    Each entry of `Errors` names the failing line and ends with E3D's own message, e.g.
    `PML command failed: !!a = !!nofunc()  [E3D: PML: Function not found]`. Errors are caught
    inside E3D (so a graphical E3D never opens an error dialog), and the run of that unit stops
    at the failing line. `Log` holds what E3D printed meanwhile (output of `$P` and `q`
    commands); read both to fix errors.

    Limits: `define function/method/object/form` cannot be typed here; save them with
    save_pml and call them. Blocked while Nexus is in read-only mode (the default): the call
    then fails with "read-only" (check session_info, and start_e3d with writable=true on a
    test project if the user allows it). Variables are lost when the session restarts.

    - script: PML text, one statement per line.
    - variables: names of global variables to return, WITHOUT the "!!" prefix. A variable
      that is not defined comes back as null.
    """
    return write_guard.guarded_run(script, variables)


@mcp.tool(annotations=DESTRUCTIVE_IDEMPOTENT)
def save_work(db: str) -> dict:
    """Commit (SAVEWORK) the pending changes of ONE database, and only if it is one this MCP
    is allowed to write.

    This is the only way to save: SAVEWORK inside run_pml is refused. Changes in any other
    database stay unsaved and are discarded when the session stops. Allowed databases are set
    per MDB in projects.json (`write_dbs`); with none configured nothing can be written.

    Tell the user which database you are saving and why before calling it.

    - db: full database name as E3D shows it, e.g. "EQ/DESI/DEMO-MCP-TEST".
    """
    return write_guard.save_work(db)


@mcp.tool(annotations=FILE_WRITE)
def save_pml(kind: str, name: str, content: str) -> dict:
    """Save PML source code into the test sandbox so it can be loaded and run in E3D.

    Use this to test functions and objects without asking the user to copy files around. E3D
    finds the file by name and reloads it automatically when you save it again. For a NEW
    file, run "PML REHASH" once with run_pml before calling it (it re-indexes only the first PMLLIB
    folder, where the sandbox lives; "PML REHASH ALL" is refused: it rewrites shared index files).

    Forms cannot be loaded in the headless session (no graphics), so put the logic you want
    to test in functions or objects and keep the form thin.

    Rules, enforced: the name must start with "mcp" (lowercase letters, digits, "_"), and the
    file must define exactly one thing with that same name, so test code can never replace a
    real function: `define function !!mcpname(...)`, `define object MCPNAME`, or
    `setup form !!mcpname`.

    - kind: "function", "object" or "form".
    - name: e.g. "mcpecho".
    - content: the full PML source of the file.
    """
    with audit.entry("save_pml", script=content, detail=f"{kind} {name}"):
        return pml_dev.save(kind, name, content)


@mcp.tool(annotations=DESTRUCTIVE_IDEMPOTENT)
def delete_pml(kind: str, name: str) -> dict:
    """Delete a file previously saved with save_pml. The function or object stays loaded in
    the running session until E3D is restarted.

    - kind: "function", "object" or "form".
    - name: the same name used in save_pml.
    """
    with audit.entry("delete_pml", detail=f"{kind} {name}"):
        return pml_dev.delete(kind, name)


@mcp.tool(annotations=READ_ONLY)
def list_pml() -> dict:
    """List the files currently in the PML test sandbox (what save_pml has written)."""
    return pml_dev.listing()


@mcp.tool(annotations=READ_ONLY)
def e3d_projects() -> dict:
    """List the E3D projects this machine can start, with project code, MDB, module and port.

    Use this to learn the valid names for start_e3d / stop_e3d. Credentials are never shown;
    `login` says how start_e3d will log in: "stored" (no dialog), "dialog" or "windows".
    """
    return e3d_launcher.list_projects()


@mcp.tool(annotations=DESTRUCTIVE_IDEMPOTENT)
def forget_e3d_login(project: str) -> dict:
    """Delete the E3D password stored for a project in the Windows Credential Manager, so the
    next start_e3d shows the login dialog again. Use it when start_e3d reports the stored
    password was refused, or when the user asks.

    - project: a name from e3d_projects.
    """
    return e3d_launcher.forget_login(project)


@mcp.tool(annotations=LAUNCH)
async def start_e3d(project: str, writable: bool = False, ctx: Context | None = None) -> dict:
    """Start a headless E3D session with Nexus for a configured project and wait until
    Nexus answers (about 30 s, up to 2 min).

    Use this when session_info reports that Nexus cannot be reached. If Nexus is already
    running it does nothing and says so.

    LOGIN: if the password is stored in the Windows Credential Manager (e3d_projects shows
    login "stored"), E3D starts with no interaction. Otherwise a login dialog opens on the
    user's screen and the call waits (up to 3 minutes); tell the user to look for it first.
    NEVER ask for the password in the chat and never put credentials in any argument: they
    go from the store or the dialog straight to E3D and you never see them.

    - project: a name from e3d_projects (e.g. "DEMO").
    - writable: default False (read-only: queries only, run_pml is blocked). True allows
      PML that changes the model, and is refused unless the user has whitelisted the
      project; never ask for it on a production project without the user's say-so.
    """
    loop = asyncio.get_running_loop()

    def on_wait(elapsed: int, total: int) -> None:  # called from the worker thread
        if ctx is not None:
            asyncio.run_coroutine_threadsafe(
                ctx.report_progress(elapsed, total, f"Waiting for Nexus ({elapsed} s)"), loop
            )

    if ctx is not None:
        await ctx.info(f"Starting E3D project {project} ({'writable' if writable else 'read-only'}); "
                       "a login dialog may open on the user's screen.")
    with audit.entry("start_e3d", project=project, detail=f"writable={writable}") as record:
        # The launch waits for E3D (30 s to minutes): keep the event loop free meanwhile.
        result = await asyncio.to_thread(e3d_launcher.start, project, writable, on_wait)
        record["status"] = result.get("status", "ok")
    if result.get("port"):
        # Later calls must reach the Nexus of THIS project, not the one NEXUS_URL named at startup.
        nexus_client.use_project_port(result["port"])
    return result


@mcp.tool(annotations=DESTRUCTIVE_IDEMPOTENT)
def stop_e3d(project: str, force: bool = False) -> dict:
    """Stop the headless E3D session started by start_e3d.

    All PML global variables of that session are lost, and so is any unsaved change. Does not
    touch E3D windows the user opened themselves. Only stop it if the user asked, or to restart
    in another mode.

    It refuses while the session still holds elements CLAIMED: killing the session would leave
    them claimed for ever and nobody else could edit them until an administrator releases them.
    Call save_work for their database first (it saves and releases the claims).

    - project: a name from e3d_projects.
    - force: stop anyway, accepting orphan claims. Only with the user's explicit say-so.
    """
    port = e3d_launcher.project_port(project)  # also rejects unknown projects
    with audit.entry("stop_e3d", project=project, detail=f"force={force}"):
        pending = [] if force else write_guard.claims_pending(port)  # claims of THIS project's session
        if pending is None:
            raise ToolError(
                "Cannot tell whether the session still holds claimed elements (Nexus answered but the log "
                "with the claim list was not captured). Stopping could leave orphan claims. Check with "
                "run_pml ('Q CLAIMLIST') or call stop_e3d with force=true if the user accepts that."
            )
        if pending:
            raise ToolError(
                f"{len(pending)} element(s) are still claimed by this session ({', '.join(pending[:5])}). "
                "Stopping now would leave them claimed and nobody else could edit them. Call save_work for "
                "their database first, or stop_e3d with force=true if the user accepts that."
            )
        return e3d_launcher.stop(project)


# Read-only model data-quality audit: audit_rules, audit_model, audit_export.
import audit_tools  # noqa: E402

audit_tools.register(mcp)


def main() -> None:
    """Entry point (console script `nexus-mcp` and `python server.py`)."""
    audit.configure_logging()
    mcp.run()  # stdio transport by default


if __name__ == "__main__":
    main()

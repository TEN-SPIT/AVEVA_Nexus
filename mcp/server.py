"""Nexus MCP server: exposes AVEVA E3D (through the Nexus HTTP add-in) as MCP tools.

IMPORTANT: this server talks to Claude over stdout, so never use print() here.
Log to stderr instead.
"""

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

import e3d_launcher
import nexus_client
import pml_dev
import write_guard

mcp = MCPServer("nexus")


@mcp.tool()
def session_info() -> dict:
    """Return the active AVEVA E3D session: project, MDB, user, module, E3D version
    and whether Nexus is in read-only mode.

    Call this first to check that E3D is running and which project you are looking at.
    """
    return nexus_client.call("GET", "/api/Session/Info")


def _without_none(**fields):
    """Drop unset optional fields so Nexus applies its own defaults."""
    return {k: v for k, v in fields.items() if v is not None}


@mcp.tool()
def query_elements(
    type: str,
    scope: str | None = None,
    where: str | None = None,
    attributes: list[str] | None = None,
    limit: int | None = None,
    offset: int | None = None,
) -> dict:
    """Find E3D elements of one type and read some of their attributes in a single call.

    Use this for questions like "list the sites", "pipes with bore over 300 in this zone".
    Returns a table: each row has the element `dbref` plus the requested attributes;
    `Total` is the count before paging.

    - type: E3D element type by its full name, e.g. SITE, ZONE, PIPE, EQUIPMENT. Use
      EQUIPMENT, not EQUI: list_children reports the short form "EQUI" but this query
      rejects it ("Unknown element type"). Other unknown types are rejected too; use
      attribute_list to check a type's valid attributes.
    - scope: where to search, as a dbref like "=123/456". Element names containing quotes
      cannot be used; resolve them to a dbref first. Omit to search the whole database.
    - where: a PML condition, e.g. "BORE gt 300". Plain read-only expressions only
      (no semicolons, no method calls, no commands such as DELETE).
    - attributes: attribute names to return, e.g. ["NAME", "BORE"]. A cell is null if the
      element has no such attribute; "unset" means it exists but has no value.
    - limit: rows to return (default 100, max 1000). Use offset to page through more.
    """
    return nexus_client.call(
        "POST",
        "/api/Element/Query",
        body=_without_none(
            type=type, scope=scope, where=where, attributes=attributes, limit=limit, offset=offset
        ),
    )


@mcp.tool()
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


@mcp.tool()
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


@mcp.tool()
def attribute_list(type: str) -> dict:
    """List the valid attributes of an E3D element type (system attributes first,
    then user-defined ones starting with ':').

    Use this before query_elements or get_attributes instead of guessing attribute names.

    - type: E3D element type, e.g. PIPE.
    """
    return nexus_client.call("GET", "/api/Element/AttributeList", params={"type": type})


@mcp.tool()
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


@mcp.tool()
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


@mcp.tool()
def save_pml(kind: str, name: str, content: str) -> dict:
    """Save PML source code into the test sandbox so it can be loaded and run in E3D.

    Use this to test functions and objects without asking the user to copy files around. E3D
    finds the file by name and reloads it automatically when you save it again. For a NEW
    file, run "PML REHASH ALL" once with run_pml before calling it.

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
    return pml_dev.save(kind, name, content)


@mcp.tool()
def delete_pml(kind: str, name: str) -> dict:
    """Delete a file previously saved with save_pml. The function or object stays loaded in
    the running session until E3D is restarted.

    - kind: "function", "object" or "form".
    - name: the same name used in save_pml.
    """
    return pml_dev.delete(kind, name)


@mcp.tool()
def list_pml() -> dict:
    """List the files currently in the PML test sandbox (what save_pml has written)."""
    return pml_dev.listing()


@mcp.tool()
def e3d_projects() -> dict:
    """List the E3D projects this machine can start, with project code, MDB, module and port.

    Use this to learn the valid names for start_e3d / stop_e3d. Credentials are never shown.
    """
    return e3d_launcher.list_projects()


@mcp.tool()
def start_e3d(project: str, writable: bool = False) -> dict:
    """Start a headless E3D session with Nexus for a configured project and wait until
    Nexus answers (about 30 s, up to 2 min).

    Use this when session_info reports that Nexus cannot be reached. If Nexus is already
    running it does nothing and says so.

    LOGIN: a Windows credential dialog opens on the user's screen and the call waits (up to
    3 minutes) for them to type the E3D user and password. Tell the user to look for it
    before calling. NEVER ask for the password in the chat and never put credentials in any
    argument: they go from the dialog straight to E3D and you never see them.

    - project: a name from e3d_projects (e.g. "DEMO").
    - writable: default False (read-only: queries only, run_pml is blocked). True allows
      PML that changes the model, and is refused unless the user has whitelisted the
      project; never ask for it on a production project without the user's say-so.
    """
    return e3d_launcher.start(project, writable)


@mcp.tool()
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
    pending = [] if force else write_guard.claims_pending()
    if pending:
        raise ToolError(
            f"{len(pending)} element(s) are still claimed by this session ({', '.join(pending[:5])}). "
            "Stopping now would leave them claimed and nobody else could edit them. Call save_work for "
            "their database first, or stop_e3d with force=true if the user accepts that."
        )
    return e3d_launcher.stop(project)


if __name__ == "__main__":
    mcp.run()  # stdio transport by default

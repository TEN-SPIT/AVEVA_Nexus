# Nexus MCP server

An [MCP](https://modelcontextprotocol.io) server that lets Claude (or any MCP client) use a running
AVEVA E3D session through the Nexus HTTP add-in of this repository.

```
Claude Code --(MCP, stdio / JSON-RPC)--> mcp/server.py --(HTTP)--> Nexus inside E3D (des.exe)
```

Nexus is a .NET add-in that must live inside E3D; this server is a separate Python program that
talks to it over HTTP, and can start and stop a local headless (TTY) E3D session.

## Requirements
- Python 3.11+ managed with [`uv`](https://docs.astral.sh/uv/) (`winget install astral-sh.uv`).
- `mcp` SDK 2.x. Note that in 2.x `FastMCP` was renamed `MCPServer`.
- A built and deployed Nexus (see the root README) reachable at `NEXUS_URL`.
- For `start_e3d`/`stop_e3d`: a local working folder with `Start-Nexus.ps1`, `Stop-Nexus.ps1` and
  `config\projects.json` (see below).

## Install (other users)
Prerequisites: Windows, [`uv`](https://docs.astral.sh/uv/), and a Nexus add-in built and deployed in your
E3D (see the root README). Nothing else needs installing: `uvx` fetches the server and its dependencies.

```
# Run straight from the git repository (no clone needed)
uvx --from "git+https://github.com/TEN-SPIT/AVEVA_Nexus#subdirectory=mcp" nexus-mcp

# or from a local clone
uvx --from <repo>\mcp nexus-mcp
```

The server speaks MCP over stdio, so you normally do not run it by hand; register it in your client.

### Register it in Claude Code
```
claude mcp add nexus --scope user -e NEXUS_LOCAL_DIR=<folder-with-Start-Nexus.ps1-and-config> -- uvx --from "git+https://github.com/TEN-SPIT/AVEVA_Nexus#subdirectory=mcp" nexus-mcp
```
Put the server name before `-e`. Restart Claude Code to see the tools (`mcp__nexus__*`). Add
`-e NEXUS_URL=...` / `-e NEXUS_TOKEN=...` if Nexus is not on `http://localhost:5001`. The full list of
variables is below. `NEXUS_LOCAL_DIR` is only needed for `start_e3d`/`stop_e3d`, and
`NEXUS_WRITABLE_PROJECTS` stays unset (read-only) unless you deliberately allow writing to a test project.

### Register it from a clone (development)
```
claude mcp add nexus --scope user -- <path-to>\uv.exe run --directory <repo>\mcp server.py
```

## Tools
| Tool | What it does |
|---|---|
| `session_info` | Project, MDB, user, module, E3D version, read-only flag |
| `query_elements` | Find elements by type, scope, PML condition or `name_like` wildcard; read attributes; paging; `compact=true` (default) returns columns + row lists (fewer tokens); both shapes include `next_offset` (null on the last page) |
| `count_elements` | Just the total for a type/scope/condition |
| `get_attributes` | Read attributes of many known elements in one call |
| `list_children` | Walk the hierarchy: owner and children, optional type filter |
| `attribute_list` | Valid attributes of an element type |
| `batch` | Up to 50 read calls in one round-trip (read routes only; falls back to sequential calls on an older Nexus) |
| `resolve_names` | Names -> dbref and type (on an older Nexus it uses PML, so it needs a writable session) |
| `element_tree` | A whole subtree in one call (depth, type filter, limit); falls back to repeated Children |
| `describe_element` | Name, type, owner, common attributes valid for the type and a children count by type |
| `pml_eval` | Evaluate PML expressions and get their values (writable session only; through the write guard) |
| `call_pml_function` | Call a sandbox/PMLLIB function with safely quoted arguments (writable session only; through the write guard) |
| `run_pml` | Run PML and read variables back, **with the real E3D messages** in `Log` |
| `save_pml` / `delete_pml` / `list_pml` | Keep test functions/objects in a sandbox folder E3D loads from |
| `save_work` | The only way to commit: `SAVEWORK` of ONE allowed database, then `UNCLAIM ALL` |
| `e3d_projects` | Projects this machine can start (never shows credentials) |
| `start_e3d` / `stop_e3d` | Start (with a login dialog) or stop the local headless session; stopping is refused while elements are still claimed |

Element types are full names; `EQUI` is accepted by `query_elements` and mapped to `EQUIPMENT`.

The server keeps one keep-alive HTTP connection to Nexus (5 s connect timeout, 60 s read, 180 s for PML). Tools that
need the newer Nexus routes (`Batch`, `Element/Resolve`, `Element/Tree`) detect a 404 and fall back to older routes.

## Resources
Read-only context a client can attach without a tool call.

| URI | Content |
|---|---|
| `nexus://session` | Same as `session_info` |
| `nexus://projects` | Same as `e3d_projects` (names, ports, modes; never credentials) |
| `nexus://attributes/{type}` | Valid attributes of an element type (template; cached per process; `EQUI` maps to `EQUIPMENT`) |
| `nexus://guide/pml` | Short PML cheat-sheet for `run_pml` (items not verified against E3D are marked) |
| `nexus://guide/safety` | Write rules: read-only default, `write_dbs`, `save_work`, refused constructs |

## Prompts
User-invocable templates: `explore_site(site)`, `test_pml_function(name)`, `safe_edit(task)`
(plan, `run_pml` in allowed databases, verify, ask before `save_work`) and `audit_zone(zone)`
(generic read-only audit).

## Structured output and naming
`query_elements`, `count_elements`, `element_tree` and `describe_element` have typed return models
(`models.py`), so the SDK publishes an `outputSchema`. Convention: keys coming from Nexus keep its
PascalCase (`Total`, `Rows`, `Items`); fields added by this server are lowercase snake_case
(`next_offset`, and the existing `describe_element` keys). Existing keys were not renamed.

### Testing PML without a person in the loop
`run_pml` wraps every script with E3D's `ALPHA LOG /<file>` and returns the file as `Log`. It holds the
output of `$P` and the real error text with its code and position, e.g.

```
(47,62)   CP: Incomplete command line
 In line 2 of PML function mcpbroken
  !y = !x +^^
```

`save_pml` writes a function/object into one fixed sandbox folder (`NEXUS_PML_SANDBOX`, default
`C:\Temp\PMLRepos\_mcp_sandbox`, which must be under a folder in the session's PMLLIB). E3D finds it
by name and reloads it automatically when saved again; a **new** file needs `PML REHASH` once (only the first PMLLIB folder; `PML REHASH ALL` is refused
because it rewrites the `pml.index` of every PMLLIB folder, shared server folders included).
Forms cannot be loaded in a headless session (`GRAPHICS OFF`), so keep logic in functions or objects.

## Environment variables
| Variable | Default | Meaning |
|---|---|---|
| `NEXUS_URL` | `http://localhost:5001` | Where Nexus listens at startup. After `start_e3d` (started or already running) the client follows that project's `port` on the same host, unless `NEXUS_URL` is set explicitly to a non-local host |
| `NEXUS_TOKEN` | empty | Sent as `X-Nexus-Token` (needed when Nexus is not on localhost) |
| `NEXUS_LOCAL_DIR` | `AVEVA_Nexus_local` next to the repository clone | Folder with the start/stop scripts and `config\projects.json` |
| `NEXUS_WRITABLE_PROJECTS` | empty | Comma-separated project keys for which `start_e3d(writable=true)` is allowed |
| `NEXUS_PML_SANDBOX` | `C:\Temp\PMLRepos\_mcp_sandbox` | Where `save_pml` writes |
| `NEXUS_AUDIT_LOG` | `%LOCALAPPDATA%\NexusMCP\audit.jsonl` | Append-only audit trail (see Security model) |

## Security model
- Nexus starts **read-only** and blocks `PML/Execute` and `PML/Run`. Writing needs `writable=true`
  **and** the project listed in `NEXUS_WRITABLE_PROJECTS`; use a test MDB, never production.
- **Write guard** (`write_guard.py`). Which databases may be written is configured per MDB in
  `projects.json` as `write_dbs` (glob patterns on database names, e.g. `*/DESI/DEMO-MCP-TEST`); with
  no list nothing can be written. The barrier is how things are *saved*: `SAVEWORK` is refused inside
  `run_pml` (and inside code saved with `save_pml`), and `save_work(db)` only runs `SAVEWORK <db>` for
  an allowed database. On E3D 3.1 `SAVEWORK <db>` saves only that database, so changes made in any
  other database stay unsaved and vanish when the session stops. On top of that, a script that names
  an element (`=123/45` or `/NAME`) in a database that is not allowed is refused before it runs, and
  if the current element is left in such a database it is moved back to WORLD. The last two are
  best-effort (they cannot see through variables or computed names); the commit gate is the barrier.
- **Claims.** In a `MULTIWRITE` database whatever a session creates or edits stays claimed by it, and
  `SAVEWORK` does not release it. `save_work` therefore runs `UNCLAIM ALL` right after saving (E3D only
  releases saved elements), and `stop_e3d` refuses to kill a session that still holds claims, because
  that leaves orphan claims nobody else can edit until an administrator releases them. `force=true`
  overrides it.
- **What the pre-run scan refuses in a writable run** (best effort, a static text scan that cannot see
  through values computed at run time; the commit gate above is the real barrier): `SAVEWORK` and any
  abbreviation (`sav...`) where a statement can start (line start, after `;`, `then`, `else`, `handle
  any`, a `$x` prefix); `$M` macros; `$!x` / `$!!x` used as a command; file I/O (`object FILE`,
  `.open(`/`.writefile(`...); `ALPHA LOG` (the server owns the log). Words inside strings and comments,
  names (`/SAVER-01`) and attributes (`:SAVINGS`) are not flagged. The same scan covers `run_pml`,
  `pml_eval`, `call_pml_function` and the content of `save_pml`.
- **Fail closed.** A script that refers to more than 60 distinct elements is refused (split it). If the
  E3D log cannot be read (Nexus on another machine, a temp path with spaces, empty log) a writable
  script that names elements is refused because their databases cannot be checked. `stop_e3d` also
  refuses when the claim list cannot be read (unknown is not "none"), unless `force=true`; it checks
  the claims of the project being stopped (its own port).
- **Connection errors.** A dropped connection (E3D restarted) gives "Connection to Nexus lost". Reads
  (GET and read-only POST routes, `Batch` of reads) are retried once; PML routes never are.
- **Audit log.** Every write-capable call (`run_pml`/`pml_eval`/`call_pml_function` in a writable
  session, `save_work`, `save_pml`, `delete_pml`, `start_e3d`, `stop_e3d`) appends one JSON line to
  `NEXUS_AUDIT_LOG`: UTC time, tool, project, MDB, sha256 and first 200 characters of the script, and
  the outcome. No passwords or tokens are ever recorded. Python logging goes to stderr.
- **Tool annotations.** Every tool declares MCP annotations (read-only for reads; destructive for
  `save_work`, `stop_e3d`, `delete_pml`, `run_pml`, `pml_eval`, `call_pml_function`). They are hints
  for the client; the guard enforces. `start_e3d` is asynchronous and reports progress while waiting.
- **Credentials are never stored.** `projects.json` holds no password. `start_e3d` opens a Windows
  credential dialog; the password goes from the dialog to E3D through environment variables and never
  through a tool argument, so the model does not see it. A project can set `"auth": "windows"` to log
  in without a password through Active Directory (supported by AVEVA per project; **untested** with
  E3D 3.1 TTY). The password still appears briefly on `mon.exe`'s command line during launch, so
  names and passwords with spaces or `& | < > ^ % "` are refused.
- `save_pml` only accepts files named `mcp...` that define a single `mcp...` function/object/form, so
  test code can never shadow a real tool in the shared PMLLIB.
- The MCP server is not the security boundary: Nexus's read-only mode is.

## `projects.json` (in `NEXUS_LOCAL_DIR\config`)
One entry per project key: `project`, `mdb`, `module`, `module3d`, `product`, `mon`, `init`, `port`,
`user` (suggested login name), `auth` (`password` or `windows`) and `write_dbs` (see above). Entries that
share a port can only run one at a time.

## Tests
```
uv run pytest
```
Runs without E3D: Nexus and the credential dialog are simulated.

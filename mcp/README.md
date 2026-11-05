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

## Register it in Claude Code
```
claude mcp add nexus --scope user -- <path-to>\uv.exe run --directory <repo>\mcp server.py
```
Add `-e NAME=value` for the environment variables below (put the server name before `-e`).
Restart Claude Code to see the tools (`mcp__nexus__*`).

## Tools
| Tool | What it does |
|---|---|
| `session_info` | Project, MDB, user, module, E3D version, read-only flag |
| `query_elements` | Find elements by type, scope, PML condition; read attributes; paging |
| `get_attributes` | Read attributes of many known elements in one call |
| `list_children` | Walk the hierarchy: owner and children, optional type filter |
| `attribute_list` | Valid attributes of an element type |
| `run_pml` | Run PML and read variables back, **with the real E3D messages** in `Log` |
| `save_pml` / `delete_pml` / `list_pml` | Keep test functions/objects in a sandbox folder E3D loads from |
| `save_work` | The only way to commit: `SAVEWORK` of ONE allowed database, then `UNCLAIM ALL` |
| `e3d_projects` | Projects this machine can start (never shows credentials) |
| `start_e3d` / `stop_e3d` | Start (with a login dialog) or stop the local headless session; stopping is refused while elements are still claimed |

Element types are full names (`EQUIPMENT`, not `EQUI`).

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
by name and reloads it automatically when saved again; a **new** file needs `PML REHASH ALL` once.
Forms cannot be loaded in a headless session (`GRAPHICS OFF`), so keep logic in functions or objects.

## Environment variables
| Variable | Default | Meaning |
|---|---|---|
| `NEXUS_URL` | `http://localhost:5001` | Where Nexus listens |
| `NEXUS_TOKEN` | empty | Sent as `X-Nexus-Token` (needed when Nexus is not on localhost) |
| `NEXUS_LOCAL_DIR` | `AVEVA_Nexus_local` next to the repository clone | Folder with the start/stop scripts and `config\projects.json` |
| `NEXUS_WRITABLE_PROJECTS` | empty | Comma-separated project keys for which `start_e3d(writable=true)` is allowed |
| `NEXUS_PML_SANDBOX` | `C:\Temp\PMLRepos\_mcp_sandbox` | Where `save_pml` writes |

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

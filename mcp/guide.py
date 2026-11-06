"""Static markdown served as MCP resources (nexus://guide/pml and nexus://guide/safety).

Everything here is backed by the README, the tool docstrings or the write guard. Anything that
comes from general PML/E3D knowledge and was not checked against this server is marked
"(unverified)".
"""

PML_GUIDE = """\
# PML cheat-sheet for the Nexus MCP

## How run_pml works
- `run_pml(script, variables)` runs PML in the live E3D session. One statement per line; plain
  lines run one by one, complete `if/do/handle` blocks run as a whole.
- A failing line does NOT stop the other units: always check `Ok` and `Errors`. Each error ends
  with E3D's own message, e.g. `[E3D: PML: Function not found]`. `Log` holds what E3D printed
  (`$P` and `Q` output).
- Variables you want back must be GLOBAL (`!!x`) and named in `variables` without the `!!`.
  Local `!x` variables do not survive between lines/units. A missing variable comes back null.
- Do not use `_` in variable names: `!!r_root` is read as `!!r` (verified on E3D 3.1).
- On a graphical E3D do not put `@` in `$P` text: `$P @@0` prints just `@` (verified on E3D 3.1).
- Blocked while Nexus is read-only (the default): check `session_info`, then `start_e3d` with
  `writable=true` only on a test project and only if the user allows it.
- `define function/method/object/form` cannot be typed in run_pml: write them with `save_pml`
  (name must start with `mcp`), run `PML REHASH` once for a NEW file, then call them with
  `call_pml_function` or from run_pml. Never `PML REHASH ALL` (refused: it rewrites shared
  pml.index files).
- `pml_eval(["1 + 2", "NAME OF /SITE-A"])` evaluates single expressions (writable session only).
- `save_work(db)` is the ONLY way to save. `SAVEWORK` inside run_pml is refused.

## Reading
```
Q DBNAME          -- database of the current element
Q CLAIMLIST       -- elements this session has claimed
Q REF OF /NAME
Q TYPE OF /NAME
$P some text      -- print to Log
```
Prefer the read tools (`query_elements`, `describe_element`, `element_tree`) over PML for reading.

## Modelling snippets (used live on E3D 3.1; check the result with `element_tree`)
```
NEW ZONE /MCP-ZONE
NEW EQUI /MCP-EQUI
POS E 5000 N 5000 U 0      -- position of the equipment
NEW CYLI
DIAM 1500
HEIG 3000
POS E 0 N 0 U 2500          -- primitives are positioned relative to their equipment
NEW BOX
XLEN 1800
YLEN 700
ZLEN 200
NEW DISH
DIAM 1500
HEIG 375
ORI Z IS D                  -- e.g. a bottom head pointing down
ADD /MCP-EQUI              -- add to the 3D view
REMOVE /MCP-EQUI           -- remove from the 3D view
```
Create things only inside a database listed in `write_dbs` for the MDB; see `nexus://guide/safety`.
"""

SAFETY_GUIDE = """\
# Write safety rules of the Nexus MCP

1. **Read-only by default.** Nexus starts read-only and blocks `run_pml`, `pml_eval` and
   `call_pml_function`. Writing needs `start_e3d(writable=true)` AND the project listed in
   `NEXUS_WRITABLE_PROJECTS`. Never ask for it on a production project without the user's say-so.
2. **Allowed databases.** `write_dbs` (glob patterns on database names) is set per MDB in
   `projects.json`. With none configured nothing can be written.
3. **Saving.** `save_work(db)` is the only way to commit: it runs `SAVEWORK <db>` for ONE allowed
   database, then `UNCLAIM ALL`. Changes in other databases stay unsaved and vanish when the session
   stops. Tell the user which database you save and why, before calling it.
4. **Refused constructs in a writable run** (`run_pml`, `pml_eval`, `call_pml_function`, `save_pml`
   content): `SAVEWORK` and abbreviations (`sav...`) where a statement can start; `$M` macros;
   `$!x` / `$!!x` used as a command; file I/O (`object FILE`, `.open(`, `.writefile(`...);
   `ALPHA LOG`; `PML REHASH ALL`.
5. **Element references.** A script naming an element (`=123/45` or `/NAME`) in a database that is
   not allowed is refused; more than 60 distinct elements is refused (split it). If the current
   element ends up in a non-writable database it is moved back to WORLD.
6. **Claims.** Edited elements stay claimed by the session until `save_work`. `stop_e3d` refuses
   while claims are pending unless `force=true` (only with the user's explicit say-so).
7. **Credentials.** Never ask for or pass a password; `start_e3d` takes it from the credential
   store or a dialog on the user's screen.
8. **Sandbox.** `save_pml` only accepts files named `mcp...` defining a single `mcp...` thing.

Recommended flow for a change: plan, run_pml in an allowed DB, verify with `element_tree` /
`get_attributes`, show the user, and only then `save_work`.
"""

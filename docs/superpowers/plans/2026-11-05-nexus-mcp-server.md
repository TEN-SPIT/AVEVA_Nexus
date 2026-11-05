# Nexus MCP server (Python) - implementation plan

Goal: let Claude (and other MCP clients) use a running E3D session through the Nexus
HTTP add-in. The MCP server is a separate Python program in `mcp/`; it never runs
inside E3D. Built step by step together with the maintainer, who wants to learn how
an MCP server works.

```
Claude Code --(MCP, stdio / JSON-RPC)--> mcp/server.py --(HTTP)--> Nexus inside E3D (des.exe)
```

## Stack
- Python, managed with `uv` (`uv add`, `uv run`; dependencies in `mcp/pyproject.toml`).
- `mcp` SDK **2.x**: the high-level server class is `mcp.server.mcpserver.MCPServer`
  (renamed from `FastMCP`; `from mcp.server.fastmcp import FastMCP` no longer works).
- `httpx` for the HTTP client.
- Config by environment variables: `NEXUS_URL` (default `http://localhost:5001`),
  `NEXUS_TOKEN` (empty for the local no-token setup).
- stdio transport: **never `print()`** in the server (stdout is the protocol channel);
  log to stderr.

## Steps
1. **Fundamentals** - what MCP is: child process, JSON-RPC over stdio, handshake
   (`initialize`), `tools/list`, `tools/call`. The tool docstring is the description
   the model reads to decide when to call it. Done.
2. **Skeleton** - `mcp/`, `uv init`, one tool `session_info` (`GET /api/Session/Info`),
   tested by hand with raw JSON-RPC, registered in Claude Code at user scope
   (`claude mcp add nexus --scope user -- <uv.exe> run --directory <repo>\mcp server.py`).
   Done.
3. **Nexus HTTP client** - one small module: base URL, token header, timeouts, and
   translation of Nexus errors (`Success=false`, 400/401/403/404, connection refused)
   into short readable messages. Tools return the useful part (`Data`), not the raw envelope.
4. **Query tools** - `query_elements` (`Element/Query`), `get_attributes` (`Element/Get`),
   `list_children` (`Element/Children`), `attribute_list` (`Element/AttributeList`),
   `run_pml` (`PML/Run`). Descriptions written for the model: when to use it, limits
   (`limit` max 1000), scope must be a dbref, read-only by default.
5. **Start / stop E3D** - tools that call `Start-Nexus.ps1` / `Stop-Nexus.ps1`
   (local setup, ~30 s cycle).
6. **Tests and docs** - unit tests with Nexus mocked (`httpx.MockTransport`), a
   smoke test against a real session, `mcp/README.md`.

## Open points
- Functions and forms (`define function/form`) can only be loaded from files in the
  `PMLLIB`: needs a Nexus endpoint or tool that writes a test file and reloads it.
- The MCP server is not the security boundary: Nexus read-only mode stays the default.

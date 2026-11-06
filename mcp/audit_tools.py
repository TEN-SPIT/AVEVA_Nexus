"""MCP tools of the model data-quality audit. The server registers them with:

    import audit_tools
    audit_tools.register(mcp)
"""

from mcp.types import ToolAnnotations

import model_audit

READ_ONLY = ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=False)
# Export only writes a local CSV file; it never changes the E3D model.
LOCAL_FILE = ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=True, open_world_hint=False)


def audit_rules() -> list[dict]:
    """List the model-audit rules: id, title (en/es), severity, kind, element types, params,
    and whether the rule runs by default. Use the ids in audit_model / audit_export `rules`."""
    return model_audit.rules_summary()


def audit_model(scope: str | None = None, rules: list[str] | None = None, max_findings: int = 200,
                offset: int = 0, max_elements: int = model_audit.MAX_ELEMENTS, refresh: bool = False) -> dict:
    """Read-only data-quality audit of the E3D model: runs the audit rules (missing specs,
    unconnected branch heads/tails, components without SPREF, empty equipment/zones, naming,
    out-of-range bores...) and returns the defects found. Nothing in the model is changed.

    - scope: dbref ("=123/456") or name ("/SITE-A") of a SITE/ZONE/... to audit; omit for the
      whole database (slow on big projects: prefer a site or zone).
    - rules: rule ids from audit_rules; omit to run the default rules.
    - max_findings / offset: page through the findings (max 1000 per call). The audit result is
      kept for 5 minutes, so the next page costs no E3D queries; refresh=true re-runs it.
    - max_elements: cap of elements read per rule and type (default 20000); past it the rule is
      flagged truncated.

    Returns {rules: {id: {checked, findings, truncated, ...}}, by_severity, findings_total,
    findings: [{rule, severity, dbref, name, type, message, value}], more, warnings, truncated}.
    Read `warnings`: a rule whose attribute does not exist on a type is skipped there.
    """
    return model_audit.audit_model(scope, rules, max_findings, offset, max_elements, refresh)


def audit_export(scope: str | None = None, path: str | None = None, rules: list[str] | None = None,
                 max_elements: int = model_audit.MAX_ELEMENTS, refresh: bool = False) -> dict:
    """Run the model audit (same as audit_model) and write ALL findings to a CSV file
    (UTF-8, opens in Excel). The E3D model is only read; the only thing written is that local file.

    - path: a .csv file or a folder; default %LOCALAPPDATA%\\NexusMCP\\audits\\model-audit-<time>.csv.
    Returns the file path and the summary counts.
    """
    return model_audit.audit_export(scope, path, rules, max_elements, refresh)


def register(mcp) -> None:
    mcp.tool(annotations=READ_ONLY)(audit_rules)
    mcp.tool(annotations=READ_ONLY)(audit_model)
    mcp.tool(annotations=LOCAL_FILE)(audit_export)

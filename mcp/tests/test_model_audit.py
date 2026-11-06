"""Model data-quality audit against a fake Nexus holding a small synthetic model with known defects."""

import csv
import json

import pytest
from mcp.server.mcpserver.exceptions import ToolError

import audit_tools
import model_audit
import nexus_client
import ops
from nexus_client import NexusError

# Attributes each fake type has (what AttributeList returns). Anything else reads as null.
ATTRS = {
    "SITE": ["NAME", "OWNER"], "ZONE": ["NAME", "OWNER"],
    "PIPE": ["NAME", "OWNER", "PSPEC", "BORE", "ISPEC"],
    "BRAN": ["NAME", "OWNER", "PSPEC", "HREF", "TREF", "HCON", "TCON", "HBOR"],
    "ELBO": ["NAME", "OWNER", "SPREF"], "VALV": ["NAME", "OWNER", "SPREF"],
    "EQUIPMENT": ["NAME", "OWNER"], "BOX": ["NAME", "OWNER"],
    "NOZZ": ["NAME", "OWNER", "CATREF"],
}


def model():
    e = {}

    def add(ref, type_, owner, **attrs):
        e[ref] = {"TYPE": type_, "OWNER": owner, **attrs}

    add("=1/1", "SITE", None, NAME="/SITE")
    add("=1/2", "ZONE", "=1/1", NAME="/ZONE-PI")
    add("=1/3", "ZONE", "=1/1", NAME="/ZONE-EMPTY")
    add("=1/4", "ZONE", "=1/1", NAME="/ZONE-EQ")
    # pipes
    add("=2/1", "PIPE", "=1/2", NAME="/P-100", PSPEC="/A150", BORE="150mm", ISPEC="unset")
    add("=2/2", "PIPE", "=1/2", NAME="/p 101", PSPEC="unset", BORE="5000mm", ISPEC="/HC")   # bad name, spec, bore
    add("=2/3", "PIPE", "=1/2", NAME="/P-102", PSPEC="/A150", BORE="80mm", ISPEC="unset")   # no branches
    add("=3/1", "BRAN", "=2/1", NAME="/P-100/B1", PSPEC="/A150", HREF="=4/1", TREF="=0/0",
        HCON="FBB", TCON="OPEN", HBOR="150mm")                                             # open tail: fine
    add("=3/2", "BRAN", "=2/2", NAME="/p 101/B1", PSPEC="unset", HREF="Nulref", TREF="=4/2",
        HCON="FBB", TCON="FBB", HBOR="abc")                                                # unconnected head
    add("=3/3", "BRAN", "=2/2", NAME="/p 101/B2", PSPEC="/A150", HREF="=4/1", TREF="=0/0",
        HCON="FBB", TCON="FBB", HBOR="100mm")                                              # tail + shared head
    add("=5/1", "ELBO", "=3/1", NAME="", SPREF="/A150/EL90")
    add("=5/2", "VALV", "=3/1", NAME="", SPREF="=0/0")                                     # no SPREF
    # equipment
    add("=4/9", "EQUIPMENT", "=1/4", NAME="/E-1")
    add("=4/8", "EQUIPMENT", "=1/4", NAME="/E-2")                                          # empty
    add("=6/1", "BOX", "=4/9", NAME="")
    add("=4/1", "NOZZ", "=4/9", NAME="/E-1/N1", CATREF="/CAT/N150")
    add("=4/2", "NOZZ", "=4/9", NAME="/E-1/N2", CATREF="unset")                           # no CATREF
    return e


class FakeNexus:
    def __init__(self, elements, owner_as_name=False):
        self.e = elements
        self.calls = []
        self.owner_as_name = owner_as_name
        self.no_batch = False

    def _resolve(self, ref):
        if ref.startswith("="):
            return ref if ref in self.e else None
        return next((k for k, v in self.e.items() if v.get("NAME") == ref), None)

    def _inside(self, ref, scope):
        while ref:
            if ref == scope:
                return True
            ref = self.e[ref]["OWNER"]
        return False

    def _value(self, ref, attr):
        el = self.e[ref]
        if attr not in ATTRS[el["TYPE"]]:
            return None
        if attr == "OWNER" and self.owner_as_name and el["OWNER"]:
            return self.e[el["OWNER"]]["NAME"]
        return el.get(attr, "unset")

    def query(self, body):
        type_ = body["type"]
        if type_ not in ATTRS:
            raise NexusError(f"Nexus 400: Unknown element type '{type_}'", 400)
        scope = self._resolve(body["scope"]) if body.get("scope") else None
        if body.get("scope") and not scope:
            raise NexusError("Nexus 404: Scope not found", 404)
        refs = [r for r, v in self.e.items() if v["TYPE"] == type_ and (not scope or self._inside(r, scope))]
        off, lim = body.get("offset", 0), min(body.get("limit", 100), 1000)
        cols = body.get("attributes", [])
        rows = [{"dbref": r, **{a: self._value(r, a) for a in cols}} for r in refs[off:off + lim]]
        return {"Total": len(refs), "Offset": off, "Limit": lim, "Columns": ["dbref", *cols], "Rows": rows}

    def handle(self, method, path, params, body):
        if path == "/api/Element/Query":
            return self.query(body)
        if path == "/api/Element/AttributeList":
            if params["type"] not in ATTRS:
                raise NexusError("Nexus 400: Unknown element type", 400)
            return ATTRS[params["type"]]
        if path == "/api/Element/Children":
            kids = [{"dbref": r, "type": v["TYPE"], "name": v["NAME"]} for r, v in self.e.items()
                    if v["OWNER"] == params["dbref"]]
            return {"Owner": params["dbref"], "Children": kids[:params.get("limit", 500)]}
        if path == "/api/Batch":
            if self.no_batch:
                raise NexusError("Nexus 404: Route Not Found", 404, True)
            out = []
            for item in body["requests"]:
                try:
                    out.append({"Status": 200, "Data": self.handle(item["method"], item["path"], item.get("query"),
                                                                    item.get("body")), "Error": None})
                except NexusError as error:
                    out.append({"Status": error.status, "Data": None, "Error": str(error)})
            return out
        raise AssertionError(f"unexpected call {method} {path}")

    def __call__(self, method, path, *, params=None, body=None):
        self.calls.append((method, path, params, body))
        assert path in ops._READ_PATHS or path == "/api/Batch", "the audit must only read"
        return self.handle(method, path, params, body)


@pytest.fixture(autouse=True)
def fresh_state(monkeypatch, tmp_path):
    ops._missing_routes.clear()
    ops._attribute_cache.clear()
    model_audit._cache.clear()
    monkeypatch.delenv("NEXUS_AUDIT_RULES", raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))


@pytest.fixture
def nexus(monkeypatch):
    fake = FakeNexus(model())
    monkeypatch.setattr(nexus_client, "call", fake)
    return fake


def by_rule(result):
    out = {}
    for f in result["findings"]:
        out.setdefault(f["rule"], set()).add(f["dbref"])
    return out


ALL = ["PIPE-SPEC", "PIPE-BORE-RANGE", "BRAN-BORE-RANGE", "PIPE-INSULATION-SPEC", "BRAN-HEAD-UNCONNECTED",
       "BRAN-TAIL-UNCONNECTED", "BRAN-HEAD-SHARED", "COMP-SPREF", "NOZZ-CATREF", "EQUI-EMPTY", "PIPE-NO-BRANCH",
       "ZONE-EMPTY", "NAME-CONVENTION"]


def test_rule_pack_loads_and_is_generic():
    rules = model_audit.load_rules()
    assert set(ALL) <= set(rules)
    for r in rules.values():
        assert r["title"]["en"] and r["title"]["es"]
    assert {r["kind"] for r in rules.values()} == set(model_audit.KINDS)
    assert rules["PIPE-INSULATION-SPEC"].get("default") is False
    listed = audit_tools.audit_rules()
    assert {r["id"] for r in listed} == set(rules)


def test_every_rule_kind_finds_the_known_defects(nexus):
    result = model_audit.run_audit("/SITE", ALL)
    found = by_rule(result)
    assert found["PIPE-SPEC"] == {"=2/2", "=3/2"}                     # required, 'unset'
    assert found["PIPE-BORE-RANGE"] == {"=2/2"}                        # range, units stripped
    assert found["BRAN-BORE-RANGE"] == {"=3/2"}                        # 'abc' is not a number
    assert found["PIPE-INSULATION-SPEC"] == {"=2/1", "=2/3"}
    assert found["BRAN-HEAD-UNCONNECTED"] == {"=3/2"}                  # Nulref
    assert found["BRAN-TAIL-UNCONNECTED"] == {"=3/3"}                  # =0/0, the OPEN tail is skipped
    assert found["BRAN-HEAD-SHARED"] == {"=3/1", "=3/3"}               # unique, nulls ignored
    assert found["COMP-SPREF"] == {"=5/2"}
    assert found["NOZZ-CATREF"] == {"=4/2"}
    assert found["EQUI-EMPTY"] == {"=4/8"}                             # children of any type
    assert found["PIPE-NO-BRANCH"] == {"=2/3"}                         # children via OWNER query
    assert found["ZONE-EMPTY"] == {"=1/3"}
    assert found["NAME-CONVENTION"] == {"=2/2"}                        # pattern
    f = next(x for x in result["findings"] if x["rule"] == "COMP-SPREF")
    assert f == {"rule": "COMP-SPREF", "severity": "error", "dbref": "=5/2", "name": None, "type": "VALV",
                 "message": "SPREF references nothing", "value": "=0/0"}
    assert result["findings"][0]["severity"] == "error"               # sorted by severity
    assert result["rules"]["PIPE-SPEC"]["checked"] == 6 and not result["truncated"]
    assert result["by_severity"]["error"] == 4


def test_scope_limits_the_audit(nexus):
    result = model_audit.run_audit("=1/4", ["EQUI-EMPTY", "PIPE-SPEC", "NOZZ-CATREF"])
    assert by_rule(result) == {"EQUI-EMPTY": {"=4/8"}, "NOZZ-CATREF": {"=4/2"}}


def test_owner_given_as_a_name_still_matches_parents(nexus):
    nexus.owner_as_name = True
    assert by_rule(model_audit.run_audit("/SITE", ["PIPE-NO-BRANCH"]))["PIPE-NO-BRANCH"] == {"=2/3"}


def test_first_pages_go_through_one_batch(nexus):
    model_audit.run_audit("/SITE", ["PIPE-SPEC", "COMP-SPREF", "NOZZ-CATREF"])
    paths = [c[1] for c in nexus.calls]
    assert set(paths) == {"/api/Batch"}
    assert len(paths) == 1          # every first page in one batch (no AttributeList pre-check)


def test_unknown_attribute_is_skipped_with_a_warning(nexus):
    nexus.e["=9/1"] = {"TYPE": "BOX", "OWNER": "=1/4", "NAME": ""}
    rules = model_audit.load_rules()
    rules["X"] = model_audit.validate_rule({"id": "X", "title": {"en": "x"}, "severity": "error", "kind": "required",
                                            "types": ["PIPE", "BOX"], "params": {"attributes": ["PSPEC"]}})
    rules["X"]["_source"] = "test"
    model_audit_load = model_audit.load_rules
    try:
        model_audit.load_rules = lambda: rules
        result = model_audit.run_audit("/SITE", ["X"])
    finally:
        model_audit.load_rules = model_audit_load
    assert by_rule(result)["X"] == {"=2/2"}        # BOX is not reported for a PSPEC it cannot have
    assert any("BOX has no attribute PSPEC" in w for w in result["warnings"])


def test_attribute_list_is_not_trusted_null_cells_still_count(nexus):
    """E3D 3.1's AttributeList leaves out readable attributes (PSPEC, SPREF, CATREF), so it is not
    consulted: an attribute with real values on some rows is checked, and its null rows are findings."""
    original = nexus.handle

    def handle(method, path, params, body):
        if path == "/api/Element/AttributeList":
            raise AssertionError("AttributeList must not be called")
        return original(method, path, params, body)

    nexus.handle = handle
    result = model_audit.run_audit("/SITE", ["PIPE-SPEC"])
    assert by_rule(result)["PIPE-SPEC"] == {"=2/2", "=3/2"}


def test_unknown_type_becomes_a_warning_not_a_failure(nexus):
    result = model_audit.run_audit("/SITE", ["COMP-SPREF"])     # the pack lists types this fake lacks
    assert by_rule(result)["COMP-SPREF"] == {"=5/2"}
    assert any("TEE" in w for w in result["warnings"])


def test_paging_and_truncation(nexus):
    for i in range(2500):
        nexus.e[f"=7/{i}"] = {"TYPE": "VALV", "OWNER": "=3/1", "NAME": "", "SPREF": "=0/0" if i % 2 else "/S"}
    result = model_audit.run_audit("/SITE", ["COMP-SPREF"])
    assert len(by_rule(result)["COMP-SPREF"]) == 1250 + 1
    queries = [c for c in nexus.calls if c[1] == "/api/Element/Query"]
    assert [c[3]["offset"] for c in queries] == [1000, 2000]     # first page went in the batch
    assert not result["truncated"]

    model_audit._cache.clear()
    capped = model_audit.run_audit("/SITE", ["COMP-SPREF"], max_elements=1500)
    assert capped["truncated"] and capped["rules"]["COMP-SPREF"]["truncated"]
    assert capped["rules"]["COMP-SPREF"]["checked"] == 1500 + 1   # 1500 VALV + the ELBO
    assert any("only the first 1500" in w for w in capped["warnings"])


def test_old_nexus_without_batch_still_works(nexus):
    nexus.no_batch = True
    assert by_rule(model_audit.run_audit("/SITE", ["EQUI-EMPTY"]))["EQUI-EMPTY"] == {"=4/8"}


def test_audit_model_pages_findings_from_cache(nexus):
    first = audit_tools.audit_model("/SITE", ALL, max_findings=5)
    n = len(nexus.calls)
    assert len(first["findings"]) == 5 and first["more"] and first["findings_total"] == 16
    second = audit_tools.audit_model("/SITE", ALL, max_findings=5, offset=15)
    assert len(nexus.calls) == n and len(second["findings"]) == 1 and not second["more"]
    audit_tools.audit_model("/SITE", ALL, refresh=True)
    assert len(nexus.calls) > n


def test_default_rules_skip_opt_in_ones(nexus):
    result = audit_tools.audit_model("/SITE")
    assert "PIPE-INSULATION-SPEC" not in result["rules"] and "PIPE-SPEC" in result["rules"]
    with pytest.raises(ToolError, match="Unknown rule"):
        audit_tools.audit_model("/SITE", ["NOPE"])


def test_export_csv(nexus, tmp_path):
    out = audit_tools.audit_export("/SITE", str(tmp_path / "r.csv"), ["COMP-SPREF", "NOZZ-CATREF"])
    rows = list(csv.DictReader((tmp_path / "r.csv").open(encoding="utf-8-sig")))
    assert out["findings_total"] == 2 and len(rows) == 2
    assert rows[0]["rule"] in ("COMP-SPREF", "NOZZ-CATREF") and rows[0]["title_es"]
    default = audit_tools.audit_export("/SITE", rules=["NOZZ-CATREF"])
    assert "NexusMCP" in default["path"] and default["path"].endswith(".csv")
    with pytest.raises(ToolError, match="CSV"):
        audit_tools.audit_export("/SITE", str(tmp_path / "r.xlsx"))


def test_extra_rule_pack_from_env(nexus, tmp_path, monkeypatch):
    pack = tmp_path / "project.json"
    pack.write_text(json.dumps({"rules": [{"id": "EQ-NAME", "title": {"en": "Equipment tag", "es": "Tag"},
                                           "severity": "warning", "kind": "pattern", "types": ["EQUI"],
                                           "params": {"attribute": "NAME", "regex": "/E-\\d"}}]}))
    monkeypatch.setenv("NEXUS_AUDIT_RULES", str(pack))
    assert by_rule(model_audit.run_audit("/SITE", ["EQ-NAME"])) == {}
    nexus.e["=4/8"]["NAME"] = "/PUMP"
    model_audit._cache.clear()
    assert by_rule(model_audit.run_audit("/SITE", ["EQ-NAME"]))["EQ-NAME"] == {"=4/8"}


@pytest.mark.parametrize("bad", [
    {"id": "A", "title": {"en": "a"}, "severity": "fatal", "kind": "required", "types": ["PIPE"], "params": {"attributes": ["X"]}},
    {"id": "A", "title": {"en": "a"}, "severity": "error", "kind": "magic", "types": ["PIPE"], "params": {}},
    {"id": "A", "title": {"en": "a"}, "severity": "error", "kind": "pattern", "types": ["PIPE"], "params": {"attribute": "NAME", "regex": "("}},
    {"id": "A", "title": {"en": "a"}, "severity": "error", "kind": "range", "types": ["PIPE"], "params": {"attribute": "BORE"}},
    {"id": "A", "title": {"en": "a"}, "severity": "error", "kind": "required", "types": [], "params": {"attributes": ["X"]}},
])
def test_invalid_rules_are_rejected(bad):
    with pytest.raises(ValueError):
        model_audit.validate_rule(bad)


def test_value_helpers():
    assert model_audit.is_unset(None) and model_audit.is_unset(" unset ") and model_audit.is_unset("=0/0")
    assert model_audit.is_unset("NULREF") and not model_audit.is_unset("/A150")
    assert model_audit.number_of("350mm") == 350 and model_audit.number_of("-5.5degC") == -5.5
    assert model_audit.number_of("abc") is None


def test_register_adds_three_tools_with_hints():
    registered = []

    class FakeMcp:
        def tool(self, annotations=None):
            return lambda fn: registered.append((fn.__name__, annotations)) or fn

    audit_tools.register(FakeMcp())
    hints = dict(registered)
    assert set(hints) == {"audit_rules", "audit_model", "audit_export"}
    assert hints["audit_model"].read_only_hint and not hints["audit_export"].destructive_hint


def test_unnamed_elements_are_not_naming_findings(nexus):
    """Query returns an unnamed element's dbref as NAME (seen live on E3D 3.1)."""
    nexus.e["=8/1"] = {"TYPE": "EQUI", "OWNER": "=1/2", "NAME": "=8/1"}
    result = model_audit.run_audit("/SITE", ["NAME-CONVENTION"])
    assert "=8/1" not in by_rule(result).get("NAME-CONVENTION", set())


def test_pipe_bore_range_is_opt_in():
    rules = model_audit.load_rules()
    assert rules["PIPE-BORE-RANGE"].get("default") is False

"""write_guard: nothing may be saved, or knowingly touched, outside the allowed databases."""

import json

import pytest
from mcp.server.mcpserver.exceptions import ToolError

import e3d_launcher
import pml_dev
import write_guard as guard

ALLOWED = "EQ/DESI/DEMO-MCP-TEST"
FORBIDDEN = "MASTER/TEMPLATE"


@pytest.fixture
def world(tmp_path, monkeypatch):
    """A writable DEMO/900-TEST session whose allowed databases are */DESI/DEMO-MCP-TEST."""
    config = tmp_path / "config"
    config.mkdir()
    (config / "projects.json").write_text(
        json.dumps({"DEMOW": {"project": "DEMO", "mdb": "/900-TEST", "write_dbs": ["*/DESI/DEMO-MCP-TEST"]}}),
        encoding="utf-8-sig",
    )
    monkeypatch.setattr(e3d_launcher, "LOCAL_DIR", tmp_path)
    state = {"info": {"Project": "DEMO", "Mdb": "900-TEST", "ReadOnly": False}, "runs": [], "dbs": {}, "ce_db": ALLOWED}

    def fake_run(script, variables):
        state["runs"].append(script)
        log = []
        for line in script.splitlines():
            if line.startswith("$P @@"):
                log.append(line[3:].strip())
            if line.startswith("Q DBNAME OF "):
                db = state["dbs"].get(line[len("Q DBNAME OF "):])
                if db:
                    log.append(f"Dbname {db}")
            elif line == "Q DBNAME":
                log.append(f"Dbname {state['ce_db']}")
        return {"Ok": True, "Errors": [], "Values": {}, "Log": "\n".join(log)}

    monkeypatch.setattr(guard.pml_dev, "run_with_log", fake_run)
    monkeypatch.setattr(guard, "session_info", lambda: state["info"])
    return state


# ---- commit gate ----------------------------------------------------------------------------

@pytest.mark.parametrize("script", ["SAVEWORK", "savework", "  SAVEW", "SAVE", "!x = 1\nSAVEWORK", "!x = 1; savework"])
def test_savework_is_refused_in_scripts(world, script):
    with pytest.raises(ToolError, match="save_work"):
        guard.guarded_run(script, None)
    assert world["runs"] == []


@pytest.mark.parametrize("script", ["!saved = 1", "$P saveworkers", "-- SAVEWORK in a comment", "Q NAME"])
def test_normal_text_is_not_mistaken_for_savework(world, script):
    guard.guarded_run(script, None)


def test_save_work_only_for_allowed_databases(world):
    result = guard.save_work(ALLOWED)
    assert result["saved"] == ALLOWED
    assert world["runs"][0] == f"SAVEWORK {ALLOWED}"


def test_save_work_releases_the_claims_after_saving(world):
    result = guard.save_work(ALLOWED)
    assert world["runs"] == [f"SAVEWORK {ALLOWED}", "UNCLAIM ALL"]  # save first, then release
    assert result["claims_released"] is True


def test_save_work_reports_when_the_claims_could_not_be_released(world, monkeypatch):
    original = guard.pml_dev.run_with_log

    def failing_unclaim(script, variables):
        if script == "UNCLAIM ALL":
            return {"Ok": False, "Errors": ["PML command failed: UNCLAIM ALL"], "Values": {}, "Log": "boom"}
        return original(script, variables)

    monkeypatch.setattr(guard.pml_dev, "run_with_log", failing_unclaim)
    result = guard.save_work(ALLOWED)
    assert result["saved"] == ALLOWED and result["claims_released"] is False
    assert "still be claimed" in result["warning"]


@pytest.mark.parametrize("db", [FORBIDDEN, "GN/IMPORT_2D", "EQ/DESI/DEMO", "EQ/DESI/DEMO-MCP-TEST2", ""])
def test_save_work_refuses_other_databases(world, db):
    with pytest.raises(ToolError):
        guard.save_work(db)
    assert world["runs"] == []


@pytest.mark.parametrize("db", ["EQ/DESI/DEMO-MCP-TEST; SAVEWORK", "EQ/DESI/DEMO-MCP-TEST\nSAVEWORK", "*", "a b"])
def test_save_work_refuses_injection_in_the_database_name(world, db):
    with pytest.raises(ToolError):
        guard.save_work(db)
    assert world["runs"] == []


def test_nothing_can_be_saved_without_a_configured_list(world):
    world["info"]["Mdb"] = "999-OTHER"
    with pytest.raises(ToolError, match="No writable databases"):
        guard.save_work(ALLOWED)


def test_nothing_can_be_saved_in_a_read_only_session(world):
    world["info"]["ReadOnly"] = True
    with pytest.raises(ToolError, match="read-only"):
        guard.save_work(ALLOWED)


# ---- reference pre-check --------------------------------------------------------------------

def test_references_are_extracted_but_arithmetic_and_words_are_not():
    script = "CE /DEMO-MCP-TEST-EQ\nNEW ZONE /MCP-ZONE\n!a = 5/6\nQ NAME OF =123/456\n$P a/b"
    assert guard.references(script) == ["/DEMO-MCP-TEST-EQ", "/MCP-ZONE", "=123/456"]


def test_script_touching_a_forbidden_database_is_refused_before_running(world):
    world["dbs"] = {"/Steel_Template_Site": FORBIDDEN}
    with pytest.raises(ToolError, match="outside the writable databases"):
        guard.guarded_run("CE /Steel_Template_Site\nNEW ZONE /X", None)
    assert all("NEW ZONE" not in run for run in world["runs"])  # the script itself never ran


def test_dbref_into_a_forbidden_database_is_refused(world):
    world["dbs"] = {"=123/456": FORBIDDEN}
    with pytest.raises(ToolError, match="=123/456"):
        guard.guarded_run("CE =123/456", None)


def test_script_on_allowed_and_not_yet_existing_elements_runs(world):
    world["dbs"] = {"/DEMO-MCP-TEST-EQ": ALLOWED}  # /NEW-ZONE does not exist yet
    result = guard.guarded_run("CE /DEMO-MCP-TEST-EQ\nNEW ZONE /NEW-ZONE", None)
    assert result["Ok"] is True
    assert any("NEW ZONE /NEW-ZONE" in run for run in world["runs"])


def test_script_without_references_is_not_resolved(world):
    guard.guarded_run("!x = 1", None)
    assert not any("Q DBNAME OF" in run for run in world["runs"])


# ---- parking the current element ------------------------------------------------------------

def test_current_element_left_in_a_forbidden_database_is_parked_at_world(world):
    world["ce_db"] = FORBIDDEN
    result = guard.guarded_run("!x = 1", None)
    assert world["runs"][-1] == "CE /*"
    assert "moved it back to WORLD" in result["Warnings"][0]


def test_current_element_in_allowed_database_is_left_alone(world):
    result = guard.guarded_run("!x = 1", None)
    assert "CE /*" not in world["runs"]
    assert "Warnings" not in result


# ---- sessions that cannot write -------------------------------------------------------------

def test_read_only_session_is_passed_through_so_nexus_gives_its_own_error(world):
    world["info"]["ReadOnly"] = True
    guard.guarded_run("!x = 1", None)
    assert world["runs"] == ["!x = 1"]


def test_writing_is_refused_when_the_mdb_has_no_write_list(world):
    world["info"]["Mdb"] = "999-OTHER"
    with pytest.raises(ToolError, match="No writable databases"):
        guard.guarded_run("!x = 1", None)


# ---- test code saved to the sandbox ---------------------------------------------------------

@pytest.mark.parametrize("body", ["SAVEWORK", "  savework", "SAVEW"])
def test_sandbox_code_cannot_smuggle_a_savework(tmp_path, monkeypatch, body):
    monkeypatch.setattr(pml_dev, "SANDBOX", tmp_path)
    code = f"define function !!mcpx() is REAL\n  {body}\n  return 1\nendfunction\n"
    with pytest.raises(ToolError, match="SAVEWORK"):
        pml_dev.save("function", "mcpx", code)

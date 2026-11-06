"""Adversarial table: ways to get around the write guard must be refused; legit PML must pass.

The scan is best effort (see pml_dev), so this table documents what IS covered.
"""

import json

import pytest
from mcp.server.mcpserver.exceptions import ToolError

import e3d_launcher
import ops
import pml_dev
import write_guard as guard

ALLOWED = "EQ/DESI/DEMO-MCP-TEST"


@pytest.fixture
def world(tmp_path, monkeypatch):
    config = tmp_path / "config"
    config.mkdir()
    (config / "projects.json").write_text(
        json.dumps({"DEMOW": {"project": "DEMO", "mdb": "/900-TEST", "write_dbs": ["*/DESI/DEMO-MCP-TEST"]}}),
        encoding="utf-8-sig",
    )
    monkeypatch.setattr(e3d_launcher, "LOCAL_DIR", tmp_path)
    state = {"info": {"Project": "DEMO", "Mdb": "900-TEST", "ReadOnly": False}, "runs": [], "log": None}

    def fake_run(script, variables):
        state["runs"].append(script)
        if state["log"] is not None:
            log = state["log"]
        else:
            log = []
            for line in script.splitlines():
                if line.startswith("$P NXMARK"):
                    log.append(line[3:].strip())
                if line.startswith("Q DBNAME OF "):
                    log.append(f"Dbname {ALLOWED}")
                elif line == "Q DBNAME":
                    log.append(f"Dbname {ALLOWED}")
            log = "\n".join(log)
        return {"Ok": True, "Errors": [], "Values": {}, "Log": log}

    monkeypatch.setattr(guard.pml_dev, "run_with_log", fake_run)
    monkeypatch.setattr(guard, "session_info", lambda: state["info"])
    return state


BYPASSES = {
    "abbrev sav": "sav",
    "abbrev savew": "!x = 1\nsavew",
    "after then (same line)": "if (!x eq 1) then savework",
    "after then;": "if (true) then; savew; endif",
    "after else": "if (!x) then\n  !y = 1\nelse savework\nendif",
    "after handle any": "handle any savework\nendhandle",
    "after handle (n,m)": "handle (2,109) savework",
    "after dollar prefix": "$R savework",
    "upper case": "IF (TRUE) THEN SAVEWORK",
    "semicolon": "!a = 1; savework",
    "macro": "$M/x.mac",
    "macro lower": "!a = 1\n  $m /tmp/x",
    "var expansion": "$!!cmd",
    "var expansion after then": "if (true) then $!cmd",
    "file object": "!f = object FILE(|C:/x.txt|)",
    "file open": "!f.open(|WRITE|)",
    "writefile": "!f.writefile(|x|)",
    "alpha log": "ALPHA LOG /C:/x.log",
    "alpha log abbreviated": "alp log end",
    "inside one-line do": "do !i from 1 to 2 sav enddo",
    "after a quote string holding a bar": "!x = 'a|' sav",
    "synonym": "$S X=SAVEWORK\nX",
    "synonym after then": "if (true) then $s X=SAV",
    "rehash all (rewrites shared pml.index files)": "PML REHASH ALL",
    "rehash all lower": "pml rehash all",
}


@pytest.mark.parametrize("script", BYPASSES.values(), ids=BYPASSES.keys())
def test_bypass_is_refused_in_writable_runs(world, script):
    with pytest.raises(ToolError):
        guard.guarded_run(script, None)
    assert world["runs"] == []


@pytest.mark.parametrize("script", BYPASSES.values(), ids=BYPASSES.keys())
def test_bypass_is_refused_through_pml_eval_and_call_inputs(world, script):
    """pml_eval / call_pml_function go through guarded_run, so the same scan applies."""
    with pytest.raises(ToolError):
        guard.guarded_run(f"!!mcpEvalx1 = 1\n{script}", ["mcpEvalx1"])
    assert world["runs"] == []


@pytest.mark.parametrize(
    "kind,content",
    [("function", f"define function !!mcpbad()\n  {line}\nendfunction") for line in
     ("savework", "if (true) then savew", "$M/x.mac", "$!!cmd", "!f.open(|W|)", "ALPHA LOG /x")],
)
def test_save_pml_content_is_scanned(tmp_path, monkeypatch, kind, content):
    monkeypatch.setattr(pml_dev, "SANDBOX", tmp_path)
    with pytest.raises(ToolError):
        pml_dev.save(kind, "mcpbad", content)
    assert not list(tmp_path.rglob("*.pmlfnc"))


def test_more_than_60_references_is_refused_not_silently_truncated(world):
    script = "\n".join(f"CE /ELEM-{i}" for i in range(61))
    with pytest.raises(ToolError, match="Split"):
        guard.guarded_run(script, None)
    assert world["runs"] == []


def test_exactly_60_references_run(world):
    script = "\n".join(f"CE /ELEM-{i}" for i in range(60))
    assert guard.guarded_run(script, None)["Ok"] is True


@pytest.mark.parametrize("log", ["", "(not captured: Nexus is not on this machine, or the temp path has spaces)", "garbage"])
def test_missing_markers_fail_closed(world, log):
    world["log"] = log
    with pytest.raises(ToolError, match="Cannot verify"):
        guard.guarded_run("CE /SOMETHING", None)


def test_script_without_references_needs_no_log(world):
    world["log"] = ""
    guard.guarded_run("!x = 1", None)


LEGIT = {
    "name with SAV": "CE /SAVER-01",
    "attribute :SAVINGS": "!x = :SAVINGS of /DEMO-MCP-TEST-EQ",
    "variable saved": "!saved = 1\n!savedValue = !saved + 1",
    "string with savework": "!c = |savework|\n!d = 'savework'",
    "print text": "$P then save it; $P ok",
    "print variable": "$P value $!x",
    "comment": "-- savework here\n$* sav",
    "plain query": "Q NAME",
    "if block": "if (!a eq 1) then\n  !b = 2\nendif",
    "method call": "!list.append(|x|)\n!n = !list.size()",
    "dollar in string": "!s = |cost $M|",
    "modelling": "NEW EQUI /SAVE-ZONE-P1\nPOS E 100 N 200 U 0\nORI X IS E AND Z IS U\nNEW BOX XLEN 100 YLEN 100 ZLEN 100",
    "string saving": "!!a = 'saving'",
    "rehash first folder only": "PML REHASH",
}


@pytest.mark.parametrize("script", LEGIT.values(), ids=LEGIT.keys())
def test_legit_scripts_are_not_flagged(script):
    assert pml_dev.forbidden_in_writable(script) is None
    assert not guard.has_save_command(script)


def test_legit_script_runs(world):
    assert guard.guarded_run("CE /SAVER-01\n$P done", None)["Ok"] is True


def test_run_query_pml_still_refuses_savework(world):
    with pytest.raises(ToolError):
        guard.run_query_pml("if (true) then savew")


def test_pml_eval_goes_through_the_scan(world):
    with pytest.raises(ToolError):
        ops.pml_eval(["1 then savework"])
    assert not any("savework" in run for run in world["runs"])


def test_call_pml_function_runs_a_normal_call(world):
    assert ops.call_pml_function("mcpecho", ["x"], returns=False)["ok"] is True

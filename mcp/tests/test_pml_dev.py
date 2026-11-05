"""pml_dev: log capture around PML runs and the sandbox rules, with Nexus simulated."""

import pytest
from mcp.server.mcpserver.exceptions import ToolError

import nexus_client
import pml_dev


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(pml_dev, "SANDBOX", tmp_path / "sandbox")
    monkeypatch.setattr(pml_dev, "LOG_DIR", tmp_path / "logs")
    monkeypatch.setattr(nexus_client, "NEXUS_URL", "http://localhost:5001")


FUNCTION = "define function !!mcpecho(!x is STRING) is STRING\n  return !x\nendfunction\n"


# ---- sandbox rules --------------------------------------------------------------------------

def test_save_writes_crlf_in_the_right_folder():
    result = pml_dev.save("function", "mcpecho", FUNCTION)
    path = pml_dev.SANDBOX / "fnc" / "mcpecho.pmlfnc"
    assert result["path"] == str(path)
    assert b"\r\n" in path.read_bytes() and b"\n" not in path.read_bytes().replace(b"\r\n", b"")


def test_save_is_case_insensitive_on_the_name():
    pml_dev.save("function", "MCPEcho", FUNCTION)
    assert (pml_dev.SANDBOX / "fnc" / "mcpecho.pmlfnc").exists()


@pytest.mark.parametrize("name", ["echo", "tpoppdir", "constructpipe", "mcp echo", "mcp/../x", "..\\mcpx", "mcp.pmlfnc", ""])
def test_save_refuses_names_that_could_shadow_or_escape(name):
    with pytest.raises(ToolError, match="start with 'mcp'"):
        pml_dev.save("function", name, FUNCTION)


def test_save_refuses_a_file_that_defines_a_different_name():
    sneaky = "define function !!constructpipe(!x is STRING) is STRING\n  return !x\nendfunction\n"
    with pytest.raises(ToolError, match="exactly one function"):
        pml_dev.save("function", "mcpecho", sneaky)


def test_save_refuses_extra_definitions_in_the_same_file():
    two = FUNCTION + "define function !!tpwpos(!x is STRING) is STRING\n  return !x\nendfunction\n"
    with pytest.raises(ToolError, match="exactly one function"):
        pml_dev.save("function", "mcpecho", two)


def test_save_object_and_form_definitions():
    obj = "define object MCPBOX\n  member .v is REAL\nendobject\n"
    pml_dev.save("object", "mcpbox", obj)
    form = "setup form !!mcpdlg\n  title |x|\nexit\n"
    pml_dev.save("form", "mcpdlg", form)
    assert (pml_dev.SANDBOX / "obj" / "mcpbox.pmlobj").exists()
    assert (pml_dev.SANDBOX / "frm" / "mcpdlg.pmlfrm").exists()


def test_save_refuses_unknown_kind_and_huge_or_unencodable_files():
    with pytest.raises(ToolError, match="Unknown kind"):
        pml_dev.save("macro", "mcpx", FUNCTION)
    with pytest.raises(ToolError, match="too large"):
        pml_dev.save("function", "mcpecho", FUNCTION + "-- " + "x" * 200_000)
    with pytest.raises(ToolError, match="cp1252"):
        pml_dev.save("function", "mcpecho", FUNCTION + "-- 中文\n")


def test_delete_and_listing():
    assert pml_dev.listing()["files"] == []
    pml_dev.save("function", "mcpecho", FUNCTION)
    assert pml_dev.listing()["files"] == ["fnc\\mcpecho.pmlfnc"] or pml_dev.listing()["files"] == ["fnc/mcpecho.pmlfnc"]
    assert pml_dev.delete("function", "mcpecho")["deleted"] is True
    assert pml_dev.delete("function", "mcpecho")["deleted"] is False
    assert pml_dev.listing()["files"] == []


def test_delete_cannot_reach_files_outside_the_sandbox(tmp_path):
    victim = tmp_path / "victim.pmlfnc"
    victim.write_text("x")
    with pytest.raises(ToolError):
        pml_dev.delete("function", "..\\..\\victim")
    assert victim.exists()


# ---- log capture ----------------------------------------------------------------------------

def fake_nexus(monkeypatch, log_text=None, errors=(), values=None):
    """Replace nexus_client.call; simulate E3D writing the ALPHA LOG file named in the script."""
    captured = {}

    def call(method, path, **kwargs):
        body = kwargs["body"]
        captured.update(method=method, path=path, body=body)
        first = body["script"].splitlines()[0]
        if first.startswith("ALPHA LOG /") and log_text is not None:
            from pathlib import Path

            Path(first[len("ALPHA LOG /"):]).write_bytes(("﻿" + log_text).encode("utf-8"))
        return {"Ok": not errors, "Errors": list(errors), "Values": values or {}}

    monkeypatch.setattr(nexus_client, "call", call)
    return captured


def test_script_is_wrapped_with_alpha_log_and_the_log_is_returned(monkeypatch):
    captured = fake_nexus(monkeypatch, log_text="PML: Function not found\n", values={"x": 1})
    result = pml_dev.run_with_log("!!x = 1", ["x"])
    lines = captured["body"]["script"].splitlines()
    assert lines[0].startswith("ALPHA LOG /") and " " not in lines[0][len("ALPHA LOG /"):]
    assert lines[1:] == ["!!x = 1", "ALPHA LOG END"]
    assert captured["body"]["return"] == ["x"]
    assert result["Log"] == "PML: Function not found"
    assert result["Values"] == {"x": 1}


def test_log_file_is_deleted_after_reading(monkeypatch):
    fake_nexus(monkeypatch, log_text="hello")
    pml_dev.run_with_log("$P hello", None)
    assert list(pml_dev.LOG_DIR.glob("*.log")) == []


def test_wrapper_failures_do_not_count_as_script_errors(monkeypatch):
    fake_nexus(monkeypatch, log_text="", errors=["PML command failed: ALPHA LOG END"])
    result = pml_dev.run_with_log("!!x = 1", None)
    assert result["Ok"] is True and result["Errors"] == []


def test_real_script_errors_are_kept(monkeypatch):
    fake_nexus(monkeypatch, log_text="(47,62) CP: Incomplete command line", errors=["PML command failed: !b = 1 +"])
    result = pml_dev.run_with_log("!b = 1 +", None)
    assert result["Ok"] is False
    assert result["Errors"] == ["PML command failed: !b = 1 +"]
    assert "Incomplete command line" in result["Log"]


def test_remote_nexus_gets_no_log_wrapper(monkeypatch):
    monkeypatch.setattr(nexus_client, "NEXUS_URL", "http://10.0.0.5:5001")
    captured = fake_nexus(monkeypatch)
    result = pml_dev.run_with_log("!!x = 1", None)
    assert captured["body"]["script"] == "!!x = 1"
    assert "not captured" in result["Log"]


def test_long_logs_are_cut(monkeypatch):
    fake_nexus(monkeypatch, log_text="x" * (pml_dev.MAX_LOG_CHARS + 500))
    assert "log cut" in pml_dev.run_with_log("!!x = 1", None)["Log"]

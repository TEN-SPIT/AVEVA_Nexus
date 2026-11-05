"""e3d_launcher: project listing, safety gates and the credential dialog parsing, all without E3D."""

import json

import pytest
from mcp.server.mcpserver.exceptions import ToolError

import e3d_launcher as launcher

STORE = {}  # fake Windows Credential Manager, reset by the local_dir fixture


@pytest.fixture
def local_dir(tmp_path, monkeypatch):
    config = tmp_path / "config"
    config.mkdir()
    (config / "projects.json").write_text(
        json.dumps(
            {
                "DEMO": {
                    "project": "DEMO", "mdb": "/001", "module": "DESIGN", "port": 5001, "auth": "password",
                    "user": "SYSTEM", "password": "never-show-this", "init": "\\\\server\\share", "mon": "C:\\mon.exe",
                }
            }
        ),
        encoding="utf-8-sig",
    )
    monkeypatch.setattr(launcher, "LOCAL_DIR", tmp_path)
    monkeypatch.setattr(launcher, "WRITABLE_PROJECTS", set())
    store = STORE
    store.clear()
    monkeypatch.setattr(launcher.credstore, "read", lambda project, user: store.get((project, user)))
    monkeypatch.setattr(launcher.credstore, "write", lambda project, user, pw: store.__setitem__((project, user), pw))
    monkeypatch.setattr(launcher.credstore, "delete", lambda project, user: store.pop((project, user), None) is not None)
    return tmp_path


def test_list_projects_never_exposes_credentials(local_dir):
    listing = launcher.list_projects()
    assert listing == {"DEMO": {"project": "DEMO", "mdb": "/001", "module": "DESIGN", "port": 5001, "auth": "password",
                               "login": "dialog"}}
    assert "never-show-this" not in json.dumps(listing)


def test_unknown_project_is_rejected_before_anything_runs(local_dir, monkeypatch):
    monkeypatch.setattr(launcher, "_run_script", lambda *a, **k: pytest.fail("must not launch anything"))
    with pytest.raises(ToolError, match="Unknown project"):
        launcher.start("DEMO; calc.exe")
    with pytest.raises(ToolError, match="Unknown project"):
        launcher.stop("../../x")


def test_writable_is_refused_unless_whitelisted(local_dir, monkeypatch):
    monkeypatch.setattr(launcher, "_run_script", lambda *a, **k: pytest.fail("must not launch anything"))
    with pytest.raises(ToolError, match="NEXUS_WRITABLE_PROJECTS"):
        launcher.start("DEMO", writable=True)


def test_already_running_does_nothing(local_dir, monkeypatch):
    monkeypatch.setattr(launcher, "_health", lambda port: True)
    monkeypatch.setattr(launcher, "_run_script", lambda *a, **k: pytest.fail("must not launch anything"))
    monkeypatch.setattr(launcher, "_ask_credentials", lambda *a: pytest.fail("must not ask for a login"))
    assert launcher.start("DEMO")["status"] == "already_running"


def test_start_passes_login_by_environment_not_by_arguments(local_dir, monkeypatch):
    seen = {}
    health = iter([False, True])
    monkeypatch.setattr(launcher, "_health", lambda port: next(health))
    monkeypatch.setattr(launcher, "_ask_credentials", lambda project, user: ("SYSTEM", "s3cret", False))

    class Done:
        returncode, stdout, stderr = 0, "", ""

    def fake_run(script, *args, extra_env=None):
        seen.update(script=script, args=args, env=extra_env)
        return Done()

    monkeypatch.setattr(launcher, "_run_script", fake_run)
    monkeypatch.setattr(launcher.time, "sleep", lambda s: None)
    result = launcher.start("DEMO", writable=False)
    assert result["status"] == "started" and result["read_only"] is True
    assert "s3cret" not in " ".join(seen["args"])
    assert seen["env"] == {"NEXUS_E3D_USER": "SYSTEM", "NEXUS_E3D_PASSWORD": "s3cret"}


def test_windows_auth_never_asks_for_a_password(local_dir, monkeypatch):
    config = local_dir / "config" / "projects.json"
    data = json.loads(config.read_text(encoding="utf-8-sig"))
    data["DEMO"]["auth"] = "windows"
    config.write_text(json.dumps(data), encoding="utf-8-sig")
    seen = {}

    class Done:
        returncode, stdout, stderr = 0, "", ""

    monkeypatch.setattr(launcher, "_health", lambda port: bool(seen))
    monkeypatch.setattr(launcher, "_ask_credentials", lambda *a: pytest.fail("windows auth has no dialog"))
    monkeypatch.setattr(launcher, "_run_script", lambda s, *a, extra_env=None: seen.update(env=extra_env) or Done())
    launcher.start("DEMO")
    assert seen["env"]["NEXUS_E3D_AUTH"] == "windows"
    assert "NEXUS_E3D_PASSWORD" not in seen["env"]


class FakeDialog:
    def __init__(self, stdout, returncode=0):
        self.stdout, self.returncode = stdout, returncode


def dialog(monkeypatch, stdout, returncode=0):
    monkeypatch.setattr(launcher.subprocess, "run", lambda *a, **k: FakeDialog(stdout, returncode))


def test_dialog_returns_user_and_password(monkeypatch):
    dialog(monkeypatch, "SYSTEM\r\nabc123\r\n")
    assert launcher._ask_credentials("DEMO", "SYSTEM") == ("SYSTEM", "abc123", False)
    dialog(monkeypatch, "SYSTEM\r\nabc123\r\nremember\r\n")
    assert launcher._ask_credentials("DEMO", "SYSTEM") == ("SYSTEM", "abc123", True)


def test_dialog_strips_a_windows_domain_prefix(monkeypatch):
    dialog(monkeypatch, "PC01\\SYSTEM\r\nabc123\r\n")
    assert launcher._ask_credentials("DEMO", "SYSTEM")[0] == "SYSTEM"


@pytest.mark.parametrize("stdout,code", [("", 0), ("", 1), ("SYSTEM\r\n\r\n", 0)])
def test_dialog_cancel_or_empty_password_stops_the_start(monkeypatch, stdout, code):
    dialog(monkeypatch, stdout, code)
    with pytest.raises(ToolError, match="cancelled"):
        launcher._ask_credentials("DEMO", "SYSTEM")


@pytest.mark.parametrize("password", ["a&b", "a|b", "a<b", "a>b", "a^b", "a%b", 'a"b', "a b", "a'b"])
def test_dialog_refuses_characters_that_break_a_command_line(monkeypatch, password):
    dialog(monkeypatch, f"SYSTEM\r\n{password}\r\n")
    with pytest.raises(ToolError, match="cannot be passed safely"):
        launcher._ask_credentials("DEMO", "SYSTEM")


class Done:
    returncode, stdout, stderr = 0, "", ""


def _fake_launch(monkeypatch, seen, health):
    monkeypatch.setattr(launcher, "_health", lambda port: next(health))
    monkeypatch.setattr(launcher, "_run_script", lambda s, *a, extra_env=None: seen.update(env=extra_env) or Done())
    monkeypatch.setattr(launcher.time, "sleep", lambda s: None)


def test_stored_login_starts_without_any_dialog(local_dir, monkeypatch):
    STORE[("DEMO", "SYSTEM")] = "fromvault"
    seen = {}
    _fake_launch(monkeypatch, seen, iter([False, True]))
    monkeypatch.setattr(launcher, "_ask_credentials", lambda *a: pytest.fail("stored login needs no dialog"))
    assert launcher.list_projects()["DEMO"]["login"] == "stored"
    result = launcher.start("DEMO")
    assert result["status"] == "started" and result["login"] == "stored"
    assert seen["env"] == {"NEXUS_E3D_USER": "SYSTEM", "NEXUS_E3D_PASSWORD": "fromvault"}
    assert "fromvault" not in json.dumps(result)


def test_remember_stores_the_password_only_after_e3d_answers(local_dir, monkeypatch):
    seen = {}
    _fake_launch(monkeypatch, seen, iter([False, True]))
    monkeypatch.setattr(launcher, "_ask_credentials", lambda *a: ("SYSTEM", "typed", True))
    launcher.start("DEMO")
    assert STORE == {("DEMO", "SYSTEM"): "typed"}


def test_remember_is_not_stored_when_e3d_refuses_the_login(local_dir, monkeypatch):
    seen = {}
    _fake_launch(monkeypatch, seen, iter([False] * 10))
    monkeypatch.setattr(launcher, "_ask_credentials", lambda *a: ("SYSTEM", "wrong", True))
    run = local_dir / "run" / "DEMO"
    run.mkdir(parents=True)
    (run / "stdout.log").write_text("AVEVA monitor\n(41,12) Invalid password for user SYSTEM\n")
    with pytest.raises(ToolError, match="Invalid password"):
        launcher.start("DEMO")
    assert STORE == {}


def test_forget_login(local_dir):
    STORE[("DEMO", "SYSTEM")] = "x"
    assert launcher.forget_login("DEMO") == {"status": "forgotten", "login": "dialog"}
    assert launcher.forget_login("DEMO")["status"] == "nothing_stored"

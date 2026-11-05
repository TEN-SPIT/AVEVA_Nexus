"""POST /api/PML/Run. Needs a session started with NEXUS_READONLY=0 (pytest -m writable)."""
import pytest

pytestmark = pytest.mark.writable


def run(api, script, ret=None):
    return api("POST", "/api/PML/Run", {"script": script, "return": ret or []})


def test_run_returns_requested_variables_with_types(api):
    status, body = run(api, "!!nxT = |abc|\n!!nxN = 42\n!!nxB = true", ["nxT", "nxN", "nxB"])
    assert status == 200 and body["Success"] is True
    data = body["Data"]
    assert data["Ok"] is True and data["Errors"] == []
    assert data["Values"] == {"nxT": "abc", "nxN": 42, "nxB": True}


def test_run_collects_array(api, site_with_zones):
    status, body = run(api, f"var !!nxArr collect all ZONE for {site_with_zones['dbref']}", ["nxArr"])
    assert status == 200
    values = body["Data"]["Values"]["nxArr"]
    assert isinstance(values, list) and len(values) >= 1
    assert all(v.startswith("=") for v in values)


def test_run_reports_errors_and_continues(api):
    status, body = run(api, "var !!nxE current MDB\n!!nxOk = |y|", ["nxOk", "nxE"])
    assert status == 200  # errors are data for the caller
    data = body["Data"]
    assert data["Ok"] is False
    assert len(data["Errors"]) == 1 and "var !!nxE current MDB" in data["Errors"][0]
    assert data["Values"]["nxOk"] == "y"
    assert data["Values"]["nxE"] is None


def test_run_undefined_return_is_null(api):
    status, body = run(api, "!!nxD = |1|", ["nxNeverDefined"])
    assert status == 200
    assert body["Data"]["Values"]["nxNeverDefined"] is None


def test_run_if_else_block(api):
    script = "!!nxIfA = 5\nif (!!nxIfA gt 3) then\n  !!nxIfB = |big|\nelse\n  !!nxIfB = |small|\nendif"
    status, body = run(api, script, ["nxIfB"])
    assert status == 200
    assert body["Data"]["Ok"] is True and body["Data"]["Errors"] == []
    assert body["Data"]["Values"]["nxIfB"] == "big"


def test_run_do_loop_and_nested_block(api):
    script = "!!nxLoopS = 0\ndo !i from 1 to 4\n  if (!i ne 2) then\n    !!nxLoopS = !!nxLoopS + !i\n  endif\nenddo"
    status, body = run(api, script, ["nxLoopS"])
    assert status == 200 and body["Data"]["Ok"] is True
    assert body["Data"]["Values"]["nxLoopS"] == 8  # 1 + 3 + 4


def test_run_block_error_is_reported_with_its_lines_and_later_lines_still_run(api):
    script = "!!nxBlkP = |1|\nif (!!nxBlkP eq |1|) then\n  var !!nxIfBad current MDB\n  !!nxBlkInside = |x|\nendif\n!!nxIfAfter = |ok|"
    status, body = run(api, script, ["nxIfAfter", "nxBlkInside"])
    assert status == 200
    data = body["Data"]
    assert data["Ok"] is False
    assert len(data["Errors"]) == 1
    assert "PML block failed" in data["Errors"][0] and "lines 2-5" in data["Errors"][0]
    assert data["Values"]["nxIfAfter"] == "ok"      # the line after the block still ran
    assert data["Values"]["nxBlkInside"] is None     # the block stopped at its failing line


def test_run_unterminated_block_and_stray_end_are_400(api):
    status, body = run(api, "!!nxIfA = 1\nif (!!nxIfA eq 1) then\n!!nxIfB = 2")
    assert status == 400 and "line 2" in body["Error"]
    status, body = run(api, "!!nxIfA = 1\nendif")
    assert status == 400 and "line 2" in body["Error"]
    assert api("GET", "/api/Health")[0] == 200


def test_run_leaves_no_temporary_macro_behind(api):
    import glob
    import os
    import tempfile

    before = set(glob.glob(os.path.join(tempfile.gettempdir(), "nexus-*.pmlmac")))
    run(api, "if (1 eq 1) then\n  !!nxTmp = |x|\nendif")
    run(api, "if (1 eq 1) then\n  var !!nxIfBad current MDB\nendif")
    after = set(glob.glob(os.path.join(tempfile.gettempdir(), "nexus-*.pmlmac")))
    assert after == before


def test_parallel_runs_do_not_interleave_variables(api):
    import concurrent.futures

    def work(i):
        status, body = run(api, f"!!nxR{i} = |v{i}|\n!!nxQ{i} = {i}", [f"nxR{i}", f"nxQ{i}"])
        return i, status, body["Data"]["Values"]

    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as pool:
        results = list(pool.map(work, range(20)))
    for i, status, values in results:
        assert status == 200
        assert values == {f"nxR{i}": f"v{i}", f"nxQ{i}": i}
    assert api("GET", "/api/Health")[0] == 200


def test_run_input_validation(api):
    assert api("POST", "/api/PML/Run", {})[0] == 400
    assert run(api, "   ")[0] == 400
    assert run(api, "!!nxX = |1|", ["x ) ; y"])[0] == 400
    assert api("GET", "/api/PML/Run")[0] == 405
    assert api("GET", "/api/Health")[0] == 200

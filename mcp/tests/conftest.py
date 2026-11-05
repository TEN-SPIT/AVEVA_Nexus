"""Keep the audit trail of the tests out of the user's real audit log."""

import pytest


@pytest.fixture(autouse=True)
def _audit_log_in_tmp(tmp_path, monkeypatch):
    monkeypatch.setenv("NEXUS_AUDIT_LOG", str(tmp_path / "audit.jsonl"))

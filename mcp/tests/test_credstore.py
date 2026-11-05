"""credstore: real round trip through the Windows Credential Manager with a throwaway target."""

import sys

import pytest

import credstore

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows Credential Manager only")


def test_round_trip_and_delete():
    project, user = "ZZPYTEST", "nobody"
    try:
        assert credstore.read(project, user) is None
        credstore.write(project, user, "pä$$ wörd")
        assert credstore.read(project, user) == "pä$$ wörd"
        assert credstore.read(project.lower(), user.upper()) == "pä$$ wörd"  # case-insensitive target
    finally:
        credstore.delete(project, user)
    assert credstore.read(project, user) is None
    assert credstore.delete(project, user) is False

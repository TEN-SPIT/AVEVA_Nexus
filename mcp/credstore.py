"""E3D login stored in the Windows Credential Manager (generic credentials, DPAPI-protected and
bound to the current Windows user). No extra dependency: plain ctypes over advapi32.

Target name: "NexusMCP/<project-code>/<user>", e.g. "NexusMCP/DEMO/SYSTEM". One credential per E3D
project code and user, so DEMO and DEMOW (same project, other MDB) share it.

CLI (the password is read from stdin, never from the command line):
    uv run python credstore.py set DEMO SYSTEM      < password on stdin
    uv run python credstore.py has DEMO SYSTEM      -> prints yes/no
    uv run python credstore.py delete DEMO SYSTEM
"""

import ctypes
import sys
from ctypes import wintypes

_CRED_TYPE_GENERIC = 1
_CRED_PERSIST_LOCAL_MACHINE = 2  # survives logoff, stays on this machine (not roamed)
_ERROR_NOT_FOUND = 1168


class _CREDENTIAL(ctypes.Structure):
    _fields_ = [
        ("Flags", wintypes.DWORD),
        ("Type", wintypes.DWORD),
        ("TargetName", wintypes.LPWSTR),
        ("Comment", wintypes.LPWSTR),
        ("LastWritten", wintypes.FILETIME),
        ("CredentialBlobSize", wintypes.DWORD),
        ("CredentialBlob", ctypes.POINTER(ctypes.c_char)),
        ("Persist", wintypes.DWORD),
        ("AttributeCount", wintypes.DWORD),
        ("Attributes", ctypes.c_void_p),
        ("TargetAlias", wintypes.LPWSTR),
        ("UserName", wintypes.LPWSTR),
    ]


_advapi = ctypes.WinDLL("advapi32", use_last_error=True) if sys.platform == "win32" else None


def target(project: str, user: str) -> str:
    return f"NexusMCP/{project.upper()}/{user.upper()}"


def read(project: str, user: str) -> str | None:
    """Stored password, or None if there is none."""
    if _advapi is None:
        return None
    pcred = ctypes.POINTER(_CREDENTIAL)()
    if not _advapi.CredReadW(target(project, user), _CRED_TYPE_GENERIC, 0, ctypes.byref(pcred)):
        if ctypes.get_last_error() == _ERROR_NOT_FOUND:
            return None
        raise OSError(ctypes.get_last_error(), "CredReadW failed")
    try:
        cred = pcred.contents
        return ctypes.string_at(cred.CredentialBlob, cred.CredentialBlobSize).decode("utf-16-le")
    finally:
        _advapi.CredFree(pcred)


def write(project: str, user: str, password: str) -> None:
    if _advapi is None:
        raise OSError("Windows Credential Manager is only available on Windows")
    blob = password.encode("utf-16-le")
    buf = ctypes.create_string_buffer(blob, len(blob))
    cred = _CREDENTIAL(
        Type=_CRED_TYPE_GENERIC,
        TargetName=target(project, user),
        Comment="E3D login used by the Nexus MCP server",
        CredentialBlobSize=len(blob),
        CredentialBlob=ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)),
        Persist=_CRED_PERSIST_LOCAL_MACHINE,
        UserName=user.upper(),
    )
    if not _advapi.CredWriteW(ctypes.byref(cred), 0):
        raise OSError(ctypes.get_last_error(), "CredWriteW failed")


def delete(project: str, user: str) -> bool:
    if _advapi is None:
        return False
    if _advapi.CredDeleteW(target(project, user), _CRED_TYPE_GENERIC, 0):
        return True
    if ctypes.get_last_error() == _ERROR_NOT_FOUND:
        return False
    raise OSError(ctypes.get_last_error(), "CredDeleteW failed")


def _main(argv: list[str]) -> int:
    if len(argv) != 3 or argv[0] not in ("set", "has", "delete"):
        sys.stderr.write(__doc__)
        return 2
    action, project, user = argv
    if action == "set":
        password = sys.stdin.readline().rstrip("\r\n")
        if not password:
            sys.stderr.write("No password on stdin; nothing stored.\n")
            return 1
        write(project, user, password)
        print(f"stored {target(project, user)}")
    elif action == "has":
        print("yes" if read(project, user) else "no")
    else:
        print("deleted" if delete(project, user) else "not found")
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))

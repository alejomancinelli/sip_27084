"""
Checks whether a module can be imported, without ever writing to stderr.

Windows PowerShell 5.1 turns every stderr line of a native executable into an
ErrorRecord (NativeCommandError); under $ErrorActionPreference = "Stop" that
aborts the calling script. So the traceback is caught here and everything goes to
stdout.

    python check_import.py stapipy
        -> prints the version (or "ok") and exits 0
        -> prints the reason for the failure and exits 1
        -> prints the usage and exits 2 when no module is given
"""

import importlib
import sys


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: check_import.py <module>")
        return 2

    _name = sys.argv[1]
    try:
        _module = importlib.import_module(_name)
    except BaseException as e:
        # BaseException on purpose: compiled extensions that cannot find their DLLs
        # may raise SystemExit or others that do not derive from Exception.
        print(f"{type(e).__name__}: {e}")
        return 1

    print(getattr(_module, "__version__", "ok"))
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Convenience launcher so the app can be started with `python run.py` (or the
IDE's Run button). Equivalent to `python -m cappa`.

WHICHEVER python starts this file, the app runs on the project venv: an IDE
Run button or a PATH `python` is often a bare interpreter (no PySide6 — user
hit exactly that, 2026-07-16), so when the imports are missing and .venv
exists, this re-launches itself with the venv's python instead of dying with
a traceback. No venv "activation" needed, ever."""

import os
import subprocess
import sys


def _venv_python():
    return os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        ".venv", "Scripts", "python.exe")


if __name__ == "__main__":
    try:
        import PySide6  # noqa: F401 -- probe: is this the right interpreter?
    except ImportError:
        venv = _venv_python()
        if (os.path.exists(venv) and os.path.normcase(os.path.abspath(
                sys.executable)) != os.path.normcase(venv)):
            sys.exit(subprocess.call(
                [venv, os.path.abspath(__file__)] + sys.argv[1:]))
        sys.exit("Cappa needs its dependencies: run\n"
                 "    pip install -r requirements.txt\n"
                 "(or create the .venv this launcher looks for).")
    from cappa.app import main
    sys.exit(main())

"""Convenience launcher so the app can be started with `python run.py` (or the
IDE's Run button). Equivalent to `python -m cappa`."""

import sys

from cappa.app import main

if __name__ == "__main__":
    sys.exit(main())

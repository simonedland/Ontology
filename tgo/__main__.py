"""``python -m tgo``: entry point (see :mod:`tgo.cli`)."""
import sys

from .cli import main

if __name__ == "__main__":   # required: worker processes (Windows/macOS spawn) re-import __main__
    sys.exit(main())

"""``python -m arbor ...`` runs the command line interface (see arbor/cli.py)."""
import sys

from arbor.cli import main

if __name__ == "__main__":
    sys.exit(main())

"""PyInstaller entry point of arborfdm-cli.exe (console)."""
import sys

from arbor.cli import main

if __name__ == "__main__":
    sys.exit(main())

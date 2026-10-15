"""PyInstaller entry point of ArborFDM.exe (windowed, no console)."""
import sys

from arbor.app.main import main

if __name__ == "__main__":
    sys.exit(main())

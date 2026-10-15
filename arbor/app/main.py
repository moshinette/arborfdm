"""ArborFDM desktop application entry point."""
from __future__ import annotations

import argparse
import os
import sys


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="arborfdm", description="Tree supports for FDM miniatures")
    parser.add_argument("file", nargs="?", help="model (STL/OBJ/3MF) or project (.arbor) to open")
    args = parser.parse_args(argv if argv is not None else sys.argv[1:])

    from PySide6 import QtCore, QtGui, QtWidgets

    # depth buffer + multisampling for the 3D view
    fmt = QtGui.QSurfaceFormat()
    fmt.setDepthBufferSize(24)
    fmt.setSamples(4)
    QtGui.QSurfaceFormat.setDefaultFormat(fmt)
    QtCore.QCoreApplication.setAttribute(QtCore.Qt.AA_ShareOpenGLContexts)

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv[:1])
    app.setApplicationName("ArborFDM")
    app.setOrganizationName("ArborFDM")

    from .main_window import MainWindow
    from .theme import apply_theme

    apply_theme(app)
    win = MainWindow()
    win.show()
    if args.file:
        QtCore.QTimer.singleShot(0, lambda: win.open_path(os.path.abspath(args.file)))
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())

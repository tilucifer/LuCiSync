from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from .ui import MainWindow, create_application_icon


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("LuCiSync")
    app.setOrganizationName("LuCiSync")
    app_icon = create_application_icon()
    app.setWindowIcon(app_icon)
    window = MainWindow()
    window.setWindowIcon(app_icon)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())

"""Application entry point for Threads Studio Pro."""
from __future__ import annotations

import logging
import os
import sys

# Force consistent PyQt6 binding before importing fluent_blue
os.environ.setdefault("FLUENT_BLUE_QT_BINDING", "PyQt6")

from fluent_blue import ThemeManager
from fluent_blue.compatibility.qt import QApplication

from .assets_gen import ensure_sample_assets
from .core.config import AppSettings
from .ui.main_window import ThreadsStudioWindow


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    ensure_sample_assets()
    settings = AppSettings.load()

    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName("Threads Studio Pro")
    ThemeManager.set_theme(settings.theme if settings.theme in ("dark", "light", "high-contrast") else "dark")

    win = ThreadsStudioWindow(settings)
    win.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())


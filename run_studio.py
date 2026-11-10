"""Quick launcher for Threads Studio Pro."""
from __future__ import annotations

import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

os.environ.setdefault("FLUENT_BLUE_QT_BINDING", "PyQt6")

from threadstudio.app import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())

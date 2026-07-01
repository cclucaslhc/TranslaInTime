from __future__ import annotations

import traceback
from pathlib import Path


def run() -> None:
    log_dir = Path(__file__).resolve().parent / "logs"
    log_dir.mkdir(exist_ok=True)
    try:
        from desktop_qt_app import main

        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception:
        (log_dir / "desktop_qt_app.log").write_text(traceback.format_exc(), encoding="utf-8")
        raise


run()

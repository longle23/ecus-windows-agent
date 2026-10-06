"""
Dump ECUS UI control tree without Inspect.exe.

Usage (on the Windows ECUS PC):
  1. Login ECUS and leave the main ECUS5-VNACCS window visible (or open export form).
  2. From ecus-windows-agent folder:
       dump_ecus_ui.cmd
     or:
       .\\venv\\Scripts\\python.exe scripts\\dump_ecus_ui.py
  3. Open logs/ecus-ui-dump.txt — first line must be WINDOW: ECUS5-VNACCS...
     If you see Taskbar, ECUS was not found.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import get_settings  # noqa: E402
from app.rpa.ui_helpers import (  # noqa: E402
    EcusUiError,
    _is_ignored_window_title,
    _window_title,
    connect_ecus,
    dump_controls,
    find_export_form,
    list_ecus_process_candidates,
)


def main() -> int:
    settings = get_settings()
    candidates = list_ecus_process_candidates(settings.ecus_process_name)
    print(f"ECUS process candidates: {candidates or 'none'}")
    print(f"ECUS_EXE_PATH={settings.ecus_exe_path}")
    print(f"ECUS_PROCESS_NAME={settings.ecus_process_name}")

    try:
        app = connect_ecus(settings)
    except EcusUiError as exc:
        print(f"ERROR: {exc}")
        return 2

    try:
        target = find_export_form(app, timeout=3)
        print(f"Dumping export form: {_window_title(target)!r}")
    except Exception:
        target = None
        for win in app.windows():
            title = _window_title(win)
            if _is_ignored_window_title(title):
                continue
            if "ecus" in title.lower() or "tờ khai" in title.lower():
                target = win
                break
        if target is None:
            target = app.top_window()
        title = _window_title(target)
        print(f"Export form not found; dumping: {title!r}")
        if _is_ignored_window_title(title):
            print(
                "ERROR: Dump target is Taskbar/Shell — ECUS window was not attached. "
                "Open ECUS5-VNACCS, login, keep window open, then rerun."
            )
            return 3

    out = settings.log_dir / "ecus-ui-dump.txt"
    dump_controls(target, out)
    print(f"Wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

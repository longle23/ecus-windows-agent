from __future__ import annotations

import logging
import re
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def ensure_default_desktop() -> None:
    """Ensure the calling thread is attached to the interactive user desktop."""
    try:
        import ctypes
        user32 = ctypes.windll.user32
        hdesk = user32.OpenInputDesktop(0, False, 0x01FF) or user32.OpenDesktopW("Default", 0, False, 0x01FF)
        if hdesk:
            user32.SetThreadDesktop(hdesk)
    except Exception:
        pass


ensure_default_desktop()

LOGIN_TITLE_RE = re.compile(r"đăng\s*nhập|login|sign\s*in", re.IGNORECASE)
EXPORT_FORM_TITLE_RE = re.compile(r"tờ\s*khai\s*xuất\s*khẩu|export\s*declaration", re.IGNORECASE)
MAIN_TITLE_RE = re.compile(r"ECUS5?\s*-?\s*VNACCS|ECUS5VNACCS|Electronic\s*Customs", re.IGNORECASE)
IGNORE_TITLE_RE = re.compile(
    r"^(taskbar|program manager|start|search|cortana|windows shell experience)$",
    re.IGNORECASE,
)

MENU_CUSTOMS = "Tờ khai hải quan"
MENU_EXPORT_EDA = "Đăng ký mới tờ khai xuất khẩu (EDA)"

LICENSE_TITLE_RE = re.compile(r"bản\s*quyền|đăng\s*ký\s*bản\s*quyền", re.IGNORECASE)


class EcusUiError(RuntimeError):
    """Raised when ECUS UI cannot be driven as expected."""


def as_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def _ctrl_name(ctrl) -> str:
    try:
        return as_text(ctrl.window_text())
    except Exception:
        pass
    try:
        info = getattr(ctrl, "element_info", None)
        if info is not None:
            return as_text(getattr(info, "name", "") or "")
    except Exception:
        pass
    return ""


def _ctrl_auto_id(ctrl) -> str:
    try:
        info = getattr(ctrl, "element_info", None)
        if info is not None:
            return as_text(getattr(info, "automation_id", "") or "")
    except Exception:
        pass
    return ""


def _ctrl_type(ctrl) -> str:
    try:
        info = getattr(ctrl, "element_info", None)
        if info is not None:
            return as_text(getattr(info, "control_type", "") or "")
    except Exception:
        pass
    return ""


def _matches(
    ctrl,
    *,
    title: str | None = None,
    title_re: str | None = None,
    control_type: str | None = None,
    auto_id: str | None = None,
) -> bool:
    if control_type and _ctrl_type(ctrl).lower() != control_type.lower():
        return False
    if auto_id and _ctrl_auto_id(ctrl) != auto_id:
        return False
    name = _ctrl_name(ctrl)
    if title is not None and name != title:
        return False
    if title_re is not None and not re.search(title_re, name or "", re.IGNORECASE):
        return False
    return True


def find_control(
    parent,
    *,
    title: str | None = None,
    title_re: str | None = None,
    control_type: str | None = None,
    auto_id: str | None = None,
    timeout: float = 0.0,
    index: "DialogIndex | None" = None,
):
    """Find a child control. Prefer DialogIndex (1 tree scan) over repeated descendants()."""
    if index is not None:
        ctrl = index.find(
            title=title,
            title_re=title_re,
            control_type=control_type,
            auto_id=auto_id,
        )
        if ctrl is not None:
            return ctrl
        if timeout <= 0:
            raise LookupError(
                f"control not found type={control_type!r} title={title!r} "
                f"title_re={title_re!r} auto_id={auto_id!r}"
            )

    deadline = time.time() + max(timeout, 0.0)
    kwargs = {
        "title": title,
        "title_re": title_re,
        "control_type": control_type,
        "auto_id": auto_id,
    }
    while True:
        child_window = getattr(parent, "child_window", None)
        if callable(child_window):
            try:
                call_kwargs = {k: v for k, v in kwargs.items() if v is not None}
                spec = child_window(**call_kwargs)
                if timeout > 0 and hasattr(spec, "wait"):
                    spec.wait("exists", timeout=min(timeout, 1.0))
                elif hasattr(spec, "exists") and not spec.exists(timeout=0.05):
                    raise LookupError("not found")
                return spec.wrapper_object() if hasattr(spec, "wrapper_object") else spec
            except Exception:
                pass

        try:
            if control_type:
                descendants = parent.descendants(control_type=control_type)
            else:
                descendants = parent.descendants()
        except Exception:
            descendants = []
        for ctrl in descendants:
            try:
                if _matches(ctrl, **kwargs):
                    return ctrl
            except Exception:
                continue

        if time.time() >= deadline:
            break
        time.sleep(0.15)
    raise LookupError(
        f"control not found type={control_type!r} title={title!r} "
        f"title_re={title_re!r} auto_id={auto_id!r}"
    )


class DialogIndex:
    """One-pass cache of visible controls for a dialog (avoids N× descendants scans)."""

    def __init__(self, dlg) -> None:
        self.dlg = dlg
        self.texts: list[tuple[str, Any]] = []
        self.edits: list[Any] = []
        self.combos: list[Any] = []
        self.buttons: list[tuple[str, Any]] = []
        self.tabs: list[tuple[str, Any]] = []
        self.by_auto_id: dict[str, Any] = {}
        self._build()

    def _build(self) -> None:
        t0 = time.time()
        try:
            # Single descendants() call — the expensive part, do it once per tab.
            nodes = list(self.dlg.descendants())
        except Exception as exc:  # noqa: BLE001
            logger.warning("DialogIndex build failed: %s", exc)
            nodes = []
        for ctrl in nodes:
            try:
                ctype = _ctrl_type(ctrl)
                name = _ctrl_name(ctrl)
                auto_id = _ctrl_auto_id(ctrl)
                cl = ctype.lower()
                if auto_id:
                    target_ctrl = ctrl
                    if cl in ("pane", "group"):
                        for sub in ctrl.children():
                            if _ctrl_type(sub).lower() == "edit":
                                target_ctrl = sub
                                break
                    self.by_auto_id.setdefault(auto_id, target_ctrl)
                if cl == "text" and name:
                    self.texts.append((name, ctrl))
                elif cl == "edit":
                    self.edits.append(ctrl)
                elif cl == "combobox":
                    self.combos.append(ctrl)
                elif cl == "button" and name:
                    self.buttons.append((name, ctrl))
                elif cl == "tabitem" and name:
                    self.tabs.append((name, ctrl))
            except Exception:
                continue
        logger.info(
            "DialogIndex built in %.1fs texts=%s edits=%s combos=%s buttons=%s",
            time.time() - t0,
            len(self.texts),
            len(self.edits),
            len(self.combos),
            len(self.buttons),
        )

    def find(
        self,
        *,
        title: str | None = None,
        title_re: str | None = None,
        control_type: str | None = None,
        auto_id: str | None = None,
    ):
        if auto_id and auto_id in self.by_auto_id:
            ctrl = self.by_auto_id[auto_id]
            if not control_type or _ctrl_type(ctrl).lower() == control_type.lower():
                return ctrl
        ctype = (control_type or "").lower()
        pools: list[Any]
        if ctype == "button":
            pools = [c for _, c in self.buttons]
            named = self.buttons
        elif ctype == "tabitem":
            pools = [c for _, c in self.tabs]
            named = self.tabs
        elif ctype == "edit":
            pools = self.edits
            named = [(_ctrl_name(c), c) for c in self.edits]
        elif ctype == "combobox":
            pools = self.combos
            named = [(_ctrl_name(c), c) for c in self.combos]
        elif ctype == "text":
            pools = [c for _, c in self.texts]
            named = self.texts
        else:
            named = (
                self.texts
                + self.buttons
                + self.tabs
                + [(_ctrl_name(c), c) for c in self.edits]
                + [(_ctrl_name(c), c) for c in self.combos]
            )
            pools = [c for _, c in named]

        if title is not None:
            for name, ctrl in named:
                if name == title:
                    return ctrl
        if title_re is not None:
            pat = re.compile(title_re, re.IGNORECASE)
            for name, ctrl in named:
                if pat.search(name or ""):
                    return ctrl
        if title is None and title_re is None and auto_id is None:
            return pools[0] if pools else None
        return None

    def set_by_auto_id(self, auto_id: str, value: str) -> bool:
        text = as_text(value)
        if not text:
            return False
        ctrl = self.find(auto_id=auto_id)
        if ctrl is not None:
            return _write_control(ctrl, text)
        return False

    def set_by_labels(self, labels: list[str], value: str) -> bool:
        text = as_text(value)
        if not text:
            return False
        for label in labels:
            # 1) control whose own name matches label
            for ctype in ("Edit", "ComboBox"):
                ctrl = self.find(title_re=f".*{re.escape(label)}.*", control_type=ctype)
                if ctrl is not None and _write_control(ctrl, text):
                    return True
            # 2) label Text → nearest Edit/Combo to the right/below
            label_ctrl = self.find(title_re=f".*{re.escape(label)}.*", control_type="Text")
            if label_ctrl is None:
                continue
            try:
                lx, ly, _, _ = _rect(label_ctrl)
            except Exception:
                continue
            best = None
            best_score = 1e18
            for ctrl in self.edits + self.combos:
                try:
                    if hasattr(ctrl, "is_enabled") and not ctrl.is_enabled():
                        continue
                    if hasattr(ctrl, "is_visible") and not ctrl.is_visible():
                        continue
                    cx, cy, _, _ = _rect(ctrl)
                    if cx < lx - 5 and cy < ly - 8:
                        continue
                    score = abs(cy - ly) * 20 + abs(cx - lx)
                    if cx >= lx:
                        score -= 50
                    if score < best_score:
                        best_score = score
                        best = ctrl
                except Exception:
                    continue
            if best is not None and _write_control(best, text):
                return True
        return False

    def set_below_section(self, section_title: str, field_label: str, value: str) -> bool:
        text = as_text(value)
        if not text:
            return False
        section = self.find(title_re=f".*{re.escape(section_title)}.*", control_type="Text")
        if section is None:
            return self.set_by_labels([field_label], text)
        try:
            sx, sy, _, _ = _rect(section)
        except Exception:
            return self.set_by_labels([field_label], text)

        label_candidates = []
        for name, ctrl in self.texts:
            if field_label.lower() not in name.lower():
                continue
            try:
                lx, ly, _, _ = _rect(ctrl)
                if ly < sy - 5:
                    continue
                label_candidates.append((abs(ly - sy) + abs(lx - sx) * 0.05, lx, ly, ctrl))
            except Exception:
                continue
        label_candidates.sort(key=lambda t: t[0])
        if not label_candidates:
            return self.set_by_labels([field_label], text)
        _, lx, ly, _label = label_candidates[0]
        best = None
        best_score = 1e18
        for ctrl in self.edits + self.combos:
            try:
                if hasattr(ctrl, "is_enabled") and not ctrl.is_enabled():
                    continue
                cx, cy, _, _ = _rect(ctrl)
                if cy < ly - 25:
                    continue
                score = abs(cy - ly) * 25 + abs(cx - lx)
                if cx >= lx - 10:
                    score -= 40
                if score < best_score:
                    best_score = score
                    best = ctrl
            except Exception:
                continue
        if best is None:
            return False
        return _write_control(best, text)

    def click_button(self, title: str) -> bool:
        ctrl = self.find(title_re=f".*{re.escape(title)}.*", control_type="Button")
        if ctrl is None:
            return False
        try:
            # Skip disabled buttons (e.g. Ghi greyed out).
            if hasattr(ctrl, "is_enabled") and not ctrl.is_enabled():
                logger.warning("button '%s' found but disabled", title)
                return False
            ctrl.click_input()
            return True
        except Exception as exc:  # noqa: BLE001
            logger.debug("click button '%s' failed: %s", title, exc)
            return False

    def select_tab(self, title: str) -> bool:
        patterns = [title, title.replace("Thông tin ", "")]
        for pattern in patterns:
            ctrl = self.find(title_re=f".*{re.escape(pattern)}.*", control_type="TabItem")
            if ctrl is None:
                continue
            try:
                try:
                    ctrl.select()
                except Exception:
                    ctrl.click_input()
                time.sleep(0.2)
                logger.info("selected tab: %s", pattern)
                return True
            except Exception:
                continue
        return False


def parse_code_label(value: str | None) -> str:
    """`B11 - Xuất kinh doanh` → `B11`; `47NF - Hải quan...` → `47NF`."""
    text = str(value or "").strip()
    if not text:
        return ""
    if " - " in text:
        return text.split(" - ", 1)[0].strip()
    if "-" in text and text.split("-", 1)[0].strip().isalnum():
        left = text.split("-", 1)[0].strip()
        if 1 <= len(left) <= 8:
            return left
    return text


def split_qty_unit(value: str | None) -> tuple[str, str]:
    """`22 RO` / `196.58 KGM` → (qty, unit)."""
    text = str(value or "").strip()
    if not text:
        return "", ""
    parts = text.split()
    if len(parts) == 1:
        return parts[0], ""
    return parts[0], " ".join(parts[1:])


def _is_ignored_window_title(title: str | None) -> bool:
    text = as_text(title)
    if not text:
        return True
    if IGNORE_TITLE_RE.match(text):
        return True
    return text.lower() in {"taskbar", "program manager"}


def list_ecus_process_candidates(process_name: str) -> list[tuple[int, str]]:
    try:
        import psutil  # type: ignore
    except ImportError:
        return []

    needle = process_name.lower().removesuffix(".exe")
    # Ignore python/agent paths that merely contain "ecus" in the folder name
    # (e.g. C:\...\ecus-windows-agent\.venv\Scripts\python.exe).
    skip_name = {"python", "pythonw", "uvicorn", "powershell", "pwsh", "cmd", "conhost"}
    found: list[tuple[int, str]] = []
    for proc in psutil.process_iter(["pid", "name", "exe"]):
        name = proc.info.get("name") or ""
        exe_path = proc.info.get("exe") or ""
        base = name.lower().removesuffix(".exe")
        if base in skip_name:
            continue
        if (
            base == needle
            or "ecus" in base
            or "vnaccs" in base
        ):
            found.append((int(proc.info["pid"]), name or exe_path or str(proc.info["pid"])))
    return found


def enum_top_level_hwnd_titles(*, include_minimized: bool = True) -> list[tuple[int, str]]:
    """Fast top-level window titles via Win32 API (no pywinauto, no AccessDenied spam)."""
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    results: list[tuple[int, str]] = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def _callback(hwnd, _lparam):  # noqa: ANN001
        try:
            visible = bool(user32.IsWindowVisible(hwnd))
            minimized = bool(user32.IsIconic(hwnd))
            if not visible and not (include_minimized and minimized):
                return True
            length = int(user32.GetWindowTextLengthW(hwnd))
            if length <= 0:
                return True
            buf = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buf, length + 1)
            title = buf.value or ""
            if title:
                results.append((int(hwnd), title))
        except Exception:
            pass
        return True

    user32.EnumWindows(_callback, 0)
    return results


def restore_window(hwnd: int) -> None:
    import ctypes

    user32 = ctypes.windll.user32
    SW_RESTORE = 9
    try:
        if user32.IsIconic(hwnd):
            user32.ShowWindow(hwnd, SW_RESTORE)
        user32.SetForegroundWindow(hwnd)
    except Exception:
        pass


def uia_window_from_hwnd(hwnd: int):
    from pywinauto import Application  # type: ignore

    restore_window(hwnd)
    app = Application(backend="uia").connect(handle=hwnd)
    try:
        return app.window(handle=hwnd).wrapper_object(), app
    except Exception:
        return app.top_window(), app


def find_top_window_by_title(title_re: re.Pattern[str], timeout: float = 0.0):
    """Return (uia_wrapper, title, app) for first matching top-level window."""
    deadline = time.time() + (timeout if timeout > 0 else 0)
    while True:
        for hwnd, title in enum_top_level_hwnd_titles():
            if _is_ignored_window_title(title):
                continue
            if title_re.search(title or ""):
                try:
                    win, app = uia_window_from_hwnd(hwnd)
                    return win, title, app
                except Exception as exc:  # noqa: BLE001
                    logger.debug("uia connect hwnd=%s failed: %s", hwnd, exc)
                    continue
        if time.time() >= deadline:
            break
        time.sleep(0.2)
    return None, None, None


def connect_ecus(settings) -> Any:
    """Attach to running ECUS or start EXE. Avoid slow UIA/win32 tree scans."""
    from pywinauto import Application  # type: ignore

    exe = settings.ecus_exe_path
    process_name = settings.ecus_process_name
    app = None
    attached_how = ""
    t0 = time.time()

    candidates = list_ecus_process_candidates(process_name)
    if candidates:
        logger.debug("ECUS process candidates: %s", candidates)

    win, title, app = find_top_window_by_title(MAIN_TITLE_RE)
    if app is None:
        win, title, app = find_top_window_by_title(EXPORT_FORM_TITLE_RE)
    if app is not None:
        attached_how = "main window"

    if app is None and candidates:
        for pid, name in sorted(
            candidates,
            key=lambda item: 0
            if item[1].lower().removesuffix(".exe") == process_name.lower().removesuffix(".exe")
            else 1,
        ):
            try:
                # Attach UIA by process — then resolve top window by title enum (no win32 children).
                Application(backend="uia").connect(process=pid)
                win, title, app = find_top_window_by_title(MAIN_TITLE_RE)
                if app is None:
                    continue
                attached_how = f"pid {pid} {name}"
                break
            except Exception as exc:  # noqa: BLE001
                logger.debug("attach pid=%s failed: %s", pid, exc)

    if app is None:
        if candidates:
            pid, name = candidates[0]
            raise EcusUiError(
                f"ECUS process is running (pid={pid}, {name}) but no main window found. "
                "Login ECUS manually, dismiss license popup, bring window to front, then retry."
            )
        if not Path(exe).exists():
            raise EcusUiError(
                f"ECUS executable not found: {exe}. "
                "Open/login ECUS5VNACCS first, then retry."
            )
        logger.info("starting ECUS: %s", exe)
        app = Application(backend="uia").start(f'"{exe}"')
        time.sleep(3)
        attached_how = "started"
        win, title, app2 = find_top_window_by_title(MAIN_TITLE_RE)
        if app2 is not None:
            app = app2
        logger.info("started ECUS (%.1fs)", time.time() - t0)

    main = ensure_main_window(app, timeout=8.0)
    reject_if_login(main)
    dismiss_blocking_dialogs(main)
    logger.info("ECUS ready in %.1fs (%s)", time.time() - t0, attached_how or "?")
    return app


def ensure_main_window(app: Any, timeout: float = 8.0):
    """Find ECUS main window via fast HWND enum (no Desktop.windows / children scans)."""
    deadline = time.time() + timeout
    last_err: Exception | None = None
    while time.time() < deadline:
        try:
            win, title, _app = find_top_window_by_title(MAIN_TITLE_RE)
            if win is None:
                win, title, _app = find_top_window_by_title(EXPORT_FORM_TITLE_RE)
            if win is not None:
                try:
                    win.set_focus()
                except Exception:
                    pass
                reject_if_login(win)
                return win
            try:
                top = app.top_window()
                title = _window_title(top)
                if not _is_ignored_window_title(title):
                    if LOGIN_TITLE_RE.search(title or ""):
                        raise EcusUiError(
                            "ECUS login screen detected. Log in manually, then retry the job."
                        )
                    top.set_focus()
                    reject_if_login(top)
                    return top
            except EcusUiError:
                raise
            except Exception as exc:  # noqa: BLE001
                last_err = exc
        except EcusUiError:
            raise
        except Exception as exc:  # noqa: BLE001
            last_err = exc
        time.sleep(0.25)
    raise EcusUiError(f"Could not find ECUS main window: {last_err}")


def reject_if_login(window) -> None:
    title = _window_title(window)
    if LOGIN_TITLE_RE.search(title or ""):
        raise EcusUiError("ECUS login screen detected. Log in manually, then retry the job.")
    # Title-only check — never walk children (win32 AccessDenied / UIA slowness).


def title_has_export_form(title: str | None) -> bool:
    return bool(EXPORT_FORM_TITLE_RE.search(title or ""))


def form_has_input_fields(dlg, min_edits: int = 5) -> bool:
    """True when export form UI is actually visible (not just MDI title on home splash)."""
    try:
        edits = dlg.descendants(control_type="Edit")
        count = 0
        for _ in edits:
            count += 1
            if count >= min_edits:
                return True
    except Exception:
        return False
    return False


_EXPORT_FORM_ID = "frmVNACCS_EDA"
# Slow PCs paint the EDA window long before its edit boxes exist.
_EXPORT_FORM_READY_TIMEOUT_SEC = 45.0


def should_open_export_again(*, clicked: bool, shell_open: bool) -> bool:
    """False after an open click, or while the EDA window is already on screen.

    A second "Đăng ký mới tờ khai xuất khẩu" click while that form is still
    loading crashes ECUS on a slow machine.
    """
    return not clicked and not shell_open


def export_form_shell(main):
    """Return the EDA MDI child even while its fields are still loading."""
    if main is None:
        return None
    try:
        children = list(main.children())
    except Exception:
        return None
    for child in children:
        class_name = getattr(getattr(child, "element_info", None), "class_name", "") or ""
        if "MDICLIENT" in class_name:
            try:
                nested = list(child.children())
            except Exception:
                nested = []
            for mdi_child in nested:
                if _is_export_form_shell(mdi_child):
                    return mdi_child
        elif _is_export_form_shell(child):
            return child
    return None


def _is_export_form_shell(ctrl) -> bool:
    aid = getattr(getattr(ctrl, "element_info", None), "automation_id", "") or ""
    return aid == _EXPORT_FORM_ID


def _wait_for_export_shell(main, timeout: float):
    """Poll until the EDA window exists. Does not click."""
    deadline = time.time() + timeout
    while True:
        shell = export_form_shell(main)
        if shell is not None:
            return shell
        if time.time() >= deadline:
            return None
        time.sleep(0.4)


def _export_form_is_ready(dlg) -> bool:
    if dlg is None:
        return False
    if getattr(getattr(dlg, "element_info", None), "automation_id", "") == "frmMain":
        return False
    return form_has_input_fields(dlg)


def open_export_eda(main) -> None:
    """Open export EDA once. Never click the menu after a click already landed."""
    if not should_open_export_again(clicked=False, shell_open=export_form_shell(main) is not None):
        logger.info("export form is already opening; not clicking Đăng ký mới again")
        return

    main.set_focus()
    time.sleep(0.15)

    # Toolbar shortcuts often labeled EDA / show in tooltip.
    # One click only: a raised click_input often means the click already landed
    # and the menu popup (or the button) was destroyed while the form loaded.
    for title_re in (r".*\bEDA\b.*", r".*xuất khẩu.*EDA.*", r".*Export.*EDA.*"):
        try:
            btn = find_control(main, title_re=title_re, control_type="Button", timeout=0.6)
        except Exception:
            continue
        try:
            btn.click_input()
            logger.info("opened export EDA via toolbar button %s", title_re)
        except Exception as exc:
            # The click often lands, then click_input raises because the UI is busy
            # opening the form. A menu click here opens EDA a second time.
            logger.warning("toolbar EDA click raised; not opening the menu as well: %s", exc)
        time.sleep(0.5)
        return

    click_menu_export_eda(main)


def ensure_export_form_ready(app: Any, main) -> Any:
    """Return a dialog that actually has input fields; open EDA if only home splash is showing."""
    ensure_default_desktop()
    # 1. Fast check if export form is already open inside main or top level
    try:
        existing = find_export_form(app, timeout=1.0, main=main)
        if _export_form_is_ready(existing):
            logger.info("export form already open and ready")
            return existing
    except Exception:
        pass

    # A slow machine shows frmVNACCS_EDA before the edit boxes exist.
    # Clicking "Đăng ký mới" again at that point crashes the flow.
    if export_form_shell(main) is not None:
        logger.info("export form is still loading; waiting for fields without clicking again")
    else:
        logger.info("Export form not found or has no input fields; opening EDA once")
        open_export_eda(main)

    deadline = time.time() + _EXPORT_FORM_READY_TIMEOUT_SEC
    while time.time() < deadline:
        try:
            dlg = find_export_form(app, timeout=2.0, main=main)
            if _export_form_is_ready(dlg):
                logger.info("export form ready after open")
                return dlg
        except Exception:
            pass
        time.sleep(1.0)
    raise EcusUiError(
        "Form tờ khai xuất khẩu chưa hiện ô nhập liệu. "
        "Mở thủ công: Tờ khai hải quan → Đăng ký mới tờ khai xuất khẩu (EDA), "
        "chờ form hiện đủ field, rồi chạy lại job."
    )


def find_export_form(app: Any, timeout: float = 25.0, main: Any = None):
    ensure_default_desktop()
    deadline = time.time() + timeout
    while time.time() < deadline:
        # 1. Check MDI child in main first (fastest and most reliable)
        if main is not None:
            try:
                for c in main.children():
                    if "MDICLIENT" in getattr(c.element_info, "class_name", ""):
                        for mdi_c in c.children():
                            aid = getattr(mdi_c.element_info, "automation_id", "")
                            txt = mdi_c.window_text()
                            ctype = getattr(mdi_c.element_info, "control_type", "")
                            if (aid == "frmVNACCS_EDA" or title_has_export_form(txt)) and ctype in ("Window", "Pane"):
                                try:
                                    mdi_c.set_focus()
                                except Exception:
                                    pass
                                logger.info("found export form via MDI client child: %s", aid or txt)
                                return mdi_c
                    aid = getattr(c.element_info, "automation_id", "")
                    ctype = getattr(c.element_info, "control_type", "")
                    if aid != "frmMain" and (aid == "frmVNACCS_EDA" or title_has_export_form(c.window_text())) and ctype in ("Window", "Pane"):
                        try:
                            c.set_focus()
                        except Exception:
                            pass
                        logger.info("found export form via main child: %s", aid or c.window_text())
                        return c
            except Exception:
                pass

        # 2. Check top level windows - NEVER match frmMain
        for hwnd, title in enum_top_level_hwnd_titles():
            if _is_ignored_window_title(title):
                continue
            if EXPORT_FORM_TITLE_RE.search(title or ""):
                try:
                    win, _ = uia_window_from_hwnd(hwnd)
                    if getattr(win.element_info, "automation_id", "") != "frmMain" and form_has_input_fields(win):
                        try:
                            win.set_focus()
                        except Exception:
                            pass
                        logger.info("found export form via top window: %s", title)
                        return win
                except Exception:
                    continue

        # 3. Check all app windows - NEVER match frmMain
        try:
            for w in app.windows():
                aid = getattr(w.element_info, "automation_id", "")
                txt = w.window_text()
                ctype = getattr(w.element_info, "control_type", "")
                if aid != "frmMain" and (aid == "frmVNACCS_EDA" or title_has_export_form(txt)) and ctype in ("Window", "Pane"):
                    if form_has_input_fields(w):
                        try:
                            w.set_focus()
                        except Exception:
                            pass
                        return w
        except Exception:
            pass

        time.sleep(0.3)
    raise EcusUiError("Export declaration form did not open (title chứa 'Tờ khai xuất khẩu').")


def dismiss_blocking_dialogs(main=None) -> None:
    """Close license / exception popups by HWND title only (fast, no tree walk)."""
    license_re = re.compile(
        r"bản\s*quyền|đăng\s*ký\s*bản\s*quyền|Unhandled exception|đăng\s*ký\s*bản\s*quyền\s*sử\s*dụng",
        re.I,
    )
    for _ in range(3):
        dismissed = False
        for hwnd, title in enum_top_level_hwnd_titles():
            if not license_re.search(title or ""):
                continue
            try:
                win, _app = uia_window_from_hwnd(hwnd)
            except Exception:
                continue
            for btn_title in ("Thử nghiệm", "Continue", "Đóng"):
                try:
                    btn = find_control(win, title=btn_title, control_type="Button", timeout=0.4)
                    btn.click_input()
                    logger.info("dismissed dialog %r via '%s'", title, btn_title)
                    time.sleep(0.35)
                    dismissed = True
                    break
                except Exception:
                    continue
            if dismissed:
                break
        if not dismissed:
            break


def _click_export_eda_dropdown(main) -> bool:
    """Click the open-EDA menu item. True means a click was sent.

    click_input often raises after the click because the dropdown closes while
    the form starts loading. That must not be treated as "click missed".
    """
    import ctypes
    from ctypes import wintypes

    from pywinauto import Application

    user32 = ctypes.windll.user32
    pid = getattr(main.element_info, "process_id", None)
    drop_hwnds: list[int] = []

    def _cb(hwnd, _lparam):
        proc = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(proc))
        if (pid is None or proc.value == pid) and user32.IsWindowVisible(hwnd) and user32.GetWindowTextLengthW(hwnd) == 0:
            cls_buf = ctypes.create_unicode_buffer(256)
            user32.GetClassNameW(hwnd, cls_buf, 256)
            if "WindowsForms10.Window" in cls_buf.value:
                drop_hwnds.append(hwnd)
        return True

    user32.EnumDesktopWindows(0, ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)(_cb), 0)
    for hwnd in drop_hwnds:
        try:
            popup = Application(backend="uia").connect(handle=hwnd).window(handle=hwnd)
            for item in popup.children():
                label = item.window_text() or ""
                if "EDA" not in label and MENU_EXPORT_EDA not in label:
                    continue
                try:
                    item.click_input()
                    logger.info("clicked export EDA via dropdown popup")
                except Exception as exc:
                    logger.warning(
                        "dropdown EDA click raised; not opening the form again: %s",
                        exc,
                    )
                return True
        except Exception:
            continue
    return False


def click_menu_export_eda(main) -> None:
    main.set_focus()
    time.sleep(0.2)
    if not should_open_export_again(clicked=False, shell_open=export_form_shell(main) is not None):
        logger.info("export form is already opening; not clicking the EDA menu")
        return

    clicked = False
    # 1. Look for 'Tờ khai hải quan' menu item on MenuStrip1
    try:
        customs = find_control(
            main,
            title_re=r".*Tờ khai hải quan.*",
            control_type="MenuItem",
            timeout=2,
        )
        customs.click_input()
        time.sleep(0.4)
        clicked = _click_export_eda_dropdown(main)
    except Exception as exc:
        logger.debug("customs menu click failed: %s", exc)

    if not should_open_export_again(
        clicked=clicked,
        shell_open=export_form_shell(main) is not None,
    ):
        logger.info("EDA open already started; skipping menu_select")
        time.sleep(0.5)
        return

    # Fallback only when the EDA item was never clicked. After each attempt, wait
    # for the form window before trying another path — menu_select can click and
    # then raise while the form is still loading.
    for path in (
        f"{MENU_CUSTOMS}->{MENU_EXPORT_EDA}",
        "Tờ khai hải quan->Đăng ký mới tờ khai xuất khẩu (EDA)",
        "Tờ khai hải quan->Đăng ký mới tờ khai xuất khẩu",
    ):
        if not should_open_export_again(
            clicked=clicked,
            shell_open=export_form_shell(main) is not None,
        ):
            logger.info("export form is opening; not selecting %s", path)
            return
        try:
            main.menu_select(path)
            logger.info("menu_select ok: %s", path)
            time.sleep(0.5)
            return
        except Exception as exc:
            logger.debug("menu_select %s failed: %s", path, exc)
            if _wait_for_export_shell(main, 8.0) is not None:
                logger.info("export form opening after menu_select; not trying another path")
                return

    raise EcusUiError("Failed to open export EDA menu.")


def dismiss_code_lookup(dlg=None) -> int:
    """Close ECUS code-suggestion popups so they cannot steal tab clicks or F4.

    These lists are untitled top-level WindowsForms windows. ESC is sent only
    when one is visible, so the declaration form's own Cancel key is left alone.
    """
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    pid = None
    if dlg is not None:
        pid = getattr(getattr(dlg, "element_info", None), "process_id", None)

    found: list[int] = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def _callback(hwnd, _lparam):  # noqa: ANN001
        try:
            if not user32.IsWindowVisible(hwnd):
                return True
            if int(user32.GetWindowTextLengthW(hwnd)) > 0:
                return True
            if pid is not None:
                owner = wintypes.DWORD()
                user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
                if int(owner.value) != int(pid):
                    return True
            class_name = ctypes.create_unicode_buffer(256)
            user32.GetClassNameW(hwnd, class_name, 256)
            if "WindowsForms10.Window" not in class_name.value:
                return True
            rect = wintypes.RECT()
            user32.GetWindowRect(hwnd, ctypes.byref(rect))
            width = int(rect.right - rect.left)
            height = int(rect.bottom - rect.top)
            if width < 80 or height < 40 or width > 1400 or height > 900:
                return True
            found.append(int(hwnd))
        except Exception:
            pass
        return True

    user32.EnumWindows(_callback, 0)
    if not found:
        return 0
    send_key("{ESC}")
    time.sleep(0.2)
    logger.info("dismissed %s ECUS code lookup popup(s)", len(found))
    return len(found)


def select_tab(dlg, title: str, index: DialogIndex | None = None) -> bool:
    dismiss_code_lookup(dlg)
    patterns = [title, title.replace("Thông tin ", "")]

    # Check TabTK first (the main TabControl of frmVNACCS_EDA)
    try:
        tab_tk = None
        if index is not None and "TabTK" in index.by_auto_id:
            tab_tk = index.by_auto_id["TabTK"]
        else:
            for c in dlg.children():
                if getattr(c.element_info, "automation_id", "") == "TabTK":
                    tab_tk = c
                    break
        if tab_tk is not None:
            for tab in tab_tk.children():
                if getattr(tab.element_info, "control_type", "") == "TabItem":
                    name = tab.window_text()
                    if any(p.lower() in name.lower() for p in patterns):
                        try:
                            tab.click_input()
                            time.sleep(0.15)
                            logger.info("selected tab via TabTK: %s", name)
                            return True
                        except Exception:
                            pass
    except Exception:
        pass

    if index is not None and index.select_tab(title):
        return True

    for pattern in patterns:
        try:
            tab = find_control(
                dlg,
                title_re=f".*{re.escape(pattern)}.*",
                control_type="TabItem",
                timeout=0.8,
                index=index,
            )
            try:
                tab.click_input()
            except Exception:
                tab.select()
            time.sleep(0.15)
            logger.info("selected tab: %s", pattern)
            return True
        except Exception:
            continue
    logger.warning("Could not select tab '%s'", title)
    return False


def click_button(dlg, title: str, timeout: float = 8.0, index: DialogIndex | None = None) -> None:
    if index is not None:
        ctrl = index.find(title_re=f".*{re.escape(title)}.*", control_type="Button")
        if ctrl is not None:
            if hasattr(ctrl, "is_enabled") and not ctrl.is_enabled():
                raise EcusUiError(
                    f"Button '{title}' is disabled — fill required fields (*) first, then retry Ghi."
                )
            ctrl.click_input()
            logger.info("clicked button: %s", title)
            time.sleep(0.25)
            return
        # Index already scanned the dialog — do not fall back to another descendants() walk.
        raise EcusUiError(f"Could not click button '{title}': not found in DialogIndex")

    deadline = time.time() + timeout
    last_err: Exception | None = None
    while time.time() < deadline:
        try:
            btn = find_control(
                dlg,
                title_re=f".*{re.escape(title)}.*",
                control_type="Button",
                timeout=0.4,
            )
            if hasattr(btn, "is_enabled") and not btn.is_enabled():
                raise EcusUiError(
                    f"Button '{title}' is disabled — fill required fields (*) first, then retry Ghi."
                )
            btn.click_input()
            logger.info("clicked button: %s", title)
            time.sleep(0.25)
            return
        except EcusUiError:
            raise
        except Exception as exc:  # noqa: BLE001
            last_err = exc
            time.sleep(0.2)
    raise EcusUiError(f"Could not click button '{title}': {last_err}")


def is_blocking_dialog_title(title: str | None) -> bool:
    """Validation and error popups must fail the job instead of being clicked away."""
    text = (title or "").lower()
    return any(token in text for token in ("validation", "c1input", "lỗi", "error"))


def find_blocking_dialog() -> str | None:
    ensure_default_desktop()
    for _hwnd, title in enum_top_level_hwnd_titles():
        if is_blocking_dialog_title(title):
            return title
    return None


def dismiss_dialogs(app: Any = None, prefer_ok: bool = True) -> None:
    ensure_default_desktop()
    try:
        for hwnd, title in enum_top_level_hwnd_titles():
            if not title:
                continue
            if MAIN_TITLE_RE.search(title) or EXPORT_FORM_TITLE_RE.search(title):
                continue
            if is_blocking_dialog_title(title):
                logger.warning("left blocking dialog open: %s", title)
                continue
            if not re.search(r"thông\s*báo|warning|error|confirm|xác\s*nhận", title, re.I):
                continue
            try:
                win, _app = uia_window_from_hwnd(hwnd)
            except Exception:
                continue
            dismissed = False
            # First pass: check for positive confirmation buttons (Yes / OK / Đồng ý / Có)
            for c in win.children():
                if c.element_info.control_type == "Button":
                    btn_id = getattr(c.element_info, "automation_id", "")
                    btn_txt = (c.window_text() or "").strip().lower()
                    is_positive = (
                        btn_id in ("cmdYes", "cmdOK", "btnOK")
                        or btn_txt in ("ok", "yes", "có", "co", "đồng ý", "dong y")
                        or any(btn_txt.startswith(w) for w in ("ok", "yes", "có ", "đồng ý "))
                    )
                    if is_positive:
                        try:
                            saved_txt = c.window_text()
                            c.click_input()
                            logger.info("dismissed dialog '%s' via positive button '%s' (%s)", title, saved_txt, btn_id)
                            time.sleep(0.2)
                            dismissed = True
                            break
                        except Exception:
                            pass
            if not dismissed:
                for c in win.children():
                    if c.element_info.control_type == "Button":
                        btn_id = getattr(c.element_info, "automation_id", "")
                        btn_txt = (c.window_text() or "").strip().lower()
                        if btn_id in ("cmdCancel", "btnDong", "cmdDong") or btn_txt in ("hủy", "hủy bỏ", "cancel", "đóng"):
                            try:
                                saved_txt = c.window_text()
                                c.click_input()
                                logger.info("dismissed dialog '%s' via fallback button '%s' (%s)", title, saved_txt, btn_id)
                                time.sleep(0.2)
                                dismissed = True
                                break
                            except Exception:
                                pass
            if dismissed:
                continue
            for label in (("OK", "Đồng ý", "Yes", "Có") if prefer_ok else ("OK",)):
                try:
                    btn = find_control(win, title_re=f".*{label}.*", control_type="Button", timeout=0.2)
                    btn.click_input()
                    logger.info("dismissed dialog '%s' via %s", title, label)
                    time.sleep(0.2)
                    break
                except Exception:
                    continue
    except Exception as exc:  # noqa: BLE001
        logger.debug("dismiss_dialogs: %s", exc)


def set_edit_by_strategies(
    dlg,
    *,
    labels: list[str],
    value: str,
    auto_ids: list[str] | None = None,
    index: DialogIndex | None = None,
) -> bool:
    text = as_text(value)
    if not text:
        return False

    if index is not None:
        for auto_id in auto_ids or []:
            if index.set_by_auto_id(auto_id, text):
                return True
        if index.set_by_labels(labels, text):
            return True
        logger.warning("field not found for auto_ids=%s labels=%s value=%s", auto_ids, labels, text[:40])
        return False

    for auto_id in auto_ids or []:
        if _set_control(dlg, auto_id=auto_id, value=text):
            return True

    for label in labels:
        if _set_by_label(dlg, label, text):
            return True
    logger.warning("field not found for auto_ids=%s labels=%s value=%s", auto_ids, labels, text[:40])
    return False


def set_field_below_section(
    dlg,
    section_title: str,
    field_label: str,
    value: str,
    index: DialogIndex | None = None,
) -> bool:
    text = as_text(value)
    if not text:
        return False
    if index is not None:
        return index.set_below_section(section_title, field_label, text)
    try:
        section = find_control(dlg, title_re=f".*{re.escape(section_title)}.*", control_type="Text", timeout=0.5)
        sx, sy, _, _ = _rect(section)
    except Exception:
        try:
            section = find_control(dlg, title_re=f".*{re.escape(section_title)}.*", timeout=0.5)
            sx, sy, _, _ = _rect(section)
        except Exception as exc:  # noqa: BLE001
            logger.debug("section '%s' not found: %s", section_title, exc)
            return set_edit_by_strategies(dlg, labels=[field_label], value=text)

    label_candidates = []
    for ctrl in dlg.descendants(control_type="Text"):
        try:
            name = as_text(ctrl.window_text())
            if field_label.lower() not in name.lower():
                continue
            lx, ly, _, _ = _rect(ctrl)
            if ly < sy - 5:
                continue
            label_candidates.append((abs(ly - sy) + abs(lx - sx) * 0.05, lx, ly, ctrl))
        except Exception:
            continue
    label_candidates.sort(key=lambda t: t[0])
    if not label_candidates:
        return set_edit_by_strategies(dlg, labels=[field_label], value=text)

    _, lx, ly, _label_ctrl = label_candidates[0]
    best = None
    best_score = 1e18
    for ctrl in list(dlg.descendants(control_type="Edit")) + list(dlg.descendants(control_type="ComboBox")):
        try:
            if not ctrl.is_enabled() or not ctrl.is_visible():
                continue
            cx, cy, _, _ = _rect(ctrl)
            if cy < ly - 25:
                continue
            score = abs(cy - ly) * 25 + abs(cx - lx)
            if cx >= lx - 10:
                score -= 40
            if score < best_score:
                best_score = score
                best = ctrl
        except Exception:
            continue
    if best is None:
        return False
    return _write_control(best, text)


def _set_by_label(dlg, label: str, value: str) -> bool:
    for control_type in ("Edit", "ComboBox"):
        try:
            ctrl = find_control(dlg, title_re=f".*{re.escape(label)}.*", control_type=control_type)
            return _write_control(ctrl, value)
        except Exception:
            pass

    try:
        label_ctrl = find_control(dlg, title_re=f".*{re.escape(label)}.*", control_type="Text")
        lx, ly, _, _ = _rect(label_ctrl)
        best = None
        best_score = 1e18
        for ctrl in list(dlg.descendants(control_type="Edit")) + list(dlg.descendants(control_type="ComboBox")):
            try:
                if not ctrl.is_enabled() or not ctrl.is_visible():
                    continue
                cx, cy, _, _ = _rect(ctrl)
                if cx < lx - 5 and cy < ly - 8:
                    continue
                score = abs(cy - ly) * 20 + abs(cx - lx)
                if cx >= lx:
                    score -= 50
                if score < best_score:
                    best_score = score
                    best = ctrl
            except Exception:
                continue
        if best is not None:
            return _write_control(best, value)
    except Exception:
        pass
    return False


def _set_control(dlg, *, auto_id: str, value: str) -> bool:
    for control_type in ("Edit", "ComboBox"):
        try:
            ctrl = find_control(dlg, auto_id=auto_id, control_type=control_type)
            return _write_control(ctrl, value)
        except Exception:
            continue
    return False


_DATE_RE = re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{4})$")


def date_digits(text: str) -> str | None:
    """`30/09/2026` → `30092026`. None when the text is not a dd/MM/yyyy date."""
    match = _DATE_RE.match(as_text(text))
    if not match:
        return None
    day, month, year = (int(part) for part in match.groups())
    if not (1 <= day <= 31 and 1 <= month <= 12):
        return None
    return f"{day:02d}{month:02d}{year:04d}"


def date_text_matches(actual: str, digits: str) -> bool | None:
    """True/False when the control exposes digits. None when it exposes no text."""
    got = "".join(ch for ch in as_text(actual) if ch.isdigit())
    if not got:
        return None
    return got == digits


def _paste_text(ctrl, text: str) -> bool:
    try:
        import win32clipboard, win32con
        win32clipboard.OpenClipboard()
        try:
            win32clipboard.EmptyClipboard()
            win32clipboard.SetClipboardText(text, win32con.CF_UNICODETEXT)
        finally:
            win32clipboard.CloseClipboard()
        ctrl.type_keys("^a{BACKSPACE}^v", pause=0.02)
        # C1 handles WM_PASTE asynchronously. The next field must not replace
        # the clipboard until this control has consumed it.
        time.sleep(0.2)
        return True
    except Exception:
        try:
            ctrl.type_keys("^a{BACKSPACE}" + _escape_keys(text), with_spaces=True, pause=0.02)
            time.sleep(0.05)
            return True
        except Exception:
            return False


def _dismiss_c1_validation() -> bool:
    """Close a C1 date-parse popup so a retry can type into the field again."""
    ensure_default_desktop()
    try:
        for hwnd, title in enum_top_level_hwnd_titles():
            if "c1input" not in (title or "").lower():
                continue
            try:
                win, _app = uia_window_from_hwnd(hwnd)
            except Exception:
                continue
            for child in win.children():
                if child.element_info.control_type != "Button":
                    continue
                btn_txt = (child.window_text() or "").strip().lower()
                btn_id = getattr(child.element_info, "automation_id", "")
                if btn_id in ("cmdOK", "btnOK", "2") or btn_txt == "ok":
                    child.click_input()
                    logger.info("dismissed C1 date validation dialog")
                    time.sleep(0.15)
                    return True
    except Exception as exc:  # noqa: BLE001
        logger.debug("dismiss C1 validation: %s", exc)
    return False


def _write_masked_date(ctrl, text: str) -> bool:
    """Type a date into a C1 mask as 8 digits.

    Pasting `30/09/2026` is unsafe: the mask already contains slashes, and the
    following field (the note, which holds the MST) replaces the clipboard
    before this control handles WM_PASTE. The mask then keeps whatever digits
    arrived, which is how Ngày đến showed `22/00/7076`.
    """
    digits = date_digits(text)
    if not digits:
        return False
    for attempt in range(2):
        ctrl.set_focus()
        time.sleep(0.08)
        # Overwrite from the first segment. Do not send '/' — that skips the month.
        ctrl.type_keys("{HOME}{HOME}" + digits, pause=0.05)
        time.sleep(0.15)
        if _dismiss_c1_validation():
            logger.warning("date %s rejected by C1 on attempt %s", text, attempt + 1)
            continue
        try:
            actual = as_text(ctrl.window_text())
        except Exception:
            actual = ""
        matched = date_text_matches(actual, digits)
        if matched is not False:
            logger.debug("typed date %s into %s (read=%r)", text, _describe(ctrl), actual)
            return True
        logger.warning(
            "date mismatch on %s attempt %s expected %s got %r",
            _describe(ctrl),
            attempt + 1,
            text,
            actual,
        )
    logger.error("could not set date %s on %s", text, _describe(ctrl))
    return False


def _write_control(ctrl, value: str) -> bool:
    try:
        text = as_text(value)
        if not text:
            return True
        if _ctrl_type(ctrl).lower() in ("pane", "group", "custom"):
            for child in ctrl.children():
                if _ctrl_type(child).lower() == "edit":
                    ctrl = child
                    break
        if date_digits(text):
            return _write_masked_date(ctrl, text)
        ctrl.set_focus()
        time.sleep(0.01)
        has_unicode = any(ord(c) > 127 for c in text)
        if has_unicode or len(text) > 8:
            if _paste_text(ctrl, text):
                logger.debug("pasted value into %s", _describe(ctrl))
                return True
        try:
            ctrl.type_keys("^a{BACKSPACE}" + _escape_keys(text), with_spaces=True, pause=0.005)
            logger.debug("typed value into %s", _describe(ctrl))
            return True
        except Exception:
            return _paste_text(ctrl, text)
    except Exception as exc:  # noqa: BLE001
        logger.debug("write failed %s: %s", _describe(ctrl), exc)
        return False


def type_into_focused(value: str) -> None:
    from pywinauto.keyboard import send_keys  # type: ignore

    send_keys("^a{BACKSPACE}" + _escape_keys(as_text(value)), with_spaces=True)
    time.sleep(0.05)


def send_key(keys: str) -> None:
    from pywinauto.keyboard import send_keys  # type: ignore

    send_keys(keys)
    time.sleep(0.08)


def read_edit_near_label(dlg, label: str) -> str:
    try:
        ctrl = find_control(dlg, title_re=f".*{re.escape(label)}.*", control_type="Edit")
        return as_text(ctrl.window_text())
    except Exception:
        pass
    try:
        label_ctrl = find_control(dlg, title_re=f".*{re.escape(label)}.*", control_type="Text")
        lx, ly, _, _ = _rect(label_ctrl)
        best = None
        best_score = 1e18
        for ctrl in dlg.descendants(control_type="Edit"):
            try:
                cx, cy, _, _ = _rect(ctrl)
                score = abs(cy - ly) * 20 + abs(cx - lx)
                if score < best_score:
                    best_score = score
                    best = ctrl
            except Exception:
                continue
        if best is not None:
            return as_text(best.window_text())
    except Exception:
        pass
    return ""


def dump_controls(window, out_path: Path) -> Path:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    title = _window_title(window)
    lines.append(f"WINDOW: {title}")
    lines.append("=" * 80)

    try:
        from io import StringIO
        import sys

        buf = StringIO()
        old = sys.stdout
        sys.stdout = buf
        try:
            window.print_control_identifiers(depth=12)
        finally:
            sys.stdout = old
        lines.append(buf.getvalue())
    except Exception as exc:  # noqa: BLE001
        lines.append(f"[print_control_identifiers failed: {exc}]")
        lines.append("")
        lines.extend(_walk_tree(window, depth=0, max_depth=10))

    out_path.write_text("\n".join(lines), encoding="utf-8")
    logger.info("UI dump written to %s", out_path)
    return out_path


def _walk_tree(ctrl, depth: int, max_depth: int) -> list[str]:
    if depth > max_depth:
        return []
    rows: list[str] = []
    try:
        rows.append(
            f"{'  ' * depth}- type={ctrl.element_info.control_type} "
            f"name={ctrl.window_text()!r} auto_id={getattr(ctrl.element_info, 'automation_id', '')!r}"
        )
        for child in ctrl.children():
            rows.extend(_walk_tree(child, depth + 1, max_depth))
    except Exception as exc:  # noqa: BLE001
        rows.append(f"{'  ' * depth}- <error {exc}>")
    return rows


def _window_title(window) -> str:
    try:
        return as_text(window.window_text())
    except Exception:
        try:
            return as_text(window.element_info.name)
        except Exception:
            return ""


def _rect(ctrl) -> tuple[int, int, int, int]:
    r = ctrl.rectangle()
    return int(r.left), int(r.top), int(r.right), int(r.bottom)


def _describe(ctrl) -> str:
    try:
        return (
            f"{ctrl.element_info.control_type}/"
            f"id={getattr(ctrl.element_info, 'automation_id', '')}/"
            f"name={ctrl.window_text()!r}"
        )
    except Exception:
        return "<ctrl>"


def _escape_keys(text: str) -> str:
    out = []
    for ch in text:
        if ch in "+^%~(){}[]":
            out.append("{" + ch + "}")
        else:
            out.append(ch)
    return "".join(out)

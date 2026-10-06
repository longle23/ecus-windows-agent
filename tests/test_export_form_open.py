from __future__ import annotations

import unittest
from unittest.mock import patch

from app.rpa.ui_helpers import (
    EcusUiError,
    click_menu_export_eda,
    ensure_export_form_ready,
    export_form_shell,
    should_open_export_again,
)


class _Info:
    def __init__(self, automation_id: str = "", class_name: str = "", control_type: str = "Window") -> None:
        self.automation_id = automation_id
        self.class_name = class_name
        self.control_type = control_type


class _Ctrl:
    def __init__(self, children: list | None = None, **info) -> None:
        self.element_info = _Info(**info)
        self.children_list = list(children or [])
        self.clicks: list[str] = []
        self.menu_paths: list[str] = []

    def children(self):
        return list(self.children_list)

    def set_focus(self) -> None:
        return None

    def click_input(self) -> None:
        self.clicks.append("click")

    def menu_select(self, path: str) -> None:
        self.menu_paths.append(path)

    def descendants(self, control_type: str | None = None):
        return [object() for _ in range(6)]


class ShouldOpenExportAgainTests(unittest.TestCase):
    def test_second_click_is_blocked_after_a_click_or_while_the_window_exists(self) -> None:
        self.assertFalse(should_open_export_again(clicked=True, shell_open=False))
        self.assertFalse(should_open_export_again(clicked=False, shell_open=True))
        self.assertTrue(should_open_export_again(clicked=False, shell_open=False))


class ExportFormShellTests(unittest.TestCase):
    def test_finds_loading_eda_window_without_input_fields(self) -> None:
        form = _Ctrl(automation_id="frmVNACCS_EDA", control_type="Window")
        mdi = _Ctrl(children=[form], class_name="MDICLIENT", control_type="Pane")
        main = _Ctrl(children=[mdi], automation_id="frmMain")
        self.assertIs(export_form_shell(main), form)

    def test_home_window_is_not_the_export_form(self) -> None:
        main = _Ctrl(automation_id="frmMain", class_name="WindowsForms10.Window")
        self.assertIsNone(export_form_shell(main))


class ClickMenuOnceTests(unittest.TestCase):
    def setUp(self) -> None:
        sleep = patch("app.rpa.ui_helpers.time.sleep", lambda *_args, **_kwargs: None)
        sleep.start()
        self.addCleanup(sleep.stop)

    def test_dropdown_click_does_not_fall_through_to_menu_select(self) -> None:
        main = _Ctrl(automation_id="frmMain")
        menu = _Ctrl()
        with (
            patch("app.rpa.ui_helpers.find_control", return_value=menu),
            patch("app.rpa.ui_helpers._click_export_eda_dropdown", return_value=True),
        ):
            click_menu_export_eda(main)
        self.assertEqual(main.menu_paths, [])
        self.assertEqual(menu.clicks, ["click"])

    def test_menu_select_stops_when_the_form_window_appears(self) -> None:
        form = _Ctrl(automation_id="frmVNACCS_EDA")
        mdi = _Ctrl(class_name="MDICLIENT", control_type="Pane")
        main = _Ctrl(children=[mdi], automation_id="frmMain")

        def _open_then_fail(path: str) -> None:
            main.menu_paths.append(path)
            mdi.children_list.append(form)
            raise RuntimeError("menu closed while the form was opening")

        main.menu_select = _open_then_fail  # type: ignore[method-assign]
        with (
            patch("app.rpa.ui_helpers.find_control", return_value=_Ctrl()),
            patch("app.rpa.ui_helpers._click_export_eda_dropdown", return_value=False),
        ):
            click_menu_export_eda(main)
        self.assertEqual(main.menu_paths, ["Tờ khai hải quan->Đăng ký mới tờ khai xuất khẩu (EDA)"])


class EnsureDoesNotReopenTests(unittest.TestCase):
    def setUp(self) -> None:
        sleep = patch("app.rpa.ui_helpers.time.sleep", lambda *_args, **_kwargs: None)
        sleep.start()
        self.addCleanup(sleep.stop)

    def test_loading_form_is_not_opened_again(self) -> None:
        form = _Ctrl(automation_id="frmVNACCS_EDA")
        mdi = _Ctrl(children=[form], class_name="MDICLIENT", control_type="Pane")
        main = _Ctrl(children=[mdi], automation_id="frmMain")
        ready = _Ctrl(automation_id="frmVNACCS_EDA")
        finds = {"n": 0}
        opened = {"n": 0}

        def _find(*_args, **_kwargs):
            finds["n"] += 1
            if finds["n"] == 1:
                raise EcusUiError("fields not ready")
            return ready

        with (
            patch("app.rpa.ui_helpers.find_export_form", side_effect=_find),
            patch("app.rpa.ui_helpers.open_export_eda", side_effect=lambda _main: opened.__setitem__("n", opened["n"] + 1)),
        ):
            found = ensure_export_form_ready(app=None, main=main)
        self.assertIs(found, ready)
        self.assertEqual(opened["n"], 0)


if __name__ == "__main__":
    unittest.main()

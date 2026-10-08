from __future__ import annotations

import unittest
from unittest.mock import patch

from app.config import Settings
from app.models import EcusJobRequest
from app.rpa.ecus_pywinauto import EcusFlowError, PywinautoEcusRpa
from app.rpa.ui_helpers import EcusUiError


class _Info:
    def __init__(self, automation_id: str = "", class_name: str = "", control_type: str = "Window") -> None:
        self.automation_id = automation_id
        self.class_name = class_name
        self.control_type = control_type


class _Win:
    def __init__(
        self,
        automation_id: str = "",
        title: str = "",
        class_name: str = "",
        control_type: str = "Window",
        children: list | None = None,
        edit_count: int = 0,
        body: str = "",
    ) -> None:
        self.element_info = _Info(automation_id, class_name, control_type)
        self.children_list = list(children or [])
        self.title = title
        self.edit_count = edit_count
        self.body = body

    def children(self):
        return list(self.children_list)

    def window_text(self) -> str:
        return self.title

    def set_focus(self) -> None:
        return None

    def descendants(self, control_type: str | None = None):
        return [object() for _ in range(self.edit_count)]


class _Btn:
    def __init__(self, aid: str, text: str, on_click) -> None:
        self.element_info = _Info(aid, control_type="Button")
        self._text = text
        self._on_click = on_click

    def window_text(self) -> str:
        return self._text

    def is_enabled(self) -> bool:
        return True

    def click_input(self) -> None:
        self._on_click()


class _App:
    def windows(self):
        return []


class _FastRpa(PywinautoEcusRpa):
    def _prepare_home_for_job(self, app, main, *, timeout: float = 8.0):
        return super()._prepare_home_for_job(app, main, timeout=0)


def _main_with(*mdi_children: _Win) -> tuple[_Win, _Win]:
    mdi = _Win(class_name="MDICLIENT", control_type="Pane", children=list(mdi_children))
    main = _Win(automation_id="frmMain", title="ECUS5 VNACCS", children=[mdi])
    return main, mdi


class PrepareHomeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.main: _Win | None = None
        self.notices: list[_Win] = []
        self.goods: _Win | None = None
        self.opened: list[str] = []
        self.code_lookups: list = []
        self.blocking_dismissed: list = []
        self.clicks: list[str] = []

        def _ensure_main(_app, timeout: float = 5.0):
            return self.main

        def _open_eda(_main) -> None:
            self.opened.append("eda")

        def _dismiss_lookup(_dlg=None) -> int:
            self.code_lookups.append(_dlg)
            return 0

        def _dismiss_blocking(_main=None) -> None:
            self.blocking_dismissed.append(_main)

        def _find_top(_title_re, timeout: float = 0.0):
            if self.goods is None:
                return None, None, None
            return self.goods, self.goods.window_text(), None

        patches = [
            patch("app.rpa.ecus_pywinauto.time.sleep", lambda *_args, **_kwargs: None),
            patch("app.rpa.ecus_pywinauto.ui.enum_top_level_hwnd_titles", return_value=[]),
            patch("app.rpa.ecus_pywinauto.ui.ensure_main_window", side_effect=_ensure_main),
            patch("app.rpa.ecus_pywinauto.ui.open_export_eda", side_effect=_open_eda),
            patch("app.rpa.ecus_pywinauto.ui.dismiss_code_lookup", side_effect=_dismiss_lookup),
            patch("app.rpa.ecus_pywinauto.ui.dismiss_blocking_dialogs", side_effect=_dismiss_blocking),
            patch("app.rpa.ecus_pywinauto.ui.find_top_window_by_title", side_effect=_find_top),
            patch("app.rpa.ecus_pywinauto.ui.connect_ecus", return_value=_App()),
        ]
        for item in patches:
            item.start()
            self.addCleanup(item.stop)

    def _rpa(self, *, fast: bool = False) -> PywinautoEcusRpa:
        settings = Settings(screenshot_on_error=False)
        rpa = _FastRpa(settings) if fast else PywinautoEcusRpa(settings)
        notices = self.notices

        def _iter():
            for win in list(notices):
                yield win, win.window_text(), win.body, False

        rpa._iter_notice_windows = _iter  # type: ignore[method-assign]
        return rpa

    def _export_form(self, *, edits: int, with_close: bool, on_close=None) -> _Win:
        children = []
        if with_close:
            children.append(_Btn("btnDong", "Đóng", on_close or (lambda: None)))
        return _Win(
            automation_id="frmVNACCS_EDA",
            title="Tờ khai xuất khẩu",
            children=children,
            edit_count=edits,
        )

    def test_home_screen_is_left_untouched(self) -> None:
        splash = _Win(title="Trang chủ", control_type="Pane")
        tab_bar = _Win(automation_id="frmShowTabBar")
        background = _Win(automation_id="frmBKGround")
        self.main, _mdi = _main_with(splash, tab_bar, background)
        warnings = self._rpa()._prepare_home_for_job(_App(), self.main)
        self.assertEqual(warnings, [])
        self.assertEqual(self.clicks, [])
        self.assertEqual(self.opened, [])
        self.assertEqual(self.code_lookups, [])
        self.assertEqual(self.blocking_dismissed, [])

    def test_open_export_form_is_closed_then_unsaved_prompt_gets_no(self) -> None:
        def _close() -> None:
            self.clicks.append("dong-form")
            mdi.children_list.remove(form)
            self.notices.append(_unsaved_dialog(self.clicks, self.notices))

        form = self._export_form(edits=6, with_close=True, on_close=_close)
        self.main, mdi = _main_with(form)
        warnings = self._rpa()._prepare_home_for_job(_App(), self.main)
        self.assertEqual(self.clicks, ["dong-form", "no"])
        self.assertEqual(warnings, ["closed a previous declaration without saving"])
        self.assertEqual(self.opened, [])

    def test_loading_shell_is_not_opened_again(self) -> None:
        form = self._export_form(edits=0, with_close=False)
        self.main, _mdi = _main_with(form)
        with self.assertRaises(EcusFlowError) as caught:
            self._rpa(fast=True)._prepare_home_for_job(_App(), self.main)
        self.assertIn("frmVNACCS_EDA", str(caught.exception))
        self.assertEqual(self.opened, [])
        self.assertEqual(self.clicks, [])

    def test_loading_shell_with_close_button_is_closed_without_reopening(self) -> None:
        def _close() -> None:
            self.clicks.append("dong-form")
            mdi.children_list.remove(form)

        form = self._export_form(edits=0, with_close=True, on_close=_close)
        self.main, mdi = _main_with(form)
        warnings = self._rpa()._prepare_home_for_job(_App(), self.main)
        self.assertEqual(self.clicks, ["dong-form"])
        self.assertEqual(self.opened, [])
        self.assertEqual(warnings, ["closed a previous declaration without saving"])

    def test_goods_modal_closes_before_the_export_form(self) -> None:
        def _close_goods() -> None:
            self.clicks.append("dong-goods")
            self.goods = None

        def _close_form() -> None:
            self.clicks.append("dong-form")
            mdi.children_list.remove(form)

        form = self._export_form(edits=6, with_close=True, on_close=_close_form)
        self.goods = _Win(
            automation_id="frmVNACCS_HANGEDA",
            title="Hàng tờ khai",
            children=[_Btn("btnDong", "Đóng", _close_goods)],
        )
        self.main, mdi = _main_with(form)
        self._rpa()._prepare_home_for_job(_App(), self.main)
        self.assertEqual(self.clicks, ["dong-goods", "dong-form"])
        self.assertEqual(self.opened, [])

    def test_unsaved_prompt_clicks_no_not_yes(self) -> None:
        def _close_form() -> None:
            self.clicks.append("dong-form")
            mdi.children_list.remove(form)

        form = self._export_form(edits=6, with_close=True, on_close=_close_form)
        self.notices.append(_unsaved_dialog(self.clicks, self.notices))
        self.main, mdi = _main_with(form)
        self._rpa()._prepare_home_for_job(_App(), self.main)
        self.assertIn("no", self.clicks)
        self.assertNotIn("yes", self.clicks)
        self.assertLess(self.clicks.index("no"), self.clicks.index("dong-form"))

    def test_timeout_does_not_open_a_new_declaration(self) -> None:
        form = self._export_form(edits=6, with_close=False)
        self.main, _mdi = _main_with(form)
        ready = patch("app.rpa.ecus_pywinauto.ui.ensure_export_form_ready")
        ready_mock = ready.start()
        self.addCleanup(ready.stop)
        result = self._rpa(fast=True).register_declaration(EcusJobRequest(jobId="job-1"))
        self.assertFalse(result.success)
        self.assertIn("not on the home screen", result.message)
        ready_mock.assert_not_called()
        self.assertEqual(self.opened, [])

    def test_login_screen_stops_without_clicking_close(self) -> None:
        self.main = _Win(
            title="Đăng nhập ECUS",
            children=[_Btn("btnDong", "Đóng", lambda: self.clicks.append("dong"))],
        )
        with self.assertRaises(EcusUiError):
            self._rpa()._prepare_home_for_job(_App(), self.main)
        self.assertEqual(self.clicks, [])
        self.assertEqual(self.opened, [])
        self.assertEqual(self.blocking_dismissed, [])

    def test_home_chrome_after_close_is_still_the_home_screen(self) -> None:
        def _close() -> None:
            self.clicks.append("dong-form")
            mdi.children_list.remove(form)

        form = self._export_form(edits=6, with_close=True, on_close=_close)
        tab_bar = _Win(automation_id="frmShowTabBar")
        background = _Win(automation_id="frmBKGround")
        self.main, mdi = _main_with(form, tab_bar, background)
        warnings = self._rpa()._prepare_home_for_job(_App(), self.main)
        self.assertEqual(self.clicks, ["dong-form"])
        self.assertNotIn("no", self.clicks)
        self.assertEqual(warnings, ["closed a previous declaration without saving"])
        self.assertEqual(self.opened, [])

    def test_unknown_form_without_close_fails_with_its_name(self) -> None:
        foreign = _Win(automation_id="frmNhapKhau", title="Tờ khai nhập khẩu", edit_count=6)
        self.main, _mdi = _main_with(foreign)
        with self.assertRaises(EcusFlowError) as caught:
            self._rpa(fast=True)._prepare_home_for_job(_App(), self.main)
        self.assertIn("frmNhapKhau", str(caught.exception))
        self.assertIn("Tờ khai nhập khẩu", str(caught.exception))
        self.assertEqual(self.clicks, [])
        self.assertEqual(self.opened, [])

    def test_successful_job_reports_the_discarded_declaration(self) -> None:
        def _close() -> None:
            self.clicks.append("dong-form")
            mdi.children_list.remove(form)

        form = self._export_form(edits=6, with_close=True, on_close=_close)
        self.main, mdi = _main_with(form)
        ready = _Win(automation_id="frmVNACCS_EDA", edit_count=6)
        rpa = self._rpa()
        with (
            patch("app.rpa.ecus_pywinauto.ui.ensure_export_form_ready", return_value=ready),
            patch.object(rpa, "_fill_thong_tin_chung"),
            patch.object(rpa, "_fill_container"),
            patch.object(rpa, "_fill_hang_hoa", return_value=0),
            patch.object(rpa, "_save", return_value=""),
        ):
            result = rpa.register_declaration(EcusJobRequest(jobId="job-1"))
        self.assertTrue(result.success)
        self.assertEqual(result.warnings, ["closed a previous declaration without saving"])
        self.assertEqual(self.opened, [])


def _unsaved_dialog(clicks: list[str], notices: list[_Win]) -> _Win:
    def _no() -> None:
        clicks.append("no")
        notices.clear()

    def _yes() -> None:
        clicks.append("yes")

    return _Win(
        title="Thông báo",
        body="Dữ liệu đã thay đổi, bạn có muốn ghi dữ liệu không?",
        children=[
            _Btn("cmdYes", "Yes", _yes),
            _Btn("cmdNo", "No", _no),
        ],
    )

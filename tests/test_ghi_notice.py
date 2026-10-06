from __future__ import annotations

import unittest

from app.rpa.ecus_pywinauto import classify_notice_body


class ClassifyNoticeBodyTests(unittest.TestCase):
    def test_saved_notice(self) -> None:
        self.assertEqual(classify_notice_body("Thông báo Đã ghi xong."), "saved")

    def test_unsaved_prompt_is_not_a_yes_click(self) -> None:
        body = "Thông báo Dữ liệu đã thay đổi, bạn có muốn ghi dữ liệu không?"
        self.assertEqual(classify_notice_body(body), "unsaved")

    def test_past_departure_date_keeps_the_date(self) -> None:
        body = (
            "Thông báo Ngày hàng đi dự kiến không được nhỏ hơn ngày hiện tại, "
            "bạn có muốn sửa lại ngày hàng đi không?"
        )
        self.assertEqual(classify_notice_body(body), "keep_date")

    def test_identity_number_notice_is_closed(self) -> None:
        body = (
            "Thông báo khai số định danh hàng hóa "
            "Thực hiện Quyết định của Bộ tài chính về việc thí điểm khai số vận đơn. "
            "Đăng ký số định danh hàng hóa trước khi khai báo tờ khai."
        )
        self.assertEqual(classify_notice_body(body), "close_notice")

    def test_known_currency_prompt(self) -> None:
        body = "Bạn có muốn sửa tất cả các nguyên tệ của tờ khai không?"
        self.assertEqual(classify_notice_body(body), "yes_prompt")

    def test_other_notice_is_an_error(self) -> None:
        self.assertEqual(classify_notice_body("Thông báo Mã HS không hợp lệ"), "error")

    def test_blocking_title_is_an_error(self) -> None:
        self.assertEqual(classify_notice_body("Validation", blocking=True), "error")

    def test_title_only_notice_keeps_waiting(self) -> None:
        self.assertEqual(classify_notice_body("Thông báo"), "pending")


if __name__ == "__main__":
    unittest.main()

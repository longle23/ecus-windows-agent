from __future__ import annotations

import logging
import re
import time
from datetime import datetime
from pathlib import Path

from app.config import Settings
from app.models import EcusJobRequest
from app.rpa import RpaResult
from app.rpa import ui_helpers as ui

logger = logging.getLogger(__name__)

_REQUIRED_FIELDS = {
    "txtMA_LH": "mã loại hình",
    "txtMA_HQ": "cơ quan hải quan",
    "txtSO_HDTM": "số hóa đơn",
}

_SAVED_RE = re.compile(r"đ[aã]\s*ghi\s*xong|da\s*ghi\s*xong", re.IGNORECASE)
_UNSAVED_RE = re.compile(
    r"thay\s*đổi|thay\s*doi|muốn\s*ghi\s*dữ\s*liệu|muon\s*ghi\s*du\s*lieu",
    re.IGNORECASE,
)
_KNOWN_YES_RE = re.compile(r"nguyên\s*tệ|nguyen\s*te", re.IGNORECASE)
_KEEP_DEPARTURE_DATE_RE = re.compile(
    r"ngày\s*hàng\s*đi.{0,80}nhỏ\s*hơn|ngay\s*hang\s*di.{0,80}nho\s*hon",
    re.IGNORECASE | re.DOTALL,
)
_NOTICE_TITLE_RE = re.compile(r"thông\s*báo|warning|confirm|xác\s*nhận", re.IGNORECASE)


def classify_notice_body(body: str, *, blocking: bool = False) -> str:
    """Classify a popup seen while waiting for Ghi to finish.

    saved: success notice. yes_prompt: known confirmation such as nguyên tệ.
    keep_date: departure date is before today; click No and keep waiting for Đã ghi xong.
    unsaved: discard prompt after Đóng. error: a message that must stop the wait.
    pending: a notice whose text has not appeared yet.
    """
    text = body or ""
    if _SAVED_RE.search(text):
        return "saved"
    if _UNSAVED_RE.search(text):
        return "unsaved"
    if _KEEP_DEPARTURE_DATE_RE.search(text):
        return "keep_date"
    if not blocking and _KNOWN_YES_RE.search(text):
        return "yes_prompt"
    if blocking:
        return "error"
    meaningful = _NOTICE_TITLE_RE.sub(" ", text)
    meaningful = re.sub(r"[\W_]+", "", meaningful, flags=re.UNICODE)
    if not meaningful:
        return "pending"
    return "error"


class EcusFlowError(Exception):
    """A declaration step failed and must not be reported as Ghi completed."""

    def __init__(self, message: str, *, filled: int | None = None, warnings: list[str] | None = None) -> None:
        super().__init__(message)
        self.filled = filled
        self.warnings = warnings if warnings is not None else [message]


class PywinautoEcusRpa:
    """Drive ECUS5-VNACCS: open export EDA form, fill tabs, click Ghi."""

    name = "pywinauto"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def register_declaration(self, job: EcusJobRequest) -> RpaResult:
        requested = len(job.danh_sach_hang.hang_hoa or [])
        try:
            from pywinauto import Application  # noqa: F401  # type: ignore
        except ImportError as exc:
            return RpaResult(
                success=False,
                message=f"pywinauto is not installed on this machine: {exc}",
                hang_hoa_requested=requested,
                warnings=[str(exc)],
            )

        filled = 0
        form_opened = False
        app = None
        dlg = None
        try:
            t0 = time.time()
            app = ui.connect_ecus(self.settings)
            main = ui.ensure_main_window(app, timeout=5.0)
            main.set_focus()
            self._reject_if_export_form_open(app, main)
            dlg = ui.ensure_export_form_ready(app, main)
            form_opened = True
            dlg.set_focus()
            time.sleep(0.15)

            self._fill_thong_tin_chung(dlg, job)
            self._fill_container(dlg, job)
            filled = self._fill_hang_hoa(app, dlg, job)
            so_to_khai = self._save(app, dlg)
            if filled != requested:
                raise EcusFlowError(
                    f"filled {filled} of {requested} hang_hoa rows",
                    filled=filled,
                )
            logger.info("job %s RPA wall time %.1fs", job.jobId, time.time() - t0)

            return RpaResult(
                success=True,
                message="ECUS Ghi completed",
                so_to_khai=so_to_khai,
                hang_hoa_requested=requested,
                hang_hoa_filled=filled,
            )
        except (EcusFlowError, ui.EcusUiError) as exc:
            logger.warning("job %s failed: %s", job.jobId, exc)
            screenshot = self._maybe_screenshot(job.jobId)
            if isinstance(exc, EcusFlowError):
                filled_out = filled if exc.filled is None else exc.filled
                warnings = list(exc.warnings)
                message = str(exc)
            else:
                filled_out = filled
                warnings = [str(exc)]
                message = str(exc)
            message, warnings = self._recover_home_after_failure(
                app, dlg, form_opened=form_opened, message=message, warnings=warnings
            )
            return RpaResult(
                success=False,
                message=message,
                screenshot=screenshot,
                hang_hoa_requested=requested,
                hang_hoa_filled=filled_out,
                warnings=warnings,
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("job %s failed", job.jobId)
            screenshot = self._maybe_screenshot(job.jobId)
            message, warnings = self._recover_home_after_failure(
                app,
                dlg,
                form_opened=form_opened,
                message=f"RPA error: {exc}",
                warnings=[str(exc)],
            )
            return RpaResult(
                success=False,
                message=message,
                screenshot=screenshot,
                hang_hoa_requested=requested,
                hang_hoa_filled=filled,
                warnings=warnings,
            )

    def _reject_if_export_form_open(self, app, main) -> None:
        try:
            existing = ui.find_export_form(app, timeout=1.0, main=main)
        except ui.EcusUiError:
            return
        if existing is None:
            return
        if getattr(existing.element_info, "automation_id", "") == "frmMain":
            return
        if not ui.form_has_input_fields(existing):
            return
        raise EcusFlowError(
            "ECUS export form is already open. Close it before sending another job.",
            filled=0,
            warnings=["export form already open"],
        )

    # ------------------------------------------------------------------ fill
    def _fill_thong_tin_chung(self, dlg, job: EcusJobRequest) -> None:
        t0 = time.time()
        ui.select_tab(dlg, "Thông tin chung")
        # Target TabTTTK pane directly to index only relevant controls fast
        tab_tk = next((c for c in dlg.children() if getattr(c.element_info, "automation_id", "") == "TabTK"), None)
        active_pane = None
        if tab_tk:
            active_pane = next((c for c in tab_tk.children() if getattr(c.element_info, "automation_id", "") == "TabTTTK"), None)
        index = ui.DialogIndex(active_pane or dlg)

        chung = job.thong_tin_chung
        tk = chung.thong_tin_to_khai
        nk = chung.nguoi_nhap_khau
        vd = chung.van_don
        hd = chung.hop_dong
        inv = chung.hoa_don
        vc = chung.van_chuyen
        other = chung.thong_tin_khac

        errors: list[str] = []
        nhom = (tk.nhom_loai_hinh or "").strip()
        if nhom and not self._click_radio(dlg, nhom, index=index):
            errors.append("could not select nhóm loại hình")

        ma_loai = ui.parse_code_label(tk.ma_loai_hinh)
        hq = ui.parse_code_label(tk.co_quan_hai_quan)
        ma_nuoc = ui.parse_code_label(nk.ma_nuoc) or ui.parse_code_label(nk.ten_nuoc)

        qty, qty_unit = ui.split_qty_unit(vd.so_luong_kien)
        gross, gross_unit = ui.split_qty_unit(vd.tong_trong_luong_hang_gross)
        kho_ma = ui.parse_code_label(
            vd.dia_diem_luu_kho_hang_cho_thong_quan_du_kien or vd.dia_diem_luu_kho_ma
        )
        dich_ma = ui.parse_code_label(
            vd.dia_diem_nhan_hang_cuoi_cung or vd.dia_diem_nhan_hang_ma
        )
        xep_ma = ui.parse_code_label(vd.dia_diem_xep_hang or vd.dia_diem_xep_hang_ma)
        if not xep_ma and kho_ma:
            xep_ma = kho_ma

        # Người nhập khẩu (exact IDs)
        if not ui.as_text(nk.ten):
            errors.append("missing người nhập khẩu")
        elif not ui.set_edit_by_strategies(dlg, auto_ids=["txtDV_DT"], labels=["Tên"], value=nk.ten, index=index):
            errors.append("could not write người nhập khẩu")
        if nk.dia_chi:
            ui.set_edit_by_strategies(dlg, auto_ids=["txtDIA_CHI_DT1"], labels=["Địa chỉ"], value=nk.dia_chi, index=index)
        if ma_nuoc:
            ui.set_edit_by_strategies(dlg, auto_ids=["txtNUOC_XK", "cboSNUOC"], labels=["Mã nước"], value=ma_nuoc, index=index)

        dich_bao_thue = (
            ui.parse_code_label(vc.dia_diem_dich_cho_van_chuyen_bao_thue)
            or vc.dia_diem_dich_cho_van_chuyen_bao_thue_ma
        )
        if not dich_bao_thue and vc.ngay_khoi_hanh_van_chuyen:
            dich_bao_thue = dich_ma or kho_ma

        tri_gia_tinh_thue = inv.tri_gia_tinh_thue or inv.tong_tri_gia_hoa_don
        ma_dong_tien_tgtt = inv.ma_dong_tien_tri_gia_tinh_thue or inv.ma_dong_tien_cua_hoa_don

        fields: list[tuple[list[str], list[str], str]] = [
            (["txtMA_LH", "cboSLHINHMD"], ["Mã loại hình"], ma_loai),
            (["txtMA_HQ", "cboSHAIQUAN"], ["Cơ quan Hải quan"], hq),
            (["cboNHOM_HO_SO"], ["Mã bộ phận xử lý tờ khai"], tk.ma_bo_phan_xu_ly_to_khai),
            (["cboMA_HIEU_PTVC"], ["Mã hiệu phương thức vận chuyển"], tk.ma_hieu_phuong_thuc_van_chuyen),
            (["txtNGAY_DK"], ["Ngày khai báo", "Ngày khai báo (dự kiến)"], tk.ngay_khai_bao_du_kien),
            (["txtVAN_DON"], ["Số vận đơn"], vd.so_van_don),
            (["txtSO_KIEN"], ["Số lượng kiện"], qty),
            (["cboDVT_KIEN"], ["Đơn vị kiện"], qty_unit),
            (["txtTR_LUONG"], ["Tổng trọng lượng hàng", "Tổng trọng lượng hàng (Gross)"], gross),
            (["cboDVT_TR_LUONG"], ["Đơn vị trọng lượng"], gross_unit),
            (["txtMA_DD_LUU_KHO", "cboMA_DD_LUU_KHO"], ["Địa điểm lưu kho", "Địa điểm lưu kho hàng chờ thông quan dự kiến"], kho_ma),
            (["txtCANGNN", "cboCANGNN"], ["Địa điểm nhận hàng cuối cùng"], dich_ma),
            (["txtMA_CK", "cboSCUAKHAU"], ["Địa điểm xếp hàng"], xep_ma),
            (
                ["txtTEN_PTVT"],
                ["Phương tiện vận chuyển dự kiến", "Phương tiện vận chuyển"],
                vd.phuong_tien_van_chuyen_du_kien or vd.phuong_tien_van_chuyen_ten,
            ),
            (["txtNGAYDEN"], ["Ngày hàng đi dự kiến"], vd.ngay_hang_di_du_kien),
            (["txtKY_HIEU_SO_HIEU"], ["Ký hiệu và số hiệu"], vd.ky_hieu_va_so_hieu),
            (["txtSO_HD"], ["Số hợp đồng"], hd.so_hop_dong),
            (["txtNGAY_HD"], ["Ngày hợp đồng"], hd.ngay_hop_dong),
            (["txtNGAY_HHHD"], ["Ngày hết hạn"], hd.ngay_het_han),
            (["cboMA_HDTM"], ["Phân loại hình thức hóa đơn"], inv.phan_loai_hinh_thuc_hoa_don),
            (["txtSO_HDTM"], ["Số hóa đơn"], inv.so_hoa_don),
            (["txtNGAY_HDTM"], ["Ngày phát hành"], inv.ngay_phat_hanh),
            (["cboSPTTT"], ["Phương thức thanh toán"], inv.phuong_thuc_thanh_toan),
            (["cboMA_PL_GIA_HDTM"], ["Mã phân loại giá hóa đơn"], inv.ma_phan_loai_gia_hoa_don),
            (["cboSDKGH"], ["Điều kiện giá hóa đơn"], inv.dieu_kien_gia_hoa_don),
            (["txtTONGTGKB"], ["Tổng trị giá hóa đơn"], inv.tong_tri_gia_hoa_don),
            (["txtTONGTGTT"], ["Trị giá tính thuế"], tri_gia_tinh_thue),
            (["cboMA_NT"], ["Mã đồng tiền của hóa đơn"], inv.ma_dong_tien_cua_hoa_don),
            (["cboMA_NT_TGTT"], ["Mã đồng tiền trị giá tính thuế"], ma_dong_tien_tgtt),
            (["txtNGAYKH"], ["Ngày khởi hành vận chuyển"], vc.ngay_khoi_hanh_van_chuyen),
            (
                ["cboTRUNG_CHUYEN_DIEM_CUOI", "txtMA_TRUNG_CHUYEN_DIEM_CUOI"],
                ["Địa điểm đích cho vận chuyển bảo thuế"],
                dich_bao_thue,
            ),
            (
                ["txtTRUNG_CHUYEN_NGAY_KT"],
                ["Ngày đến / Ngày khởi hành", "Ngày đến"],
                vc.ngay_den_ngay_khoi_hanh_bao_thue,
            ),
            (["txtTRUNG_CHUYEN_GHI_CHU"], ["Phần ghi chú"], other.phan_ghi_chu),
            (["txtSoHSTK"], ["Số quản lý của nội bộ doanh nghiệp"], other.so_quan_ly_cua_noi_bo_doanh_nghiep),
        ]

        for auto_ids, labels, value in fields:
            required_name = _REQUIRED_FIELDS.get(auto_ids[0])
            text = ui.as_text(value)
            if required_name and not text:
                errors.append(f"missing {required_name}")
                continue
            if not text:
                continue
            wrote = ui.set_edit_by_strategies(dlg, auto_ids=auto_ids, labels=labels, value=value, index=index)
            if required_name and not wrote:
                errors.append(f"could not write {required_name}")
            if any("MA_NT" in aid for aid in auto_ids):
                # When changing currency, ECUS asks: "Bạn có muốn sửa tất cả các nguyên tệ của tờ khai không?" -> Click Yes
                time.sleep(0.35)
                self._confirm_yes_dialog()
                ui.send_key("{ENTER}")
                time.sleep(0.2)
                self._reject_blocking_dialog()
                ui.dismiss_dialogs(prefer_ok=True)

        # Dismiss any remaining confirmation prompt on Thông tin chung
        self._confirm_yes_dialog()
        self._reject_blocking_dialog()
        ui.dismiss_dialogs(prefer_ok=True)
        self._reject_blocking_dialog()
        if errors:
            raise EcusFlowError("; ".join(errors), filled=0, warnings=errors)

        logger.info("filled Thông tin chung for job=%s in %.1fs", job.jobId, time.time() - t0)

    def _fill_container(self, dlg, job: EcusJobRequest) -> None:
        t0 = time.time()
        ui.select_tab(dlg, "Thông tin Container")
        time.sleep(0.3)
        tab_tk = next((c for c in dlg.children() if getattr(c.element_info, "automation_id", "") == "TabTK"), None)
        active_pane = None
        if tab_tk:
            active_pane = next((c for c in tab_tk.children() if getattr(c.element_info, "automation_id", "") == "TabThongTinCon"), None)
        index = ui.DialogIndex(active_pane or dlg)

        cont = job.thong_tin_container
        dia_diem = cont.dia_diem_xep_hang_len_xe_cho_hang or {}
        ma1 = ui.as_text(dia_diem.get("ma_1") or "")
        if not ma1:
            ma1 = ui.parse_code_label(
                job.thong_tin_chung.van_don.dia_diem_luu_kho_ma
                or job.thong_tin_chung.van_don.dia_diem_luu_kho_hang_cho_thong_quan_du_kien
            )
        if ma1:
            ui.set_edit_by_strategies(dlg, auto_ids=["txtMA_DD_LUU_KHO", "txtMA_DIEM_XEP_HANG"], labels=["Mã"], value=ma1, index=index)
            ui.dismiss_code_lookup(dlg)

        containers = [
            part.strip()
            for part in re.split(r"[,;\n]+", cont.so_container_1_50 or "")
            if part.strip()
        ]
        for idx, number in enumerate(containers[:50], start=1):
            ui.set_edit_by_strategies(
                dlg,
                auto_ids=[f"txtCONTAINER_NO{idx}"],
                labels=[str(idx)],
                value=number,
                index=index,
            )

        ui.dismiss_code_lookup(dlg)
        logger.info(
            "filled Container (%s numbers) job=%s in %.1fs",
            len(containers),
            job.jobId,
            time.time() - t0,
        )

    def _fill_hang_hoa(self, app, dlg, job: EcusJobRequest) -> int:
        t0 = time.time()
        ui.select_tab(dlg, "Danh sách hàng")
        time.sleep(0.5)
        rows = job.danh_sach_hang.hang_hoa or []
        if not rows:
            raise EcusFlowError("hang_hoa is empty", filled=0, warnings=["hang_hoa is empty"])

        dlg.set_focus()
        ui.dismiss_code_lookup(dlg)

        def _find_them_moi():
            found_gb1 = None
            found_button = None
            for _ in range(8):
                try:
                    tab_tk = next((c for c in dlg.children() if getattr(c.element_info, "automation_id", "") == "TabTK"), None)
                    tab_hang = None
                    if tab_tk:
                        tab_hang = next((c for c in tab_tk.children() if getattr(c.element_info, "automation_id", "") == "TabHang"), None)
                    if tab_hang:
                        found_gb1 = next((c for c in tab_hang.children() if getattr(c.element_info, "automation_id", "") == "GroupBox1"), None)
                        if found_gb1:
                            ms2 = next((c for c in found_gb1.children() if getattr(c.element_info, "automation_id", "") == "MenuStrip2"), None)
                            if ms2:
                                found_button = next((m for m in ms2.children() if any(k in m.window_text().lower() for k in ("thêm", "mới", "them", "moi"))), None)
                                if found_button is not None:
                                    return found_button, found_gb1
                except Exception:
                    pass
                time.sleep(0.25)
            return found_button, found_gb1

        them_moi, gb1 = _find_them_moi()
        if them_moi is None:
            ui.dismiss_code_lookup(dlg)
            ui.select_tab(dlg, "Danh sách hàng")
            time.sleep(0.4)
            them_moi, gb1 = _find_them_moi()

        modal_win = None
        modal_idx = None
        filled = 0
        for row_i, row in enumerate(rows, start=1):
            if row_i == 1:
                try:
                    dlg.set_focus()
                except Exception:
                    pass
                if them_moi:
                    try:
                        them_moi.click_input()
                    except Exception:
                        pass
                modal_win, _, app_modal = ui.find_top_window_by_title(
                    re.compile(r"hàng\s*tờ\s*khai|frmVNACCS_HANGEDA", re.IGNORECASE),
                    timeout=2.0,
                )
                if modal_win is None:
                    try:
                        dlg.set_focus()
                        # Click on grid inside gb1 to ensure focus for F4
                        grd = next((c for c in gb1.children() if getattr(c.element_info, "automation_id", "") == "grdData"), None)
                        if grd:
                            grd.click_input()
                    except Exception:
                        pass
                    ui.send_key("{F4}")
                    modal_win, _, app_modal = ui.find_top_window_by_title(
                        re.compile(r"hàng\s*tờ\s*khai|frmVNACCS_HANGEDA", re.IGNORECASE),
                        timeout=4.0,
                    )
            else:
                if modal_idx is not None:
                    btn_them = modal_idx.find(auto_id="btnThem")
                    if btn_them:
                        try:
                            btn_them.click_input()
                            time.sleep(0.3)
                        except Exception:
                            pass
                if modal_win is None:
                    modal_win, _, app_modal = ui.find_top_window_by_title(
                        re.compile(r"hàng\s*tờ\s*khai|frmVNACCS_HANGEDA", re.IGNORECASE),
                        timeout=3.0,
                    )

            if modal_win is None:
                raise EcusFlowError(
                    f"Modal Hàng tờ khai xuất not found for row {row_i}",
                    filled=filled,
                    warnings=[f"modal not found for row {row_i}"],
                )

            modal_idx = ui.DialogIndex(modal_win)

            # Fill row fields
            ma_hang = ui.as_text(row.ma_hang)
            ten_hang = ui.as_text(row.ten_hang)
            ma_hs = ui.as_text(row.ma_hs)
            xuat_xu = ui.as_text(row.xuat_xu) or "VN"
            luong1 = ui.as_text(row.so_luong_1)
            dvt1 = ui.as_text(row.don_vi_tinh_1)
            luong2 = ui.as_text(row.so_luong_2)
            dvt2 = ui.as_text(row.don_vi_tinh_2)
            don_gia = ui.as_text(row.don_gia)
            tri_gia = ui.as_text(row.tri_gia_hoa_don)

            if ma_hang:
                modal_idx.set_by_auto_id("txtMA_NPL_SP", ma_hang)
            if ten_hang:
                modal_idx.set_by_auto_id("txtTEN_HANG", ten_hang)
            if ma_hs:
                modal_idx.set_by_auto_id("txtMA_HANGKB", ma_hs)
            if xuat_xu:
                modal_idx.set_by_auto_id("cboSNUOC", xuat_xu)
            if luong1:
                modal_idx.set_by_auto_id("txtLUONG", luong1)
            if dvt1:
                modal_idx.set_by_auto_id("cboMA_DVT", dvt1)
            if luong2:
                modal_idx.set_by_auto_id("txtLUONG2", luong2)
            if dvt2:
                modal_idx.set_by_auto_id("cboMA_DVT2", dvt2)
            if don_gia:
                modal_idx.set_by_auto_id("txtDGIA_HDTM", don_gia)
            if tri_gia:
                modal_idx.set_by_auto_id("txtTRIGIA_HDTM", tri_gia)

            btn_ghi = modal_idx.find(auto_id="btnGhi")
            if not btn_ghi:
                raise EcusFlowError(
                    f"btnGhi not found for hang_hoa row {row_i}",
                    filled=filled,
                    warnings=[f"btnGhi not found for row {row_i}"],
                )
            btn_ghi.click_input()
            time.sleep(0.4)
            self._reject_blocking_dialog(filled=filled)
            ui.send_key("{ENTER}")
            time.sleep(0.2)
            self._reject_blocking_dialog(filled=filled)
            ui.dismiss_dialogs(app_modal or app, prefer_ok=True)
            self._reject_blocking_dialog(filled=filled)
            filled += 1

            if row_i == len(rows):
                btn_dong = modal_idx.find(auto_id="btnDong")
                if btn_dong:
                    btn_dong.click_input()
                    time.sleep(0.3)

        logger.info(
            "filled %s/%s hang_hoa rows job=%s in %.1fs",
            filled,
            len(rows),
            job.jobId,
            time.time() - t0,
        )
        return filled

    def _save(self, app, dlg) -> str:
        # Fast save: click btnGhi directly from dlg.children without re-indexing
        btn_ghi = next((c for c in dlg.children() if getattr(c.element_info, "automation_id", "") == "btnGhi"), None)
        if btn_ghi and hasattr(btn_ghi, "is_enabled") and btn_ghi.is_enabled():
            btn_ghi.click_input()
        else:
            ui.click_button(dlg, "Ghi", timeout=2)

        # Đóng only after the success notice "Đã ghi xong" is on screen.
        self._wait_and_ack_ghi_saved()

        so = ""
        try:
            tab_tk = next((c for c in dlg.children() if getattr(c.element_info, "automation_id", "") == "TabTK"), None)
            if tab_tk:
                for c in tab_tk.children():
                    if getattr(c.element_info, "automation_id", "") == "TabTTTK":
                        for sub in c.children():
                            if getattr(sub.element_info, "automation_id", "") in ("txtSOTK", "txtSoHSTK"):
                                so = ui.as_text(sub.window_text())
                                if so:
                                    break
        except Exception as exc:  # noqa: BLE001
            logger.debug("read so_to_khai failed: %s", exc)

        # Close declaration form to return to home screen
        try:
            btn_dong = next((c for c in dlg.children() if getattr(c.element_info, "automation_id", "") == "btnDong"), None)
            if btn_dong and hasattr(btn_dong, "is_enabled") and btn_dong.is_enabled():
                btn_dong.click_input()
                time.sleep(0.5)
                self._reject_blocking_dialog()
                self._reject_unsaved_dialog()
                time.sleep(0.3)
                ui.dismiss_dialogs(app, prefer_ok=True)
                self._reject_blocking_dialog()
                self._reject_unsaved_dialog()
                logger.info("closed declaration form via btnDong, returned to home screen")
        except EcusFlowError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.debug("click btnDong failed: %s", exc)

        return so

    def _click_dialog_ok(self, win) -> None:
        for c in win.children():
            if getattr(c.element_info, "control_type", "") != "Button":
                continue
            aid = getattr(c.element_info, "automation_id", "") or ""
            txt = (c.window_text() or "").strip().lower()
            if aid in ("cmdOK", "btnOK", "2") or txt == "ok" or txt.startswith("ok"):
                c.click_input()
                logger.info("clicked OK on 'Đã ghi xong'")
                return
        ui.send_key("{ENTER}")
        logger.info("confirmed 'Đã ghi xong' with Enter")

    def _wait_and_ack_ghi_saved(self, timeout: float = 12.0) -> None:
        """Fail the job unless Ghi produced the success modal, then click its OK.

        An error popup or any Thông báo other than the known nguyên-tệ Yes prompt
        stops the wait immediately so the caller can screenshot and close the form.
        The unsaved-changes prompt is not clicked here; recovery clicks No.
        """
        deadline = time.time() + timeout
        while time.time() < deadline:
            kind, win, text = self._inspect_ghi_dialogs()
            if kind == "saved" and win is not None:
                self._click_dialog_ok(win)
                time.sleep(0.3)
                self._reject_blocking_dialog()
                self._reject_unsaved_dialog()
                return
            if kind == "yes_prompt" and win is not None:
                if self._click_dialog_yes(win):
                    logger.info("clicked Yes on known prompt after Ghi")
                time.sleep(0.25)
                continue
            if kind == "keep_date" and win is not None:
                if self._click_dialog_button(win, auto_ids={"cmdNo", "btnNo", "7"}, names={"no", "không"}):
                    logger.info("clicked No on departure-date prompt; waiting for 'Đã ghi xong'")
                    deadline = max(deadline, time.time() + 5.0)
                else:
                    logger.warning("departure-date prompt has no No button")
                time.sleep(0.25)
                continue
            if kind == "error":
                detail = text or "unexpected dialog after Ghi"
                raise EcusFlowError(
                    f"ECUS dialog instead of 'Đã ghi xong': {detail}",
                    warnings=[detail],
                )
            if kind == "unsaved":
                raise EcusFlowError(
                    "ECUS still asks to save after Ghi; data was not stored",
                    warnings=["unsaved changes dialog after Ghi"],
                )
            time.sleep(0.25)
        raise EcusFlowError(
            "Ghi did not show 'Đã ghi xong'",
            warnings=["missing Đã ghi xong dialog"],
        )

    def _inspect_ghi_dialogs(self):
        """Return the highest-priority popup: saved, keep_date, error, unsaved, then yes_prompt."""
        found: dict[str, tuple] = {}
        for win, title, body, blocking in self._iter_notice_windows():
            kind = classify_notice_body(body, blocking=blocking)
            if kind == "pending" or kind in found:
                continue
            snippet = " ".join((body or title).split())[:240]
            found[kind] = (win, snippet)
        for kind in ("saved", "keep_date", "error", "unsaved", "yes_prompt"):
            if kind in found:
                win, snippet = found[kind]
                return kind, win, snippet
        return None, None, ""

    def _iter_notice_windows(self):
        for hwnd, title in ui.enum_top_level_hwnd_titles():
            if not title:
                continue
            if ui.MAIN_TITLE_RE.search(title) or ui.EXPORT_FORM_TITLE_RE.search(title):
                continue
            blocking = ui.is_blocking_dialog_title(title)
            if not blocking and not _NOTICE_TITLE_RE.search(title):
                continue
            try:
                win, _ = ui.uia_window_from_hwnd(hwnd)
                body = self._dialog_body(win)
            except Exception:
                continue
            yield win, title, body, blocking

    def _recover_home_after_failure(self, app, dlg, *, form_opened: bool, message: str, warnings: list[str]):
        """Screenshot is already taken. Close only a form this job opened, then click No."""
        if not form_opened or app is None:
            return message, warnings
        note = None
        try:
            note = self._abandon_open_declaration(app, dlg)
        except Exception:
            logger.exception("could not return ECUS to the home screen")
            return message, list(warnings) + ["could not close declaration after failure"]
        warnings = list(warnings)
        if self._export_form_is_open(app):
            warnings.append("export form still open after close attempt")
        else:
            warnings.append("closed declaration without saving")
        if note and note not in message:
            message = f"{message}: {note}"
        return message, warnings

    def _abandon_open_declaration(self, app, dlg) -> str | None:
        """Click Đóng, then No on the unsaved-changes prompt, until the form is gone."""
        notes: list[str] = []
        note = self._dismiss_error_dialogs_ok()
        if note:
            notes.append(note)
        deadline = time.time() + 8.0
        while time.time() < deadline and self._export_form_is_open(app):
            if self._click_unsaved_no():
                time.sleep(0.4)
                continue
            self._click_form_dong(app, dlg)
            if self._wait_and_click_unsaved_no(app, timeout=2.0):
                time.sleep(0.35)
                continue
            extra = self._dismiss_error_dialogs_ok()
            if extra:
                notes.append(extra)
            time.sleep(0.25)
        if self._export_form_is_open(app):
            logger.warning("export form still open after Đóng + No")
        else:
            logger.info("closed declaration via Đóng + No, returned to home screen")
        joined = "; ".join(dict.fromkeys(notes))
        return joined or None

    def _dismiss_error_dialogs_ok(self) -> str | None:
        """Read a covering error popup and click OK. Never clicks Yes or No."""
        texts: list[str] = []
        for win, title, body, blocking in list(self._iter_notice_windows()):
            if classify_notice_body(body, blocking=blocking) != "error":
                continue
            snippet = " ".join((body or title).split())[:240]
            if self._click_dialog_button(win, auto_ids={"cmdOK", "btnOK", "2"}, names={"ok"}):
                logger.info("dismissed error dialog via OK: %s", snippet[:120])
            else:
                logger.info("error dialog has no OK button: %s", snippet[:120])
            texts.append(snippet)
            time.sleep(0.2)
        return "; ".join(texts) if texts else None

    def _wait_and_click_unsaved_no(self, app, timeout: float = 2.0) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self._click_unsaved_no():
                return True
            if not self._export_form_is_open(app):
                return False
            time.sleep(0.2)
        return False

    def _click_unsaved_no(self) -> bool:
        for win, _title, body, blocking in self._iter_notice_windows():
            if classify_notice_body(body, blocking=blocking) != "unsaved":
                continue
            if self._click_dialog_button(win, auto_ids={"cmdNo", "btnNo", "7"}, names={"no", "không"}):
                logger.info("clicked No on unsaved-changes prompt")
                return True
            logger.warning("unsaved-changes prompt is visible but No was not found")
            return False
        return False

    def _click_form_dong(self, app, dlg) -> bool:
        targets = []
        if dlg is not None:
            targets.append(dlg)
        try:
            main = ui.ensure_main_window(app, timeout=2.0)
            found = ui.find_export_form(app, timeout=0.6, main=main)
            if found is not None and all(found is not item for item in targets):
                targets.append(found)
        except Exception:
            pass
        for target in targets:
            try:
                btn = next(
                    (
                        c
                        for c in target.children()
                        if getattr(c.element_info, "automation_id", "") == "btnDong"
                    ),
                    None,
                )
                if btn is None:
                    continue
                if hasattr(btn, "is_enabled") and not btn.is_enabled():
                    continue
                btn.click_input()
                logger.info("clicked Đóng on export form")
                return True
            except Exception as exc:  # noqa: BLE001
                logger.debug("click btnDong failed: %s", exc)
        return False

    def _export_form_is_open(self, app) -> bool:
        try:
            main = ui.ensure_main_window(app, timeout=2.0)
        except Exception:
            main = None
        try:
            existing = ui.find_export_form(app, timeout=0.4, main=main)
        except Exception:
            return False
        if existing is None:
            return False
        if getattr(existing.element_info, "automation_id", "") == "frmMain":
            return False
        try:
            return bool(ui.form_has_input_fields(existing))
        except Exception:
            return False

    def _click_dialog_yes(self, win) -> bool:
        return self._click_dialog_button(win, auto_ids={"cmdYes", "btnYes"}, names={"yes", "có"})

    def _click_dialog_button(self, win, *, auto_ids: set[str], names: set[str]) -> bool:
        for btn in self._dialog_buttons(win):
            aid = getattr(btn.element_info, "automation_id", "") or ""
            txt = (btn.window_text() or "").strip().lower()
            if aid in auto_ids or txt in names or any(txt.startswith(name + " ") for name in names):
                btn.click_input()
                return True
        return False

    def _dialog_buttons(self, win):
        found = []
        try:
            for child in win.children():
                if getattr(child.element_info, "control_type", "") == "Button":
                    found.append(child)
        except Exception:
            found = []
        if found:
            return found
        try:
            return list(win.descendants(control_type="Button"))
        except Exception:
            return []

    def _reject_blocking_dialog(self, *, filled: int | None = None) -> None:
        title = ui.find_blocking_dialog()
        if not title:
            return
        raise EcusFlowError(
            f"ECUS validation dialog: {title}",
            filled=filled,
            warnings=[f"validation dialog: {title}"],
        )

    def _dialog_body(self, win) -> str:
        txt = win.window_text() or ""
        try:
            for sub in win.descendants(control_type="Text"):
                txt += " " + (sub.window_text() or "")
        except Exception:
            pass
        return txt

    def _find_unsaved_prompt(self) -> str | None:
        for _hwnd, title in ui.enum_top_level_hwnd_titles():
            if not title or "thông báo" not in title.lower():
                continue
            try:
                win, _ = ui.uia_window_from_hwnd(_hwnd)
                body = self._dialog_body(win).lower()
            except Exception:
                continue
            if "thay đổi" in body or "thay doi" in body:
                return body[:240]
        return None

    def _reject_unsaved_dialog(self) -> None:
        prompt = self._find_unsaved_prompt()
        if not prompt:
            return
        raise EcusFlowError(
            "ECUS still asks to save after Ghi; data was not stored",
            warnings=["unsaved changes dialog after Ghi"],
        )

    def _confirm_yes_dialog(self) -> bool:
        """Explicitly clicks 'Yes' on prompts like 'Bạn có muốn sửa tất cả các nguyên tệ của tờ khai không?'"""
        for hwnd, title in ui.enum_top_level_hwnd_titles():
            if not title or not re.search(r"thông\s*báo|warning|confirm|xác\s*nhận", title, re.I):
                continue
            if ui.is_blocking_dialog_title(title):
                continue
            try:
                win, _ = ui.uia_window_from_hwnd(hwnd)
                body = self._dialog_body(win)
                if _UNSAVED_RE.search(body) or _KEEP_DEPARTURE_DATE_RE.search(body):
                    continue
                for c in win.children():
                    if c.element_info.control_type == "Button":
                        aid = getattr(c.element_info, "automation_id", "")
                        txt = (c.window_text() or "").strip().lower()
                        if aid in ("cmdYes", "btnYes") or txt in ("yes", "có"):
                            c.click_input()
                            logger.info("clicked Yes on prompt '%s' (%s)", title, aid)
                            return True
            except Exception:
                pass
        return False

    # -------------------------------------------------------------- helpers
    def _click_radio(self, dlg, title: str, index: ui.DialogIndex | None = None) -> bool:
        title_lower = title.lower()
        auto_id = None
        if any(k in title_lower for k in ("kinh doanh", "đầu tư", "kd")):
            auto_id = "optKD"
        elif any(k in title_lower for k in ("sản xuất", "sxxk")):
            auto_id = "optSXXK"
        elif any(k in title_lower for k in ("gia công", "gc")):
            auto_id = "optGC"
        elif any(k in title_lower for k in ("chế xuất", "cx")):
            auto_id = "optCX"

        if auto_id and index and auto_id in index.by_auto_id:
            try:
                index.by_auto_id[auto_id].click_input()
                logger.info("clicked radio by auto_id %s (%s)", auto_id, title)
                return True
            except Exception as exc:
                logger.debug("radio by auto_id failed: %s", exc)

        try:
            radio = ui.find_control(
                dlg,
                title_re=f".*{re.escape(title)}.*",
                control_type="RadioButton",
                timeout=1.0,
            )
            radio.click_input()
            logger.info("clicked radio: %s", title)
            return True
        except Exception as exc:  # noqa: BLE001
            logger.debug("radio '%s' not clicked: %s", title, exc)
            return False

    def _set_adjacent_unit(
        self,
        dlg,
        label: str,
        unit: str,
        index: ui.DialogIndex | None = None,
    ) -> None:
        """Best-effort: find combo/edit near qty label for unit code."""
        if not unit:
            return
        if ui.set_edit_by_strategies(dlg, labels=[label, "Đơn vị"], value=unit, index=index):
            return
        if index is None:
            return
        label_ctrl = index.find(title_re=f".*{re.escape(label)}.*", control_type="Text")
        if label_ctrl is None:
            return
        try:
            lx, ly, _, _ = ui._rect(label_ctrl)  # noqa: SLF001
            near = []
            for ctrl in index.combos + index.edits:
                try:
                    cx, cy, _, _ = ui._rect(ctrl)  # noqa: SLF001
                    if abs(cy - ly) <= 40 and cx >= lx:
                        near.append((cx, ctrl))
                except Exception:
                    continue
            near.sort(key=lambda t: t[0])
            target = near[1][1] if len(near) >= 2 else (near[0][1] if near else None)
            if target is not None:
                ui._write_control(target, unit)  # noqa: SLF001
        except Exception as exc:  # noqa: BLE001
            logger.debug("unit set failed for %s: %s", label, exc)

    def _maybe_screenshot(self, job_id: str) -> str | None:
        if not self.settings.screenshot_on_error:
            return None
        try:
            from PIL import ImageGrab  # type: ignore

            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            safe_id = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in job_id)[:64]
            path = Path(self.settings.screenshot_dir) / f"{stamp}_{safe_id}.png"
            ImageGrab.grab().save(path)
            logger.info("[pywinauto] screenshot saved %s", path)
            return str(path)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[pywinauto] screenshot failed: %s", exc)
            return None

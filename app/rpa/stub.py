from __future__ import annotations

import logging
import time

from app.config import Settings
from app.models import EcusJobRequest
from app.rpa import RpaResult

logger = logging.getLogger(__name__)


class StubEcusRpa:
    """Dry-run backend: validates payload shape and pretends ECUS save succeeded."""

    name = "stub"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def register_declaration(self, job: EcusJobRequest) -> RpaResult:
        hang_hoa = job.danh_sach_hang.hang_hoa
        to_khai = job.thong_tin_chung.thong_tin_to_khai
        nk = job.thong_tin_chung.nguoi_nhap_khau
        hd = job.thong_tin_chung.hop_dong
        vd = job.thong_tin_chung.van_don

        logger.info(
            "[stub] job=%s index=%s/%s action=%s request_ids=%s hang_hoa=%s",
            job.jobId,
            job.jobIndex,
            job.jobTotal,
            job.action,
            job.request_ids,
            len(hang_hoa),
        )
        logger.info(
            "[stub] loai_hinh=%s hq=%s nk=%s hop_dong=%s van_don=%s",
            to_khai.ma_loai_hinh,
            to_khai.co_quan_hai_quan,
            nk.ten or nk.ma,
            hd.so_hop_dong,
            vd.so_van_don,
        )
        for row in hang_hoa[:5]:
            logger.info(
                "[stub] hang stt=%s ma_hs=%s ten=%s sl1=%s",
                row.stt,
                row.ma_hs,
                (row.ten_hang or "")[:80],
                row.so_luong_1,
            )
        if len(hang_hoa) > 5:
            logger.info("[stub] ... +%s more hang_hoa rows", len(hang_hoa) - 5)

        requested = len(hang_hoa)
        if not hang_hoa:
            return RpaResult(
                success=False,
                message="Stub rejected job: danh_sach_hang.hang_hoa is empty",
                hang_hoa_requested=0,
                hang_hoa_filled=0,
                warnings=["hang_hoa is empty"],
            )

        time.sleep(max(0.0, float(self.settings.stub_delay_sec)))
        fake_so = to_khai.so_to_khai or f"STUB-{job.jobId[-8:]}"
        return RpaResult(
            success=True,
            message=f"Stub OK — would open ECUS Đăng ký tờ khai and save ({requested} lines)",
            so_to_khai=fake_so,
            hang_hoa_requested=requested,
            hang_hoa_filled=requested,
        )

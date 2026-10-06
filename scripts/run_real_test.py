import json
import logging
import sys
import time
from pathlib import Path

# Ensure UTF-8 output
sys.stdout.reconfigure(encoding="utf-8")

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("ECUS-TestRunner")

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root))

from app.config import Settings
from app.models import EcusJobRequest
from app.rpa import ui_helpers as ui
from app.rpa.ecus_pywinauto import PywinautoEcusRpa
from PIL import ImageGrab

def capture_screen(save_path: Path):
    try:
        ui.ensure_default_desktop()
        ImageGrab.grab().save(str(save_path))
        logger.info("Saved screen capture: %s", save_path.name)
    except Exception as e:
        logger.warning("Screen capture failed: %s", e)

def main():
    sample_path = root / "samples" / "job.real_test.json"
    if not sample_path.exists():
        sample_path = root / "samples" / "job.sample.json"

    with open(sample_path, encoding="utf-8") as f:
        job_data = json.load(f)

    job = EcusJobRequest.model_validate(job_data)

    print("\n" + "=" * 65)
    print(f"🚀 BẮT ĐẦU CHẠY TEST THỰC TẾ ECUS RPA: Job [{job.jobId}]")
    print(f"📦 Loại hình: {job.thong_tin_chung.thong_tin_to_khai.ma_loai_hinh}")
    print(f"🏢 Chi cục HQ: {job.thong_tin_chung.thong_tin_to_khai.co_quan_hai_quan}")
    print(f"📋 Số lượng dòng hàng: {len(job.danh_sach_hang.hang_hoa or [])} mặt hàng")
    print("=" * 65 + "\n")

    settings = Settings()
    rpa = PywinautoEcusRpa(settings)

    scratch_dir = Path("C:/Users/HenryLong/.gemini/antigravity-ide/brain/45b58142-caf3-4d41-9689-256fd603199c/scratch")
    scratch_dir.mkdir(parents=True, exist_ok=True)

    capture_screen(scratch_dir / "real_test_before.png")

    t_start = time.time()
    result = rpa.register_declaration(job)
    t_total = time.time() - t_start

    print("\n" + "=" * 65)
    print("🏁 KẾT QUẢ THỰC THI KIỂM TRA")
    print("=" * 65)
    print(f"⏱️  Tổng thời gian: {t_total:.2f} giây")
    print(f"✅ Thành công: {result.success}")
    print(f"💬 Thông điệp: {result.message}")
    if result.so_to_khai:
        print(f"🔢 Số tờ khai: {result.so_to_khai}")

    # Chụp ảnh xác thực màn hình ECUS sau khi hoàn tất và đóng form về trang chủ
    try:
        ui.ensure_default_desktop()
        capture_screen(scratch_dir / "real_test_after_saved.png")
    except Exception as e:
        logger.warning("Could not capture post-test screen: %s", e)

    print("=" * 65 + "\n")

if __name__ == "__main__":
    main()

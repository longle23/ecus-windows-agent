import json
import logging
import sys
import time
from pathlib import Path

# Ensure UTF-8 output
sys.stdout.reconfigure(encoding="utf-8")

# Configure logging to console
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root))

from app.config import Settings
from app.models import EcusJobRequest
from app.rpa import ui_helpers as ui
from app.rpa.ecus_pywinauto import PywinautoEcusRpa

def main():
    root = Path(__file__).resolve().parents[1]
    sample_path = root / "samples" / "job.sample.json"
    with open(sample_path, encoding="utf-8") as f:
        job_data = json.load(f)

    job = EcusJobRequest.model_validate(job_data)
    print("=" * 60)
    print(f"STARTING LIVE RPA TEST: jobId={job.jobId} action={job.action}")
    print("=" * 60)

    settings = Settings()
    rpa = PywinautoEcusRpa(settings)

    t_start = time.time()
    result = rpa.register_declaration(job)
    t_end = time.time()

    print("=" * 60)
    print(f"RPA EXECUTION COMPLETED in {t_end - t_start:.2f}s")
    print(f"Success: {result.success}")
    print(f"Message: {result.message}")
    print(f"So to khai: {result.so_to_khai}")
    if result.screenshot:
        print(f"Screenshot on error: {result.screenshot}")
    print("=" * 60)

    # Take a confirmation screenshot
    try:
        from PIL import ImageGrab
        confirm_path = "C:/Users/HenryLong/.gemini/antigravity-ide/brain/45b58142-caf3-4d41-9689-256fd603199c/scratch/live_test_result.png"
        ui.ensure_default_desktop()
        ImageGrab.grab().save(confirm_path)
        print(f"Confirmation screenshot saved: {confirm_path}")
    except Exception as e:
        print(f"Failed to grab confirmation screenshot: {e}")

if __name__ == "__main__":
    main()

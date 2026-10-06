from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(ROOT_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    host: str = "0.0.0.0"
    port: int = 8787
    api_key: str = Field(default="CHANGE_ME", alias="API_KEY")

    rpa_backend: Literal["stub", "pywinauto"] = Field(default="stub", alias="RPA_BACKEND")
    ecus_exe_path: str = Field(default=r"C:\ECUSDATA\ECUS5VNACCS.exe", alias="ECUS_EXE_PATH")
    ecus_process_name: str = Field(default="ECUS5VNACCS", alias="ECUS_PROCESS_NAME")

    job_lock_timeout_sec: int = Field(default=30, alias="JOB_LOCK_TIMEOUT_SEC")
    job_queue_max: int = Field(default=200, alias="JOB_QUEUE_MAX")
    job_timeout_sec: int = Field(default=540, alias="JOB_TIMEOUT_SEC")
    stub_delay_sec: float = Field(default=1.0, alias="STUB_DELAY_SEC")

    screenshot_on_error: bool = Field(default=True, alias="SCREENSHOT_ON_ERROR")
    screenshot_dir: Path = Field(default=ROOT_DIR / "screenshots", alias="SCREENSHOT_DIR")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")
    log_dir: Path = Field(default=ROOT_DIR / "logs", alias="LOG_DIR")


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.log_dir.mkdir(parents=True, exist_ok=True)
    settings.screenshot_dir.mkdir(parents=True, exist_ok=True)
    return settings

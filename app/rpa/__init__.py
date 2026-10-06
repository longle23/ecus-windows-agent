from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from app.models import EcusJobRequest


@dataclass
class RpaResult:
    success: bool
    message: str
    so_to_khai: str = ""
    screenshot: str | None = None
    hang_hoa_requested: int = 0
    hang_hoa_filled: int = 0
    warnings: list[str] = field(default_factory=list)


class EcusRpaBackend(Protocol):
    name: str

    def register_declaration(self, job: EcusJobRequest) -> RpaResult:
        """Open ECUS → Đăng ký tờ khai → fill tabs → Save."""
        ...

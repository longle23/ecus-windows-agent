from __future__ import annotations

from app.config import Settings
from app.rpa import EcusRpaBackend
from app.rpa.ecus_pywinauto import PywinautoEcusRpa
from app.rpa.stub import StubEcusRpa


def build_rpa_backend(settings: Settings) -> EcusRpaBackend:
    if settings.rpa_backend == "pywinauto":
        return PywinautoEcusRpa(settings)
    return StubEcusRpa(settings)

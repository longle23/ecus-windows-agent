"""Long-lived UI thread so UI Automation is not unloaded between jobs.

Each /jobs call used to spawn a ThreadPoolExecutor thread and then shut the
pool down. That thread initialized COM, loaded UIAutomationCore.dll, and on
exit COM uninitialized and unloaded the DLL. The next job called into the
unloaded DLL and python.exe died with access violation 0xc0000005
(module uiautomationcore.dll_unloaded). n8n then saw ECONNRESET and the agent
was gone.

This thread stays alive for the whole process and initializes COM once, as an
STA apartment, which UI Automation requires.
"""

from __future__ import annotations

import ctypes
import logging
import queue
import threading
from typing import Callable, TypeVar

logger = logging.getLogger(__name__)

_COINIT_APARTMENTTHREADED = 0x2
_S_OK = 0
_S_FALSE = 1

T = TypeVar("T")


class _Call:
    def __init__(self, fn: Callable[[], T]) -> None:
        self.fn = fn
        self.done = threading.Event()
        self.result: T | None = None
        self.error: BaseException | None = None


class StaWorker:
    def __init__(self) -> None:
        self._queue: queue.Queue[_Call | None] = queue.Queue()
        self._thread = threading.Thread(target=self._loop, name="ecus-sta", daemon=True)
        self._started = False
        self._start_lock = threading.Lock()

    def start(self) -> None:
        with self._start_lock:
            if self._started:
                return
            self._thread.start()
            self._started = True

    def run(self, fn: Callable[[], T], timeout_sec: float) -> T:
        self.start()
        call: _Call = _Call(fn)
        self._queue.put(call)
        if not call.done.wait(timeout_sec):
            raise TimeoutError(f"RPA timed out after {timeout_sec}s")
        if call.error is not None:
            raise call.error
        return call.result  # type: ignore[return-value]

    def _loop(self) -> None:
        _init_sta()
        logger.info("ECUS UI thread ready (STA, kept alive across jobs)")
        while True:
            call = self._queue.get()
            if call is None:
                return
            try:
                call.result = call.fn()
            except BaseException as exc:  # noqa: BLE001
                call.error = exc
            finally:
                call.done.set()


def _init_sta() -> None:
    try:
        hr = ctypes.windll.ole32.CoInitializeEx(None, _COINIT_APARTMENTTHREADED)
    except Exception as exc:  # noqa: BLE001
        logger.warning("CoInitializeEx failed: %s", exc)
        return
    hr &= 0xFFFFFFFF
    if hr in (_S_OK, _S_FALSE):
        return
    logger.warning("CoInitializeEx returned 0x%08X", hr)


sta_worker = StaWorker()


def run_on_sta_thread(fn: Callable[[], T], timeout_sec: float) -> T:
    return sta_worker.run(fn, timeout_sec)

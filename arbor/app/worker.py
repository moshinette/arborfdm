"""Background computations (QThread + QObject worker) with progress and cancellation.

The UI thread never runs the engine: TaskRunner.start(fn, kwargs) runs
``fn(progress=Progress(...), **kwargs)`` in a QThread. Progress callbacks are turned into
Qt signals (queued to the UI thread); cancel() sets the Progress cancel_event, the engine
raises Cancelled at its next check and the runner emits ``cancelled``.
"""
from __future__ import annotations

import threading
import time
import traceback
from typing import Any, Callable, Optional

from PySide6 import QtCore

from arbor.engine.types import Cancelled, Progress


class TaskWorker(QtCore.QObject):
    progress = QtCore.Signal(float, str)
    finished = QtCore.Signal(object)
    failed = QtCore.Signal(str, str)
    cancelled = QtCore.Signal()

    def __init__(self, fn: Callable[..., Any], kwargs: dict, cancel_event: threading.Event):
        super().__init__()
        self.fn, self.kwargs, self.cancel_event = fn, kwargs, cancel_event
        self._last_emit = 0.0
        self._last_msg = ""

    def _on_progress(self, fraction: float, message: str) -> None:
        now = time.monotonic()
        # throttle: the engine may report thousands of steps
        if message != self._last_msg or now - self._last_emit > 0.05 or fraction >= 1.0:
            self._last_emit, self._last_msg = now, message
            self.progress.emit(float(fraction), message)

    @QtCore.Slot()
    def run(self) -> None:
        progress = Progress(self._on_progress, self.cancel_event)
        try:
            result = self.fn(progress=progress, **self.kwargs)
        except Cancelled:
            self.cancelled.emit()
            return
        except Exception as exc:  # reported to the UI, never swallowed
            self.failed.emit(f"{type(exc).__name__}: {exc}", traceback.format_exc())
            return
        if self.cancel_event.is_set():
            self.cancelled.emit()
            return
        self.finished.emit(result)


class TaskRunner(QtCore.QObject):
    """Runs one task at a time. Signals are delivered in the UI thread."""
    started = QtCore.Signal(str)
    progress = QtCore.Signal(float, str)
    finished = QtCore.Signal(object, object)          # result, context
    failed = QtCore.Signal(str, str, object)          # message, traceback, context
    cancelled = QtCore.Signal(object)                 # context
    running_changed = QtCore.Signal(bool)

    def __init__(self, parent: Optional[QtCore.QObject] = None):
        super().__init__(parent)
        self._thread: Optional[QtCore.QThread] = None
        self._worker: Optional[TaskWorker] = None
        self._cancel_event: Optional[threading.Event] = None
        self._context: Any = None
        self.label = ""
        self.started_at = 0.0
        self.elapsed = 0.0

    def is_running(self) -> bool:
        return self._thread is not None

    def start(self, fn: Callable[..., Any], kwargs: Optional[dict] = None, context: Any = None,
              label: str = "") -> bool:
        if self.is_running():
            return False
        self._cancel_event = threading.Event()
        self._context, self.label = context, label
        thread = QtCore.QThread()
        worker = TaskWorker(fn, kwargs or {}, self._cancel_event)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.progress.connect(self._on_progress)
        worker.finished.connect(self._on_finished)
        worker.failed.connect(self._on_failed)
        worker.cancelled.connect(self._on_cancelled)
        self._thread, self._worker = thread, worker
        self.started_at = time.perf_counter()
        thread.start()
        self.started.emit(label)
        self.running_changed.emit(True)
        return True

    def cancel(self) -> None:
        if self._cancel_event is not None:
            self._cancel_event.set()

    def is_cancelling(self) -> bool:
        return bool(self._cancel_event and self._cancel_event.is_set())

    def wait(self, ms: int = 30000) -> bool:
        """Block until the thread ends (used when closing the application)."""
        if self._thread is None:
            return True
        ok = self._thread.wait(ms)
        QtCore.QCoreApplication.processEvents()
        return ok

    def _cleanup(self) -> Any:
        self.elapsed = time.perf_counter() - self.started_at
        ctx = self._context
        thread, worker = self._thread, self._worker
        self._thread = self._worker = None
        self._cancel_event = None
        if thread is not None:
            thread.quit()
            thread.wait(5000)
            thread.deleteLater()
        if worker is not None:
            worker.deleteLater()
        self.running_changed.emit(False)
        return ctx

    @QtCore.Slot(float, str)
    def _on_progress(self, fraction: float, message: str) -> None:
        if self._thread is not None:
            self.progress.emit(fraction, message)

    @QtCore.Slot(object)
    def _on_finished(self, result: Any) -> None:
        self.finished.emit(result, self._cleanup())

    @QtCore.Slot(str, str)
    def _on_failed(self, message: str, tb: str) -> None:
        self.failed.emit(message, tb, self._cleanup())

    @QtCore.Slot()
    def _on_cancelled(self) -> None:
        self.cancelled.emit(self._cleanup())


def run_generation(progress: Progress, **kwargs) -> Any:
    """Task function: the only place where the UI calls the support engine."""
    from arbor.engine import pipeline
    return pipeline.generate(progress=progress, **kwargs)

"""机群调度器。

保存待投产队列与最近若干条完成上报，提供入队、排产、完成上报与有序回放
四件事。入队前统一计算"队列 + 在机模具"的在制数量，超上限直接拒绝；
上报序号在调度器内统一分配，排序始终走可复现的键；控制台与顺序自查都从
这里取数。
"""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING

from ..config import BatchLimits
from ..eventbus import Event, EventBus
from ..timer.curve import CurveBook
from .capacity import CapacitySnapshot, capacity_snapshot, require_wip_capacity
from .ordering import order_completions
from .profile import BatchProfile
from .queue import DispatchQueue, QueueEntry
from .report import CompletionReport, report_completion
from .state import SchedulerPhase, SchedulerStateMachine

if TYPE_CHECKING:  # pragma: no cover - 仅用于类型标注
    from ..press.fleet import PressFleet

_REPORT_MEMORY = 512


class BatchScheduler:
    """机群调度器。"""

    def __init__(
        self,
        bus: EventBus,
        limits: BatchLimits,
        curves: CurveBook,
        fleet: "PressFleet",
    ) -> None:
        self.limits = limits
        self.bus = bus
        self.curves = curves
        self.fleet = fleet
        self.queue = DispatchQueue(limits.max_queue)
        self.lock = threading.RLock()
        self._state = SchedulerStateMachine()
        self._reports: list[CompletionReport] = []
        self._report_sequence = 0

    @property
    def phase(self) -> SchedulerPhase:
        return self._state.phase

    def transition(self, target: SchedulerPhase) -> SchedulerPhase:
        return self._state.transition(target)

    def enqueue(self, profile: BatchProfile, enqueued_at: float, priority: int = 50) -> QueueEntry:
        """把一份配方入队；在制配额满时直接拒绝。"""

        with self.lock:
            require_wip_capacity(self.capacity(), profile.mold_id)
            return self.queue.push(
                profile.mold_id,
                profile.product_code,
                profile.thickness_mm,
                enqueued_at,
                priority=priority,
            )

    def capacity(self) -> CapacitySnapshot:
        """返回当前在制配额与机位余量。"""

        with self.lock:
            return capacity_snapshot(self.fleet, self.queue, self.limits.max_wip)

    def capacity_snapshot(self) -> dict:
        """返回可序列化的容量快照。"""

        return self.capacity().as_dict()

    def record_report(
        self,
        press_id: str,
        mold_id: str,
        product_code: str,
        completed_at: float,
        curve_generation: int,
    ) -> CompletionReport:
        """登记一条完成上报（统一分配上报序号）。"""

        with self.lock:
            self._report_sequence += 1
            sequence = self._report_sequence
            report = report_completion(
                press_id,
                mold_id,
                product_code,
                completed_at,
                curve_generation,
                sequence,
                self.bus,
            )
            self._reports.append(report)
            if len(self._reports) > _REPORT_MEMORY:
                del self._reports[: len(self._reports) - _REPORT_MEMORY]
            return report

    def ordered_reports(self, limit: int = 20, window_s: float | None = None) -> list[dict]:
        """按可复现顺序返回最近的上报。"""

        with self.lock:
            reports = list(self._reports[-limit:])
        window = self.limits.dispatch_window_s if window_s is None else window_s
        return [report.as_dict() for report in order_completions(reports, window)]

    def recent_reports(self, limit: int = 20) -> list[CompletionReport]:
        """返回最近的上报（保持原始记录，供排序与审计使用）。"""

        with self.lock:
            return list(self._reports[-limit:])

    def drain(self, drained_at: float) -> dict:
        """把队列清空并进入排空相位。"""

        with self.lock:
            self.transition(SchedulerPhase.DRAINED)
            removed = 0
            while self.queue.pop_next() is not None:
                removed += 1
        self.bus.publish(
            Event.of("batch", "batch", "queue_drained", subject="dispatch-queue", removed=removed)
        )
        return {"removed": removed, "phase": self._state.phase.value, "at": round(drained_at, 3)}

    def snapshot(self) -> dict:
        with self.lock:
            return {
                "phase": self._state.phase.value,
                "history": self._state.history(),
                "capacity": self.capacity().as_dict(),
                "queue": self.queue.snapshot(),
                "report_sequence": self._report_sequence,
                "recent_reports": [report.as_dict() for report in self._reports[-10:]],
            }

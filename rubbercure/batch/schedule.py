"""排产推进。

从队列取模次、挑空闲机位、登记分配，是调度器的核心一步。排产只在派工
相位执行；返回值区分三种结果：已分配、队列为空、有任务但机位占满。整个
动作在调度器锁内完成，避免两个线程同时把同一台机分配两次。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from ..eventbus import EventBus
from ..mold.registry import MoldRegistry
from .assign import Assignment, assign_next
from .capacity import (
    REASON_NO_PRESS,
    STATUS_ASSIGNED,
    STATUS_NO_PRESS,
    STATUS_NO_TASK,
    available_press_ids,
    capacity_snapshot,
)
from .queue import DispatchQueue
from .state import SchedulerPhase

if TYPE_CHECKING:  # pragma: no cover - 仅用于类型标注
    from ..press.fleet import PressFleet
    from .batch import BatchScheduler


@dataclass(frozen=True)
class ScheduleResult:
    """一次排产推进的结果。"""

    status: str
    reason: str | None
    assignment: Assignment | None
    queued: int
    available_presses: int
    capacity: dict

    def as_dict(self) -> dict:
        return {
            "status": self.status,
            "reason": self.reason,
            "assigned": self.assignment.as_dict() if self.assignment else None,
            "queued": self.queued,
            "available_presses": self.available_presses,
            "capacity": self.capacity,
        }


def schedule_next(
    scheduler: "BatchScheduler",
    fleet: "PressFleet",
    queue: DispatchQueue,
    registry: MoldRegistry,
    bus: EventBus,
    assigned_at: float,
) -> ScheduleResult:
    """推进一次排产，返回分配结果或未分配原因。"""

    with scheduler.lock:
        scheduler.transition(SchedulerPhase.DISPATCHING)
        queued = len(queue)
        assignment = assign_next(fleet, queue, registry, bus, assigned_at)
        available_after = len(available_press_ids(fleet))
        if assignment is not None:
            status = STATUS_ASSIGNED
            reason = None
        elif queued == 0:
            status = STATUS_NO_TASK
            reason = None
            if scheduler.phase is not SchedulerPhase.DRAINED:
                scheduler.transition(SchedulerPhase.DRAINED)
        else:
            status = STATUS_NO_PRESS
            reason = REASON_NO_PRESS
        snapshot = capacity_snapshot(fleet, queue, scheduler.limits.max_wip)
        return ScheduleResult(
            status=status,
            reason=reason,
            assignment=assignment,
            queued=len(queue),
            available_presses=available_after,
            capacity=snapshot.as_dict(),
        )

"""机群调度与完成上报。"""

from .assign import assign_next
from .batch import BatchScheduler
from .capacity import (
    REASON_NO_PRESS,
    REASON_WIP_LIMIT,
    STATUS_ASSIGNED,
    STATUS_NO_PRESS,
    STATUS_NO_TASK,
    CapacitySnapshot,
    active_press_ids,
    available_press_ids,
    capacity_snapshot,
    require_wip_capacity,
)
from .ordering import order_completions
from .profile import BatchProfile
from .queue import DispatchQueue, QueueEntry
from .report import CompletionReport, report_completion
from .schedule import ScheduleResult, schedule_next
from .state import SchedulerPhase, SchedulerStateMachine

__all__ = [
    "assign_next",
    "BatchScheduler",
    "REASON_NO_PRESS",
    "REASON_WIP_LIMIT",
    "STATUS_ASSIGNED",
    "STATUS_NO_PRESS",
    "STATUS_NO_TASK",
    "CapacitySnapshot",
    "active_press_ids",
    "available_press_ids",
    "capacity_snapshot",
    "require_wip_capacity",
    "order_completions",
    "BatchProfile",
    "DispatchQueue",
    "QueueEntry",
    "CompletionReport",
    "report_completion",
    "ScheduleResult",
    "schedule_next",
    "SchedulerPhase",
    "SchedulerStateMachine",
]

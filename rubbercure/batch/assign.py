"""模次分配。

调度把队首任务分配给一台空闲硫化机：装模、记录分配、发布事件。分配要求
目标机当前没有在机模具——同一台机在完成上报之前不能被再次分配，否则两个
模次会挤在同一台机上。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from ..errors import NoIdlePressError
from ..eventbus import Event, EventBus
from ..mold.registry import MoldRegistry
from .queue import DispatchQueue

if TYPE_CHECKING:  # pragma: no cover - 仅用于类型标注
    from ..press.fleet import PressFleet


REASON_NO_TASK = "no_task"
REASON_NO_IDLE_PRESS = "no_idle_press"


@dataclass(frozen=True)
class Assignment:
    """一次模次分配。"""

    press_id: str
    mold_id: str
    product_code: str
    thickness_mm: float
    assigned_at: float

    def as_dict(self) -> dict:
        return {
            "press_id": self.press_id,
            "mold_id": self.mold_id,
            "product_code": self.product_code,
            "thickness_mm": round(self.thickness_mm, 3),
            "assigned_at": round(self.assigned_at, 3),
        }


@dataclass(frozen=True)
class DispatchResult:
    """一次派工尝试的结果，用原因区分队列空和机位满。"""

    assignment: Assignment | None
    reason: str | None

    @property
    def assigned(self) -> bool:
        return self.assignment is not None

    def as_dict(self) -> dict:
        return {
            "assigned": self.assignment.as_dict() if self.assignment else None,
            "reason": self.reason,
        }


def assign_next(
    fleet: "PressFleet",
    queue: DispatchQueue,
    registry: MoldRegistry,
    bus: EventBus,
    assigned_at: float,
) -> DispatchResult:
    """把队首任务分配给一台空闲机位。"""

    with fleet.lock:
        idle = fleet.available_presses()
        if not idle:
            if len(queue) > 0:
                raise NoIdlePressError(f"全部 {fleet.size} 个机位均被占用", component="batch")
            return DispatchResult(None, REASON_NO_TASK)
        entry = queue.pop_next()
        if entry is None:
            return DispatchResult(None, REASON_NO_TASK)
        registry.require_active(entry.mold_id)
        target = idle[0]
        target.load_mold(entry.mold_id)
        assignment = Assignment(
            press_id=target.press_id,
            mold_id=entry.mold_id,
            product_code=entry.product_code,
            thickness_mm=entry.thickness_mm,
            assigned_at=assigned_at,
        )
    bus.publish(
        Event.of(
            "batch",
            "batch",
            "mold_assigned",
            subject=entry.mold_id,
            severity="info",
            **assignment.as_dict(),
        )
    )
    return DispatchResult(assignment, None)

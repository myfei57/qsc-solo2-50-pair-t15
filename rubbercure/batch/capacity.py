"""车间在制容量。

在制数量同时包含"已经挂在硫化机上的模具"和"已入队等待机位的模具"。它和
调度队列容量是两道闸门：队列容量防止待办无限积压，在制上限防止车间同时挂
太多模具。机位数量则来自实际机群，满机位时不能再把任务从队列挂上去。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from ..errors import CapacityError
from .queue import DispatchQueue

REASON_WIP_LIMIT = "wip_limit_reached"
REASON_NO_PRESS = "no_idle_press"

STATUS_ASSIGNED = "assigned"
STATUS_NO_TASK = "queue_empty"
STATUS_NO_PRESS = "press_capacity_reached"

if TYPE_CHECKING:  # pragma: no cover - 仅用于类型标注
    from ..press.fleet import PressFleet


@dataclass(frozen=True)
class CapacitySnapshot:
    """某一时刻的在制配额与机位占用。"""

    queued: int
    active: int
    wip: int
    wip_limit: int
    queue_capacity: int
    total_presses: int
    available_presses: int

    @property
    def wip_available(self) -> int:
        return max(0, self.wip_limit - self.wip)

    @property
    def quota_full(self) -> bool:
        return self.wip >= self.wip_limit

    @property
    def presses_full(self) -> bool:
        return self.available_presses == 0

    def as_dict(self) -> dict:
        return {
            "queued": self.queued,
            "active": self.active,
            "wip": self.wip,
            "wip_limit": self.wip_limit,
            "wip_available": self.wip_available,
            "quota_full": self.quota_full,
            "queue_capacity": self.queue_capacity,
            "total_presses": self.total_presses,
            "available_presses": self.available_presses,
            "presses_full": self.presses_full,
        }


def available_press_ids(fleet: "PressFleet") -> list[str]:
    """返回相位空闲且没有挂模的机位。"""

    return [unit.press_id for unit in fleet.idle_presses() if unit.mold_id is None]


def active_press_ids(fleet: "PressFleet") -> list[str]:
    """返回已经挂模、正在占用机群容量的机位。"""

    return [unit.press_id for unit in fleet.all() if unit.mold_id is not None]


def capacity_snapshot(
    fleet: "PressFleet",
    queue: DispatchQueue,
    wip_limit: int,
) -> CapacitySnapshot:
    """汇总队列、在机模具、配额与机位余量。"""

    available = available_press_ids(fleet)
    active = active_press_ids(fleet)
    queued = len(queue)
    return CapacitySnapshot(
        queued=queued,
        active=len(active),
        wip=queued + len(active),
        wip_limit=int(wip_limit),
        queue_capacity=queue.capacity,
        total_presses=fleet.size,
        available_presses=len(available),
    )


def require_wip_capacity(snapshot: CapacitySnapshot, mold_id: str) -> None:
    """入队前检查在制配额；满额时拒绝，不允许先挤进队列。"""

    if snapshot.quota_full:
        raise CapacityError(
            f"在制配额已满（{snapshot.wip}/{snapshot.wip_limit}），模具 {mold_id} 拒绝入队",
            reason=REASON_WIP_LIMIT,
            used=snapshot.wip,
            limit=snapshot.wip_limit,
            available=snapshot.wip_available,
            queued=snapshot.queued,
            active=snapshot.active,
            mold_id=mold_id,
        )

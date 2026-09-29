"""在制上限：配额满拒绝入队，机位满保留任务并给出不同原因。"""

from __future__ import annotations

from dataclasses import replace

import pytest

from rubbercure.app import build_platform
from rubbercure.batch.capacity import (
    REASON_NO_PRESS,
    REASON_WIP_LIMIT,
    STATUS_ASSIGNED,
    STATUS_NO_PRESS,
)
from rubbercure.batch.schedule import schedule_next
from rubbercure.config import BatchLimits, ConfigError, PlatformConfig, default_config
from rubbercure.console.api import ConsoleAPI
from rubbercure.errors import CapacityError


def capacity_config(
    max_wip: int,
    max_queue: int | None = None,
    fleet_size: int = 3,
) -> PlatformConfig:
    base = default_config()
    return replace(
        base,
        press=replace(base.press, fleet_size=fleet_size),
        batch=replace(
            base.batch,
            max_wip=max_wip,
            max_queue=max_queue if max_queue is not None else max_wip,
        ),
    )


def register_and_enqueue(platform, mold_id: str) -> dict:
    platform.register_mold(mold_id, cavity_count=2)
    return platform.enqueue_mold(mold_id)


def dispatch(platform):
    return schedule_next(
        platform.scheduler,
        platform.fleet,
        platform.scheduler.queue,
        platform.registry,
        platform.bus,
        platform.clock.now(),
    ).as_dict()


def test_wip_quota_rejects_new_mold_when_limit_reached(tmp_path, clock):
    platform = build_platform(
        capacity_config(max_wip=4), clock=clock, storage_root=str(tmp_path / "state")
    )

    for index in range(4):
        register_and_enqueue(platform, f"mold-{index}")

    capacity = platform.scheduler.capacity_snapshot()
    assert capacity["wip"] == 4
    assert capacity["quota_full"] is True
    assert capacity["wip_available"] == 0

    with pytest.raises(CapacityError) as excinfo:
        register_and_enqueue(platform, "mold-over")
    error = excinfo.value
    assert error.reason == REASON_WIP_LIMIT
    assert error.used == 4
    assert error.limit == 4
    assert "在制配额已满" in error.message
    assert len(platform.scheduler.queue) == 4


def test_active_molds_count_toward_wip_quota(tmp_path, clock):
    platform = build_platform(
        capacity_config(max_wip=3), clock=clock, storage_root=str(tmp_path / "state")
    )

    for index in range(3):
        register_and_enqueue(platform, f"mold-{index}")
    first = dispatch(platform)
    assert first["status"] == STATUS_ASSIGNED

    capacity = platform.scheduler.capacity_snapshot()
    assert capacity["queued"] == 2
    assert capacity["active"] == 1
    assert capacity["wip"] == 3
    assert capacity["wip_available"] == 0

    with pytest.raises(CapacityError) as excinfo:
        register_and_enqueue(platform, "mold-over")
    assert excinfo.value.reason == REASON_WIP_LIMIT


def test_dispatch_keeps_queued_task_and_reports_no_idle_press(tmp_path, clock):
    platform = build_platform(
        capacity_config(max_wip=6), clock=clock, storage_root=str(tmp_path / "state")
    )

    for index in range(5):
        register_and_enqueue(platform, f"mold-{index}")

    results = [dispatch(platform) for _ in range(3)]
    assert [result["status"] for result in results] == [
        STATUS_ASSIGNED,
        STATUS_ASSIGNED,
        STATUS_ASSIGNED,
    ]

    blocked = dispatch(platform)
    assert blocked["status"] == STATUS_NO_PRESS
    assert blocked["reason"] == REASON_NO_PRESS
    assert blocked["assigned"] is None
    assert blocked["queued"] == 2
    assert [entry["mold_id"] for entry in platform.scheduler.queue.entries()] == [
        "mold-3",
        "mold-4",
    ]
    assert blocked["available_presses"] == 0
    assert blocked["capacity"]["presses_full"] is True
    assert blocked["capacity"]["quota_full"] is False


def test_wip_capacity_returns_after_press_is_opened(tmp_path, clock):
    platform = build_platform(
        capacity_config(max_wip=1, fleet_size=1), clock=clock, storage_root=str(tmp_path / "state")
    )
    register_and_enqueue(platform, "mold-1")
    assert dispatch(platform)["status"] == STATUS_ASSIGNED

    with pytest.raises(CapacityError):
        register_and_enqueue(platform, "mold-2")

    press = platform.press("press-01")
    press.clear_mold()
    press.idle()

    register_and_enqueue(platform, "mold-2")
    assert platform.scheduler.capacity_snapshot()["wip"] == 1


def test_console_api_distinguishes_quota_rejection_from_press_shortage(tmp_path, clock):
    quota_platform = build_platform(
        capacity_config(max_wip=3), clock=clock, storage_root=str(tmp_path / "quota")
    )
    quota_api = ConsoleAPI(quota_platform)
    for index in range(3):
        mold_id = f"quota-{index}"
        quota_platform.register_mold(mold_id, cavity_count=2)
        assert quota_api.enqueue({"mold_id": mold_id})["ok"] is True

    quota_platform.register_mold("mold-over", cavity_count=2)
    rejected = quota_api.enqueue({"mold_id": "mold-over"})
    assert rejected["ok"] is False
    assert rejected["error"]["reason"] == REASON_WIP_LIMIT
    assert rejected["error"]["used"] == 3
    assert rejected["error"]["limit"] == 3

    platform = build_platform(
        capacity_config(max_wip=6), clock=clock, storage_root=str(tmp_path / "press")
    )
    api = ConsoleAPI(platform)
    for index in range(5):
        mold_id = f"mold-{index}"
        platform.register_mold(mold_id, cavity_count=2)
        assert api.enqueue({"mold_id": mold_id})["ok"] is True

    for _ in range(3):
        dispatch(platform)
    blocked = api.dispatch({"press_id": "press-01"})
    assert blocked["ok"] is True
    assert blocked["report"]["dispatch_status"] == STATUS_NO_PRESS
    assert blocked["report"]["block_reason"] == REASON_NO_PRESS
    assert blocked["report"]["capacity"]["presses_full"] is True
    assert blocked["report"]["capacity"]["quota_full"] is False


def test_config_rejects_queue_capacity_above_wip_limit():
    with pytest.raises(ConfigError):
        BatchLimits.from_mapping(
            {"dispatch_window_s": 180.0, "max_queue": 3, "max_wip": 2}
        )

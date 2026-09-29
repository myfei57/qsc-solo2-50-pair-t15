"""并发写入方与机群容量约束都必须可复现。"""

from __future__ import annotations

import threading
from dataclasses import replace

import pytest

from rubbercure.app import build_platform
from rubbercure.config import default_config
from rubbercure.batch.assign import assign_next
from rubbercure.batch.ordering import order_completions, ordering_keys
from rubbercure.batch.report import CompletionReport
from rubbercure.errors import (
    NoIdlePressError,
    QueueCapacityError,
    WipLimitError,
)
from rubbercure.steam.setpoint import SetpointArbiter


def _platform_with_wip(tmp_path, clock, *, max_wip: int, max_queue: int = 12):
    config = replace(
        default_config(),
        batch=replace(default_config().batch, max_wip=max_wip, max_queue=max_queue),
    )
    return build_platform(config, clock=clock, storage_root=str(tmp_path / "state"))


def test_setpoint_arbiter_commits_a_single_complete_request():
    arbiter = SetpointArbiter(min_kpa=80.0, max_kpa=760.0, default_kpa=640.0)
    submitted: list[float] = []
    lock = threading.Lock()

    def writer(name: str, target: float, priority: int) -> None:
        for _ in range(200):
            arbiter.request(name, target, priority)
            outcome = arbiter.resolve()
            with lock:
                submitted.append(outcome.target_kpa)

    threads = [
        threading.Thread(target=writer, args=("temp_loop", 658.0, 70)),
        threading.Thread(target=writer, args=("header_loop", 628.0, 60)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert set(round(value, 3) for value in submitted) <= {628.0, 658.0}
    final = arbiter.resolve()
    assert final.target_kpa in (628.0, 658.0)
    assert final.winner in ("temp_loop", "header_loop", "idle")
    assert arbiter.committed_kpa() in (628.0, 658.0)
    assert arbiter.decisions(5)


def test_order_completions_is_independent_of_input_order():
    reports = [
        CompletionReport("press-03", "m3", "RB-215", 10.0, 1, 3),
        CompletionReport("press-01", "m1", "RB-215", 10.0, 1, 1),
        CompletionReport("press-02", "m2", "RB-215", 10.0, 1, 2),
        CompletionReport("press-01", "m4", "RB-215", 9.0, 1, 0),
    ]
    forward = ordering_keys(reports)
    backward = ordering_keys(list(reversed(reports)))
    assert forward == backward
    assert [item[1] for item in forward] == ["press-01", "press-01", "press-02", "press-03"]
    assert order_completions(reports)[0].mold_id == "m4"


def test_order_completions_window_filters_stale_reports():
    reports = [
        CompletionReport("press-01", "m1", "RB-215", 1.0, 1, 1),
        CompletionReport("press-02", "m2", "RB-215", 100.0, 1, 2),
        CompletionReport("press-03", "m3", "RB-215", 101.0, 1, 3),
    ]
    windowed = order_completions(reports, window_s=5.0)
    assert [item.mold_id for item in windowed] == ["m2", "m3"]


def test_wip_quota_counts_loaded_and_queued_molds(tmp_path, clock):
    platform = _platform_with_wip(tmp_path, clock, max_wip=2)
    for mold_id in ("wip-1", "wip-2", "wip-3"):
        platform.register_mold(mold_id, cavity_count=2)

    platform.enqueue_mold("wip-1")
    platform.scheduler.queue.pop_next()
    platform.press("press-01").load_mold("wip-1")
    platform.enqueue_mold("wip-2")
    capacity = platform.scheduler.capacity_snapshot()
    assert capacity == {
        "fleet_size": 3,
        "loaded": 1,
        "queued": 1,
        "wip": 2,
        "max_wip": 2,
        "idle_presses": ["press-02", "press-03"],
        "available_presses": 2,
        "available_wip_slots": 0,
    }

    with pytest.raises(WipLimitError) as excinfo:
        platform.enqueue_mold("wip-3")
    assert "在制模具已达上限" in str(excinfo.value)
    assert excinfo.value.component == "batch"


def test_no_idle_press_is_distinct_from_wip_quota_full(tmp_path, clock):
    platform = _platform_with_wip(tmp_path, clock, max_wip=4)
    for index in range(1, 5):
        mold_id = f"cap-{index}"
        platform.register_mold(mold_id, cavity_count=2)
        if index <= 3:
            platform.press(f"press-{index:02d}").load_mold(mold_id)
        platform.enqueue_mold(mold_id)

    capacity = platform.scheduler.capacity_snapshot()
    assert capacity["wip"] == 4
    assert capacity["available_presses"] == 0
    with pytest.raises(NoIdlePressError):
        assign_next(
            platform.fleet,
            platform.scheduler.queue,
            platform.registry,
            platform.bus,
            clock.now(),
        )


def test_empty_queue_and_full_presses_do_not_enter_drained_phase(tmp_path, clock):
    platform = _platform_with_wip(tmp_path, clock, max_wip=3)
    for index, unit in enumerate(platform.fleet.all(), start=1):
        platform.register_mold(f"loaded-{index}", cavity_count=2)
        unit.load_mold(f"loaded-{index}")

    report = platform.run_dispatch("press-01")
    assert report["assigned"] is None
    assert report["reason"] == "no_idle_press"
    assert report["capacity"]["available_presses"] == 0
    assert report["scheduler_phase"] == "dispatching"


def test_queue_capacity_error_remains_distinct_from_wip(tmp_path, clock):
    platform = _platform_with_wip(tmp_path, clock, max_wip=10, max_queue=1)
    for mold_id in ("q-1", "q-2"):
        platform.register_mold(mold_id, cavity_count=2)

    platform.enqueue_mold("q-1")
    with pytest.raises(QueueCapacityError):
        platform.enqueue_mold("q-2")


def test_concurrent_enqueue_never_exceeds_wip_quota(platform):
    for index in range(20):
        platform.register_mold(f"race-wip-{index}", cavity_count=2)

    accepted: list[str] = []
    rejected: list[str] = []
    start = threading.Barrier(20)

    def enqueue(index: int) -> None:
        mold_id = f"race-wip-{index}"
        start.wait()
        try:
            platform.enqueue_mold(mold_id)
        except WipLimitError:
            rejected.append(mold_id)
        else:
            accepted.append(mold_id)

    threads = [threading.Thread(target=enqueue, args=(index,)) for index in range(20)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    capacity = platform.scheduler.capacity_snapshot()
    assert len(accepted) == platform.config.batch.max_wip
    assert len(rejected) == 20 - platform.config.batch.max_wip
    assert capacity["queued"] == platform.config.batch.max_wip
    assert capacity["wip"] == platform.config.batch.max_wip
    assert capacity["available_wip_slots"] == 0


def test_platform_concurrency_probes_are_stable(platform):
    setpoint = platform.concurrency_setpoint_probe(rounds=16)
    assert setpoint["rounds"] == 16
    assert setpoint["distinct_targets"] <= 2
    assert setpoint["committed_kpa"] > 0

    reports = platform.concurrency_report_probe(rounds=6)
    assert reports["stable"] is True
    assert reports["reports"] == 6 * platform.fleet.size


def test_concurrency_probe_rejects_non_positive_rounds(platform):
    with pytest.raises(ValueError):
        platform.concurrency_setpoint_probe(rounds=0)
    with pytest.raises(ValueError):
        platform.concurrency_report_probe(rounds=0)

from datetime import UTC, datetime, timedelta
from typing import get_args

import pytest

from tado_monitor.dashboard import (
    CARRY_FORWARD,
    RANGES,
    Range,
    RangeKey,
    align,
    build,
    dew_point,
    grid,
    heating_estimate,
    query_since,
)

NOW = datetime(2026, 10, 4, 12, 7, 30, tzinfo=UTC)
MINUTE = timedelta(minutes=1)


def test_range_keys_match_the_api_literal() -> None:
    assert set(get_args(RangeKey)) == set(RANGES)


@pytest.mark.parametrize("key", list(RANGES))
def test_every_range_gives_a_sensible_point_count(key: str) -> None:
    times = grid(NOW, RANGES[key])
    assert 100 <= len(times) <= 400
    assert times[-1] <= NOW < times[-1] + RANGES[key].bucket


def test_grid_is_aligned_to_bucket_boundaries() -> None:
    times = grid(NOW, Range(timedelta(hours=1), timedelta(minutes=10), ""))
    assert times[0] == datetime(2026, 10, 4, 11, 0, tzinfo=UTC)
    assert times[-1] == datetime(2026, 10, 4, 12, 0, tzinfo=UTC)
    assert len(times) == 7


def test_align_carries_values_forward_then_leaves_a_gap() -> None:
    times = [NOW + i * MINUTE for i in range(15)]
    rows = [(times[0], "Office", "temperature", 21.0)]
    series = align(rows, times, MINUTE)["temperature"]["Office"]
    assert series[: CARRY_FORWARD // MINUTE + 1] == [21.0] * 11
    assert series[11:] == [None] * 4


def test_align_seeds_from_a_reading_just_before_the_grid() -> None:
    times = [NOW + i * MINUTE for i in range(5)]
    rows = [
        (NOW - 3 * MINUTE, "Office", "humidity", 50.0),
        (NOW - 8 * MINUTE, "Office", "humidity", 40.0),  # older, ignored
    ]
    assert align(rows, times, MINUTE)["humidity"]["Office"] == [50.0] * 5
    assert query_since(times) == NOW - CARRY_FORWARD


def test_align_drops_devices_with_nothing_in_range() -> None:
    times = [NOW + i * MINUTE for i in range(3)]
    rows = [(NOW - timedelta(hours=1), "Gone", "temperature", 20.0)]
    assert align(rows, times, MINUTE)["temperature"] == {}


def test_dew_point() -> None:
    dp = dew_point(20.0, 50.0)
    assert dp is not None and dp == pytest.approx(9.26, abs=0.05)
    dp = dew_point(24.0, 78.0)  # roughly the readings on day one
    assert dp is not None and dp == pytest.approx(19.9, abs=0.1)
    assert dew_point(20.0, 100.0) == pytest.approx(20.0, abs=0.01)
    assert dew_point(20.0, 0.0) is None


def test_heating_estimate_is_clamped_at_zero() -> None:
    assert heating_estimate(21.0, 19.5) == 1.5
    assert heating_estimate(18.0, 23.8) == 0.0


def test_build_payload() -> None:
    r = RANGES["3h"]
    t = grid(NOW, r)[-1]
    rows = [
        (t, "Office", "temperature", 20.0),
        (t, "Office", "humidity", 50.0),
        (t, "Office", "setpoint", 21.0),
        (t, "Salon", "temperature", 22.0),
    ]
    payload = build(rows, ["Salon", "Office", "Kids room"], NOW, "3h")
    assert payload["rooms"] == ["Kids room", "Office", "Salon"]
    assert payload["bucket_s"] == 60
    assert len(payload["x"]) == len(grid(NOW, r))
    charts = payload["charts"]
    assert [s["name"] for s in charts["temperature"]] == ["Office", "Salon"]
    # Dew point and heating need both inputs: Salon has neither humidity
    # nor a setpoint.
    assert [s["name"] for s in charts["dew_point"]] == ["Office"]
    assert charts["dew_point"][0]["values"][-1] == pytest.approx(9.26, abs=0.05)
    assert charts["heating_demand"] == [
        {"name": "Office", "values": [None] * (len(payload["x"]) - 1) + [1.0]}
    ]

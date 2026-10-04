"""Shape bucketed readings into chart series. Pure, no I/O -- web.py does SQL.

Each range has a fixed bucket size (~150-350 points per line). The database
averages readings per bucket; this module lays them on a complete time grid
so every room's line shares one x axis, then derives dew point and an
estimated heating demand.
"""

import math
from collections import defaultdict
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

RangeKey = Literal["3h", "6h", "24h", "3d", "7d", "1m", "3m", "12m"]
type Series = list[float | None]
# (bucket start, device name, metric, average value) -- one row per bucket.
type Row = tuple[datetime, str, str, float]


@dataclass(frozen=True)
class Range:
    span: timedelta
    bucket: timedelta
    label: str


RANGES: dict[str, Range] = {
    "3h": Range(timedelta(hours=3), timedelta(minutes=1), "Last 3 hours"),
    "6h": Range(timedelta(hours=6), timedelta(minutes=2), "Last 6 hours"),
    "24h": Range(timedelta(hours=24), timedelta(minutes=10), "Last 24 hours"),
    "3d": Range(timedelta(days=3), timedelta(minutes=30), "Last 3 days"),
    "7d": Range(timedelta(days=7), timedelta(hours=1), "Last 7 days"),
    "1m": Range(timedelta(days=30), timedelta(hours=4), "Last month"),
    "3m": Range(timedelta(days=90), timedelta(hours=12), "Last 3 months"),
    "12m": Range(timedelta(days=365), timedelta(days=1), "Last 12 months"),
}

# Buckets are aligned to a fixed origin so the same bucket always covers the
# same wall-clock span, whichever minute the page is loaded.
ORIGIN = datetime(2000, 1, 1, tzinfo=UTC)

# Sensors report on change, and the collector re-saves every value every
# 5 minutes. So an empty bucket shortly after a reading still has a known
# value: carry it forward this long, and show a real gap after that.
CARRY_FORWARD = timedelta(minutes=10)

# What the charts are built from. Matter's PIHeatingDemand would be the real
# heating demand, but no tado X device exposes it (checked: the Thermostat
# cluster's AttributeList is LocalTemperature, limits, setpoint and modes).
METRICS = ("temperature", "humidity", "setpoint")


def bucket_floor(t: datetime, bucket: timedelta) -> datetime:
    return ORIGIN + ((t - ORIGIN) // bucket) * bucket


def grid(now: datetime, r: Range) -> list[datetime]:
    """Bucket starts covering ``r.span`` up to and including now's bucket."""
    start = bucket_floor(now - r.span, r.bucket)
    count = (bucket_floor(now, r.bucket) - start) // r.bucket + 1
    return [start + i * r.bucket for i in range(count)]


def query_since(times: list[datetime]) -> datetime:
    """Fetch a little before the grid, to seed values carried into it."""
    return times[0] - CARRY_FORWARD


def align(
    rows: Iterable[Row], times: list[datetime], bucket: timedelta
) -> dict[str, dict[str, Series]]:
    """metric -> device name -> one value (or None) per grid bucket."""
    index = {t: i for i, t in enumerate(times)}
    out: dict[str, dict[str, Series]] = defaultdict(dict)
    # Latest reading before the grid starts, to carry into its first buckets.
    seeds: dict[tuple[str, str], tuple[datetime, float]] = {}
    for t, name, metric, value in rows:
        series = out[metric].setdefault(name, [None] * len(times))
        if (i := index.get(t)) is not None:
            series[i] = value
        elif t < times[0]:
            seed = seeds.get((metric, name))
            if seed is None or seed[0] < t:
                seeds[metric, name] = (t, value)

    for metric, by_name in out.items():
        for name, series in list(by_name.items()):
            last: float | None = None
            last_t = times[0]
            if (seed := seeds.get((metric, name))) is not None:
                last_t, last = seed
            for i, v in enumerate(series):
                if v is not None:
                    last, last_t = v, times[i]
                elif last is not None and times[i] - last_t <= CARRY_FORWARD:
                    series[i] = last
            if all(v is None for v in series):
                del by_name[name]
    return out


def dew_point(temperature: float, humidity: float) -> float | None:
    """Magnus formula (Sonntag 1990 constants); good to ~0.1 C indoors."""
    if humidity <= 0:
        return None
    a, b = 17.62, 243.12
    gamma = math.log(humidity / 100) + a * temperature / (b + temperature)
    return b * gamma / (a - gamma)


def heating_estimate(setpoint: float, temperature: float) -> float:
    """How far below its setpoint a room is, in C -- 0 once it's reached it.

    tado X exposes no PIHeatingDemand over Matter, so this stands in for it.
    A valve measures at the radiator, so it reads warm while heating and
    this underestimates during a heat-up; the shape is still useful.
    """
    return max(0.0, setpoint - temperature)


def _zip_map(
    left: dict[str, Series],
    right: dict[str, Series],
    fn: Callable[[float, float], float | None],
) -> dict[str, Series]:
    result: dict[str, Series] = {}
    for name in left.keys() & right.keys():
        values = [
            None if a is None or b is None else fn(a, b)
            for a, b in zip(left[name], right[name], strict=True)
        ]
        if any(v is not None for v in values):
            result[name] = values
    return result


def _rounded(by_name: dict[str, Series], digits: int) -> list[dict[str, Any]]:
    return [
        {
            "name": name,
            "values": [None if v is None else round(v, digits) for v in values],
        }
        for name, values in sorted(by_name.items())
    ]


def build(
    rows: Iterable[Row], names: list[str], now: datetime, key: str
) -> dict[str, Any]:
    """The /api/series payload: one shared x axis, four charts of series."""
    r = RANGES[key]
    times = grid(now, r)
    data = align(rows, times, r.bucket)
    temperature = data.get("temperature", {})
    humidity = data.get("humidity", {})

    # Only devices with a setpoint: the collector drops it while a thermostat
    # is Off, which also keeps the always-Off Wireless Temperature Sensor X out.
    heating = _zip_map(data.get("setpoint", {}), temperature, heating_estimate)
    return {
        "range": key,
        "label": r.label,
        "bucket_s": int(r.bucket.total_seconds()),
        # Every device, sorted: colors are assigned from this list, so a room
        # keeps its color on every chart and every range.
        "rooms": sorted(names),
        "x": [int(t.timestamp()) for t in times],
        "charts": {
            "temperature": _rounded(temperature, 2),
            "dew_point": _rounded(_zip_map(temperature, humidity, dew_point), 2),
            "humidity": _rounded(humidity, 1),
            "heating_demand": _rounded(heating, 2),
        },
    }

"""Turn a Matter node's attribute map into room readings. Pure, no I/O.

The Matter server hands us each node as a flat dict keyed by attribute path,
``"endpoint/cluster/attribute"`` -> value, e.g. ``"1/1026/0": 2134`` is
endpoint 1, Temperature Measurement cluster, MeasuredValue = 21.34 C. Matter
sends fixed-point integers, so every source below carries its scale.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

type Attributes = Mapping[str, Any]

TEMPERATURE_MEASUREMENT = 0x0402
RELATIVE_HUMIDITY_MEASUREMENT = 0x0405
THERMOSTAT = 0x0201
BASIC_INFORMATION = 0x0028

# metric -> candidate (cluster, attribute, scale), first non-null wins. A
# dedicated Temperature Measurement cluster beats the Thermostat's
# LocalTemperature, so a device exposing both still yields one temperature.
SOURCES: dict[str, list[tuple[int, int, float]]] = {
    "temperature": [(TEMPERATURE_MEASUREMENT, 0, 0.01), (THERMOSTAT, 0x00, 0.01)],
    "humidity": [(RELATIVE_HUMIDITY_MEASUREMENT, 0, 0.01)],
    "setpoint": [(THERMOSTAT, 0x12, 0.01)],  # OccupiedHeatingSetpoint
    "heating_demand": [(THERMOSTAT, 0x08, 1.0)],  # PIHeatingDemand, percent
}

# Thermostat.SystemMode. 0 = Off: the setpoint is then a leftover number, not
# a target. The Wireless Temperature Sensor X presents itself as a thermostat
# that is always Off, and a valve in a room switched off in tado reads Off.
SYSTEM_MODE = (THERMOSTAT, 0x1C)
SYSTEM_MODE_OFF = 0
HEATING_METRICS = frozenset({"setpoint", "heating_demand"})

_RELEVANT = frozenset(
    {(c, a) for sources in SOURCES.values() for c, a, _ in sources} | {SYSTEM_MODE}
)


def parse_path(path: str) -> tuple[int, int, int] | None:
    """``"1/1026/0"`` -> ``(1, 1026, 0)``; None for anything malformed."""
    parts = path.split("/")
    if len(parts) != 3 or not all(p.isdigit() for p in parts):
        return None
    endpoint, cluster, attribute = (int(p) for p in parts)
    return endpoint, cluster, attribute


def is_relevant(path: str) -> bool:
    """Does an ``attribute_updated`` on this path change any reading?"""
    parsed = parse_path(path)
    return parsed is not None and parsed[1:] in _RELEVANT


def readings(attributes: Attributes) -> dict[str, float]:
    """Current metric values for one node; metrics it doesn't report are absent.

    If several endpoints carry the same cluster, the lowest endpoint wins.
    Null (Matter's "unknown", e.g. a sensor fault) falls through to the next
    source rather than being stored. Heating metrics are dropped while the
    thermostat is Off.
    """
    by_source: dict[tuple[int, int], int | float] = {}
    for parsed, value in sorted(
        (p, v) for path, v in attributes.items() if (p := parse_path(path))
    ):
        _, cluster, attribute = parsed
        if (cluster, attribute) not in _RELEVANT:
            continue
        # bool is an int subclass; no reading we want is ever a bool.
        if isinstance(value, int | float) and not isinstance(value, bool):
            by_source.setdefault((cluster, attribute), value)

    result: dict[str, float] = {}
    for metric, sources in SOURCES.items():
        for cluster, attribute, scale in sources:
            raw = by_source.get((cluster, attribute))
            if raw is not None:
                result[metric] = round(raw * scale, 2)
                break
    if by_source.get(SYSTEM_MODE) == SYSTEM_MODE_OFF:
        for metric in HEATING_METRICS:
            result.pop(metric, None)
    return result


@dataclass(frozen=True)
class DeviceInfo:
    node_id: int
    vendor: str | None
    product: str | None
    serial: str | None
    # NodeLabel: whatever the first commissioner (the tado app) wrote, if any.
    label: str | None


def device_info(node_id: int, attributes: Attributes) -> DeviceInfo:
    def text(attribute: int) -> str | None:
        value = attributes.get(f"0/{BASIC_INFORMATION}/{attribute}")
        if not isinstance(value, str):
            return None
        return value.strip() or None

    return DeviceInfo(
        node_id=node_id,
        vendor=text(0x01),
        product=text(0x03),
        serial=text(0x0F),
        label=text(0x05),
    )


def display_name(info: DeviceInfo, names: Mapping[int, str]) -> str:
    """rooms.toml wins, then the device's own label, then a stable fallback."""
    fallback = f"{info.product or 'node'} #{info.node_id}"
    return names.get(info.node_id) or info.label or fallback

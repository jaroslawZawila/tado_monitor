import json
from pathlib import Path

from tado_monitor.config import load_names
from tado_monitor.matter import (
    device_info,
    display_name,
    is_relevant,
    parse_path,
    readings,
)

FIXTURES = Path(__file__).parent / "fixtures"

# Shaped like a wall thermostat: its own sensors on endpoint 1, plus the
# Thermostat cluster whose LocalTemperature disagrees slightly.
THERMOSTAT = {
    "0/40/1": "tado",
    "0/40/3": "Smart Thermostat X",
    "0/40/5": "",
    "0/40/15": "VA1234567890",
    "1/513/0": 2150,  # LocalTemperature
    "1/513/8": 40,  # PIHeatingDemand
    "1/513/18": 2100,  # OccupiedHeatingSetpoint
    "1/1026/0": 2134,  # TemperatureMeasurement.MeasuredValue
    "1/1029/0": 5520,  # RelativeHumidityMeasurement.MeasuredValue
    "1/6/0": True,  # irrelevant cluster
}

# Radiator valve: only the Thermostat cluster.
VALVE = {
    "0/40/3": "Smart Radiator Thermostat X",
    "1/513/0": 1987,
    "1/513/8": 0,
    "1/513/18": 1700,
}


def test_parse_path() -> None:
    assert parse_path("1/1026/0") == (1, 1026, 0)
    assert parse_path("garbage") is None
    assert parse_path("1/*/0") is None


def test_is_relevant() -> None:
    assert is_relevant("1/1026/0")
    assert is_relevant("2/513/18")
    assert not is_relevant("1/6/0")
    assert not is_relevant("0/40/3")


def test_thermostat_prefers_temperature_measurement_cluster() -> None:
    assert readings(THERMOSTAT) == {
        "temperature": 21.34,
        "humidity": 55.2,
        "setpoint": 21.0,
        "heating_demand": 40.0,
    }


def test_valve_falls_back_to_local_temperature() -> None:
    assert readings(VALVE) == {
        "temperature": 19.87,
        "setpoint": 17.0,
        "heating_demand": 0.0,
    }


def test_null_measurement_falls_through_to_next_source() -> None:
    assert readings({"1/1026/0": None, "1/513/0": 2000}) == {"temperature": 20.0}


def test_lowest_endpoint_wins() -> None:
    assert readings({"2/1026/0": 1500, "1/1026/0": 2000}) == {"temperature": 20.0}


def test_bools_and_strings_are_ignored() -> None:
    assert readings({"1/1026/0": True, "1/1029/0": "55"}) == {}


def test_device_info_and_display_name() -> None:
    info = device_info(7, THERMOSTAT)
    assert info.vendor == "tado"
    assert info.product == "Smart Thermostat X"
    assert info.serial == "VA1234567890"
    assert info.label is None  # empty NodeLabel counts as unset
    assert display_name(info, {}) == "Smart Thermostat X #7"
    assert display_name(info, {7: "Living room"}) == "Living room"


def test_load_names(tmp_path: Path) -> None:
    rooms = tmp_path / "rooms.toml"
    rooms.write_text('[rooms]\n3 = "Bedroom"\n12 = "Office"\n')
    assert load_names(rooms) == {3: "Bedroom", 12: "Office"}
    assert load_names(tmp_path / "missing.toml") == {}


def test_real_radiator_valve_x() -> None:
    # `tado-cli dump 1` of a paired Smart Radiator Thermostat X: temperature
    # only via the Thermostat cluster (endpoint 1), humidity on its own
    # endpoint 2, no PIHeatingDemand.
    node = json.loads((FIXTURES / "radiator_valve_x.json").read_text())
    assert readings(node["attributes"]) == {
        "temperature": 23.82,
        "humidity": 77.69,
        "setpoint": 18.0,
    }
    info = device_info(node["node_id"], node["attributes"])
    assert info.product == "Smart Radiator Thermostat X"
    assert info.vendor == "tado° GmbH"


def test_real_temperature_sensor_x_has_no_setpoint() -> None:
    # The sensor's firmware exposes a Thermostat cluster with SystemMode Off
    # and a meaningless 23.0 setpoint; only temperature and humidity count.
    node = json.loads((FIXTURES / "temperature_sensor_x.json").read_text())
    assert readings(node["attributes"]) == {"temperature": 23.95, "humidity": 79.0}


def test_valve_switched_off_drops_heating_metrics() -> None:
    assert readings({**VALVE, "1/513/28": 0}) == {"temperature": 19.87}
    assert readings({**VALVE, "1/513/28": 4})["setpoint"] == 17.0  # Heat
    assert is_relevant("1/513/28")

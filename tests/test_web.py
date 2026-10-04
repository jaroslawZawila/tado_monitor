from datetime import datetime, timedelta

from fastapi.testclient import TestClient

from tado_monitor.dashboard import Row
from tado_monitor.web import app, get_store


class FakeStore:
    def __init__(self) -> None:
        self.calls: list[tuple[datetime, timedelta]] = []

    async def rows(self, since: datetime, bucket: timedelta) -> list[Row]:
        self.calls.append((since, bucket))
        return [(since, "Office", "temperature", 21.5)]

    async def names(self) -> list[str]:
        return ["Office"]


# No `with`: the lifespan (and so the Postgres pool) never starts.
store = FakeStore()
app.dependency_overrides[get_store] = lambda: store
client = TestClient(app)


def test_series_defaults_to_24h() -> None:
    response = client.get("/api/series")
    assert response.status_code == 200
    body = response.json()
    assert body["range"] == "24h"
    assert store.calls[-1][1] == timedelta(minutes=10)
    assert set(body["charts"]) == {
        "temperature",
        "dew_point",
        "humidity",
        "heating_demand",
    }


def test_series_rejects_unknown_ranges() -> None:
    assert client.get("/api/series", params={"range": "2y"}).status_code == 422


def test_index_and_static_assets_are_served() -> None:
    assert "Home climate" in client.get("/").text
    assert client.get("/static/app.js").status_code == 200
    assert client.get("/static/vendor/uPlot.iife.min.js").status_code == 200

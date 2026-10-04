"""The dashboard: FastAPI serving one page plus a JSON endpoint for its charts.

    uv run uvicorn tado_monitor.web:app --port 8080

A light alternative to Grafana (~40 MB instead of ~370 MB), so it also fits
a 1 GB Pi 3. Reads with the SELECT-only role, like Grafana does.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated, Any, Protocol

from fastapi import Depends, FastAPI, Query, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from psycopg_pool import AsyncConnectionPool

from tado_monitor.config import Settings
from tado_monitor.dashboard import (
    METRICS,
    ORIGIN,
    RANGES,
    RangeKey,
    Row,
    build,
    grid,
    query_since,
)

STATIC = Path(__file__).parent / "static"


class Store(Protocol):
    async def rows(self, since: datetime, bucket: timedelta) -> list[Row]: ...
    async def names(self) -> list[str]: ...


class PostgresStore:
    def __init__(self, pool: AsyncConnectionPool) -> None:
        self.pool = pool

    async def rows(self, since: datetime, bucket: timedelta) -> list[Row]:
        # date_bin with the same origin as dashboard.grid, so bucket starts
        # from SQL match the grid exactly.
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                """
                SELECT date_bin(%(bucket)s, ts, %(origin)s), name, metric, avg(value)
                FROM named_readings
                WHERE ts >= %(since)s AND metric = ANY(%(metrics)s)
                GROUP BY 1, 2, 3
                """,
                {
                    "bucket": bucket,
                    "origin": ORIGIN,
                    "since": since,
                    "metrics": list(METRICS),
                },
            )
            return await cur.fetchall()

    async def names(self) -> list[str]:
        async with self.pool.connection() as conn:
            cur = await conn.execute("SELECT name FROM devices")
            return [name for (name,) in await cur.fetchall()]


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = Settings()
    async with AsyncConnectionPool(
        settings.database_url, min_size=1, max_size=4, open=False
    ) as pool:
        app.state.store = PostgresStore(pool)
        yield


app = FastAPI(title="tado monitor", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC), name="static")


def get_store(request: Request) -> Store:
    store: Store = request.app.state.store
    return store


@app.get("/", include_in_schema=False)
async def index() -> FileResponse:
    return FileResponse(STATIC / "index.html")


@app.get("/api/series")
async def series(
    store: Annotated[Store, Depends(get_store)],
    range_key: Annotated[RangeKey, Query(alias="range")] = "24h",
) -> dict[str, Any]:
    r = RANGES[range_key]
    now = datetime.now(UTC)
    rows = await store.rows(query_since(grid(now, r)), r.bucket)
    return build(rows, await store.names(), now, range_key)


@app.get("/healthz", include_in_schema=False)
async def healthz() -> dict[str, str]:
    return {"status": "ok"}

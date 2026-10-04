"""Postgres writes. psycopg 3 async, one autocommit connection: every write
is a single statement, so there's nothing to group into a transaction."""

from collections.abc import Mapping
from datetime import datetime

from psycopg import AsyncConnection

from tado_monitor.matter import DeviceInfo


async def upsert_device(db: AsyncConnection, info: DeviceInfo, name: str) -> None:
    await db.execute(
        """
        INSERT INTO devices (node_id, name, vendor, product, serial)
        VALUES (%s, %s, %s, %s, %s)
        ON CONFLICT (node_id) DO UPDATE
        SET name = EXCLUDED.name, vendor = EXCLUDED.vendor,
            product = EXCLUDED.product, serial = EXCLUDED.serial,
            updated_at = now()
        """,
        (info.node_id, name, info.vendor, info.product, info.serial),
    )


async def insert_readings(
    db: AsyncConnection, node_id: int, ts: datetime, values: Mapping[str, float]
) -> None:
    if not values:
        return
    async with db.cursor() as cur:
        await cur.executemany(
            """
            INSERT INTO readings (ts, node_id, metric, value)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT DO NOTHING
            """,
            [(ts, node_id, metric, value) for metric, value in values.items()],
        )

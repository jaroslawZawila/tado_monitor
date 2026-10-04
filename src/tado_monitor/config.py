"""Typed configuration via pydantic-settings.

Env prefix is TADO_ (TADO_DATABASE_URL -> database_url). Defaults match
docker-compose.yml: the collector runs with host networking, so both the
Matter server and Postgres are on 127.0.0.1.
"""

import tomllib
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="TADO_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # matterjs-server's WebSocket API (python-matter-server compatible).
    matter_ws_url: str = "ws://127.0.0.1:5580/ws"
    # Plain libpq DSN -- psycopg directly, no SQLAlchemy in this project.
    database_url: str = "postgresql://tado:tado@127.0.0.1:5433/tado"
    # node_id -> display name. Matter has no idea what tado calls your rooms.
    names_file: Path = Path("rooms.toml")
    # Re-save every current value this often, even if unchanged, so graphs
    # have no gaps and "last seen" stays meaningful for quiet sensors.
    snapshot_interval_s: int = Field(default=300, ge=10)


def load_names(path: Path) -> dict[int, str]:
    """rooms.toml's ``[rooms]`` table: ``<node_id> = "<display name>"``.

    A missing file is fine (everything falls back to device labels); a
    malformed one should fail loudly at startup, so no try/except here.
    """
    if not path.exists():
        return {}
    with path.open("rb") as f:
        rooms = tomllib.load(f).get("rooms", {})
    return {int(node_id): str(name) for node_id, name in rooms.items()}

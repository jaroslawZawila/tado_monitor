# One image for both Python services: the collector (default CMD) and the
# web dashboard (compose overrides the command). Astral's image with Python
# 3.14 + uv, dependencies on their own layer from uv.lock.
FROM ghcr.io/astral-sh/uv:python3.14-bookworm-slim

ENV UV_COMPILE_BYTECODE=1 \
    UV_NO_CACHE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Dependencies first, on their own layer; then the project itself, which
# installs the tado-collector / tado-cli entry points into the venv.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY src ./src
RUN uv sync --frozen --no-dev

ENV PATH="/app/.venv/bin:$PATH"

RUN useradd --system --no-create-home app && chown -R app:app /app
USER app

CMD ["tado-collector"]

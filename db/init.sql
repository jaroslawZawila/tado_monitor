-- Runs once, on the first start of an empty postgres volume
-- (/docker-entrypoint-initdb.d). To re-run: docker compose down -v.

CREATE TABLE devices (
    node_id    bigint PRIMARY KEY,       -- Matter node id on *our* fabric
    name       text        NOT NULL,     -- rooms.toml > NodeLabel > fallback
    vendor     text,
    product    text,
    serial     text,
    updated_at timestamptz NOT NULL DEFAULT now()
);

-- Narrow on purpose: temperature and humidity arrive as separate Matter
-- events, so one row per metric per observation, no half-filled wide rows.
CREATE TABLE readings (
    ts      timestamptz      NOT NULL,
    node_id bigint           NOT NULL REFERENCES devices (node_id),
    metric  text             NOT NULL
            CHECK (metric IN ('temperature', 'humidity', 'setpoint', 'heating_demand')),
    value   double precision NOT NULL,
    PRIMARY KEY (node_id, metric, ts)
);
CREATE INDEX readings_metric_ts ON readings (metric, ts);

-- What Grafana queries: one series per device, labelled by its display name.
CREATE VIEW named_readings AS
SELECT r.ts, d.name, r.metric, r.value
FROM readings r
JOIN devices d USING (node_id);

-- Read-only access for the dashboards (Grafana and the FastAPI web app).
CREATE ROLE grafana LOGIN PASSWORD 'grafana';
GRANT SELECT ON devices, readings, named_readings TO grafana;

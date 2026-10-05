# tado_monitor

Logs per-room temperature and humidity from tado X devices into Postgres
and shows it in Grafana, using **local Matter over Thread**: no tado cloud API.

```
tado X ──Thread──▶ Bridge X (border router) ──IPv6──▶ host
                                                        │
 matter-server ──WebSocket──▶ collector ──▶ postgres ◀── grafana :3000
 (host net, :5580 local)      (Python)      (:5433 local)
```

- **matter-server**: [matterjs-server](https://github.com/matter-js/matterjs-server),
  our own Matter controller. Devices are shared to it from the tado app
  (multi-admin), so tado keeps working as before.
- **collector**: `src/tado_monitor/collector.py`. Listens for attribute
  updates, writes changed readings right away and re-saves every value every
  5 minutes.
- **postgres**: schema in `db/init.sql`. `readings` is one row per
  (device, metric, time), and `named_readings` is the view Grafana reads.
- **web**: `src/tado_monitor/web.py`, a FastAPI dashboard on
  http://localhost:8080 with temperature, dew point, humidity and heating
  demand per room (all rooms, or pick one; `?room=Office` links to it), over
  3h / 6h / 24h / 3d / 7d / 1m / 3m / 12m. It uses
  about 50 MB of RAM, so it also runs on a Pi 3. `GET /api/series?range=24h`
  serves the same data as JSON.
- **grafana** (optional): the "Home climate" dashboard is provisioned from
  `grafana/dashboards/climate.json`. It's read-only without a login;
  the admin login is `admin` / `admin`. It uses about 370 MB of RAM, so it's
  opt-in.

## Run

```sh
cp .env.example .env     # then uncomment what applies to this host
docker compose up -d --build
```

Set `COMPOSE_PROFILES=grafana` in `.env` to start Grafana too.

| What | Where |
|---|---|
| Dashboard (FastAPI) | http://localhost:8080 |
| Grafana (optional) | http://localhost:3000 |
| Matter server UI | http://localhost:5580 (localhost only, no auth) |
| Postgres | `psql postgresql://tado:tado@127.0.0.1:5433/tado` |

## Pairing a device

Do this once per device:

1. In the tado app, open the device's Matter linking option and generate a
   pairing code. You get an 11-digit number, and the device stays open for
   pairing for a few minutes.
2. Run:
   ```sh
   uv run tado-cli commission 12345678901
   ```
   The device is already on Bridge X's Thread network, so no Bluetooth or
   Thread credentials are needed. Sleepy devices can take a minute to answer.
3. Name it in `rooms.toml` using the node id that was printed, then run
   `docker compose restart collector`.

`uv run tado-cli nodes` lists every paired node with its current readings.
`uv run tado-cli dump <id>` prints the raw attribute map.

## Raspberry Pi

Measured RAM use with three devices: matter-server ~160 MB, postgres ~40 MB,
collector ~35 MB, Grafana ~370 MB.

| Pi | Grafana | `.env` |
|---|---|---|
| Pi 4, 2 GB+ | yes | `COMPOSE_PROFILES=grafana` |
| Pi 3, 1 GB | no | `MATTER_NODE_OPTIONS=--max-old-space-size=256`, plus swap (zram or a 512 MB swapfile) |

1. Flash **Raspberry Pi OS Lite 64-bit**. The Matter server image is arm64
   only, with no 32-bit build. Use Ethernet if you can: Matter discovery
   depends on multicast, which Wi-Fi handles worse. Set `MATTER_INTERFACE`
   (`eth0` or `wlan0`) in `.env`.
2. Install Docker:
   `curl -fsSL https://get.docker.com | sh && sudo usermod -aG docker $USER`,
   then log out and back in.
3. Check that the Pi learned the route to the Thread network from Bridge X.
   It should match the laptop:
   ```sh
   ip -6 route | grep fd59        # fd59:6e64:bc92:1::/64 via fe80::... proto ra
   ```
   If the route is missing (only on images without NetworkManager), set
   `net.ipv6.conf.<iface>.accept_ra=1` and `accept_ra_rt_info_max_plen=64`.

### Moving from the laptop (no re-pairing)

Every pairing lives in the Matter volume, so the devices come along with it.
**Never run two Matter servers with the same volume at the same time.**

```sh
# laptop
docker compose stop matter-server collector
scripts/backup.sh                       # prints the two backup files
rsync -a --exclude .venv ../tado_monitor pi@<pi>:~/

# Pi
cd ~/tado_monitor && cp .env.example .env   # adjust
scripts/restore.sh backups/tado-<stamp>.dump backups/matter-<stamp>.tgz
docker compose up -d --build

# laptop, once the Pi shows all devices available (docker compose logs collector)
docker compose down                     # keeps the volumes as a fallback
```

This was tested by restoring onto a fresh stack: the restored server
reconnected to all three devices within about 20 seconds.
`restore.sh` refuses to run on a database or Matter volume that already
holds data.

### Backups

`scripts/backup.sh` dumps the readings and archives the Matter volume into
`backups/`, keeping the newest 14. Run it nightly from cron:

```
0 3 * * *  /home/pi/tado_monitor/scripts/backup.sh >/dev/null
```

Copy `backups/` somewhere off the SD card now and then. The Matter archive
is the one that saves you from re-pairing everything.

## Dashboard notes

- **Dew point** is calculated from each room's temperature and humidity
  with the Magnus formula. Surfaces colder than the dew point collect
  condensation.
- **Heating demand is an estimate.** tado X exposes no valve demand
  (`PIHeatingDemand`) over Matter: its thermostat cluster has only
  temperature, setpoint and mode. The chart shows *setpoint − temperature*,
  clamped at 0, for devices that have a setpoint. Valves measure at the
  radiator, so they read warm while heating, and the estimate runs low
  during a heat-up.
- **Averaging:** each range averages readings into fixed buckets, from
  1 minute for 3h up to 1 day for 12m. Sensors only report when a value
  changes, so a value is carried forward up to 10 minutes. After that the
  line breaks, which shows when a device went offline.

## Develop

```sh
uv run pytest && uv run mypy && uv run ruff check .
uv run uvicorn tado_monitor.web:app --reload --port 8080   # dashboard against the local DB
```

## Host network notes (checked on this machine)

- Bridge X (`tado-TR…`, Thread network `tado-82b0`) advertises the route
  `fd59:6e64:bc92:1::/64`. NetworkManager installs it on `wlo1`. Check it with
  `ip -6 route | grep fd59`.
- If you are on a different interface, set `MATTER_INTERFACE` in `.env`.
- If battery devices stop reporting after 15–30 minutes, a stateful firewall
  is dropping UDP flows. Run
  `sudo sysctl -w net.netfilter.nf_conntrack_udp_timeout_stream=3600`.
  No firewall was active here when this was set up.

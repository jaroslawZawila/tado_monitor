"use strict";

// One page, four charts over the same time range, for every room or just
// one. Data comes from /api/series (see dashboard.py); uPlot draws the lines.

const RANGES = ["3h", "6h", "24h", "3d", "7d", "1m", "3m", "12m"];
const DEFAULT_RANGE = "24h";
const REFRESH_MS = 60_000;
const SLOTS = 8; // --series-1 .. --series-8; rooms past that share --series-other

const CHARTS = [
  { key: "temperature", title: "Temperature", unit: "°C", digits: 1,
    note: "Per room. Valves measure at the radiator and read warm while heating." },
  { key: "dew_point", title: "Dew point", unit: "°C", digits: 1,
    note: "Surfaces colder than this collect condensation." },
  { key: "humidity", title: "Relative humidity", unit: "%", digits: 1,
    note: "Per room." },
  { key: "heating_demand", title: "Heating demand", unit: "°C", digits: 1, zeroBased: true,
    note: "Estimated: degrees below setpoint. tado X doesn't report valve demand over Matter." },
];

const state = {
  range: initialRange(),
  room: new URLSearchParams(location.search).get("room"), // null: all rooms
  roomList: null, // rooms the room row was last drawn for
  payload: null,
  cards: new Map(),
  hovered: null, // the card under the pointer; only it shows a tooltip
  request: 0,
};

function initialRange() {
  const r = new URLSearchParams(location.search).get("range");
  return RANGES.includes(r) ? r : DEFAULT_RANGE;
}

// ---- formatting -----------------------------------------------------------

function css(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

// Color follows the room, never its position in a chart: the index comes
// from the full, sorted room list the API sends with every response.
function roomColor(rooms, name) {
  const i = rooms.indexOf(name);
  return i >= 0 && i < SLOTS ? css(`--series-${i + 1}`) : css("--series-other");
}

function fmt(v, digits) {
  return v.toLocaleString(undefined, { minimumFractionDigits: digits, maximumFractionDigits: digits });
}

// Y ticks get as many decimals as their spacing needs (0.5 steps -> 1), so
// adjacent ticks never round to the same label.
function yTicks(ticks, spec) {
  const step = ticks.length > 1 ? Math.abs(ticks[1] - ticks[0]) : 1;
  const digits = Math.max(0, Math.ceil(-Math.log10(step) - 1e-9));
  return ticks.map((v) => {
    const s = v.toLocaleString(undefined, { minimumFractionDigits: digits, maximumFractionDigits: digits });
    return spec.unit === "%" ? `${s}%` : `${s}°`;
  });
}

const HOUR_MINUTE = new Intl.DateTimeFormat(undefined, { hour: "2-digit", minute: "2-digit" });
const DAY_MONTH = new Intl.DateTimeFormat(undefined, { day: "numeric", month: "short" });

// X ticks in the viewer's locale (24 h where that's the norm). Short ranges
// show times, with the date under the first tick of each day; longer ones
// show dates only.
function xTicks(u, ticks) {
  const span = u.scales.x.max - u.scales.x.min;
  return ticks.map((t, i) => {
    const d = new Date(t * 1000);
    if (span > 2 * 86400) return DAY_MONTH.format(d);
    const newDay = i === 0 || new Date(ticks[i - 1] * 1000).getDate() !== d.getDate();
    return newDay ? `${HOUR_MINUTE.format(d)}\n${DAY_MONTH.format(d)}` : HOUR_MINUTE.format(d);
  });
}

function fmtTime(sec, bucketS) {
  const opts = { weekday: "short", day: "numeric", month: "short" };
  if (bucketS < 86400) Object.assign(opts, { hour: "2-digit", minute: "2-digit" });
  return new Intl.DateTimeFormat(undefined, opts).format(new Date(sec * 1000));
}

function bucketText(s) {
  const names = { 3600: "hourly", 86400: "daily" };
  if (names[s]) return `${names[s]} averages`;
  return s < 3600 ? `${s / 60}-minute averages` : `${s / 3600}-hour averages`;
}

function latest(values) {
  for (let i = values.length - 1; i >= 0; i--) if (values[i] != null) return values[i];
  return null;
}

// ---- range control --------------------------------------------------------

function renderRanges() {
  const nav = document.getElementById("ranges");
  nav.replaceChildren(...RANGES.map((key) => {
    const b = document.createElement("button");
    b.type = "button";
    b.textContent = key;
    b.setAttribute("aria-pressed", String(key === state.range));
    b.addEventListener("click", () => selectRange(key));
    return b;
  }));
}

function selectRange(key) {
  if (key === state.range) return;
  state.range = key;
  setParam("range", key);
  renderRanges();
  load({ dim: true });
}

function setParam(name, value) {
  const url = new URL(location.href);
  if (value == null) url.searchParams.delete(name);
  else url.searchParams.set(name, value);
  history.replaceState(null, "", url);
}

// ---- room control ---------------------------------------------------------

// Rebuilt only when the room list changes, so the minutely refresh doesn't
// steal keyboard focus from these buttons.
function renderRooms() {
  const rooms = state.payload.rooms;
  const nav = document.getElementById("rooms");
  const list = rooms.join("\n");
  if (state.roomList !== list) {
    state.roomList = list;
    nav.hidden = rooms.length < 2;
    nav.replaceChildren(...[null, ...rooms].map((room) => {
      const b = document.createElement("button");
      b.type = "button";
      b.textContent = room ?? "All rooms";
      b.dataset.room = room ?? "";
      b.addEventListener("click", () => selectRoom(room));
      return b;
    }));
  }
  for (const b of nav.children) {
    b.setAttribute("aria-pressed", String(b.dataset.room === (state.room ?? "")));
  }
}

function selectRoom(room) {
  if (room === state.room) return;
  state.room = room;
  setParam("room", room);
  renderRooms();
  render();
}

// A room from an old link that no longer exists falls back to all rooms.
function validateRoom() {
  if (state.room != null && !state.payload.rooms.includes(state.room)) {
    state.room = null;
    setParam("room", null);
  }
}

function visibleSeries(p, key) {
  const series = p.charts[key];
  return state.room == null ? series : series.filter((s) => s.name === state.room);
}

// ---- data -----------------------------------------------------------------

async function load({ dim = false } = {}) {
  const id = ++state.request;
  const main = document.getElementById("charts");
  if (dim) main.classList.add("loading");
  try {
    const res = await fetch(`/api/series?range=${encodeURIComponent(state.range)}`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const payload = await res.json();
    if (id !== state.request) return; // superseded by a newer range click
    state.payload = payload;
    validateRoom();
    renderRooms();
    render();
    const time = new Date().toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
    document.getElementById("meta").textContent =
      `${payload.label} · ${bucketText(payload.bucket_s)} · updated ${time}`;
  } catch (err) {
    if (id === state.request) {
      document.getElementById("meta").textContent = `Couldn't load data (${err.message}), retrying shortly.`;
    }
  } finally {
    if (id === state.request) main.classList.remove("loading");
  }
}

// ---- cards ----------------------------------------------------------------

function render() {
  const main = document.getElementById("charts");
  for (const spec of CHARTS) {
    let card = state.cards.get(spec.key);
    if (!card) {
      card = createCard(spec);
      main.append(card.el);
      state.cards.set(spec.key, card);
    }
    updateCard(card, state.payload);
  }
}

function createCard(spec) {
  const el = document.getElementById("chart-card").content.firstElementChild.cloneNode(true);
  el.querySelector(".title").textContent = spec.title;
  el.querySelector(".note").textContent = spec.note;
  const card = {
    el,
    spec,
    plot: el.querySelector(".plot"),
    legend: el.querySelector(".legend"),
    empty: el.querySelector(".empty"),
    toggle: el.querySelector(".table-toggle"),
    tableWrap: el.querySelector(".table-wrap"),
    u: null,
    names: null,
    colors: [],
    tooltip: null,
  };
  card.toggle.addEventListener("click", () => {
    const open = card.toggle.getAttribute("aria-expanded") !== "true";
    card.toggle.setAttribute("aria-expanded", String(open));
    card.toggle.textContent = open ? "Hide table" : "Show table";
    card.tableWrap.hidden = !open;
    if (open) renderTable(card);
  });
  new ResizeObserver(() => card.u?.setSize(plotSize(card))).observe(card.plot);
  return card;
}

function plotSize(card) {
  // Height includes the x-axis band, so the card never scrolls internally.
  return { width: Math.max(200, card.plot.clientWidth), height: 260 };
}

function updateCard(card, p) {
  const series = visibleSeries(p, card.spec.key);
  const hasData = series.length > 0;
  card.plot.hidden = !hasData;
  card.empty.hidden = hasData;
  card.toggle.hidden = !hasData;
  if (!hasData) card.tableWrap.hidden = true;
  else card.tableWrap.hidden = card.toggle.getAttribute("aria-expanded") !== "true";

  const data = [p.x, ...series.map((s) => s.values)];
  const names = series.map((s) => s.name).join("\n");
  if (card.u && card.names === names) {
    card.u.setData(data); // same rooms: keep the instance, cursor and toggles
  } else {
    card.u?.destroy();
    card.u = null;
    card.colors = series.map((s) => roomColor(p.rooms, s.name));
    if (hasData) card.u = new uPlot(options(card, series), data, card.plot);
  }
  card.names = names;
  renderLegend(card, series);
  if (!card.tableWrap.hidden) renderTable(card);
}

// Points only where a value has no neighbours: a lone bucket between gaps
// would otherwise be invisible as a line.
function isolated(values) {
  const idx = [];
  for (let i = 0; i < values.length; i++) {
    if (values[i] != null && values[i - 1] == null && values[i + 1] == null) idx.push(i);
  }
  return idx;
}

function options(card, series) {
  const spec = card.spec;
  const surface = css("--surface-1");
  const axis = {
    stroke: css("--text-muted"),
    font: '12px system-ui, -apple-system, "Segoe UI", sans-serif',
    grid: { stroke: css("--grid"), width: 1 },
    ticks: { show: false },
  };
  return {
    ...plotSize(card),
    padding: [12, 16, 0, 0],
    legend: { show: false },
    cursor: {
      sync: { key: "climate" }, // one crosshair across all four charts
      drag: { x: false, y: false },
      y: false,
      points: {
        size: 8,
        width: 2,
        stroke: () => surface,
        fill: (_u, i) => card.colors[i - 1],
      },
    },
    scales: {
      x: { time: true },
      y: spec.zeroBased ? { range: (_u, _min, max) => [0, Math.max(1, Math.ceil(max ?? 0))] } : {},
    },
    axes: [
      { ...axis, size: 48, values: xTicks },
      { ...axis, size: 56, values: (_u, ticks) => yTicks(ticks, spec) },
    ],
    series: [
      {},
      ...series.map((s, i) => ({
        label: s.name,
        stroke: card.colors[i],
        width: 2,
        spanGaps: false,
        points: {
          // 12 px including the 2 px surface ring: an 8 px visible dot.
          show: true,
          size: 12,
          width: 2,
          stroke: surface,
          fill: card.colors[i],
          filter: (u, si) => isolated(u.data[si]),
        },
      })),
    ],
    hooks: {
      init: [(u) => attachTooltip(card, u)],
      setCursor: [(u) => updateTooltip(card, u)],
    },
  };
}

// ---- legend, tooltip, table -----------------------------------------------

function renderLegend(card, series) {
  const spec = card.spec;
  card.legend.replaceChildren(...series.map((s, i) => {
    const b = document.createElement("button");
    b.type = "button";
    b.title = "Show or hide this room";
    b.setAttribute("aria-pressed", String(card.u ? card.u.series[i + 1].show : true));

    const key = document.createElement("span");
    key.className = "key";
    key.style.setProperty("--key", card.colors[i]);
    const name = document.createElement("span");
    name.textContent = s.name;
    const value = document.createElement("span");
    value.className = "value";
    const v = latest(s.values);
    value.textContent = v == null ? "–" : `${fmt(v, spec.digits)} ${spec.unit}`;
    b.append(key, name, value);

    b.addEventListener("click", () => {
      const show = !card.u.series[i + 1].show;
      card.u.setSeries(i + 1, { show });
      b.setAttribute("aria-pressed", String(show));
    });
    const li = document.createElement("li");
    li.append(b);
    return li;
  }));
}

function attachTooltip(card, u) {
  const tip = document.createElement("div");
  tip.className = "tooltip";
  tip.hidden = true;
  u.over.append(tip);
  card.tooltip = tip;
  u.over.addEventListener("mouseenter", () => { state.hovered = card; });
  u.over.addEventListener("mouseleave", () => {
    if (state.hovered === card) state.hovered = null;
    tip.hidden = true;
  });
}

function updateTooltip(card, u) {
  const tip = card.tooltip;
  const idx = u.cursor.idx;
  if (!tip || state.hovered !== card || idx == null) {
    if (tip) tip.hidden = true;
    return;
  }
  const spec = card.spec;
  const rows = [];
  for (let i = 1; i < u.series.length; i++) {
    const v = u.data[i][idx];
    if (v != null && u.series[i].show) rows.push({ v, name: u.series[i].label, color: card.colors[i - 1] });
  }
  if (!rows.length) {
    tip.hidden = true;
    return;
  }
  rows.sort((a, b) => b.v - a.v);

  const when = document.createElement("div");
  when.className = "when";
  when.textContent = fmtTime(u.data[0][idx], state.payload.bucket_s);
  // Values lead, names follow; names go in via textContent (they're data).
  tip.replaceChildren(when, ...rows.map((r) => {
    const row = document.createElement("div");
    row.className = "row";
    const key = document.createElement("span");
    key.className = "key";
    key.style.setProperty("--key", r.color);
    const value = document.createElement("strong");
    value.textContent = `${fmt(r.v, spec.digits)} ${spec.unit}`;
    const name = document.createElement("span");
    name.textContent = r.name;
    row.append(key, value, name);
    return row;
  }));
  tip.hidden = false;
  const x = u.cursor.left;
  const w = tip.offsetWidth;
  tip.style.left = `${x + 16 + w > u.over.clientWidth ? x - 16 - w : x + 16}px`;
}

// The accessible twin of each chart: every value, newest first.
function renderTable(card) {
  const p = state.payload;
  const spec = card.spec;
  const series = visibleSeries(p, spec.key);
  const table = document.createElement("table");
  table.setAttribute("aria-label", `${spec.title}, ${p.label.toLowerCase()}`);
  const head = table.createTHead().insertRow();
  for (const label of ["Time", ...series.map((s) => `${s.name} (${spec.unit})`)]) {
    const th = document.createElement("th");
    th.scope = "col";
    th.textContent = label;
    head.append(th);
  }
  const body = table.createTBody();
  for (let i = p.x.length - 1; i >= 0; i--) {
    if (series.every((s) => s.values[i] == null)) continue;
    const tr = body.insertRow();
    tr.insertCell().textContent = fmtTime(p.x[i], p.bucket_s);
    for (const s of series) {
      tr.insertCell().textContent = s.values[i] == null ? "–" : fmt(s.values[i], spec.digits);
    }
  }
  card.tableWrap.replaceChildren(table);
}

// ---- boot -----------------------------------------------------------------

function rebuildAll() {
  // Theme switch: canvas colors are baked in, so recreate every chart.
  for (const card of state.cards.values()) {
    card.u?.destroy();
    card.u = null;
    card.names = null;
  }
  if (state.payload) render();
}

renderRanges();
load();
setInterval(() => { if (!document.hidden) load(); }, REFRESH_MS);
document.addEventListener("visibilitychange", () => { if (!document.hidden) load(); });
matchMedia("(prefers-color-scheme: dark)").addEventListener("change", rebuildAll);

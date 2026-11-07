// Nova frontend: geolocation -> environment card -> controls -> /route -> map + stats + profile.
// API_BASE is same-origin when served by the backend; override with ?api=http://host:port for split dev.
const params = new URLSearchParams(location.search);
const API_BASE = params.get("api") || "";
const SUN = "#f5a524";
const SHADE = "#4c5fd5";
const NEUTRAL = "#64748b";
const DEFAULT_CENTER = [37.7793, -122.4193];

const state = {
  start: null,
  env: null,
  distance: 3,
  sun: "min",
  sunOverridden: false,
  elevation: 0.3,
  seed: null,
  route: null,
};

const $ = (id) => document.getElementById(id);

// ---------------------------------------------------------------- map
const map = L.map("map", { zoomControl: false }).setView(DEFAULT_CENTER, 14);
L.control.zoom({ position: "topright" }).addTo(map);
L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
  maxZoom: 19,
  attribution: "&copy; OpenStreetMap contributors",
  className: "base-tiles",
}).addTo(map);

let startMarker = null;
let routeLayer = L.layerGroup().addTo(map);

const startIcon = L.divIcon({ className: "", html: '<div class="start-pin"></div>', iconSize: [18, 18] });

map.on("click", (e) => {
  setStart(e.latlng.lat, e.latlng.lng, "pin");
});

function banner(msg, ms) {
  const b = $("banner");
  if (!msg) return b.classList.add("hidden");
  b.textContent = msg;
  b.classList.remove("hidden");
  if (ms) setTimeout(() => b.classList.add("hidden"), ms);
}

// ---------------------------------------------------------------- start point
function locate() {
  const qLat = parseFloat(params.get("lat"));
  const qLng = parseFloat(params.get("lng"));
  if (!isNaN(qLat) && !isNaN(qLng)) return setStart(qLat, qLng, "url");

  if (!("geolocation" in navigator)) {
    banner("Location isn't available. Tap the map to drop a pin.");
    return;
  }
  banner("Finding you…");
  navigator.geolocation.getCurrentPosition(
    (pos) => setStart(pos.coords.latitude, pos.coords.longitude, "gps"),
    (err) => {
      const msg = err.code === err.PERMISSION_DENIED
        ? "Location permission denied. Tap the map to drop a start pin."
        : "Couldn't get your location. Tap the map to drop a start pin.";
      banner(msg);
    },
    { enableHighAccuracy: true, timeout: 8000, maximumAge: 60000 }
  );
}

function setStart(lat, lng, source) {
  state.start = { lat, lng };
  if (!startMarker) {
    startMarker = L.marker([lat, lng], { icon: startIcon, draggable: true }).addTo(map);
    startMarker.on("dragend", () => {
      const p = startMarker.getLatLng();
      setStart(p.lat, p.lng, "pin");
    });
  } else {
    startMarker.setLatLng([lat, lng]);
  }
  if (source !== "pin") map.setView([lat, lng], 15);
  banner(source === "pin" ? "Start moved. Drag the dot or tap again to adjust." : null, 2500);
  routeLayer.clearLayers();
  $("result").classList.add("hidden");
  $("go").disabled = false;
  loadEnvironment();
}

// ---------------------------------------------------------------- environment
async function loadEnvironment() {
  const { lat, lng } = state.start;
  $("env").innerHTML = '<div class="env-loading">Reading the sky…</div>';
  try {
    const r = await fetch(`${API_BASE}/environment?lat=${lat}&lng=${lng}`);
    if (!r.ok) throw new Error(r.status);
    state.env = await r.json();
    renderEnvironment();
    if (!state.sunOverridden) setSun(state.env.suggested_sun, false);
    $("sunHint").textContent = state.sunOverridden ? "" : `· ${state.env.suggestion_reason}`;
  } catch (e) {
    $("env").innerHTML = '<div class="env-loading">Conditions unavailable. You can still generate a loop.</div>';
  }
}

function fmtMinutes(m) {
  if (m == null) return "—";
  if (m < 0) return "set";
  const h = Math.floor(m / 60);
  return h ? `${h}h ${m % 60}m` : `${m}m`;
}

function compass(alt, az) {
  // Sun dot on a ring: azimuth = angle, altitude = closeness to centre.
  const r = 22, cx = 27, cy = 27;
  const d = alt > 0 ? r * (1 - alt / 90) : r;
  const a = (az * Math.PI) / 180;
  const x = cx + d * Math.sin(a), y = cy - d * Math.cos(a);
  const up = alt > 0;
  return `<svg class="compass" viewBox="0 0 54 54" aria-label="Sun position">
    <circle cx="${cx}" cy="${cy}" r="${r}" fill="none" stroke="var(--line)" stroke-width="2"/>
    <text x="${cx}" y="9" font-size="7" text-anchor="middle" fill="var(--muted)">N</text>
    <circle cx="${x}" cy="${y}" r="6" fill="${up ? SUN : NEUTRAL}" ${up ? `style="filter:drop-shadow(0 0 4px ${SUN})"` : ""}/>
  </svg>`;
}

function renderEnvironment() {
  const e = state.env;
  const t = e.temperature_f != null ? `${Math.round(e.temperature_f)}°` : "—";
  $("env").innerHTML = `
    <div class="env-top">
      <div>
        <div class="temp">${t}</div>
        <div class="cond">${e.condition}${e.feels_like_f != null ? ` · feels ${Math.round(e.feels_like_f)}°` : ""}</div>
      </div>
      ${compass(e.sun_altitude, e.sun_azimuth)}
    </div>
    <div class="env-grid">
      <div><span>UV</span><b>${e.uv_index != null ? e.uv_index.toFixed(1) : "—"}</b></div>
      <div><span>Sun angle</span><b>${e.sun_altitude > 0 ? Math.round(e.sun_altitude) + "°" : "down"}</b></div>
      <div><span>Sunset</span><b>${fmtMinutes(e.minutes_to_sunset)}</b></div>
    </div>
    ${e.notes.map((n) => `<p class="env-note">${n}</p>`).join("")}
  `;
}

// ---------------------------------------------------------------- controls
document.querySelectorAll("#distance .chip").forEach((btn) =>
  btn.addEventListener("click", () => {
    document.querySelectorAll("#distance .chip").forEach((b) => b.classList.remove("active"));
    btn.classList.add("active");
    const custom = btn.dataset.mi === "custom";
    $("customWrap").classList.toggle("hidden", !custom);
    state.distance = custom ? parseFloat($("customMi").value) : parseFloat(btn.dataset.mi);
  })
);
$("customMi").addEventListener("input", (e) => {
  const v = parseFloat(e.target.value);
  if (!isNaN(v)) state.distance = Math.min(10, Math.max(0.5, v));
});

function setSun(mode, byUser) {
  state.sun = mode;
  if (byUser) {
    state.sunOverridden = true;
    $("sunHint").textContent = "";
  }
  document.querySelectorAll("#sun button").forEach((b) =>
    b.setAttribute("aria-checked", String(b.dataset.sun === mode))
  );
}
document.querySelectorAll("#sun button").forEach((b) => b.addEventListener("click", () => setSun(b.dataset.sun, true)));
setSun("min", false);

$("elev").addEventListener("input", (e) => (state.elevation = e.target.value / 100));
$("go").addEventListener("click", () => generate(null));
$("again").addEventListener("click", () => generate(Math.floor(Math.random() * 1e6)));

// ---------------------------------------------------------------- loading
const LOADING_LINES = [
  "Mapping the streets around you…",
  "Tracing shadows…",
  "Measuring building heights…",
  "Finding the hills…",
  "Drawing your loop…",
];
let loadingTimer = null;
function setLoading(on) {
  $("loading").classList.toggle("hidden", !on);
  clearInterval(loadingTimer);
  if (!on) return;
  let i = 0;
  $("loadingText").textContent = LOADING_LINES[0];
  loadingTimer = setInterval(() => {
    i = (i + 1) % LOADING_LINES.length;
    $("loadingText").textContent = LOADING_LINES[i];
  }, 1800);
}

// ---------------------------------------------------------------- route
async function generate(seed) {
  if (!state.start) return;
  state.seed = seed;
  setLoading(true);
  $("go").disabled = true;
  const { lat, lng } = state.start;
  const q = new URLSearchParams({
    lat, lng, distance_mi: state.distance, sun: state.sun, elevation: state.elevation,
  });
  if (seed != null) q.set("seed", seed);

  try {
    // A brand-new area can take a while to download; the API answers 202 until it's ready.
    for (let attempt = 0; attempt < 60; attempt++) {
      const r = await fetch(`${API_BASE}/route?${q}`);
      const data = await r.json();
      if (r.status === 202) {
        await new Promise((res) => setTimeout(res, (data.retry_after_s || 3) * 1000));
        continue;
      }
      if (!r.ok) throw new Error(data.message || `Error ${r.status}`);
      state.route = data;
      renderRoute(data);
      return;
    }
    throw new Error("This area is taking too long to load. Try a shorter distance.");
  } catch (e) {
    banner(e.message || "Something went wrong.", 6000);
  } finally {
    setLoading(false);
    $("go").disabled = false;
  }
}

function mix(a, b, t) {
  const pa = a.match(/\w\w/g).map((h) => parseInt(h, 16));
  const pb = b.match(/\w\w/g).map((h) => parseInt(h, 16));
  return "#" + pa.map((v, i) => Math.round(v + (pb[i] - v) * t).toString(16).padStart(2, "0")).join("");
}
const sunColor = (s) => (s == null ? NEUTRAL : mix(SHADE, SUN, s));

function renderRoute(data) {
  routeLayer.clearLayers();
  const all = [];
  data.segments.forEach((seg) => all.push(...seg.coords));
  L.polyline(all, { color: "#fff", weight: 10, opacity: 0.9, lineJoin: "round" }).addTo(routeLayer);
  data.segments.forEach((seg) =>
    L.polyline(seg.coords, { color: sunColor(seg.sun), weight: 6, opacity: 1, lineJoin: "round", lineCap: "round" })
      .bindTooltip(seg.sun == null ? "Shade unknown" : `${Math.round(seg.sun * 100)}% sun`, { sticky: true })
      .addTo(routeLayer)
  );
  // The loop starts at the nearest walkable node; link it to the start pin.
  if (state.start && all.length) {
    L.polyline([[state.start.lat, state.start.lng], all[0]], {
      color: NEUTRAL, weight: 3, dashArray: "4 6", opacity: 0.9,
    }).addTo(routeLayer);
  }
  const bounds = L.latLngBounds(all);
  const wide = innerWidth > 720;
  map.fitBounds(bounds, {
    paddingTopLeft: [wide ? 400 : 20, 20],
    paddingBottomRight: [20, wide ? 20 : innerHeight * 0.6],
  });

  const s = data.stats;
  const stat = (v, label) => `<div class="stat"><b>${v}</b><span>${label}</span></div>`;
  $("stats").innerHTML =
    stat(`${s.distance_mi} mi`, "distance") +
    stat(s.shade_pct != null ? `${s.shade_pct}%` : "—", "in shade") +
    stat(s.gain_ft != null ? `${s.gain_ft} ft` : "—", "climb") +
    stat(`${s.run_min} min`, `run · ${s.walk_min} min walk`);

  drawProfile(data.profile);

  const notes = [];
  if (data.degraded.includes("shade")) notes.push("Building shadows were still loading, so this loop ignores shade. Try again in a moment.");
  if (data.degraded.includes("elevation")) notes.push("Elevation data was unavailable, so hills weren't considered.");
  if (state.env) notes.push(...state.env.notes);
  $("notes").innerHTML = notes.map((n) => `<li>${n}</li>`).join("");

  $("result").classList.remove("hidden");
  $("result").scrollIntoView({ behavior: "smooth", block: "nearest" });
  describe(data);
}

function drawProfile(profile) {
  const svg = $("profile");
  const W = 320, H = 110, strip = 8, top = 6, bottom = H - strip - 6;
  const pts = profile.filter((p) => p.elev_ft != null);
  const maxD = profile[profile.length - 1].d_mi || 1;
  let body = "";
  if (pts.length > 1) {
    const lo = Math.min(...pts.map((p) => p.elev_ft));
    const hi = Math.max(...pts.map((p) => p.elev_ft));
    const span = Math.max(hi - lo, 30); // avoid exaggerating tiny bumps
    const x = (d) => (d / maxD) * W;
    const y = (e) => bottom - ((e - lo) / span) * (bottom - top);
    const line = pts.map((p, i) => `${i ? "L" : "M"}${x(p.d_mi).toFixed(1)},${y(p.elev_ft).toFixed(1)}`).join("");
    body += `<path d="${line}L${W},${bottom}L0,${bottom}Z" fill="var(--chip)"/>`;
    body += `<path d="${line}" fill="none" stroke="var(--ink)" stroke-width="1.5" vector-effect="non-scaling-stroke"/>`;
    body += `<text x="4" y="14" font-size="10" fill="var(--muted)">${Math.round(hi)} ft</text>`;
    body += `<text x="4" y="${bottom - 3}" font-size="10" fill="var(--muted)">${Math.round(lo)} ft</text>`;
  } else {
    body += `<text x="${W / 2}" y="${bottom / 2}" font-size="11" text-anchor="middle" fill="var(--muted)">No elevation data</text>`;
  }
  // Sun/shade ribbon along the bottom.
  for (let i = 1; i < profile.length; i++) {
    const x0 = (profile[i - 1].d_mi / maxD) * W;
    const x1 = (profile[i].d_mi / maxD) * W;
    body += `<rect x="${x0.toFixed(2)}" y="${H - strip}" width="${Math.max(x1 - x0, 0.5).toFixed(2)}" height="${strip}" fill="${sunColor(profile[i].sun)}"/>`;
  }
  svg.innerHTML = body;
}

async function describe(data) {
  const el = $("blurb");
  el.textContent = "Writing a quick summary…";
  el.classList.add("pending");
  try {
    const e = state.env || {};
    const r = await fetch(`${API_BASE}/describe`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        stats: data.stats,
        streets: data.streets,
        sun_mode: state.sun,
        elevation_pref: state.elevation < 0.35 ? "flat" : state.elevation > 0.65 ? "hilly" : "mixed",
        environment: {
          temperature_f: e.temperature_f, feels_like_f: e.feels_like_f, uv_index: e.uv_index,
          cloud_cover: e.cloud_cover, condition: e.condition, sun_altitude: data.sun.altitude,
          minutes_to_sunset: e.minutes_to_sunset,
        },
      }),
    });
    const { text } = await r.json();
    if (state.route !== data) return; // a newer route replaced this one
    if (text) {
      el.textContent = text;
      el.classList.remove("pending");
    } else {
      el.textContent = "";
    }
  } catch {
    el.textContent = "";
  }
}

locate();

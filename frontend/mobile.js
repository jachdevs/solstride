// SolStride mobile web: Setup -> Preview -> Run -> Summary. Same backend as the main page.
// On a laptop, "Simulate run" moves a fake GPS position along the loop so the run screen can be demoed.
const API = new URLSearchParams(location.search).get("api") || "";
const CMU = { lat: 40.4442, lng: -79.943 };
const M_PER_MI = 1609.344;
const SUN = "#f5a524", SHADE = "#4c5fd5", GRAY = "#64748b", DONE = "#9aa0aa";
const $ = (id) => document.getElementById(id);

const state = { start: null, env: null, distance: 3, sun: null, terrain: 0.1, route: null, run: null };

// ---------------------------------------------------------------- helpers
const getJSON = async (p) => { const r = await fetch(API + p); return { status: r.status, data: await r.json() }; };
const postJSON = async (p, body) => (await fetch(API + p, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) })).json();
const mix = (t) => { const a = [0x4c, 0x5f, 0xd5], b = [0xf5, 0xa5, 0x24]; return "#" + a.map((v, i) => Math.round(v + (b[i] - v) * t).toString(16).padStart(2, "0")).join(""); };
const sunColor = (s) => (s == null ? GRAY : mix(s));
const esc = (t) => String(t).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
function hav(a, b) {
  const R = 6371000, r = Math.PI / 180, dLat = (b[0] - a[0]) * r, dLng = (b[1] - a[1]) * r;
  const h = Math.sin(dLat / 2) ** 2 + Math.cos(a[0] * r) * Math.cos(b[0] * r) * Math.sin(dLng / 2) ** 2;
  return 2 * R * Math.asin(Math.sqrt(h));
}
const fmtTime = (s) => { s = Math.max(0, Math.floor(s)); const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), x = s % 60; return (h ? `${h}:${String(m).padStart(2, "0")}` : `${m}`) + `:${String(x).padStart(2, "0")}`; };
const fmtPace = (spm) => (!isFinite(spm) || spm <= 0 || spm > 3600 ? "--:--" : fmtTime(spm));

function show(id) {
  $("phone").dataset.screen = id;
  document.querySelectorAll(".screen").forEach((s) => s.classList.toggle("hidden", s.id !== id));
  // Never hide the map (a zero-size MapLibre canvas hangs); park it behind the screens instead.
  const slot = (id === "run" ? $("run") : document.querySelector(`#${id} .map-slot`)) || $("phone");
  slot.prepend($("map"));
  setTimeout(() => map.invalidateSize(), 0);
}

// ---------------------------------------------------------------- map (clean light basemap, same as main page)
const map = L.map("map", { zoomControl: false, attributionControl: true }).setView([CMU.lat, CMU.lng], 15);
try {
  const gl = L.maplibreGL({ style: "https://tiles.openfreemap.org/styles/liberty", attribution: "OpenFreeMap © OpenMapTiles © OpenStreetMap" }).addTo(map);
  const m = gl.getMaplibreMap();
  const tidy = () => m.getStyle().layers.forEach((l) => {
    if (/^(poi|airport|aeroway|highway-shield|road_shield|label_other|building-3d)/.test(l.id)) m.setLayoutProperty(l.id, "visibility", "none");
    else if (l.type === "line" && /(motorway|trunk|primary|secondary|tertiary)/.test(l.id) && /^(road|bridge|tunnel|highway)/.test(l.id))
      m.setPaintProperty(l.id, "line-color", /casing/.test(l.id) ? "#d9d6cf" : "#ffffff");
  });
  if (m.isStyleLoaded()) tidy();
  m.on("style.load", tidy);
} catch {
  L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", { maxZoom: 19, attribution: "© OpenStreetMap" }).addTo(map);
}
const routeLayer = L.layerGroup().addTo(map);
const runLayer = L.layerGroup().addTo(map);

// The map just moved into a differently sized slot: resize first, then fit.
function fitAfterResize(latlngs, pad) {
  setTimeout(() => { map.invalidateSize(false); map.fitBounds(L.latLngBounds(latlngs), { padding: [pad, pad], animate: false }); }, 120);
}

function drawRoute(route) {
  routeLayer.clearLayers();
  const all = route.segments.flatMap((s) => s.coords);
  L.polyline(all, { color: "#fff", weight: 10, opacity: 0.95 }).addTo(routeLayer);
  route.segments.forEach((s) => L.polyline(s.coords, { color: sunColor(s.sun), weight: 6, lineCap: "round" }).addTo(routeLayer));
  L.marker([route.start.lat, route.start.lng], { icon: L.divIcon({ className: "", html: '<div class="start-pin"></div>', iconSize: [16, 16] }) }).addTo(routeLayer);
  return all;
}

// ---------------------------------------------------------------- 1. setup
function pick(groupId, attr, onPick) {
  $(groupId).querySelectorAll("button").forEach((b) => b.addEventListener("click", () => {
    $(groupId).querySelectorAll("button").forEach((x) => x.classList.toggle("on", x === b));
    onPick(b.dataset[attr], b);
  }));
}
pick("dist", "mi", (v) => { $("customMi").classList.toggle("hidden", v !== "custom"); state.distance = v === "custom" ? "custom" : +v; });
pick("sun", "sun", (v) => { state.sun = v; $("sunHint").textContent = ""; });
pick("terrain", "e", (v) => (state.terrain = +v));
$("vibes").querySelectorAll("button").forEach((b) => b.addEventListener("click", () => {
  const on = !b.classList.contains("on");
  $("vibes").querySelectorAll("button").forEach((x) => x.classList.remove("on"));
  b.classList.toggle("on", on);
  $("vibe").value = on ? b.dataset.p : "";
}));
const setSunUI = (mode) => $("sun").querySelectorAll("button").forEach((b) => b.classList.toggle("on", b.dataset.sun === mode));

async function loadEnv() {
  try {
    const { data: e } = await getJSON(`/environment?lat=${state.start.lat}&lng=${state.start.lng}`);
    if (e.covered === false) {
      $("note").textContent = "You're outside SolStride's Pittsburgh area, so we'll start at CMU.";
      state.start = { ...CMU };
      return loadEnv();
    }
    state.env = e;
    const sunset = e.minutes_to_sunset > 0 ? `${Math.floor(e.minutes_to_sunset / 60)}h ${e.minutes_to_sunset % 60}m` : "set";
    $("env").innerHTML = `
      <div class="env-top"><span class="temp">${e.temperature_f != null ? Math.round(e.temperature_f) + "°" : "—"}</span>
      <span class="muted">${esc(e.condition)}${e.feels_like_f != null ? `<br>feels ${Math.round(e.feels_like_f)}°` : ""}</span></div>
      <div class="env-grid">
        <div class="stat"><span>UV</span><b>${e.uv_index != null ? e.uv_index.toFixed(1) : "—"}</b></div>
        <div class="stat"><span>Sun angle</span><b>${e.sun_altitude > 0 ? Math.round(e.sun_altitude) + "°" : "down"}</b></div>
        <div class="stat"><span>Sunset</span><b>${sunset}</b></div>
      </div>
      ${e.notes.length ? `<p class="muted env-note">${esc(e.notes[0])}</p>` : ""}`;
    if (!state.sun) { setSunUI(e.suggested_sun); $("sunHint").textContent = `· ${e.suggestion_reason}`; }
  } catch {
    $("env").innerHTML = '<span class="muted">Conditions unavailable.</span>';
  }
}

function locate() {
  const done = (p, msg) => { state.start = p; if (msg) $("note").textContent = msg; loadEnv(); };
  if (!navigator.geolocation) return done({ ...CMU }, "Location unavailable, so we'll start at CMU.");
  navigator.geolocation.getCurrentPosition(
    (p) => { $("phone").classList.add("loc-on"); done({ lat: p.coords.latitude, lng: p.coords.longitude }); },
    () => done({ ...CMU }, "Location unavailable, so we'll start at CMU."),
    { enableHighAccuracy: true, timeout: 8000, maximumAge: 60000 }
  );
}

const LINES = ["Mapping the streets…", "Tracing shadows…", "Sampling street photos…", "Drawing your loop…"];
let loadTimer;
function loading(on) {
  $("loading").classList.toggle("hidden", !on);
  clearInterval(loadTimer);
  if (on) { let i = 0; $("loadingText").textContent = LINES[0]; loadTimer = setInterval(() => ($("loadingText").textContent = LINES[++i % LINES.length]), 1600); }
}

async function generate(seed) {
  if (!state.start) return;
  loading(true);
  try {
    const miles = state.distance === "custom" ? Math.min(10, Math.max(0.5, +$("customMi").value || 2)) : state.distance;
    const sun = state.sun || state.env?.suggested_sun || "min";
    const q = new URLSearchParams({ lat: state.start.lat, lng: state.start.lng, distance_mi: miles, sun, elevation: state.terrain });
    if ($("vibe").value.trim()) q.set("description", $("vibe").value.trim());
    if (seed != null) q.set("seed", seed);
    for (let i = 0; i < 40; i++) {
      const { status, data } = await getJSON(`/route?${q}`);
      if (status === 202) { await new Promise((r) => setTimeout(r, 3000)); continue; }
      if (status !== 200) throw new Error(data.message || `Error ${status}`);
      state.route = data;
      return renderPreview(data);
    }
    throw new Error("Taking too long. Try a shorter distance.");
  } catch (e) {
    alert(e.message);
  } finally {
    loading(false);
  }
}
$("go").addEventListener("click", () => generate(null));

// ---------------------------------------------------------------- 2. preview
function renderPreview(r) {
  show("preview");
  $("preview").scrollTop = 0;
  const all = drawRoute(r);
  runLayer.clearLayers();
  fitAfterResize(all, 24);
  const s = r.stats, stat = (v, l) => `<div class="stat"><b>${v}</b><span>${l}</span></div>`;
  $("stats").innerHTML = stat(`${s.distance_mi}`, "miles") + stat(s.shade_pct != null ? `${s.shade_pct}%` : "—", "shade")
    + stat(s.gain_ft != null ? `${s.gain_ft}` : "—", "ft climb") + stat(`${s.run_min}`, "min run");
  drawProfile(r.profile);
}

function drawProfile(profile) {
  const W = 320, H = 100, strip = 8, bottom = H - strip - 4;
  const pts = profile.filter((p) => p.elev_ft != null), maxD = profile[profile.length - 1].d_mi || 1;
  let out = "";
  if (pts.length > 1) {
    const lo = Math.min(...pts.map((p) => p.elev_ft)), hi = Math.max(...pts.map((p) => p.elev_ft)), span = Math.max(hi - lo, 30);
    const d = pts.map((p, i) => `${i ? "L" : "M"}${(p.d_mi / maxD) * W},${bottom - ((p.elev_ft - lo) / span) * (bottom - 6)}`).join("");
    out += `<path d="${d}" fill="none" stroke="#f1f0ec" stroke-width="1.5" vector-effect="non-scaling-stroke"/>`;
  }
  for (let i = 1; i < profile.length; i++) {
    const x0 = (profile[i - 1].d_mi / maxD) * W, x1 = (profile[i].d_mi / maxD) * W;
    out += `<rect x="${x0}" y="${H - strip}" width="${Math.max(x1 - x0, 0.5)}" height="${strip}" fill="${sunColor(profile[i].sun)}"/>`;
  }
  $("profile").innerHTML = out;
}

$("back").addEventListener("click", () => show("setup"));
$("again").addEventListener("click", () => generate(Math.floor(Math.random() * 1e6)));

// ---------------------------------------------------------------- 3. run
function routePoints(route) {
  const pts = []; let cum = 0;
  route.segments.forEach((s) => s.coords.forEach((c) => {
    if (pts.length) cum += hav(pts[pts.length - 1].ll, c);
    pts.push({ ll: c, sun: s.sun, cum });
  }));
  return pts;
}

let geoWatch = null, tick = null, simTimer = null, wakeLock = null;
$("start").addEventListener("click", startRun);

async function startRun() {
  const pts = routePoints(state.route);
  state.run = { pts, total: pts[pts.length - 1].cum || 1, startedAt: Date.now(), pausedMs: 0, pausedAt: null, dist: 0, last: null, along: 0,
    sunDist: 0, sunW: 0, recent: [], track: [], off: false };
  show("run");
  runLayer.clearLayers();
  state.run.doneLine = L.polyline([], { color: DONE, weight: 7 }).addTo(runLayer);
  state.run.trackLine = L.polyline([], { color: "#2f7cf6", weight: 3, opacity: 0.8 }).addTo(runLayer);
  state.run.me = L.marker(pts[0].ll, { icon: L.divIcon({ className: "", html: '<div class="me"></div>', iconSize: [18, 18] }) }).addTo(runLayer);
  map.setView(pts[0].ll, 17);
  $("pause").textContent = "❚❚ Pause";
  $("sim").textContent = "🛰 Simulate run (laptop demo)";
  try { wakeLock = await navigator.wakeLock?.request("screen"); } catch {}
  if (navigator.geolocation) {
    geoWatch = navigator.geolocation.watchPosition(
      (p) => !simTimer && onFix(p.coords.latitude, p.coords.longitude, p.coords.accuracy, Date.now()),
      () => {}, { enableHighAccuracy: true, maximumAge: 0, timeout: 15000 }
    );
  }
  tick = setInterval(renderRun, 500);
  renderRun();
}

// Nearest point on the route polyline between distances lo..hi (metres along the loop).
function matchRoute(r, p, lo, hi) {
  const kx = 111320 * Math.cos(p[0] * Math.PI / 180), ky = 110540;
  let best = { dist: Infinity, along: r.along, sun: null };
  for (let i = 0; i < r.pts.length - 1; i++) {
    const A = r.pts[i], B = r.pts[i + 1];
    if (B.cum < lo) continue;
    if (A.cum > hi) break;
    const ax = (A.ll[1] - p[1]) * kx, ay = (A.ll[0] - p[0]) * ky;
    const bx = (B.ll[1] - p[1]) * kx, by = (B.ll[0] - p[0]) * ky;
    const vx = bx - ax, vy = by - ay, L2 = vx * vx + vy * vy || 1e-9;
    const t = Math.max(0, Math.min(1, -(ax * vx + ay * vy) / L2));
    const dist = Math.hypot(ax + t * vx, ay + t * vy);
    if (dist < best.dist) best = { dist, along: A.cum + t * (B.cum - A.cum), sun: A.sun };
  }
  return best;
}

// Route coordinates up to `along` metres, ending exactly at that point.
function routeUpTo(r) {
  const out = [];
  for (let i = 0; i < r.pts.length; i++) {
    const P = r.pts[i];
    if (P.cum <= r.along) { out.push(P.ll); continue; }
    const A = r.pts[i - 1];
    if (A) { const k = (r.along - A.cum) / Math.max(P.cum - A.cum, 0.01); out.push([A.ll[0] + (P.ll[0] - A.ll[0]) * k, A.ll[1] + (P.ll[1] - A.ll[1]) * k]); }
    break;
  }
  return out;
}

function onFix(lat, lng, acc, t) {
  const r = state.run;
  if (!r || r.pausedAt || (acc != null && acc > 25)) return;
  const p = [lat, lng];
  if (r.last) {
    const d = hav(r.last, p);
    if (d < 1.5) return; // jitter while standing still
    r.dist += d;
    // Match to the nearest route *segment* (points can be 100 m+ apart on straight streets),
    // only slightly ahead of current progress: a loop ends where it starts, so an
    // unbounded search would jump straight to the finish.
    const m = matchRoute(r, p, r.along - 50, r.along + d + 40);
    r.off = m.dist > 40;
    if (!r.off) r.along = Math.max(r.along, m.along);
    const sv = m.sun;
    if (sv != null) { r.sunW += sv * d; r.sunDist += d; }
  }
  r.last = p;
  r.track.push(p);
  r.recent = r.recent.filter((q) => t - q.t < 30000).concat({ t, dist: r.dist });
  r.me.setLatLng(p);
  r.trackLine.setLatLngs(r.track);
  r.doneLine.setLatLngs(routeUpTo(r));
  map.panTo(p, { animate: true });
}

const elapsed = (r) => (Date.now() - r.startedAt - r.pausedMs - (r.pausedAt ? Date.now() - r.pausedAt : 0)) / 1000;

function renderRun() {
  const r = state.run; if (!r) return;
  const rc = r.recent;
  // A simulated run plays back SIM_SPEEDUP x faster than real time, so scale clock and pace.
  const k = r.simulated ? SIM_SPEEDUP : 1;
  const curPace = rc.length > 1 ? ((rc[rc.length - 1].t - rc[0].t) / 1000 * k) / ((rc[rc.length - 1].dist - rc[0].dist) / M_PER_MI) : NaN;
  $("timer").textContent = fmtTime(elapsed(r) * k);
  $("rMi").textContent = (r.dist / M_PER_MI).toFixed(2);
  $("rPace").textContent = fmtPace(curPace);
  $("rPct").textContent = `${Math.min(100, Math.round(r.along / r.total * 100))}%`;
  $("off").classList.toggle("hidden", !r.off);
}

$("pause").addEventListener("click", () => {
  const r = state.run; if (!r) return;
  if (r.pausedAt) { r.pausedMs += Date.now() - r.pausedAt; r.pausedAt = null; $("pause").textContent = "❚❚ Pause"; }
  else { r.pausedAt = Date.now(); $("pause").textContent = "▶ Resume"; }
});

// Hold to finish (800 ms) so a pocket tap can't end the run.
(() => {
  const b = $("finish"), fill = b.querySelector(".fill");
  let t0, raf;
  const reset = () => { cancelAnimationFrame(raf); fill.style.width = "0"; };
  const step = () => {
    const k = Math.min(1, (Date.now() - t0) / 800);
    fill.style.width = `${k * 100}%`;
    if (k >= 1) { reset(); finishRun(); } else raf = requestAnimationFrame(step);
  };
  b.addEventListener("pointerdown", () => { t0 = Date.now(); raf = requestAnimationFrame(step); });
  ["pointerup", "pointerleave", "pointercancel"].forEach((e) => b.addEventListener(e, reset));
})();

// Laptop demo: move along the loop at ~9:00/mi, shown 20x faster than real time.
const SIM_SPEEDUP = 20;
$("sim").addEventListener("click", () => {
  const r = state.run; if (!r) return;
  if (simTimer) { clearInterval(simTimer); simTimer = null; $("sim").textContent = "🛰 Simulate run (laptop demo)"; return; }
  r.simulated = true;
  // Restart the clock so simulated time is consistent.
  r.startedAt = Date.now(); r.pausedMs = 0; r.dist = 0; r.last = null; r.along = 0; r.track = []; r.recent = []; r.sunDist = 0; r.sunW = 0;
  const speed = M_PER_MI / 540 * SIM_SPEEDUP; // metres per real second
  let along = 0;
  $("sim").textContent = "⏹ Stop simulation";
  simTimer = setInterval(() => {
    if (r.pausedAt) return;
    along = Math.min(r.total, along + speed * 0.5);
    let i = r.pts.findIndex((p) => p.cum >= along);
    if (i < 1) i = 1;
    const a = r.pts[i - 1], b = r.pts[i], k = (along - a.cum) / Math.max(b.cum - a.cum, 0.01);
    const jitter = () => (Math.random() - 0.5) * 0.00002;
    onFix(a.ll[0] + (b.ll[0] - a.ll[0]) * k + jitter(), a.ll[1] + (b.ll[1] - a.ll[1]) * k + jitter(), 5, Date.now());
    if (along >= r.total) { clearInterval(simTimer); simTimer = null; $("sim").textContent = "✓ Loop complete: hold to finish"; }
  }, 500);
});

function finishRun() {
  const r = state.run; if (!r) return;
  const factor = r.simulated ? SIM_SPEEDUP : 1;
  const secs = elapsed(r) * factor;
  clearInterval(tick); clearInterval(simTimer); simTimer = null;
  if (geoWatch != null) navigator.geolocation.clearWatch(geoWatch);
  try { wakeLock?.release(); } catch {}
  const mi = r.dist / M_PER_MI;
  const shade = r.sunDist > 0 ? Math.round((1 - r.sunW / r.sunDist) * 100) : null;
  show("summary");
  routeLayer.clearLayers(); runLayer.clearLayers();
  drawRoute(state.route);
  if (r.track.length > 1) {
    L.polyline(r.track, { color: "#2f7cf6", weight: 5 }).addTo(runLayer);
    fitAfterResize(r.track, 20);
  }
  const stat = (v, l) => `<div class="stat"><b>${v}</b><span>${l}</span></div>`;
  $("sumStats").innerHTML = stat(fmtTime(secs), "time") + stat(`${mi.toFixed(2)} mi`, `of ${state.route.stats.distance_mi} mi planned`)
    + stat(fmtPace(mi > 0.01 ? secs / mi : NaN), "avg pace /mi") + stat(shade != null ? `${shade}%` : "—", "ran in shade");
  state.run = null;
}
$("done").addEventListener("click", () => show("setup"));

// ---------------------------------------------------------------- simulated iOS chrome (laptop frame only)
function tickClock() {
  const d = new Date();
  $("clock").textContent = `${d.getHours() % 12 || 12}:${String(d.getMinutes()).padStart(2, "0")}`;
}
tickClock(); setInterval(tickClock, 15000);

show("setup");
locate();

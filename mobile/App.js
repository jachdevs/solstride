// SolStride mobile: Setup -> Preview -> Run -> Summary, in one file on purpose (hackathon-minimal).
// Talks to the same FastAPI backend as the web app; set EXPO_PUBLIC_API_BASE in mobile/.env.
import { useEffect, useMemo, useRef, useState } from "react";
import {
  ActivityIndicator, Alert, Image, Pressable, ScrollView, StyleSheet, Text, TextInput, View,
} from "react-native";
import MapView, { Marker, Polyline } from "react-native-maps";
import * as Location from "expo-location";
import { activateKeepAwakeAsync, deactivateKeepAwake } from "expo-keep-awake";
import Svg, { Path, Rect } from "react-native-svg";
import { SafeAreaProvider, SafeAreaView } from "react-native-safe-area-context";
import { StatusBar } from "expo-status-bar";

const API = (process.env.EXPO_PUBLIC_API_BASE || "http://localhost:8765").replace(/\/$/, "");
const HEADERS = { "ngrok-skip-browser-warning": "1" };
const CMU = { lat: 40.4442, lng: -79.943 };
const C = {
  bg: "#1b1b21", card: "#26262e", line: "#34343d", ink: "#f1f0ec", muted: "#a09fa8",
  sun: "#f5a524", shade: "#4c5fd5", gray: "#64748b", done: "#9aa0aa",
};
const VIBES = [
  ["🌳 Tree-lined", "A tree-lined residential street."],
  ["🌿 Park path", "A pedestrian path through a park."],
  ["🚶 Sidewalk", "A street with a clearly visible sidewalk."],
  ["🏙️ Urban road", "A busy multilane road."],
];
const TERRAIN = [["Flat", 0.1], ["Rolling", 0.5], ["Hilly", 0.9]];

// ------------------------------------------------------------------ helpers
const getJSON = async (path) => {
  const r = await fetch(API + path, { headers: HEADERS });
  return { status: r.status, data: await r.json() };
};
const postJSON = async (path, body) => {
  const r = await fetch(API + path, {
    method: "POST", headers: { ...HEADERS, "Content-Type": "application/json" }, body: JSON.stringify(body),
  });
  return r.json();
};
const mix = (t) => {
  const a = [0x4c, 0x5f, 0xd5], b = [0xf5, 0xa5, 0x24];
  return "#" + a.map((v, i) => Math.round(v + (b[i] - v) * t).toString(16).padStart(2, "0")).join("");
};
const sunColor = (s) => (s == null ? C.gray : mix(s));
function haversine(a, b) {
  const R = 6371000, r = Math.PI / 180;
  const dLat = (b.latitude - a.latitude) * r, dLng = (b.longitude - a.longitude) * r;
  const h = Math.sin(dLat / 2) ** 2 + Math.cos(a.latitude * r) * Math.cos(b.latitude * r) * Math.sin(dLng / 2) ** 2;
  return 2 * R * Math.asin(Math.sqrt(h));
}
const fmtTime = (s) => {
  s = Math.max(0, Math.floor(s));
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = s % 60;
  return (h ? `${h}:${String(m).padStart(2, "0")}` : `${m}`) + `:${String(sec).padStart(2, "0")}`;
};
const fmtPace = (secPerMi) => (!isFinite(secPerMi) || secPerMi <= 0 || secPerMi > 3600 ? "--:--" : fmtTime(secPerMi));
const M_PER_MI = 1609.344;

// Flatten route segments into points with sun value + cumulative distance (for progress/shade).
function routePoints(route) {
  const pts = [];
  let cum = 0;
  for (const seg of route.segments) {
    for (const [lat, lng] of seg.coords) {
      const p = { latitude: lat, longitude: lng, sun: seg.sun };
      if (pts.length) cum += haversine(pts[pts.length - 1], p);
      pts.push({ ...p, cum });
    }
  }
  return pts;
}

// ------------------------------------------------------------------ small UI pieces
const Chip = ({ label, active, onPress, style }) => (
  <Pressable onPress={onPress} style={[s.chip, active && s.chipOn, style]}>
    <Text style={[s.chipText, active && s.chipTextOn]}>{label}</Text>
  </Pressable>
);
const Stat = ({ value, label, big }) => (
  <View style={s.stat}>
    <Text style={[s.statValue, big && { fontSize: 34 }]}>{value}</Text>
    <Text style={s.statLabel}>{label}</Text>
  </View>
);
const Button = ({ title, onPress, onLongPress, kind = "primary", style }) => (
  <Pressable onPress={onPress} onLongPress={onLongPress} delayLongPress={800}
    style={({ pressed }) => [s.btn, kind === "ghost" && s.btnGhost, kind === "danger" && s.btnDanger, pressed && { opacity: 0.8 }, style]}>
    <Text style={[s.btnText, kind === "ghost" && { color: C.ink }]}>{title}</Text>
  </Pressable>
);

function RouteLines({ route }) {
  const all = useMemo(() => route.segments.flatMap((sg) => sg.coords.map(([a, b]) => ({ latitude: a, longitude: b }))), [route]);
  return (
    <>
      <Polyline coordinates={all} strokeColor="#ffffff" strokeWidth={9} />
      {route.segments.map((sg, i) => (
        <Polyline key={i} coordinates={sg.coords.map(([a, b]) => ({ latitude: a, longitude: b }))}
          strokeColor={sunColor(sg.sun)} strokeWidth={5} />
      ))}
    </>
  );
}

function Profile({ profile }) {
  const W = 320, H = 100, strip = 8, bottom = H - strip - 4;
  const pts = profile.filter((p) => p.elev_ft != null);
  const maxD = profile[profile.length - 1]?.d_mi || 1;
  let line = "";
  if (pts.length > 1) {
    const lo = Math.min(...pts.map((p) => p.elev_ft)), hi = Math.max(...pts.map((p) => p.elev_ft));
    const span = Math.max(hi - lo, 30);
    line = pts.map((p, i) => `${i ? "L" : "M"}${(p.d_mi / maxD) * W},${bottom - ((p.elev_ft - lo) / span) * (bottom - 6)}`).join("");
  }
  return (
    <Svg width="100%" height={H} viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none">
      {line ? <Path d={line} stroke={C.ink} strokeWidth={1.5} fill="none" /> : null}
      {profile.slice(1).map((p, i) => {
        const x0 = (profile[i].d_mi / maxD) * W, x1 = (p.d_mi / maxD) * W;
        return <Rect key={i} x={x0} y={H - strip} width={Math.max(x1 - x0, 0.5)} height={strip} fill={sunColor(p.sun)} />;
      })}
    </Svg>
  );
}

// ------------------------------------------------------------------ screens
function Setup({ start, setStart, onRoute }) {
  const [env, setEnv] = useState(null);
  const [distance, setDistance] = useState(3);
  const [custom, setCustom] = useState("");
  const [sun, setSun] = useState(null); // null = follow suggestion
  const [vibe, setVibe] = useState("");
  const [terrain, setTerrain] = useState(0.1);
  const [loading, setLoading] = useState(false);
  const [note, setNote] = useState("");

  useEffect(() => {
    (async () => {
      const { status } = await Location.requestForegroundPermissionsAsync();
      if (status !== "granted") { setNote("Location denied: starting at CMU."); setStart(CMU); return; }
      const p = await Location.getCurrentPositionAsync({ accuracy: Location.Accuracy.Balanced });
      setStart({ lat: p.coords.latitude, lng: p.coords.longitude });
    })().catch(() => { setNote("Couldn't get location: starting at CMU."); setStart(CMU); });
  }, []);

  useEffect(() => {
    if (!start) return;
    getJSON(`/environment?lat=${start.lat}&lng=${start.lng}`).then(({ data }) => {
      setEnv(data);
      if (data.covered === false) {
        setNote("You're outside SolStride's Pittsburgh area, so we'll start at CMU.");
        setStart(CMU);
      }
    }).catch(() => setNote(`Can't reach the server at ${API}`));
  }, [start?.lat, start?.lng]);

  const sunMode = sun || env?.suggested_sun || "min";
  const miles = distance === "custom" ? parseFloat(custom) || 2 : distance;

  async function generate(seed) {
    setLoading(true);
    try {
      const q = new URLSearchParams({ lat: start.lat, lng: start.lng, distance_mi: miles, sun: sunMode, elevation: terrain });
      if (vibe.trim()) q.set("description", vibe.trim());
      if (seed != null) q.set("seed", seed);
      for (let i = 0; i < 40; i++) {
        const { status, data } = await getJSON(`/route?${q}`);
        if (status === 202) { await new Promise((r) => setTimeout(r, 3000)); continue; }
        if (status !== 200) throw new Error(data.message || `Error ${status}`);
        onRoute(data, { regenerate: () => generate(Math.floor(Math.random() * 1e6)), vibe: vibe.trim() });
        return;
      }
      throw new Error("Taking too long. Try a shorter distance.");
    } catch (e) {
      Alert.alert("Couldn't build a loop", e.message);
    } finally {
      setLoading(false);
    }
  }

  return (
    <ScrollView contentContainerStyle={s.pad}>
      <View style={s.brand}><Text style={s.logo}>◐</Text><View>
        <Text style={s.h1}>SolStride</Text><Text style={s.muted}>Loops that follow the light, or dodge it.</Text>
      </View></View>

      <View style={s.card}>
        {!env ? <ActivityIndicator color={C.sun} /> : (<>
          <Text style={s.temp}>{env.temperature_f != null ? `${Math.round(env.temperature_f)}°` : "—"}</Text>
          <Text style={s.muted}>{env.condition}{env.feels_like_f != null ? ` · feels ${Math.round(env.feels_like_f)}°` : ""}</Text>
          <View style={s.row}>
            <Stat value={env.uv_index != null ? env.uv_index.toFixed(1) : "—"} label="UV" />
            <Stat value={env.sun_altitude > 0 ? `${Math.round(env.sun_altitude)}°` : "down"} label="SUN ANGLE" />
            <Stat value={env.minutes_to_sunset > 0 ? `${Math.floor(env.minutes_to_sunset / 60)}h ${env.minutes_to_sunset % 60}m` : "set"} label="SUNSET" />
          </View>
          {env.notes.map((n) => <Text key={n} style={[s.muted, { marginTop: 8 }]}>{n}</Text>)}
        </>)}
      </View>
      {note ? <Text style={s.note}>{note}</Text> : null}

      <Text style={s.label}>DISTANCE</Text>
      <View style={s.wrap}>
        {[1, 3, 5].map((m) => <Chip key={m} label={`${m} mi`} active={distance === m} onPress={() => setDistance(m)} />)}
        <Chip label="Custom" active={distance === "custom"} onPress={() => setDistance("custom")} />
      </View>
      {distance === "custom" ? (
        <TextInput style={[s.input, { width: 120 }]} keyboardType="decimal-pad" placeholder="miles"
          placeholderTextColor={C.muted} value={custom} onChangeText={setCustom} />
      ) : null}

      <Text style={s.label}>SUN{env && !sun ? <Text style={{ color: C.sun }}>{`  · ${env.suggestion_reason}`}</Text> : null}</Text>
      <View style={s.row}>
        <Chip label="☂ Min sun" active={sunMode === "min"} onPress={() => setSun("min")} style={[s.flex, sunMode === "min" && { backgroundColor: C.shade, borderColor: C.shade }]} />
        <Chip label="☀ Max sun" active={sunMode === "max"} onPress={() => setSun("max")} style={[s.flex, sunMode === "max" && { backgroundColor: C.sun, borderColor: C.sun }]} />
      </View>

      <Text style={s.label}>ROUTE VIBE & SCENERY</Text>
      <TextInput style={s.input} multiline placeholder="Describe your preferred route (e.g. tree-lined street, park path)…"
        placeholderTextColor={C.muted} value={vibe} onChangeText={setVibe} />
      <View style={s.wrap}>
        {VIBES.map(([label, prompt]) => (
          <Chip key={label} label={label} active={vibe === prompt} onPress={() => setVibe(vibe === prompt ? "" : prompt)} />
        ))}
      </View>

      <Text style={s.label}>TERRAIN</Text>
      <View style={s.row}>
        {TERRAIN.map(([label, v]) => <Chip key={label} label={label} active={terrain === v} onPress={() => setTerrain(v)} style={s.flex} />)}
      </View>

      <Button title={loading ? "Tracing shadows…" : "Generate loop"} onPress={() => !loading && start && generate(null)} style={{ marginTop: 18 }} />
    </ScrollView>
  );
}

function Preview({ route, regenerate, vibeText, onBack, onStart }) {
  const mapRef = useRef(null);
  // undefined = photos still loading, null = none available
  const initialVibe = () => route.vibe ?? (route.photo_points?.length ? undefined : null);
  const [vibe, setVibe] = useState(initialVibe);
  const all = useMemo(() => route.segments.flatMap((sg) => sg.coords.map(([a, b]) => ({ latitude: a, longitude: b }))), [route]);

  useEffect(() => {
    setVibe(initialVibe());
    if (!route.vibe && route.photo_points?.length) {
      postJSON("/photos", { points: route.photo_points, description: "" })
        .then((r) => setVibe(r.vibe ?? null)).catch(() => setVibe(null));
    }
    setTimeout(() => mapRef.current?.fitToCoordinates(all, { edgePadding: { top: 40, bottom: 40, left: 40, right: 40 }, animated: false }), 300);
  }, [route]);

  const st = route.stats;
  return (
    <ScrollView contentContainerStyle={{ paddingBottom: 30 }}>
      <MapView ref={mapRef} style={{ height: 340 }} userInterfaceStyle="light" showsPointsOfInterest={false}
        initialRegion={{ latitude: route.start.lat, longitude: route.start.lng, latitudeDelta: 0.03, longitudeDelta: 0.03 }}>
        <RouteLines route={route} />
        <Marker coordinate={{ latitude: route.start.lat, longitude: route.start.lng }} pinColor={C.ink} />
      </MapView>
      <View style={s.pad}>
        <View style={s.grid}>
          <Stat big value={`${st.distance_mi} mi`} label="distance" />
          <Stat big value={st.shade_pct != null ? `${st.shade_pct}%` : "—"} label="in shade" />
          <Stat big value={st.gain_ft != null ? `${st.gain_ft} ft` : "—"} label="climb" />
          <Stat big value={`${st.run_min} min`} label={`run · ${st.walk_min} min walk`} />
        </View>

        <Text style={s.label}>ELEVATION & SUN ALONG THE LOOP</Text>
        <Profile profile={route.profile} />

        {vibe?.photos?.length ? (<>
          <Text style={s.label}>STREET SCENERY</Text>
          <View style={[s.card, s.rowBetween]}>
            <Text style={[s.muted, s.flex]} numberOfLines={2}>
              {vibe.description ? <>Match with <Text style={{ color: C.ink, fontWeight: "600" }}>“{vibe.description.replace(/\.$/, "")}”</Text></> : "Scenery match"}
            </Text>
            <Text style={s.pct}>{Math.round(vibe.avg_match_pct)}%</Text>
          </View>
          <ScrollView horizontal showsHorizontalScrollIndicator={false} style={{ marginTop: 10 }}>
            {vibe.photos.map((p) => (
              <View key={p.sample_idx} style={s.photo}>
                <Image source={{ uri: p.image }} style={{ height: 110, width: "100%" }} />
                <View style={{ padding: 8 }}>
                  <Text style={s.photoTitle} numberOfLines={1}>#{p.sample_idx + 1}{p.street_name && p.street_name !== "Unnamed road" ? ` · ${p.street_name}` : ""}</Text>
                  <Text style={s.photoMatch}>{Math.round(p.target_score_pct)}% match</Text>
                  <Text style={s.muted} numberOfLines={2}>{p.top_benchmark.replace(/\.$/, "")}</Text>
                </View>
              </View>
            ))}
          </ScrollView>
        </>) : vibe === undefined ? <Text style={[s.muted, { marginTop: 12 }]}>Loading street photos…</Text> : null}

        <Button title="▶  Start run" onPress={onStart} style={{ marginTop: 20 }} />
        <View style={[s.row, { marginTop: 10 }]}>
          <Button kind="ghost" title="← Back" onPress={onBack} style={s.flex} />
          <Button kind="ghost" title="↻ Try another" onPress={regenerate} style={s.flex} />
        </View>
      </View>
    </ScrollView>
  );
}

function Run({ route, onFinish }) {
  const pts = useMemo(() => routePoints(route), [route]);
  const total = pts.length ? pts[pts.length - 1].cum : 1;
  const [now, setNow] = useState(Date.now());
  const [paused, setPaused] = useState(false);
  const [track, setTrack] = useState([]);
  const st = useRef({ startedAt: Date.now(), pausedMs: 0, pausedAt: null, dist: 0, last: null, idx: 0, sunDist: 0, sunWeighted: 0, offRoute: false, recent: [] });
  const pausedRef = useRef(false);

  const elapsed = () => {
    const r = st.current;
    return (now - r.startedAt - r.pausedMs - (r.pausedAt ? now - r.pausedAt : 0)) / 1000;
  };

  useEffect(() => {
    activateKeepAwakeAsync("run");
    const tick = setInterval(() => setNow(Date.now()), 1000);
    let sub;
    Location.watchPositionAsync({ accuracy: Location.Accuracy.BestForNavigation, distanceInterval: 3, timeInterval: 1000 }, (loc) => {
      const { latitude, longitude, accuracy } = loc.coords;
      if (pausedRef.current || (accuracy != null && accuracy > 25)) return;
      const p = { latitude, longitude, t: loc.timestamp };
      const r = st.current;
      if (r.last) {
        const d = haversine(r.last, p);
        if (d < 1.5) return; // GPS jitter while standing still
        r.dist += d;
        // Sun/shade actually experienced: nearest route point's sun value.
        let best = r.idx, bestD = Infinity;
        const hi = Math.min(pts.length, r.idx + 250);
        for (let i = Math.max(0, r.idx - 20); i < hi; i++) {
          const dd = haversine(p, pts[i]);
          if (dd < bestD) { bestD = dd; best = i; }
        }
        r.offRoute = bestD > 40;
        if (!r.offRoute) r.idx = Math.max(r.idx, best);
        const sv = pts[best]?.sun;
        if (sv != null) { r.sunWeighted += sv * d; r.sunDist += d; }
      }
      r.last = p;
      r.recent = [...r.recent.filter((q) => p.t - q.t < 30000), { t: p.t, dist: r.dist }];
      setTrack((t) => [...t, { latitude, longitude }]);
    }).then((s_) => (sub = s_));
    return () => { clearInterval(tick); sub?.remove(); deactivateKeepAwake("run"); };
  }, []);

  function togglePause() {
    const r = st.current;
    if (paused) { r.pausedMs += Date.now() - r.pausedAt; r.pausedAt = null; }
    else r.pausedAt = Date.now();
    pausedRef.current = !paused;
    setPaused(!paused);
  }

  function finish() {
    const r = st.current;
    const secs = elapsed();
    onFinish({
      seconds: secs, meters: r.dist, track,
      shadePct: r.sunDist > 0 ? Math.round((1 - r.sunWeighted / r.sunDist) * 100) : null,
      plannedMi: route.stats.distance_mi,
    });
  }

  const r = st.current;
  const secs = elapsed();
  const mi = r.dist / M_PER_MI;
  const rc = r.recent;
  const curPace = rc.length > 1 ? ((rc[rc.length - 1].t - rc[0].t) / 1000) / ((rc[rc.length - 1].dist - rc[0].dist) / M_PER_MI) : NaN;
  const progress = Math.min(100, Math.round((pts[r.idx]?.cum || 0) / total * 100));
  const done = pts.slice(0, r.idx + 1);

  return (
    <View style={{ flex: 1 }}>
      <MapView style={{ flex: 1 }} userInterfaceStyle="light" showsUserLocation followsUserLocation showsPointsOfInterest={false}
        initialRegion={{ latitude: route.start.lat, longitude: route.start.lng, latitudeDelta: 0.012, longitudeDelta: 0.012 }}>
        <RouteLines route={route} />
        {done.length > 1 ? <Polyline coordinates={done} strokeColor={C.done} strokeWidth={6} /> : null}
      </MapView>
      <View style={s.runPanel}>
        {r.offRoute ? <Text style={s.offRoute}>Off route: head back to the colored line</Text> : null}
        <Text style={s.timer}>{fmtTime(secs)}</Text>
        <View style={s.row}>
          <Stat value={mi.toFixed(2)} label="miles" />
          <Stat value={fmtPace(curPace)} label="pace /mi" />
          <Stat value={`${progress}%`} label="of loop" />
        </View>
        <View style={[s.row, { marginTop: 12 }]}>
          <Button kind="ghost" title={paused ? "▶ Resume" : "❚❚ Pause"} onPress={togglePause} style={s.flex} />
          <Button kind="danger" title="Hold to finish" onPress={() => Alert.alert("Hold the button to finish")} onLongPress={finish} style={s.flex} />
        </View>
      </View>
    </View>
  );
}

function Summary({ run, onDone }) {
  const mi = run.meters / M_PER_MI;
  return (
    <ScrollView contentContainerStyle={s.pad}>
      <Text style={s.h1}>Run complete 🎉</Text>
      {run.track.length > 1 ? (
        <MapView style={{ height: 260, borderRadius: 16, marginTop: 14 }} userInterfaceStyle="light"
          initialRegion={{ latitude: run.track[0].latitude, longitude: run.track[0].longitude, latitudeDelta: 0.02, longitudeDelta: 0.02 }}>
          <Polyline coordinates={run.track} strokeColor={C.sun} strokeWidth={5} />
        </MapView>
      ) : null}
      <View style={[s.grid, { marginTop: 14 }]}>
        <Stat big value={fmtTime(run.seconds)} label="time" />
        <Stat big value={`${mi.toFixed(2)} mi`} label={`of ${run.plannedMi} mi planned`} />
        <Stat big value={fmtPace(mi > 0.01 ? run.seconds / mi : NaN)} label="avg pace /mi" />
        <Stat big value={run.shadePct != null ? `${run.shadePct}%` : "—"} label="ran in shade" />
      </View>
      <Button title="Done" onPress={onDone} style={{ marginTop: 20 }} />
    </ScrollView>
  );
}

// ------------------------------------------------------------------ app
export default function App() {
  const [screen, setScreen] = useState("setup");
  const [start, setStart] = useState(null);
  const [route, setRoute] = useState(null);
  const [ctx, setCtx] = useState({});
  const [run, setRun] = useState(null);

  return (
    <SafeAreaProvider>
      <SafeAreaView style={s.app} edges={screen === "run" ? ["top"] : ["top", "bottom"]}>
        <StatusBar style="light" />
        {screen === "setup" && (
          <Setup start={start} setStart={setStart}
            onRoute={(r, c) => { setRoute(r); setCtx(c); setScreen("preview"); }} />
        )}
        {screen === "preview" && route && (
          <Preview route={route} regenerate={ctx.regenerate} onBack={() => setScreen("setup")}
            onStart={() => setScreen("run")} />
        )}
        {screen === "run" && route && <Run route={route} onFinish={(r) => { setRun(r); setScreen("summary"); }} />}
        {screen === "summary" && run && <Summary run={run} onDone={() => setScreen("setup")} />}
      </SafeAreaView>
    </SafeAreaProvider>
  );
}

const s = StyleSheet.create({
  app: { flex: 1, backgroundColor: C.bg },
  pad: { padding: 18, paddingBottom: 40 },
  brand: { flexDirection: "row", alignItems: "center", gap: 12, marginBottom: 16 },
  logo: { fontSize: 34, color: C.sun },
  h1: { fontSize: 26, fontWeight: "700", color: C.ink },
  muted: { color: C.muted, fontSize: 14 },
  note: { color: C.sun, fontSize: 13, marginTop: 8 },
  card: { backgroundColor: C.card, borderRadius: 16, padding: 14, borderWidth: 1, borderColor: C.line },
  temp: { fontSize: 40, fontWeight: "700", color: C.ink },
  row: { flexDirection: "row", gap: 8, marginTop: 10 },
  rowBetween: { flexDirection: "row", alignItems: "center", gap: 12, marginTop: 4 },
  wrap: { flexDirection: "row", flexWrap: "wrap", gap: 8 },
  grid: { flexDirection: "row", flexWrap: "wrap", gap: 8 },
  flex: { flex: 1 },
  label: { color: C.muted, fontSize: 13, fontWeight: "700", letterSpacing: 0.8, marginTop: 20, marginBottom: 8 },
  chip: { borderWidth: 1, borderColor: C.line, backgroundColor: C.card, borderRadius: 999, paddingVertical: 10, paddingHorizontal: 16, alignItems: "center" },
  chipOn: { backgroundColor: C.ink, borderColor: C.ink },
  chipText: { color: C.ink, fontSize: 15 },
  chipTextOn: { color: C.bg, fontWeight: "600" },
  input: { backgroundColor: C.card, borderColor: C.line, borderWidth: 1, borderRadius: 14, color: C.ink, padding: 12, fontSize: 15, minHeight: 48, marginBottom: 10 },
  stat: { flexGrow: 1, flexBasis: "30%", backgroundColor: C.card, borderRadius: 12, padding: 10 },
  statValue: { color: C.ink, fontSize: 20, fontWeight: "700" },
  statLabel: { color: C.muted, fontSize: 12, marginTop: 2 },
  btn: { backgroundColor: C.sun, borderRadius: 14, paddingVertical: 15, alignItems: "center" },
  btnGhost: { backgroundColor: "transparent", borderWidth: 1, borderColor: C.line },
  btnDanger: { backgroundColor: "#e5484d" },
  btnText: { color: "#1d1d22", fontSize: 17, fontWeight: "700" },
  pct: { color: C.sun, fontSize: 24, fontWeight: "700" },
  photo: { width: 170, marginRight: 10, backgroundColor: C.card, borderRadius: 12, overflow: "hidden", borderWidth: 1, borderColor: C.line },
  photoTitle: { color: C.ink, fontWeight: "600", fontSize: 13 },
  photoMatch: { color: C.ink, fontWeight: "700", fontSize: 12, marginTop: 4 },
  runPanel: { backgroundColor: C.bg, padding: 16, paddingBottom: 30, borderTopLeftRadius: 20, borderTopRightRadius: 20, marginTop: -20 },
  timer: { color: C.ink, fontSize: 56, fontWeight: "800", textAlign: "center", fontVariant: ["tabular-nums"] },
  offRoute: { color: "#ff8a8a", textAlign: "center", fontWeight: "600", marginBottom: 4 },
});

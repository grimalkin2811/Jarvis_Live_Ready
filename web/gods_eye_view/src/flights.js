/**
 * Flights layer — the only layer of the v1 vertical slice.
 *
 * Architecture (reused from God's Eye View, deliberately for weak GPUs):
 *   - one BillboardCollection of pre-rendered canvas glyphs (no Cesium Entity),
 *   - hard cap of MAX_AIRCRAFT contacts (nearest first),
 *   - poll every POLL_MS with ±20 % jitter, paused while disabled,
 *   - NO per-frame animation: aircraft positions are refreshed only at poll
 *     time, so the render governor stays in idle mode between polls,
 *   - destruction is synchronous on disable (no leak across toggles).
 *
 * Source: adsb.lol public API v2 (data ODbL 1.0), fetched live and never
 * stored. If unreachable (offline PC, rate limit), the layer degrades to a
 * bundled SYNTHETIC sample labelled as such — never to stale live data.
 */
import * as Cesium from 'cesium';

import { governorRequestRender } from './vendor/renderGovernor.js';
import samplePayload from './samples/flights-sample.json';

export const MAX_AIRCRAFT = 600;
const POLL_MS = 10_000;
const FETCH_TIMEOUT_MS = 8_000;
const ADS_LOL_RADIUS_NM = 250;
const ALTITUDE_CLAMP_M = [30, 15_000];

const COLOR_NORMAL = Cesium.Color.fromCssColorString('#7de3f4');
const COLOR_EMERGENCY = Cesium.Color.fromCssColorString('#fb7185');

/** Plane glyph drawn once, tinted per billboard via `color`. */
function makePlaneIcon() {
  const size = 64;
  const canvas = document.createElement('canvas');
  canvas.width = size;
  canvas.height = size;
  const ctx = canvas.getContext('2d');
  ctx.translate(size / 2, size / 2);
  ctx.fillStyle = '#ffffff';
  ctx.beginPath();
  // Fuselage
  ctx.moveTo(0, -26);
  ctx.lineTo(6, -8);
  ctx.lineTo(6, 12);
  ctx.lineTo(14, 20);
  ctx.lineTo(14, 26);
  ctx.lineTo(2, 22);
  ctx.lineTo(0, 26);
  ctx.lineTo(-2, 22);
  ctx.lineTo(-14, 26);
  ctx.lineTo(-14, 20);
  ctx.lineTo(-6, 12);
  ctx.lineTo(-6, -8);
  ctx.closePath();
  ctx.fill();
  return canvas;
}

function toMetersAltitude(row) {
  const raw = row.alt_m ?? (Number.isFinite(row.alt_baro) ? row.alt_baro * 0.3048 : null)
    ?? (Number.isFinite(row.alt_geom) ? row.alt_geom * 0.3048 : null);
  if (!Number.isFinite(raw)) return 10_000;
  return Math.min(ALTITUDE_CLAMP_M[1], Math.max(ALTITUDE_CLAMP_M[0], raw));
}

function normalizeRow(row, index) {
  const lat = Number(row.lat);
  const lon = Number(row.lon);
  if (!Number.isFinite(lat) || !Number.isFinite(lon)) return null;
  if (Math.abs(lat) > 90 || Math.abs(lon) > 180) return null;
  const emergency = String(row.emergency ?? 'none').toLowerCase();
  return {
    id: String(row.hex ?? `row-${index}`),
    callsign: String(row.flight ?? row.hex ?? '—').trim(),
    lat,
    lon,
    altM: toMetersAltitude(row),
    trackDeg: Number.isFinite(Number(row.track)) ? Number(row.track) : 0,
    emergency: emergency !== 'none' && emergency !== '',
  };
}

async function fetchAdsbLol(anchor) {
  const latitude = anchor.latitude.toFixed(3);
  const longitude = anchor.longitude.toFixed(3);
  const url = `https://api.adsb.lol/v2/lat/${latitude}/lon/${longitude}/dist/${ADS_LOL_RADIUS_NM}`;
  const controller = new AbortController();
  const timer = window.setTimeout(() => controller.abort(), FETCH_TIMEOUT_MS);
  try {
    const response = await fetch(url, { signal: controller.signal });
    if (!response.ok) throw new Error(`adsb.lol HTTP ${response.status}`);
    const payload = await response.json();
    const rows = Array.isArray(payload?.ac) ? payload.ac : [];
    return { rows, source: 'adsb.lol' };
  } finally {
    window.clearTimeout(timer);
  }
}

function haversineNm(lat1, lon1, lat2, lon2) {
  const toRad = (d) => (d * Math.PI) / 180;
  const dLat = toRad(lat2 - lat1);
  const dLon = toRad(lon2 - lon1);
  const a =
    Math.sin(dLat / 2) ** 2 +
    Math.cos(toRad(lat1)) * Math.cos(toRad(lat2)) * Math.sin(dLon / 2) ** 2;
  return 3440.065 * 2 * Math.asin(Math.min(1, Math.sqrt(a)));
}

export function createFlightsLayer(viewer, { onChange = null } = {}) {
  let enabled = false;
  let collection = null;
  let timer = null;
  let objectCount = 0;
  let source = 'none';
  let lastError = null;
  let lastUpdated = null;
  let anchor = { latitude: 48.8566, longitude: 2.3522 };
  let icon = null;
  let benchmarkProfile = null;
  let benchmarkOverride = false;

  function applyRows(rows, rowSource, timings = null) {
    if (!collection) return;
    const profileUpdate = benchmarkProfile !== null;
    if (profileUpdate && !timings) timings = {};
    // Rebuild at poll cadence (≤0.1 Hz): removing/re-adding ≤600 billboards
    // is cheaper and leak-proof vs diffing at this scale.
    let stageStart = timings ? performance.now() : 0;
    collection.removeAll();
    if (timings) timings.removeAllMs = performance.now() - stageStart;

    stageStart = timings ? performance.now() : 0;
    const normalized = [];
    for (let i = 0; i < rows.length; i++) {
      const row = normalizeRow(rows[i], i);
      if (row) normalized.push(row);
    }
    if (timings) timings.normalizeMs = performance.now() - stageStart;

    stageStart = timings ? performance.now() : 0;
    if (normalized.length > MAX_AIRCRAFT) {
      // Cap dur : les 600 contacts les plus proches de l'ancre caméra.
      normalized.sort(
        (a, b) =>
          haversineNm(anchor.latitude, anchor.longitude, a.lat, a.lon) -
          haversineNm(anchor.latitude, anchor.longitude, b.lat, b.lon),
      );
      normalized.length = MAX_AIRCRAFT;
    }
    if (timings) timings.sortAndCapMs = performance.now() - stageStart;

    stageStart = timings ? performance.now() : 0;
    for (const row of normalized) {
      collection.add({
        position: Cesium.Cartesian3.fromDegrees(row.lon, row.lat, row.altM),
        image: icon,
        width: 34,
        height: 34,
        color: row.emergency ? COLOR_EMERGENCY : COLOR_NORMAL,
        rotation: -Cesium.Math.toRadians(row.trackDeg),
        alignedAxis: Cesium.Cartesian3.UNIT_Z,
        // Distance LOD : icône pleine près, réduite/fondue lointain (UHD 610).
        scaleByDistance: new Cesium.NearFarScalar(1.0e5, 1.0, 1.5e7, 0.35),
        translucencyByDistance: new Cesium.NearFarScalar(1.5e6, 1.0, 1.5e7, 0.4),
      });
    }
    if (timings) timings.billboardCreationMs = performance.now() - stageStart;
    objectCount = normalized.length;
    source = rowSource;
    lastUpdated = Date.now();
    notify();
    governorRequestRender('flights-poll');
    if (profileUpdate) {
      benchmarkProfile.updates.push({
        atMs: performance.now() - benchmarkProfile.startedAt,
        source: rowSource,
        inputCount: rows.length,
        renderedCount: objectCount,
        timings,
      });
    }
  }

  async function refresh() {
    if (!enabled || !collection) return;
    try {
      const { rows, source: rowSource } = await fetchAdsbLol(anchor);
      // L'utilisateur a pu désactiver pendant le fetch : rien à dessiner.
      if (!enabled || !collection || benchmarkOverride) return;
      lastError = null;
      applyRows(rows, rowSource);
    } catch (error) {
      if (!enabled || !collection || benchmarkOverride) return;
      lastError = `adsb.lol indisponible (${error?.message || error})`;
      // Repli honnête : échantillon synthétique, étiqueté comme tel.
      applyRows(samplePayload.ac, 'synthetic-sample');
    }
  }

  function scheduleNext() {
    if (timer) window.clearInterval(timer);
    if (benchmarkOverride) return;
    // ±20 % jitter pour éviter l'alignement des requêtes.
    const delay = POLL_MS * (0.8 + Math.random() * 0.4);
    timer = window.setTimeout(() => {
      void refresh().finally(scheduleNext);
    }, delay);
  }

  function enable() {
    if (enabled) {
      return { enabled: true, changed: false, objectCount, source };
    }
    enabled = true;
    benchmarkOverride = false;
    notify();
    icon ??= makePlaneIcon();
    collection = new Cesium.BillboardCollection();
    viewer.scene.primitives.add(collection);
    void refresh().finally(scheduleNext);
    return { enabled: true, changed: true };
  }

  function disable() {
    if (!enabled) {
      return { enabled: false, changed: false };
    }
    enabled = false;
    benchmarkOverride = false;
    if (timer) {
      window.clearTimeout(timer);
      timer = null;
    }
    if (collection) {
      viewer.scene.primitives.remove(collection);
      collection.destroy();
      collection = null;
    }
    objectCount = 0;
    lastError = null;
    notify();
    governorRequestRender('flights-disabled');
    return { enabled: false, changed: true };
  }

  function setAnchor(latitude, longitude) {
    anchor = { latitude, longitude };
    if (enabled) void refresh();
  }

  function getState() {
    return { enabled, objectCount, source, lastError, lastUpdated };
  }

  function loadBenchmarkRows(rows) {
    if (!enabled || !collection) throw new Error('Activer la couche flights avant le scénario benchmark');
    if (!Array.isArray(rows)) throw new TypeError('Les lignes benchmark doivent être un tableau');
    benchmarkOverride = true;
    if (timer) {
      window.clearTimeout(timer);
      timer = null;
    }
    const timings = {};
    const startedAt = performance.now();
    applyRows(rows, 'synthetic-benchmark', timings);
    return {
      inputCount: rows.length,
      ...getState(),
      applyRowsMs: performance.now() - startedAt,
      timings,
    };
  }

  function startBenchmarkProfile() {
    benchmarkProfile = { startedAt: performance.now(), updates: [] };
  }

  function getBenchmarkProfile() {
    if (benchmarkProfile === null) return JSON.stringify({ updates: [] });
    return JSON.stringify({
      elapsedMs: performance.now() - benchmarkProfile.startedAt,
      updates: benchmarkProfile.updates,
    });
  }

  function stopBenchmarkProfile() {
    const profile = JSON.parse(getBenchmarkProfile());
    benchmarkProfile = null;
    return JSON.stringify(profile);
  }

  function getBenchmarkDiagnostics() {
    return JSON.stringify({
      enabled,
      objectCount,
      source,
      timerScheduled: timer !== null,
      collectionAlive: collection !== null,
      benchmarkOverride,
    });
  }

  function notify() {
    try {
      onChange?.(getState());
    } catch {
      /* l'observateur ne doit jamais casser la couche */
    }
  }

  return {
    enable,
    disable,
    setAnchor,
    getState,
    loadBenchmarkRows,
    startBenchmarkProfile,
    getBenchmarkProfile,
    stopBenchmarkProfile,
    getBenchmarkDiagnostics,
    destroy: () => disable(),
  };
}

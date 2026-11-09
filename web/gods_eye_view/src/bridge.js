/**
 * JSON bridge between the Jarvis host (PySide6, via QWebEngineView +
 * runJavaScript) and the globe. Single entry point: window.jarvisWorld.handle.
 *
 * Rules:
 *   - action name whitelist enforced here AND in the Python protocol layer;
 *   - arguments re-validated here (never trust the host blindly);
 *   - results are always { ok, action, ... } — never an unhandled rejection;
 *   - no eval, no dynamic import, no arbitrary property access.
 */
import * as Cesium from 'cesium';

import { governorRequestRender } from './vendor/renderGovernor.js';
import { getCameraState } from './viewer.js';
import { resolveRegion, REGION_NAMES } from './regions.js';

const LAYER_IDS = Object.freeze(['flights']);

const CAMERA_LIMITS = Object.freeze({
  altitude_m: [1_000, 40_000_000],
  duration_s: [0, 10],
  heading_deg: [-360, 360],
  pitch_deg: [-89.9, 89.9],
});

function ok(action, extra = {}) {
  return { ok: true, action, ...extra };
}
function fail(action, error, extra = {}) {
  return { ok: false, action, error: String(error?.message || error), ...extra };
}

function clampNumber(value, [min, max], fallback) {
  const n = Number(value);
  if (!Number.isFinite(n)) return fallback;
  return Math.min(max, Math.max(min, n));
}

export function getWorldState({ viewer, governor, layers, base }) {
  const camera = getCameraState(viewer);
  const layerStates = {};
  for (const id of LAYER_IDS) layerStates[id] = layers[id].getState();
  return {
    camera,
    baseLayer: base,
    layers: layerStates,
    governor: governor.diagnostics(),
    credit: [
      base === 'osm' ? '© OpenStreetMap contributors (ODbL 1.0)' : 'Powered by Esri',
      'Flights: adsb.lol (ODbL 1.0)',
    ],
  };
}

export function installBridge({ viewer, governor, layers }) {
  let baseLayer = 'pending';

  function flyCamera({ latitude, longitude, altitude_m, heading_deg = 0, pitch_deg = -35, duration_s = 2.5 }) {
    // Le vol recentre aussi la couche vols sur la nouvelle zone (une seule
    // requête adsb.lol par vol, pas par frame).
    layers.flights.setAnchor(latitude, longitude);
    return new Promise((resolve) => {
      viewer.camera.flyTo({
        destination: Cesium.Cartesian3.fromDegrees(longitude, latitude, altitude_m),
        orientation: {
          heading: Cesium.Math.toRadians(heading_deg),
          pitch: Cesium.Math.toRadians(pitch_deg),
          roll: 0,
        },
        duration: duration_s,
        complete: () => {
          governorRequestRender('camera-flight-complete');
          resolve(true);
        },
        cancel: () => {
          governorRequestRender('camera-flight-cancelled');
          resolve(false);
        },
      });
    });
  }

  const actions = {
    async world_set_view(args = {}) {
      const view = String(args.view ?? 'globe');
      if (view !== 'globe') return fail('world_set_view', `Vue inconnue: ${view}`);
      governorRequestRender('world_set_view');
      return ok('world_set_view', { view: 'globe' });
    },

    async world_set_camera(args = {}) {
      const latitude = Number(args.latitude);
      const longitude = Number(args.longitude);
      if (!Number.isFinite(latitude) || latitude < -90 || latitude > 90)
        return fail('world_set_camera', 'latitude hors bornes (-90..90)');
      if (!Number.isFinite(longitude) || longitude < -180 || longitude > 180)
        return fail('world_set_camera', 'longitude hors bornes (-180..180)');
      const [altMin, altMax] = CAMERA_LIMITS.altitude_m;
      const [durMin, durMax] = CAMERA_LIMITS.duration_s;
      const camera = {
        latitude,
        longitude,
        altitude_m: clampNumber(args.altitude_m, [altMin, altMax], 2_000_000),
        heading_deg: clampNumber(args.heading_deg, CAMERA_LIMITS.heading_deg, 0),
        pitch_deg: clampNumber(args.pitch_deg, CAMERA_LIMITS.pitch_deg, -35),
        duration_s: clampNumber(args.duration_s, [durMin, durMax], 2.5),
      };
      const arrived = await flyCamera(camera);
      return ok('world_set_camera', { camera: getCameraState(viewer), arrived });
    },

    async world_focus_region(args = {}) {
      const regionName = String(args.region ?? '').toLowerCase().trim();
      const region = resolveRegion(regionName);
      if (!region)
        return fail(
          'world_focus_region',
          `Région inconnue: ${regionName || '(vide)'} — attendu: ${REGION_NAMES.join(', ')}`,
        );
      const [altMin, altMax] = CAMERA_LIMITS.altitude_m;
      const altitude_m = clampNumber(args.altitude_m, [altMin, altMax], region.altitude_m);
      const arrived = await flyCamera({
        latitude: region.latitude,
        longitude: region.longitude,
        altitude_m,
        heading_deg: 0,
        pitch_deg: -40,
        duration_s: 2.5,
      });
      return ok('world_focus_region', {
        region: regionName,
        label: region.label,
        camera: getCameraState(viewer),
        arrived,
      });
    },

    async world_enable_layer(args = {}) {
      const layerId = String(args.layer_id ?? '');
      if (!LAYER_IDS.includes(layerId))
        return fail('world_enable_layer', `Couche non autorisée: ${layerId || '(vide)'} — v1: ${LAYER_IDS.join(', ')}`);
      const result = layers[layerId].enable();
      return ok('world_enable_layer', { layer_id: layerId, enabled: true, ...result });
    },

    async world_disable_layer(args = {}) {
      const layerId = String(args.layer_id ?? '');
      if (!LAYER_IDS.includes(layerId))
        return fail('world_disable_layer', `Couche non autorisée: ${layerId || '(vide)'}`);
      const result = layers[layerId].disable();
      return ok('world_disable_layer', { layer_id: layerId, enabled: false, ...result });
    },

    async world_get_state() {
      return ok('world_get_state', { state: getWorldState({ viewer, governor, layers, base: baseLayer }) });
    },
  };

  async function handle(request) {
    const id = request?.id ?? null;
    const action = String(request?.action ?? '');
    const handler = actions[action];
    try {
      if (!handler) return fail(action || 'unknown', `Action inconnue: ${action || '(vide)'}`);
      const result = await handler(request.args ?? {});
      return { id, ...result };
    } catch (error) {
      return { id, ...fail(action, error) };
    }
  }

  // Point d'entrée unique pour l'hôte (runJavaScript). Accepte une string ou
  // un objet (les deux côtés sérialisent en JSON).
  window.jarvisWorld = {
    version: '0.1.0',
    ready: false,
    setBaseLayer(name) {
      baseLayer = name;
    },
    async handle(payload) {
      let request = payload;
      if (typeof payload === 'string') {
        try {
          request = JSON.parse(payload);
        } catch {
          return JSON.stringify(fail('unparseable', 'Charge utile JSON invalide'));
        }
      }
      return JSON.stringify(await handle(request));
    },
  };
  return {
    setBaseLayer(name) {
      baseLayer = name;
    },
    markReady() {
      window.jarvisWorld.ready = true;
    },
  };
}

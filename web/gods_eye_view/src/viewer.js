/**
 * Jarvis God's Eye View — viewer bootstrap.
 *
 * Deliberately minimal vs the upstream app: 3D ellipsoid globe, Esri World
 * Imagery (keyless, attribution required) with automatic OpenStreetMap
 * fallback, no terrain provider, no photogrammetry, governed render loop.
 * The Esri/OSM base stack mirrors God's Eye View's defaultSources.js design
 * (createDefaultMapSources) with everything else removed.
 */
import * as Cesium from 'cesium';

import {
  installRenderGovernor,
  governorRequestRender,
  holdContinuousRender,
  releaseContinuousRender,
} from './vendor/renderGovernor.js';

export const ESRI_IMAGERY_URL =
  'https://services.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer';
export const OSM_IMAGERY_URL = 'https://tile.openstreetmap.org/';

function observeImageryProvider(provider, source, onStatus) {
  let errors = 0;
  provider.errorEvent.addEventListener(() => {
    errors++;
    onStatus?.({ source, errors, state: 'error' });
  });
  onStatus?.({ source, errors, state: 'loading' });
}

export async function createBaseImagery(viewer, { onStatus = null } = {}) {
  try {
    const provider = await Cesium.ArcGisMapServerImageryProvider.fromUrl(
      ESRI_IMAGERY_URL,
      {
        credit: new Cesium.Credit(
          'Powered by Esri — Source: Esri, Maxar, Earthstar Geographics, and the GIS User Community',
        ),
        enablePickFeatures: false,
      },
    );
    observeImageryProvider(provider, 'esri', onStatus);
    viewer.imageryLayers.add(new Cesium.ImageryLayer(provider));
    governorRequestRender('base-imagery:esri');
    return 'esri-imagery';
  } catch (error) {
    console.warn('[GodsEyeView] Esri indisponible, repli OpenStreetMap', error);
    const provider = new Cesium.OpenStreetMapImageryProvider({
      url: OSM_IMAGERY_URL,
      credit: '© OpenStreetMap contributors',
    });
    observeImageryProvider(provider, 'osm', onStatus);
    viewer.imageryLayers.add(new Cesium.ImageryLayer(provider));
    governorRequestRender('base-imagery:osm');
    return 'osm';
  }
}

export function createViewer(containerId = 'jarvis-globe') {
  const container = document.getElementById(containerId);
  if (!container) throw new Error(`Conteneur #${containerId} introuvable`);

  const viewer = new Cesium.Viewer(container, {
    baseLayer: false, // ajouté par createBaseImagery (Esri → OSM repli)
    baseLayerPicker: false,
    geocoder: false,
    homeButton: false,
    sceneModePicker: false,
    navigationHelpButton: false,
    animation: false,
    timeline: false,
    fullscreenButton: false,
    vrButton: false,
    infoBox: false,
    selectionIndicator: false,
    creditContainer: 'jarvis-credits',
    terrainProvider: new Cesium.EllipsoidTerrainProvider(),
    requestRenderMode: true,
    maximumRenderTimeChange: Infinity,
    msaaSamples: 1,
    stencil: false,
    // Le globe Jarvis ne doit jamais progresser en simulation : rien ne doit
    // déclencher un rendu hors action explicite (governor).
    useDefaultRenderLoop: true,
    contextOptions: {
      webgl: {
        preserveDrawingBuffer: false,
        // UHD 610: fill-rate first; MSAA off, no temporal effects.
        antialias: false,
      },
    },
  });

  const scene = viewer.scene;
  scene.globe.maximumScreenSpaceError = 2.5;
  scene.globe.dynamicScreenSpaceError = true;
  scene.globe.showGroundAtmosphere = true;
  scene.skyAtmosphere.show = true;
  scene.screenSpaceCameraController.minimumZoomDistance = 1000;
  scene.screenSpaceCameraController.maximumZoomDistance = 40_000_000;
  // Pas d'éclairage coûteux : style "Jarvis" plat/sombre.
  scene.highDynamicRange = false;

  installRenderGovernor(viewer);

  // Hold uniquement pendant un mouvement caméra effectif (input ou flyTo) ;
  // libérée à moveEnd. Entre deux actions, le governor reste en idle.
  viewer.camera.moveStart.addEventListener(() => holdContinuousRender('world-camera'));
  viewer.camera.moveEnd.addEventListener(() => {
    releaseContinuousRender('world-camera');
    governorRequestRender('camera-settled');
  });

  viewer.camera.setView({
    destination: Cesium.Cartesian3.fromDegrees(2.3522, 48.8566, 18_000_000),
  });

  return viewer;
}

export function getCameraState(viewer) {
  const cartographic = Cesium.Cartographic.fromCartesian(viewer.camera.position);
  return {
    latitude: Cesium.Math.toDegrees(cartographic.latitude),
    longitude: Cesium.Math.toDegrees(cartographic.longitude),
    altitude_m: cartographic.height,
    heading_deg: Cesium.Math.toDegrees(viewer.camera.heading),
    pitch_deg: Cesium.Math.toDegrees(viewer.camera.pitch),
  };
}

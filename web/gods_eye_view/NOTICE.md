# Tiers — God's Eye View module (web/gods_eye_view)

This module is MIT-licensed like the rest of Jarvis EXCEPT for the third-party
components listed here. Keep this file and the inline copyright headers when
redistributing built artifacts. Human-readable attribution is also rendered
in-app (see the bottom-left credit line of the globe).

## God's Eye View (engine, render governor, layer architecture, data providers)

- Source: https://github.com/bilawalsidhu/gods-eye-view (v0.3.0, commit 581ef7e)
- License: MIT — Copyright (c) 2026 Bilawal Sidhu
- Reused here: `src/vendor/renderGovernor.js` (verbatim, MIT), the
  requestRenderMode/requestRender discipline, the BillboardCollection layer
  pattern, and the Esri-imagery-with-OSM-fallback base stack design.
- The MIT grant covers God's Eye View **source code only**. No data file,
  model or icon from the upstream repository is redistributed here except as
  listed below. TeleGeography / Bhote Koshi / ODbL-bundled datasets are NOT
  shipped (see docs/gods-eye-view-integration.md §1).

## CesiumJS

- Source: https://cesium.com/platform/cesiumjs/ (npm `cesium`)
- License: Apache-2.0 — Copyright Cesium Contributors / Analytical Graphics, Inc.
- https://github.com/CesiumGS/cesium/blob/main/LICENSE.md

## Map imagery (rendered at runtime, never redistributed)

- Esri World Imagery (default): « Powered by Esri » — Esri, Maxar, Earthstar
  Geographics and the GIS User Community. Shown permanently in-app.
- OpenStreetMap (fallback): © OpenStreetMap contributors — ODbL 1.0
  (https://www.openstreetmap.org/copyright)
- Esri/OSM tiles are streamed by the client; Jarvis does not redistribute them.

## Live flight data (fetched at runtime, never stored)

- adsb.lol API v2 — data ODbL 1.0 (https://adsb.lol/ · https://odbl.org/).
  Fetched live only; attribution « adsb.lol » shown in-app while the layer is
  enabled. If adsb.lol is unreachable the layer degrades to the bundled
  synthetic sample (clearly labelled « ÉCHANTILLON »), not to cached live data.

## Bundled sample data

- `src/samples/flights-sample.json` is **synthetic** (callsigns SAMxxx and
  fake positions generated for UI development/offline demos). It contains no
  real aircraft observations and is CC0.

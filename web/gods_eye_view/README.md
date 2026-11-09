# God's Eye View — module web Jarvis (`web/gods_eye_view`)

Globe géospatial OSINT embarqué dans Jarvis (fork réduit et Jarvis-native du
projet [God's Eye View](https://github.com/bilawalsidhu/gods-eye-view),
MIT © 2026 Bilawal Sidhu — voir `NOTICE.md`).

Ce dossier contient **uniquement le code d'intégration** : aucun asset tiers
n'est vendoré à l'exception de `src/vendor/renderGovernor.js` (MIT) et d'un
échantillon **synthétique** (`src/samples/`). Les tuiles Esri/OSM et les vols
adsb.lol sont chargés **à l'exécution**, jamais redistribués.

## Build

```bash
cd web/gods_eye_view
npm install     # registry.npmjs.org uniquement
npm run build   # produit dist/ (non versionné)
```

Prérequis : Node ≥ 22 (le PC de développement Jarvis actuel utilise Node 22).

## Développement

```bash
npm run dev     # vite dev server sur 127.0.0.1:5173
```

## Architecture

| Fichier | Rôle |
|---|---|
| `src/main.js` | boot : viewer → imagerie de base → bridge → ready |
| `src/viewer.js` | Viewer Cesium governé (`requestRenderMode`), globe Ellipsoid, Esri → OSM |
| `src/flights.js` | couche vols (BillboardCollection, cap 600, poll 10 s ± jitter) |
| `src/bridge.js` | `window.jarvisWorld.handle(json)` — whitelist d'actions `world_*` |
| `src/regions.js` | table fermée des régions nommées (`world_focus_region`) |
| `src/vendor/renderGovernor.js` | render governor GEV (MIT, notice conservée) |

## Protocole (JSON, les deux sens)

Hôte → module :

```json
{ "id": "r1", "action": "world_enable_layer", "args": { "layer_id": "flights" } }
```

Module → hôte :

```json
{ "id": "r1", "ok": true, "action": "world_enable_layer", "layer_id": "flights", "enabled": true, "changed": true }
```

Actions : `world_set_view`, `world_set_camera`, `world_focus_region`,
`world_enable_layer`, `world_disable_layer`, `world_get_state`.
Fermeture : Échap / bouton FERMER → `window.jarvisHost.closeRequested()` si
l'hôte l'expose (channel optionnel).

## Licence

Voir `NOTICE.md`. Le code propre à Jarvis dans ce dossier est MIT comme le
reste de l'intégration (cf. `docs/gods-eye-view-integration.md`).

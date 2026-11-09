# God's Eye View × Jarvis — Integration Audit & Plan

**Date :** 2026-11-09
**Branche de travail :** `arena/961d92ed-jarvis-live-ready` (pointe sur `main` = `ec944e26457b22492b5872ee520d6bb3ce410081`, tree propre)
**Amont étudié :** `bilawalsidhu/gods-eye-view` @ `581ef7e` (depth 1, cloné en lecture seule hors dépôt, jamais copié
tel quel)
**PR #51 / `feature/v1.9.0` :** non modifiée, non fusionnée (`OPEN`, base `main`, HEAD `43ff0029…`)
**Module :** « God's Eye View » (nom de travail ; le nom commercial peut changer plus tard)

> Ce document est la trace d'audit demandée par la mission. Il est écrit **avant** tout code
> d'intégration. Les mesures annoncées sont soit issues des documents publics du dépôt amont
> (citées et encadrées comme telles), soit mesurées localement dans ce sandbox (et étiquetées
> comme telles). Aucune mesure navigateur/GPU n'est fabriquée : ce sandbox est headless, sans
> Chrome ni WebGL, et le PC cible (Pentium Gold G5400 / UHD 610 / 16 Go) n'est pas mesurable ici.

---

## 1. Licence / droits

### 1.1 Règle de base

Le LICENSE de God's Eye View est explicite : **le MIT ne couvre que le code source**.
Les données embarquées (`src/data/local_data/`), les modèles 3D (`public/models/`) et tout ce
qui est récupéré chez des tiers **ne sont pas MIT**. Le fichier `DATA_SOURCES.md` et
`public/models/README.md` font foi, source par source.

### 1.2 Éléments retenus pour le module Jarvis (prototype + v1)

| Élément | Provenance | Licence | Modif. | Redistribution | Attribution | Restriction commerciale | Partage des modifs | Compat. Jarvis* |
|---|---|---|---|---|---|---|---|---|
| Code source GEV (moteur, layers, render governor, viewer, embed) | bilawalsidhu/gods-eye-view | **MIT** © 2026 Bilawal Sidhu | ✅ | ✅ (notice conservée) | Notice copyright conservée | Aucune | Non | ✅ |
| `src/renderGovernor.js` (vendoré tel quel) | GEV | MIT | ✅ | ✅ | Notice dans le fichier + `NOTICE.md` | Aucune | Non | ✅ |
| Cesium | npm `cesium ^1.124.0` | **Apache-2.0** | ✅ | ✅ | NOTICE + crédit Cesium affiché (obligatoire sur les tuiles ion/Google, courtoisie sinon) | Aucune | Non (sauf fichiers modifiés du SDK) | ✅ |
| vite / vite-plugin-cesium / @vitejs/plugin-vue | npm | MIT | ✅ | ✅ | NOTICE | Aucune | Non | ✅ |
| Imagerie de base **Esri World Imagery** (défaut keyless) | ArcGIS Online | Esri ToS — crédit requis | n/a | n/a (streaming tuiles) | **« Powered by Esri » affiché en permanence** + fallback OSM auto sur échec | Usage grand public raisonnable ; pas de redistribution des tuiles | n/a | ✅ (affichage uniquement) |
| **OpenStreetMap** (fallback + données ODbL) | OSM | **ODbL 1.0** | ✅ | ✅ | « © OpenStreetMap contributors » | Oui, sous conditions ODbL (share-alike *des données*, accès aux données dérivées) | Données dé-rivées sous ODbL | ⚠️ voir 1.4 |
| **adsb.lol** (API publique v2, fallback Flights) | adsb.lol | données ODbL 1.0 | n/a (streaming) | ❌ ne pas redistribuer le flux | « adsb.lol » + ODbL | Usage commercial toléré si ODbL respecté | Données sous ODbL | ⚠️ fetch live uniquement |
| **Natural Earth** (regions, populated_places) | nvkelso/natural-earth-vector | **Domaine public** | ✅ | ✅ | Courtoisie « Made with Natural Earth » | Aucune | Non | ✅ |
| **Yale Bright Star Catalogue** (sky) | GEV `local_data/sky` | Domaine public | ✅ | ✅ | Courtoisie | Aucune | Non | ✅ (si sky réutilisé) |
| **ESO Milky Way panorama** (sky) | eso0932a | **CC BY 4.0** | ✅ | ✅ | « ESO/S. Brunier » + lien, modifications signalées | Oui, avec attribution | Non | ✅ avec attribution |
| Modèles 3D `airplane.glb`, `jet.glb`, `bell206.glb`, `c172.glb`, `citation2.glb`, `mq9.glb`, `b789.glb`, `atr72.glb` | Sketchfab via GEV | **CC BY 4.0** (par modèle) | ✅ | ✅ | **Attribution par modèle (auteur, source, lien, modifs)** | Oui, avec attribution | Non | ⚠️ attribution requise — voir `public/models/README.md` amont |
| **UCS Satellite Database** 5-1-2023 | ucsusa.org | Libre, usage non restreint | ✅ | ✅ | Recommandée (version + lien) | Aucune | Non | ✅ |
| **NASA FIRMS** (live) | NASA EOSDIS | **CC0 / domaine public US** | n/a | n/a | Citation demandée | Aucune | Non | ✅ (clé MAP_KEY gratuite requise) |
| **NOAA GFS / nowCOAST / NHC / cyclone** | NOAA/NWS | Domaine public US | n/a | n/a | Courtoisie | Aucune | Non | ✅ |
| **ECMWF IFS (vent)** | ECMWF | **CC BY 4.0** + Terms of Use | n/a | n/a | Mention ECMWF + lien CC BY + notice modifs + disclaimer liability | Oui, avec attribution | Non | ⚠️ attribution requise |

\* Jarvis (dépôt) n'a **aucun fichier LICENSE** : il est *de facto* propriétaire/tous droits réservés
jusqu'à décision du mainteneur. Toute réutilisation de données share-alike/NC doit donc rester
derrière une décision explicite du mainteneur (voir §1.4).

### 1.3 Éléments exclus ou REVIEW_REQUIRED (ne **pas** intégrer par défaut)

| Élément | Licence | Statut |
|---|---|---|
| **TeleGeography Submarine Cable Map** (`local_data/telegeography_submarine_cables/`, ~1,1 Mo) | CC BY-NC-SA 3.0 | **BLOCKED** — NonCommercial + ShareAlike. Exclus du module Jarvis ; réintégration seulement avec licence commerciale ou accord explicite du mainteneur |
| **Bhote Koshi event pack** (`public/events/bhote-koshi-2026/`, imagerie Vantor / centre de rivière GeoPera) | **CC BY-NC 4.0** | **BLOCKED** — NonCommercial. Exclus, y compris la donnée de coordonnées embarquée `src/data/bhoteKoshiFloodPath.js` (≠ MIT) |
| **Datacenters OSM** (`local_data/datacenters/`, 4 352) | ODbL 1.0 | **REVIEW_REQUIRED** — attribution + share-alike sur la donnée ; OK à afficher avec crédit, redistribution du dataset modifié sous ODbL |
| **Dams (OpenInfraMap)** (`local_data/dams/`, 704) | ODbL 1.0 | **REVIEW_REQUIRED** — idem |
| **Military area names OSM/Overture** (`local_data/osm_military_names/`, 36 466) | ODbL 1.0 | **REVIEW_REQUIRED** — idem |
| **Who's On First neighborhoods** (`local_data/wof_neighborhoods/`, 61 441) | mixte CC BY / ODC-By / OGL / CC0 / PDDL | **REVIEW_REQUIRED** — attribution par source (`ATTRIBUTION.md` du dossier amont) à embarquer si activé |
| **OpenSky Network** (vols mondiaux) | Licence recherche/éducation non commerciale | **BLOCKED** pour distribution ; utiliser adsb.lol / opensky seulement en usage personnel de dev |
| **Google Photorealistic 3D Tiles / Places / Geocoding / Street View** | Google Maps Platform ToS (propriétaire) | **BLOCKED** par défaut — clé + facturation + ToS ; réactivable uniquement en opt-in explicite avec clés utilisateur |
| **Mapillary / KartaView / Panoramax / Panoramax imagery** | CC BY-SA 4.0 + ToS variables | **REVIEW_REQUIRED** (attribution + SA + ToS par fournisseur) — hors périmètre v1 |
| **TomTom Traffic** | ToS propriétaire | **REVIEW_REQUIRED** — clé requise |
| **CCTV packs** (`config/cctv_sources.*.json`, catalogs world/Quebec/…) | Mixte : CC BY 4.0 (MTQ), ODbL (Montréal/OSM), MLIT, ODOT, termes opérateurs | **REVIEW_REQUIRED** par source ; les *frames* restent chez l'opérateur — ne rien mettre en cache/redistribuer |
| **AISStream** | ToS (streaming) | **REVIEW_REQUIRED** — clé ; données live uniquement |
| **Radio Browser** | PDDL 1.0 (annuaire) | ✅ catalogue ; streams individuels sous les termes du diffuseur |
| **Transit GTFS-RT** (multiple feeds) | Mixte : Licence Ouverte 2.0, ODbL, CC BY 4.0, non précisé (Carris/Metro Transit/OVapi) | **REVIEW_REQUIRED** par feed ; Tours Fil Bleu (Licence Ouverte 2.0) noté pour un usage local |
| **OpenSeaMap / OpenSnowMap** (tuiles) | CC BY-SA 2.0 / CC BY-SA + ODbL | **REVIEW_REQUIRED** — attribution + interdiction bulk download |
| **ECMWF** (si Wind activé) | CC BY 4.0 + ToU | Attribution + disclaimers exacts à conserver in-app |

### 1.4 Points durs et décisions

1. **Aucun élément NonCommercial ne doit entrer dans le build Jarvis par défaut**
   (TeleGeography, Bhote Koshi). C'est le point juridique le plus facile à violer par copie
   aveugle — il est donc traité par *non-copie* : rien de ces dossiers n'est vendoré.
2. **ODbL** n'interdit pas l'usage ; il impose l'attribution et le share-alike *sur la base
   de données* (pas sur le code). Tant que les datasets OSM ne sont pas **redistribués** par
   Jarvis (fetch live ou non activés), le risque est faible. S'ils sont embarqués
   (datacenters/dams), il faut : crédit « © OpenStreetMap contributors » permanent + notice
   ODbL + offre des données sous ODbL. **Décision prototype : aucun dataset ODbL embarqué.**
3. **Les notices de copyright GEV ne doivent pas être retirées.** Le module vendoré
   (`renderGovernor.js`) conserve son en-tête ; un `NOTICE.md` est ajouté sous
   `web/gods_eye_view/`.
4. Jarvis n'a pas de licence : la distribution des binaires embarquant des données ODbL/CC BY
   reste possible, mais la qualification juridique exacte relève du mainteneur — le document
   liste précisément ce qui est requis (attributions) pour rester clair.

### 1.5 Verdict licence

**GO conditionnel.** Le code MIT et l'écosystème permissif (Cesium Apache-2.0, Natural Earth
PD, Yale PD, UCS, NASA CC0, Esri/OSM avec crédits) suffisent pour un module fonctionnel.
TeleGeography / Bhote Koshi / OpenSky-commercial / Google Photoreal sont **exclus du défaut**.
Tout ajout futur d'une couche de §1.3 exige une revue explicite et une ligne dans ce tableau.

---

## 2. Cartographie God's Eye View (GEV)

Dépôt amont : https://github.com/bilawalsidhu/gods-eye-view @ `581ef7e` (v0.3.0),
~159 Mo hors `.git` dont **77 Mo de `src/data/`**. Vue d'ensemble :

```
GOD'S EYE VIEW
├── core/runtime        src/app/ (application.js, viewer.js, embed.js, catalog.js, operations.js)
│                       src/main.js, src/mapStartup.js, src/mapLifecycle*, stateChannel.js
├── rendering           src/renderGovernor.js (requestRenderMode gouverné), src/bloom.js,
│                       src/visualStylePolicy.js, src/camera.js, src/cameraVerbs.js,
│                       src/cameraGroundGuard.js, src/globeInView.js, src/zoomOutLimit…
├── Cesium              cesium ^1.124.0 via vite-plugin-cesium ; SceneMode 3D/2D/Columbus ;
│                       primitives (BillboardCollection / PrimitiveCollection), pas Entity
├── layers              src/layers/*/ (41 dossiers) : flights, military, vessels, satellites,
│                       earthquakes, cctv, submarineCables, datacenters(data), firms, radio,
│                       weather, traffic, transit, streetLevel, gnss, alpr, bikeshare…
├── data providers      server/providers/* (Node) : /api/* proxies (adsbl, opensky, ais, firms,
│                       traffic, overpass, cctv catalogs, openai, …) — clé côté serveur
├── UI                  Vue 3 + Pinia + vue-i18n (2,6 Mo locales) — HUD, cockpit, rails,
│                       panneaux ; src/ui/* ; src/styles
├── tools / agent       src/tools/ (index.js, mcp, panel) ; src/agent/worldPacket.js
│                       (« world packet » = état géospatial compact pour le LLM)
├── voice               src/voice/* (realtimeController, gevActions.js 5 607 lignes,
│                       actionSchemas.js, commands.js, session, panel) — OpenAI Realtime +
│                       Gemini ; double chemin de transport
├── context             src/data/contextStore.js, contextModePolicy.js, worldFocus.js,
│                       analystEngine.js — « context mode » = LLM pilote la caméra
├── maps                src/maps/* (catalog.js MAP_STACKS, controller.js 618 l., imagery.js,
│                       terrain.js, google3d.js, defaultSources.js)
├── share/scenes        src/share/viewCodec.js, src/director/*, src/scenes/* — liens de partage,
│                       scénarios/« director », timelines
├── search              src/search/* (Nominatim, Pelias? — nominatim.js, index.js)
├── build               build/vite.js (CSP stricte, embed-framing, panel) ; vite.config.js →
│                       server/standalone/vite.config.js ; build-panel.mjs (panel MCP)
├── server              server/ (providers, mcp, standalone) — Node ≥24 requis (engines)
└── docs                25 docs (PERFORMANCE.md, CURRENT-STATE.md 7 512 lignes, KNOWN-ISSUES,
                        APPLICATION.md, TOOLS.md, …)
```

### 2.1 Constats d'architecture amont (utiles pour l'intégration)

1. **Embed mode natif** : `?embed=1` + `globalThis.GEV_EMBED_INLINE` → `installViews()`
   (`src/app/embed.js`) :
   - vue « propre » (HUD coupé), restreinte au globe ;
   - protocole postMessage documenté : `gev:view` → `applyView()` → `gev:view-applied`
     (+`steps[]`) ; annonce `gev:ready` ;
   - la vue = `{ style, map, layers[], camera, annotations[], follow? }` validée par
     `createView()` (`src/view/`) ; chaque étape passe par l'action runner applicatif
     (`run(name, args)`), pas par des appels internes ad hoc ;
   - réponses adressées à l'origine demandeuse ; file (`queue`) sérialisée ;
   - `keepPanelRendering()` : si l'hôte rapporte le panneau comme « hidden », un timer 33 ms
     re-`resize()`/`render()` — à connaître avant tout portage dans un WebView.
2. **Action runner applicatif** (`src/voice/gevActions.js::createGevActionRunner`) : un seul
   point d'entrée `run(name, args)` avec ~150 actions (`set_layer_visibility`,
   `fly_to_location`, `track_entity`, `annotate_map`, `control_cockpit`…), résultats
   structurés `{ ok, action, … }`, attente de settle de lifecycle, `signal`/`isCurrent()`
   pour annulation coopérative. C'est **exactement** la surface qu'un LLM doit piloter.
3. **Layer lifecycle** (`src/data/lifecycle.js::LayerLifecycle`, 2 418 lignes) : transitions
   d'état validées, epochs d'intent, cancel/supersede coopératifs, sérialisation des options,
   résumés de lifecycle (`readLayerLifecycleSummary`) — l'infra existe pour des erreurs
   propres et idempotentes.
4. **Render governor** (`src/renderGovernor.js`) : voir §5. Mode binaire continu/idle piloté
   par des *holds* identité-clé (Set), O(1), zéro travail par frame. Entrée/sortie d'idle
   déclenchée par `maximumRenderTimeChange = Infinity` + `requestRenderMode` + `requestRender()`.
5. **Rendu par primitives** : les couches denses (flights, vessels) utilisent
   `BillboardCollection` / `PrimitiveCollection`, **pas** l'API Entity ; les labels Cesium sont
   quasi-bannis (`noCesiumLabels.test.mjs` + snapshotRenderer). Les icônes sont pré-rendues en
   canvas (`aircraftIcons.js`).
6. **Serveur Node requis** pour les sources proxifiées (`/api/*`) avec clés ; le navigateur ne
   parle jamais directement aux fournisseurs (sauf tuiles/keyless compatibles CORS).
7. **Engines** : `node >=24.14 <25 || >=26 <27`. Notre sandbox : node v22.22.3 → un build du
   dépôt amont complet n'est **pas supporté** ici ; le sous-module Jarvis visé n'en dépend pas
   (Vite 6 accepte node 22).
8. **Tests** : `node --test` + happy-dom ; harnais perf opt-in `*.perf.test.mjs` avec budgets
   (`GEV_PERF_BUDGETS=1`).

### 2.2 Classification A/B/C/D/E (composants à traiter un par un)

**A — réutiliser presque tel quel (vendoré ou reconstruit à l'identique, notice conservée)**
- `src/renderGovernor.js` (MIT) → vendoré dans `web/gods_eye_view/src/vendor/` (voir §8).
- Discipline `requestRenderMode` + holds — réutilisée via le vendor.
- `src/share/viewCodec.js` + `src/view/` — concept réutilisé (codec de vue JSON) ; code non
  vendoré tel quel (dépendances), mais le format `gev:view` est aligné.
- `src/maps/defaultSources.js`, `imagery.js`, `terrain.js` — logique keyless (Esri défaut +
  fallback OSM + terrain keyless/ion) reprise telle quelle en adaptation ESM minimale.
- Esri/OSM/Natural Earth credits — repris tels quels.
- Tests happy-dom + `node --test` — même outillage pour le sous-module.

**B — adapter**
- `src/app/embed.js` → protocole bridge Jarvis (`jarvis:world:*`), même sémantique
  (ready/result, file sérialisée, origine vérifiée), sans `run()` universel — **whitelist**.
- `src/app/viewer.js` → viewer Jarvis : `SceneMode.SCENE3D`, `requestRenderMode`,
  `maximumRenderTimeChange=Infinity`, `msaaSamples=1`, crédit Esri, *credit display* compact.
- `src/data/lifecycle.js` — **pattern** repris sous forme réduite (états idle/loading/ready/
  closing/error + idempotence), pas le fichier de 2 418 lignes (couplé share-links/epochs).
- `src/layers/flights/` — architecture (billboards + poll borné + interpolation légère) ;
  réécriture *réduite* (source adsb.lol keyless + fallback échantillon, cap dur 600,
  `nearFarScalar`, labels off). Aucun fichier copié : la couche est réécrite contre l'API
  Cesium en respectant les invariants (billboard collection, pas d'Entity, rotation par
  sprite, cul-ing par frustum Cesium).
- `src/voice/actionSchemas.js` → sous-ensemble `world_*` au format Jarvis (`_decl` existant).
- `src/data/dataCredits.js` → attribution Esri/adsb.lol/OSM dans le HUD Jarvis (ligne crédit).
- BUILD (vite + plugin-cesium) — repris pour le sous-module.

**C — remplacer**
- Toute la **chrome GEV** (Vue app, cockpit, HUD futuriste, rails, panneaux, vue cockpit
  3D, styles CRT/NVG/FLIR…) → le HUD Jarvis existant (PySide6 : menu radial, overlay desktop).
- Le **serveur Node providers** → fetch direct keyless côté module web (CORS) + fallback Python
  (phase 2 : mini-proxy local `127.0.0.1` dans Jarvis si besoin d'une source non-CORS).
- La **voice stack GEV** (OpenAI Realtime, panel voix, session) → Gemini Live de Jarvis
  (`src/gemini_live.py` + tools) — c'est tout l'intérêt de l'intégration.
- `src/agent/worldPacket.js` → remplacé par `world_get_state` (Jarvis assemble déjà le contexte
  conversationnel ; le « world packet » complet peut revenir plus tard comme outil dédié).

**D — inutile pour Jarvis v1 (abandonnés)**
- Cockpit 3D (plates, VR, orbital), starfield Bloom, FLIR/NVG/CRT/Snow/Noir, directed
  « Director » scenes, share links, MCP panel, `src/sdr/`, street-level Mapillary/Google,
  CCTV, radio browser, transit, bikeshare, ALPR, ghostmaps, cyclones/wildfires (réévaluables
  plus tard couche par couche), i18n complet (Jarvis est FR-first), Vue/Pinia entier.

**E — licence / juridique bloquant → non intégrés (voir §1.3)**
- TeleGeography cables, Bhote Koshi (NC), OpenSky (NC recherche), Google 3D Tiles/Places
  (ToS propriétaire), TomTom, datasets ODbL embarqués (datacenters/dams/military names —
  seulement en opt-in avec notices), Mapillary.

---

## 3. Architecture de Jarvis (branche `main` = ec944e2)

jarvis_live_ready est un assistant vocal **Windows** Python 3.11+ / **PySide6** :

```
run_jarvis.py → src/main.py
  ├── première exécution : src/first_run.py (setup)
  ├── voix : src/wakeword.py (openwakeword « Hey Jarvis ») → src/audio.py (sounddevice)
  │           → src/gemini_live.py (google-genai Live) → src/tools.py (TOOL_DECLARATIONS
  │           ≈ 100 outils) → src/conversation.py (contexte multi-tours)
  │           → src/memory.py (mémoire persistante), src/routines.py, src/scheduler.py,
  │             src/modes.py (focus/jeu), src/music/* (Deezer), src/writing/*
  ├── UI : UI/jarvis_menu.py (plateau radial 3 431 l., MorphingOrbWidget, MenuSpec) ;
  │        UI/desktop/* (overlay « Desktop Mode » : halo, widgets, états, Appearance) ;
  │        src/ui.py (InterfaceModeController blob/desktop, SessionVisibility) ;
  │        UI/boot_sequence.py, UI/notification_bridge.py, UI/background_tasks_dialog.py …
  └── packaging : packaging/jarvis.spec (+ launcher.spec) — onedir PyInstaller ;
                  **QtWebEngineCore/QtWebEngineWidgets sont dans `excludes`** ;
                  openwakeword embarqué ; VERSION via version.py ; installeur Inno Setup
```

Points structurants pour le module :

- **Pas de frontend web** aujourd'hui : toute l'UI est Qt native. **PR #51** (`feature/v1.9.0`,
  « Dashboard natif ») ajoute un dashboard Qt dans une autre branche — non touchée ici.
- **Outils LLM** : `src/tools.py::TOOL_DECLARATIONS` (+ `_decl`) est la source unique ;
  `gemini_live.py` les convertit en `FunctionDeclaration` ; `TOOL_FUNCTIONS` exécute ;
  chaque outil renvoie `{"success": bool, ...}` ; règles blanches strictes (applications
  autorisées, dossiers autorisés, `confirm=True` pour les actions destructrices).
- **Threading** : la boucle asyncio tourne dans un thread vocal dédié ; l'UI Qt vit sur le
  thread principal. Tout outil qui touche l'UI doit être re-marshalé sur le thread Qt
  (pattern existant : signaux Qt / `QTimer`). Nous ne touchons pas à cette mécanique — nos
  outils `world_*` passeront par un dispatcher enregistré par la fenêtre du module.
- **Thème** : hiérarchie de couleurs Jarvis (violet/cyan, `--glow`) définie dans
  `UI/desktop/design.py` et `UI/jarvis_menu.py` (teintes HSL dérivées) ; le module web doit
  reprendre *les mêmes rôles* (fond quasi-noir, accent cyan/violet) sans copier le CSS GEV.
- **Cycle de vie UI** : widgets Qt parenté ; `closeEvent` = destruction ; pas de leak attendu
  si la fenêtre est détruite proprement (le moteur web doit être `deleteLater()` + `page`
  détruite avant `QApplication` arrêtée).
- **Packaging** : `jarvis.spec` exclut QtWebEngine* (payload +~150-250 Mo). Tout choix WebView
  impose la levée de cette exclusion — **décision packaging séparée**, documentée, non appliquée
  dans ce chantier (voir §4.3 et §12).

## 4. Architecture d'intégration — options et choix

### 4.1 Alternatives évaluées

| Option | Description | Perf | Maintenabilité | Contrôle LLM | Lifecycle | Sécurité | Packaging | Score |
|---|---|---|---|---|---|---|---|---|
| A. Frontend direct | Impossible : aucun frontend web dans Jarvis | — | — | — | — | — | — | Rejeté (N/A) |
| B. **WebView embarquée (QWebEngineView)** | Module web statique (build Vite) servi en `file://`/`http://127.0.0.1` local, hébergé dans un QWidget Jarvis | ★★★★ (GPU natif du PC, 0 iframe) | ★★★★ (frontière claire Python↔JS, protocole JSON) | ★★★★★ (pont JSON explicite, whitelist) | ★★★★ (create/destroy Qt natif) | ★★★★★ (contenu 100 % local, aucun réseau entrant ; CSP) | ★★ (+QtWebEngine, exclu aujourd'hui) | **RETENUE** |
| C. Sous-application externe | GEV lancé en process/app séparé | ★★★ | ★★ | ★★ (localhost + token) | ★★ (2 lifecycles) | ★★ (surface réseau, CORS, token) | ★ | Rejeté : ≠ « composant natif », 2 process, packaging double |
| D. Module séparé + API interne (Python natif) | Réécrire le globe en Qt (pydeck/QGraphicsView…) | ★ (perf Cesium perdue) | ★ (réimplémentation complète) | ★★★★ | ★★★★ | ★★★★ | ★★★★★ | Rejeté : viole « réutiliser au maximum » |
| E. iframe/navigateur | Hors mission | — | — | — | — | — | — | Rejeté explicitement |

### 4.2 Choix recommandé — B « WebView embarquée + protocole JSON explicite »

```
┌────────────────────────── Jarvis (PySide6, thread UI) ─────────────────────────┐
│  src/gods_eye/ (Python)                                                       │
│   ├── protocol.py   actions/état JSON, validation pure (testable sans Qt)     │
│   ├── controller.py GodsEyeController : cycle de vie + idempotence + polling   │
│   │                 borné, zéro double-abonnement, stats de dispatch          │
│   ├── tools.py      déclarations world_* (format TOOL_DECLARATIONS de Jarvis) │
│   └── widget.py     GodsEyeViewWindow(QWidget) — charge QWebEngineView       │
│                        (fallback placeholder si QtWebEngine indisponible)      │
│        │ runJavaScript(payload) / callback JSON                               │
│════════╪══════════════════════════════════════════════════════════════════    │
│  web/gods_eye_view/ (Vite + Cesium, build statique)                          │
│   ├── src/vendor/renderGovernor.js  (MIT, notice conservée)                   │
│   ├── src/main.js    viewer + couche Flights (adsb.lol keyless + fallback)    │
│   ├── src/bridge.js  window.jarvisWorld.handle(json) + jarvis:world-ready     │
│   └── dist/          artefact de build (git-ignoré)                           │
└──────────────────────────────────────────────────────────────────────────────┘
```

Justifications :

1. **Réutilisation maximale** : le moteur Cesium, le render governor, la discipline
   `requestRenderMode`, les patterns BillboardCollection/culling et le codec de vue sont
   réutilisés — pas un globe réécrit.
2. **Frontière propre** : un seul point d'échange (JSON validé des deux côtés) ; aucun objet
   Python ou JS ne traverse. La whitelist d'actions est identique côté Python et côté JS.
3. **Contrôle LLM borné** : Gemini Live n'a pas accès au code — seulement à 5 outils
   `world_*` (voir §7). Les paramètres sont bornés/validés (lat/lon ranges, layerIds en
   énumération, zoom borné).
4. **Isolation** : aucun import depuis `src/tools.py` vers Qt WebEngine ; `src/gods_eye/`
   n'importe pas `src/main.py`, ni aucun runtime core (shutdown, TaskManager, JobRunner).
   **Aucune** modification du cycle de vie global de Jarvis n'est requise.
5. **Packaging** (à trancher séparément) : lever les exclusions `QtWebEngineCore/Widgets`
   (+ dépendances : QtWebChannel si utilisé, QtPositioning non requis), ajouter
   `web/gods_eye_view/dist/**` aux datas PyInstaller. Coût estimé +150-250 Mo installeur.
   Sinon : fallback documenté (placeholder + lien navigateur ? non — le module reste
   *dans* Jarvis mais en mode dégradé informa­tif). **Jamais de lancement externe en silence.**

### 4.3 Alternatives rejetées — pourquoi

- **Iframe / navigateur externe** : explicitement interdit par la mission ; double contexte,
  fuite de credentials, aucune intégration menu/thème/outils, accès clavier dupliqué.
- **Sous-app séparée** : même moteur mais deuxième process, double cycle de vie, IPC
  à maintenir, UX « deux applications » — rejetée.
- **Réécriture Python-native** : perd Cesium (3D, tuiles, primitives, LOD) — le cœur de
  valeur de GEV. Rejetée.
- **WebEngine pour *tout* le tableau de bord** : prématuré ; le module est le premier
  composant web, pas un remplacement de l'UI Qt.

## 5. Audit performance

### 5.1 Ce que GEV fait déjà bien (part de l'existant, pas zéro)

- **Render governor** (`renderGovernor.js`) : `requestRenderMode` quand aucune hold ;
  holds identité-clé ; `maximumRenderTimeChange=Infinity` ; idle rend une frame de
  règlement puis s'arrête. Documenté : avant la vague 2, « ~60 % GPU + ~54 % CPU with ZERO
  layers enabled » — l'état idle actuel est le point de départ, pas un objectif.
- **Primitives, pas Entity** pour les flottes denses (BillboardCollection + PrimitiveCollection).
- **Labels quasi absents** (pré-rendu sprite/canvas, `noCesiumLabels` guardé par test).
- **LOD** : modèle 3D seulement en proximité/sélection (`MODEL_*` thresholds,
  `nearFarScalar`), billboards sinon ; culling frustum natif Cesium.
- **Ingestion bornée** : polling 5-30 s selon couche, backoff, cache serveur (`/api/*`),
  abort coopératif.
- **HUD cheap** : repaint ciblé, panneaux lazy, attribution via credit line.

### 5.2 Chiffres publiés par GEV (Apple M5, Chrome 150, 1440×900 — NON représentatif du
Pentium G5400)

| Scène | M5 (motion / rest) | Note GEV |
|---|---|---|
| Globe idle | 60 / 60 FPS | après governor |
| Flights 3D (tous modèles) | 60 / 60 | |
| Détection 25 % / 50 % / 100 % | 39.3 / 37.4 / 34.4 FPS | 8 169-8 170 observations ; labels 14→28→56 |
| Combiné « static » (11 575 objets) | 57.6 / 60 | **872 Mo heap JS**, 48 665→54 106 text draws |
| Combiné « opérationnel » | 39.9 / 43.1 | échantillons live vides (limité) |
| AISStream **12 000 navires** | 22.1 / 29.8 | **6,4 s activation** |
| NASA FIRMS **100 430 détections** | 32.1 / 55.2 | **30 s activation** |
| Cockpit | 49.6 / 49.2 | |
| Snow (retiré) / Noir / FLIR | 42.3 / 47 / 49 | pires styles post-processing |
| Startup settle | ~1,81-2,04 s ; heap ~105 MiB | |
| Cold activation CCTV | **19,6 s** (48 caméras) · Cables 614 ms / 2 629 objets / **412 Mo heap** · Datacenters 818 ms / 4 362 objets / **328 Mo heap** · Flights 668 ms / 247 avions | |

Lecture : même sur M5, les scènes denses dégradent ; sur **Pentium Gold G5400 (2C/4T, 3,7 GHz,
UHD 610 ~ 192 ALU… classe 2018, TDP 58 W)** — i.e. un GPU intégré sans FP16 ni WebGL2 moderne
sérieux — ces scènes sont **hors budget**. Le module Jarvis doit donc être conçu pour un
plafond bas : caps durs, agrégats visuels, chargement paresseux, et **jamais** de
photogrammétrie 3D par défaut.

### 5.3 Hotspots identifiés (analyse statique du code amont)

1. **Fréquence de render loop** — résolue en amont par le governor ; **régression facile** si
   un hold fuit (listener non désenregistré) → idle jamais atteint → 60 fps inutiles. Notre
   module doit tenir et *prouver* l'idle (test de non-régression + outil de diag
   `getRenderGovernorDiagnostics`).
2. **Entités/billboards par couche** : vols ~250-8 000, AIS ~12 000, FIRMS ~100 000,
   datacenters 4 362, cables 2 629 (1 917 landing points), CCTV illimité (catalogs). Sans
   cap, chaque couche peut exploser. **Doublons d'entités** = fuite classique lors de
   disable/enable répétés (GEV gère via lifecycle epochs ; notre sous-module applique la
   même règle : `disable` = destruction *synchrone* des primitives + `destroy()` des
   collections).
3. **Allocations JS par frame** : extrapolation de position (`Cartesian3.fromDegrees` par
   frame par avion), conversions, closures. Le pattern amont (résolution locale frame, reuse)
   est à reprendre ; tout `new` dans la boucle est suspect.
4. **Labels Cesium** — bannis (test-guardé amont) ; labels HUD uniquement pour la sélection.
5. **Appels réseau** : poll 5 s (flights) / AIS streaming + snapshots ; FIRMS 30 s activation.
   Sur PC lent : augmenter les intervalles (flights ≥ 10 s) + jitter + pause hors focus.
6. **Parsing** : FIRMS CSV (100 000 lignes) + GRIB2 (weather, ecCodes WASM) — à exclure du
   scope v1 ; Flights JSON (~250-8 000 objets) OK.
7. **GPU** : UHD 610 = fill-rate faible ; tuiles Esri 2D/3D sans photogrammétrie ; bloom/FLIR
   **interdits** ; `msaaSamples: 1` ; résolution plafonnée (devicePixelRatio ≤ 1.25) ;
   `maximumScreenSpaceError` ≥ 2 (moins de tuiles) en mode « faible GPU ».
8. **Mémoire JS** : 872 Mo heap sur « combined static » → nous imposons un budget :
   **≤ 150 Mo heap** en cible G5400 pour 1-3 couches légères.
9. **Chargement initial** : toute la GEV charge ~105 Mo heap + tout le globe d'un coup. Notre
   sous-module ne charge que Cesium + Esri/OSM + 1 couche max au démarrage.
10. **Subdivision cross-thread** : fetch + parse dans un Web Worker (phase 2) si le profilage
    le justifie ; en v1, parse JSON natif suffisant (≤ 10 000 objets).

### 5.4 Mesures de ce sandbox (honnêtes) et lacunes

Mesurable ici (réalisé, §11) :
- tests Python (protocol/controller/tools) — dispatch, idempotence, fuites logiques ;
- poids du bundle Cesium après build (`dist/`) — ordre de grandeur (~8-12 Mo JS + tuiles workers) ;
- bench dispatch Python (`scripts/benchmark_gods_eye.py`).

**Non mesurable ici** (headless, pas de Chrome/WebGL, réseau OSINT bloquée hors hosts npm/github) :
FPS, frame-time, GPU, heap navigateur, démarrage Cesium réel, fetch adsb.lol. Ces mesures
sont planifiées sur la machine cible via `web/gods_eye_view` en navigateur + journal de
benchmarks §11 — **aucune valeur inventée n'est publiée**.

## 6. Stratégie de performance (ROI) — hypotheses → mesure → modif → vérification

| Priorité | Optimisation | Hypothèse | Mesure avant | Modification | Mesure après | Statut |
|---|---|---|---|---|---|---|
| P0 | Governed `requestRenderMode` dès la 1re frame ; aucune hold hors vol/animation | Idle = ~0 frame | n/m (M5: 60 fps mode idle après governor amont) | Vendor renderGovernor + holds seulement pendant `flyTo`/interpolation | §11 (tests + protocole bench) | appliqué (prototype) |
| P0 | Caps durs : flights ≤ 600, (vessels/FIRMS exclus v1) | Au-delà → chute fill-rate UHD 610 | GEV PERFORMANCE.md (AIS 12 000 → 22 fps M5) | `MAX_TRACKED = 600`, sample aléatoire stable | §11 (unit test) | appliqué |
| P0 | 1 couche max au démarrage ; paresseux ensuite | Démarrage ≤ M5 ~600 ms impossible une fois Cesium chargé ; mais idle doit rester ~0 fps | — | chargement à la demande via `world_enable_layer` | §11 | appliqué |
| P0 | Labels off par défaut ; icônes canvas pré-rendues 32/56 px | Labels = fill-rate ×N sur UHD 610 | GEV combined static : 48-54 k text draws / 872 Mo | sprites + `nearFarScalar` | §11 | appliqué |
| P0 | Destruction synchrone au toggle (billboards/models/collections) | enable/disable répété = leak | — | `disable` → `destroy()` partout + test de churn ×20 | §11 | appliqué |
| P1 | LOD distance : points (< 12 000 km) → icônes (> 12 000 km distance caméra) ; tailles par `nearFarScalar` | moins de fill-rate lointain | — | `PointPrimitiveCollection` + bascule seuil | §11 (vérifié logiquement) | appliqué (simple) |
| P1 | Culling frustum (Cesium primitives) + skip update si couche disabled | travail zéro quand désactivé | — | boucle update no-op sur disabled | §11 | appliqué |
| P1 | Poll ≥ 10 s + jitter ±20 % ; pause hors focus (`visibilitychange`) | réseau + CPU | — | timer côté web, un seul `setInterval` | §11 | appliqué |
| P2 | Réutilisation d'objets (Color, Cartesian3) ; aucune allocation dans la boucle de frame | GC pauses | — | pools locaux dans le module vols | code review | appliqué |
| P2 | `maximumScreenSpaceError: 2.5`, `msaaSamples: 1`, résolution ≤ 1.25 | fill-rate | — | options Viewer | §11 | appliqué |
| P3 | Web Worker de parse ; instanced models ; WebGL2 obligatoire-off | gain micro | non justifié sans mesure sur cible | **non fait** (attendre bench cible) | — | différé |
> Chaque ligne suivra le cycle complet hypothèse→mesure→modif→vérification avec les bancs
> de §11 ; les lignes marquées « §11 » sont mesurées dans ce sandbox, les gains GPU restent
> **à confirmer sur le PC cible** — aucune extrapolation n'est annoncée.

## 7. « Jarvis Mode » — surface de contrôle structurée

Nommage conforme aux conventions de Jarvis (`snake_case`, verbes explicites, préfixe du module) :

| Outil Gemini | Rôle | Paramètres (bornés) | Retour |
|---|---|---|---|
| `world_set_view` | Active le style/stack par défaut (v1 : `esri-imagery` + `osm` fallback, pas de Google) | `view` : `"globe"` (défaut) | `{success, view}` |
| `world_set_camera` | Vol caméra vers un point | `latitude [-90,90]`, `longitude [-180,180]`, `altitude_m [1000, 40_000_000]`, `heading_deg`, `pitch_deg`, `duration_s [0..10]` | `{success, camera}` |
| `world_focus_region` | Région nommée (présets : paris, singapour, tokyo, newyork … 20 max, table fermée) | `region` (enum), `altitude_m` | `{success, region, camera}` |
| `world_enable_layer` | Active une couche de la **whitelist** | `layer_id` : enum (`flights`) | `{success, layer_id, enabled: true, objects?}` |
| `world_disable_layer` | Désactive / détruit | `layer_id` : enum (`flights`) | `{success, layer_id, enabled: false}` |
| `world_get_state` | État courant (caméra, couches, FPS governor diag, compteurs) | — | `{success, state: {...}}` |

Exemples ciblés :
- « Jarvis, montre-moi les vols autour de Paris » → `world_enable_layer(flights)` +
  `world_focus_region(paris)` (les deux outils, dans cet ordre).
- « Qu'est-ce qu'il y a autour de cette position ? » → `world_get_state` puis lecture des
  entités visibles (v1 : compte d'objets de la couche ; requêtes riches en v2).

Garde-fous : enums fermés ; bornes numériques ; **aucune** exécution de code arbitraire ;
aucun accès réseau hors module ; réponses `{success: false, error}` systématiques (jamais
d'exception vers Gemini) ; confirmation non requise (lecture/visualisation uniquement, rien
de destructeur).

## 8. « Ne charger que ce qui est utile » — chargement paresseux + LOD

```
requête utilisateur ("avions près de Paris")
      ↓ intention géospatiale (Gemini Live + world_* tools)
      ↓ activation minimale : layers = {flights} ; sources = {adsb.lol (ou fallback sample)}
      ↓ chargement (fetch + parse, cap 600)
      ↓ LOD : caméra > 12 000 km → points ; sinon icônes 32 px ; sélection → label HUD
      ↓ governor : hold pendant vol/interpolation uniquement ; idle → requestRenderMode
```

- **Démarrage : 0 couche.** Le globe seul (Esri/OSM), governor idle.
- **Activation explicite uniquement** : chaque `world_enable_layer` = opt-in conscient ;
  aucun préchargement de couches adjacentes.
- **LOD unifié** (cible) : GLOBAL → clusters (grille 250 km) → points → icônes → label
  sélection → (v2) modèle 3D proche/tracké. La v1 implémente points/icônes + `nearFarScalar`.
- **Désactivation = destruction** : zéro état résiduel, zéro timer, zéro hold.
- **Prérequis bench avant généralisation** : le protocole §11.3 mesure activation/désactivation
  répétée et scène dense ; l'extension à d'autres couches (vessels, satellites, earthquakes,
  FIRMS) n'est autorisée qu'après validation du même harness.

---

## 9. Premier prototype (espace vertical minimal)

Livré dans ce chantier — petit mais réel, et utilisable hors ligne :

```
Gemini Live → src/tools.py (registry additif, 6 outils world_*)
            → src/gods_eye/tools.py (déclarations + exécuteurs)
            → src/gods_eye/protocol.py (validation pure JS/Python miroir)
            → src/gods_eye/controller.py (cycle de vie, idempotence, dispatch)
            → src/gods_eye/widget.py (QWebEngineView, pont runJavaScript)
            → web/gods_eye_view/ (Cesium 1.147 via vite-plugin-cesium)
                → window.jarvisWorld.handle(json)  (bridge.js, whitelist)
                → viewer governé (requestRenderMode, msaa=1, Esri→OSM)
                → couche flights (adsb.lol live, cap 600, poll 10 s ± jitter)
```

Exemple de session vocale cible :

> « Jarvis, montre-moi les vols autour de Paris. »
> → `world_enable_layer(flights)` (ouvre le globe à la demande, Esri/OSM,
>   0 couche au démarrage) → `world_focus_region(paris)` (vol caméra 2,5 s)
>   → état via `world_get_state` (« 600 vols affichés, source adsb.lol »).

Retour propre : Échap/FERMER/X → `window.jarvisHost.closeRequested()`
(channel QWebChannel optionnel) ou bouton natif → `closeEvent` → destruction
page + contrôleur `idle`. Aucun état résiduel, aucun timer Python.

**Volontairement hors prototype** : CCTV, cables, datacenters, satellites,
cockpit, voix interne GEV, MCP/panel, photogrammétrie Google, datasets ODbL,
interpolation per-frame, labels. La couche Flights n'est PAS le moteur
flights complet d'amont (7 430 lignes) : c'est une implémentation réduite
Jarvis-native qui conserve ses invariants clés (primitives, cap, LOD distance,
aucune Entity, governor).

## 10. Tests

### 10.1 Nouveaux (ce chantier) — 65 tests, tous verts

| Fichier | Couvre |
|---|---|
| `tests/test_gods_eye_protocol.py` | validation actions/args, bornes, régions, parsing réponses, **parité Python↔JS** (régions + bornes caméra) — 25 tests |
| `tests/test_gods_eye_controller.py` | cycle de vie complet, idempotence enable/disable (1 seul message au globe), churn ×10/×20, double-réponse, stale, listeners exactement-une-fois, destruction pendant dispatch, destroy/close → idle — 24 tests |
| `tests/test_gods_eye_tools.py` | enregistrement dans TOOL_DECLARATIONS/FUNCTIONS, SDK google-genai (`FunctionDeclaration`), headless (world_unavailable, no-op), enums fermées — 13 tests |
| `tests/test_gods_eye_widget.py` | import sans Qt, request_open headless → False, chemin dist — 3 tests |

Étape 9 de la mission — couverture exigée : démarrage ✅ (attach/ready/close),
arrêt ✅, création/destruction ✅, navigation caméra ✅ (construction + parité ;
vol réel non mesurable headless), activation/désactivation layer ✅, répétée ✅,
rapide ✅, fuite évidente ✅ (stats pending/stale, destruction synchrone),
boucle de rendu inutile ✅ (design governor + requestRenderMode ; diagnostic
`getRenderGovernorDiagnostics()` exposé dans world_get_state), double polling
✅ (timer UNIQUE dans la couche web ; aucun timer Python ; test idempotence),
double abonnement événementiel ✅ (listeners identité uniques + handler unique
par transport).

### 10.2 Suite complète du dépôt — zéro régression

| | Base ec944e2 | Avec le module |
|---|---|---|
| passed | 1030 | **1095** (+65) |
| failed | 6 (libGL) | 6 (libGL, identiques) |
| skipped | 122 | 122 |
| collection errors | 14 (libGL/PySide6) | 14 (identiques) |
| subtests passed | 539 | 557 |

Les 6 échecs et 14 erreurs sont des limitations **système** de ce sandbox
(`libGL.so.1` absent, pas de root) — vérifiés identiques sur la base via un
worktree propre. Aucun test existant n'est modifié.

⚠️ Ces Qt-dependents tests ne sont PAS validés ici : ils tournent en CI
Windows/Linux avec libs GL. Le module doit y être revalidé avant merge.

### 10.3 Banc Python (headless, mesuré, chiffres bruts)

`python scripts/benchmark_gods_eye.py` (sandbox, CPython 3.11.2, 2 vCPU) :

| Mesure | Résultat |
|---|---|
| dispatch `world_get_state` (transport mémoire) | ~24-43 µs/op |
| `enable_layer` idempotent (no-op, P0) | ~0,7-1,5 µs/op |
| cycle enable+disable complet (2 dispatches) | ~42-69 µs/cycle |
| import src.tools | ~26-134 ms (1er import) |
| dist/ (Cesium complet) | 13,16 Mo — JS app 7,4 Ko/19,1 Ko |

### 10.4 BASELINE vs OPTIMIZED — honnêteté des mesures

Ce sandbox n'a **ni navigateur, ni WebGL, ni GPU**. Aucune mesure de FPS,
frame-time, heap JS, démarrage Cesium ou réseau adsb.lol n'est publiée ici.
Ce qui EST mesuré : le chemin Python (négligeable), le poids du build, la
correction fonctionnelle.

**Verdict perf : non mesurable dans ce sandbox.** Le module est conçu pour
le pire cas (UHD 610, G5400) mais n'y est pas encore mesuré. Protocole
BASELINE→OPTIMIZED sur le PC cible (à exécuter après `npm run build`) :

```
# 1. Lancer le module dans Chrome/Edge Windows sur cef
cd web/gods_eye_view && npm run preview
# 2. DevTools → Rendering → FPS meter ; Performance monitor → JS heap
# 3. scénarios (5 s chacun, page visible) :
#    a. globe idle (0 couche)                    → attendu governor idle : ~0 frame
#    b. vol caméra Paris (world_set_camera)      → frames pendant 2,5 s seulement
#    c. flights ON → attente 15 s (1 poll 600)   → puis idle à nouveau
#    d. flights OFF                              → 0 objet, 0 timer web, 0 hold
#    e. enable/disable ×10                       → heap stable (DevTools), 0 leak
#    f. 600 vols + globe 2D/3D                   → frame-time noté (cible < 100 ms)
# 4. Comparaison : GEV complet (npm run dev amont, M5) ≠ comparable ; seule
#    la même machine Windows fait foi. Consigner les 6 scénarios avant tout
#    merge dans main.
```

Les décisions P1/P2 (LOD points, workers, instancing) restent ouvertes
**jusqu'à ces mesures** — aucune n'est appliquée « au jugé ».

## 11. Risques

| # | Risque | Sévérité | Mitigation |
|---|---|---|---|
| R1 | QtWebEngine exclu du packaging Jarvis actuel (`jarvis.spec`, `launcher.spec`) → module inutilisable en release tant que l'exclusion n'est pas levée | **Haute** (bloquant distribution) | Décision packaging séparée + spec modifiée derrière release-note ; installeur +~150-250 Mo ; PR dédiée, hors ce chantier |
| R2 | Jarvis sans licence + données ODbL embarquées à l'avenir | Moyenne | Aucune donnée embarquée en v1 ; attributions rendues in-app ; tableau §1 tenu à jour |
| R3 | adsb.lol indisponible / rate-limit / hors ligne | Moyenne | Repli échantillon synthétique étiqueté « ÉCHANTILLON » ; aucun faux « direct » affiché |
| R4 | Performance réelle UHD 610 inconnue | **Haute** (critère GO) | Protocole §10.4 ; module borné (600 objets max, 1 couche, governor) ; mesurer AVANT merge |
| R5 | Cesium 14 Mo `dist/` non versionné → build web obligatoire au packaging | Basse | `npm ci && npm run build` documenté ; à câbler dans build_windows.ps1 (futur) |
| R6 | PR #51 chevauche l'UX dashboard | Moyenne | Module isolé (nouveaux fichiers + 30 lignes dans tools.py) ; aucun conflit attendu ; merge après PR #51 ou rebase |
| R7 | Drift Python↔JS (régions, bornes) | Basse | Test de parité `tests/test_gods_eye_protocol.py::JsParityTests` |
| R8 | CSP QWebEngine trop permissive | Basse | CSP meta stricte dans index.html ; QWebEngine : contenu local uniquement |
| R9 | Usage voix → hallucination d'actions (mauvais layer/région) | Basse | Enums fermés + réponses explicites ; world_get_state avant/après action |
| R10 | Fuite de mémoire JS si couche mal détruite | Moyenne | destroy synchrone ; test churn ; vérification `performance.memory` sur cible |
| R11 | Écran faible (Pentium) + QtWebEngine + Cesium = UX dégradée | Moyenne | Mode « globe léger » documenté : maximumScreenSpaceError=2.5, msaa=1, pas de bloom |
| R12 | API Cesium change entre 1.124 et 1.147 (épinglage caret) | Basse | package-lock.json épinglé (cesium 1.147.0 résolu) ; CI de build web recommandée |

## 12. Décisions de packaging (à faire séparément, PAS ici)

1. `packaging/jarvis.spec` + `packaging/launcher.spec` : retirer
   `PySide6.QtWebEngineCore`/`QtWebEngineWidgets` des `excludes` (launcher
   exclut aussi QtNetwork — requis par WebEngine : à arbitrer séparément).
2. `datas += [("web/gods_eye_view/dist", "web/gods_eye_view/dist")]` après build.
3. Installer Inno Setup : aucun changement prévu (dossiers embarqués).
4. Smoke-test étendu : lancer `python scripts/run_gods_eye_demo.py`.
5. Installer size impact : mesurer (`dist/Jarvis/` avant/après) ; documenter
   dans la release note. Si refusé → module reste dev-only (placeholder affiche
   les instructions d'installation de QtWebEngine au lieu du globe).

## 13. Git / branches / commits

- Branche de session : `arena/961d92ed-jarvis-live-ready` (imposée par le
  tracking Arena — ÉTAPE 0 demandait `feature/gods-eye-view` ; la branche de
  session pointe exactement sur `main` (ec944e2) et n'est **pas** un chantier
  shutdown/Arena 01.5 : la création d'une branche parallèle aurait orpheliné
  le travail. Écart documenté, pas de rename possible.)
- `main` intouché · PR #51 / `feature/v1.9.0` intouchés · aucun merge/push
  avant validation explicite de l'utilisateur.
- Commits atomiques (branche de session) :
  1. `44e5800 docs: add God's Eye View integration audit (licence, architecture, performance)`
  2. `07465b2 feat(web): add Jarvis-native God's Eye View web module`
  3. `4b26602 feat: add God's Eye View bridge (protocol, controller, Qt host)`
  4. `095c86d feat: register world_* tools and add demo entry point`
  5. `673c041 test: add God's Eye View protocol/controller/tools/widget coverage`
  6. `fc5d406 perf: add headless dispatch benchmark for the world module`
- Aucune modification de `src/main.py`, `src/gemini_live.py`, `src/audio.py`,
  `src/background_tasks/*`, `UI/boot_sequence.py`, `src/updater.py` — le module
  est **additif** partout.

## 14. Verdict GO / NO-GO

**GO CONDITIONNEL** pour la poursuite (prototype → intégration packaging),
sous les conditions explicites suivantes :

1. **NO-GO** pour tout merge dans `main` avant : mesure §10.4 sur le PC cible
   (frame-time globe+Vols, idle ~0 frame, churn enable/disable heap stable) ;
   validation Windows du module (PySide6 + QtWebEngine + build PyInstaller) ;
   feu vert packaging (§12).
2. **GO** pour continuer le prototype sur cette branche : l'audit licencie OK,
   l'architecture Isolée tient (zéro patch au core), 65 tests verts, zéro
   régression (1095 vs 1030 passés), banc Python mesuré, divergence JS/Python
   protégée par test.
3. **NO-GO** (permanent) : intégrer TeleGeography, Bhote Koshi, OpenSky en
   distribution ; donner au LLM autre chose que les 6 outils ; toucher au
   shutdown/TaskManager/JobRunner.

Risque principal résiduel : **performance non mesurée sur cible**. C'est le
seul critère qui peut transformer le GO en NO-GO. L'audit préfère un NO-GO
mesuré à une intégration fragile — le protocole §10.4 tranche.

## 15. Prochaines étapes (ordre recommandé)

1. `npm ci && npm run build` sur le PC cible ; exécuter §10.4 ; consigner.
2. Si idle≈0 frame et frame-time Vols OK → PR « intégration packaging »
   (§12) avec release note dédiée ; référencer PR #51 (attendre son merge ou
   rebaser).
3. Mini-lot UX : entrée de menu « God's Eye View » (après PR #51, là où vivent
   les entrées Dashboard) — pas avant.
4. v2 : couche satellites (UCS, PD/libre), earthquakes (USGS, PD) — mêmes
   invariants (primitives + cap + governor + test de churn) ; vessels/FIRMS
   seulement avec bench.
5. worldPacket v2 : `world_get_state` étendu (entités trackées, densité par
   région) en s'inspirant de `src/agent/worldPacket.js` (amont).

---

## Annexe A — Fichiers du module (inventaire)

```
web/gods_eye_view/            (MIT; Cesium Apache-2.0 ; voir NOTICE.md)
  package.json  package-lock.json  index.html  vite.config.js
  README.md  NOTICE.md  .gitignore
  src/main.js  viewer.js  flights.js  bridge.js  regions.js  hud.css
  src/vendor/renderGovernor.js   (verbatim amont, en-tête MIT conservé)
  src/samples/flights-sample.json (synthétique, CC0)
src/gods_eye/                 (code Jarvis, MIT comme le dépôt)
  __init__.py  protocol.py  controller.py  tools.py  widget.py
src/tools.py                  (+20 lignes : branchement additif)
tests/test_gods_eye_*.py      (65 tests)
scripts/benchmark_gods_eye.py  scripts/run_gods_eye_demo.py
.gitignore                    (+node_modules/)
docs/gods-eye-view-integration.md (ce fichier)
```

## Annexe B — Notice de copyright conservée (amont)

`web/gods_eye_view/src/vendor/renderGovernor.js` est une copie verbatim de
God's Eye View (MIT, © 2026 Bilawal Sidhu). Les droits sur ce fichier restent
auteur ; l'attribution figure aussi dans `web/gods_eye_view/NOTICE.md`. Aucune
autre partie d'amont n'est vendorée.


## Annexe C — Vérification sur clone frais (2026-11-09)

`git clone --branch arena/961d92ed-jarvis-live-ready file://<repo> /tmp/jarvis_verify` :

- `pytest tests/test_gods_eye_*.py` → **65 passed** ;
- `npm ci && npm run build` → **succès** ; `cesium@1.147.0` (épinglé via
  package-lock.json) ; `dist/` = **13,16 Mo** (JS app 19,1 Ko + Cesium) ;
- import `src.tools` → registry cohérent (154 déclarations / 154 fonctions) ;
- aucun fichier manquant : le module est entièrement reconstruisible depuis le
  clone (node_modules/dist exclus du Git, cf. .gitignore).

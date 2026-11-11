# God's Eye View — validation corrective et readiness

Ce rapport sépare les comparaisons contrôlées des répétitions réalisées dans
une autre résolution. Les mentions **MESURÉ**, **VÉRIFIÉ**, **NON MESURÉ** et
**NON FAISABLE** décrivent l'état réel des preuves; une valeur non observée
n'est pas extrapolée.

## 1. Environnement exact

| Élément | Valeur observée |
|---|---|
| OS | Windows 11 Professionnel, build 26300 |
| CPU | Intel Pentium Gold G5400 @ 3,70 GHz, 2 cœurs / 4 processeurs logiques |
| GPU | Intel UHD Graphics 610 |
| RAM | 15,83 Go |
| Rendu WebGL | ANGLE / Direct3D 11 sur Intel UHD Graphics 610 |
| Python benchmark / PySide6 / QtWebEngine | 3.11.9 / 6.11.1 / 6.11.1 |
| Chromium QtWebEngine / Cesium | 140.0.0.0 / 1.147.0 |
| Branche et SHA des trois répétitions | `arena/961d92ed-jarvis-live-ready`, `51e8c51a7f63b634562e2a9e1056dbca3d01b4e5` |
| Résolution physique pendant les trois répétitions | 1024×768 |
| Fenêtre / canvas pendant les trois répétitions | 1280×720 / 1280×720 |

**VÉRIFIÉ :** avant chacune des trois répétitions, le script a contrôlé la
résolution effective et a refusé la taille de fenêtre/canvas si elle ne
correspondait pas à 1280×720. Windows n'a pas été reconfiguré. Les JSON
enregistrent `git_clean=true` et le même SHA; le répertoire de preuves a été
ajouté après les mesures.

## 2. Méthode de benchmark

**VÉRIFIÉ :** [le script de benchmark](../scripts/benchmark_gev_real.py) ouvre
réellement `QWebEngineView → build Vite/Cesium → globe`; il attend la scène,
vérifie Cesium et le canvas WebGL, puis collecte les résultats et les traces.

- Les FPS et intervalles Cesium proviennent de `Scene.postRender`.
  `requestAnimationFrame` est rapporté séparément et n'est pas assimilé à un
  rendu Cesium.
- Les frame-times moyen, p95, p99 et maximum sont calculés depuis les
  intervalles `postRender`.
- CPU et RSS viennent de l'arbre de processus Python/QtWebEngine. Le GPU est
  le compteur PDH `GPU Engine 3D` agrégé pour les PID mesurés; ce n'est pas
  l'utilisation globale de l'adaptateur ni le temps de rendu GPU.
- Le scénario caméra dure environ 2,7–2,8 s en mouvement effectif. Les
  percentiles de ce court échantillon portent sur seulement 16–17 frames.
- Les objets de test sont une fixture synthétique et sont étiquetés comme tels.
  Aucune donnée synthétique n'est présentée comme ADS-B live.
- Les résultats bruts, journaux de répétition et captures sont conservés dans
  [le dossier de preuves](gods-eye-view-validation-evidence/). Les JSON
  historiques et leurs journaux/captures sont également conservés dans
  [`baseline_valid`](benchmark_gev_real_baseline_valid.json),
  [`final`](benchmark_gev_real_final.json) et
  [`final_repeat`](benchmark_gev_real_final_repeat.json), avec leurs journaux
  et captures associés.

## 3. Baseline avant optimisation et comparabilité

La comparaison principale est **`baseline_valid` → `final`**. Les deux runs
ont été effectués sur l'écran 1920×1080 avec fenêtre 1280×720 et canvas
1280×720. `final_repeat` n'est pas la répétition de cette condition : il a été
fait sur écran 1024×768 avec fenêtre/canvas 1028×720. Il n'est donc pas utilisé
pour calculer un gain contrôlé.

| Run | Écran | Fenêtre | Canvas | Usage |
|---|---:|---:|---:|---|
| `baseline_valid.json` | 1920×1080 | 1280×720 | 1280×720 | Baseline principale |
| `final.json` | 1920×1080 | 1280×720 | 1280×720 | Comparaison principale |
| `final_repeat.json` | 1024×768 | 1028×720 | 1028×720 | Répétition en conditions différentes, non comparable |
| `repeat-1..3.json` | 1024×768 | 1280×720 | 1280×720 | Trois répétitions homogènes entre elles, non comparables à la paire principale |

Résultats de la paire contrôlée (FPS et frame-times Cesium) :

| Scénario | Baseline: FPS / moyen / p95 / p99 | Final: FPS / moyen / p95 / p99 |
|---|---|---|
| Globe idle | 6,35 / 109,03 / 514,2 / 1788,1 ms | 6,56 / 98,36 / 282,3 / 1925,5 ms |
| Vol caméra | 25,68 / 38,82 / 64,3 / 186,4 ms | 21,94 / 45,04 / 118,0 / 138,6 ms |
| Flights ON, 48 objets | 27,07 / 37,06 / 51,9 / 88,9 ms | 8,57 / 116,76 / 123,6 / 124,5 ms |
| Flights OFF | 27,47 / 35,18 / 46,5 / 98,9 ms | 8,27 / 117,28 / 123,7 / 126,5 ms |
| 600 flights synthétiques + caméra | 18,65 / 40,90 / 64,7 / 352,3 ms | 23,43 / 32,44 / 40,6 / 507,3 ms |

**MESURÉ :** dans cette paire, le scénario caméra/600 gagne 25,6 % de FPS,
réduit la moyenne de 20,7 % et le p95 de 37,3 %, mais le p99 augmente de 44,0 %.
Il s'agit d'un run par version; cela ne prouve pas une amélioration générale.
Les faibles FPS des fenêtres flights ON/OFF sont des frames observées pendant
ces fenêtres, pas des mesures de mouvement caméra ni une certification de
données live.

## 4. Profiling et reproduction du p99 élevé

Le p99 caméra/600 supérieur à 500 ms a été reproduit dans le `final` contrôlé
(507,3 ms) et dans deux des trois nouvelles répétitions à 1024×768. Les
résultats pendant le mouvement effectif sont :

| Répétition | Frames Cesium | FPS | Moyen | p95 | p99 / max | CPU processus* | CPU système | GPU 3D processus | RSS processus |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | 16 | 5,71 | 182,707 ms | 1252 ms | 1252 ms | 6,98 % | 99,88 % | 18,47 % | 758,1 Mo |
| 2 | 17 | 6,29 | 165,825 ms | 283,4 ms | 487,7 ms | 6,70 % | 31,08 % | 18,78 % | 755,4 Mo |
| 3 | 16 | 5,93 | 176,487 ms | 283,3 ms | 513,7 ms | 9,08 % | 48,65 % | 19,56 % | 755,4 Mo |

\* Pourcentage de la capacité totale de la machine, selon le compteur du
processus benchmark.

**MESURÉ :** le `PerformanceObserver` a vu des tâches principales de 1011 ms,
224 ms et 260 ms respectivement. La première coïncide avec un intervalle
Cesium de 1252 ms et une utilisation CPU système de 99,88 %, alors que le
processus benchmark ne représentait que 6,98 % de la machine. Les deux autres
répétitions ont aussi des tâches longues, mais des tailles de queue différentes.

**NON MESURÉ :** l'attribution des tâches reste `unknown` dans `window`.
L'échantillonnage n'identifie ni fonction JavaScript, ni allocation, ni code
Cesium responsable. Le CPU élevé du système n'établit pas à lui seul la cause
de l'arrêt. Avec seulement 16–17 frames par scénario, p99 et maximum sont
identiques et les percentiles de queue sont instables.

La cadence d'updates live n'est pas établie par les trois nouvelles
répétitions : le fetch QtWebEngine échoue et `controller_stats.dispatched`
reste à 0. Les anciens journaux ont observé des intervalles de 9,25–11,93 s,
mais ces quelques événements ne caractérisent pas une cadence stable.

**Conclusion du profiling :** le problème est reproduit et les longues tâches
sont corrélées aux interruptions de frames; leur cause exacte n'est pas
établie. Aucun changement de qualité, d'objet, de fréquence, de primitive ou de
LOD n'a été appliqué sur une hypothèse non profilée.

## 5. Governor caméra et optimisation mesurée

Le correctif antérieur libère `world-camera` dans le callback de fin de
`flyTo`. Le signal avant/après disponible est limité :

| Mesure | Avant | Après | Gain / réserve |
|---|---|---|---|
| Stabilisation observée juste après le vol | Baseline : `continuous`, hold `world-camera` encore présent après 3,055 s | Vérification immédiate du `final` et des trois nouvelles captures : `idle`, sans hold, attente supplémentaire 0 ms | Amélioration observée au point de contrôle |
| Diagnostic pris plus tard dans les trois nouvelles répétitions | Sans répétition tardive comparable dans la baseline | Les trois JSON retrouvent `continuous` avec `world-camera` | Le correctif n'est pas validé comme stable; déclencheur non identifié |

**VÉRIFIÉ :** les contrôles d'état finaux après destruction reviennent à `idle`;
les vols caméra sont bien exécutés et les transitions caméra sont enregistrées.
Toutefois, la première mesure `idle` et le diagnostic ultérieur `continuous`
se contredisent quant à la stabilisation durable. La correction existante reste
dans le code, mais son bénéfice de lifecycle après caméra n'est pas déclaré
pleinement résolu. Aucun nouvel ajustement n'a été fait sans isoler le
déclencheur.

## 6. Répétitions finales et lifecycle

**MESURÉ :** les trois répétitions ont les mêmes empreintes SHA-256 pour les
fichiers JS critiques :

| Fichier | SHA-256 |
|---|---|
| `bridge.js` | `48b05dbb33a0b6c59aedc39980b03699e55ca07ba5e9e0d6ed0fe3ddf80620d5` |
| `flights.js` | `46df8d6e81ce02623de21fc8b672ddd0808edcbe1d237d4f5583864e992b3f03` |
| `viewer.js` | `5a415e0ccd5887cf5e732f2b9511938ef6b5cf648169f030f1d6162c0954995b` |
| `main.js` | `e434bb9c80f5ad89390c5ab027d79cf897bc812fe113cbe189d04339874ca3a8` |
| `renderGovernor.js` | `8539f44a70fd9c2dc9c47c7ead1792b5a349d144ba3cd118b5831a091bdf64d5` |

Le benchmark complet a aussi mesuré les autres scénarios. Les étendues
ci-dessous sont uniquement celles des trois runs à écran 1024×768; elles ne
sont pas comparées à la paire principale en 1920×1080.

| Scénario | FPS Cesium | Moyen | p95 | p99 / max |
|---|---:|---:|---:|---:|
| Globe idle | 2,78–3,38 | 257,146–312,913 ms | 552,8–2677,8 ms | 552,8–2677,8 ms |
| Mouvement caméra simple | 3,56–5,05 | 193,286–264,827 ms | 274,9–586,9 ms | 355,4–586,9 ms |
| Flights ON, échantillon synthétique | 3,61–3,95 | 251,932–276,833 ms | 539,2–815,5 ms | 540,9–815,5 ms |
| Flights OFF | 3,49–3,74 | 239,518–287,645 ms | 539,8–545,2 ms | 539,8–545,2 ms |
| Chargement fixture de 600 flights | **NON MESURÉE** (0–1 frame) | **NON MESURÉE** | **NON MESURÉE** | **NON MESURÉE** |
| Fixture de 600 en mouvement caméra | 4,87–5,47 | 188,781–199,047 ms | 274,2–1252 ms | 487,7–1252 ms |

Les fenêtres contiennent peu de frames et les runs ont une forte variabilité.
Pour le chargement seul, il n'y a eu que zéro ou une frame `postRender`; aucun
intervalle n'existe pour calculer des frame-times. Le temps de chargement des
600 objets est rapporté séparément ci-dessous.

Autres mesures des trois répétitions :

- Activation jusqu'à `ready` : 5,089–6,820 s; `request_open()` : 1,888–3,873 s.
- Chargement des 600 objets : 259,03–279,59 ms mur Python↔WebEngine; `applyRows` :
  7,0–9,9 ms, création de billboards : 6,0–8,6 ms. Les données confirment
  exactement 600 objets avec `source=synthetic-benchmark`.
- Churn ×10 : activation moyenne 371,48–448,81 ms; désactivation moyenne
  475,40–498,34 ms; variation RSS après stabilisation −1,3 à +0,1 Mo. Ces
  moyennes murales incluent les échanges avec WebEngine; elles ne sont pas un
  temps JavaScript isolé.
- Désactivation des 600 objets : 20,36–24,66 ms; fermeture de fenêtre :
  16,57–55,93 ms.
- Après désactivation, les trois runs vérifient `enabled=false`,
  `objectCount=0`, aucun timer, aucune collection vivante et lifecycle final
  `idle`.

**NON MESURÉ :** profil CPU JavaScript par fonction, allocations/heap Chromium,
coût séparé du pont Python↔WebEngine et temps GPU. Aucun hotspot de primitives,
`BillboardCollection`, caméra ou reconstruction de layer n'est établi par les
mesures disponibles.

## 7. Sources réelles, fraîcheur et mode hors ligne

Les contrôles séparent bien l'accès réseau Python des requêtes réellement
effectuées par le moteur embarqué :

- **MESURÉ — requête Python directe :** Esri metadata et tuile image, et tuile
  OSM, ont répondu HTTP 200.
- **MESURÉ — ADS-B direct :** `adsb.lol` a répondu HTTP 200, mais avec zéro
  aéronef; les âges de position les plus récent/ancien sont `null`.
  **NON MESURÉE :** fraîcheur d'une position live, puisqu'aucune position
  n'était retournée.
- **MESURÉ — QtWebEngine :** Esri metadata et ADS-B ont échoué avec
  `TypeError: Failed to fetch`; les requêtes de tuiles Esri et OSM ont échoué.
  Les tuiles ne sont donc pas validées visuellement dans l'application.
- **VÉRIFIÉ — hors ligne :** le contrôleur utilise explicitement
  `synthetic-sample`, 48 contacts, `HORS LIGNE — ÉCHANTILLON` et
  `FLIGHTS — 48 · ÉCHANTILLON`. Ce ne sont pas des données live.
- **VÉRIFIÉ — fixture de charge :** les 600 objets sont synthétiques,
  explicitement marqués `BENCHMARK`/`SYNTHÉTIQUE`; ils ne représentent pas un
  flux live.

Le probe réseau brut est dans
[direct-sources.json](gods-eye-view-validation-evidence/direct-sources.json);
le probe QtWebEngine et offline est dans
[qtwebengine-source-probe.json](gods-eye-view-validation-evidence/qtwebengine-source-probe.json).
**NON FAISABLE dans l'état réseau constaté :** valider la fraîcheur ou la
performance d'un flux ADS-B live depuis QtWebEngine.

## 8. Vérification UX

**VÉRIFIÉ :** la capture de l'application réelle montre l'identité sombre,
cyan/violet, sans bloom, shader coûteux ni particules. Les informations
opérationnelles visibles incluent le statut du fond (`FOND OSM — ÉCHECS`),
la source des flights (`BENCHMARK SYNTHÉTIQUE`), le nombre 600, l'état de vue
et les coordonnées courantes (`VUE LIBRE`, latitude/longitude). En mode
hors-ligne, le probe confirme le libellé et le compte d'échantillon de 48.

La capture est [repeat-1-ui.png](gods-eye-view-validation-evidence/repeat-1-ui.png).
**VÉRIFIÉ :** le canvas mesuré est bien 1280×720. **Limite :** l'écran physique
fait 1024×768; la visibilité et l'interaction de toute la fenêtre 1280×720 sur
le bureau réel ne sont donc pas entièrement validées.

## 9. Tests et build web

- **VÉRIFIÉ :** les 91 tests ciblés passent (65 GEV et 26 packaging).
- **VÉRIFIÉ :** `npm run build` depuis `web/gods_eye_view` réussit avec Vite
  6.4.6; les assets Cesium et `dist/` sont produits.
- **VÉRIFIÉ :** `py_compile` passe pour le script de benchmark et les fichiers
  Python touchés.
- Aucun changement produit supplémentaire n'a été fait après ces tests; les
  changements suivants sont ce rapport et les preuves associées.

## 10. Packaging PyInstaller et coût incrémental

**VÉRIFIÉ :** le smoke test s'exécute depuis `Jarvis.exe` frozen et ouvre le
GEV avec `ready=true`, Cesium 1.147.0 et canvas 1280×720. Le log atteste
`Frozen: True`, `QtWebEngineProcess`, les ressources Qt et des chemins
QtWebEngine à l'intérieur du bundle. Ce n'est pas seulement un probe avec le
Python système.

Comparaison de l'onedir GEV au contrôle construit sans GEV :

| Paquet | Taille déployée | Fichiers | ZIP |
|---|---:|---:|---:|
| GEV | 1 750 683 997 octets | 8 772 | 656 589 852 octets |
| Contrôle sans GEV | 1 375 928 241 octets | 8 312 | 516 642 494 octets |
| Incrément GEV | 374 755 756 octets (+27,237 %) | +460 | 139 947 358 octets (+27,088 %) |

**MESURÉ :** l'ajout GEV pèse 357,395 MiB dans l'onedir et 133,464 MiB dans
l'archive. L'incrément provient principalement du runtime PySide6/QtWebEngine
(environ 361,6 MB), puis du build web Cesium (environ 13,17 MB). Parmi les
fichiers individuels figurent `Qt6WebEngineCore.dll` (204 828 984 octets),
les ressources Chromium, `QtWebEngineProcess.exe` et le build Vite/Cesium.
Le paquet de base reste lui-même volumineux, notamment `torch_cpu.dll`
(307 778 048 octets) et `llvmlite.dll` (106 602 496 octets), présents dans
les deux builds.

Le contrôle a été produit avec le spec historique sans GEV du commit
`71b9f6b`; aucune dépendance Jarvis n'a été supprimée. Les builds et
comparaisons sont consignés dans
[packaging-size-comparison.json](gods-eye-view-validation-evidence/packaging-size-comparison.json),
[pyinstaller-gev.log](gods-eye-view-validation-evidence/pyinstaller-gev.log),
[pyinstaller-no-gev.log](gods-eye-view-validation-evidence/pyinstaller-no-gev.log)
et [frozen-gev-smoke.log](gods-eye-view-validation-evidence/frozen-gev-smoke.log).

**NON MESURÉ :** installation propre par Inno Setup; le contrôle porte sur
l'onedir et son ZIP portable de validation.

## 11. Limites et éléments non résolus

- **NON MESURÉ :** cause exacte des stalls caméra/600, attribution JavaScript,
  heap/allocation Chromium et temps GPU.
- **NON RÉSOLU :** `world-camera` réapparaît dans les diagnostics tardifs après
  un premier état `idle`; la séquence de déclenchement reste inconnue.
- **NON MESURÉ :** ADS-B live ou fraîcheur de données, l'endpoint embarqué
  échouant et la réponse directe ne contenant aucun aéronef.
- **NON VÉRIFIÉ :** tuiles Esri/OSM visibles dans QtWebEngine; le réseau direct
  ne prouve pas leur affichage dans l'application.
- **NON FAISABLE dans les conditions observées :** benchmark live à 600 avions
  ou mesure de fraîcheur sans positions reçues.
- **NON VÉRIFIÉ :** interaction de la fenêtre complète 1280×720 sur le bureau
  physique 1024×768, ni installation Inno Setup.
- Les mesures GPU sont l'utilisation 3D du processus, pas le GPU entier.
  Les percentiles de chaque mouvement proviennent d'un petit échantillon; le
  p99 de queue demande une instrumentation de profil plus fine pour attribuer
  les pauses.

## 12. Verdict

**État : validation technique partielle; readiness de production non établie.**
Le rendu Cesium réel, trois répétitions dans des conditions homogènes entre
elles, les 91 tests, le build web et l'ouverture depuis l'exécutable PyInstaller
frozen sont vérifiés. Le packaging a un coût GEV mesuré de 374 755 756 octets
dans l'onedir, sans suppression de dépendance.

Le p99 caméra/600 supérieur à 500 ms est reproduit, mais son origine n'est pas
attribuée; le governor caméra présente aussi un état tardif incohérent. Le
flux live et les tuiles depuis QtWebEngine ne sont pas fonctionnels dans la
condition testée, même si le fallback synthétique est explicitement identifié.
La fenêtre n'a pas été testée intégralement sur un écran physique 1280×720.
Ces limites empêchent de déclarer la validation de production terminée; elles
ne justifient pas un verdict `NO-GO` fondé uniquement sur des mesures dont la
cause reste inconnue. Aucun merge dans `main` n'est effectué.

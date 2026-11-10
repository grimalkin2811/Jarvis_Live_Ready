# God's Eye View — validation sur la machine cible

Validation réalisée sur le poste Windows réel, sans extrapolation depuis une
machine plus puissante. Les statuts **MESURÉ**, **VÉRIFIÉ**, **NON MESURÉ**
et **NON FAISABLE** distinguent les données collectées des limites.

## 1. Environnement exact

| Élément | Valeur observée |
|---|---|
| OS | Windows 11, build **26300** (**pas 26200**) |
| CPU | Intel Pentium Gold G5400 @ 3.70 GHz, 2 cœurs / 4 processeurs logiques |
| GPU | Intel UHD Graphics 610 |
| RAM | 15,83 Go |
| Rendu WebGL | ANGLE / Direct3D 11 sur Intel UHD Graphics 610 |
| Python du benchmark | 3.11.9 |
| PySide6 / QtWebEngine | 6.11.1 / 6.11.1 |
| Chromium QtWebEngine | 140.0.0.0 |
| Cesium | 1.147.0 |
| Écran / fenêtre mesurée | 1920×1080 / 1280×720 |

**VÉRIFIÉ :** la build de Windows détectée est 26300. La machine demandée était
décrite comme build 26200; cette différence est signalée et les résultats ne
sont pas attribués à 26200.

## 2. Méthode de benchmark

**VÉRIFIÉ :** [`scripts/benchmark_gev_real.py`](../scripts/benchmark_gev_real.py)
ouvre réellement `QWebEngineView → build Vite/Cesium → globe`; il attend
l'initialisation de la scène, vérifie le canvas WebGL et la version Cesium.

- Les FPS et intervalles Cesium viennent de `Scene.postRender`; `requestAnimationFrame`
  est mesuré séparément et n'est jamais présenté comme un rendu Cesium.
- Les frame-times moyens, p95 et p99 sont calculés depuis les intervalles
  `postRender` conservés dans le JSON brut.
- CPU et RSS sont échantillonnés dans l'arbre de processus Python/QtWebEngine.
- GPU est mesuré via PDH `GPU Engine 3D`, pour les PID de cet arbre; le maximum
  des moteurs correspondants est rapporté, pas l'utilisation globale de la carte.
- Les scénarios comprennent globe idle, vol caméra, flights ON/OFF, churn ×10,
  fixture synthétique de 600 flights et vol caméra avec ces 600 billboards.
- La fixture de 600 objets est déterministe et explicitement étiquetée
  **BENCHMARK SYNTHÉTIQUE** dans l'interface et les résultats.

Les données et conditions complètes sont conservées dans
[`benchmark_gev_real_baseline_valid.json`](benchmark_gev_real_baseline_valid.json),
[`benchmark_gev_real_baseline_valid.log`](benchmark_gev_real_baseline_valid.log),
[`benchmark_gev_real_baseline_valid_ui.png`](benchmark_gev_real_baseline_valid_ui.png),
[`benchmark_gev_real_final_repeat.json`](benchmark_gev_real_final_repeat.json),
[`benchmark_gev_real_final_repeat.log`](benchmark_gev_real_final_repeat.log) et
[`benchmark_gev_real_final_repeat_ui.png`](benchmark_gev_real_final_repeat_ui.png).
Le run post-correctif précédent est également conservé dans
[`benchmark_gev_real_final.json`](benchmark_gev_real_final.json), son
[`benchmark_gev_real_final.log`](benchmark_gev_real_final.log) et sa
[`benchmark_gev_real_final_ui.png`](benchmark_gev_real_final_ui.png).

## 3. Baseline avant optimisation

Baseline capturée avant le correctif du governor caméra, sur la même machine.
Les FPS/frame-times ci-dessous sont les observations du benchmark, et non une
mesure de `requestAnimationFrame`.

| Scénario baseline | FPS Cesium | Frame-time moyen | p95 | p99 | CPU moyen | GPU 3D moyen | RSS moyen |
|---|---:|---:|---:|---:|---:|---:|---:|
| Globe idle | 6,35 | 109,03 ms | 514,2 ms | 1788,1 ms | 3,68 % | 18,96 % | 596,9 Mo |
| Vol caméra | 25,68 | 38,82 ms | 64,3 ms | 186,4 ms | 11,60 % | 43,74 % | 662,0 Mo |
| Flights ON, 48 objets | 27,07 | 37,06 ms | 51,9 ms | 88,9 ms | 7,55 % | 42,98 % | 694,1 Mo |
| Flights OFF | 27,47 | 35,18 ms | 46,5 ms | 98,9 ms | 8,85 % | 49,98 % | 728,8 Mo |
| 600 flights, mouvement caméra | 18,65 | 40,90 ms | 64,7 ms | 352,3 ms | 10,01 % | 43,43 % | 812,6 Mo |

**MESURÉ :** l'activation a pris 5,51 s jusqu'à `request_open()` et 13,03 s
jusqu'à l'état `ready`. Churn ×10 : activation moyenne 36,65 ms, désactivation
37,22 ms, delta RSS après stabilisation +0,1 Mo. Désactivation après 600 :
23,34 ms; fermeture de fenêtre : 51,85 ms; lifecycle final `idle`.

## 4. Profiling réalisé

**MESURÉ :**

- Le probe caméra a vu `moveStart` sans `moveEnd`; après la fenêtre caméra, le
  governor était encore `continuous`, avec le hold `world-camera`, même après
  3,055 s supplémentaires. C'est l'anomalie directement établie.
- Avec 48 contacts d'échantillon, le profil a observé deux mises à jour séparées
  de 11,19 s sur la baseline et de 9,25 puis 11,93 s dans les deux derniers
  runs. Ces petits échantillons ne suffisent pas à établir une dérive de cadence;
  le polling nominal est de 10 s avec jitter.
- Chargement initial des 600 billboards : 10,3 ms au total, dont 5,0 ms de
  création de billboards et 3,6 ms de normalisation sur la baseline. Les trois
  runs post-correctif vont de 5,7 à 12,2 ms au total (4,8 à 10,8 ms de création).
- Le profil de tâches longues a observé une tâche de 324 ms pendant le scénario
  baseline avec 600 objets en mouvement, puis 229 à 301 ms dans les runs finaux;
  aucun long task pendant leur création.

**NON MESURÉ :** attribution du CPU JavaScript par fonction, allocations JS
par fonction et heap Chromium. `PerformanceObserver` rapporte les longues
tâches, mais n'identifie pas leur fonction responsable. Le benchmark ne justifie
donc aucune optimisation de LOD, culling, primitives ou fréquence de mise à jour
par simple supposition.

## 5. Problème identifié

**VÉRIFIÉ :** l'animation `flyTo` ne produisait pas l'événement `moveEnd`
observé par le probe. Le hold `world-camera` n'était donc pas libéré par le
listener caméra et le governor restait en rendu continu après l'animation.
Ce comportement explique les FPS plus élevés dans certaines fenêtres « idle »
de la baseline; ils ne constituent pas une preuve d'un meilleur rendu utile.

## 6. Optimisation appliquée

Le callback `complete` du `flyTo` libère explicitement `world-camera`, puis
demande une frame de stabilisation. La voie `cancel` garde sa demande de rendu
final. Aucun objet ni fonctionnalité utilisateur n'a été supprimé.

**VÉRIFIÉ :** après le correctif, le governor est `idle` et sans hold lorsque
le benchmark commence à vérifier la stabilisation caméra; l'attente
supplémentaire mesurée est de 0 ms. Les deux runs finaux conservés donnent un GPU 3D
moyen post-vol de 10,73 à 16,13 % et un CPU machine de 2,58 à 4,04 %. Ces
valeurs post-vol n'étaient pas enregistrées dans la baseline:
**NON MESURÉES avant**, donc aucun gain GPU chiffré avant/après n'est revendiqué.

## 7. Mesures avant / après

| Mesure | Avant | Après (répétition finale) | Gain observé | Compromis / réserve |
|---|---:|---:|---:|---|
| Governor après caméra | `continuous`, hold `world-camera` encore présent après 3,055 s | `idle`, zéro hold dès la première vérification (0 ms d'attente supplémentaire) | Hold résiduel supprimé dans les trois runs finaux | GPU/CPU post-vol non enregistrés avant |
| Vol caméra Cesium | 25,68 FPS; moyen 38,82 ms | 21,94–25,64 FPS; moyen 38,03–45,04 ms | Résultats finaux variables, pas de gain global établi | p95 final 117,1–118,0 ms; pas de comparaison stable |
| Vol caméra avec 600 objets | 18,65 FPS; moyen 40,90 ms; p95 64,7 ms | 19,19–23,43 FPS; moyen 32,44–39,76 ms; p95 40,6–75,5 ms | FPS final +2,9 à +25,6 % selon le run | p99 final 507,3–515,4 ms, au-dessus de la baseline (352,3 ms) |
| Churn ×10, activation | 36,65 ms | 28,73–51,51 ms | Variation non concluante | Pas de profil heap Chromium |
| Churn ×10, désactivation | 37,22 ms | 26,80–39,02 ms | Variation non concluante | RSS delta −0,1 à +0,2 Mo |
| Chargement 600 billboards | 10,3 ms | 5,7–12,2 ms | Aucun gain reproductible établi | Pas de changement d'algorithme; création baseline 5,0 ms, final 4,8–10,8 ms |

La baseline et le final sont des runs distincts. Les résultats caméra simples et
les percentiles varient; les chiffres ne démontrent pas un gain universel de
performance. Le bénéfice solidement démontré est la libération du hold caméra.

## 8. Benchmark final

Répétition complète post-correctif; résultats bruts dans le JSON associé.

| Scénario final | FPS Cesium | Moyen | p95 | p99 | CPU moyen | GPU 3D moyen | RSS moyen |
|---|---:|---:|---:|---:|---:|---:|---:|
| Globe idle | 6,56 | 98,36 ms | 282,3 ms | 1925,5 ms | 6,06 % | 16,18 % | 597,7 Mo |
| Vol caméra | 21,94 | 45,04 ms | 118,0 ms | 138,6 ms | 16,45 % | 37,69 % | 642,1 Mo |
| Flights ON, 48 objets | 8,57 | 116,76 ms | 123,6 ms | 124,5 ms | 7,37 % | 24,32 % | 665,5 Mo |
| Flights OFF | 8,27 | 117,28 ms | 123,7 ms | 126,5 ms | 6,70 % | 24,74 % | 673,7 Mo |
| 600 flights, chargement | 0,40 | 323,2 ms | 323,2 ms | 323,2 ms | 4,91 % | 6,15 % | 754,0 Mo |
| 600 flights, mouvement caméra | 19,19 | 39,76 ms | 75,5 ms | 515,4 ms | 13,24 % | 40,86 % | 770,8 Mo |

**MESURÉ / VÉRIFIÉ :**

- Le test synthétique contient exactement 600 objets. Dans ce run `applyRows`
  a pris 5,7 ms, dont 4,8 ms de création de billboards et 0,6 ms de
  normalisation.
- Churn ×10 : activation moyenne 51,51 ms, désactivation moyenne 39,02 ms,
  delta RSS après stabilisation +0,2 Mo. Après flights OFF, `enabled=false`,
  0 objet, aucun timer programmé, aucune collection vivante; governor `idle`,
  sans hold. Les mêmes invariants sont vérifiés après destruction des 600 objets.
- Désactivation après 600 : 22,23 ms; fermeture de fenêtre : 78,26 ms;
  lifecycle final `idle`.
- Les deux runs post-correctif conservés du mouvement avec 600 objets couvrent
  19,19 à 23,43 FPS, p95 40,6 à 75,5 ms et p99 507,3 à 515,4 ms. Cette variance empêche
  d'attribuer un gain reproductible aux métriques caméra.

Les mesures flights ON/OFF sont les frames Cesium effectivement observées dans
leurs fenêtres; elles sont présentées telles quelles. Elles ne doivent pas être
comparées comme des performances en mouvement caméra. L'idle du governor après
caméra est vérifié séparément.

## 9. Tests et validation de l'interface

- **VÉRIFIÉ :** les 65 tests GEV et 26 tests packaging passent ensemble sur
  Python 3.11.
- **VÉRIFIÉ :** `npm ci` puis `npm run build` depuis `web/gods_eye_view` réussit;
  Vite 6.4.6 produit un `dist/` de 13 165 460 octets et un bundle Cesium.
- **VÉRIFIÉ :** capture QtWebEngine réelle du globe avec les 600 marqueurs et
  l'étiquette visible « BENCHMARK SYNTHÉTIQUE ».
- **NON MESURÉ :** les tuiles d'imagerie n'ont pas été vérifiées sur cette
  condition réseau; la capture montre le globe bleu et les billboards, sans
  fond cartographique visible.

## 10. Packaging QtWebEngine

**VÉRIFIÉ :** build PyInstaller onedir complet avec Python 3.11.9, PyInstaller
6.22.3 et PySide6/QtWebEngine 6.11.1. `validate_build.py --pyinstaller
--require-gods-eye` passe. Le bundle contient `QtWebEngineCore.pyd`,
`QtWebEngineWidgets.pyd`, `QtWebEngineProcess.exe`, `icudtl.dat`, les ressources
`.pak`, la locale Chromium `en-US.pak`, `dist/index.html`, Cesium et les assets
hashés.

**VÉRIFIÉ :** `Jarvis.exe --smoke-test` renvoie `SMOKE TEST PASSED`.
Un probe QtWebEngine a aussi chargé le `index.html` packagé en utilisant
explicitement le `QtWebEngineProcess.exe` et le dossier `resources` issus du
bundle; résultat JavaScript: `ready=true`, Cesium `1.147.0`, canvas présent,
statut `PRÊT`. Le host de ce probe était le Python système avec PySide6 6.11.1,
pas l'exécutable frozen lui-même.

**VÉRIFIÉ :** ZIP portable de validation réel généré avec 8 772 entrées
(656 584 987 octets); `validate_build.py --zip --require-wakeword
--require-gods-eye` passe.

Taille mesurée de l'onedir: 1 750 675 372 octets (8 772 fichiers); `dist/`
Vite/Cesium: 13 165 460 octets. **NON MESURÉE :** augmentation incrémentale
par rapport à un ancien paquet sans QtWebEngine, car aucun build comparatif
antérieur n'était conservé. Le ZIP et l'onedir sont des artefacts de validation,
pas des paquets publiés.

**NON MESURÉ :** lancement de God's Eye View depuis l'interface complète de
l'exécutable frozen; son smoke test standard valide le démarrage Jarvis, pas
l'ouverture du globe. La présence des fichiers requis et l'exécution du process
WebEngine packagé sont vérifiées séparément ci-dessus.

**NON MESURÉ :** launcher portable final, installateur Inno Setup et installation
propre complète; seul l'onedir de l'application et un ZIP de validation de cet
onedir ont été produits.

## 11. Limitations

- **NON MESURÉE :** utilisation GPU totale de l'adaptateur; le compteur porte
  uniquement sur les moteurs 3D des PID benchmark.
- **NON MESURÉE :** frame-time GPU et heap Chromium.
- **NON MESURÉE :** flux ADS-B live. `adsb.lol` a renvoyé `Failed to fetch`;
  l'interface a utilisé l'échantillon synthétique de 48 flights.
- **NON FAISABLE dans ces conditions :** comparaison d'un dataset live à
  600 flights. Le scénario à 600 est nécessairement une fixture synthétique.
- **NON MESURÉE :** profil CPU JS par fonction, allocations et nombre de
  réallocations/reconstructions internes Cesium.
- **NON VÉRIFIÉE :** imagerie Esri/OSM visible, bien que le fallback OSM soit
  enregistré dans l'état initial de la scène.
- Le p99 élevé du mouvement caméra 600 (507,3–515,4 ms dans les deux runs
  post-correctif conservés) mérite d'être reproduit. Les moyennes et p95 varient
  également entre ces runs; aucune stabilité de queue n'est présumée.

## 12. Verdict

**GO conditionnel pour poursuivre le prototype sur cette branche**, pas
d'autorisation implicite de merge vers `main`.

Le moteur Cesium est réellement rendu sur la machine cible, les 65 tests GEV
passent, le scénario 600 conserve les 600 objets; dans la répétition finale,
son p95 de mouvement est 75,5 ms et son p99 est 515,4 ms. Le défaut mesuré du governor caméra est
corrigé sans réduire la fonctionnalité. Le chargement de 600 billboards coûte
10,1 ms; les données ne justifient pas une optimisation supplémentaire des
primitives ou de la fréquence des flights.

Le p99 caméra 600 élevé, l'absence du flux ADS-B live, la visibilité des tuiles
sur réseau externe et l'intégration depuis l'interface frozen restent
explicitement ouverts. Le packaging onedir et le ZIP de validation incluent
QtWebEngine et ses ressources, sans constituer une validation de l'installateur.
**Aucun merge dans `main` n'est effectué.**

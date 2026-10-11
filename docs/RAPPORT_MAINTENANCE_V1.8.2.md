# Rapport de maintenance — Jarvis Live Ready v1.8.2

Version publiée : **1.8.2** (release de maintenance).
Branche : `arena/9076c6a8-jarvis-live-ready`, à partir de `66d53f6` (= `v1.8.1`).

Ce document distingue systématiquement :

- les **faits vérifiés** (exécutions reproductibles de ce dépôt) ;
- les **mesures** (avec protocole, machine, dispersion) ;
- les **estimations** (signalées comme telles) ;
- les **contrôles NON exécutés** dans cet environnement (liste en §7).

---

## 1. Objet et périmètre

Release de maintenance, sans nouvelle fonctionnalité. Trois objectifs :

1. **Nettoyage** du code mort, des mécanismes obsolètes et des artefacts générés.
2. **Lisibilité / maintenabilité** : annotations de type, factorisation des
   duplications, robustesse des entrées/sorties d'état, documentation.
3. **Optimisation mesurée** des chemins chauds, sans changer le comportement.

Contraintes respectées : modèle principal inchangé
(`gemini-2.5-flash-native-audio-preview-12-2025`), protocoles, quotas et
architecture des tâches d'arrière-plan inchangés ; aucun appel réel à l'API
Gemini ; aucune suppression de test ; aucun benchmark inventé.

---

## 2. Méthode de vérification du code mort

Un symbole n'a été retiré qu'après **trois** confirmations :

1. recensement des appels statiques dans `src/`, `UI/`, `launcher/`, `tests/`
   et `scripts/` ;
2. recherche par **chaîne** du nom (les attributs lus dynamiquement —
   `getattr`/`hasattr` — n'apparaissent pas comme appels) ;
3. pour les attributs d'état, analyse AST `Store` vs `Load` : un attribut
   seulement écrit et jamais lu est candidat, mais uniquement si son nom
   n'apparaît nulle part ailleurs.

Contre-exemples conservés après vérification : `_running_checker` (lu via
`hasattr`/`getattr` par `src/music/manager.py:290-291`), `_last_wake_score` et
`wake_key` (lus via `getattr` dans `src/audio.py`), `ensure_user_content_dir`
(utilisé par `src/paths.py` et le runtime).

---

## 3. Nettoyage (phase 2)

Commit `4e89449` — 15 fichiers, **−197 / +6** :

| Fichier | Retiré |
|---|---|
| `src/logging_setup.py` | `_log_dir`, `log_exception` |
| `src/paths.py` | `ensure_dir`, `resource_dir` |
| `src/updater.py` | `assert_valid_app_dir`, `show_prerelease`, repli `expect_app_prefix` |
| `src/packaging_validation.py` | `validate_zip(expect_app_prefix)`, `validate_zip_strict`, `_zip_contains`, `assert_app_dir`, `assert_zip`, `assert_install_dir` |
| `src/settings.py` | `_field_defaults` (+ import `fields`) |
| `src/ui.py` | `PresenceBridge`, `VoiceEnergyRouter` |
| `src/background_tasks/gateway.py` | `api_status_code` |
| `src/background_tasks/manager.py` | `reset_default_task_manager_for_tests` |
| `src/music/intents.py` | `intent_to_tool_args` |
| `src/music/providers/deezer.py` | `MatchCandidate` |
| `src/wakeword.py` | `ensure_onnx_models` |
| `src/writing/active_field.py` | `running_on_windows` (+ import `sys`) |
| `UI/appearance_actions.py` | `set_theme_blue`, `set_theme_white` |
| `UI/system_actions.py` | `response_mode_info` |
| `UI/visibility_bridge.py` | `valid_menu_names` |

`packaging_validation` détecte désormais le format de l'archive **depuis
l'archive elle-même**, ce qui supprime le paramètre `expect_app_prefix` et ses
deux variantes de validation.

Commit `922aa10` — attributs d'état jamais lus : `gemini_live._resumable` et
sept réglages morts de `UI/jarvis_menu.py` (`_menu_drag_axis`,
`_menu_wheel_index`, `_menu_exit_seconds`, `_menu_distance_close_enabled`,
`_menu_open_projection`, `_menu_close_projection`, `_menu_open_lateral`).

Artefacts ignorés (`922aa10`) : `.pytest_cache/`, `.ruff_cache/`,
`.mypy_cache/`, `pytest-results.xml`, `*.junit.xml`, `.coverage`, `htmlcov/`.

**Vérification** : `ruff` (E4/E7/E9/F) sur `src/ UI/ launcher/` après chaque
lot ; tests dédiés des modules touchés verts (105 tests après le premier lot) ;
aucun test supprimé.

---

## 4. Lisibilité, robustesse, factorisation

### 4.1 Source unique du modèle principal (`72bc8db`, `d828436`)

Le nom du modèle principal était écrit en clair à trois endroits du code
(`src/settings.AppConfig.model`, `src/first_run.DEFAULT_MODEL`,
`src/background_tasks.config.DEFAULT_MAIN_MODEL`) et cinq fois dans les scripts
de validation/diagnostic. `src/settings.DEFAULT_MODEL` devient la source
unique ; les autres modules la réexportent sous leur nom historique, donc
**aucun appelant ni test modifié**. Les surcharges `--model` / `GEMINI_MODEL`
restent prioritaires. Valeur inchangée.

### 4.2 Persistance JSON partagée (`36dc5fe`)

Quatre modules réimplémentaient la même mécanique de lecture/écriture d'état
local : `UI/menu_state.py`, `UI/system_actions.py`,
`UI/appearance_actions.py`, `src/modes.py`. Nouveau module `src/json_state.py`
(`load_dict`, `save_dict`) ; chaque appelant garde son schéma, ses validations
champ par champ et ses valeurs par défaut. Bilan : **−91 / +27** lignes dans
les quatre appelants.

Deux gains de robustesse :

- **écritures atomiques** partout (`.tmp` + `os.replace`) — une interruption
  d'écriture ne peut plus laisser un fichier d'état tronqué ;
- seules les erreurs attendues sont masquées (`OSError`, `ValueError` — ce
  dernier couvre JSON corrompu et `UnicodeDecodeError`) au lieu d'un
  `except Exception` aveugle.

**Preuve d'équivalence** : `verify_state_equivalence.py` recharge chaque état
avec le code actuel puis avec une réimplémentation fidèle de l'ancien code,
sur **7 états de fichier × 3 modules = 21 comparaisons, 0 divergence** (fichier
absent, `{}`, objet partiel, racine liste, racine chaîne, JSON corrompu, chemin
vide). Nouveau fichier de tests `tests/test_json_state.py` (10 tests).

### 4.3 Annotations de retour (`f4df2a7`)

Recensement AST : 952 fonctions publiques dans `src/`, `UI/` et `launcher/`,
dont 164 sans annotation de retour — **122 d'entre elles dans `src/tools.py`**,
où les signatures sont dictées par les schémas d'outils exposés à Gemini
(hors périmètre, volontairement).

Les 42 autres sont désormais annotées **avec le type réellement retourné**,
déduit du corps de chaque fonction : gestionnaires d'évènements Qt et boucles
de rendu (`-> None`), cycle de vie audio/Gemini (`AudioIO.start/stop/play/
clear_output/extend_listening/begin_speaking`, `GeminiLive.connect/close/
receive_loop/send_audio`, `can_send -> bool`), passerelles de tâches
(`-> TextModelResponse`), `list_once -> list`, `manager.update` (paramètres
typés), presse-papiers d'écriture (`-> str` / `-> list[tuple[int, bytes]]`),
propriétés Desktop (`cfg.DesktopAppearanceConfig`, `QScreen | None`),
`build_presence_hook -> Callable[[str], None]`, dialogue des tâches
(`-> BackgroundTasksDialog | None`), validations du launcher
(`-> tuple[bool, list[str], list[str]]`).

Point technique : les annotations de `src/ui.py` référencent les widgets via
des imports `TYPE_CHECKING`, pour ne pas casser les imports paresseux (temps de
démarrage) de ce module.

Résultat : **0 fonction publique sans annotation de retour** hors
`src/tools.py` (vérifié par script AST).

---

## 5. Performance — mesures avant/après

Machine : conteneur Linux 2 vCPU, Python 3.11.2, PySide6 6.12.0.
Outils : `bench_audio.py` (microbenchmark) et `bench_output_pending.sh`
(instrumentation en charge réelle). On compare la version 1.8.1 (`66d53f6`,
arbre vierge) à la version 1.8.2.

### 5.1 Microbenchmark `AudioIO` (médiane, µs/appel, 8 000 itérations)

| Chemin | 1.8.1 | 1.8.2 | Écart |
|---|---|---|---|
| `_in` (callback micro, bloc 80 ms) | 16,30 | 18,24 | bruit inter-exécutions (± 2 µs) |
| `_block_rms` (RMS d'un bloc) | 6,70 | 7,33 | bruit inter-exécutions |
| `play` (960 échantillons) | 3,28 | 3,22 | inchangé |
| `_out` (1 024 trames) | 0,76 | 0,76 | inchangé |
| **`output_pending_bytes`** (file de 120 blocs + tampon) | **5,21** | **0,18** | **÷ 29** |
| lecture directe de `_queued_bytes` | 0,10 | 0,10 | témoin (plancher) |

La dispersion mesurée sur `_in` et `_block_rms` (± 2 µs selon les exécutions,
sur les deux versions) est supérieure à tout effet réel : **aucune conclusion
n'en est tirée**. `_block_rms` regroupe la même formule float64 auparavant
dupliquée dans trois chemins, et le coût du verrou n'existe que pour
`output_pending_bytes`.

### 5.2 Mesure en charge réelle (pipeline audio complet, faux serveur Live)

Protocole : les scénarios `TestA_Silence`, `TestB_TwoPhrases` et
`TestStability::test_long_run_counters_are_exact` du harnais d'écho, exécutés
intégralement (≈ 180 s de conversation simulée par exécution, ~6 240 appels à
la fonction), avec une sonde `perf_counter_ns` autour de la fonction.

| Variante | Appels | Temps cumulé | µs/appel |
|---|---|---|---|
| 1.8.1 — scan de la file sous verrou | 6 245 / 6 239 | 39,99 / 43,08 ms | 6,41 / 6,91 |
| 1.8.2 — compteur lu sous verrou | 6 245 | 28,64 ms | 4,59 |
| **1.8.2 — compteur lu sans verrou** | 6 244 / 6 235 | 18,22 / 22,75 ms | **2,92 / 3,65** |

Lecture : la suppression du verrou est un **gain structurel** — le worker audio
n'entre plus en concurrence avec `_out`, le callback temps réel de la carte son
qui tient ce verrou pour remplir le tampon. Le reliquat (~2,7 µs/appel) est de
l'ordonnancement multi-thread (GIL), présent dans les trois variantes ; la
sonde elle-même coûte 0,25 µs/appel (mesurée). Sur 180 s, le temps total passé
dans cette fonction passe de **~40-43 ms à ~18-23 ms**.

**Portée honnête** : rapporté à la durée de la conversation, ce gain représente
environ **0,01 % du temps CPU** — il n'est pas perceptible par l'utilisateur.
L'intérêt réel est (a) la disparition d'un coût **linéaire** en nombre de blocs
en attente sur un chemin appelé trois fois par bloc audio, et (b) la
suppression d'une contention avec le thread temps réel de sortie audio.

### 5.3 Pistes examinées et **écartées**

- `np.dot(x_int16, x_int16)` pour le RMS : trois fois plus rapide, mais
  l'accumulateur int16 **déborde silencieusement** (32767² × 1280) — vérifié
  expérimentalement (RMS saturé à 1,0 au lieu de 32767,0). Non retenu.
- Import paresseux de `google.genai.types` : analysé mais non retenu dans cette
  release (risque de rupture d'API SDK pour un gain de démarrage non mesuré).

---

## 6. Validation

### 6.1 Suite complète, module par module (protocole A/B identique)

Le protocole « un processus pytest par module » est nécessaire ici : ce
conteneur **sans GPU** fait planter PySide6 6.12.0 hors écran
(`Fatal Python error: none_dealloc`) dès qu'un certain nombre de
`QPainterPath.quadTo` sont utilisés dans un même processus — reproduit par un
programme de 6 lignes **sans aucun code Jarvis** (voir §7). Les mêmes modules
tombent à l'identique sur la version 1.8.1.

| | 1.8.1 (`66d53f6`) | 1.8.2 (`HEAD`) |
|---|---|---|
| Modules exécutés | 73 | 74 |
| Modules avec code retour ≠ 0 | 5 | 5 |
| Modules en anomalie **identiques** | `test_menu_backgrounds` (134), `test_menu_layout` (134), `test_menu_render_offscreen` (5, script non pytest), `test_protocol_ui` (134), `test_ui_menu_regression` (134) | idem |
| Tests verts (modules rc=0) | 1 168 | **1 178** |

Le seul module dont le code retour diffère entre les deux arbres est
`test_json_state` (nouveau, absent en 1.8.1). Les cinq anomalies sont
**pré-existantes** : `test_protocol_ui` affiche « 14 passed, 0 failures » avant
de s'arrêter au démontage de l'interpréteur, et les quatre autres modules de
menus tombent de la même façon sur les deux versions.

### 6.2 Autres contrôles exécutés

| Contrôle | Résultat |
|---|---|
| `ruff check src UI launcher run_jarvis.py version.py` | 0 avertissement (après chaque lot) |
| `scripts/check_release_lineage.py` sur HEAD | `LIGNEE : OK`, `CONTRAT UI : OK`, `TOTAL 0 probleme` |
| Équivalence ancien/nouveau `load_state` (21 comparaisons) | 0 divergence |
| Racine `version.py` (ré-export) vs `src/version.py` | cohérents |
| Encodage UTF-8 des fichiers modifiés | intact (comparaison octet à octet des lignes ajoutées) |

### 6.3 Invariants de la release vérifiés

- Modèle principal : `gemini-2.5-flash-native-audio-preview-12-2025`
  (inchangé, `DEFAULT_MAIN_MODEL` reste assertable par la validation v1.8).
- États des tâches d'arrière-plan (`QUEUED`, `RUNNING`, `WAITING_FOR_TOOL`,
  `COMPLETED`, `FAILED`, `CANCELLED`) : inchangés — la suite des tâches
  d'arrière-plan (60 tests) est verte.
- Chemins de configuration/données : inchangés (`src/paths.py` conserve son
  catalogue ; seules deux fonctions mortes ont été retirées).
- Compatibilité Windows : aucun appel `os.fork`, aucun chemin POSIX codé en
  dur ajouté ; les écritures atomiques utilisent `os.replace` (atomique sur
  Windows comme sur POSIX).

---

## 7. Contrôles NON exécutés dans cet environnement

À ne pas confondre avec des validations réussies :

1. **Aucun appel réel à l'API Gemini** (ni Live, ni `generateContent`) : les
   scénarios réels du dépôt (`validate_real_gemini.py`,
   `validate_reconnect_v174_real.py`, `validate_v180_real.py --api`) n'ont pas
   été exécutés faute de clé et pour ne pas consommer de quota. Les scripts ont
   été vérifiés uniquement en import/`--help`.
2. **Aucun build Windows / PyInstaller** : impossible ici (pas de runner
   Windows). Le workflow `build.yml` déclenche le build sur les tags `v*`.
3. **Aucun test sur carte son réelle ni sur micro réel** : seul le harnais à
   carte son simulée a été utilisé.
4. **Absence d'interface graphique réelle** : rendu Qt en mode hors écran
   uniquement.
5. Le crash PySide6 hors écran décrit en §6.1 **n'est pas un défaut du
   dépôt** (reproduit sans code Jarvis) et n'affecte pas Windows, mais il
   empêche toute exécution de la suite complète dans un seul processus ici.

---

## 8. Publication

- Branche : `arena/9076c6a8-jarvis-live-ready`
- Commits (du plus ancien au plus récent) :
  1. `4e89449` — `refactor: remove dead helpers and unused parameters (v1.8.2 cleanup)`
  2. `f05d4fe` — `perf(audio): read the pending-bytes counter instead of rescanning the queue`
  3. `72bc8db` — `refactor(config): single source of truth for the default Gemini Live model`
  4. `d828436` — `refactor(validation): use the shared default model in the real-API scripts`
  5. `922aa10` — `refactor: drop unread state attributes and ignore tool caches`
  6. `36dc5fe` — `refactor: one JSON state helper for the four local state files (dedup + atomic writes)`
  7. `f4df2a7` — `refactor(typing): annotate the return types of the remaining public APIs`
  8. `6b8e415` — `perf(audio): read the pending-audio counter without the output lock`
  9. (commit de version) — `chore(release): v1.8.2`
- Diff total vs `v1.8.1` : 36 fichiers, +301 / −368 (dont
  `src/json_state.py` et `tests/test_json_state.py` ajoutés).
- Tag annoté `v1.8.2` → commit `f2eac2b` (vérifié : `git ls-remote` renvoie
  `refs/tags/v1.8.2` pointant sur `f2eac2b`).
- **Publication déclenchée par le push du tag** (workflow `Build & Release`,
  exécution **38161762028**, conclusion `success`, 17 min 35 s) :
  - `Tests (Windows)` : **succès en 11 min 5 s** — c'est la suite complète
    exécutée sur un vrai runner Windows, avec écran, donc sans le problème
    PySide6 hors écran de ce conteneur ;
  - `Lint` : succès (8 s) ;
  - `Build & Release` : succès (6 min 25 s) — garde-fou de lignée, contrôle
    tag/version, build PyInstaller, installeur, validations, publication.
- **Release publiée** : https://github.com/grimalkin2811/Jarvis_Live_Ready/releases/tag/v1.8.2
  (créée par `github-actions[bot]`, `draft: false`, `prerelease: false`,
  publiée le 2026-10-11 à 18:14:30 UTC, désormais marquée **Latest**).
- **Actifs attachés** (tailles relevées via l'API GitHub) :
  - `JarvisSetup-1.8.2.exe` — 150 746 532 octets ;
  - `Jarvis-v1.8.2-portable.zip` — 155 198 858 octets ;
  - `Jarvis-v1.8.2-portable.zip.sha256` — 94 octets.
- Notes de release : le corps auto-généré par l'action ne contenait que le lien
  de comparaison ; il a été **remplacé** par les notes de cette version
  (résumé du CHANGELOG, mesures, limites explicites), via l'API GitHub.
- Note d'environnement : les *fichiers* d'actifs ne peuvent pas être
  téléchargés depuis ce bac à sable (leur hôte de téléchargement
  `release-assets.githubusercontent.com` n'est pas dans la liste réseau
  autorisée). Leur **existence, leurs tailles et le succès de l'exécution**
  sont en revanche vérifiés via l'API GitHub (api.github.com).

---

## 9. Ce qui reste ouvert (hors périmètre de cette release)

- `src/tools.py` : 122 fonctions publiques sans annotation de retour, par
  conception (schémas d'outils Gemini).
- Chevauchement fonctionnel entre `scripts/emit_pytest_annotations.py` et
  `scripts/emit_test_annotations.py` (reniflage des rapports CI) — conservés
  car le comportement exact des annotations GitHub n'est pas testable ici.
- Les vérifications « API réelle » listées en §7 (audit de concurrence
  `docs/AUDIT_CONCURRENCE_BACKGROUND_V1.8.md` §« Ce qui reste à vérifier »).

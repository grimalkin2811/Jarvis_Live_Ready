# Protocole de test du Dashboard

Protocole **dédié**, indépendant du build PyInstaller et de la release : il
ne produit aucun exécutable, ne publie rien et ne touche à aucun workflow.
Toutes les données sont isolées dans des dossiers temporaires
(`tests/dashboard_support.isolated_jarvis()`) : la mémoire, les notes, les
tâches et les préférences réelles de l'utilisateur ne sont jamais utilisées
(un test le vérifie explicitement pour la mémoire).

## 1. Lancer

```bash
pip install -r requirements.txt -r requirements-dev.txt

# Tout le protocole (≈ 1 min 10 s sur 2 vCPU) → build/dashboard_protocol/summary.json
python scripts/run_dashboard_protocol.py

# Étapes au choix : unit, integration, perf, visual, static
python scripts/run_dashboard_protocol.py --only unit integration
python scripts/run_dashboard_protocol.py --skip visual --fail-fast

# Étapes séparées
python -m pytest -q tests/test_dashboard_*.py tests/test_runtime_status.py \
    tests/test_conversation_listeners.py tests/test_gemini_text_input.py tests/test_pyside_refcount.py
python scripts/dashboard_perf.py --out build/dashboard_perf
python scripts/dashboard_visual_protocol.py --out build/dashboard_screens [--themes jarvis --quick]

# Non-régression : suite complète du dépôt (≈ 8 min)
python -m pytest -q
```

Sous Linux sans écran : `QT_QPA_PLATFORM=offscreen` (positionné
automatiquement par les scripts et les tests du Dashboard). Sous Windows,
aucune variable n'est nécessaire.

> Environnement de cette session : conteneur Linux sans écran ni carte son,
> Python 3.11.2, PySide6 6.11.2. Les bibliothèques système `libGL`,
> `libEGL`, `libxkbcommon` et `libdbus` étant absentes (et non installables),
> des **bibliothèques factices** compilées localement ont été chargées via
> `LD_LIBRARY_PATH` pour que Qt démarre en mode `offscreen`. Elles ne sont
> pas dans le dépôt et n'ont aucune influence sur Windows.

## 2. Carte des tests

| Niveau | Fichier | Ce qui est vérifié |
|---|---|---|
| Unitaire | `test_dashboard_config.py` | Défauts, aller-retour, clés inconnues, fichier corrompu sauvegardé, illisible jamais écrasé, schéma plus récent, migration v0→v1, préréglages + instantané, pages personnalisées, échec d'écriture. |
| Unitaire | `test_dashboard_layout.py` | Points de rupture, tassement sans chevauchement, emplacement libre, bornes min/max, projection étroite non destructive, identifiants dupliqués renommés. |
| Unitaire | `test_dashboard_security.py` | Niveaux 1/2/3, niveau 3 non abaissable, action inconnue = confirmation, chaque action de widget a une politique, journal et rapport sans secret. |
| Unitaire / mémoire | `test_dashboard_memory_service.py` | Migration v2 (base v1 réaliste), création sans écrasement, saisie invalide, historique + diff, **concurrence** (même seconde, écrivains parallèles), suppression, restauration réversible, **échec d'écriture sans corruption**, **cohérence après redémarrage**, base réelle jamais utilisée ; **audit** : migration v2 en échec annulée puis retentée sans perte, purge gardant exactement les 20 versions les plus récentes, restauration d'une version identique refusée sans écriture. |
| Unitaire | `test_dashboard_notifications.py` | Canaux par catégorie, libellé « Dashboard — », anti-doublon, pas de bulle native si Dashboard au premier plan, échec de l'API native toléré, seuls les statuts finaux réels notifient, filtre du pont historique. |
| Unitaire | `test_dashboard_resources.py` | Aucun thread sans abonné, suspension/reprise, intervalle borné, erreurs de mesure tolérées, seuils sur valeurs réelles, psutil réel. |
| Unitaire | `test_runtime_status.py` | Santé (« opérationnel » exige moteur prêt + audio), crochets isolés, masquage des secrets, envoi de texte sans moteur expliqué, **mises à jour concurrentes**. |
| Unitaire | `test_conversation_listeners.py` | Écouteurs du contexte appelés hors verrou, erreurs isolées. |
| Unitaire | `test_pyside_refcount.py` | Garde-fou PySide6 : pas de fuite de références `None` (régression de PySide6 6.12.0, cf. §7). |
| Unitaire | `test_suite_isolation.py` | **Audit pré-fusion** : toute la session pytest pointe vers un dossier temporaire (`tests/conftest.py`) — chemins par défaut, gestionnaire mémoire par défaut, rétablissement après un test qui retire `JARVIS_DATA_DIR`. |
| Intégration (faux serveur) | `test_gemini_text_voice_interplay.py` | **Audit pré-fusion**, vrai `GeminiLive` + boucle de reconnexion de `ui.py` : voix → texte → voix dans **une** session et **un** contexte ; refus « occupé » pendant un tour vocal ; aucun audio micro mêlé à un texte en attente (interruption toujours prioritaire) ; relance de génération vide d'un texte (micro retenu, pas de doublon) ; interruption serveur ; appel d'outil ; coupure + reconnexion ; nouvelle conversation ; **course du filet de 30 s entre deux textes** ; **annulation de l'envoi** ; envois simultanés depuis deux threads ; tour purement vocal sans effet du chemin texte. Ne valide PAS le vrai service. |
| Intégration | `test_gemini_text_input.py` | Texte = tour ordinaire de la **même** session, micro retenu pendant la réponse, chien de garde, **2 envois simultanés → 1 accepté, 1 « occupé »**, états occupés, validation, échec d'envoi, appel depuis un autre thread, chemin audio inchangé sans texte. |
| Intégration | `test_dashboard_widgets.py` | Contrat de **tous** les widgets du registre, cycle de vie complet (0 connexion, 0 minuteur après désactivation), même widget deux fois = une seule source de vérité, panneau personnalisé, échec d'activation contenu, type inconnu. |
| Intégration | `test_dashboard_window.py` | Ouverture légère, navigation, commande depuis un autre thread, **bascule d'interface sans toucher moteur/conversation/micro**, 3 modes de fermeture, plein écran, états étroit/normal/large, OnePage (diagnostics en dernier, seuls les widgets visibles actifs), mode édition (ajouter, déplacer, masquer, retirer, annuler, enregistrer, relire après redémarrage), données métier préservées, thème à chaud persisté, préférences de fenêtre relues après redémarrage, animations réduites, préférences corrompues non bloquantes ; **audit** : fenêtre fermée (libérée) pendant un envoi, fenêtre libérée insensible aux changements d'état du moteur, outil vocal `open_dashboard` qui annonce la page réellement affichée (page inexistante signalée, Dashboard déjà ouvert). |
| Intégration | `test_dashboard_conversation_widget.py` | Hors ligne (raison réelle, texte conservé), envoi via le contexte partagé, **envoi concurrent explicite**, refus « occupé », échec, message trop long, tours vocaux + transcriptions partielles, tour échoué, échappement HTML, **widget détruit avant la confirmation du moteur : aucune erreur, tour affiché à la réouverture** (audit). |
| Intégration | `test_dashboard_tasks.py` | Statuts réels, jamais pause/reprise, annuler/relancer selon le service, Task Manager absent expliqué, widget mis à jour par les hooks réels. |
| Intégration | `test_dashboard_entry_points.py` | Alias de pages, pont sans consommateur, outils vocaux honnêtes sans interface et avec confirmation réelle, entrée du menu de la zone de notification, Ctrl+D dans l'orbe, isolation des erreurs, option `--dashboard`. |
| Intégration | `test_dashboard_productivity.py` | Note écrite dans le Dashboard = note de l'outil vocal (même fichier), saisie invalide refusée sans écriture, documents réels seulement (fichier disparu signalé, jamais « ouvert »), tâches personnelles ajoutées/terminées dans la base de l'outil vocal, mise à jour : notification unique, « à jour » affirmé seulement avec une version publiée connue, échec jamais présenté comme un succès. |
| Performance | `test_dashboard_perf.py` | Aucun import lourd à la création du contrôleur, coût nul Dashboard masqué, cadres en cache, changement de page et première ouverture interactifs. |
| Visuel | `scripts/dashboard_visual_protocol.py` | Captures reproductibles (voir §4). |
| Performance | `scripts/dashboard_perf.py` | Mesures chiffrées avec critères (voir §3). |
| Non-régression | suite complète `pytest` | Tous les tests historiques du dépôt. |

## 3. Performance

Mesures : `scripts/dashboard_perf.py` (fenêtre hors écran, données isolées,
moteur **simulé**, 12 widgets sur 6 pages, préréglage Cockpit).

| Critère | Seuil | Justification |
|---|---|---|
| Première ouverture de la fenêtre | ≤ 1 500 ms | Ouverture à la demande : au-delà d'1,5 s l'action paraît ignorée. |
| Changement de page (médiane / max) | ≤ 150 / 400 ms | Sous ~150 ms une navigation est perçue comme immédiate. |
| Latence max de la boucle Qt pendant les chargements | ≤ 150 ms | Au-delà de 100–150 ms une interface est perçue comme figée ; tout accès disque/réseau passe par le pool de travaux. |
| CPU au repos, Léger | ≤ 2 % d'un cœur | Aucun minuteur d'animation. |
| CPU au repos, Normal | ≤ 10 % | Seul l'emblème est animé. |
| CPU au repos, Poussé | ≤ 20 % | Fond animé à 8 i/s en plus ; niveau choisi explicitement. |
| CPU Dashboard masqué | ≤ 2 % | Jarvis tourne souvent des heures sans Dashboard visible. |
| État masqué | 0 widget actif, 0 minuteur, mesures arrêtées | Les widgets inactifs ne consomment rien. |

Résultats de cette session (Linux 6.1, 2 vCPU, `offscreen`, 3 exécutions) :

| Mesure | Exéc. 1 | Exéc. 2 | Exéc. 3 (protocole) | Seuil |
|---|---|---|---|---|
| Première ouverture | 167,2 ms | 166,7 ms | 168,8 ms | 1 500 |
| Changement de page médiane / max | 22,7 / 60,2 ms | 22,3 / 62,4 ms | 21,6 / 61,3 ms | 150 / 400 |
| Latence max boucle Qt | 94,5 ms | 93,4 ms | 95,5 ms | 150 |
| CPU repos Léger / Normal / Poussé | 0,97 / 1,63 / 13,49 % | 1,15 / 1,76 / 12,42 % | 0,99 / 1,70 / 12,86 % | 2 / 10 / 20 |
| CPU masqué | 0,88 % | 0,86 % | 0,94 % | 2 |
| État masqué | 0 / 0 / arrêtées | 0 / 0 / arrêtées | 0 / 0 / arrêtées | — |
| Initialisation du contrôleur | 3,1 ms | 3,0 ms | 3,0 ms | (info) |
| OnePage : 1er affichage / construction complète | 113,4 / 281,5 ms | 117,2 / 282,7 ms | 115,4 / 285,3 ms | (info) |

Avant optimisation (même machine), le fond animé plein écran coûtait 49 %
(Normal) et 77 % (Poussé) d'un cœur, l'initialisation 421 ms (import du
Task Manager dans le thread graphique) et la latence max 181 ms. Corrections :
animation limitée à l'emblème en Normal, cadres peints en cache, fond à
8 i/s en Poussé seulement, import différé (`sys.modules`).

Limites : rendu logiciel hors écran, sans GPU ni compositeur ; les valeurs
absolues sur un PC Windows réel seront différentes (à mesurer avec le même
script, cf. §6).

### 3.1 Première ouverture sous Windows natif (D01, correctif PR #50)

Validation du 2026-10-16 (Pentium Gold G5400) : fenêtre affichée en 1 867 ms
(seuil 1 500), contenu complet en 1 910 ms — le contenu ne coûtait que ~45 ms,
tout le temps précédait la première peinture. Hors écran (Linux), la même
ouverture prend ~45 ms : la cause est propre à Windows natif.

`scripts/dashboard_first_open_profile.py` découpe le MÊME évènement en phases
(import de la fenêtre, construction, affichage, première peinture, contenu),
dans des processus neufs répétés, chronomètre chaque étape du constructeur et
liste les fichiers projetés en mémoire pendant la construction. Exécuté sur un
runner Windows natif (`.github/workflows/pr-windows-native.yml`) :

| Processus | Première ouverture | dont `rebuild_pages` |
|---|---|---|
| 1 (froid) | 3 656 à 4 807 ms | 3 477 ms |
| 2 | ~560 ms | 552 ms |
| 3 à 5 | ~105 à 121 ms | ~34 ms |

Pendant `rebuild_pages`, des dizaines de fichiers de police sont chargés
(Candara, Cascadia, Gabriola, Ink Free, Leelawadee, Nirmala, Sitka, Yu Gothic,
Arial…) : les boutons de navigation portent des symboles (⌂ ◈ ◎ ☑ ⚙ ✎…)
absents de la police d'interface, et faute de famille désignée Qt parcourt la
liste de repli du système en chargeant chaque police jusqu'à trouver le
glyphe — coûteux à froid (antivirus, cache disque). Correctif : les chaînes de
polices des thèmes se terminent par des polices de symboles explicites
(`SYMBOL_FONT_FALLBACKS` : Segoe UI Symbol, Segoe UI Emoji, Apple Symbols,
Noto Sans Symbols 2, DejaVu Sans). Seuil inchangé ; D01 rapporte désormais la
part construction / affichage et les polices chargées pendant l'ouverture.

Le runner GitHub n'est pas le PC de validation : ces chiffres sont un repère
Windows natif ; la mesure qui fait foi reste D01 sur le PC cible.

### 3.2 Latence de la boucle et OnePage (C10)

Le rapport du 2026-10-16 affichait « perf_measures=ÉCHEC (… OnePage : premier
affichage 179) » : la dernière ligne, indicative, et jamais le critère dépassé.
`dashboard_perf.py` termine désormais par « Critères en échec : … » et le
résumé du protocole contient `failed_checks` et les mesures clés, repris par
C10. Le seul bloc mesuré au-delà de 150 ms sur le PC était le premier
affichage OnePage (179 ms, une section entière construite d'un bloc) : OnePage
construit maintenant quelques widgets par tour de boucle (budget de 12 ms,
au moins un widget), soit 102 → ~64 ms hors écran pour le premier affichage
et 44 → 27 ms pour le plus gros bloc d'une section.

## 4. Protocole visuel et ergonomique

`scripts/dashboard_visual_protocol.py` produit 24 captures + `index.json` :
6 pages × 3 thèmes, OnePage (haut / bas avec diagnostics), état étroit
(820 px), état normal (1 200 px), mode édition, confirmation de niveau 3.
Données de **démonstration** isolées (souvenirs, tâches, notes, documents,
une tâche en cours, une terminée, une échouée, une notification réseau) et
état moteur **simulé** « connecté ».

Liste de contrôle appliquée à chaque capture :

1. Hiérarchie : l'Accueil montre d'abord état, conversation et tâches.
2. Aucun texte tronqué net (réduction avec « … » + infobulle), aucun
   chevauchement de widgets, aucune case vide inexpliquée.
3. États explicites : vide, chargement, erreur, indisponible (avec raison).
4. Identité de thème reconnaissable (formes, fond, typographie, couleurs).
5. Lisibilité (contraste texte/fond, tailles) en densité normale.
6. Le sélecteur de modèle apparaît verrouillé ; aucune action fictive.
7. États étroit/normal : navigation et en-tête adaptés, rien d'inaccessible.
8. Mode édition : poignées et boutons visibles sur chaque cadre.

Captures revues dans cette session (sélection versionnée dans
[screenshots/](screenshots/)) : Accueil ×3 thèmes, Tâches, Mémoire avec
historique et diff, Système (EDITH), Paramètres (FRIDAY), OnePage bas
(diagnostics en dernier), étroit, édition, dialogue de niveau 3. Défauts
trouvés et corrigés au fil des passes : glyphes absents de la police
(remplacés), `letter-spacing` QSS non supporté (police Qt), arcs JARVIS,
titres tronqués (réduction « … »), widget Ressources, notifications
éphémères, navigation et en-tête étroits, OnePage incomplète, bulles FRIDAY,
pied du Journal d'activité tronqué.

## 5. Non-régression (suite complète)

| Exécution | Résultat |
|---|---|
| Référence avant modifications (`main` @ `ec944e2`, PySide6 6.11.2) | **1 332 réussis**, 691 sous-tests, 20 avertissements, 0 échec — 460,95 s |
| Après modifications (1re exécution) | **1 485 réussis**, 700 sous-tests, 20 avertissements, 0 échec — 460,40 s |
| Après modifications (2e exécution, 2 tests ajoutés) | **1 487 réussis**, 700 sous-tests, 20 avertissements, 0 échec, 0 ignoré — 462,49 s |
| Après modifications (exécution finale : correctifs mise à jour / tâches perso + `test_dashboard_productivity.py`) | **1 495 réussis**, 700 sous-tests, 20 avertissements, 0 échec, 0 ignoré — 464,23 s |
| Audit pré-fusion : suite rejouée sur `d3f1fbe` | **1 495 réussis**, 700 sous-tests, 20 avertissements, 0 échec — 467,59 s |
| Audit pré-fusion : après corrections (23 tests ajoutés, `HOME` vierge) | **1 518 réussis**, 700 sous-tests, 20 avertissements, 0 échec, 0 ignoré — 466,18 s ; aucun accès aux données réelles |

Commande : `QT_QPA_PLATFORM=offscreen python -m pytest -q -p no:cacheprovider -ra`.
Le protocole dédié (`run_dashboard_protocol.py`) a été exécuté en entier :
unitaires 88 réussis, intégration 64 réussis (+ 3 sous-tests), performance
3 réussis + mesures (§3, exéc. 3), 24 captures, ruff et compileall sans
erreur. `test_dashboard_productivity.py` (8 tests) a été ajouté ensuite :
ensemble des tests du Dashboard = 163 réussis (+ 3 sous-tests).
Audit pré-fusion (voir [AUDIT_PRE_FUSION.md](AUDIT_PRE_FUSION.md)) : protocole
rejoué en entier après corrections → unitaires 95, intégration 88 (+ 3
sous-tests), performance 3 + mesures dans les seuils, 24 captures, ruff et
compileall sans erreur — **SUCCÈS**.

CI GitHub sur la PR brouillon #50 (commits `f915b24` et `a4f85db`, résultats identiques) :

| Tâche | Résultat |
|---|---|
| `Tests (Windows)` — `windows-latest`, Python 3.11, `pytest -q` (suite complète) + diagnostics unittest | **réussi** (12 min 48 s puis 12 min 43 s ; décomptes non lisibles depuis l'environnement de développement, journaux inaccessibles) |
| `Lint` — `ruff check` (ubuntu) | **réussi** |
| `Validation reelle (audio, reconnexions, outils)` | tâche « réussie » mais verdict **NON TESTABLE** : « aucune clé API disponible dans cet environnement » (secret `GEMINI_API_KEY` non fourni au workflow) — **aucune validation Gemini réelle** |
| `Build & Release` | ignoré (ne s'exécute que sur un tag `v*`) — aucun build ni publication |

Les 20 avertissements sont des `DeprecationWarning` préexistants
(`QMouseEvent` dans `test_desktop_appearance_dialog.py` et
`test_protocol_ui.py`), identiques avant/après.

## 6. Validation Windows — liste de contrôle (NON exécutée ici)

À dérouler sur un PC Windows 10/11 réel, avec micro, haut-parleurs et une
clé Gemini valide. Rien de ce qui suit n'a pu être vérifié dans
l'environnement Linux de développement (la CI Windows n'exécute que les
tests automatisés, hors écran, sans audio ni session Gemini).

1. `python run_jarvis.py --dashboard` : l'orbe/Desktop **et** le Dashboard
   s'ouvrent ; la voix fonctionne.
2. Pendant une réponse vocale longue : ouvrir/fermer le Dashboard (Ctrl+D,
   menu de la zone de notification, « ouvre le Dashboard » à la voix) — la
   voix ne se coupe pas, la session n'est pas reconnectée (page Système →
   Diagnostics : le « Session n° » ne change pas).
3. Basculer Orbe ↔ Desktop depuis le Dashboard pendant une tâche longue :
   la tâche continue, la conversation est conservée.
4. Écrire un message dans le widget Conversation : réponse dite à voix haute
   et transcrite ; pendant que Jarvis parle, l'envoi est refusé « occupé »
   et le texte reste dans le champ.
5. Couper le réseau : état « Hors ligne », envoi désactivé avec la raison ;
   rétablir : reconnexion et retour à « Opérationnel ».
6. Notifications natives : tâche terminée Dashboard fermé → bulle
   « Dashboard — Tâche terminée » ; Dashboard au premier plan → pas de bulle ;
   pas de doublon avec la bulle historique.
7. Modes de fermeture : persistant (✕ masque), interactif (services
   réduits, la voix continue), arrêt complet (confirmation renforcée avec
   tâches actives listées, puis arrêt propre).
8. Plein écran (F11/Échap), redimensionnement, écrans à haute densité
   (125 %/150 %), deux écrans ; géométrie restaurée au redémarrage.
9. « Afficher les animations dans Windows » désactivé → niveau effectif Léger.
10. `python scripts/dashboard_perf.py` sur la machine cible (seuils §3) et
    Gestionnaire des tâches : CPU de Jarvis Dashboard masqué ≈ inchangé.
11. Mémoire : créer / modifier / supprimer / restaurer depuis le Dashboard
    puis demander à la voix « que sais-tu de… » : cohérent.
12. Build PyInstaller (`packaging/jarvis.spec`, workflow existant) :
    l'exécutable ouvre le Dashboard (le paquet `UI.dashboard` est déclaré
    dans les imports cachés).
13. (Audit pré-fusion) Écrire un message, puis fermer aussitôt le Dashboard
    (mode interactif avec « libérer la fenêtre ») : la réponse est dite à voix
    haute, rien d'anormal dans les journaux ; à la réouverture, le message et
    la réponse sont affichés.
14. (Audit) Enchaîner voix → message écrit → voix : la réponse écrite tient
    compte de ce qui a été dit, puis la voix continue sans reconnexion
    (« Session n° » inchangé) ; pendant que Jarvis répond au message écrit,
    parler ne crée pas de second tour (sauf « stop »/interruption).
15. (Audit) « Ouvre le Dashboard sur la page météo » (page inexistante) :
    Jarvis dit que la page est introuvable et quelle page est affichée.
16. (Audit) Une conversation ordinaire qui mentionne un tableau de bord
    (« le tableau de bord de ma voiture affiche un voyant ») n'ouvre pas le
    Dashboard — dépend du modèle, **non testable sans clé**.

## 7. Point d'attention : PySide6 6.12.0

Pendant l'audit, la suite de référence s'arrêtait brutalement
(`none_dealloc`, code 134) avec PySide6 6.12.0 (publié le 2026-10-08) sur
CPython 3.11 : fuite d'une référence `None` par appel de méthode `void`.
`requirements.txt` épingle désormais `PySide6<6.12` (6.11.2 vérifié) et
`tests/test_pyside_refcount.py` détecte la régression.

Reproduction de l'audit pré-fusion (venv isolé, Linux x86-64, CPython 3.11.2,
roues `manylinux` de PySide6-Essentials/shiboken6 6.12.0) :
`test_pyside_refcount.py` échoue avec une dérive de **−2000** (exactement une
référence perdue par appel, 500 × 4) puis l'interpréteur s'arrête
(`Fatal Python error: none_dealloc`, code 134) ; une boucle `save()/restore()`
de QPainter fait tomber le processus après ~5 100 appels (moins d'une
seconde) alors que 6.11.2 encaisse 92 millions d'appels en 20 s sans dérive.

Limites : toutes les reproductions (ici et publiques) sont sous **Linux** ;
l'effet des roues **Windows** de 6.12.0 n'est pas prouvé (la CI Windows de
`main`, non bornée, est restée verte après le 8 octobre, ce qui n'est pas
concluant : le nombre d'appels avant épuisement dépend du compteur initial
de `None`). La v1.8.2 publiée (construite le 2026-10-11, dépendance non
épinglée) a très probablement embarqué 6.12.0 : vérification à faire sur
Windows (version de `_internal\PySide6\Qt6Core.dll`), les fichiers de
release n'étant pas téléchargeables depuis l'environnement de développement.
La borne s'applique partout où elle doit l'être (CI Windows, `build_windows.ps1`
pour l'exécutable et le launcher, `setup.bat`, `verify-release.yml`) ; le
workflow de validation Gemini n'installe pas PySide6. NB : les 6.11.x
exigent Python < 3.15 — sans effet aujourd'hui (onnxruntime n'a aucune roue
3.15), à reprendre avant toute montée de version de Python.

**État après la validation Windows du 2026-10-16 (PR #50)** :

* l'installation quotidienne du PC de validation embarque bien Qt 6.12.0
  (`Qt6Core.dll` 6.12.0.0) — construite avant la borne ; sur ce PC, B07 n'a
  mesuré **aucune dérive** sur 20 000 appels avec PySide6 6.12.0. Indice
  favorable pour Windows, pas une preuve générale : la borne est conservée
  (le défaut reste reproduit sous Linux / Python < 3.12, et un seul PC ne
  couvre pas tous les chemins d'appel) ;
* B05 ne déclare plus cette installation « en échec » : être hors de la plage
  validée par la PR n'est pas une incompatibilité démontrée ; c'est une
  installation antérieure, à mettre à jour après fusion. Restent des échecs
  stricts : composants Qt de versions différentes, ou build portant le
  contrôle (`qt_versions.json`, désormais embarqué dans l'application) qui ne
  correspond pas à ce qu'il embarque ou sort de la plage ;
* build de validation de la PR sur un runner Windows
  (`.github/workflows/pr-windows-native.yml`) : versions attendue (contrainte
  `<6.12`, PySide6 6.11.2), embarquée (`Qt6Core.dll` 6.11.2.0) et chargée
  (`Jarvis.exe --qt-versions` : PySide6, shiboken6 et Qt 6.11.2) identiques,
  test de fumée réussi.

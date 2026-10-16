# Validation finale automatisée d'une PR — `valider_pr.bat`

Une seule commande, à lancer sur le PC Windows qui fait tourner Jarvis :

```bat
valider_pr.bat
```

(double-clic dans l'explorateur, ou dans une console à la racine du dépôt). Le lanceur trouve Python
(dans l'ordre : `py -3.11`, le `.venv` du dépôt — utilisé uniquement pour exécuter l'orchestrateur —,
`py -3`, `python`), lance `scripts\validation\valider_pr.py` et transmet son code de sortie.

Durée : 30 à 60 minutes. La première exécution installe un environnement Python isolé (environ 1 Go,
réutilisé ensuite).

Avant de lancer : **fermer Jarvis** (sinon la preuve par instantané de la non-modification des données
n'est pas concluante). Pendant les contrôles du Dashboard et de Gemini, de vraies fenêtres du Dashboard
s'ouvrent, se ferment et passent brièvement en plein écran, et une icône Jarvis temporaire apparaît
dans la zone de notification : ne pas cliquer dedans et ne pas fermer la console.

## Ce qui est vérifié

| Section | Contenu |
|---|---|
| A. Dépôt, commit, PR | La tête **exacte** de la PR distante est récupérée (`git fetch` vers `FETCH_HEAD` uniquement) et testée dans une copie isolée (`git worktree` dans le dossier de validation). L'état de la PR, la CI, les nouveaux commentaires et les changements depuis l'audit (`53ed72e`) sont relevés. `main` et les modifications locales sont comparés avant/après. |
| B. Environnements | Venv de validation créé avec `requirements.txt` + `requirements-dev.txt` de la PR (comme la CI). Le `.venv` de développement et l'installation quotidienne (`%LOCALAPPDATA%\Jarvis\app`) sont **inspectés sans être modifiés** : versions de Python, PySide6, Qt (`Qt6Core.dll`). Le défaut de PySide6 6.12.0 est mesuré sur ce PC dans un venv **jetable**. Inspection des scripts de lancement, de packaging et de mise à jour. |
| C. Tests automatisés | Suite pytest complète (protocole CI) sous la garde des données, avec JUnit et journal de progression (un plantage natif est détecté et le test en cours identifié). Vues par domaine **extraites de la même exécution** (aucun double comptage). Étapes diagnostiques `unittest` de la CI, protocole officiel du Dashboard, ruff, compileall, tests de fumée du code de la PR et de l'exécutable installé. |
| D. Dashboard réel | Fenêtres Qt **natives** (plateforme `windows`) : ouverture, outil vocal, page inconnue, zone de notification, Ctrl+D de l'orbe, pages et widgets, navigation chronométrée, mode édition et enregistrement, thèmes, notes, tâches, tâches de fond, notifications (dont une vraie notification Windows si autorisée), six fermetures/réouvertures avec contrôle des exceptions et de la libération mémoire, persistance, plein écran, CPU au repos. Captures d'écran natives. |
| E. Gemini Live réel et audio | Si une clé est disponible et autorisée : les deux scripts officiels (`validate_real_gemini.py`, `validate_reconnect_v174_real.py`) et les scénarios 1 à 10 Dashboard + Gemini (`gemini_reel_dashboard.py`). Les énoncés (voix réelle synthétisée) passent par la **vraie `AudioIO` et le pont micro de production**, alimentés par une fausse carte son qui capture **en continu** comme un vrai micro (la VAD de Gemini voit la fin de chaque phrase) ; l'interruption est un vrai barge-in vocal pendant la lecture. Inventaire des périphériques audio sans capture ; test guidé du haut-parleur et du micro dans la fenêtre finale. |
| F. Mémoire et données | Migration, restauration et limite d'historique (bases temporaires) ; migration de **votre** base sur une **copie** (si autorisée) ; recherche de secrets dans toutes les sorties ; instantanés avant/après des données réelles ; incidents de la garde. |

## Garanties

* **Données personnelles** : chaque processus Python du protocole charge `scripts/validation/garde/`
  (via `PYTHONPATH`), qui refuse et consigne tout accès sous `%LOCALAPPDATA%\Jarvis`,
  `%APPDATA%\Jarvis`, `~\.jarvis` (et `JARVIS_DATA_DIR` s'il est défini), ainsi que toute écriture
  dans votre copie de travail. Les processus reçoivent en plus des dossiers de données temporaires.
  Les instantanés avant/après (taille, date, SHA-256) prouvent l'absence de modification.
* **Installation quotidienne et `.venv`** : jamais modifiés, désinstallés ni mis à jour.
* **Dépôt** : aucune fusion, aucun push, aucun tag, aucune publication ; la copie de travail et ses
  modifications locales ne sont pas touchées (seul le nouveau rapport est ajouté dans
  `docs/validation/rapports/`).
* **Clé Gemini** : lue seulement après autorisation, transmise par variable d'environnement aux seuls
  processus réels, jamais affichée, jamais écrite ; toutes les sorties sont masquées puis analysées.
* **Outils vocaux** : pendant les scénarios réels, le modèle voit les outils de production mais seuls
  les outils sans effet sur le PC s'exécutent (`scripts/validation/outils_surs.py`). Aucun message
  n'est envoyé à un tiers.
* **Audio** : aucune capture sans avertissement explicite dans la fenêtre finale ; 3 secondes en
  mémoire, niveau mesuré, tampon effacé ; rien n'est enregistré.

## Fenêtres

1. **Fenêtre de lancement** (au début) : autorisations — Gemini réel, notification Windows de test,
   migration de la mémoire sur copie, test audio guidé. Démarrage automatique avec les choix affichés
   après 3 minutes. Sans interface graphique, les mêmes questions sont posées dans la console.
2. **Fenêtre finale** (à la fin, facultative) : confirmation visuelle des captures natives,
   notification vue ou non, test du haut-parleur et du micro. Toute question sans réponse est notée
   « NON TESTÉ ». Fermeture automatique après 15 minutes d'inactivité.

`valider_pr.bat --non-interactif` n'affiche aucune fenêtre (choix par défaut ; Gemini réel seulement
si une clé est trouvée ; tests guidés « NON TESTÉ »).

## Résultats

* Rapport versionné : `docs/validation/rapports/validation_pr50_<AAAAMMJJ-HHMMSS>.md` — un nouveau
  fichier à chaque exécution (suffixe `_2`, `_3`… si le nom existe : jamais d'écrasement).
* Copie locale et journaux : `%LOCALAPPDATA%\JarvisValidation\runs\<horodatage>\` (`rapport.md`,
  `rapport.json`, `journaux\`, JUnit, captures du Dashboard, incidents de la garde).
* Le rapport est réécrit après chaque étape et à la fin, même en cas d'échec, d'erreur ou de Ctrl+C.

Statuts (un seul par contrôle) : **RÉUSSI**, **ÉCHEC**, **NON TESTÉ**, **BLOQUÉ**, **NON APPLICABLE**.
Un contrôle non exécuté n'est jamais compté comme réussi ; une exécution hors écran ou un faux
serveur ne valide jamais Windows ou Gemini réel.

| Code | Verdict |
|---|---|
| 0 | PR prête à être proposée à la fusion — tous les contrôles **requis** sont RÉUSSIS (Windows natif, Gemini réel et audio compris). La fusion reste une décision humaine. |
| 1 | PR bloquée — au moins un contrôle requis en ÉCHEC ou un problème bloquant. |
| 2 | Validation insuffisante — aucun échec bloquant, mais des contrôles requis NON TESTÉS ou BLOQUÉS. |
| 3 | Erreur du protocole ou interruption (le rapport est tout de même écrit). |

## Contrôles humains (fenêtre finale)

Ces contrôles ne sont pas automatisables ; sans réponse, ils restent « NON TESTÉ » (jamais comptés
comme réussis) et une réponse « non » est un ÉCHEC. **E14 et E15 sont requis** pour le verdict « prête
à être proposée à la fusion » ; D18 et D19 sont recommandés (non requis). Terminer la fenêtre sans
avoir fait E14/E15 demande une confirmation explicite.

* **D18 — captures natives** (vignettes affichées dans la fenêtre finale ; originaux `D*.png` dans
  le dossier de l'exécution) : répondre « oui » seulement si, sur **toutes** les captures :
  1. chaque page affiche ses widgets (aucune zone vide, aucun texte coupé ou superposé) ;
  2. `D01_ouverture.png` montre la page **avec son contenu** — l'état « Chargement du tableau de
     bord… » ne doit apparaître qu'un instant à la première ouverture, jamais sur une capture ;
  3. le thème est lisible (contraste du texte, icônes visibles) et les captures de thèmes diffèrent ;
  4. le Dashboard en plein écran et restauré a la bonne taille (aucune fenêtre tronquée).
* **D19 — notification Windows** : cocher « Notification Windows de test » dans la fenêtre de
  lancement ; une seule notification « Validation Jarvis » est émise pendant D10 ; répondre « oui »
  si elle est apparue (coin de l'écran ou centre de notifications), sinon « non » ; « pas regardé »
  si vous n'étiez pas devant l'écran (NON TESTÉ).
* **E14/E15 — haut-parleur et micro (requis)** :
  * prérequis : cocher « Test guidé haut-parleur/micro » dans la fenêtre de lancement ; haut-parleurs
    ou casque allumés, volume audible ; micro branché, non coupé et autorisé (Windows : Paramètres ›
    Confidentialité et sécurité › Microphone) ;
  * E14 : « Jouer une tonalité de 1 s » puis répondre « oui » seulement si la tonalité a été entendue ;
  * E15 : « Tester le micro » puis parler normalement pendant 3 s ; le niveau est mesuré en mémoire,
    bloc par bloc, et rien n'est enregistré ni envoyé ;
  * formats : ceux de Jarvis (`AudioIO`) — sortie 24 kHz mono 16 bits par blocs de 1 024 échantillons,
    entrée 16 kHz mono 16 bits par blocs de 1 280. E13 (inventaire des périphériques) ne remplace
    jamais E14/E15 : un périphérique présent n'est pas un son entendu ni une voix captée.
* **B09 — compatibilité de l'installation quotidienne** : demande d'autoriser la « migration de la
  mémoire sur une copie » dans la fenêtre de lancement (sans quoi B09 reste NON TESTÉ). Trois preuves :
  base mémoire migrable par la PR (copie), configuration réelle lue sans perte (copie assainie, aucune
  valeur personnelle ni clé recopiée), mise à jour réversible sans toucher aux données (tests de C01).

## Lire un échec des scénarios E1–E10

Chaque ÉCHEC joint, dans `gemini_reel.json` (champ `preuve`) et le journal, l'état exact du moteur
au moment de l'échec (aucune clé, valeurs masquées) :

| Champ | Lecture |
|---|---|
| `moteur` | `engine_snapshot()` : session ouverte/prête, `session_id`, `turn_id`, `speaking`, `regeneration_pending`, `text_turn_pending`, `turn_open`, `model_output_seen` (la réponse a-t-elle commencé ?), `pending_response` (ce qui est attendu du serveur), `interrupted_generation_pending`, `seed_reply_pending`, `last_interruption` (classification et origine), `last_server_message_age_s`, nombre de sessions, `stalls_recovered`. |
| `porte_micro` | Raison pour laquelle le micro n'était pas transmis (`jarvis parle`, `message texte en cours`, `session en preparation`…). |
| `audio` | Jarvis éveillé, voix en cours, octets de lecture encore en attente. |
| `erreurs_session` | Dernières erreurs de session : classe, message, `code`/`status` d'API, code WebSocket. |
| `traces_gemini` | Fin du cycle de tour (`TURN_OPEN`, `INTERRUPTION`, `RESPONSE_STALLED`, `SESSION_READY`…). |

`RESPONSE_STALLED` signale que le serveur s'est tu alors qu'une réponse était due : Jarvis clôt le
tour en échec (demande conservée) et relance la session après 30 s (`JARVIS_LIVE_STALL_TIMEOUT`).

Interruptions du serveur (`INTERRUPTION`, champs `classification` et `origin`) :

| `classification` | Sens | Effet |
|---|---|---|
| `interruption_reponse` | Jarvis parlait, ou un arrêt était demandé | Tour clos, interruption signalée ; le `turn_complete` qui suit clôt ce tour. |
| `generation_annulee_avant_reponse` | Le modèle n'avait encore rien produit (pause entre deux phrases, ou `clientContent` de Jarvis) | Tour **gardé ouvert** (`TURN_KEPT_OPEN`) : la suite de la parole s'y ajoute, la réponse lui revient ; jamais de relance. |
| `traine_vide` | Aucun tour en cours | Rien n'est signalé. |

`origin` : `barge_in_local`, `arret_manuel`, `client_content` (un message de Jarvis vient de partir)
ou `activite_vocale_serveur` (parole détectée par la VAD du serveur). Un `turn_complete` accompagné de
`interaction_status=IN_PROGRESS` sur un tour encore muet (`EMPTY_TURN_COMPLETE_IN_PROGRESS`) signifie
que la réponse est encore à venir : aucune relance n'est envoyée. `EMPTY_GENERATION_RETRY` n'est émis
que pour une vraie génération vide (`IDLE`), précédé d'un `AUDIO_STREAM_END`.

## Verdicts des scripts officiels réels (E11, E12)

`validate_real_gemini.py` et `validate_reconnect_v174_real.py` déclarent chacun de leurs contrôles :

* **PASS** — exercé contre le service réel et vérifié ;
* **FAIL** — exercé et incorrect, ou sans réponse dans le délai (produit ou harnais), avec le
  diagnostic du moteur ;
* **NON TESTABLE** — prérequis absent seulement (clé refusée, quota épuisé, réseau injoignable),
  avec sa raison.

`validate_real_gemini.py` alimente Jarvis par un micro simulé CONTINU filtré par `can_send()` (comme
le pont micro de production), attend réellement chaque reconnexion et ne compte les rejeux de
contexte qu'après elle.

## Limites assumées

* La conversation avec **votre propre voix** au micro n'est pas automatisable : les scénarios réels
  envoient une voix synthétisée (SAPI français local, sinon synthèse Gemini) par `AudioIO` et le
  pont micro de production (capture continue simulée) ; le micro lui-même est vérifié par le test
  guidé (niveau sonore).
* « Entendre » le haut-parleur et juger le rendu visuel demandent un humain (fenêtre finale).
* Si Jarvis est ouvert pendant la validation, il peut écrire lui-même dans ses données : la preuve
  par instantané devient non concluante (F07 « NON TESTÉ ») — fermer Jarvis avant de lancer.
* Hors Windows, les contrôles D et l'environnement Windows sont « NON TESTÉ » (répétition hors écran
  consignée à titre d'information).

## Fichiers

| Fichier | Rôle |
|---|---|
| `valider_pr.bat` | Lanceur unique (racine du dépôt). |
| `scripts/validation/valider_pr.py` | Orchestrateur (bibliothèque standard). |
| `scripts/validation/protocole.py` | Statuts, verdict, masquage, comptage des tests, rendu du rapport. |
| `scripts/validation/garde/` | Garde des données (`sitecustomize.py`, `garde_donnees.py`) et plugin pytest. |
| `scripts/validation/controle_dashboard_reel.py` | Contrôles D01–D17 (Qt natif). |
| `scripts/validation/gemini_reel_dashboard.py` | Scénarios E1–E10 (Gemini réel ; `--selftest` = faux serveur). |
| `scripts/validation/outils_surs.py` | Liste blanche d'outils pour les scénarios réels. |
| `scripts/validation/sonde_env.py`, `sonde_audio.py` | Sondes d'environnement et d'audio. |
| `scripts/validation/fenetre_finale.py` | Fenêtre finale unique. |
| `tests/test_validation_protocole.py` | Tests du protocole (garde, masquage, verdict, comptage…). |

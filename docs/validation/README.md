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
| E. Gemini Live réel et audio | Si une clé est disponible et autorisée : les deux scripts officiels (`validate_real_gemini.py`, `validate_reconnect_v174_real.py`) et les scénarios 1 à 10 Dashboard + Gemini (`gemini_reel_dashboard.py`), avec de l'audio réel synthétisé envoyé dans le vrai pipeline vocal. Inventaire des périphériques audio sans capture ; test guidé du haut-parleur et du micro dans la fenêtre finale. |
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

## Limites assumées

* La conversation avec **votre propre voix** au micro n'est pas automatisable : les scénarios réels
  envoient une voix synthétisée (SAPI français local, sinon synthèse Gemini) dans le même chemin
  `send_realtime_input` ; le micro lui-même est vérifié par le test guidé (niveau sonore).
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

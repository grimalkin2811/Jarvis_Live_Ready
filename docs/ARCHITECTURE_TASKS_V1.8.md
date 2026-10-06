# Jarvis v1.8 — tâches d’arrière-plan

## Flux et séparation des responsabilités

```text
Utilisateur
  → Gemini 2.5 Flash Native Audio (conversation principale, inchangée)
  → outil start_background_task (retour immédiat)
  → Task Manager (thread + boucle asyncio dédiés)
  → Router Gemini 3 Flash Live (décision JSON validée)
  → Gemini 3 Flash Live [simple/medium]
       ou Gemini 3.8 Flash [complex]
  → état/résultat/fichier dans le Task Manager
  → notification visuelle silencieuse
  → dialogue ou consultation vocale
```

`MAIN LOOP ≠ BACKGROUND TASK LOOP` est une contrainte structurelle :
`TaskManager` crée le thread `jarvis-background-tasks` et sa propre boucle
`asyncio`. `create_task()` persiste l’état `QUEUED`, soumet une coroutine avec
`run_coroutine_threadsafe`, puis revient. Il n’attend ni le Router ni
l’exécuteur. La réception Live, le micro, le TTS et les callbacks audio ne sont
donc jamais exécutés dans cette boucle.

## Modules

- `src/gemini_live.py` : cerveau conversationnel **Gemini 2.5 Flash Native
  Audio**. Son architecture Live n’est pas remplacée. Six outils lui donnent
  accès au gestionnaire central.
- `src/background_tasks/config.py` : identifiants de modèles et limites.
- `router.py` : appel Gemini 3 Flash Live, schéma JSON strict, mapping de
  complexité indépendant du Manager.
- `manager.py` : source de vérité, concurrence, persistance, hooks,
  annulation/retry et API publique.
- `executor.py` : exécution Gemini séparée, Google Search grounding lorsque le
  Router demande des outils, timeout, document Markdown éventuel.
- `quota.py` : protection Gemini 3.8 (RPM, RPD, TPM estimé, concurrence).
- `models.py` : états et objets sérialisables.
- `UI/background_tasks_dialog.py` : liste, compteurs, détail, lecture et
  annulation. Les hooks backend passent par un `Signal` Qt en connexion queued.
- `src/notifications.py` / `UI/notification_bridge.py` : fin de tâche visible
  mais silencieuse, sans prise du micro ni interruption de la voix.

## Modèles

| Rôle | Valeur par défaut | Variable |
|---|---|---|
| Conversation | `gemini-2.5-flash-native-audio-preview-12-2025` | `GEMINI_MODEL` |
| Router | `gemini-3-flash-live` | `JARVIS_TASK_ROUTER_MODEL` |
| Simple/medium | `gemini-3-flash-live` | `JARVIS_TASK_SIMPLE_MODEL` |
| Complex | `gemini-3.8-flash` | `JARVIS_TASK_COMPLEX_MODEL` |

Les noms preview peuvent évoluer côté Google : les variables permettent de
mettre les identifiants autorisés à jour sans modifier le Task Manager. Le
Router choisit seulement une complexité ; le mapping complexité → modèle est
centralisé dans `BackgroundModelConfig`.

## États

`QUEUED → RUNNING → [WAITING_FOR_TOOL → RUNNING] → COMPLETED`

Les sorties terminales alternatives sont `FAILED` et `CANCELLED`. Chaque tâche
contient identifiant UUID, titre, demande, dates, priorité, complexité, modèle,
progression, étape, résultat/résumé, fichiers, erreur, erreurs partielles,
usage de tokens et drapeau `seen`. Une tâche chargée après un arrêt alors
qu’elle était active devient `FAILED` avec une raison réessayable : aucun état
fantôme `RUNNING` n’est présenté.

## Quotas Gemini 3.8

Valeurs par défaut : 5 appels/minute, 20 appels/jour, 250k tokens/minute et un
appel complexe simultané. L’acquisition refuse explicitement un dépassement au
lieu d’attendre silencieusement. Une tâche complexe effectue un seul appel 3.8
contrôlé ; il n’existe aucune chaîne automatique d’appels rares. L’usage fourni
par `usage_metadata.total_token_count` est compté. Une erreur de quota passe la
tâche à `FAILED`; `retry_background_task` permet un nouvel essai explicite.
Ces compteurs sont persistés dans `background_quota.json` (y compris la
fenêtre glissante de la minute) et constituent une protection conservatrice,
pas le compteur contractuel de Google.

## API interne

```python
manager.create_task(title, description, priority="normal")
manager.get_task(task_id)
manager.list_tasks(status=None)
manager.list_active_tasks()
manager.list_completed_tasks()
manager.list_unread_completed_tasks()
manager.cancel_task(task_id)
manager.retry_task(task_id)
manager.mark_task_as_seen(task_id)
manager.get_task_result(task_id, mark_seen=True)
manager.quota_status()
```

Les objets retournés sont des copies : seul le Manager modifie l’état. Les
écritures JSON sont atomiques (`.tmp` puis remplacement). Les résultats de
session survivent aussi au changement Blob/Desktop et, en pratique, au
redémarrage.

## Interface et commandes naturelles

Le dialogue est accessible depuis l’icône de notification (« Tâches
d’arrière-plan… ») et le menu radial Memory (« Background Tasks »). Il affiche
actives, progression, étape, modèle, ancienneté, nouvelles tâches terminées,
détail, résultat et fichiers. Ouvrir un résultat le marque vu.

Les outils vocaux sont `start_background_task`, `list_background_tasks`,
`get_background_task_result`, `cancel_background_task`,
`retry_background_task`, `get_background_quota`. Gemini Live reçoit des
consignes explicites pour confirmer immédiatement le lancement et ne jamais
inventer un état.

## Tests

```bash
python -m pytest -q tests/test_background_tasks.py
python -m pytest -q
python -m ruff check .
python -m src.main --smoke-test
```

Le fichier dédié couvre création, transitions, routage simple/complexe,
validation JSON, non-blocage, concurrence, lecture/non-lu, échec isolé,
annulation, retry, quotas, notification/hook, résultat, fichier et persistance.
La validation API réelle nécessite une clé autorisant les trois identifiants et
la validation audio réelle nécessite micro/haut-parleur.

## Limites connues

- Le grounding exploite l’outil Google Search natif du modèle ; il ne fournit
  pas un navigateur arbitraire ni des connecteurs privés.
- Les compteurs sont locaux à cette installation et persistants ; ils ne voient
  pas les appels réalisés avec la même clé depuis une autre application. Le
  fournisseur reste l’autorité finale.
- L’annulation est coopérative : le SDK peut finir une requête réseau déjà
  envoyée, mais son résultat est ignoré et l’état reste annulé.
- Les documents v1.8 sont Markdown. Les formats bureautiques pourront être
  ajoutés comme exécuteurs sans changer le Manager.

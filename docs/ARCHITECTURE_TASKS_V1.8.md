# Jarvis v1.8 — tâches d’arrière-plan

## Flux

```text
Utilisateur
  → Gemini 2.5 Flash Native Audio (conversation principale inchangée)
  → start_background_task (retour immédiat)
  → Task Manager (thread + boucle asyncio dédiés)
  → découverte models.list + validation Live réelle
  → Router Gemini 3 Live résolu (BidiGenerateContent)
  → Gemini 3 Live résolu [simple/medium]
       ou Gemini 3.8 Flash Generate Content [complex]
  → résultat / document / notification / consultation
```

Le nom du modèle Gemini 3 Live n’est pas codé en dur. `LiveModelResolver` liste
les modèles visibles par la clé, conserve uniquement Gemini 3 avec l’action
`bidiGenerateContent`, préfère une version stable et standard récente, puis
exécute un véritable tour Live : sortie `AUDIO`, transcription de sortie non
vide, `turn_complete` et fermeture de session. Le seul handshake WebSocket ne
suffit jamais à produire `validated=True`. Le résultat `(model, transport,
validated)` est mémorisé en mémoire pour le processus. Un hint
`JARVIS_TASK_LIVE_MODEL` peut changer l’ordre des candidats, jamais contourner
la validation. Sans candidat utilisable, la tâche échoue explicitement.

## Isolation asyncio

`MAIN LOOP ≠ BACKGROUND TASK LOOP`. Le client GenAI background, le resolver,
les sémaphores, le Router et l’Executor sont tous construits par une coroutine
exécutée dans `jarvis-background-tasks`. Aucun client ou primitive asyncio de
l’UI/harness n’est transféré au worker. Les entrées externes utilisent
`run_coroutine_threadsafe`; les hooks UI utilisent les signaux Qt queued.

Chaque opération modèle a un timeout et chaque tâche un timeout global. Une
annulation ferme les context managers Live et libère les réservations. Une
tâche bloquée/échouée passe à `FAILED` ou `CANCELLED` sans arrêter la boucle ni
les autres tâches.

## Modules

- `config.py` : limites, modèle principal constant, modèle complexe et hint
  Live facultatif.
- `discovery.py` : liste, filtre de capacité Bidi, classement, connexion de
  validation et cache par empreinte non réversible de clé.
- `gateway.py` : cycle Live `AUDIO` complet pour Router/simple/medium, activation
  de `output_audio_transcription`, assemblage du texte transcrit et abandon des
  octets audio; Generate Content pour le complexe; retry borné 429/503 et reprise
  Live 1011 strictement limitée lorsqu'un épuisement de ressources est explicite.
- `router.py` : protocole JSON strict robuste aux fragments/fences, validation
  des champs et du modèle.
- `executor.py` : outils, progression, timeout, synthèse et document Markdown.
- `manager.py` : source de vérité, thread/loop, concurrence, persistance,
  annulation, lecture/non-lu et résultats.
- `quota.py` : réservation loop-safe et compte des appels 3.8 effectivement
  tentés.

## Modèles et transports

| Rôle | Modèle | Transport |
|---|---|---|
| Conversation | `gemini-2.5-flash-native-audio-preview-12-2025` | Live principal existant |
| Router | Gemini 3 Live découvert et validé | Live/BidiGenerateContent |
| Simple | même modèle résolu | Live/BidiGenerateContent |
| Medium | même modèle résolu | Live/BidiGenerateContent |
| Complex | `gemini-3.8-flash` | `aio.models.generate_content` |

Google Search des tâches Live est envoyé comme outil built-in dans la session
Live. Pour un outil built-in sur le chemin classique complexe, l’AFC client est
désactivé : l’exécution reste côté serveur et le warning AsyncModels est évité.

## États et API

`QUEUED → RUNNING → [WAITING_FOR_TOOL → RUNNING] → COMPLETED`, avec sorties
`FAILED` et `CANCELLED`.

API centrale : `create_task`, `get_task`, `list_tasks`, `list_active_tasks`,
`list_unread_completed_tasks`, `cancel_task`, `retry_task`,
`mark_task_as_seen`, `get_task_result`, `quota_status`, `live_model_status` et
`resolve_live_model`.

## Retry et erreurs externes

Une classification runtime unique privilégie les champs structurés SDK
(`status_code`, code/reason de fermeture Live) et le message fournisseur
conservé. Un motif explicite tel que `You exceeded your current quota` prime sur
le code générique 1011 et devient `EXTERNAL_QUOTA`. Les 429 et 503 ont un
backoff exponentiel borné. Une fermeture 1011 qui indique seulement
`Resource has been exhausted` devient `LIVE_RESOURCE_EXHAUSTED` et admet au plus
une reprise, avec backoff et jitter, dans le timeout global. Une 1011
sans ce motif n'est pas réessayée. Si la reprise consomme le délai restant, la
1011 initiale et son message restent la cause classée au lieu d'être masqués par
un timeout générique. Un diagnostic Live indique phase, tentative,
messages/audio/transcriptions, `turn_complete`, fermeture, dernier événement et
historique récent des erreurs. Le harness réutilise exactement cette classification.

## Quota 3.8

Valeurs par défaut : 5 RPM, 20 RPD, 250k TPM, concurrence 1. `reserve()` prend
la place concurrente sans compter d’appel. `mark_attempt()` est invoqué juste
avant chaque appel SDK réel, y compris un retry 503 : chaque tentative envoyée
est comptée. Une réservation annulée avant envoi ne consomme aucun appel.
`finish()` libère exactement une fois et ajuste les tokens connus. Les compteurs
et la fenêtre minute sont persistés dans `background_quota.json`.

## Validation

```powershell
py -3 scripts\validate_v180_real.py --preflight
py -3 scripts\validate_v180_real.py --api
```

Le rapport affiche le modèle réellement résolu, le transport, la validation,
les Router A/B/C, grounding, simple, complexe, concurrence, document, quota,
Gemini principal pendant le background et récupération après erreur. Un 429 ou
503 épuisé est `NON_TESTABLE / external service unavailable`, jamais un faux
PASS. Voir `docs/VALIDATION_REELLE_V1.8.md`.

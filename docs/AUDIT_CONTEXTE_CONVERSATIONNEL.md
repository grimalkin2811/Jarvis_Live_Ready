# Audit du contexte conversationnel — v1.7.0 (2026-09-28)

> Document de travail produit avant toute modification (règle §1 de la mission).
> Objectif : reconstruire la totalité de la chaîne de contexte **à partir du code
> réel**, identifier pourquoi « Jarvis sait qu'il y a eu X demandes et Y réponses
> mais le modèle n'utilise pas leur contenu sémantique », et servir de référence
> à la correction.

---

## 1. Carte de flux réelle (produite depuis le code)

```text
[Micro]  AudioIO._audio_worker (thread son)
   │  gating : awake / wake word / écoute continue / barge-in   (src/audio.py)
   ↓
mic(pcm)  (src/main.py:222 / src/ui.py:367 — closure sur `gemini`)
   │  gemini.can_send() : session != None, pas d'outil actif,
   │                       pas en train de parler (sauf interruption)   (src/gemini_live.py)
   ↓
asyncio.run_coroutine_threadsafe(gemini.send_audio(pcm), loop)
   ↓
GeminiLive.send_audio → session.send_realtime_input(audio=Blob 16 kHz)   ← CANAL TEMPS RÉEL
   ↓
┌────────────────────────── Gemini Live (WebSocket) ──────────────────────────┐
│  session maintenue CÔTÉ SERVEUR : l'audio et les tours passés y résident.   │
└─────────────────────────────────────────────────────────────────────────────┘
   ↓ (messages serveur)
GeminiLive._receive_loop                                     (src/gemini_live.py)
   ├─ session_resumption_update.new_handle → self.resumption_handle
   ├─ server_content.input_transcription  → _turn_user_text (fragments)
   │      └─ _maybe_handle_reset_command() : « nouvelle conversation » → reset local
   ├─ server_content.output_transcription → _commit_user_text() puis _turn_model_text
   ├─ server_content.model_turn (audio)   → _commit_user_text() + on_audio(pcm)
   ├─ server_content.interrupted          → _finish_turn() + on_interrupted
   ├─ server_content.turn_complete        → _finish_turn() + on_turn_complete
   │      └─ memory_manager.remember_from_text (extraction conservatrice, SEPARÉE)
   └─ tool_call → exécution (asyncio.to_thread) → conversation.add_tool_interaction()
          └─ session.send_tool_response(function_responses)

ConversationContext (verrou RLock, jamais persisté)          (src/conversation.py)
   add_user_message / extend_user_message     (depuis input_transcription)
   add_tool_call / add_tool_result            (compactés, jamais le JSON brut)
   add_assistant_message                      (depuis output_transcription)
   close_turn / fail_open_turn                (turnComplete / interruption / erreur)
   trimming par TOURS ENTIERS (max_turns=12, max_tokens=3000)

Sortie vers le modèle — LE SEUL POINT D'INJECTION DU CONTEXTE LOCAL :
GeminiLive.connect()                                          (src/gemini_live.py)
   ├─ config : SessionResumptionConfig(handle=self.resumption_handle)
   │          + transcriptions entrée/sortie + outils + system_instruction
   │          (PAS de context_window_compression, PAS de history_config)
   ├─ __aenter__() : le SDK attend setupComplete puis rend la session
   ├─ _replay_context() :
   │      SI resumption_handle → PAS de rejeu (le serveur restaure lui-même)
   │      SINON → send_client_content(turns=to_gemini_contents(messages),
   │                                   turn_complete=False)      ← CANAL ORDONNÉ
   └─ boucle externe (src/main.py / src/ui.py) : receive_loop() se termine
      → close() → connect() (avec handle) → receive_loop() …
```

### Réponses aux 9 questions, par étape

| Étape | Entrée | Sortie | Transformé par | Quand / thread | Perdable ? | Dupliquable ? | Envoyé au modèle ? | Preuve |
|---|---|---|---|---|---|---|---|---|
| Micro → `mic()` | PCM 16 kHz | coroutine `send_audio` | `AudioIO` (thread son) | continu / thread son | OUI (can_send=False pendant réponse, veille, session absente) | non | — | code src/audio.py + closures main/ui |
| `send_audio` | PCM | `realtimeInput` WS | SDK `AsyncSession` | boucle asyncio | OUI si WS tombe | non | OUI (canal temps réel) | SDK live.py:258 |
| `input_transcription` | texte partiel | `_turn_user_text` | `_receive_loop` | boucle asyncio | non (en RAM) | non | indirectement (audio déjà envoyé) | gemini_live.py:_receive_loop |
| `_commit_user_text` | fragments | `add_user_message`/`extend` | ConversationContext | boucle asyncio (ou thread outil via tool_call) | non | non (extend fusionne) | **NON dans la session courante** (le serveur a l'audio) | code conversation.py |
| `output_transcription` | texte partiel | `add_assistant_message` | idem | boucle asyncio | non | non | idem | idem |
| tool call/result | FunctionCall/Response | messages texte compactés | `add_tool_interaction` | boucle asyncio + `to_thread` | non | non | `send_tool_response` OUI ; contexte local NON | gemini_live.py outil |
| `turnComplete`/`interrupted` | flag | `close_turn`/`_finish_turn` | `_receive_loop` | boucle asyncio | non | non | — | gemini_live.py |
| **Reconnexion** | exception / fin de flux | `close()` → `connect()` | boucle externe | boucle asyncio | **OUI (voir bugs)** | **OUI (voir bugs)** | via handle OU rejeu | main.py/ui.py |
| **Rejeu** | `get_messages()` | `clientContent` | `to_gemini_contents` + SDK | `connect()` (boucle asyncio) | **OUI — c'est le bug central** | non | **envoyé mais SOUS FORME NON EXPLOITABLE par le chemin audio (preuves §3)** | gemini_live.py `_replay_context` |

---

## 2. Cycle de vie des sessions Gemini dans Jarvis (code réel)

| Événement | Où | Handle après | Rejeu après | Contexte local après |
|---|---|---|---|---|
| Démarrage | `run_headless`/`run_ui` → `start_new_conversation("démarrage")` → `connect()` | None | contexte vide → rien | vide |
| Fin normale du flux (`receive_loop` retourne) | boucle externe, `else:` sleep 0.1 | conservé | **sauf si handle → PAS de rejeu** | conservé |
| Erreur réseau / WS | boucle externe, `except:` sleep 5 s | conservé | idem | conservé (+ `fail_open_turn`) |
| Changement de voix | `_watch_voice_changes` → `_shutdown_session` | **conservé** | idem | conservé |
| `reset_conversation` / commande vocale | `_handle_context_reset` | **remis à None** | contexte vidé → rien | vidé |
| Limite ~10 min de connexion (serveur) | WS fermé → chemin « erreur » ou « fin normale » | conservé | idem | conservé |
| Limite ~15 min de session audio sans compression | session TERMINÉE | conservé (mort ?) | **PAS de rejeu si handle truthy** | conservé |

Aucune gestion de : `setupComplete` (implicite via SDK), `goAway`, `session_resumption_update.resumable`,
`session_id`, invalidité du handle, compression de fenêtre de contexte.

---

## 3. Recherche externe — convergences (sources détaillées dans le rapport final)

1. **Thread 111617 (forum Google AI, déc. 2025)** « Audio Input Cannot Trigger History
   Recall in Gemini Live API (Only Text Input Works) » : sur
   `gemini-2.5-flash-native-audio-preview-09/12-2025` **et** `gemini-2.0-flash-live`
   (le modèle par défaut de Jarvis est précisément
   `gemini-2.5-flash-native-audio-preview-12-2025`, voir `.env.example`), un
   historique injecté par `send_client_content` **n'est pas rappelé quand la
   question suivante arrive en AUDIO**, alors que la même question en texte
   fonctionne. Repris tel quel par un second développeur (janv. 2026), « we are
   looking into this » côté Google, sans résolution.
2. **Thread 62949 (janv. 2025)** « turnComplete flag set to false … prevents
   processing of subsequent RealtimeInputMessage » : un `clientContent` laissé
   en attente (`turnComplete=false`) empêche ou perturbe le traitement des
   `realtimeInput` suivants.
3. **SDK google-genai 2.25.0 (docstring `send_client_content`)** : « *Caution:
   Interleaving `send_client_content` and `send_realtime_input` in the same
   conversation is not recommended and can lead to unexpected results.* » —
   c'est exactement ce que fait `_replay_context`.
4. **Pipecat (framework de production)**, `services/google/gemini_live/llm.py` :
   - `HistoryConfig(initial_history_in_client_content=True)` envoyé par défaut ;
   - sur Gemini 2.5, le seed doit se terminer par un tour **user** (ajout d'un
     tour user vide sinon) ;
   - sur Gemini 2.5 en reconnexion, seed avec `turn_complete=True` **pour
     déclencher une inférence immédiate** (« *otherwise 2.5 ignores the seeded
     history when the user's first input is audio* », en citant le thread 111617) ;
   - la reprise de session par handle est le mécanisme primaire, le reseeding
     n'intervient que si aucun handle n'a été reçu.
5. **Documentation officielle** (session management) : connexion limitée à ~10 min ;
   session audio sans compression limitée à 15 min ; handles valables 2 h ;
   `contextWindowCompression` requis pour les sessions longues ; `GoAway` envoyé
   avant coupure ; `historyConfig.initialHistoryInClientContent` = protocole
   documenté d'échange d'historique initial (3.x).
6. **Issue googleapis/python-genai#2197** : un handle invalide provoque une
   `APIError 1007 Invalid session handle` à la connexion (pas de session vide
   silencieuse).

---

## 4. Causes racines identifiées (à démontrer par le harness)

### BUG 1 — CAUSE RACINE DU SYMPTÔME RAPPORTÉ
`_replay_context()` envoie tout l'historique en UN `clientContent` avec
`turn_complete=False` puis n'envoie jamais la clôture. Sur les modèles Live
audio (2.5 natif audio, 2.0 flash live), un `clientContent` en attente n'est
PAS rappelé par le tour AUDIO suivant : le modèle répond à la voix mais « comme
si la conversation venait de commencer ». Le contexte local, lui, contient tout
→ « Jarvis sait qu'il y a eu X demandes et Y réponses » (l'outil
`get_conversation_state` expose d'ailleurs les compteurs au modèle) « mais ne
retrouve pas Simon ». **Chaque reconnexion sans handle détruit donc la mémoire
sémantique côté modèle.**

### BUG 2 — Seed terminant par un tour `model`
Sur 2.5, le seed doit se terminer par un tour user (pipecat : « *we append a
blank user turn to satisfy the server* »). L'historique de Jarvis se termine
presque toujours par une réponse assistant → seed mal formé, comportement
serveur indéfini (rejet possible).

### BUG 3 — Rejeu sauté dès qu'un handle existe, sans vérification
`if self.resumption_handle: return`. Si le handle est expiré (>2 h), invalide
(erreur 1007 → boucle de reconnexion infinie actuelle) ou mort (session
terminée à 15 min), le contexte n'est NI restauré par le serveur NI rejoué par
Jarvis → perte totale silencieuse, contexte local intact (symptôme identique).
Le flag `resumable=false` des `session_resumption_update` est ignoré.

### BUG 4 — Course audio/rejeu à la connexion
`self.session` est défini AVANT `_replay_context()` : le micro peut envoyer de
l'audio pendant la fenêtre de rejeu → l'audio précède l'historique sur le
WebSocket (ordre corrompu) et tombe dans le pattern « clientContent en attente
+ realtimeInput » déconseillé par le SDK.

### BUG 5 — Pas de compression de fenêtre de contexte
Session audio plafonnée à 15 min sans compression → terminaison brutale de la
session (et du contexte serveur) dans les conversations longues, puis BUG 3.

### BUG 6 — Changement de voix : reconnexion AVEC l'ancien handle
La reprise de session conserve la configuration d'origine (voix comprise) :
le changement de voix peut ne pas s'appliquer, et le contexte ne peut être
restauré que par le serveur. Il faut une session NEUVE (nouvelle voix) + rejeu.

### BUG 7 — `GoAway` ignoré
Coupure « ABORTED » subie → chemin d'erreur (traceback + 5 s) au lieu d'une
reconnexion propre et immédiate.

### Non-bugs vérifiés
- `ConversationContext` : ordre, fusion, verrouillage, trimming par tours
  entiers, séparation mémoire/contexte — corrects (tests existants solides).
- `to_gemini_contents` : rôles user/model, fusion des rôles consécutifs,
  appels d'outils en texte compacté — conforme à ce que pipecat envoie aussi.
- Le pipeline transcription → contexte (ordre USER → OUTILS → ASSISTANT) est
  correct.
- Dans une session **continue**, le contexte audio réside côté serveur : le
  cas « Simon » y fonctionne. La régression apparaît dès qu'une session est
  recréée (coupure réseau, ~10 min de connexion, changement de voix, limite
  15 min) — c'est-à-dire régulièrement en conditions réelles.

---

## 5. Architecture de correction retenue (hybride, inspirée de pipecat + docs)

1. **Primaire — reprise de session** (état serveur conservé) :
   - captures des handles à chaque `session_resumption_update`, en respectant
     `resumable` ;
   - reconnexion immédiate et silencieuse sur `GoAway` ;
   - **compression de fenêtre de contexte** activée (sliding window) pour ne
     plus perdre la session à 15 min ;
   - **repli automatique** : handle refusé (erreur 1007/« invalid session
     handle ») → handle effacé → session neuve + rejeu. Plus jamais de boucle
     infinie ni de perte silencieuse.
2. **Secondaire — rejeu (seeding) conforme au protocole** :
   - `history_config=HistoryConfig(initial_history_in_client_content=True)`
     (protocole documenté ; sur 3.x le seed est committé sans appel modèle) ;
   - seed terminant par un tour **user** (tour user vide ajouté si besoin) ;
   - `send_client_content(turns, turn_complete=True)` : sur 2.5 le modèle
     produit une brève reprise/récapitulatif (comportement de production
     pipecat documenté) et **tous les tours audio suivants rappellent
     l'historique** ; sur 3.x le seed est committé silencieusement ;
   - l'audio est bloqué tant que le seed n'est pas parti (`_session_ready`) :
     plus de course.
3. **Changement de voix** : handle abandonné → session neuve avec la nouvelle
   voix + rejeu complet (le contexte survit au changement de voix).
4. **Instrumentation** : événements structurés `SESSION CREATED/RESUMED`,
   `CONTEXT REPLAY START/END`, `TURN …` avec `conversation_id`,
   `session_generation`, `session_id`, compteurs — jamais de contenu ni de
   secret.

### Variantes rejetées (détaillées dans le journal d'expériences)
- « Tout répéter dans le system prompt » / réinjecter l'historique à chaque
  tour : interdit par la mission (§10) et confondu contexte/mémoire.
- Seed `turn_complete=False` sans clôture : comportement actuel, démontré
  défaillant (BUG 1).
- Seed `turn_complete=False` + clôture vide au premier `user_stopped_speaking`
  (variante pipecat « démarrage ») : sur 2.5 le premier tour audio reste
  « naïf » — inacceptable pour une reconnexion en milieu de conversation.

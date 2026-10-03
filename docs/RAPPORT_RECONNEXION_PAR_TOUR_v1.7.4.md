# Rapport final — Reconnexion après chaque tour normal / « Je vous écoute » périodique (v1.7.4)

## 0. Résumé

Après une interaction **normale** (mot de réveil → phrase → réponse), et
**sans aucune nouvelle parole de l'utilisateur**, Jarvis répétait
« Je vous écoute. »/« Je suis prêt... » à intervalles réguliers pendant (et
au-delà de) la fenêtre de suivi de 8 secondes. Le journal montrait une
tempête de `SESSION CREATED`.

**Cause racine :** `session.receive()` du SDK Gemini Live se termine
**naturellement à la fin de chaque tour** — ce n'est **pas** un signal que la
connexion WebSocket est morte. `src/main.py`/`src/ui.py` traitaient ce retour
normal comme la fin de la session et rouvraient une connexion neuve après
**chaque tour**, y compris pendant la fenêtre de conversation active, sans
aucune parole. Chaque reconnexion à tort déclenchait le rejeu du contexte
local (`GeminiLive._seed_context`), qui clôt le rejeu par un tour « user »
synthétique — ce qui provoque une brève réponse du modèle, persistée comme
message assistant orphelin, qui réarme elle-même la fenêtre de 8 secondes.
Boucle auto-entretenue.

Ce bug est **distinct** de celui corrigé en v1.7.2 (écho acoustique du haut-
parleur repris par le micro) et de celui corrigé en v1.7.3 (course entre le
pont micro et la boucle asyncio) — les deux étant déjà couverts par
`tests/test_echo_loop.py` et `tests/test_turn_race.py`, qui continuent de
passer intégralement (voir §6).

## 1. Symptôme (log réel Windows v1.7.3 fourni par l'utilisateur)

- Une interaction normale se termine (réponse de Jarvis jouée intégralement).
- Sans nouvelle parole, `SESSION CREATED gen=N` réapparaît à répétition dans
  le journal, chaque fois suivi de `CONTEXT REPLAY START/END roles=.../user`
  et d'un `TURN_COMPLETE` qui réarme la fenêtre de 8 s.
- Jarvis prononce « Je vous écoute. »/« Je suis prêt. Qu'est-ce que tu
  souhaites faire ? » sans qu'aucune parole humaine ne le justifie.
- Le `CONTEXT REPLAY` d'un `gen=2` montre `roles=user/model/user` alors que
  l'interface affiche un seul tour / deux messages (voir §10).

## 2. Cause racine, précisément

### 2.1 Le contrat réel de `session.receive()`

Confirmé par Google (mainteneur du SDK), issue
[googleapis/python-genai#1224](https://github.com/googleapis/python-genai/issues/1224)
(résolue) :

> « the receive() method throws you out of the loop if turn is complete. To
> keep receiving messages from the following turns you need to put this part
> of the code under the while loop. »

Autrement dit : `receive()` représente **un tour**, pas la connexion entière.
Le motif attendu par le SDK est :

```python
async with client.aio.live.connect(...) as session:
    while True:
        await session.send_client_content(...)
        async for message in session.receive():
            ...
```

— la session/le WebSocket reste ouvert entre les tours ; seul `receive()`
est rappelé.

### 2.2 Ce que faisait Jarvis (avant ce correctif)

- `src/main.py` (~301-329) et `src/ui.py` (~509) :

  ```python
  while True:
      gemini.reconnect_requested = False
      try:
          await gemini.connect()
          await gemini.receive_loop()
      except ...:
          ...
      else:
          # Reconnexion immédiate et silencieuse après une fermeture normale
          # pour préserver l'état éveillé...
          await asyncio.sleep(0.1)
      finally:
          await gemini.close()
  ```

  Tout retour **normal** de `receive_loop()` — donc la fin de **chaque
  tour** — tombait dans la branche `else` et déclenchait un `gemini.close()`
  suivi d'un `gemini.connect()` : une **connexion WebSocket neuve**, sans
  qu'aucune vraie raison (GoAway, erreur, reset, changement de voix) ne
  l'ait demandé.

- `src/gemini_live.py::_receive_loop()` (avant correctif) : un seul
  `async for r in self.session.receive(): ...`, sans boucle englobante — il
  rendait donc la main après **un seul tour**, exactement au moment où le
  générateur du SDK se termine naturellement (§2.1).

- `src/gemini_live.py::connect()`/`_connect_once()` (~790-935) : ouvre
  **toujours** un WebSocket neuf. Si `resumption_handle` est vide (ce qui
  est le cas ici : les reconnexions sont si rapprochées — 0,1 s de délai —
  que le `session_resumption_update` du serveur n'a souvent pas encore été
  reçu), la connexion est journalisée `SESSION CREATED` (pas `SESSION
  RESUMED`) et **`_seed_context()` est appelée**.

- `src/gemini_live.py::_seed_context()` (~439-511) : rejoue l'historique
  local. Comme les modèles 2.x exigent que le rejeu se termine par un tour
  **user**, et que la conversation se termine normalement par la réponse de
  l'assistant, un tour vide synthétique
  `{"role": "user", "parts": [{"text": " "}]}` est ajouté et envoyé avec
  `turn_complete=True`. Par la conception même de cette fonction (héritée de
  v1.7.1, documentée en citant pipecat), cette clôture déclenche
  **délibérément** une brève inférence de reprise sur les modèles 2.x — la
  source directe de « Je t'écoute. »/« Je suis prêt... ».

- `src/conversation.py::add_assistant_message()` (~531) persiste cette
  réponse sans vérifier qu'un message utilisateur réel la précède
  (`_commit_user_text()` dans `gemini_live.py` ~332 est un no-op ici, car le
  tour de seed synthétique n'alimente jamais `_turn_user_text`) : la
  conversation stockée se termine à nouveau par « model ».

- `on_turn_complete` (`audio.extend_listening`, `src/audio.py` ~937) est
  déclenché pour CE tour fantôme exactement comme pour un tour réel,
  réarmant la fenêtre de 8 s (`_arm_follow_up("turn_complete", ...)`,
  `src/audio.py` ~355) — journalisé `silence_timer RESET reason=turn_complete`.

- La reconnexion suivante retrouve donc, à nouveau, un historique se
  terminant par « model » → nouveau tour synthétique → nouvelle réponse →
  nouveau message orphelin → **boucle auto-entretenue**, jusqu'à ce qu'une
  condition non confirmée avec certitude (expiration du délai malgré les
  réarmements répétés, ou éventuellement le contexte trop long) la brise.

### 2.3 Pourquoi 227 tests verts n'avaient rien détecté

`tests/live_harness.py::FakeLiveSession.receive()` (et plusieurs faux
serveurs locaux dans d'autres fichiers de test) modélisaient `receive()`
comme un flux **infini** qui ne se termine qu'à un `close()` explicite — pas
comme le contrat réel décrit en §2.1. Toute la suite de tests s'exécutait
donc dans un monde où « reconnecter après chaque tour » était invisible :
aucun test ne rappelait jamais `receive()` une seconde fois sur la même
session, donc rien ne pouvait démontrer que Jarvis le faisait à tort.
Ce point est corrigé en même temps que le bug (§5.3) : sans cela, les
nouveaux tests de régression auraient pu passer à tort même sans le
correctif de production.

## 3. Réponses aux points d'audit demandés

**Item 1 — chaîne complète pouvant produire `SESSION CREATED`/`connect()` :**
voir §2.2 ; les seuls appelants sont `src/main.py` (~309), `src/ui.py`
(~509), et — en cas d'échec de reprise de handle — le repli interne de
`GeminiLive.connect()` (~577-593).

**Item 2 — tableau des sites de reconnexion :**

| Site | Déclencheur exact | `awake` | `speaking` | minuteur silence | `session_generation` | `turn_epoch` | Raison invoquée |
|---|---|---|---|---|---|---|---|
| `main.py`/`ui.py`, boucle externe, branche `else` (avant correctif) | **Tout** retour normal de `receive_loop()`, y compris après un tour normal complet | `True` (fenêtre active) | `False` (le tour vient de finir) | vient d'être réarmé par ce même tour | incrémenté à chaque passage | incrémenté à `_finish_turn()` | Aucune — retour normal confondu avec fin de connexion |
| `main.py`/`ui.py`, boucle externe, branche `except` | Exception réseau/serveur réelle | variable | variable | variable | inchangé jusqu'au prochain `connect()` | inchangé | Erreur réelle (légitime) |
| GoAway serveur (`_receive_loop`, ~1077) | `r.go_away` reçu | `True` en général | variable | variable | inchangé jusqu'au prochain `connect()` | inchangé | Fin de connexion annoncée par le serveur (légitime) |
| `_handle_context_reset` (~408) | Commande « nouvelle conversation » / outil `reset_conversation` | variable | variable | remis à zéro | inchangé jusqu'au prochain `connect()` | inchangé | Reset explicite demandé (légitime) |
| `_watch_voice_changes` (~918) | Changement de voix détecté | variable | variable | inchangé | inchangé jusqu'au prochain `connect()` | inchangé | Nouvelle configuration nécessaire (légitime) |

Après correctif, la première ligne du tableau **disparaît** : un retour
normal de `_receive_one_turn_cycle()` ne fait plus sortir `receive_loop()` —
il boucle en interne sur la même session (§4).

**Item 3 — `TURN_COMPLETE` déclenche-t-il indirectement une reconnexion
alors que `awake=True`, la fenêtre de 8 s est active et aucune nouvelle
parole n'a été détectée ? OUI, confirmé.** C'est exactement le mécanisme
décrit en §2.2 : `TURN_COMPLETE` → `_finish_turn()` → fin normale de
`_receive_loop()` (avant correctif) → `main.py` reconnecte → `_seed_context`
rejoue → nouveau `TURN_COMPLETE` fantôme → nouvelle reconnexion, etc.

**Item 4 — relation entre `silence_timer RESET`, son expiration,
`SESSION CREATED`, `TURN_COMPLETE`, la reconnexion et le maintien de la
conversation « active » :** chaque `TURN_COMPLETE` — réel ou fantôme —
réarme le minuteur de 8 s via `on_turn_complete` (§2.2). Avant le correctif,
les reconnexions à tort produisaient des `TURN_COMPLETE` fantômes qui
réarmaient indéfiniment ce minuteur, empêchant Jarvis de revenir en veille
au bout de 8 s de vrai silence. Après correctif, aucun tour fantôme n'est
produit pendant la fenêtre active : le minuteur n'est réarmé que par de
vrais tours, et expire normalement (démontré par
`tests/test_session_lifecycle.py::TestNoSpuriousReconnectDuringActiveWindow`).

**Item 5 — pourquoi le pipeline continue d'envoyer `AUDIO_SENT_TO_GEMINI
bytes=2560` à des sessions neuves pendant le silence, et une session neuve
doit-elle recevoir quoi que ce soit avant une vraie activité vocale ?**
Le microphone est **conçu** pour transmettre en continu (VAD côté serveur) —
ces blocs sont du bruit ambiant réel, pas une preuve de parole (item 6). Le
problème n'était **jamais** ces blocs audio eux-mêmes (les garde-fous
anti-écho v1.7.2 et anti-course v1.7.3 — `turn_epoch`, `capture_generation`,
`_can_send_now()` — fonctionnaient correctement et ne sont pas modifiés) :
c'était la **fréquence anormale de création de sessions neuves** à qui ces
blocs, parfaitement normaux, étaient envoyés. Après correctif, une session
n'est plus recréée pendant la fenêtre active : les blocs continuent d'arriver
sur la **même** session légitime, comme prévu.

**Item 6 — vérification RMS/VAD et chemin mic → porte anti-écho →
`send_audio()` :** tracé et confirmé sans modification. `AudioIO._mic_gate_open`
(porte anti-écho v1.7.2) contrôle si le micro peut être transmis (ouverte
sauf pendant la sortie audio de Jarvis + traîne acoustique) ; `mic()` dans
`main.py`/`ui.py` vérifie `gemini.can_send()` puis planifie `send_audio()` ;
`send_audio()` revérifie `_can_send_now()` sur la boucle asyncio juste avant
l'envoi réseau (protection v1.7.3, **inchangée**). Aucune étape de cette
chaîne n'a de lien avec le bug corrigé ici — c'est la seule raison pour
laquelle la trace montre des envois audio « normaux » vers des sessions qui,
elles, n'auraient jamais dû être créées.

**Item 7 — instrumentation ajoutée :** voir §5.2.

**Item 8/9 — tests de reproduction :** voir §6.

**Item 10 — pourquoi `gen=2` montre `roles=user/model/user` alors que
l'interface affiche 1 tour / 2 messages :** `_seed_context()` construit sa
liste `turns` à partir de `to_gemini_contents(self.conversation.get_messages())`
(les messages **réellement persistés** — ici 2 : user + assistant) **puis**
ajoute, si nécessaire, un dictionnaire supplémentaire
`{"role": "user", "parts": [{"text": " "}]}` **uniquement pour l'envoi sur le
fil** (`session.send_client_content(turns=turns, ...)`) — ce troisième tour
n'est **jamais** écrit dans `conversation.py`. D'où la cohérence apparente
(l'UI affiche bien 1 tour/2 messages) qui masquait le vrai problème : ce
tour synthétique, bien que non persisté tel quel, provoque une **vraie**
réponse du modèle qui, elle, est persistée (§2.2, étape suivante) — c'est le
mécanisme par lequel le bug s'auto-entretient sans jamais casser le
comptage affiché à l'utilisateur.

## 4. Correctif

### 4.1 `src/gemini_live.py::_receive_loop()`

Restructuré pour boucler en interne sur la **même** session :

```python
async def _receive_loop(self):
    turn_cycle = 0
    while self.session is not None and not self.reconnect_requested:
        if turn_cycle > 0:
            self._record_trace("SESSION_REUSED_NEXT_TURN", turn_cycle=turn_cycle)
        turn_cycle += 1
        received_any = await self._receive_one_turn_cycle()
        if not received_any:
            return

async def _receive_one_turn_cycle(self) -> bool:
    received_any = False
    async for r in self.session.receive():
        received_any = True
        ...  # traitement des messages, INCHANGÉ
    return received_any
```

- La boucle externe ne s'arrête que sur une **vraie** raison : GoAway (met
  `reconnect_requested=True`), reset de contexte ou changement de voix
  (libèrent `self.session` via `_shutdown_session()`), ou une exception (se
  propage normalement à travers `receive_loop()`).
- Garde-fou supplémentaire : si un appel à `receive()` ne délivre
  **strictement rien** (cas qui ne devrait jamais se produire sur une
  session réellement vivante — `receive()` représente toujours un tour
  complet), la boucle s'arrête sans lever d'erreur, plutôt que de rappeler
  `receive()` indéfiniment en boucle serrée. Ce garde-fou n'a d'effet que
  sur des sessions de test dégénérées ; il ne modifie aucun comportement en
  production.
- **`src/main.py` et `src/ui.py` ne sont pas modifiés.** Leur boucle
  externe reste celle codée depuis v1.6.0 (`connect()` → `receive_loop()` →
  reconnexion sur retour anormal) ; elle devient simplement correcte, parce
  que `receive_loop()` ne rend plus la main après un tour normal.

### 4.2 Instrumentation (`_reconnect_reason`)

Nouvel attribut `GeminiLive._reconnect_reason`, positionné explicitement à
chaque déclencheur légitime :

- `"startup"` (valeur initiale, premier `connect()`) ;
- `"goaway"` (bloc GoAway, `_receive_loop`) ;
- `"context_reset"` (`_handle_context_reset`) ;
- `"voice_change"` (`_watch_voice_changes`) ;
- `"error"` (exception propagée par `receive_loop()`, si aucune des raisons
  ci-dessus n'était déjà positionnée) ;
- `"unexpected_after_normal_turn"` (valeur par défaut réinitialisée à
  **chaque** `connect()` réussi) : si une future régression réintroduit une
  reconnexion après un tour normal, cette valeur apparaîtra dans les traces
  `SESSION_CONNECT`/`SESSION_RESUME` sans qu'aucune cause légitime ne
  l'explique — signal immédiatement détectable.

`SESSION_CONNECT`/`SESSION_RESUME` incluent désormais `reason=...` dans le
log et la trace structurée. Un nouvel événement `SESSION_REUSED_NEXT_TURN`
est journalisé à chaque tour supplémentaire traité sur une session déjà
ouverte (preuve positive qu'aucune reconnexion n'a eu lieu).

### 4.3 Fidélité des harnais de test

`session.receive()` du SDK réel se termine à la fin de **chaque tour**
(§2.1). Plusieurs faux serveurs de test le modélisaient comme un flux
infini jusqu'à `close()` — ce qui, une fois `_receive_loop()` corrigé pour
rappeler `receive()` sur la même session, les aurait fait **rejouer
indéfiniment** le même scénario (boucle infinie dans les tests). Corrigé
dans :

- `tests/live_harness.py::FakeLiveSession.receive()` — se termine désormais
  après un message `turn_complete`/`go_away`, et lève une erreur explicite
  si rappelée sur une session déjà fermée (au lieu de bloquer indéfiniment) ;
- `tests/test_conversation_pipeline.py::_FakeSession.receive()`,
  `tests/test_desktop_events.py::_FakeSession.receive()`,
  `tests/test_interruption.py::_Session.receive()` — consomment
  progressivement leur liste de messages au lieu de la rejouer, et
  s'arrêtent à `turn_complete`/`interrupted` ;
- `tests/test_interruption.py::_ScriptedSession` et
  `tests/test_gemini_live.py` (deux tests d'exécution d'outils) — un
  garde-fou empêche le rejeu du scénario scripté sur un second appel.

Ces changements ne modifient **aucune assertion** existante : ils rendent
seulement les faux serveurs fidèles au protocole réel, ce qui est
précisément ce qui manquait pour que la suite puisse détecter ce bug (§2.3).

## 5. Ce qui n'a PAS été modifié (garde-fous préservés)

- `turn_epoch`, `capture_generation`, `_can_send_now()` (protections
  anti-course v1.7.3) : inchangés.
- La porte micro anti-écho (`AudioIO._mic_gate_open`, v1.7.2) : inchangée.
- Le mécanisme de reprise de session (`resumption_handle`,
  `SessionResumptionConfig`) et son repli sur handle invalide
  (`connect()` ~577-593) : inchangés.
- `_seed_context()` elle-même (le rejeu et son tour « user » synthétique) :
  **inchangée**. Elle reste nécessaire et correcte pour les **vraies**
  reconnexions (reprise de session refusée, redémarrage à froid) ; le
  correctif l'empêche seulement d'être appelée à tort après chaque tour
  normal.
- Aucune reconnexion n'est désactivée globalement ; aucun message n'est
  masqué ; aucun minuteur n'est simplement rallongé.

## 6. Tests

### 6.1 Nouveaux tests dédiés (`tests/test_session_lifecycle.py`)

Utilisent le même harness temps réel que `test_echo_loop.py` (AudioIO +
GeminiLive + faux serveur Live avec VAD) — pas une simulation isolée.

- **`TestNoSpuriousReconnectDuringActiveWindow`** (mission item 8) :
  réveil → phrase → réponse → silence total pendant toute la fenêtre de
  8 s → vérifie : exactement une connexion de session
  (`server.connect_count == 1`), aucun rejeu de contexte supplémentaire,
  exactement une interaction utilisateur, aucun tour fantôme, aucun
  réarmement du minuteur pendant le silence observé, exactement 2 messages
  persistés (user + assistant), retour en veille normal après expiration.
- **`TestRealFollowUpStillHandledOnSameSession`** (mission item 9) : réveil
  → phrase 1 → réponse 1 → **vraie** phrase 2 avant expiration des 8 s →
  vérifie : toujours **une seule** connexion de session, même
  `session_generation` entre les deux tours, une trace
  `SESSION_REUSED_NEXT_TURN`, les deux échanges réels persistés dans
  l'ordre (4 messages), aucun tour fantôme. Ce test garantit explicitement
  que le correctif **ne fonctionne pas en bloquant les nouvelles sessions
  ou tout nouvel audio** — un vrai enchaînement continue de produire un
  vrai tour.

Résultat : **2 passed** (33.6 s).

### 6.2 Suite existante (non-régression)

| Fichier | Résultat |
|---|---|
| `tests/test_turn_race.py` (10 tests, régression v1.7.3) | ✅ 10 passed |
| `tests/test_gemini_live.py` | ✅ 18 passed (2 faux serveurs mis à jour, §4.3) |
| `tests/test_conversation_pipeline.py` | ✅ 38 passed (faux serveur mis à jour) |
| `tests/test_live_context_harness.py` | ✅ 33 passed |
| `tests/test_interruption.py` | ✅ 24 passed (faux serveurs mis à jour) |
| `tests/test_voice_runtime.py` | ✅ 11 passed |
| `tests/test_desktop_events.py` | ✅ 19 passed (faux serveur mis à jour) |
| `tests/test_echo_loop.py` (régression v1.7.2, temps réel complet) | ✅ 20 passed (4 min 29 s) |
| `tests/test_session_lifecycle.py` (nouveau) | ✅ 2 passed |

**145 + 20 + 2 = 167 tests exécutés en local (sandbox Linux, `google-genai`
installé via `pip`), tous verts.** Exécution locale réalisée dans cette
session (voir §7 pour la limite Qt/UI restant non testable en sandbox).

Une flakiness **préexistante et non liée à ce correctif** a été identifiée
et documentée : `TestG_SessionChurn::test_reconnect_during_playback_does_not_create_ghosts`
échoue sous `JARVIS_ECHO_FAST=1` (mode rapide, fenêtre de silence réduite à
6 s) car une reconnexion après coupure réseau réelle peut légitimement
provoquer une brève ré-inférence qui réarme la fenêtre au-delà de 6 s ;
confirmé identique sur la base `a726ce7` **avant** ce correctif (même
échec, mêmes conditions). En mode normal (fenêtre de 20 s, celui utilisé
par défaut et par la CI), ce test passe (17,4 s). Aucune action requise :
non introduit par ce correctif, non couvert par la mission.

## 7. Limites de l'exécution en sandbox

- `google-genai` a été installé via `pip` dans un environnement virtuel
  dédié (`/tmp/jarvis_venv`) pour permettre l'exécution réelle de la suite
  de tests ci-dessus en sandbox (précédemment jugée impossible faute du
  paquet). Cela ne remplace pas la CI GitHub Actions réelle (Windows), qui
  doit être vérifiée après publication de la branche (§8).
- Les tests dépendant de Qt/PySide6 (`test_menu_*.py`, `test_orb_*.py`,
  etc.) n'ont pas pu s'exécuter en sandbox (`libGL.so.1` absent, aucun accès
  root pour l'installer) — ils sont **sans rapport** avec ce correctif
  (aucun fichier UI Qt modifié).
- Comme pour v1.7.3, un test avec un vrai microphone/haut-parleur et une
  vraie clé API Gemini n'a **pas** été effectué dans cette session.

## 8. Risques restants

- La condition exacte qui, dans le journal original de l'utilisateur,
  finissait par interrompre la boucle auto-entretenue (avant ce correctif)
  n'a pas été identifiée avec certitude (troncature du contexte, ou échec
  éventuel d'une reconnexion parmi tant d'autres) — sans conséquence
  pratique puisque le mécanisme qui produisait la boucle est supprimé à la
  racine.
- La CI Windows réelle (GitHub Actions) de ce correctif n'a pas encore été
  exécutée au moment de la rédaction de ce rapport (à vérifier après
  publication de la branche, comme pour v1.7.3).
- Test flaky pré-existant documenté en §6.2, non lié à ce correctif.

---

## 9. Addendum v1.7.5 — Audit complet des points d'entrée + validation réelle

Suite de mission explicitement demandée après le correctif v1.7.4 : audit
exhaustif de tous les points qui peuvent créer/fermer/reconnecter une
session, instrumentation supplémentaire, régressions prouvant que le bug est
réel (et pas un artefact du mock), et mise en place de l'infrastructure de
validation contre l'API Gemini Live **réelle**.

### 9.1 Audit exhaustif des points d'entrée (avant toute modification)

| Question | Réponse |
|---|---|
| Qui crée une session (`connect()`) ? | `GeminiLive.connect()` → `_connect_once()` (`src/gemini_live.py`). Appelé par **exactement 3 boucles**, toutes identiques dans leur structure : `src/main.py` (console), `src/ui.py` (Desktop Mode), et les harnesses de test (`tests/echo_loop_harness.py::EchoLab._voice_main`, `tests/real_gemini_harness.py::RealVoiceLab._voice_main`, `scripts/validate_real_gemini.py::RealRunner._run`). Aucun autre point d'entrée. |
| Qui ferme une session ? | `GeminiLive._shutdown_session()` (vide `self.session`/`self.ctx`). Appelé par : `_watch_voice_changes()` (changement de voix), `_schedule_session_restart()` via `_handle_context_reset()` (commande « nouvelle conversation » / outil `reset_conversation`), `close()` (arrêt complet), et implicitement à chaque itération de la boucle externe (`finally: await gemini.close()`), que `receive_loop()` ait levé une exception ou soit sortie proprement (GoAway). |
| Qui interprète `TURN_COMPLETE` ? | `GeminiLive._receive_one_turn_cycle()` : sur `server_content.turn_complete`, remet `speaking=False`, appelle `_finish_turn()` (commit USER → OUTILS → ASSISTANT dans `self.conversation`), puis déclenche `on_turn_complete` — câblé dans `main.py`/`ui.py` sur `AudioIO.extend_listening`. **`TURN_COMPLETE` ne déclenche JAMAIS, directement ou indirectement, un appel à `connect()`** depuis le correctif v1.7.4 (c'était exactement le bug : avant, `receive_loop()` *retournait* après chaque `TURN_COMPLETE`, et la boucle externe interprétait ce retour normal comme une session terminée). |
| Qui démarre/arrête le timer de 8 s ? | `AudioIO._arm_follow_up()` (fixe `follow_up_until = now + FOLLOW_UP_SECONDS`), appelé depuis `_wake()` (mot de réveil) et depuis `extend_listening()` (à CHAQUE `on_turn_complete`, y compris sans parole utilisateur — c'est le réarmement légitime documenté). `AudioIO._check_timeout()` (appelé à chaque bloc micro, ~80 ms) compare au temps courant et appelle `_go_to_sleep()` à l'expiration — **sans toucher à `GeminiLive`** : la session reste ouverte, seul `awake` passe à `False`. |
| Qui déclenche une reconnexion ? | La boucle EXTERNE uniquement, et seulement quand `receive_loop()` rend la main. Depuis v1.7.4, cela n'arrive que pour une cause RÉELLE : GoAway serveur (`reason="goaway"`), erreur réseau/protocole (`reason="error"`), reset de contexte explicite (`reason="context_reset"`), changement de voix (`reason="voice_change"`). Un sentinel `"unexpected_after_normal_turn"` est positionné par défaut sur CHAQUE connexion et doit être écrasé par l'une des raisons ci-dessus avant la connexion suivante — s'il apparaît dans les traces, c'est la preuve d'une régression de la classe de bug corrigée (testé explicitement, cf. §9.3). |
| Qui transmet les blocs micro à `send_audio()` ? | La fermeture `mic(pcm)`, dupliquée à l'identique dans `src/main.py`, `src/ui.py` et les deux harnesses de test. Elle n'est appelée par `AudioIO` QUE si `self.awake is True` (`_handle_awake_block`) ; elle fait un pré-contrôle rapide (`gemini.can_send()`) sur le thread audio puis planifie `gemini.send_audio(pcm, capture_generation=…, capture_turn_epoch=…)` sur la boucle asyncio, où la revérification autoritaire (`_can_send_now()`, v1.7.3) a lieu juste avant l'écriture réseau — **chaîne inchangée par v1.7.4/v1.7.5**. |
| Quelles tâches asyncio survivent à la fermeture d'une session ? | Uniquement `_watcher_task` (`_watch_voice_changes()`). Il est annulé par `close()` (appelé dans le `finally` de la boucle externe) et recréé par `_start_voice_watcher()` à chaque `connect()` réussi — aucune duplication possible (garde explicite), aucune fuite constatée. |

**Conclusion de l'audit** : aucun autre composant que `GeminiLive`/`AudioIO`/la
boucle externe ne participe au cycle de vie de session. Le correctif v1.7.4
(modifier `_receive_loop` pour boucler en interne sur la même session) reste
la correction correcte et suffisante : elle agit exactement au point où le
bug se produisait (l'interprétation erronée du retour de `receive()`), sans
toucher `AudioIO` ni la boucle externe.

### 9.2 Nouvelle instrumentation (purement additive)

Ajout d'un évènement de trace `SESSION_READY`, émis dans `_connect_once()`
juste après que `_session_ready = True` (donc juste après la fin du rejeu de
contexte). Il permet de prouver, trace à l'appui plutôt que par déduction,
qu'aucun bloc audio n'atteint jamais une session avant la fin de son
initialisation (item « E » de la mission). Aucun comportement existant n'est
modifié ; aucun test existant n'a eu besoin d'être changé pour cet ajout.

### 9.3 Nouveaux tests de non-régression (faux serveur, `tests/test_session_lifecycle.py`)

En plus des deux tests déjà livrés en v1.7.4 (silence sans reconnexion ;
vrai enchaînement sur la même session), quatre tests supplémentaires :

- **Item D** — `TestRealReconnectionIsNotCausedByTurnComplete` : une coupure
  réseau RÉELLE (pas une fin de tour) déclenche bien une nouvelle session,
  avec une raison journalisée (`error`/`goaway`) jamais confondue avec le
  sentinel de régression `unexpected_after_normal_turn`.
- **Item E** — `TestNoAudioBeforeSessionReady` : après une reconnexion
  forcée, aucun évènement `AUDIO_SENT_TO_GEMINI` de la nouvelle génération
  n'apparaît avant son `SESSION_READY` — vérifié sur la trace, pas supposé.
- **Item F** — `TestContextPreservedAcrossRealReconnection` : le contexte
  local (2 messages) survit intact à une reconnexion réelle ET est
  effectivement RENVOYÉ sur le câble lors du rejeu (vérifié via
  `WireEvent.all_text()`, pas seulement « toujours en mémoire côté
  client »). Note méthodologique explicite dans le test : le faux serveur
  `VadLiveServer` (fidèle au temps réel, mais sans oracle sémantique)
  prouve la partie CLIENT ; l'exploitation sémantique réelle par le modèle
  est du ressort du scénario S6 contre l'API réelle (§9.4).
- **Contrôle négatif** — `TestBugReproducesWithoutThePatch` : réintroduit
  (via monkeypatch scopé au test) le comportement `_receive_loop`
  PRÉ-v1.7.4 (un seul tour par appel) et vérifie que le scénario « silence
  après réponse » reproduit bien une reconnexion et/ou un tour fantôme
  pendant la fenêtre active. Sans ce contrôle, rien ne garantirait que
  `TestNoSpuriousReconnectDuringActiveWindow` détecte un vrai bug plutôt que
  de passer par construction (mock aligné sur l'implémentation).

Les 6 tests passent (`pytest tests/test_session_lifecycle.py` : 6/6, ~80 s).
La suite combinée (155 tests, fichiers listés en §6) et `test_echo_loop.py`
(20/20) repassent intégralement sans régression. Lint (`ruff check src
launcher run_jarvis.py version.py` et `ruff check UI tests`) : propre.

### 9.4 Validation contre l'API Gemini Live RÉELLE — statut honnête

Infrastructure livrée et prête à l'emploi :

- **`tests/real_gemini_harness.py`** : `RealVoiceLab`, qui exécute le VRAI
  pipeline (`AudioIO` + pont micro + `GeminiLive`, boucle
  `connect()`/`receive_loop()` identique à `src/main.py`) contre un VRAI
  `google.genai.Client`, alimenté par de la parole humaine synthétisée par
  TTS (pas une tonalité) rejouée dans une fausse carte son (mêmes threads
  temps réel qu'`EchoLab`, sans modèle d'écho — hors-scope ici).
- **`scripts/validate_reconnect_v174_real.py`** : exécute les scénarios
  S1+S4 (tour unique, silence ≥ 12 s, expiration normale), S2 (deux tours
  rapides), S3 (trois tours), S5 (reconnexion réseau réelle forcée — raison
  journalisée, vérification qu'aucun audio n'atteint la session avant
  `SESSION_READY`), S6 (mémoire contextuelle réelle après cette
  reconnexion : « Mon prénom est Simon » → coupure réelle → « Quel est mon
  prénom ? »). Verdicts PASS/FAIL/NON TESTABLE imprimés, clé jamais
  journalisée (filtrée par un masque regex sur tout motif `AIza…`/`AQ….`).
- **`.github/workflows/validate-gemini-live.yml`** : nouvelle étape qui
  exécute ce script en CI (GitHub Actions a un accès réseau réel aux
  domaines Google) si le secret `GEMINI_API_KEY` est configuré, et publie
  son verdict en commentaire de PR.

**Ce qui n'a PAS pu être exécuté depuis ce sandbox, et pourquoi (honnêteté
explicite, conformément à la consigne) :**

1. **Appel direct à l'API depuis ce sandbox** : impossible. Le pare-feu
   sortant de cet environnement bloque la poignée de main TLS vers TOUS les
   domaines Google (`generativelanguage.googleapis.com`, `google.com`,
   `googleapis.com`…) — `SSL_ERROR_SYSCALL` immédiat après le ClientHello,
   alors que `github.com`/`pypi.org` fonctionnent normalement depuis le
   même sandbox. Vérifié avec `curl -4 -v` sur 6 domaines Google distincts :
   tous échouent de la même façon ; confirmé qu'il ne s'agit pas d'un
   problème de clé ou de code.
2. **Déclenchement du workflow CI avec un vrai secret** : impossible depuis
   ce sandbox. Le jeton GitHub de la session n'a pas la permission de créer
   le secret `GEMINI_API_KEY` du dépôt (`gh secret set` → `403 Forbidden :
   Resource not accessible by integration`) — confirmé en vérifiant
   l'historique des runs existants de ce workflow : tous affichent
   `NON TESTABLE … aucune clé API disponible`, preuve que le secret n'a
   jamais été configuré, y compris lors des sessions précédentes.
3. **Décision explicitement validée avec l'utilisateur** : face à ces deux
   blocages, l'utilisateur a choisi d'exécuter lui-même
   `scripts/validate_reconnect_v174_real.py` en local (Windows), où Jarvis
   tourne déjà normalement avec un accès réseau réel. **Les verdicts
   S1/S2/S3/S5/S6 réels seront donc rapportés séparément, après cette
   exécution locale — ils ne sont PAS présentés comme validés dans cette
   session tant que ce résultat n'a pas été communiqué.**

### 9.5 Ce qui reste donc non définitivement tranché

- Le comportement exact du VRAI modèle Gemini lors du rejeu de contexte
  (`_seed_context`, mode `commit`) — produit-il, comme documenté pour les
  modèles « 2.x », une brève reprise orale après une reconnexion légitime
  (un message assistant orphelin de plus, sans gravité en soi tant que ça
  ne se reproduit pas en boucle) ou bien, comme documenté pour « 3.x »
  avec `historyConfig`, un commit silencieux ? Les tests contre le faux
  serveur (§9.3, item F) montrent les DEUX cas comme acceptables côté
  client ; seul un run réel (scénario S5) peut trancher lequel s'applique
  au modèle de production `gemini-2.5-flash-native-audio-preview-12-2025`.
- La confirmation chiffrée que le bug « Je vous écoute » périodique du log
  Windows original ne se reproduit PAS avec la vraie API (scénario
  S1+S4) — fortement probable au vu de l'audit (§9.1) et des tests contre
  le faux serveur, mais seule l'exécution réelle demandée à l'utilisateur
  peut le confirmer formellement.

## 10. Suivi v1.7.5 bis — Correction réelle du scénario S6 (mémoire contextuelle après reconnexion)

### 10.0 Contexte

L'utilisateur a exécuté `scripts/validate_reconnect_v174_real.py` sur une
machine Windows réelle (micro + TTS + clé Gemini réelle). Résultat :
`v1.7.3` (double cycle) et `v1.7.4` (tempête de reconnexions) restent
**corrigés** (trace réelle : `SESSION_REUSED_NEXT_TURN`,
`session_generation=1` stable sur plusieurs tours). Verdicts par scénario :

| Scénario | Verdict réel (avant ce correctif) |
|---|---|
| S1+S4 (silence, expiration) | PASS |
| S2 (deux tours rapides, même session) | PASS (note : rappel de « Simon » faible même en restant dans la même session) |
| S3 (trois tours consécutifs) | NON TESTABLE (`TimeoutError`) |
| S5 (reconnexion réseau forcée) | PASS |
| **S6 (mémoire après reconnexion réelle)** | **FAIL — réponse vide après la reconnexion** |

Ce chapitre documente l'investigation, la cause racine identifiée et le
correctif appliqué pour S6, ainsi que le diagnostic de S3.

### 10.1 Méthode : trace structurée du scénario minimal avant tout correctif

Avant toute modification de code, le scénario minimal (Tour A « Mon prénom
est Simon. » → reconnexion forcée → Tour B « Quel est mon prénom ? ») a été
rejoué contre le faux serveur temps réel (`EchoLab`, fidèle au séquencement
réseau réel : `send_client_content()` revient immédiatement, la réponse du
serveur n'arrive que plus tard via `session.receive()`), en instrumentant
explicitement :

- le contenu exact du contexte local avant reconnexion (`gemini.conversation.get_messages()` — rôles + nombre de messages) ;
- le moment exact de mise à jour de `ConversationContext` (après chaque tour utilisateur/assistant complet, cf. `src/conversation.py`) ;
- le moment exact de capture du contexte pour une reconnexion (`_seed_context()`, appelé depuis `connect()` juste avant `_session_ready = True`) ;
- la séquence complète d'évènements de trace : `SESSION_CONNECT` → (rejeu si pas de handle) → `SESSION_READY` → premier `AUDIO_SENT_TO_GEMINI` → première réponse ;
- `session_generation`, `resumption_handle`, et le flag diagnostique ajouté `context_seed_confirmed`.

Extrait réel de cette trace (faux serveur, aucune clé API impliquée) **avant**
correctif (le code de confirmation n'existant pas encore, `SESSION_READY`
suit immédiatement l'envoi du rejeu, sans attendre quoi que ce soit) :

```
SESSION_CONNECT   gen=2 reason=error
SESSION_READY     gen=2 context_seeded=True     # <-- porte ouverte tout de suite
```

**Constat :** `context_seeded=True` ne prouve que l'ÉCRITURE réseau du rejeu
(`send_client_content(turn_complete=True)` a retourné), jamais que le
serveur a fini de le TRAITER. Sur les modèles audio « 2.x », ce tour de
rejeu clôturé déclenche côté serveur une courte reprise orale (son propre
tour, avec son propre `turn_complete`) ; avant le correctif, rien n'attendait
cette confirmation.

### 10.2 Cause racine de S6

`send_client_content()` est un appel réseau asynchrone qui revient dès que
le client a écrit sur le WebSocket — **pas** quand le serveur a fini de
traiter ce tour. Après une reconnexion sans handle de reprise valide
(`_seed_context()`), Jarvis ouvrait la porte micro (`_session_ready = True`,
`SESSION_READY` tracé) **immédiatement après cet envoi**, sans jamais
vérifier que le serveur avait réellement fini de committer/traiter le
rejeu. Contre le faux serveur de test, ceci ne pouvait pas être détecté,
car `VadLiveServer`/`FakeLiveSession` répondent de façon **synchrone**, à
l'intérieur même de l'appel `send_client_content()` — un vrai WebSocket ne
fait jamais ça.

Contre l'API réelle, la fenêtre entre « rejeu envoyé » et « rejeu traité »
est bien réelle (latence réseau + temps d'inférence du serveur pour générer
sa reprise). Si le tour B (« Quel est mon prénom ? ») arrive dans cette
fenêtre, il est traité par un serveur encore occupé par le tour de rejeu :
symptôme observé = **réponse vide** après reconnexion (exactement S6).

**Distinction explicite (demandée) entre les quatre niveaux :**

| Niveau | Avant correctif | Après correctif |
|---|---|---|
| Contexte local Jarvis (`ConversationContext`) | Toujours intact (jamais perdu) | Inchangé |
| Historique envoyé à Gemini (`_seed_context`/`_replay_context`) | Envoyé en entier, rôles préservés | Inchangé |
| Contexte accepté par Gemini (écriture réseau confirmée) | `context_seeded=True` dès l'envoi | Idem, mais ne dit toujours que « envoyé » |
| Contexte réellement exploitable par Gemini pour le tour suivant | **Jamais vérifié** — porte ouverte trop tôt | **`context_seed_confirmed`** : vrai seulement si le propre `turn_complete` du serveur pour CE tour de rejeu a été observé avant d'ouvrir la porte |

### 10.3 Correctif (`src/gemini_live.py`)

- Nouveau flag `context_seed_confirmed` (toujours réinitialisé à `False` à
  chaque reconnexion et avant chaque rejeu).
- Nouvelle méthode `_await_seed_commit()` : après un rejeu clôturé
  (`turn_complete=True`), consomme **le même mécanisme exact** qu'un tour
  réel (`_receive_one_turn_cycle()`) pour absorber la réponse du serveur à
  ce rejeu (reprise 2.x : texte + audio + son propre `turn_complete`),
  borné par `SEED_COMMIT_TIMEOUT_SECONDS` (défaut **5 s**, override
  `JARVIS_LIVE_SEED_COMMIT_TIMEOUT`).
- Sur un modèle à commit silencieux (3.x + `historyConfig`, aucune
  inférence déclenchée par le rejeu), ce délai expire **normalement** sans
  réponse : la porte s'ouvre quand même après le délai (dégradation
  contrôlée — **jamais un blocage permanent du micro**), et
  `context_seed_confirmed` reste honnêtement `False`.
- `_connect_once()` n'ouvre `_session_ready` (et donc la porte micro)
  qu'**après** cette attente, que la session soit neuve (rejeu) ou reprise
  (pas de rejeu, pas d'attente — handle déjà valide).
- `SESSION_READY` trace désormais `context_seeded` **et**
  `context_seed_confirmed` séparément : les deux ne doivent plus jamais être
  confondus.

Extrait réel de la même trace **après** correctif (faux serveur, item
`TestSeedCommitAwaitsRealServerAck`) :

```
SESSION_CONNECT        gen=2 reason=error
SEED_COMMIT_CONFIRMED  gen=2 received=True   # <-- ack serveur du rejeu, observé en premier
SESSION_READY          gen=2 context_seeded=True context_seed_confirmed=True
```

La porte reste donc fermée strictement entre ces deux lignes — prouvé par
`TestSeedCommitAwaitsRealServerAck::test_connect_once_blocks_until_server_acks_the_seed`,
qui retarde artificiellement l'ack serveur et vérifie qu'aucun audio ne peut
être envoyé avant sa réception, et qu'il s'ouvre immédiatement après.

### 10.4 Protocole avant/après (séquence SESSION_CONNECT → … → premier audio)

**Avant :**
`SESSION_CONNECT` → setup → (si pas de handle) rejeu envoyé
(`send_client_content`, retour réseau immédiat) → `SESSION_READY`
(immédiat) → micro ouvert → **risque réel** : le tour utilisateur suivant
peut atteindre un serveur encore occupé par sa propre reprise du rejeu.

**Après :**
`SESSION_CONNECT` → setup → session resumption (si handle valide, aucun
rejeu, aucune attente) **ou** rejeu envoyé puis `_await_seed_commit()`
(consomme l'ack serveur du rejeu, borné à 5 s) → `SESSION_READY` (avec
`context_seed_confirmed` reflétant honnêtement si l'ack a été vu) → micro
ouvert → le tour utilisateur suivant ne peut plus arriver pendant que le
serveur traite encore le rejeu.

### 10.5 Tests ajoutés (`tests/test_session_lifecycle.py`, 6 nouveaux, items C/E/G)

Utilisent un faux double de session minimal (`_SlowAckSession`) construit
spécifiquement pour ce correctif, plus fidèle qu'`EchoLab`/`VadLiveServer`
au comportement réseau réel : `send_client_content()` revient
**immédiatement** (fidélité WebSocket), et la réponse du serveur n'est
délivrée que lorsque le test appelle explicitement `release_ack()` (file
`asyncio.Queue`). Cela permet de prouver déterministement, sans dépendre
d'un vrai réseau :

- `TestSeedCommitAwaitsRealServerAck` — la porte reste fermée tant que
  l'ack serveur n'est pas arrivé, et s'ouvre juste après.
- `TestSeedCommitTimesOutWithoutBlockingForever` — modèle à commit
  silencieux : la porte s'ouvre en mode dégradé après le délai borné, sans
  jamais bloquer indéfiniment.
- `TestSeedCommitSkippedWithoutASeed` (2 tests) — démarrage à froid
  (historique vide) et session reprise (handle déjà valide) : aucun rejeu,
  aucune attente, porte ouverte immédiatement.
- `TestBugReproducesWithoutThePatchS6` — contrôle négatif : en
  réintroduisant (monkeypatch scopé au test) le comportement pré-correctif
  (pas d'attente), la porte s'ouvre AVANT l'ack serveur — preuve que la
  course corrigée était réelle, pas un artefact du mock.
- `TestMultipleReconnectionsDoNotDuplicateContext` (item G, contre le vrai
  `EchoLab` temps réel) — deux reconnexions réelles successives : la
  première rejoue (aucun handle), la seconde **reprend** (handle obtenu
  après la première) au lieu de rejouer une seconde fois ; le contexte
  local ne grossit ni ne diminue entre les deux ; un vrai tour de suivi
  après les deux reconnexions reste fonctionnel.

Les items A, B, D et F étaient déjà couverts par des tests existants
(`test_live_context_harness.py`, `TestRealFollowUpStillHandledOnSameSession`,
`TestContextPreservedAcrossRealReconnection`,
`test_F_seed_cloture_et_termine_par_un_tour_user`) ; deux d'entre eux ont été
renforcés (voir §10.6) pour lire `context_seeded`/`context_seed_confirmed`
seulement après la fin réelle de l'attente de confirmation (sans quoi ils
auraient pu lire un état transitoire par accident de timing, pas par
preuve).

### 10.6 Durcissement de l'infrastructure de test (nécessaire pour que l'attente soit testable)

- `tests/__init__.py` : `JARVIS_LIVE_SEED_COMMIT_TIMEOUT=1.5` par défaut en
  environnement de test (appliqué avant tout import de `src.gemini_live`),
  pour ne pas ralentir chaque test de 5 s.
- `tests/voice_harness.py` : nouvelle méthode publique `VoiceHarness.wait_ready()`
  pour les tests qui lisent l'état post-rejeu sans enchaîner immédiatement
  sur `speak()` (qui, lui, attend déjà en interne).
- `tests/test_live_context_harness.py` : deux tests corrigés pour attendre
  `wait_ready()` avant de lire `context_seeded`/`context_seed_confirmed` —
  sans quoi ils pouvaient lire l'état avant que l'attente de confirmation
  n'ait eu le temps de se résoudre (faux négatif/positif selon le timing
  d'ordonnancement, pas un vrai bug).

### 10.7 Diagnostic de S3 (« NON TESTABLE », `TimeoutError`)

**Ce n'est pas un bug de contexte/session** : l'audit du scénario (trois
tours consécutifs) montre que le script de validation réelle insérait un
délai **fixe** de 2 s entre deux tours
(`scripts/validate_reconnect_v174_real.py`, scénario S3) avant d'envoyer le
tour suivant. Un délai fixe ne garantit pas que Jarvis a réellement fini de
jouer la réponse précédente (TTS) ni que la passerelle micro a réellement
rouvert sa porte — sur du matériel/réseau réel, ceci peut varier et produire
un envoi de tour pendant que Jarvis parle encore, d'où un `TimeoutError`
(aucune réponse détectée dans le délai imparti), sans rapport avec le
correctif S6 ni avec une perte de contexte.

**Correctif appliqué (harnais de script uniquement, aucun changement de
comportement produit) :** les deux délais fixes `wait(2.0)` du scénario S3
sont remplacés par une attente de préparation réelle :
`self.lab.wait_until(lambda: gemini.can_send() and not gemini.speaking, 20.0, "Jarvis prêt à écouter")`.
Ceci ne change ni les scénarios S1/S2/S4/S5/S6, ni le comportement du
produit — uniquement la synchronisation du harnais de test avec l'état réel
de Jarvis avant d'envoyer le tour suivant.

### 10.8 Résultats locaux (après correctif)

| Suite | Résultat |
|---|---|
| `tests/test_session_lifecycle.py` | **12/12** PASS (~96 s) |
| `tests/test_live_context_harness.py` | **33/33** PASS (~5.4 s) |
| Régression combinée (`test_version`, `test_session_lifecycle`, `test_gemini_live`, `test_turn_race`, `test_conversation_pipeline`, `test_live_context_harness`, `test_interruption`, `test_voice_runtime`, `test_desktop_events`, `test_conversation_context`, `test_conversation_providers`) | **232/232** PASS (~125 s) |
| `tests/test_echo_loop.py` (harnais temps réel, écho acoustique) | **20/20** PASS (~269 s) |
| `ruff check` (`src/gemini_live.py`, `tests/__init__.py`, `tests/voice_harness.py`, `tests/test_live_context_harness.py`, `tests/test_session_lifecycle.py`, `scripts/validate_reconnect_v174_real.py`) | Propre |

### 10.9 Confirmation explicite de non-régression v1.7.3 / v1.7.4

- **v1.7.3** (double cycle « Dis-moi tout ») : `tests/test_turn_race.py` et
  `tests/test_echo_loop.py` passent intégralement (20/20) — aucune
  modification de ces fichiers ni du chemin qu'ils couvrent.
- **v1.7.4** (reconnexion-par-tour / « Je vous écoute » périodique) :
  `tests/test_session_lifecycle.py` passe intégralement, y compris les 6
  tests déjà livrés en v1.7.4 (`TestNoSpuriousReconnectDuringActiveWindow`,
  `TestRealFollowUpStillHandledOnSameSession`,
  `TestRealReconnectionIsNotCausedByTurnComplete`,
  `TestNoAudioBeforeSessionReady`,
  `TestContextPreservedAcrossRealReconnection`,
  `TestBugReproducesWithoutThePatch`) — aucun n'a dû être affaibli.
- Les 7 invariants listés dans la mission v1.7.5 (fenêtre active de 8 s
  sans reconnexion parasite, tours rapides sur la même session,
  reconnexion uniquement pour une vraie raison, aucun audio avant
  `SESSION_READY`, protections `session_generation`/`turn_epoch`/
  `_can_send_now()` actives, absence de boucle/réveil périodique/réponse
  double, S1+S4/S2/S5 toujours PASS) restent tous vérifiés par la suite
  combinée ci-dessus.

### 10.10 Ce qui reste à faire avant de considérer la mission terminée

**Le correctif ci-dessus n'a PAS encore été revalidé contre l'API Gemini
Live réelle.** Conformément à la consigne (le test réel est la seule source
de vérité pour S6, une suite contre faux serveur qui passe n'est pas
suffisante), il reste à :

1. Faire ré-exécuter `scripts/validate_reconnect_v174_real.py` par
   l'utilisateur sur sa machine Windows (seul environnement avec accès
   réseau réel à l'API Gemini depuis ce contexte de travail).
2. Vérifier explicitement : **S6 = PASS** (Tour B mentionne bien « Simon »
   après une reconnexion réelle), **S1+S4 = PASS**, **S2 = PASS**,
   **S5 = PASS** (toujours, sans régression).
3. Vérifier si le correctif du harnais S3 (§10.7) suffit à le rendre
   TESTABLE ; documenter le verdict réel obtenu (PASS/FAIL), qu'il soit
   positif ou négatif.
4. Si S6 échoue encore en conditions réelles (p. ex. parce que le modèle de
   production est en mode « commit silencieux » et ne renvoie jamais de
   reprise orale après le rejeu, auquel cas `_await_seed_commit()` expire
   systématiquement sans ack et n'apporte alors aucune garantie
   supplémentaire), **ne pas déclarer la mission terminée** : revenir à
   l'instrumentation réelle (trace complète de session avec la clé jamais
   journalisée) pour déterminer si le modèle de production utilisé
   (`gemini-2.5-flash-native-audio-preview-12-2025` ou équivalent) est bien
   du côté « reprise 2.x » ou du côté « commit silencieux 3.x », et adapter
   le correctif en conséquence (p. ex. nécessité d'un tour placeholder
   différent, ou d'un mécanisme de confirmation propre au mode silencieux).
5. Ne pas fusionner/publier tant que le point 2 n'est pas entièrement
   vérifié avec un verdict S6 = PASS réel.

## 11. Suivi v1.7.5 quater — S6 reste FAIL malgré EMPTY_GENERATION_RETRY (investigation trace-first)

### 11.0 Contexte

Après le correctif v1.7.5 bis (§10, `_await_seed_commit`) et le correctif
v1.7.5 ter (bugs A/B, extinction prématurée + tour fantôme), un nouveau run
réel a été exécuté. Résultat :

| Scénario | Verdict réel |
|---|---|
| S2 (deux tours rapides) | **PASS** (bug A confirmé corrigé) |
| **S6 (mémoire après reconnexion réelle)** | **FAIL — toujours une réponse vide après reconnexion** |

Trace fournie par l'utilisateur pour S6 :

```
SEED_COMMIT_CONFIRMED        gen=2
SESSION_READY                gen=2 context_seeded=True context_seed_confirmed=True
GEMINI_USER_TRANSCRIPT       gen=2 text="Quel est mon prénom ?"
EMPTY_GENERATION_RETRY       gen=2 attempt=1/2
EMPTY_GENERATION_RETRY       gen=2 attempt=2/2
TTS_START                    gen=2                     # 3e tentative (épuisée)
TURN_COMPLETE                gen=2 had_content=False    # pas de GEMINI_ASSISTANT_TRANSCRIPT
```

Résultat observé côté harnais : S6 reçoit la question mais une réponse
« (sans transcription) » → FAIL.

**Consigne explicite reçue : ne pas se contenter d'augmenter le nombre de
relances — investigation trace-first complète avant toute modification.**

### 11.1 Comparaison g2-t8 (S6) vs g1-t3 (S2/S3) — ce qui diffère structurellement

Aucune trace brute horodatée (JSON) n'a été fournie pour cette investigation
(seul un résumé texte) : la comparaison ci-dessous s'appuie sur (a) le
résumé exact fourni par l'utilisateur, (b) une relecture complète du code
de réception (`GeminiLive._receive_one_turn_cycle`, `_seed_context`,
`_await_seed_commit`, `_connect_once`), et (c) les `tests/test_s6_persistent_empty_generation.py`
qui rejouent déterministiquement la signature exacte observée.

| | g1-t3 (S2/S3, session fraîche) | g2-t8 (S6, après reconnexion S5) |
|---|---|---|
| `session_generation` | 1 (jamais reconnecté) | 2 (reconnexion S5 déjà effectuée) |
| Taille du contexte serveur au moment du tour | 1-2 tours, écrits en direct | TOUT l'historique S1-S5 rejoué via `_seed_context()` (`CONTEXT REPLAY START ... contents=N`) juste avant |
| `context_window_compression` (config session) | activé (défaut), mais improbable qu'il se déclenche réellement sur un contexte aussi court | activé (défaut) — **sur un contexte déjà « plein » dès la reconnexion** |
| Génération vide observée ? | Non (0 relance nécessaire, S2 = PASS directement) | Oui, 3 tentatives consécutives, toutes vides |

**Conclusion de la comparaison :** la différence structurelle n'est PAS le
mécanisme de réception (`_receive_one_turn_cycle` est rigoureusement
identique dans les deux cas, cf. §11.4) ni une confusion de `turn_id`/
`session_generation` (cf. §11.4) — c'est la **taille/l'état du contexte au
moment de la génération**, directement causée par le rejeu de contexte
(`_seed_context`) qui est, par construction, plus volumineux qu'une session
neuve.

### 11.2 Ce que Gemini envoie autour de TTS_START / TURN_COMPLETE / interrupted

D'après le code de réception et la trace fournie :

- Tentatives 1 et 2 (originale + 1re relance) : `turn_complete` arrive
  **sans qu'aucun `server_content.model_turn` n'ait jamais été vu** — pas
  de `TTS_START` mentionné pour elles. C'est la variante « génération
  totalement nulle » du bug serveur (aucune inférence audio n'a même
  commencé à être streamée).
- Tentative 3 (2e relance, dernière autorisée) : un `server_content.model_turn`
  arrive bien (`TTS_START`/`on_speaking` se déclenchent — `self.speaking`
  passe à `True`), mais ses `parts` ne contiennent **aucun `inline_data.data`**
  et aucun `output_transcription` séparé n'arrive avant `turn_complete`.
  C'est la variante « le modèle démarre puis s'arrête immédiatement » du
  même bug.
- Aucun `interrupted` n'apparaît dans cette trace (confirmé par l'absence
  de mention) : ce n'est donc PAS une interruption locale ou serveur qui
  avale la réponse — c'est bien un `turn_complete` qui arrive de façon
  prématurée/vide, sans jamais passer par `interrupted` au préalable, ce
  qui correspond exactement à la description du bug serveur Google
  (§11.3) : *« a `turnComplete` message arrives **without** `interrupted:
  true`»*.
- `tests/test_s6_persistent_empty_generation.py::test_exact_real_trace_signature_g2_t8`
  rejoue cette séquence exacte (2 tentatives sans `model_turn` du tout, puis
  une 3e avec un `model_turn` creux) et confirme que `_receive_one_turn_cycle`
  la traite exactement comme observé : `EMPTY_GENERATION_RETRY` × 2,
  `TTS_START`/`on_speaking` sur la 3e tentative, aucun
  `GEMINI_ASSISTANT_TRANSCRIPT`, puis `TURN_COMPLETE` avec `had_content=False`
  — mais `on_turn_complete` se déclenche quand même car la question de
  l'utilisateur (`pending_user_text`), elle, était bien réelle. **Ce n'est
  pas une régression du filtre `had_content` du bug B (v1.7.5 ter)** : ce
  filtre protège contre un tour SANS question réelle (traîne fantôme) ; ici
  la question est bien réelle, seule la réponse manque — le tour DOIT se
  clore et être signalé, avec une réponse vide, exactement ce qui a été
  observé.

### 11.3 Cause racine : un bug serveur Gemini documenté, aggravé par la taille du contexte ET par `context_window_compression`

Recherche externe (GitHub, requise par la méthode trace-first avant toute
modification) :
[googleapis/python-genai#2117](https://github.com/googleapis/python-genai/issues/2117)
« Gemini Live API Native Audio Premature turnComplete Causes Mid-Sentence
Audio Truncation » — **~40 développeurs indépendants**, confirmé par
Google comme un bug serveur (pas un artefact client, testé avec AEC
matériel + VAD + désactivation de l'activité automatique, toujours
reproductible), toujours **ouvert et non résolu** au moment de cette
investigation (« There is currently no Gemini Live audio model without
this bug »).

Facteurs aggravants listés explicitement par Google/la communauté dans ce
rapport, et leur pertinence pour Jarvis :

| Facteur aggravant documenté | Présent dans g1-t3 (S2/S3) ? | Présent dans g2-t8 (S6) ? |
|---|---|---|
| **Contexte grandissant** (« Growing context length... longer conversations worsen ») | Non (session neuve, 1-2 tours) | **Oui** — tout l'historique S1-S5 vient d'être rejoué |
| **`context_window_compression` activé** (« enabling worsens ») | Activé mais peu pertinent (contexte trop court pour déclencher quoi que ce soit) | **Activé**, et c'est précisément le facteur sous notre contrôle |
| Appels d'outils récents | Non | Non (S1-S5 ne déclenchent aucun outil) |
| Langue non-anglaise (français) | Oui (constant, ne distingue pas g1 de g2) | Oui (constant, ne distingue pas g1 de g2) |

Un autre témoignage indépendant dans ce même rapport (équipe LiveKit,
déploiement en production) confirme qu'un « nudge » texte applicatif (notre
`EMPTY_GENERATION_RETRY`, conceptuellement identique) **« recovers most
stalls » mais que « ~30% of stalls don't recover from the nudge »** — donc
même la stratégie de relance par texte, déjà la meilleure pratique connue
de l'écosystème, n'est PAS fiable à 100 % : un taux d'échec résiduel
significatif (jusqu'à ~30 % par tentative selon ce témoignage) est attendu,
**même en production chez d'autres équipes**, ce qui rend 2-3 échecs
consécutifs statistiquement plausible sans qu'il s'agisse d'un bug dans
notre bookkeeping de relance.

**Verdict explicite demandé par l'utilisateur (points 3, 4, 5 de la
consigne) :**

3. Le problème n'est PAS lié à un contexte seed/replay « encore actif » au
   sens d'un état corrompu côté client (`_seed_context`/`_await_seed_commit`
   fonctionnent exactement comme prévu, `SEED_COMMIT_CONFIRMED` le prouve) :
   il est lié à la TAILLE du contexte serveur résultant du rejeu, combinée à
   `context_window_compression`, deux facteurs EXTERNEMENT documentés par
   Google comme aggravant ce bug serveur précis.
4. La relance renvoie bien le texte utilisateur dans un cycle `receive()`
   strictement neuf à chaque tentative (`session.receive_calls` avance de 1
   par tentative, `session.sent` contient bien le texte exact à chaque
   relance — vérifié par les tests déjà existants
   `tests/test_empty_generation_retry.py::EmptyGenerationRetrySucceedsTests`/
   `EmptyGenerationRetryBoundedTests`, et par le nouveau
   `test_exact_real_trace_signature_g2_t8`). Le serveur ne « sait » pas que
   c'est une relance (chaque `send_client_content` est un tour normal) :
   s'il répond encore vide, c'est que les CONDITIONS qui ont fait échouer la
   première tentative (contexte long + compression) sont toujours réunies
   pour les suivantes, pas que le serveur traite spécifiquement les
   relances comme un état à part.
5. `turn_id`/`session_generation` restent cohérents sur les 3 tentatives
   (vérifié explicitement par le nouveau test
   `test_turn_identity_is_stable_across_all_three_attempts` :
   `_begin_turn_if_needed()` n'est appelé qu'une seule fois, sur la
   transcription utilisateur de la 1re tentative ; `turn_counter` et
   `session_generation` ne varient jamais entre les 3 tentatives du même
   cycle `g2-t8`) — aucune confusion d'identité côté client.

### 11.4 Pourquoi « augmenter `EMPTY_GENERATION_MAX_RETRIES` » ne réglerait rien

Si la cause est un contexte durablement « dans un état aggravant » pour
toute la durée de la session (pas un hasard ponctuel qui se dissiperait
après 1-2 tentatives), chaque relance supplémentaire a statistiquement la
MÊME probabilité d'échouer que les précédentes — ajouter des relances
déplace le problème (répond parfois après 5 tentatives au lieu de 3) sans
l'adresser, au prix d'une latence perçue bien plus grande pour
l'utilisateur à chaque fois que le bug se manifeste. Ce n'est pas la
correction demandée.

### 11.5 Levier concret identifié, tests ajoutés, et ce qui reste à valider

Le seul facteur aggravant réellement **sous notre contrôle** (le contexte
long après reconnexion est inhérent à la fonctionnalité de mémoire
conversationnelle — on ne peut pas raisonnablement le supprimer) est
`context_window_compression`, actif par défaut via `CONTEXT_COMPRESSION_ENABLED`
(kill-switch déjà existant : `JARVIS_LIVE_COMPRESSION=0`, jamais testé
jusqu'ici). Son rôle actuel est d'éviter la coupure de session Live après
~15 minutes (documentation Live API) : le désactiver a donc un coût propre,
distinct de ce bug.

**Ce qui a été fait dans cette session (uniquement de l'investigation et des
tests, aucune modification de comportement par défaut) :**

- `tests/test_s6_persistent_empty_generation.py` (2 tests) : rejoue la
  signature EXACTE du run réel (3 tentatives vides, la dernière avec un
  `model_turn` creux déclenchant `TTS_START`) et prouve que le fallthrough
  actuel (`on_turn_complete` se déclenche avec une réponse vide) est le
  comportement ATTENDU étant donné ces conditions, pas un bug de
  bookkeeping ; confirme l'identité stable de `turn_id`/`session_generation`
  sur les 3 tentatives.
- `tests/test_live_context_harness.py::ModernModelTests::test_le_kill_switch_de_compression_retire_bien_le_champ` :
  le kill-switch `JARVIS_LIVE_COMPRESSION=0` n'avait jamais été testé —
  confirmé qu'il retire bien `context_window_compression` de la
  configuration envoyée au serveur, sans toucher aux autres protections
  (`historyConfig`, transcriptions).

**Ce qui reste à faire avant de considérer S6 comme corrigé (ne pas
fusionner/publier avant) :**

1. Décision produit à prendre par l'utilisateur : tester en conditions
   réelles `JARVIS_LIVE_COMPRESSION=0` sur le scénario S6 (et S3, plus
   long, pour vérifier l'absence de coupure de session à ~15 min) avant
   tout changement de comportement par défaut — c'est une hypothèse
   plausible et bien documentée côté Google, pas une certitude pour CET
   cette application précise.
2. Si la désactivation de la compression ne suffit pas (le bug reste
   fondamentalement un bug serveur Google non résolu, cf. §11.3 — aucune
   mitigation client connue dans l'écosystème n'atteint 100 % de fiabilité),
   envisager une dégradation explicite côté UX plutôt qu'un silence : par
   exemple, après épuisement des relances, faire dire à Jarvis une phrase
   de repli générique (« Je n'ai pas bien capté, peux-tu répéter ? ») au
   lieu de rester muet — ceci n'élimine pas le bug serveur mais transforme
   un échec silencieux (perçu comme un crash) en un échec audible et
   actionnable par l'utilisateur. Décision produit à valider avant
   implémentation (change le comportement observable, pas seulement un
   correctif interne).
3. Ne pas marquer S6 comme corrigé tant que (1) n'a pas été testé en
   conditions réelles avec un verdict PASS explicite.

## 12. Suivi v1.7.5 quinquies — `JARVIS_LIVE_COMPRESSION=0` ne corrige PAS S6 ; instrumentation ajoutée, cause encore non prouvée

### 12.0 Contexte et résultat du test réel demandé en §11.5

Le point (1) de §11.5 a été exécuté par l'utilisateur :
`scripts/validate_reconnect_v174_real.py` avec `JARVIS_LIVE_COMPRESSION=0`.
Résultat :

| Scénario | Verdict réel (compression désactivée) |
|---|---|
| S1 + S4 / S2 / S3 / S5 | **PASS** |
| **S6 (mémoire après reconnexion réelle)** | **FAIL — même signature exacte qu'en §11** : `EMPTY_GENERATION_RETRY` 1/2 puis 2/2, 3ᵉ tentative avec `TTS_START` + `TURN_COMPLETE had_content=True` mais toujours aucun `GEMINI_ASSISTANT_TRANSCRIPT` |

**Conclusion ferme et actionnable :** `context_window_compression` n'est
**pas** la cause de S6 (ou n'en est, au mieux, qu'un facteur marginal/non
déterminant) — désactiver ce kill-switch par défaut n'aurait aucun effet
démontré sur le symptôme, pour un coût propre (risque de coupure de
session Live à ~15 min). **Décision : ne pas changer ce comportement par
défaut**, conformément à l'instruction explicite reçue.

### 12.1 Pourquoi cette section ne referme PAS l'investigation (donnée manquante)

L'utilisateur a demandé une nouvelle analyse événement-par-événement très
détaillée (comparaison exacte `g1-t3` vs un nouveau tour S6 réel,
inspection précise de `receive()`, preuve que `had_content=True` ne
signifie pas « contenu assistant reçu », etc.), en réaction au fichier de
trace de ce nouveau run (« Texte collé(7).txt » dans l'échange).
**Ce fichier n'a cependant jamais été effectivement transmis à l'agent**
dans cette session (recherché explicitement sur l'ensemble du système de
fichiers accessible : absent). Sans lui, les points suivants de la demande
ne peuvent PAS être répondus avec des preuves (seulement avec du
raisonnement sur le code, explicitement jugé insuffisant par le critère de
fin demandé) :

- Comparaison événement-par-événement exacte d'un nouveau tour S6 réel
  (la seule comparaison disponible reste celle de §11.1/11.2, basée sur le
  run *précédent*, pas sur celui avec compression désactivée).
- Confirmation empirique que `had_content=True` dans CE run précis est bien
  dû à `has_user_text=True` (raisonnement correct par lecture du code,
  cf. §12.2, mais jamais observé directement faute de trace).
- Contenu réel de `turn_complete_reason`/`interaction_status`/
  `waiting_for_input` pour ce run — ces champs n'étaient pas encore tracés
  au moment où ce run a eu lieu (voir §12.3 : ils viennent d'être ajoutés).

**Décision pour cette session, conformément au point 10 de la consigne
reçue** (« si la cause reste incertaine : ne pas implémenter de
contournement spéculatif ; ajouter l'instrumentation minimale nécessaire
pour distinguer les hypothèses, et expliquer précisément quelle
observation manque ») : aucun correctif ni contournement n'a été
implémenté. Seule de l'instrumentation en lecture seule a été ajoutée
(§12.3), et l'observation manquante est énoncée explicitement (§12.4).

### 12.2 Ce qui EST prouvé par lecture de code (sans ambiguïté, sans trace supplémentaire nécessaire)

Deux points de la consigne (n°2 et n°3) ont une réponse certaine, obtenue
directement par lecture de `src/gemini_live.py::_receive_one_turn_cycle`
(pas une supposition) :

- **Sémantique de `TTS_START` (point 3)** : l'évènement est émis dans le
  bloc `if server_content and server_content.model_turn:`, dès que
  `(not self.speaking or self._interrupt_was_active)` — c'est-à-dire **dès
  qu'une enveloppe `model_turn` arrive sur le flux**, AVANT que la boucle
  qui consomme `part.inline_data.data` (seule source d'audio réellement
  jouée) ne s'exécute. `TTS_START` prouve donc uniquement « un `model_turn`
  est arrivé », jamais « du contenu exploitable est arrivé » — exactement
  ce qui explique qu'il se déclenche sur la 3ᵉ tentative de `g2-t8` sans
  qu'aucun `GEMINI_ASSISTANT_TRANSCRIPT` ne suive (le `model_turn` reçu est
  creux : `parts` sans `inline_data.data` exploitable, cf. §11.2).
- **Sémantique de `had_content=True` (point 2)** : dans le bloc
  `TURN_COMPLETE`, `had_content = has_user_text or has_model_content or
  has_local_interrupt`, où `has_user_text = bool(pending_user_text)` (la
  question utilisateur transcrite) et `has_model_content =
  self._turn_model_content_seen` (vrai UNIQUEMENT si de l'audio
  `inline_data.data` a réellement été reçu — voir boucle juste après
  `MODEL_TURN_RECEIVED`). Dans la signature S6 décrite par l'utilisateur
  (question transcrite, zéro audio, zéro transcription assistant),
  `had_content=True` ne peut être porté QUE par `has_user_text=True` — il
  **ne prouve en aucun cas qu'un contenu assistant a été reçu**. C'est
  précisément la mise en garde du point 2 de la consigne : « ne pas traiter
  `had_content=True` comme preuve d'un contenu assistant exploitable ».
  Ce raisonnement est maintenant vérifiable DIRECTEMENT sur une future trace
  (plus besoin de le déduire du code) grâce à l'instrumentation du §12.3.

### 12.3 Instrumentation ajoutée (lecture seule, aucun changement de comportement)

Quatre champs officiels de `google.genai.types.LiveServerContent`, présents
dans le SDK installé mais **jamais lus nulle part dans ce code base**
avant cette session, sont maintenant tracés :

- `turn_complete_reason` — énumération `TurnCompleteReason` (28 valeurs
  dans le SDK installé, incluant notamment `RESPONSE_REJECTED`,
  `NEED_MORE_INPUT`, `MALFORMED_FUNCTION_CALL`, `MAX_REGENERATION_REACHED`,
  plusieurs raisons liées à la sécurité du contenu). Si Gemini explique
  explicitement pourquoi une génération n'a rien produit, c'est ce champ
  qui le dirait.
- `generation_complete` — documenté dans le SDK comme pouvant arriver
  **seul**, sur un message séparé de `model_turn`/`turn_complete`, si le
  modèle attend la fin de la lecture audio temps réel.
- `interaction_status` — énumération `InteractionStatus`
  (`IN_PROGRESS`/`REQUIRES_ACTION`/`IDLE`/`INTERACTION_STATUS_UNSPECIFIED`),
  documentée comme « toujours envoyée aux côtés de `turn_complete` ».
- `waiting_for_input` — documenté comme signifiant que « le modèle
  n'est pas en train de générer parce qu'il attend plus d'entrée, par ex.
  il s'attend à ce que l'utilisateur continue à parler ».

Changements dans `src/gemini_live.py` (commit `ea63c37`) :

- `GeminiLive._live_diagnostics(server_content) -> dict` (nouvelle méthode
  statique) : lit ces quatre champs, déballe la valeur `.value` des
  énumérations, ne retourne que les entrées non `None`.
- Nouvel évènement de trace `LIVE_DIAGNOSTIC_FIELDS`, émis dès que
  `server_content` est extrait (avant tout aiguillage `model_turn`/
  `turn_complete`/`interrupted`) si au moins un de ces champs est présent —
  capture le cas où `generation_complete` arrive seul.
- Nouvel évènement de trace `MODEL_TURN_RECEIVED`, émis au tout début du
  bloc `model_turn`, AVANT `TTS_START` et avant toute décision : expose
  `parts_count`, `has_inline_audio` (vrai si au moins une `part` a un
  `inline_data.data` non vide) et `has_text_part` (vrai si au moins une
  `part` a un `.text` non vide — teste l'hypothèse qu'un contenu texte
  exploitable pourrait exister et être actuellement ignoré, puisque seule
  `inline_data` est consommée par la boucle existante).
- `TURN_COMPLETE` : `had_content` reste calculé et utilisé à l'identique
  (aucun changement de la valeur ni des branches `if had_content:`), mais
  le payload de trace expose maintenant séparément `has_user_text`,
  `has_model_content` et `has_local_interrupt`.
- `INTERRUPTION` : payload de trace enrichi des mêmes champs
  `live_diag`.

Confirmé sans régression : les 98 tests ciblés
(`test_gemini_live`, `test_interruption`, `test_empty_generation_retry`,
`test_turn_callbacks`, `test_s6_persistent_empty_generation`,
`test_session_lifecycle`, `test_turn_race`, `test_live_context_harness`)
passent tous, inchangés — ce changement est purement additif sur le
contenu des évènements de trace.

### 12.4 Observation manquante, formulée explicitement

Pour répondre aux points 1, 4, 5, 6 et 7 de la consigne (comparaison
événement-par-événement exacte, distinction entre « 3 générations
distinctes » et « 3 envois dans un état serveur bloqué », état résiduel
après rejeu de contexte), il manque précisément :

- **Soit** le contenu réel du fichier de trace déjà évoqué mais jamais
  reçu par l'agent dans cette session (nécessaire pour toute analyse
  événement-par-événement exacte d'un run passé) ;
- **Soit** un nouveau run réel de `scripts/validate_reconnect_v174_real.py`
  (scénario S6 au minimum) exécuté AVEC le code de ce commit (`ea63c37`),
  dont la trace complète (via `GeminiLive.trace_events()` / le fichier
  JSON produit par le harnais) montrerait, pour chacune des 3 tentatives de
  `g2-t8` : les valeurs de `turn_complete_reason`/`interaction_status`/
  `waiting_for_input`/`generation_complete` (actuellement jamais
  observées), et les valeurs de `parts_count`/`has_inline_audio`/
  `has_text_part` du `model_turn` creux de la 3ᵉ tentative.

**Tant que l'une de ces deux données n'est pas disponible, la cause de S6
reste non prouvée au sens du critère de fin demandé** : l'hypothèse du bug
serveur documenté `googleapis/python-genai#2117` (§11.3) demeure la plus
plausible et la mieux étayée par des sources externes indépendantes, mais
elle n'est pas confirmée par une observation directe des champs
`turn_complete_reason`/`interaction_status` sur un run Jarvis réel — ce qui
est exactement ce que cette instrumentation vise à obtenir au prochain run.
Aucun correctif n'est implémenté sur cette base tant que cette confirmation
manque, conformément à la consigne reçue.

### 12.5 Premier run réel avec l'instrumentation v1.7.5 quinquies — S6 PASS, bug non reproduit cette fois

Un run complet (S1+S4/S2/S3/S5/S6, verdict final : **tous PASS, y compris
S6**) a été fourni par l'utilisateur, exécuté avec le code de ce commit
(`ea63c37`). **Aucun `EMPTY_GENERATION_RETRY` ne s'est déclenché une seule
fois dans tout ce run** : `g2-t8` (« Quel est mon prénom ? » après
reconnexion réelle) a reçu sa réponse complète (« Votre prénom est Simon »)
dès la toute première tentative. Ce run ne reproduit donc pas le bug — il
ne peut pas répondre aux questions encore ouvertes du §12.4, mais il
apporte des faits nouveaux, directement observés (plus seulement déduits
du code) :

- **Chaque tour réel (9/9 dans ce run, y compris le tour de reprise
  automatique `g2-t7` « Je vous écoute » après reconnexion) commence par un
  `model_turn` contenant une part TEXTE SEULE** (`MODEL_TURN_RECEIVED
  parts_count=1 has_inline_audio=False has_text_part=True`), **avant**
  qu'un seul chunk audio n'arrive. C'est désormais un comportement protocole
  confirmé et systématique, pas une occurrence isolée. `TTS_START` se
  déclenche dès ce tout premier chunk (texte, sans audio) — confirmation
  directe de l'analyse du §12.2 : `TTS_START` ne garantit aucun contenu
  audible. Ce texte n'est actuellement consommé nulle part (ni pour la
  lecture audio, ni pour `GEMINI_ASSISTANT_TRANSCRIPT`, qui provient du
  canal séparé `output_transcription`) ; rien dans ce run ne prouve qu'il
  diffère du contenu réellement prononcé ensuite, donc aucune action n'est
  prise sur cette base — mais c'est une piste précise et falsifiable pour
  la prochaine capture d'un tour S6 QUI ÉCHOUE : si une tentative vide
  s'arrête après CE SEUL chunk texte sans qu'aucun chunk `inline_data` ne
  suive jamais, ce serait la première preuve directe que le modèle
  « démarre puis s'arrête immédiatement » au sens strict (hypothèse du
  §11.2, jamais confirmée par une observation directe jusqu'ici).
- **`generation_complete=True` accompagne systématiquement (9/9) la
  complétion normale d'un tour**, juste avant `TURN_COMPLETE` — c'est donc
  la signature confirmée du chemin « tout s'est bien passé ».
- **`turn_complete_reason` / `interaction_status` / `waiting_for_input`
  ne sont apparus NULLE PART dans ce run** (aucune ligne
  `LIVE_DIAGNOSTIC_FIELDS` ne les contient) : ils sont donc authentiquement
  absents/`None` sur le chemin normal — preuve négative utile, mais qui ne
  dit toujours rien du chemin défaillant.
- **Confirmation directe (pas seulement par lecture de code) du
  §12.2 pour un cas `has_user_text=False`** : le tour de reprise `g2-t7`
  (réponse automatique du serveur après rejeu de contexte, aucune question
  utilisateur) a bien `TURN_COMPLETE has_user_text=False
  has_model_content=True had_content=True` — exactement le comportement
  attendu, observé pour la première fois en conditions réelles.

**Ce run ne clôt donc PAS l'investigation** : conforme à la nature
probabiliste du bug serveur documenté en §11.3 (jusqu'à ~30 % d'échecs
résiduels par tentative selon le témoignage externe cité), il est normal
qu'un run donné ne reproduise pas le symptôme. **Il manque toujours** une
capture d'un run où `EMPTY_GENERATION_RETRY` se déclenche réellement,
produite avec ce même code instrumenté, pour lire les valeurs de
`turn_complete_reason`/`interaction_status`/`waiting_for_input` et la
forme exacte (`has_text_part`/`has_inline_audio`) du `model_turn` creux
des tentatives qui échouent.

## 13. Quatre nouveaux runs réels instrumentés — preuve directe obtenue, deux phénomènes distincts identifiés

L'utilisateur a fourni 4 runs complets exécutés avec le code du §12 (commits
`ea63c37`/`c8d0ad7`). **2 PASS (tests 2 et... non, précisément : test 2 = PASS,
tests 1/3/4 = FAIL sur S6.** Contrairement au run du §12.5, ces runs
reproduisent le bug — avec, pour la première fois, les champs
`LIVE_DIAGNOSTIC_FIELDS`/`MODEL_TURN_RECEIVED` capturés **pendant** l'échec.
Ceci permet de répondre aux points 1 à 7 de la consigne avec des preuves
directes, plus seulement du raisonnement sur le code.

### 13.1 Séquence exacte de `g2-t8` dans les runs qui échouent (tests 1 et 3) — identique dans les deux

```
TURN_OPEN                 g2-t8
GEMINI_USER_TRANSCRIPT    g2-t8  texte réellement transcrit
[... AUDIO_SENT_TO_GEMINI ...]
MODEL_TURN_RECEIVED       g2-t8  parts_count=1 has_inline_audio=False has_text_part=True
TTS_START                 g2-t8
LIVE_DIAGNOSTIC_FIELDS    g2-t8  generation_complete=True   <-- SEUL champ présent
EMPTY_GENERATION_RETRY    g2-t8  attempt=1/2
[... nouvel envoi du texte, AUDIO_SENT_TO_GEMINI ...]
MODEL_TURN_RECEIVED       g2-t8  parts_count=1 has_inline_audio=False has_text_part=True
TTS_START                 g2-t8
LIVE_DIAGNOSTIC_FIELDS    g2-t8  generation_complete=True
EMPTY_GENERATION_RETRY    g2-t8  attempt=2/2
[... nouvel envoi du texte, AUDIO_SENT_TO_GEMINI ...]
MODEL_TURN_RECEIVED       g2-t8  parts_count=1 has_inline_audio=False has_text_part=True
TTS_START                 g2-t8
LIVE_DIAGNOSTIC_FIELDS    g2-t8  generation_complete=True
TURN_COMPLETE             g2-t8  had_content=True has_user_text=True has_model_content=False has_local_interrupt=False
```

Cette séquence, strictement identique dans les deux runs qui échouent (y
compris après l'épuisement des 2 relances), apporte des réponses directes et
définitives :

- **Point 6 (3 générations distinctes, ou 3 envois dans un état bloqué ?)**
  — **3 générations réellement distinctes, prouvé.** Chaque tentative a son
  propre `TTS_START` et son propre `LIVE_DIAGNOSTIC_FIELDS`
  (`generation_complete=True` à chaque fois) : ce n'est pas un état serveur
  figé qui répond trois fois la même chose en boucle, c'est le serveur qui
  choisit, **trois fois indépendamment**, de terminer la génération
  immédiatement après un unique envelope creux. Chaque relance obtient bien
  son propre cycle complet.
- **Point 3 (que signale `TTS_START` ?)** — confirmé une fois de plus,
  directement : il se déclenche sur un `model_turn` dont les `parts`
  contiennent uniquement du **texte** (`has_inline_audio=False
  has_text_part=True`), jamais suivi d'un seul octet `inline_data` avant que
  `generation_complete=True` n'arrive. `TTS_START` ne prouve donc **jamais**
  qu'un son sera produit.
- **Point 2 (`had_content=True` ne prouve pas un contenu assistant)** —
  confirmé directement sur la trace réelle : le `TURN_COMPLETE` final affiche
  `has_model_content=False` explicitement ; `had_content=True` est
  entièrement porté par `has_user_text=True` (la question de l'utilisateur,
  réellement transcrite).
- **Nouvelle réponse, cruciale, au point 10 (quelle observation manquait) :
  `turn_complete_reason`, `interaction_status`, `waiting_for_input` ne sont
  JAMAIS apparus, pas une seule fois, dans AUCUNE des 3 tentatives, dans
  AUCUN des 4 runs (PASS ou FAIL).** `generation_complete=True` est le seul
  champ renvoyé, aussi bien dans les tours réussis que dans les tours
  totalement vides. **Conclusion ferme : le serveur Gemini ne fournit, à ce
  jour, strictement aucune information exploitable permettant de distinguer
  côté client une génération vide « normale » d'une génération vide « due au
  bug ». Aucune instrumentation supplémentaire côté client ne peut extraire
  plus d'information du serveur sur CE point précis** — les quatre champs du
  §12.3 ont été testés en conditions réelles d'échec et n'apportent rien de
  plus que ce qui était déjà su.
- **Points 1, 4, 5 (comparaison événement par événement, inspection du cycle
  `receive()`)** — désormais répondus avec des preuves directes
  ci-dessus : le cycle `receive()` se comporte identiquement à chaque
  tentative (nouvel appel propre, cf. commentaire ligne ~1649 confirmé par
  la trace), sans aucune fuite d'état d'une tentative à l'autre ; la seule
  différence entre un tour réussi (`g1-t3` par ex.) et un tour qui échoue
  est le nombre de `MODEL_TURN_RECEIVED` reçus après le premier : plusieurs
  dizaines avec `has_inline_audio=True` dans les tours réussis, **zéro**
  dans les tours qui échouent.

### 13.2 Un second phénomène, distinct, découvert dans le test 4 — à ne pas confondre avec le précédent

Le test 4 échoue aussi sur S6, mais avec une signature **complètement
différente**, jamais observée auparavant :

```
GEMINI_USER_TRANSCRIPT    g2-t8  text=" Quel est mon prÚnom ?"
MODEL_TURN_RECEIVED       g2-t8  parts_count=1 has_inline_audio=False has_text_part=True
TTS_START                 g2-t8
GEMINI_ASSISTANT_TRANSCRIPT  g2-t8  text="[appel outil] recall(query='prÚnom') exquisitely"
LIVE_DIAGNOSTIC_FIELDS    g2-t8  generation_complete=True
TURN_COMPLETE             g2-t8  had_content=True has_user_text=True has_model_content=True has_local_interrupt=False
```
`tool_active` reste `False` sur tout l'échange : **aucun appel d'outil réel
n'a eu lieu** (`src/gemini_live.py` ~ligne 1734 : `tool_active` ne passe à
`True` que si le serveur envoie réellement des `function_calls`). Le modèle
a donc **prononcé littéralement, en français, la phrase
« [appel outil] recall(query='prénom') exquisitely »** comme une réponse
vocale normale (un `output_transcription` existe, confirmé par
`GEMINI_ASSISTANT_TRANSCRIPT`), sans jamais produire le moindre octet audio
(`has_inline_audio` reste `False` sur l'unique `MODEL_TURN_RECEIVED` de ce
tour) ni de suite cohérente (« exquisitely » est un mot isolé, sans lien
avec le reste de la phrase).

**Ce qui est prouvé, par le code, pas supposé :**

- `src/conversation.py::to_gemini_contents()` (ligne ~320-327) sérialise tout
  appel d'outil antérieur en un `Content` de rôle **`"model"`** dont le texte
  littéral est `f"[appel outil] {message.text}"` (ex. :
  `"[appel outil] recall(query='prénom')"`) — exactement la chaîne
  prononcée. C'est **notre propre code**, pas une invention du modèle : ce
  préfixe `[appel outil]` est un artefact de présentation interne destiné à
  l'historique de conversation texte, jamais pensé pour être rejoué tel quel
  comme la **propre parole passée** du modèle dans une session Live.
- Ce `Content` (rôle `"model"`, texte `"[appel outil] ..."`) est exactement
  le format que `_seed_context()` rejoue au serveur après reconnexion comme
  historique de conversation.
- **Ce qui N'EST PAS prouvé (hypothèse, pas une certitude)** : qu'un appel
  réel à `recall` ait eu lieu plus tôt dans CETTE session précise pour que ce
  texte provienne effectivement d'un rejeu. Aucun `tool_active=True` n'apparaît
  nulle part dans le test 4 avant ce point — donc soit (a) un appel d'outil a
  eu lieu lors d'un tour non capturé par cette trace tronquée, soit (b) le
  modèle a **halluciné** ce texte de toutes pièces en imitant le style
  `[appel outil] nom(args)` qu'il a pu voir ailleurs dans son contexte
  système ou ses instructions (`recall` est un nom d'outil réel, déclaré et
  cité en toutes lettres dans `system_instruction`, cf.
  `src/gemini_live.py` ligne ~867), combiné à une troncature prématurée en
  plein mot (« exquisitely ») — cohérent avec le MÊME bug serveur documenté
  en §11.3 (« Mid-Sentence Audio Truncation »), mais appliqué cette fois à
  une génération qui avait commencé à produire du texte halluciné plutôt
  qu'à une génération totalement vide.

**Conclusion sur ce second phénomène : il s'agit très probablement de la
MÊME troncature prématurée côté serveur (§11.3), simplement observée sur
une génération qui avait déjà commencé à produire du contenu (halluciné)
avant d'être coupée, plutôt que sur une génération qui n'avait encore rien
produit.** Ce n'est PAS un bug différent nécessitant un correctif séparé au
sens strict — mais le contenu halluciné `[appel outil] ...` révèle un
défaut d'hygiène de prompt indépendant et potentiellement actionnable : le
préfixe `"[appel outil] {text}"` utilisé par `to_gemini_contents()` ressemble
à une phrase que l'assistant pourrait prononcer, ce qui est fragile dès lors
que ce texte est rejoué dans une session Live (vocale) et non plus consommé
par un LLM texte. **Cette hypothèse n'est pas confirmée avec une preuve
suffisante (un seul cas observé, mécanisme de rejeu non vérifié en pratique)
pour justifier une modification de `to_gemini_contents()` maintenant** —
conformément au point 10 de la consigne, elle est documentée ici comme piste
à creuser avec de nouvelles données plutôt qu'implémentée en spéculatif.

### 13.3 Réponse au critère de fin de l'utilisateur

**Ce qui est maintenant PROUVÉ (preuve directe, trace réelle, pas une
supposition) :**

1. Ce que Jarvis reçoit pendant `g2-t8` qui échoue : un unique `model_turn`
   contenant une part texte vide de sens pratique (jamais consommée,
   jamais de contenu audio), suivi immédiatement de `generation_complete=True`
   sans aucun autre champ de diagnostic renseigné — et ceci se reproduit
   identiquement 3 fois de suite (tentative originale + 2 relances).
2. Pourquoi cela produit `TTS_START` + `had_content=True` sans transcript
   assistant : `TTS_START` se déclenche sur la simple arrivée d'un
   `model_turn` quel que soit son contenu ; `had_content=True` est
   entièrement porté par la question utilisateur réellement transcrite
   (`has_user_text=True`), jamais par un contenu assistant
   (`has_model_content=False` est visible explicitement dans la trace).
3. En quoi cela diffère de `g1-t3` (tour réussi) : structurellement
   identique au tout début (un `model_turn` texte-seul déclenche
   `TTS_START` dans les deux cas), mais un tour réussi reçoit ensuite des
   dizaines de `model_turn` supplémentaires porteurs d'audio
   (`has_inline_audio=True`) avant `generation_complete=True` ; un tour qui
   échoue n'en reçoit plus aucun.
4. Qu'aucune instrumentation supplémentaire ne peut, à ce stade, en dire
   plus : les 4 champs ajoutés en §12.3 ont été observés sur des échecs
   réels et n'apportent aucune information complémentaire au-delà de ce qui
   était déjà documenté en §11.3 sur la base de la littérature externe.

**Ce qui reste une hypothèse, pas une preuve :** le mécanisme exact par
lequel le texte `"[appel outil] recall(query='prénom')"` est apparu dans le
test 4 (rejeu d'un appel réel vs. hallucination stylistique) — documenté en
§13.2, non résolu, nécessite soit une trace plus complète du test 4
(tours antérieurs à `g2-t8`), soit une reproduction délibérée.

**Décision pour cette session** : aucun changement de comportement
implémenté. Le mécanisme `EMPTY_GENERATION_RETRY` existant reste la
meilleure atténuation connue d'un bug serveur désormais confirmé,
indépendamment, comme n'émettant aucun signal diagnostique exploitable.
Le second phénomène (§13.2) est documenté comme piste à explorer, pas comme
correctif à implémenter faute de preuve suffisante.

## 14. Investigation dédiée — le marqueur « [appel outil] » prononcé par Gemini (test 4) : mécanisme client identifié, instrumenté, testé ; origine de l'occurrence réelle non tranchable avec les données disponibles

Mission : déterminer si le phénomène du test 4 (§13.2) — Gemini prononçant
littéralement `[appel outil] recall(query='prénom') exquisitely` — est une
fuite du rejeu d'historique d'outil vers le contexte Gemini, ou une
génération/hallucination spontanée. **L'investigation `EMPTY_GENERATION_RETRY`
est désormais considérée close** (§13.1) : aucun changement n'y a été
apporté dans cette section, les relances ne sont pas modifiées, la
compression n'est pas désactivée.

### 14.1 Inspection du chemin de données (`src/conversation.py`, `src/gemini_live.py`)

**Où un message `role="tool"` de type `KIND_TOOL_CALL` peut-il naître ?**
Un seul point d'entrée dans tout le code base :
`ConversationContext.add_tool_interaction()` (`src/conversation.py` ligne
~587), qui délègue à `add_tool_call()` (ligne ~546). `grep -rn
"add_tool_call\|add_tool_interaction"` sur `src/` ne trouve **qu'un seul
site d'appel** : `src/gemini_live.py` ligne ~1791, à l'intérieur du bloc
`if tc and tc.function_calls:` qui commence par `self.tool_active = True`
(ligne ~1734). **Conséquence prouvée par lecture directe du code (pas une
supposition) : un message `KIND_TOOL_CALL` ne peut exister dans
`ConversationContext` QUE si `tool_active` est passé à `True` au moins une
fois plus tôt dans la même session, pour un appel de fonction réellement
reçu du serveur.** Il n'existe aucun autre chemin (ni dans `src/tools.py`, ni
ailleurs) capable de fabriquer un tel message sans exécution réelle.

**Comment le préfixe `[appel outil]` est-il généré ?** `to_gemini_contents()`
(`src/conversation.py` ligne ~306-338) : pour tout message de rôle `tool` et
de genre `KIND_TOOL_CALL`, il produit `{"role": "model", "parts": [{"text":
f"[appel outil] {message.text}"}]}` où `message.text` est
`format_tool_call(name, args)` (ligne ~292-297), qui rend
`f"{name}({', '.join(f'{k}={v!r}' for k, v in compact.items())})"`. Pour
`recall(query="prénom")`, ceci produit EXACTEMENT
`"recall(query='prénom')"`, donc le texte complet
`"[appel outil] recall(query='prénom')"` — **chaîne identique, caractère
pour caractère, à ce qui a été entendu en test 4** (vérifié en §14.3 par un
test automatisé, pas seulement par inspection).

**Est-ce intentionnel ?** OUI, explicitement, d'après le docstring de la
fonction elle-même (ligne ~303-311) : « Les interactions d'outils sont donc
rattachées au rôle qui les produit : l'appel côté `model`, le résultat côté
`user` (c'est le canal par lequel l'environnement répond au modèle). » Ce
choix découle d'une contrainte réelle et documentée : l'API Gemini Live,
pour le rejeu (`send_client_content`), ne connaît que deux rôles
conversationnels (`user`/`model`) — il n'existe pas de rôle `tool` natif
pour ce canal. L'auteur du module a donc choisi de représenter un appel
d'outil passé comme un texte descriptif attribué au rôle `model` plutôt que
de l'omettre. **Mais rien dans le code ni la documentation n'anticipe le
risque qu'un modèle audio natif puisse PRONONCER ce texte littéralement** :
aucune consigne de prompt ne dit au modèle « ne répète jamais un texte
commençant par `[appel outil]` », et le même module expose, pour Ollama
(`to_ollama_messages()`, ligne ~341-377), une représentation **structurée et
native** du même événement (`{"role": "assistant", "tool_calls": [...]}`
façon OpenAI) — la version Gemini est donc une simplification ad hoc, pas
un choix uniforme entre fournisseurs.

**Un historique `recall(query='prénom')` peut-il exister sans appel réel
dans la session courante ?** Non — démontré en §14.1 (lecture du code) et
confirmé en §14.3 (test automatisé négatif). **Le contexte conversationnel
n'est jamais persisté entre sessions** (docstring de tête du module,
§ »Trois contextes distincts« , point 2 : « Jamais persisté sur disque,
remis à zéro au démarrage. ») — un `recall` exécuté lors d'un lancement
antérieur de Jarvis ne peut donc pas non plus être la source : à chaque
démarrage, l'historique repart vide.

**Les messages d'outils sont-ils bien rejoués après reconnexion/seed ?**
Oui, sans condition particulière : `_seed_context()` (`src/gemini_live.py`
ligne ~587) appelle `to_gemini_contents(self.conversation.get_messages())`
sur TOUT l'historique local conservé (borné par `max_turns`/`max_tokens`,
§ »Limitation de taille« ), sans filtrage par type de message — un appel
d'outil présent dans la fenêtre glissante conservée est donc rejoué comme
n'importe quel autre message. Le seed n'est contourné QUE si
`self.resumption_handle` est présent (restauration côté serveur) — **dans
le scénario S5/S6, le harness force explicitement
`force_disconnect(drop_handle=True)`** (`scripts/validate_reconnect_v174_real.py`
ligne ~670), ce qui garantit que le chemin de rejeu local est bien exercé à
chaque exécution du scénario, sans ambiguïté possible sur ce point précis.

### 14.2 Remontée dans la trace du test 4 avant `g2-t8`

**Limite factuelle à annoncer explicitement** : la portion de la trace du
test 4 antérieure au tour `g2-t8` (en particulier le tour où « Mon prénom
est Simon. » a été dit, et tout tour S1-S4 précédent) n'est plus disponible
dans cette session de travail — seul l'extrait montrant `g2-t8` a été
conservé dans les notes d'investigation. **Il est donc impossible, à ce
stade, de confirmer ou d'infirmer directement si un `tool_active=True` est
apparu plus tôt dans ce run précis, ou si un appel réel à `recall` a eu
lieu avant la coupure.** Ceci est une limite de preuve explicite, pas une
supposition déguisée.

### 14.3 Reproduction ciblée et déterministe (ce qui PEUT être tranché sans la trace manquante)

Trois tests ont été ajoutés à `tests/test_live_context_harness.py`
(classe `ToolCallReplaySignatureTests`), construits sur le harness réel
existant (`VoiceHarness` + `FakeLiveServer`, le même socle que les
scénarios A-H déjà en place) — **pas des mocks grossiers : le code de
production (`GeminiLive`, dispatch d'outils, `ConversationContext`,
`to_gemini_contents`, `_seed_context`) tourne réellement** :

1. **`test_I_appel_reel_de_recall_est_rejoue_tel_quel_apres_reconnexion`**
   — un VRAI appel à `recall(query='prénom')` est déclenché (le serveur
   factice envoie un `function_call`, le code de production l'exécute via
   `TOOL_FUNCTIONS`, exactement comme avec le vrai SDK), suivi d'une
   reconnexion sans handle. Résultat : la chaîne EXACTE
   `"[appel outil] recall(query='prénom')"` apparaît, verbatim, sous
   `role="model"`, dans le `client_content` physiquement envoyé à la
   session suivante. **PASS.**
2. **`test_I_sans_appel_reel_aucune_trace_du_marqueur_dans_le_rejeu`**
   (contrôle négatif) — même scénario de reconnexion, mais sans qu'aucun
   outil n'ait jamais été appelé : le marqueur `[appel outil]` n'apparaît
   nulle part dans le rejeu, et la nouvelle instrumentation
   `CONTEXT_REPLAY_TOOL_ENTRIES` le confirme explicitement
   (`tool_call_names=[]`). **PASS.**
3. **`test_I_marqueur_dans_la_transcription_assistant_est_detecte`** — valide
   la nouvelle instrumentation de détection elle-même. **PASS** (après
   correction : la première version ne vérifiait que le dernier fragment de
   transcription reçu, alors que la transcription arrive par morceaux et le
   marqueur peut être coupé pile entre deux fragments — ce test a
   directement mis ce défaut en évidence avant correction, cf. §14.4).

**Conclusion de la reproduction : le mécanisme client est capable,
mécaniquement et de façon répétable, de produire exactement la chaîne
observée en test 4 — à la seule et unique condition qu'un appel réel à
`recall` ait eu lieu plus tôt dans la session.** Ceci ne prouve pas que
CE fut le cas dans le test 4 spécifiquement (cf. limite §14.2), mais prouve
que l'hypothèse « fuite du rejeu » est mécaniquement vraie et pas une pure
spéculation.

### 14.4 Instrumentation ajoutée (lecture seule, aucun changement de comportement)

Trois événements de trace, tous visibles via `trace_events()` /
`JARVIS_AUDIO_TRACE`, aucun ne modifie le comportement produit :

- **`TOOL_CALL_EXECUTED`** (`src/gemini_live.py`, juste après
  `add_tool_interaction`) — journalise `name` (nom de l'outil) et le tour de
  conversation courant, à chaque exécution RÉELLE d'un outil par le serveur.
  Permet de répondre sans ambiguïté, sur une future trace, à « un
  `recall` réel a-t-il eu lieu dans cette session, et quand ? ».
- **`CONTEXT_REPLAY_TOOL_ENTRIES`** (`src/gemini_live.py::_seed_context`,
  juste avant l'envoi) — journalise `tool_call_names`/`tool_result_names` :
  la liste des noms d'outils présents dans l'historique sur le point d'être
  rejoué. Permet de vérifier directement si l'historique rejoué CONTENAIT
  un appel à `recall` au moment précis de la reconnexion.
- **`TRANSCRIPT_TOOL_MARKER_LEAK`** (`src/gemini_live.py`, sur
  `output_transcription`) — se déclenche si le texte accumulé de la
  transcription assistant du tour en cours contient littéralement
  `"[appel outil]"` ou `"[résultat outil]"`. Vérifie le texte ACCUMULÉ du
  tour (pas seulement le dernier fragment reçu) : un test dédié a montré que
  le marqueur peut être coupé entre deux fragments de transcription
  consécutifs, ce qui aurait fait manquer l'événement avec une vérification
  fragment par fragment.

Aucune donnée sensible n'est journalisée au-delà de ce qui l'était déjà
(noms d'outils, déjà visibles via `on_tool_start`/`on_tool_end` ; aucun
argument brut n'est ajouté aux nouveaux événements).

### 14.5 Classification demandée

**B — Comportement client intentionnel, dont l'interaction avec un modèle
audio natif n'a manifestement pas été anticipée ni garde-fouée — combiné à
D pour la question spécifique de l'occurrence du test 4.**

- Le mécanisme qui envoie `"[appel outil] {nom}({args})"` comme texte
  `role="model"` à Gemini lors du rejeu post-reconnexion est **prouvé
  intentionnel** (docstring explicite, contrainte réelle de l'API — pas de
  rôle `tool` natif dans `send_client_content`) et **prouvé mécaniquement
  capable** de produire exactement la chaîne entendue en test 4, à la
  condition qu'un appel réel ait eu lieu (§14.3). C'est la réponse à la
  question « ce mécanisme est-il volontaire et pourrait-il expliquer le
  phénomène ? » — **oui aux deux**.
- En revanche, savoir si CETTE occurrence précise (test 4) a été
  effectivement causée par ce mécanisme — c'est-à-dire si un `recall` réel
  a eu lieu avant `g2-t8` dans ce run — **reste non tranché (D)**, faute de
  la portion de trace antérieure à `g2-t8` (§14.2). Une capture future avec
  l'instrumentation du §14.4 lèvera cette ambiguïté sans effort
  supplémentaire : soit `TOOL_CALL_EXECUTED(name=recall)` apparaît avant la
  reconnexion et `CONTEXT_REPLAY_TOOL_ENTRIES` liste `recall` au rejeu
  suivant (→ A confirmé, la fuite est la cause), soit aucun des deux
  n'apparaît alors que `TRANSCRIPT_TOOL_MARKER_LEAK` se déclenche quand
  même (→ C, hallucination/génération spontanée du style par Gemini,
  probablement en imitant le nom d'outil réel `recall` cité dans le prompt
  système, combinée à la troncature prématurée déjà documentée en §11.3).

### 14.6 Décision pour cette section

Conformément à la consigne : **aucune correction n'est appliquée.** Le
mécanisme `[appel outil]`/`[résultat outil]` n'est pas retiré ni modifié —
il n'est démontré ni comme la cause confirmée de cette occurrence
spécifique, ni comme une fuite dans l'absolu (il est prouvé qu'IL POURRAIT
l'être, pas qu'il L'EST dans ce cas précis). Le protocole de seed/reconnexion
n'est pas modifié. Les relances (`EMPTY_GENERATION_RETRY`) ne sont pas
modifiées. La compression n'est pas désactivée. Seule de l'instrumentation
de lecture seule a été ajoutée (§14.4), accompagnée de 3 tests de
non-régression ciblés (§14.3) qui passent, en plus de la suite existante
(197 tests exécutés, 196 OK ; le seul échec,
`test_3x_le_seed_est_committe_sans_recap_et_rappelle`, est confirmé
pré-existant et indépendant de ce travail — il échoue identiquement sur
l'état déjà poussé `0cca5be`, avant toute modification de cette section,
et reproduit un dépassement de délai de 5 s spécifique à l'environnement
d'exécution de ces vérifications, sans rapport avec l'investigation outil).

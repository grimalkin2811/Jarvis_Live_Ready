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

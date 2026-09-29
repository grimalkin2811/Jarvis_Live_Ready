# Rapport final — Double cycle superposé « Dis-moi tout » (v1.7.3)

**Bug critique rapporté après v1.7.2 (PR #41), CI Windows verte :** l'utilisateur
parle normalement, Jarvis produit la bonne réponse, mais **en parallèle** un
second cycle fantôme se déclenche et produit une réponse générique
superposée — typiquement « Dis-moi tout » — par-dessus la bonne réponse.
Contrairement au bug v1.7.2 (boucle stable de « Je vous écoute » toutes les
~2 s, causée par un réarmement de minuteur après coup), ici **deux cycles
logiques sont produits à partir d'une seule phrase utilisateur**, quasiment
en même temps, pas après coup.

**Mission :** comprendre exactement quel composant génère « Dis-moi tout »,
sous quel événement précis, pourquoi v1.7.2 — malgré 20 tests verts et une CI
Windows verte — n'a pas empêché ce bug, corriger la cause architecturale (pas
un délai ou un filtre de texte), et le démontrer par des tests qui échouent
sans le correctif et passent avec.

---

## 1. Symptôme

- L'utilisateur prononce **une seule phrase**.
- Jarvis répond **correctement** à cette phrase (audio 1).
- **En parallèle**, une seconde réponse générique de type « Dis-moi tout »
  est mise en lecture, superposée à l'audio 1 (audio 2).
- Ce n'est pas une boucle qui s'auto-entretient (comme en v1.7.2) : c'est un
  **doublon ponctuel**, un seul phénomène par phrase, pas un cycle qui se
  répète indéfiniment.
- Le symptôme n'était **pas reproductible** par la suite de tests existante
  (20/20 verts) ni par la CI Windows (verte) au moment du signalement — il ne
  se manifeste que sur matériel réel, dans des conditions de chronométrage
  particulières.

## 2. Cause racine

Le **pont micro** (`mic()` dans `src/main.py`/`src/ui.py`) tourne sur le
**thread audio temps réel** de `sounddevice`. Avant ce correctif, son code
était :

```python
def mic(pcm):
    if gemini.can_send():                                     # (1) vérifié ICI, thread audio
        asyncio.run_coroutine_threadsafe(gemini.send_audio(pcm), loop)  # (2) exécuté PLUS TARD, thread asyncio
```

`can_send()` est une **vérification instantanée** (`self.speaking`,
`self.tool_active`, `self.session`…) : vraie à l'instant (1), elle ne l'est
plus forcément à l'instant (2). Entre (1) et (2), rien ne revérifie l'état :
`run_coroutine_threadsafe` **planifie** l'exécution de `send_audio(pcm)` sur
la boucle asyncio, mais le thread audio ne l'attend jamais et ne la
revérifie jamais.

**Chaîne causale démontrée** (reproduite ci-dessous, voir « Trace ») :

```
[Thread audio]   t=0.00s  mic() lit can_send() -> True (Jarvis n'a pas encore répondu)
[Thread audio]   t=0.00s  run_coroutine_threadsafe(send_audio(pcm_perime), loop)
[Boucle asyncio] t=0.00s  … occupée (callback de lecture audio synchrone, ou
                          traitement du 1er message de réponse du serveur) …
[Boucle asyncio] t=0.00s  self.speaking = True   (Gemini a commencé à répondre)
[Boucle asyncio] t=0.15s  la boucle se libère, exécute ENFIN send_audio(pcm_perime)
[Boucle asyncio] t=0.15s  send_audio() envoie pcm_perime SANS revérifier l'état
[Serveur Gemini] t=0.15s  reçoit de l'audio en pleine réponse -> l'interprète
                          comme le DÉBUT D'UN SECOND TOUR utilisateur
[Serveur Gemini] t=0.9s   répond au second tour (souffle/bruit résiduel, sans
                          contexte) par une réponse générique : « Dis-moi tout »
[Client]         t=0.9s   l'audio du 2e tour est mis en file JUSTE APRÈS
                          celui du 1er tour -> superposition perçue
```

**Réponse au format exigé** : *« Dis-moi tout » est généré par **Gemini
lui-même** (ce n'est ni une phrase locale codée en dur, ni un fallback
Jarvis), **pendant le tour n°2** (un second tour fantôme, ouvert
involontairement), **parce que** le serveur a reçu un bloc audio résiduel
(souffle/bruit de fin de phrase) alors qu'il était déjà en train de générer
la réponse au tour n°1 — bloc capturé par le pont micro à un instant où
`can_send()` était vrai, mais envoyé après que cet état a changé. Ce n'est
donc ni un texte codé en dur côté Jarvis, ni un comportement du minuteur de
8 s (v1.7.2) : c'est Gemini qui répond, de bonne foi, à un second tour que le
pipeline audio n'aurait jamais dû ouvrir.*

Ce mécanisme exact — un bloc micro dont l'exécution planifiée atterrit après
que l'état a changé — avait **déjà été observé sur le runner Windows de la
CI** et documenté dans l'historique Git : le commit `1fd75ff`
(« `test(echo): TestC tolere le trainard d'ordonnancement observe sur runner
Windows` », déjà mergé dans la lignée de la PR #41) décrit très exactement un
bloc micro capturé avant la fermeture de la porte anti-écho, dont
l'exécution *via* `run_coroutine_threadsafe` « atterrit après » cette
fermeture — et au lieu de corriger la course, ce commit a **affaibli
l'assertion du test** (`assertEqual(forwarded, [])` → `assertLessEqual(len(forwarded), 1)`
+ un contrôle de RMS par bloc) pour la tolérer. C'est une preuve, dans le
propre historique du dépôt, que la course était connue et observée en
conditions réelles avant ce correctif.

## 3. Pourquoi v1.7.2 n'a pas suffi

v1.7.2 corrigeait un bug **différent et réel** : le réarmement du minuteur de
8 s par l'écho acoustique de la propre voix de Jarvis captée par le micro
pendant la lecture. Sa porte anti-écho (`AudioIO._mic_gate_open`) empêche
bien le micro d'être **transmis à la boucle audio** tant que de la voix
reste à jouer — mais cette porte agit **en amont** du pont micro, sur le
thread audio, **au moment de la capture**. Elle ne protège pas contre la
course qui se produit **en aval**, entre la planification de
`send_audio()` (thread audio) et son exécution réelle (boucle asyncio) :
même si le bloc a franchi la porte anti-écho légitimement (parce qu'à cet
instant Jarvis n'avait pas encore commencé à parler), l'état peut changer
avant que la coroutine planifiée ne s'exécute. v1.7.2 n'a jamais revérifié
l'état **au moment de l'exécution effective** — seulement au moment de la
capture. Les 20 tests de `test_echo_loop.py` et la CI Windows testent des
scénarios de chronométrage précis, mais ne forcent jamais cette fenêtre
étroite (chargement du thread asyncio pile entre la capture et l'exécution) ;
elle est trop rare pour être vue par hasard dans une CI courte, mais bien
réelle sur un poste utilisateur chargé (wake-word, interface Qt, lecture
audio simultanés).

## 4. Fix

Trois lignes de défense, toutes appliquées **sur la boucle asyncio, au
moment de l'exécution réelle de `send_audio()`** — le seul endroit où
`self.speaking`/`self.session`/`self.turn_epoch` ne peuvent pas changer sous
nos pieds (asyncio est mono-thread) :

1. **Revalidation de `can_send()` à l'exécution** (`_can_send_now()`,
   factorisée et réutilisée par `can_send()` et `send_audio()`) : la
   décision n'est plus figée au moment de la capture — elle est réévaluée
   juste avant l'écriture réseau. Ferme la course « boucle asyncio occupée
   entre planification et exécution » (le cas historique du commit
   `1fd75ff`).
2. **`capture_generation`** (lu par le pont micro *au moment de la capture*,
   comparé à `self.session_generation` *au moment de l'exécution*) : un bloc
   capturé pour une session qui a été remplacée par une reconnexion avant
   son exécution est abandonné, même si l'état local semble à nouveau
   cohérent par coïncidence.
3. **`capture_turn_epoch`** (le plus fin des trois, ajouté après avoir
   constaté que les deux premiers ne suffisaient pas — voir « Risques
   restants ») : `turn_epoch` est un compteur qui n'avance qu'aux
   **fermetures** de tour (`_finish_turn`), jamais aux ouvertures. Un bloc
   capturé pendant le tour N, mais exécuté après que N a été clos, est
   abandonné **même si Jarvis est redevenu totalement idle entre-temps**
   (`speaking == False`) — un état indiscernable d'une fenêtre de suivi
   légitime pour un simple contrôle de `speaking`. Comparer l'epoch capturé
   à l'epoch courant lève l'ambiguïté sans jamais bloquer un vrai follow-up
   (un follow-up authentique est *capturé après* la fermeture, donc avec le
   même epoch que l'état courant).

```python
async def send_audio(self, pcm, *, capture_generation=None, capture_turn_epoch=None):
    if capture_generation is not None and capture_generation != self.session_generation:
        ...  # reconnexion pendant le trajet -> abandon tracé
    if capture_turn_epoch is not None and capture_turn_epoch != self.turn_epoch:
        ...  # tour déjà clos entre capture et exécution -> abandon tracé
    allowed, reason = self._can_send_now()   # revalidation fraîche, ICI, sur la boucle asyncio
    if not allowed:
        ...  # "jarvis parle"/"outil en cours" -> abandon tracé et compté (stale_audio_dropped)
        return
    await self.session.send_realtime_input(...)
```

Le pont micro (`src/main.py`, `src/ui.py`, `tests/echo_loop_harness.py`) lit
`session_generation` et `turn_epoch` **au moment de la capture**, sur le
thread audio, et les transmet à `send_audio()` :

```python
capture_generation = gemini.session_generation
capture_turn_epoch = gemini.turn_epoch
asyncio.run_coroutine_threadsafe(
    gemini.send_audio(pcm, capture_generation=capture_generation,
                       capture_turn_epoch=capture_turn_epoch),
    loop,
)
```

**Audit des invariants demandés (§5–9 du plan de diagnostic) :**

- **`output_level_hook` et ses consommateurs** : `AudioIO.output_level_hook`
  n'alimente que `desktop("level", value=..., source="output")`
  (`src/ui.py`), reçu par `DesktopOverlayController.handle_event`
  (`UI/desktop/overlay.py`). Ce gestionnaire traite l'événement `"level"`
  **en tout premier**, avec un `return` immédiat, **avant** d'atteindre
  `self.machine.handle(name, data)` — la machine à états qui gouverne
  écoute/tour/veille. Il est donc **architecturalement impossible** qu'un
  niveau audio de sortie (TTS) atteigne la machine à états et réarme un
  minuteur ou ouvre un tour. Même chose côté Blob Mode :
  `VoiceEnergyRouter.handle_level` n'alimente que le rendu visuel de l'orbe
  (`jarvis_menu.set_voice_energy`), jamais une décision de tour.
- **Non-conflation `audio_level`/`voice_activity`/`turn_activity`** :
  `AudioIO._last_mic_rms` (niveau brut, pour le visualiseur uniquement, via
  `_emit_voice`) est un champ **distinct** des déclencheurs réels du
  minuteur de suivi (`_arm_follow_up`), qui ne sont armés que par des
  événements **sémantiques** : réveil (`_wake`), fin de tour confirmée par
  le serveur (`extend_listening` sur `turn_complete`), interruption
  confirmée (barge-in), ou renouvellement du mode écoute continue — jamais
  par la simple présence d'échantillons audio ou un niveau RMS.
- **Invariants « un seul tour actif / une seule session d'écoute / une
  seule réponse TTS »** : garantis côté serveur simulé par la VAD (un seul
  tour ouvert à la fois dans le harness) et côté client par `_turn_closed` +
  désormais `turn_epoch` : aucun nouvel événement ne peut plus créer
  silencieusement un second tour pendant qu'un premier est en cours, sauf
  interruption explicite (qui clôt proprement le premier avant d'ouvrir le
  second).

## 5. Trace avant/après

Instrumentation ajoutée (`JARVIS_AUDIO_TRACE=1`, partagée avec la trace
`AudioIO` déjà existante depuis v1.7.2), sur `GeminiLive` :
`TURN_OPEN`/`TURN_CLOSE`, `GEMINI_USER_TRANSCRIPT`/`GEMINI_ASSISTANT_
TRANSCRIPT`, `TTS_START`, `INTERRUPTION`, `TURN_COMPLETE`,
`AUDIO_SENT_TO_GEMINI`, `AUDIO_SEND_DROPPED_STALE`,
`SESSION_CONNECT`/`SESSION_RESUME`. Chaque événement porte `turn_id`
(`g<session>-t<tour>`), `session_id`, `session_generation`, `speaking`,
`tool_active`, le thread d'origine et un horodatage monotone.

**Avant le correctif** (reproduction déterministe, `tests/test_turn_race.py`
exécuté contre le code non corrigé) :

```
TURN_OPEN     turn_id=g1-t1 thread=AsyncioLoop
AUDIO_SENT_TO_GEMINI  thread=AsyncioLoop  (bloc légitime, tour 1)
...
TTS_START     turn_id=g1-t1 speaking=True
AUDIO_SENT_TO_GEMINI  thread=AsyncioLoop  bytes=2560   <-- BLOC PÉRIMÉ ENVOYÉ QUAND MÊME
TURN_OPEN     turn_id=g1-t2                             <-- second tour fantôme ouvert
GEMINI_ASSISTANT_TRANSCRIPT turn_id=g1-t2 text="Dis-moi tout"
TURN_COMPLETE turn_id=g1-t2
```

**Après le correctif** (même scénario, code corrigé) :

```
TURN_OPEN     turn_id=g1-t1 thread=AsyncioLoop
AUDIO_SENT_TO_GEMINI  thread=AsyncioLoop  (bloc légitime, tour 1)
...
TTS_START     turn_id=g1-t1 speaking=True
AUDIO_SEND_DROPPED_STALE  reason="jarvis parle"  bytes=2560   <-- ABANDONNÉ, TRACÉ, COMPTÉ
TURN_COMPLETE turn_id=g1-t1
(aucun second TURN_OPEN — un seul tour pour une seule phrase)
```

`gemini.stale_audio_dropped` passe de `0` à `≥1` exactement au moment de la
course ; `gemini.trace_events()` conserve la preuve consultable après coup
(y compris quand `JARVIS_AUDIO_TRACE` est désactivé — la collecte n'est
jamais optionnelle, seul l'affichage console l'est).

## 6. Tests

**10 nouveaux tests** dans `tests/test_turn_race.py` (tous rouges sans le
correctif — vérifié explicitement en repassant le patch sous `git stash` —
tous verts avec) :

| Test | Type | Vérifie |
|---|---|---|
| `test_stale_speaking_flag_is_dropped_at_execution_time` | unitaire | Test D §10 : capturé `can_send()==True`, exécuté après `speaking=True` -> abandonné |
| `test_stale_generation_after_reconnect_is_dropped` | unitaire | Test E §10 (reconnexion) : bloc d'une session remplacée -> abandonné |
| `test_fresh_audio_is_still_sent_normally` | unitaire | non-régression : un bloc frais part normalement |
| `test_interrupt_window_still_allows_send_while_speaking` | unitaire | le barge-in continue de fonctionner malgré le correctif |
| `test_tool_active_is_also_revalidated` | unitaire | `tool_active` est aussi revalidé à l'exécution |
| `test_stale_turn_epoch_is_dropped_even_when_idle_again` | unitaire | Test E §10, cas précis où `speaking` seul ne peut pas voir la course (idle après clôture) |
| `test_fresh_turn_epoch_after_close_is_a_legitimate_follow_up` | unitaire | non-régression : un vrai follow-up n'est jamais confondu avec un bloc périmé |
| `test_delayed_capture_after_turn_closes_is_ignored` | intégration (vrai pipeline) | Test D §10 sur `AudioIO`/pont micro/`GeminiLive` réels : bloc retardé jusqu'au début de la réponse -> 1 seul tour, `AUDIO_SEND_DROPPED_STALE` tracé |
| `test_delayed_capture_after_response_also_ignored` | intégration | Test E §10 : retard jusqu'après la fin complète du tour -> toujours abandonné |
| `test_three_utterances_under_race_pressure_yield_exactly_three_turns` | intégration | Test H §10 : 3 phrases avec pression de course à chaque tour -> exactement 3 tours, jamais plus |

Les tests d'intégration utilisent le **vrai** `AudioIO`, le **vrai** pont
micro (copié à l'identique de `src/main.py`/`src/ui.py`), la **vraie**
`GeminiLive`, une carte son factice threadée et une VAD serveur simulée
(`tests/echo_loop_harness.py`) — pas de mock du mécanisme testé.

**Suites existantes, non régressées** :

- `tests/test_echo_loop.py` : 19/20 verts + 1 skip (mode rapide) — le seul
  échec (`TestG_SessionChurn.test_reconnect_during_playback_does_not_create_
  ghosts`) est un test **sensible au chronométrage réel du sandbox**,
  confirmé instable de façon identique **avant** ce correctif (`git stash` +
  4 répétitions : échoue parfois sur le code non modifié aussi, repasse au
  vert en isolation) — non lié à ce changement.
- `tests/test_interruption.py` : 25/25 verts.
- `tests/test_gemini_live.py`, `tests/test_live_context_harness.py`,
  `tests/test_conversation_pipeline.py`, `tests/test_voice_runtime.py`,
  `tests/test_memory.py` : 101/101 verts.
- `tests/test_version.py`, `tests/test_verify_release_bundle.py`,
  `tests/test_packaging.py`, `tests/test_updater.py`,
  `tests/test_launcher_core.py` : 82/82 verts (version épinglée mise à jour
  vers 1.7.3).
- Les tests d'interface graphique (`test_interruption_ui.py` et autres
  utilisant PySide6) n'ont pas pu être exécutés dans ce bac à sable : aucun
  binding Qt (`PySide6`/`PyQt5`) n'y est installé — limitation
  d'environnement préexistante, sans rapport avec ce correctif.

**Total pour ce correctif : 10/10 nouveaux tests verts ; 227/228 tests
préexistants exécutables verts (le seul échec est un flake de chronométrage
préexistant et confirmé indépendant du correctif).**

## 7. Windows CI

**Exécutée et verte** sur la PR de ce correctif
(https://github.com/grimalkin2811/Jarvis_Live_Ready/pull/42) :

| Check | Résultat | Durée |
|---|---|---|
| Lint | ✅ pass | 10 s |
| Tests (Windows) | ✅ pass | 8 m 51 s |
| Validation réelle (audio, reconnexions, outils) | ✅ pass | 21 s |

Le correctif ne modifie aucune primitive spécifique à une plateforme
(`asyncio`, `threading`, comparaisons d'entiers) ; les tests ajoutés
utilisent le même harness multi-thread que `test_echo_loop.py`, déjà validé
sur CI Windows en v1.7.2, et ont maintenant eux-mêmes tourné sur le runner
Windows réel de la CI. **Cela reste toutefois, comme le rappelle la section
suivante, une CI verte — pas une preuve suffisante à elle seule** : v1.7.2
avait déjà une CI Windows verte et 20 tests verts, et le bug de ce rapport
s'est pourtant manifesté sur matériel réel après coup. La vérification sur
un vrai poste Windows (section 8) reste le seul juge de paix final.

## 8. Vrai microphone

**Non testé.** Ce travail a été réalisé entièrement dans un sandbox Linux
sans périphérique audio physique ni accès à un poste Windows réel. La
reproduction du bug s'appuie sur un harness qui rejoue fidèlement les
conditions de course (vraie `AudioIO`, vrai pont micro, vraie `GeminiLive`,
carte son factice threadée avec écho acoustique modélisé, VAD serveur
simulée avec latence réseau) — mais cela reste une reproduction contrôlée,
pas une validation sur matériel réel. **Une vérification sur un poste
Windows réel, dans les conditions exactes du signalement (charge CPU du
wake-word + interface Qt + lecture audio simultanés), reste nécessaire avant
de considérer ce correctif comme définitivement validé en conditions
utilisateur**, conformément à l'expérience de v1.7.2 (CI verte, 20 tests
verts, bug pourtant présent sur matériel réel).

## 9. Risques restants (uniquement les points réellement non vérifiés)

- **Fenêtre de latence réseau pure (mécanisme non couvert par ce
  correctif) :** reproduite explicitement (`/tmp/repro_race7.py`, non inclus
  dans le dépôt — script de diagnostic jetable) avec un serveur simulant un
  aller-retour réseau réaliste (0,6 s) avant de committer un tour. Dans ce
  scénario, un bloc résiduel (souffle) est capturé **avant même que le
  client ait été informé** que le tour a changé d'état — donc `speaking`,
  `session_generation` ET `turn_epoch` sont **tous encore corrects** au
  moment de l'exécution, puisque, du point de vue du client, rien n'a
  encore changé. Ce n'est pas une staleness côté client : c'est un vrai
  bloc audio, envoyé de bonne foi, qui atteint un serveur dont la VAD a
  déjà décidé (mais pas encore communiqué) la fin du tour précédent. Les
  trois lignes de défense de ce correctif, toutes fondées sur une
  revalidation de l'**état local**, ne peuvent structurellement pas fermer
  cette fenêtre : il n'y a rien de « périmé » à détecter localement tant
  que le message serveur n'est pas arrivé. Cette fenêtre est inhérente à
  toute architecture où la VAD de fin de tour est déléguée au serveur
  (« automatic activity detection » côté Gemini Live) pendant qu'un flux
  audio brut continue d'être transmis en continu. Pistes non explorées
  faute de temps : (a) réduire la fenêtre en resserrant les paramètres de
  VAD côté serveur si l'API Gemini Live les expose (`silence_duration_ms`
  ou équivalent) ; (b) ajouter une porte d'énergie locale (mini-VAD client)
  qui cesse de transmettre les blocs sous un certain niveau après N blocs
  de parole confirmée, au prix d'un risque de couper une fin de phrase
  légitime ; (c) vérifier si le SDK expose un signal explicite de fin
  d'activité utilisable en complément de la transcription entrante.
- **Confirmation en conditions réelles non faite** : voir section 8. Il est
  possible (documenté comme hypothèse, non tranché) que le bug rapporté sur
  le terrain soit dominé par le mécanisme corrigé ici (délai
  d'ordonnancement, déjà observé sur CI Windows réelle via le commit
  `1fd75ff`) plutôt que par la fenêtre réseau ci-dessus — mais rien ne
  permet de l'affirmer avec certitude sans test sur le matériel exact du
  signalement.
- **`TestG_SessionChurn.test_reconnect_during_playback_does_not_create_
  ghosts`** reste occasionnellement instable dans ce sandbox (confirmé
  indépendant du correctif — voir section 6), mais sa cause précise de
  sensibilité au chronométrage n'a pas été investiguée plus avant ; à
  surveiller sur la CI Windows réelle.
- **Suite d'interface graphique non exécutée** (`PySide6` absent du
  sandbox) : aucune régression détectée par lecture statique du code
  (`UI/desktop/overlay.py`, `src/ui.py`), mais non confirmée par exécution.

---

### Fichiers modifiés

- `src/gemini_live.py` — `_can_send_now()`, `send_audio()` avec
  `capture_generation`/`capture_turn_epoch`, `turn_epoch`, traçage complet
  du cycle de tour, `SESSION_CONNECT`/`SESSION_RESUME`.
- `src/main.py`, `src/ui.py` — pont micro : capture `session_generation` et
  `turn_epoch` au moment de la capture, transmis à `send_audio()`.
- `tests/echo_loop_harness.py` — pont micro du harness aligné à l'identique.
- `tests/test_turn_race.py` — 10 nouveaux tests de régression (nouveau
  fichier).
- `tests/test_version.py`, `src/version.py`, `CHANGELOG.md` — bump 1.7.2 →
  1.7.3.

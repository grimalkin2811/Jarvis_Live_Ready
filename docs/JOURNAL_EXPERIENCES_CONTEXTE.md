# Journal d'expériences — contexte conversationnel (2026-09-28)

Chaque expérience suit le format demandé : hypothèse → modification → test
minimal → résultat → explication → décision. Les expériences E1–E3 sont la
démonstration « avant correction » sur le code v1.7.0 intact, exécutées avec le
harness (`tests/live_harness.py` + `tests/voice_harness.py`), faux serveur Live
fidèle au protocole documenté et aux comportements rapportés (thread Google AI
111617, pipecat, issues SDK).

Rejouables à tout moment : `python scripts/diag_conversation_context.py`.

---

## E1 — Rappel dans une session unique (témoin)

* **Hypothèse** : dans une session Live continue, le contexte audio réside côté
  serveur ; le rappel doit fonctionner sans intervention locale.
* **Test** : « Mon prénom est Simon. » → réponse → « Quel est mon prénom ? »
  (audio, même session).
* **Résultat** : **PASS** — « Ton prénom est Simon. »
* **Explication** : le pipeline local n'est pas en cause ; le symptôme réel
  apparaît donc au recréement de session.
* **Décision** : garder ce scénario comme témoin (régression).

## E2 — Reconnexion sans handle : le rejeu « pending » est invisible pour l'audio

* **Hypothèse (BUG 1)** : `_replay_context` envoie l'historique en
  `clientContent` avec `turn_complete=False` et ne le clôture jamais ; sur les
  modèles audio 2.x, ce contenu en attente n'est PAS rappelé par le tour audio
  suivant.
* **Test** : session sans handle de reprise (`suppress_resumption_updates`) :
  « Mon prénom est Simon. » → coupure → reconnexion → « Il fait beau. » →
  « Quel est mon prénom ? ».
* **Résultat** : **FAIL** — « Je ne sais pas, tu ne me l'as pas dit. »
  Trace du câble (session 2) : `1 clientContent, turn_complete=[False]`
  — l'historique a bien été ENVOYÉ mais JAMAIS committé : le modèle y répond
  « comme en début de conversation ». C'est exactement le symptôme rapporté
  (« Jarvis sait qu'il y a eu X demandes et Y réponses » — le contexte local
  contient tout — « mais ne retrouve pas Simon »).
* **Explication** : convergent avec le thread 111617 (même modèle
  `gemini-2.5-flash-native-audio-preview-12-2025`), le thread 62949
  (clientContent en attente + realtimeInput), l'avertissement officiel du SDK
  (« Interleaving send_client_content and send_realtime_input … can lead to
  unexpected results ») et l'implémentation pipecat (« *without this, the
  model ignores the context it's been seeded with before the user started
  speaking* »).
* **Décision** : corriger le protocole de rejeu (E8).

## E3 — Handle expiré : boucle de reconnexion infinie

* **Hypothèse (BUG 3)** : Jarvis garde un handle mort ; `connect()` échoue
  (APIError 1007 « Invalid session handle ») et la boucle externe réessaie
  indéfiniment avec le même handle, sans jamais retomber sur une session neuve
  + rejeu.
* **Test** : handles invalidés côté serveur (> 2 h), coupure, 0,5 s d'attente.
* **Résultat** : **FAIL** — session jamais rétablie, `connect_count=38` en
  0,5 s (loop), aucun rejeu.
* **Explication** : aucun chemin de repli sur handle invalide.
* **Décision** : repli automatique handle → session neuve + rejeu (E9).

## E4 — Seed terminant par un tour `model`

* **Hypothèse (BUG 2)** : sur 2.x le seed doit se terminer par un tour user
  (pipecat : « *we append a blank user turn to satisfy the server* ») ; le
  contexte Jarvis se termine presque toujours par une réponse assistant.
* **Test** : inspection du payload actuel — `to_gemini_contents` produit un
  dernier contenu `role="model"`.
* **Résultat** : écart de protocole confirmé côté payload (le faux serveur le
  trace comme `seed_termine_par_tour_model`).
* **Décision** : ajouter un tour user vide en fin de seed sur les modèles 2.x.

## E5 — Course audio / rejeu à la connexion

* **Hypothèse (BUG 4)** : `self.session` est défini avant le rejeu ; le micro
  peut therefore envoyer de l'audio pendant la fenêtre de rejeu, sur le
  pattern déconseillé (clientContent en attente + realtimeInput).
* **Test** : lecture du code (fenêtre réelle entre `__aenter__()` et
  `_replay_context()`).
* **Résultat** : fenêtre confirmée (le callback micro ne vérifie que
  `session is not None`).
* **Décision** : porte `_session_ready` — l'audio n'est envoyé qu'une fois la
  session prête (seed parti ou reprise confirmée).

## E6 — Ordre des messages sur le câble

* **Hypothèse** : avec la correction, dans chaque session reseedée, le seed
  doit précéder le premier `realtime_input`.
* **Test** : ajouté à la suite de tests (assertion sur `server.wire`).
* **Résultat** : validé après correction (E8).
* **Décision** : assertion permanente anti-régression.

## E7 — Compression de fenêtre de contexte

* **Hypothèse (BUG 5)** : sans compression, une session audio est terminée à
  15 min (documentation officielle) ; le contexte serveur disparaît, puis E3
  empêche tout recouvrement.
* **Test** : vérification de la configuration envoyée (absence totale de
  `contextWindowCompression` en v1.7.0).
* **Résultat** : confirmé — aucun `context_window_compression` en v1.7.0.
* **Décision** : activer `sliding_window` par défaut (kill-switch
  `JARVIS_LIVE_COMPRESSION=0`), conforme à la recommandation officielle.

## E8 — Nouveau protocole de rejeu (« commit »)

* **Hypothèse** : un seed qui se termine par un tour user et clôturé par
  `turn_complete=True` devient un historique committé ; sur 2.x le modèle
  produit une brève inférence de reprise (comportement pipecat documenté) et
  **tous les tours audio suivants rappellent l'historique**.
* **Modification** : `_seed_context()` (turn user final + `turn_complete=True`)
  + `historyConfig.initial_history_in_client_content=True` (protocole 3.x :
  commit silencieux sans appel modèle).
* **Résultat** : **PASS** sur tous les scénarios F/G/H (voir rapport final) ;
  le récapitulatif post-reconnexion est parlé une fois, puis le rappel est
  immédiat.
* **Décision** : retenue (architecture hybride : reprise de session en
  primaire, seed committé en repli).

## E9 — Repli sur handle refusé

* **Hypothèse** : détecter l'erreur 1007/« Invalid session handle » à la
  connexion, effacer le handle, reconnecter en session neuve + seed.
* **Résultat** : **PASS** — le scénario G passe d'une boucle infinie à un
  rétablissement en une seule reconnexion.
* **Décision** : retenue.

## E10 — Variante « pending + clôture différée » (rejetée)

* **Hypothèse** : seed `turn_complete=False` puis clôture vide
  (`send_client_content(turn_complete=True)`) au premier silence utilisateur,
  à la manière du démarrage pipecat.
* **Analyse** : sur 2.x, le premier tour audio après le seed reste « naïf »
  (pipecat le documente : « *the first user utterance after this kind of seed
  still produces one "naive" response* ») ; inacceptable pour une reconnexion
  en milieu de conversation (l'utilisateur vient justement de perdre le fil).
* **Décision** : rejetée pour le repli de reconnexion ; le mode reste
  accessible via `JARVIS_LIVE_SEED_MODE=pending` pour expérimentation.

## E11 — Variante « tout en texte via send_realtime_input(text=...) » (rejetée)

* **Hypothèse** : envoyer l'historique comme texte temps réel pour éviter le
  canal clientContent.
* **Analyse** : un historique multi-rôles ne peut pas être représenté par du
  texte user simple sans dénaturer les rôles ; pipecat réserve ce canal au
  texte utilisateur courant. Combiné au fait que la reprise de session couvre
  déjà le cas nominal, aucun bénéfice.
* **Décision** : rejetée.

## E12 — Variante « réinjection complète à chaque tour » (rejetée)

* **Hypothèse** : garantir le contexte en réinjectant tout l'historique avant
  chaque tour.
* **Analyse** : interdit par la mission (§10), coûteux, et masque le vrai
  problème (le serveur Live maintient déjà l'état dans une session).
* **Décision** : rejetée.

## E13 — Changement de voix et contexte

* **Hypothèse (BUG 6)** : la reprise de session conserve la configuration
  d'origine (voix comprise) ; garder le handle au changement de voix peut
  empêcher l'application de la nouvelle voix ET rend le contexte dépendant du
  serveur.
* **Modification** : le changement de voix abandonne le handle → session neuve
  avec la nouvelle voix + seed committé.
* **Résultat** : **PASS** — le contexte survit au changement de voix ET la
  nouvelle session porte la nouvelle configuration.
* **Décision** : retenue.

## E14 — GoAway

* **Hypothèse (BUG 7)** : sans gestion, la coupure « ABORTED » passe par le
  chemin d'erreur (traceback + 5 s d'attente).
* **Modification** : `go_away` → reconnexion silencieuse immédiate
  (`reconnect_requested`), le handle est conservé (la session reste
  reprenable, c'est le but du message).
* **Résultat** : **PASS** (test dédié).
* **Décision** : retenue.

## E15 — Limites locales du contexte

* **Hypothèse** : `max_turns=12` tronque une conversation de 20 tours avant
  le début ; le seed ne peut alors plus restituer une information du début.
* **Modification** : défauts portés à 20 tours / 4096 tokens (bornes env
  inchangées), trimming par tours entiers conservé (déjà sans résultat
  d'outil orphelin : user + outils partagent le même numéro de tour).
* **Résultat** : **PASS** — le scénario « historique long » (20 tours, question
  sur le tour 2) réussit après reconnexion.
* **Décision** : retenue.

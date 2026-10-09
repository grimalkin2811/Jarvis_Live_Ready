# Changelog

## 1.8.0 — Tâches d’arrière-plan asynchrones

- Task Manager central, thread et boucle asyncio indépendants de Gemini Live.
- Découverte dynamique d’un modèle Gemini 3 Live listé puis validé par un tour Bidi complet AUDIO + transcription pour Router/simple/medium; tâches complexes Gemini 3.8.
- Client GenAI et primitives asyncio background créés sur la boucle worker dédiée, sans transfert inter-boucles.
- États, progression, résultats, chemins complets de fichiers, non-lus, annulation, timeouts et persistance.
- Retry borné 429/503; une unique reprise avec jitter pour une 1011 Live explicitement `Resource has been exhausted`.
- Classification runtime/harness unifiée : quota explicite en 1011 reconnu comme `EXTERNAL_QUOTA`, trace Live et message fournisseur conservés.
- Validation de concurrence avec quota local isolé et classification fournisseur/local/code explicite.
- Protection du quota 3.8 (RPM/RPD/TPM/concurrence), comptée à chaque appel SDK réellement tenté.
- Six outils conversationnels, dialogue Qt, compteur radial et notifications silencieuses.
- Tests de routage, concurrence, non-blocage, quota, erreurs, fichiers et consultation.

Le projet suit le [versionnage sémantique](https://semver.org/lang/fr/) :
`MAJOR.MINOR.PATCH`. Une **MINOR** ajoute des fonctionnalités compatibles avec
l'existant (comme ici), une **PATCH** corrige un bug, une **MAJOR** casse la
compatibilité.

Les notes détaillées de chaque version sont publiées dans les
[GitHub Releases](https://github.com/grimalkin2811/Jarvis_Live_Ready/releases)
et résumées ci-dessous.

## [Non publié] — correctif S6 (mémoire après reconnexion), en attente de validation réelle

**Ne pas fusionner/publier avant confirmation.** Voir
`docs/RAPPORT_RECONNEXION_PAR_TOUR_v1.7.4.md` §10 pour l'investigation
complète, la cause racine et le protocole avant/après.

### Corrigé (cause racine architecturale, mission dédiée S6) — `GeminiLive._receive_one_turn_cycle`/`_can_send_now`

**Cause racine identifiée par preuve directe sur trace réelle (§13.1 du
rapport), distincte des pistes déjà écartées (compression de contexte,
fuite de marqueur `[appel outil]`, limite de relances) :** pendant
`EMPTY_GENERATION_RETRY`, `GeminiLive` mettait `self.speaking = False`
juste avant de ré-envoyer le texte déjà transcrit via un second
`send_client_content` dans la MÊME session. Comme `can_send()`/
`_can_send_now()` n'observent que `self.speaking` (et `_session_ready`/
`tool_active`) pour décider si le pont micro peut transmettre de l'audio
temps réel, cela rouvrait exactement la porte censée rester fermée pendant
une génération en cours. Le pont micro tourne en continu sur le thread
audio (VAD serveur oblige) : la trace réelle montre systématiquement des
`AUDIO_SENT_TO_GEMINI speaking=False` intercalés ENTRE chaque
`EMPTY_GENERATION_RETRY` et le `model_turn` (toujours vide) suivant — du
PCM micro réel (bruit ambiant au minimum) atteignait donc le serveur, sur
la session en train de traiter la relance, EN MÊME TEMPS qu'un
`clientContent` texte lui demandant de régénérer une réponse pour le tour
précédent. Mélanger une clôture de tour texte avec de l'audio temps réel
qui arrive pendant que le serveur doit encore y répondre est exactement le
genre d'ambiguïté de tour qui peut lui faire clore la génération suivante
sans contenu — une relance censée CORRIGER une génération vide pouvait
ainsi elle-même entretenir le problème, expliquant l'épuisement répété des
relances observé en validation réelle (g2-t8 : 3 tentatives identiques,
toutes vides).

**Correctif :** nouveau drapeau d'instance `GeminiLive._regeneration_pending`,
DÉLIBÉRÉMENT distinct de `self.speaking` (le réutiliser aurait fermé
`can_send()` correctement mais aurait supprimé à tort la notification
`on_speaking`/`TTS_START` si la tentative suivante est la première à
recevoir un `model_turn` exploitable). Posé à `True` dès qu'une relance de
génération vide part sur le câble, lu par `_can_send_now()` (nouvelle
raison de refus `"relance de generation en cours"`, tracée comme
`AUDIO_SEND_DROPPED_STALE` au même titre que `"jarvis parle"`/`"outil en
cours"` si le pont micro tente malgré tout un envoi), et remis à `False`
uniquement au dénouement réel du tour (réponse obtenue, relances épuisées,
échec d'envoi de la relance, ou fermeture de session). Un barge-in RÉEL
(`interrupt_active()`) garde la priorité, exactement comme pour `speaking`
— l'utilisateur peut toujours couper court. Nouvel évènement de trace
`EMPTY_GENERATION_RETRY_MIC_HELD` à chaque relance, pour une observation
directe sur une future trace réelle.

**Ce que ce correctif NE prétend PAS :** il supprime une cause
d'aggravation CLIENT, prouvée sur trace réelle, mais ne garantit pas à lui
seul l'éradication totale du bug serveur Gemini sous-jacent
(`googleapis/python-genai#2117`, confirmé par Google, ~40 rapports
indépendants) — `EMPTY_GENERATION_RETRY` reste nécessaire comme filet de
sécurité. Validation automatisée complète (harnais factices, voir
`tests/test_empty_generation_retry_mic_gate.py` et
`tests/test_s6_reconnection_compression_integration.py`) ; validation
contre l'API Gemini réelle non exécutable dans cet environnement (pas de
clé API/matériel audio) — à confirmer via
`python scripts/validate_reconnect_v174_real.py` sur un poste Windows avec
une vraie clé `GEMINI_API_KEY`.

### Corrigé

- **`GeminiLive`** : après une reconnexion sans session resumption valide,
  le rejeu local du contexte (`_seed_context`) ouvrait la porte micro dès
  l'écriture réseau du rejeu (`context_seeded=True`), sans jamais vérifier
  que le serveur avait réellement fini de le traiter. Sur les modèles audio
  « 2.x », ce tour de rejeu déclenche côté serveur une courte reprise orale
  (son propre tour, son propre `turn_complete`) : si le tour utilisateur
  suivant arrivait pendant que le serveur générait encore cette reprise, il
  pouvait recevoir une réponse vide/hors-sujet — symptôme observé en
  validation réelle (scénario S6 : « Quel est mon prénom ? » -> réponse
  vide). Nouveau flag `context_seed_confirmed` et méthode
  `_await_seed_commit()` : la porte micro n'est désormais ouverte qu'après
  avoir observé la confirmation serveur du rejeu (bornée par
  `SEED_COMMIT_TIMEOUT_SECONDS`, défaut 5 s, override
  `JARVIS_LIVE_SEED_COMMIT_TIMEOUT`) ; sur un modèle à commit silencieux
  (3.x + `historyConfig`), ce délai expire normalement sans bloquer le
  micro. `SESSION_READY` journalise désormais `context_seeded` et
  `context_seed_confirmed` séparément.
- **`scripts/validate_reconnect_v174_real.py`** : la synthèse TTS locale via `pyttsx3`/SAPI5 est désormais exécutée dans un processus enfant avec un timeout dur de 30 s par prompt. Un blocage de `runAndWait()` ne peut plus figer le harness ; le fichier WAV et le PCM sont vérifiés avant de poursuivre, puis le fallback Gemini est utilisé si le backend local échoue. Cette correction reste confinée au harnais de validation.
- **`scripts/validate_reconnect_v174_real.py`** : le scénario S3 (trois
  tours consécutifs) utilisait un délai fixe de 2 s entre les tours, qui
  pouvait expirer (`TimeoutError`, « NON TESTABLE ») alors que Jarvis
  jouait encore sa réponse précédente ou n'avait pas encore rouvert sa
  porte micro — sans rapport avec un bug de contexte/session. Remplacé par
  une attente explicite de disponibilité réelle (`can_send()` et
  `not speaking`). N'affecte que le harnais de validation, pas le produit.

### Ajouté (tests)

- `tests/test_session_lifecycle.py` : 6 nouveaux tests de non-régression
  (items C, E, G de l'audit v1.7.5) prouvant que la porte micro bloque
  jusqu'à l'ack serveur réel du rejeu (et seulement jusque-là), qu'elle
  s'ouvre en mode dégradé après un délai borné sans ack, qu'aucune attente
  n'a lieu sans rejeu (historique vide ou session reprise), et que deux
  reconnexions réelles successives ne dupliquent ni ne perdent le contexte
  (la seconde reprend via handle au lieu de rejouer une seconde fois).
- `tests/test_live_context_harness.py` : deux tests existants renforcés
  pour lire l'état post-rejeu seulement après la fin réelle de l'attente de
  confirmation.

### Corrigé (harnais de validation réelle — S6 toujours FAIL en conditions
réelles après le correctif ci-dessus ; deux bugs du harnais, pas du
produit, en étaient la cause — voir §11 du rapport)

- **Fuite de variable d'environnement de test vers un script réel** :
  `tests/__init__.py` positionnait `JARVIS_LIVE_SEED_COMMIT_TIMEOUT=1.5`
  (marge réservée aux tests contre le faux serveur). Comme ce module
  s'exécute dès qu'on importe quoi que ce soit depuis le paquet `tests`,
  `scripts/validate_reconnect_v174_real.py` (qui fait
  `from tests.real_gemini_harness import ...`) héritait silencieusement de
  ce délai de 1,5 s au lieu du défaut produit de 5 s — confirmé dans une
  trace réelle (`SEED COMMIT TIMEOUT ... aucune confirmation serveur sous
  1.5s`). Déplacé vers un nouveau `tests/conftest.py` (chargé uniquement
  par pytest, jamais par un `import` Python ordinaire du paquet `tests`) ;
  `tests/__init__.py` ne fait plus aucun `os.environ.setdefault` lié aux
  tests. Comportement de la suite pytest inchangé (même délai de 1,5 s sous
  pytest, vérifié par ré-exécution complète de la suite).
- **Synthèse de secours peu fiable corrompant les prompts réels** :
  quand `generateContent` (TTS dédié) échoue (`ClientError`, observé dans
  les deux runs réels), le script se rabat sur
  `synthesize_all_via_live()`, qui demandait à une session Live
  conversationnelle de « répéter exactement » chaque prompt. Dérive
  confirmée en conditions réelles : le modèle répondait parfois AU prompt
  au lieu de le répéter (« Mon prénom est Simon. » -> audio généré disant
  « D'accord, Simon. » ; « Quel est mon prénom ? » -> audio généré disant
  « Même en pleine. »), ce qui corrompait l'entrée audio du scénario S6
  AVANT même que le mécanisme de contexte/session soit sollicité — un FAIL
  de S6 dans ces conditions ne prouvait donc rien sur le produit. Corrigé :
  chaque prompt est désormais synthétisé dans sa PROPRE session Live (plus
  d'historique partagé pouvant dériver au fil des 5 prompts) et la
  transcription de sortie (`output_audio_transcription`) est comparée (
  normalisée : minuscules, sans accents/ponctuation) au texte demandé, avec
  un nouvel essai en cas de désaccord et un échec explicite (au lieu d'une
  mise en cache silencieuse d'un audio incorrect) si la synthèse ne
  reproduit jamais fidèlement le prompt. Le message d'erreur du
  `ClientError` de `generateContent` est maintenant journalisé (redacté)
  pour permettre de diagnostiquer sa cause plutôt que de masquer le repli.
  N'affecte que le harnais de validation, pas le produit.

### Changé (harnais de validation réelle — quota TTS Gemini)

- **`scripts/validate_reconnect_v174_real.py`** : le backend TTS PAR DÉFAUT
  pour synthétiser l'audio d'entrée (les 5 `PROMPTS`) est désormais une
  synthèse vocale **locale** (`pyttsx3` -> SAPI5 sur Windows, NSSS sur
  macOS, espeak sur Linux), sans appel réseau ni quota Gemini. Signalé par
  l'utilisateur : `gemini-2.5-flash-preview-tts` via `generateContent` est
  limité sur le palier disponible à **3 requêtes/minute ET 10
  requêtes/jour** — un quota épuisé en une seule exécution du script (5
  prompts), ce qui expliquait aussi en partie le repli vers la synthèse
  Live peu fiable corrigée plus haut. Le backend Gemini (TTS dédié, puis
  repli conversationnel Live) devient un secours utilisé seulement si
  aucun moteur local n'est disponible dans l'environnement — espacé de 22 s
  entre appels et avec un essai supplémentaire sur 429
  (`RESOURCE_EXHAUSTED`). Tout prompt synthétisé avec succès (quel que soit
  le backend) est mis en cache sur disque (`scripts/.tts_cache/`, ajouté au
  `.gitignore`) pour ne plus jamais reconsommer de quota une fois obtenu.
  Sélection automatique d'une voix française locale si disponible (best
  effort, continue avec la voix par défaut sinon). Nouvelles variables
  d'environnement : `JARVIS_VALIDATE_TTS_BACKEND` (`auto`/`local`/`gemini`)
  et `JARVIS_VALIDATE_TTS_NO_CACHE=1`. `pyttsx3` (+ `pywin32` sur Windows)
  ajoutés à `requirements-dev.txt` (outil de validation, pas une dépendance
  produit). N'affecte que le harnais de validation, pas le produit.

### Corrigé (S6 toujours FAIL contre l'API Gemini réelle après les deux
correctifs ci-dessus : cause racine confirmée côté serveur Google, pas
Jarvis — voir addendum « génération vide » du rapport)

- **`GeminiLive`** : même avec `context_seed_confirmed=True` (le serveur a
  bien traité le rejeu AVANT le tour suivant, cf. correctif précédent), une
  exécution réelle a montré « Quel est mon prénom ? » recevant un
  `turn_complete` sans la moindre miette de réponse (ni audio, ni
  `output_transcription`), alors que la transcription utilisateur était
  bien reçue. Ce n'est pas un défaut de construction du rejeu de contexte :
  Google confirme un bug serveur connu où le modèle de génération vocale
  native retourne occasionnellement un `turn_complete` prématuré/vide
  (issue `googleapis/python-genai#2117`), un symptôme déjà documenté côté
  client officiel (`google-gemini/live-api-web-console#117`) et déjà
  contourné architecturalement par d'autres SDK de production
  (`livekit/agents#4249`/`agents-js#1450`). `_receive_one_turn_cycle`
  détecte désormais une génération totalement vide survenant pour un VRAI
  tour utilisateur (texte effectivement transcrit — le tour de rejeu de
  contexte, qui n'a jamais de transcription utilisateur associée, n'est
  jamais concerné) et la relance automatiquement en ré-envoyant le même
  texte déjà transcrit, de façon transparente (aucun `TURN_COMPLETE`/
  `on_turn_complete` n'est émis pour la tentative avortée — seule la
  réponse finale, réussie ou en dégradé après épuisement des relances, clôt
  le tour). Bornée par la nouvelle constante `EMPTY_GENERATION_MAX_RETRIES`
  (défaut 2, override `JARVIS_LIVE_EMPTY_GENERATION_RETRIES`, `0` désactive
  la relance) ; une relance abandonnée (interruption locale entre-temps,
  erreur réseau sur le ré-envoi) referme le tour normalement, sans jamais
  bloquer ni planter. Chaque tentative est tracée séparément
  (`EMPTY_GENERATION_RETRY`) pour rester diagnosticable en conditions
  réelles. **Il s'agit d'une atténuation d'un bug serveur probabiliste, pas
  d'une élimination garantie** : seule une nouvelle validation réelle peut
  confirmer sa portée pratique.

### Ajouté (tests)

- `tests/test_empty_generation_retry.py` (4 tests) : relance automatique
  déclenchée et bornée, aucune régression du tour de rejeu de contexte
  (jamais relancé), et aucune relance tant qu'une interruption locale est
  active.

### Corrigé (addendum — bugs A et B, nouvelle validation réelle)

**Ne pas fusionner/publier avant confirmation.** Deux bugs supplémentaires
révélés par une validation réelle post-correctif S6 ci-dessus (Windows,
vraie clé Gemini) : S6/S2 restaient instables (« soit en pass soit en
fail, ça dépend des runs »/« non testable » selon les exécutions).

- **`GeminiLive` (bug A — extinction prématurée)** : rien ne signalait à
  `AudioIO` qu'un VRAI tour utilisateur était ouvert et en cours de
  génération *avant* que Gemini produise le moindre octet de réponse.
  Si la génération prenait plus longtemps que la fenêtre de conversation
  restante, `_check_timeout()` endormait Jarvis (ou laissait expirer la
  fenêtre) pendant que la réponse arrivait encore — toute relance posée
  immédiatement après, sans mot de réveil, était alors silencieusement
  perdue. Nouveaux callbacks `on_turn_open` (déclenché une fois par VRAI
  tour, dès la première transcription utilisateur) et `on_turn_resolved`
  (déclenché inconditionnellement à la clôture de ce tour, quel que soit le
  contenu produit — pour ne jamais laisser une suspension ouverte côté
  appelant).
- **`AudioIO` (bug A, suite)** : nouvelles méthodes `note_turn_open()`/
  `note_turn_resolved()` (câblées sur les callbacks ci-dessus dans
  `src/main.py`, `src/ui.py` et les harnais de test) et `_turn_pending()`,
  désormais vérifiée par `_check_timeout()` en plus de `_voice_audible()` :
  le minuteur de conversation reste suspendu tant qu'un tour est annoncé
  ouvert sans être encore résolu, borné par la nouvelle constante
  `TURN_PENDING_MAX_GRACE_SECONDS` (45 s, filet de sécurité si
  `on_turn_resolved` n'arrivait jamais — connexion perdue sans
  notification, par exemple).
- **`GeminiLive` (bug B — tour fantôme pris pour un vrai tour)** : la
  traîne tardive d'un tour de rejeu de contexte déjà abandonné (après
  expiration de `SEED_COMMIT_TIMEOUT_SECONDS`) pouvait arriver bien après
  l'ouverture dégradée de la porte micro, sous forme d'un message
  `interrupted`/`turn_complete` totalement vide (ni transcription
  utilisateur, ni la moindre miette de contenu assistant). Rien ne
  distinguait alors ce non-évènement d'une vraie fin de tour : un
  consommateur qui attendait « le prochain tour terminé » recevait ce tour
  fantôme à la place de la vraie réponse (observé en validation réelle,
  scénario de contexte après reconnexion). Les callbacks `on_interrupted`
  et `on_turn_complete` sont désormais filtrés par `had_content` (texte
  utilisateur transcrit pour CE tour, contenu assistant produit, ou
  barge-in local en cours — pour ne jamais avaler un VRAI « stop »
  utilisateur qui n'aurait eu le temps de couper aucun octet audio) :
  un tour totalement vide clôt toujours proprement le contexte conversationnel
  en interne (`_finish_turn()` reste inconditionnel), mais ne se fait plus
  jamais passer pour un échange réel auprès de l'appelant. Nouveaux
  évènements de trace `INTERRUPTION_CALLBACK_SUPPRESSED_EMPTY` et
  `TURN_COMPLETE_CALLBACK_SUPPRESSED_EMPTY` pour rester diagnosticable.
  **Il s'agit de deux correctifs timing/race-dépendants** : seule une
  nouvelle validation réelle (scénarios S2/S6 de
  `scripts/validate_reconnect_v174_real.py`) peut confirmer l'élimination
  effective des deux symptômes.

#### Ajouté (tests)

- `tests/test_turn_callbacks.py` (4 tests) : `on_turn_open`/
  `on_turn_resolved` se déclenchent en paire pour un vrai tour répondu ;
  un tour totalement vide (rejeu de contexte ou traîne tardive
  `interrupted`+`turn_complete`) ne déclenche ni `on_turn_open`,
  ni `on_interrupted`, ni `on_turn_complete` ; le VRAI tour suivant reste
  signalé normalement après une traîne fantôme.
- `tests/test_audio_turn_pending.py` (4 tests) : `_check_timeout()` ne
  rendort plus Jarvis tant qu'un tour est annoncé ouvert sans réponse ;
  reprise normale du minuteur une fois le tour résolu ; filet de sécurité
  `TURN_PENDING_MAX_GRACE_SECONDS` ; non-régression du mode écoute
  continue.
- `tests/test_interruption.py`/`tests/test_empty_generation_retry.py` :
  mis à jour pour refléter le nouveau filtre `had_content` (un barge-in
  local reste signalé même sans octet audio joué ; un tour de rejeu de
  contexte totalement vide ne déclenche plus `on_turn_complete`).

### Investigation (addendum — S6 reste FAIL malgré EMPTY_GENERATION_RETRY)

**Ne pas fusionner/publier avant confirmation. S6 n'est PAS corrigé.** Un
nouveau run réel (après les correctifs bugs A/B ci-dessus) confirme S2 =
PASS, mais S6 échoue toujours : `EMPTY_GENERATION_RETRY` se déclenche
2 fois, les 2 relances produisent encore une génération vide, puis la
tentative finale (épuisée) obtient bien un `TTS_START` mais sans
`GEMINI_ASSISTANT_TRANSCRIPT` ni audio. Investigation trace-first complète
dans `docs/RAPPORT_RECONNEXION_PAR_TOUR_v1.7.4.md` §11 : la cause est un
bug serveur Gemini documenté et toujours non résolu
(`googleapis/python-genai#2117`, « Premature turnComplete », ~40
confirmations indépendantes), **aggravé par la taille du contexte après
rejeu et par `context_window_compression`** (facteurs explicitement cités
par Google/la communauté) — pas par une confusion de `turn_id`/
`session_generation` ni par un bug de bookkeeping des relances (les deux
sont vérifiés sains par les nouveaux tests ci-dessous). Augmenter
`EMPTY_GENERATION_MAX_RETRIES` ne réglerait rien : chaque relance
supplémentaire a statistiquement la même probabilité d'échouer tant que le
contexte reste dans cet état aggravant. Aucun changement de comportement
par défaut n'a été appliqué : le seul levier identifié sous notre contrôle
(`JARVIS_LIVE_COMPRESSION=0`, kill-switch déjà existant mais jamais testé)
nécessite une validation réelle avant toute décision, car il a un coût
propre (protection contre la coupure de session Live à ~15 min).

#### Ajouté (tests)

- `tests/test_s6_persistent_empty_generation.py` (2 tests) : rejoue la
  signature exacte du run réel (3 tentatives vides consécutives, la
  dernière avec un `model_turn` creux déclenchant `TTS_START`) et prouve
  que le fallthrough actuel est le comportement attendu, pas un bug de
  bookkeeping ; confirme l'identité stable de `turn_id`/`session_generation`
  sur les 3 tentatives.
- `tests/test_live_context_harness.py` : nouveau test confirmant que le
  kill-switch `JARVIS_LIVE_COMPRESSION=0` retire bien
  `context_window_compression` de la configuration envoyée au serveur
  (jamais vérifié jusqu'ici), sans affecter les autres protections.

### Investigation (addendum 2 — `JARVIS_LIVE_COMPRESSION=0` ne corrige PAS
S6 ; cause encore non prouvée, instrumentation ajoutée)

**Ne pas fusionner/publier avant confirmation. S6 n'est toujours PAS
corrigé.** Un nouveau run réel avec `JARVIS_LIVE_COMPRESSION=0` confirme
S1/S2/S3/S4/S5 = PASS mais **S6 échoue encore, avec exactement la même
signature** que sans le kill-switch (`EMPTY_GENERATION_RETRY` 1/2 puis
2/2, 3ᵉ tentative avec `TTS_START` + `TURN_COMPLETE had_content=True` mais
toujours aucun `GEMINI_ASSISTANT_TRANSCRIPT`). Ceci démontre que
`context_window_compression` n'est **pas** la cause (unique ou
suffisante) : **aucun changement de comportement par défaut sur ce point
n'est fait** (le kill-switch reste disponible mais désactivé par défaut).

Le fichier de trace réel annoncé pour cette investigation n'a en pratique
jamais été transmis à l'agent (recherché sur l'ensemble du système de
fichiers, absent). En l'absence de cette preuve, et conformément à la
consigne explicite de ne PAS implémenter de correctif/contournement
spéculatif tant que la cause n'est pas démontrée, seule de
l'instrumentation en lecture seule a été ajoutée (aucun comportement
produit modifié — confirmé par la suite de 98 tests ciblés, tous verts) :

- **`GeminiLive._live_diagnostics()`** (nouveau) : lit quatre champs
  officiels du SDK `google-genai` jamais exploités jusqu'ici —
  `turn_complete_reason` (énumération incluant notamment
  `RESPONSE_REJECTED`, `NEED_MORE_INPUT`, `MALFORMED_FUNCTION_CALL`,
  `MAX_REGENERATION_REACHED`), `generation_complete`,
  `interaction_status` (`IN_PROGRESS`/`REQUIRES_ACTION`/`IDLE`) et
  `waiting_for_input`. Si Gemini indique explicitement pourquoi une
  génération n'a rien produit, ces champs sont le seul moyen actuel de le
  voir. Nouvel évènement de trace `LIVE_DIAGNOSTIC_FIELDS` (capture ces
  champs même sur un message isolé, sans `model_turn` ni `turn_complete` —
  comportement documenté du SDK pour `generation_complete`).
- **`MODEL_TURN_RECEIVED`** (nouveau) : trace `parts_count`,
  `has_inline_audio` et `has_text_part` pour chaque `model_turn` reçu,
  avant toute logique d'interruption/contenu — teste l'hypothèse que
  Gemini enverrait un `part.text` actuellement ignoré (seul
  `part.inline_data` est consommé).
- **`TURN_COMPLETE`** : le booléen agrégé `had_content` (valeur et
  logique en aval strictement inchangées) est désormais accompagné de ses
  trois composantes explicites `has_user_text`, `has_model_content`,
  `has_local_interrupt` — pour qu'une lecture future de la trace ne
  suppose jamais que `had_content=True` prouve qu'une réponse assistant
  exploitable a été reçue (seul `has_model_content=True` le prouve).
- **`INTERRUPTION`** : enrichie des mêmes champs `live_diag`.

Observation manquante, explicitement : il faut un nouveau run réel
reproduisant S6 **avec cette instrumentation** (ou le fichier de trace
réel déjà évoqué mais jamais reçu) pour savoir si
`turn_complete_reason`/`interaction_status`/`waiting_for_input` se
remplissent pendant les tentatives vides de S6, et si `has_user_text`
(et non `has_model_content`) est bien ce qui rend `had_content=True`
dans ce cas précis.

#### Ajouté (tests)

- Aucun nouveau test : changement purement additif sur la forme des
  évènements de trace, vérifié par la suite existante (notamment
  `tests/test_s6_persistent_empty_generation.py`,
  `tests/test_turn_callbacks.py`, `tests/test_interruption.py`,
  `tests/test_live_context_harness.py`) sans aucune modification.

## [1.7.5] — 2026-10-01

**Validation approfondie du correctif v1.7.4 + nouvelle instrumentation de
cycle de vie.** Poursuite de la mission v1.7.4 : audit complet des points
d'entrée de reconnexion, régressions supplémentaires contre le faux serveur
(preuve que le bug est RÉEL et que les tests le détectent — pas un artefact
du mock), et mise en place de l'infrastructure de validation contre l'API
Gemini Live réelle. Rapport complet :
`docs/RAPPORT_RECONNEXION_PAR_TOUR_v1.7.4.md` (section de suivi v1.7.5).

### Ajouté

- **`GeminiLive`** : nouvel évènement de trace `SESSION_READY`, émis
  exactement quand une session (neuve ou reprise) devient autorisée à
  recevoir de l'audio (`_session_ready = True`, après la fin du rejeu de
  contexte). Permet de prouver, trace à l'appui, qu'aucun bloc audio
  n'atteint jamais une session avant la fin de son initialisation —
  purement additif, aucun comportement existant modifié.
- **`tests/real_gemini_harness.py`** : harness d'intégration qui exécute le
  VRAI pipeline Jarvis (`AudioIO` + pont micro + `GeminiLive`, boucle
  `connect()`/`receive_loop()` identique à `src/main.py`) contre l'API
  Gemini Live RÉELLE, alimenté par de la parole humaine synthétisée (TTS) —
  pas une tonalité factice — rejouée dans une fausse carte son (mêmes
  threads temps réel qu'`EchoLab`).
- **`scripts/validate_reconnect_v174_real.py`** : script de validation
  réelle ciblé sur le bug « reconnexion-par-tour » (scénarios S1 à S6 de
  l'audit : tour unique + silence, deux tours rapides, trois tours,
  expiration de fenêtre, reconnexion réseau réelle, mémoire contextuelle
  réelle). Clé lue uniquement depuis `GEMINI_API_KEY`/`GOOGLE_API_KEY`,
  jamais journalisée.
- **`.github/workflows/validate-gemini-live.yml`** : nouvelle étape qui
  exécute ce script en CI (si le secret `GEMINI_API_KEY` est configuré) et
  publie son verdict en commentaire de PR, en plus de la validation v1.7.1
  existante.
- **`tests/test_session_lifecycle.py`** : quatre tests supplémentaires —
  une vraie coupure réseau déclenche bien une reconnexion jamais attribuée
  à `turn_complete` ; aucun audio n'atteint une session neuve avant
  `SESSION_READY` ; le contexte local survit à une reconnexion réelle et
  est effectivement rejoué sur le câble ; et un test de contrôle qui
  réintroduit le comportement PRÉ-v1.7.4 pour prouver que le bug est réel
  (pas un artefact d'un mock aligné sur l'implémentation).

## [1.7.4] — 2026-09-30

**Correctif critique : reconnexion après chaque tour normal (« Je vous
écoute » périodique).** Après une interaction normale (réveil → phrase →
réponse), et SANS aucune nouvelle parole, Jarvis répétait « Je vous
écoute. »/« Je suis prêt... » à intervalles réguliers pendant (et au-delà
de) la fenêtre de suivi de 8 secondes, avec une tempête de `SESSION
CREATED` dans le journal. Cause racine démontrée et corroborée par Google
(issue googleapis/python-genai#1224, résolue) : `session.receive()` du SDK
Gemini Live se termine NATURELLEMENT à la fin de CHAQUE tour — ce n'est pas
un signal que la connexion est morte. `src/main.py`/`src/ui.py`
traitaient ce retour normal comme la fin de la session et rouvraient un
WebSocket neuf après CHAQUE tour, ce qui déclenchait à tort le rejeu de
contexte (`GeminiLive._seed_context`) : celui-ci clôt le rejeu par un tour
« user » synthétique, provoquant une brève réponse du modèle persistée
comme message assistant orphelin, qui réarmait elle-même la fenêtre de 8 s
— boucle auto-entretenue. Rapport complet :
`docs/RAPPORT_RECONNEXION_PAR_TOUR_v1.7.4.md`.

### Corrigé

- **`GeminiLive._receive_loop`** boucle maintenant en interne sur LA MÊME
  session (`session.receive()` rappelé pour chaque tour suivant) tant
  qu'aucune vraie raison de reconnecter n'est apparue (GoAway, erreur,
  reset explicite du contexte, changement de voix). `src/main.py`/
  `src/ui.py` ne changent pas : leur reconnexion ne se déclenche plus qu'aux
  VRAIES fins de session.
- **Instrumentation** : `_reconnect_reason` distingue désormais dans les
  traces une reconnexion volontaire (`context_reset`, `voice_change`) d'une
  reconnexion technique (`goaway`, `error`) ou du démarrage (`startup`) ;
  toute reconnexion non attribuée à l'une de ces causes connues
  (`unexpected_after_normal_turn`) signalerait une régression. Un nouvel
  événement `SESSION_REUSED_NEXT_TURN` prouve qu'un tour suivant a été reçu
  sur la session déjà ouverte, sans reconnexion.
- **Fidélité des harnais de test** (`tests/live_harness.py` et plusieurs
  faux serveurs locaux) : `receive()` se termine désormais après chaque
  tour comme le SDK réel, au lieu de tourner indéfiniment jusqu'à `close()`
  — cette différence masquait complètement la classe de bug corrigée ici,
  malgré une suite de tests déjà verte.
- Deux nouveaux tests de régression (`tests/test_session_lifecycle.py`)
  reproduisent le scénario exact rapporté (réveil → phrase → réponse →
  silence → fenêtre de 8 s) et vérifient qu'un VRAI enchaînement reste
  traité normalement, sur la même session.

## [1.7.3] — 2026-09-29

**Correctif critique : double cycle superposé (« Dis-moi tout » en écho).**
Après v1.7.2, une seule phrase pouvait déclencher DEUX tours logiques en
parallèle : la réponse correcte ET une réponse générique (« Dis-moi tout »)
superposée. Cause racine démontrée par reproduction déterministe et
corroborée par l'historique Git (commit `1fd75ff`, qui avait déjà observé
la même course sur CI Windows et l'avait tolérée au lieu de la corriger) :
le pont micro (`mic()`) vérifie `can_send()` sur le THREAD AUDIO puis
planifie `send_audio()` via `run_coroutine_threadsafe` SANS jamais la
revérifier. Si la boucle asyncio met du temps à exécuter cette coroutine
(callback de lecture audio synchrone, traitement d'un message serveur),
Gemini peut avoir commencé à répondre entre-temps — le bloc, capturé
alors que c'était encore permis, est quand même envoyé et ouvre un second
tour fantôme dans la session déjà en train de répondre. Rapport complet :
`docs/RAPPORT_DOUBLE_CYCLE_v1.7.3.md`.

### Corrigé

- **Revalidation à l'exécution** (`GeminiLive.send_audio`) : la décision
  « puis-je envoyer ? » n'est plus figée au moment de la capture — elle est
  ré-exécutée sur la boucle asyncio, juste avant l'écriture réseau, seul
  endroit où l'état ne peut pas changer sous les pieds du code.
- **`capture_generation`** : un bloc capturé pour une session qui a été
  remplacée par une reconnexion avant son exécution est abandonné, même si
  l'état local semble à nouveau cohérent.
- **`capture_turn_epoch`** (le plus fin des trois garde-fous) : un bloc
  capturé pendant un tour, mais exécuté après que ce tour a été clos, est
  abandonné même quand Jarvis est redevenu totalement idle entre-temps — un
  état indiscernable d'un vrai suivi pour un simple contrôle de `speaking`.
  `turn_epoch` n'avance qu'aux fermetures de tour réelles, ce qui lève
  l'ambiguïté sans jamais bloquer un follow-up authentique.
- **Instrumentation du cycle de tour** (`JARVIS_AUDIO_TRACE=1`, partagée
  avec celle d'`AudioIO`) : `TURN_OPEN`/`TURN_CLOSE`, `GEMINI_USER_
  TRANSCRIPT`/`GEMINI_ASSISTANT_TRANSCRIPT`, `TTS_START`, `INTERRUPTION`,
  `TURN_COMPLETE`, `AUDIO_SENT_TO_GEMINI`, `AUDIO_SEND_DROPPED_STALE`,
  `SESSION_CONNECT`/`SESSION_RESUME` — chaque bloc micro envoyé ou
  abandonné est désormais explicable après coup.

### Résiduel (documenté, non corrigé par ce correctif)

- Une fenêtre de latence réseau pure reste ouverte : de l'audio capturé
  *avant même* que le serveur n'informe le client de la fin du tour (donc
  sans qu'aucun état local n'ait encore changé) peut légitimement partir et
  être interprété comme un second tour par la VAD serveur elle-même. Voir
  « Risques restants » du rapport pour le détail et les pistes.

### Tests

- 10 nouveaux tests dédiés `tests/test_turn_race.py` (unitaires
  déterministes sur `GeminiLive.send_audio` + intégration sur le vrai
  pipeline `AudioIO`/pont micro/`GeminiLive`, Tests D/E/H du plan de
  diagnostic) : aucun n'échoue sans le correctif, tous passent avec.
- Suite `tests/test_echo_loop.py` (20 tests) et `tests/test_interruption.py`
  (25 tests) toujours vertes après le correctif (hors un test connu instable
  au chronométrage, déjà présent avant ce correctif — voir le rapport).

## [1.7.2] — 2026-09-29

**Correctif critique : fin de la boucle d'écho « Je vous écoute ».**
Après une requête, Jarvis pouvait répéter « Je vous écoute » toutes les ~2
secondes sans aucune parole humaine, la fenêtre d'écoute de 8 secondes étant
réarmée en boucle. Cause racine démontrée par harness dédié : à
`turn_complete` (fin de *génération* serveur), le micro se rouvrait alors que
la file de sortie contenait encore jusqu'à plusieurs secondes de voix de
Jarvis — le serveur entendait l'écho des haut-parleurs, sa VAD commitait un
tour « utilisateur » fantôme, le modèle répondait, et chaque `turn_complete`
réarmait la fenêtre : boucle stable. Rapport complet :
`docs/RAPPORT_BOUCLE_ECHO_v1.7.2.md`.

### Corrigé

- **Porte micro anti-écho** (`AudioIO._mic_gate_open`) : le micro n'est pas
  transmis à Gemini tant que de la voix reste à jouer (nouvelles primitives
  `output_pending_bytes/seconds`) ou que la traîne acoustique (0,25 s) n'est
  pas écoulée. La détection d'interruption locale n'est pas concernée : elle
  lit le micro brut et rouvre la porte en vidant la sortie.
- **Interruption vocale pendant la traîne de lecture** : « stop » fonctionne
  désormais tant que la voix est *audible* (génération ou lecture), plus
  seulement pendant la génération — avant, seule la fenêtre entre les deux
  laissait le micro ouvert, sans possibilité de couper la parole.
- **Fenêtre de conversation suspendue tant que la voix est audible** : le
  timeout ne compte plus pendant la traîne de lecture (le commentaire
  historique « on ne retourne en veille que lorsque la réponse est finie »
  est enfin implémenté à la lettre).
- **Comptage de file corrigé** : `_queued_bytes` était décompté deux fois par
  octet ; le plafond de 5 s d'avance audio fonctionne à nouveau.
- **Callbacks résiduels neutralisés** : `clear_output`/`extend_listening`
  sont ignorés quand Jarvis est endormi — un vieux callback ne peut plus
  rouvrir l'écoute ni armer la fenêtre.
- **Réveil anti-rebond en écoute continue** : un Jarvis rendu endormi (perte
  de connexion) n'est plus réveilli à chaque bloc de 80 ms.
- **Instrumentation pérenne** (`JARVIS_AUDIO_TRACE=1`) : chaque réarmement du
  timer de 8 s est numéroté et journalisé avec sa raison, son thread, l'état,
  l'énergie micro et l'audio restant en sortie — plus aucun réarmement
  inexpliqué.

### Tests

- Nouveau harness `tests/echo_loop_harness.py` (vraie `AudioIO` + vraie
  `GeminiLive` + carte son factice threadée + écho acoustique modélisé +
  VAD serveur simulée) et 20 tests de régression `tests/test_echo_loop.py` :
  silence 20 s → 0 redémarrage ; deux phrases ; TTS ≠ entrée ; interruption ;
  follow-up ; sans follow-up ; reconnexion pendant la lecture ; stabilité
  30/30/60 s à compteurs exacts ; performance de la porte.

## [1.7.1] — 2026-09-28

**Correctif : le contexte conversationnel survit réellement aux reconnexions.**
Jarvis comptait bien les tours, mais le modèle oubliait tout dès que la session
Live était recréée : l'historique était envoyé au serveur en `clientContent`
**jamais clôturé** (`turn_complete=False`) — un contenu en attente que les
tours audio suivants ne rappellent pas (limite documentée des modèles audio
2.x, reproduite et confirmée). « Mon prénom est Simon » → reconnexion →
« Quel est mon prénom ? » échouait. Rapport complet :
`docs/RAPPORT_FINAL_CONTEXTE_v1.7.1.md`.

### Corrigé

- **Rejeu du contexte en un `clientContent` clôturé** (`turn_complete=True`)
  se terminant par un tour utilisateur (tour vide si l'historique finit par
  une réponse) : l'historique devient **committé** et les tours audio suivants
  le rappellent. La reprise de session par handle reste le chemin primaire
  (aucun rejeu inutile, aucun doublon).
- **Handle de reprise expiré** (erreur 1007) : le handle est abandonné et une
  session neuve + rejeu rétablissent la conversation en une reconnexion
  (auparavant : boucle de reconnexion infinie).
- **Course audio/rejeu** : plus aucun audio n'est envoyé pendant la fenêtre de
  rejeu (porte `_session_ready`).
- **Changement de voix** : le handle est abandonné (la reprise conservait
  l'ancienne voix) ; session neuve avec la nouvelle voix + rejeu du contexte.
- **`GoAway`** : reconnexion silencieuse immédiate avec handle conservé
  (auparavant : traceback + 5 s d'attente).
- **Seed dupliqué** : un seul rejeu par session, avant tout audio, sans
  duplication des tours.

### Ajouté

- **Compression de fenêtre glissante** (`sliding_window`) par défaut : les
  sessions audio ne sont plus terminées à 15 min côté serveur.
- **`HistoryConfig.initial_history_in_client_content`** pour les modèles 3.x
  (commit silencieux du seed, sans inférence de reprise).
- **Instrumentation structurée** du pipeline (SESSION CREATED/RESUMED, CONTEXT
  REPLAY START/END, TURN START/COMPLETE…) sans jamais journaliser de contenu
  ni de secret.
- **Kill-switchs** : `JARVIS_LIVE_SEED_MODE` (`commit` par défaut, `pending`
  = ancien protocole), `JARVIS_LIVE_HISTORY_CONFIG`, `JARVIS_LIVE_COMPRESSION`.
- **33 tests sémantiques** (`tests/test_live_context_harness.py`) sur le
  chemin vocal complet avec assertions sur le câble (rappel, anaphores,
  outils, interruption, reconnexion, resumption, modes/voix, providers,
  arêtes sémantiques, protocole exact du seed re-sérialisé par le SDK),
  plus un faux serveur Live fidèle au protocole (`tests/live_harness.py`)
  et un harness vocal (`tests/voice_harness.py`).
- **Diagnostics** : `scripts/diag_conversation_context.py` (scénarios
  rejouables), `scripts/perf_context_replay.py` (coût du rejeu : ~0,07 ms
  et 6 Ko pour 20 tours), `scripts/dump_wire_protocol.py` (dump exact du
  câble sérialisé par le SDK) et `scripts/validate_real_gemini.py`
  (validation Windows sur l'API réelle, clé requise — 5 verdicts).

### Modifié

- Contexte conversationnel : **20 tours / 4096 tokens** par défaut (12/3000
  avant) — une conversation longue reste restituable après reconnexion
  (`JARVIS_CONTEXT_MAX_TURNS` / `JARVIS_CONTEXT_MAX_TOKENS` inchangés).

### Inchangé

- Desktop Mode v1.7, mémoire persistante (toujours distincte du contexte),
  providers, outils, audio, UI. Aucun test supprimé ni affaibli.

## [1.7.0] — 2026-09-27

**Desktop Mode : une vraie présence sur le bureau.** Jusqu'ici le cadre
Desktop savait faire une chose — s'allumer plus ou moins fort. « Écoute »,
« réflexion » et « réponse » n'étaient que trois intensités du même dessin, le
halo repeignait tout l'écran soixante fois par seconde, et le retour en veille
reposait sur un minuteur. Cette version reprend le sujet à la base : Jarvis est
là, visible d'un coup d'œil, et jamais dans le chemin.

### Ajouté

- **Machine d'états visuelle explicite** (`UI/desktop/state.py`, sans Qt) :
  `hidden`, `loading`, `listening`, `thinking`, `tool_use`, `speaking`,
  `follow_up`, `interrupted`, `error`. Chaque état a **sa couleur ET sa
  forme** : la lumière monte du bas quand Jarvis écoute, deux accents
  circulent le long du périmètre quand il réfléchit, des segments s'allument
  en séquence quand il exécute un outil, la pulsation part des côtés quand il
  répond, et une ligne de compte à rebours montre la fenêtre d'écoute se
  refermer. On reconnaît l'état sans lire.
- **Pilotage par évènements réels**, plus par supposition : `on_tool_start`,
  `on_tool_end`, `on_user_transcript`, `on_assistant_transcript` sur
  `GeminiLive`, `output_level_hook` sur `AudioIO`, et un pont thread-safe sans
  Qt (`UI/desktop/events.py`). Le minuteur de 8,4 s de la 1.5.3 n'est plus la
  source de l'état : c'est un simple filet de sécurité.
- **Widgets optionnels** : pastille d'état, transcription, réponse, nom de
  l'outil, visualiseur audio, contrôles Stop/Micro/Masquer. Tous facultatifs,
  tous positionnables, tous configurables par état.
- **Éditeur d'apparence Desktop** (`UI/desktop_appearance_dialog.py`),
  accessible depuis **Appearance → Desktop HUD** et depuis l'icône de
  notification : activation, **glisser-déposer** avec aimantation, taille,
  visibilité par état, aperçu de chaque état, force et épaisseur du halo,
  taille du texte, animations réduites, réinitialisation. Enregistrement
  immédiat, appliqué à chaud.
- **Politique de clic explicite** : « Toujours traversable », « Widgets
  interactifs uniquement » (défaut) et « Overlay interactif ». Les boutons
  cliquables vivent dans une fenêtre outil séparée, ce qui permet d'avoir des
  contrôles **sans** rendre l'écran entier interceptant.
- **Design system centralisé** (`UI/desktop/design.py`) : toutes les durées,
  courbes, opacités, épaisseurs et marges en un seul endroit.
- **Documentation** : `docs/DESKTOP_MODE.md`.
- **158 tests** supplémentaires : `tests/test_desktop_state_machine.py`,
  `test_desktop_config.py`, `test_desktop_hud.py`,
  `test_desktop_appearance_dialog.py`, `test_desktop_events.py`,
  `test_desktop_integration.py`.

### Modifié

- **Rendu du halo réécrit** : bandes pré-rendues dans des `QPixmap` mis en
  cache, repaint **limité aux quatre bords** (jamais le centre de l'écran),
  accents animés par un unique sprite. Mesuré en repaint plein écran (pire
  cas) : **18,9 ms → 1,6 ms** en 1080p, **36,9 ms → 9,8 ms** en 4K. À l'état
  `hidden`, le minuteur est arrêté : coût CPU **nul**.
- Les réglages Desktop sont rangés dans le fichier d'apparence **existant**
  (`appearance_state.json`, bloc `desktop`) : aucun second fichier de
  configuration.
- `UI/screen_halo_overlay.py` devient une **façade** : `ScreenHaloOverlay` et
  `build_presence_hook` gardent exactement leur contrat 1.6.0, et
  `src.ui.PresenceRouter` est désormais le contrôleur Desktop complet (même
  nom, même API, `_listen_hide_timer` compris).

### Non-régression

- Aucun test existant n'a été supprimé ni affaibli : les tests Desktop 1.5.3
  (`tests/test_desktop_overlay.py`) et les tests de bascule
  (`tests/test_interface_mode.py`) s'exécutent tels quels sur la nouvelle
  implémentation. Le contrat de position du menu Appearance est préservé
  (« Blob Visible » reste le dernier item ; « Desktop HUD » s'insère avant).
- Un `appearance_state.json` écrit par la 1.6.0 continue de fonctionner : le
  bloc `desktop` absent applique les valeurs par défaut sans toucher au thème
  ni à l'opacité des fonds d'items.
- Blob ↔ Desktop : toujours à chaud, sans redémarrage, sans perte de contexte
  conversationnel, de mémoire ni de réglages.
- Le mode jeu supprime toujours intégralement l'affichage.

### Journalisation

- Logger `jarvis.desktop` : états, écran cible (nom, taille, DPI), politique
  d'interaction, configuration. Les transcriptions et les réponses ne sont
  journalisées **que par leur taille** — jamais leur contenu, ce qu'un test
  vérifie.

## [1.6.0] — 2026-09-27

**Contexte conversationnel multi-tour.** Jusqu'ici chaque demande était
traitée isolément : Jarvis n'avait aucune représentation locale de ce qui
venait d'être dit et perdait tout dès qu'une session Live était recréée.
Cette version donne à Jarvis SA propre mémoire de conversation, indépendante
du fournisseur LLM.

### Ajouté

- **`src/conversation.py`** : composant central `ConversationContext`
  (messages structurés par rôle, tours ordonnés, limitation de taille,
  réinitialisation, instrumentation). Le reste du code ne manipule jamais la
  liste interne : il passe par `add_user_message`, `add_assistant_message`,
  `add_tool_interaction`, `get_messages`, `snapshot`…
- **Suivi réel des échanges** : les transcriptions d'entrée et de sortie de
  l'API Gemini Live sont désormais activées (`input_audio_transcription`,
  `output_audio_transcription`) et alimentent le contexte dans l'ordre
  `utilisateur → outils → assistant`. « Et sa population ? », « Lance le
  deuxième », « Non, l'autre » fonctionnent enfin.
- **Adaptateurs par fournisseur** : `to_gemini_contents()` (rôles `user` /
  `model`, `parts`) et `to_ollama_messages()` (rôles `system` / `user` /
  `assistant` / `tool`, `tool_calls`). La conversation est la même des deux
  côtés ; changer de fournisseur ne la perd pas.
- **Rejeu du contexte** dans une session Live neuve (reconnexion, changement de
  voix, expiration) : la continuité ne dépend plus d'un état implicite côté
  serveur. Quand le serveur reprend lui-même la session (`session_resumption`),
  aucun rejeu n'est envoyé — pas de doublon.
- **Trace compacte des outils** dans le contexte : nom, arguments résumés et
  résultat abrégé (5 éléments, 420 caractères max) — jamais le JSON complet.
- **Réinitialisation** : commande vocale « nouvelle conversation », « efface le
  contexte », « on repart de zéro »… reconnue **localement** (aucun appel LLM
  supplémentaire) par `is_new_conversation_command()`, plus les outils Gemini
  `reset_conversation` et `get_conversation_state` (117 → **119 outils**).
- **Limitation de taille** documentée et configurable :
  `JARVIS_CONTEXT_MAX_TURNS` (12), `JARVIS_CONTEXT_MAX_TOKENS` (3000),
  `JARVIS_CONTEXT_ENABLED`. Le rognage supprime des **tours entiers**, du plus
  ancien au plus récent, sans jamais descendre sous le tour courant.
- **Journalisation DEBUG** dédiée (`jarvis.conversation`) : identifiant de
  conversation, tours, nombre de messages, tokens estimés, fournisseur,
  rognage, reset — sans jamais recopier le contenu des échanges.
- **108 tests** supplémentaires : `tests/test_conversation_context.py`,
  `tests/test_conversation_providers.py`, `tests/test_conversation_pipeline.py`.

### Outillage (après publication)

- La vérification post-publication (`Verify Release`) ne code plus en dur le
  nombre d'outils attendu : il est **compté dans la source du tag vérifié**.
  Une valeur figée devenait fausse à chaque release et faisait échouer la
  vérification d'une release pourtant conforme.

### Inchangé

- **La mémoire persistante reste séparée** : le contexte conversationnel n'y
  est jamais promu automatiquement. Seule l'extraction explicite existante
  (« souviens-toi que… ») écrit dans `memory.db`, et un reset de conversation
  ne supprime aucun souvenir, réglage, routine ni rappel.
- Le prompt système (identité de Jarvis) reste un troisième espace distinct,
  reconstruit à chaque connexion.
- Les changements de mode (Blob ↔ Desktop, Focus, Jeu, Writing, Musique) ne
  vident pas le contexte. Un redémarrage de Jarvis, si.

## [1.5.3] — 2026-09-27

**Petit patch UX, sans refonte de l'interface.** Jarvis peut désormais passer
rapidement entre son Blob existant et son cadre Desktop existant.

### Ajouté

- **Switch Blob Mode / Desktop Mode à chaud** dans le menu radial `System`,
  dans la zone de notification et dans le launcher. Le contrôle affiche le
  mode actif et applique la transition sans redémarrer le backend vocal.
- **Commande vocale** « Passe en mode Desktop » / « Passe en mode Blob », via
  le mécanisme d'outils Gemini existant (`set_interface_mode`).
- **Persistance centralisée** dans le `config.json` existant (`interface_mode`,
  défaut `blob`) : launcher, menu et runtime lisent et écrivent la même valeur.
- Tests ciblés du défaut, des sélections, de la persistance, des transitions,
  du rendu du contrôle, du tray et de la réutilisation des widgets Blob/halo.

### Inchangé

- Le Blob, le cadre Desktop, leurs animations, les fonds et layouts des menus,
  le Writing Mode, Deezer, la mémoire et le moteur vocal conservent leurs
  mécanismes existants. Desktop Mode reste le cadre réactif autour de l'écran,
  et non une nouvelle interface complète.


## [1.5.2] — 2026-09-27

### Ajouté

- Associations locales de playlists Deezer (`nom → ID`) dans
  `deezer_playlists.json`, séparées du stockage OAuth.
- Enregistrement par URL ou ID Deezer, résolution tolérante des noms, liste et
  suppression locales, avec outils `music_playlist_save`,
  `music_playlist_import`, `music_playlist_list` et `music_playlist_remove`.
- Lancement d’une playlist personnelle enregistrée directement par deep-link,
  sans appel OAuth ni requête `/user/me/playlists`.

### Amélioré

- OAuth reste utilisé automatiquement pour découvrir/importer une playlist
  personnelle quand aucune association locale ne correspond.
- Un token invalide ne bloque pas une association locale existante ; les erreurs
  d’authentification sont retournées clairement dans les autres cas.
- Le fichier local est validé, écrit atomiquement et sauvegardé avant
  réécriture ; les données corrompues ou les IDs/URLs invalides sont ignorés ou
  refusés sans faire planter Jarvis.

### Sécurité et documentation

- Aucun token, mot de passe, cookie ou session Deezer n’est écrit dans le
  fichier d’associations. Aucun scraping ni contournement des restrictions de
  création d’applications Deezer n’est introduit.
- `README.md` et `docs/DEEZER.md` documentent le parcours sans OAuth et la
  différence entre retirer une association de Jarvis et supprimer une playlist
  Deezer.

## [1.5.1] — 2026-09-26

**Release de correction de provenance.** `v1.5.1` est la première release
construite depuis le `main` reconstruit et validé. Elle ne modifie ni ne
remplace `v1.5.0`, qui reste publiée telle quelle : `v1.5.0` demeure une
release historique, inchangée, avec ses propres tag, SHA et artefacts.

### Corrigé

- **Provenance de la release.** Les binaires de `v1.5.1` sont construits
  depuis le `main` courant, qui contient la chaîne historique complète
  1.1.2 → 1.2.0 → 1.3.0 → 1.3.1 → 1.3.2 → 1.4.x → code 1.5.0 → 1.5.1. Les
  artefacts embarquent donc réellement les fonds d'items des menus radiaux,
  la correction des chevauchements (dont le menu Voice), les corrections
  Blob/menu, le Writing Mode et l'intégration Deezer.
- **Régression UI de la release v1.5.0 — fonds d'items et menus superposés.**
  Le tag `v1.5.0` a été posé sur `68392d4`, tête de la branche Deezer
  (PR #27), branche coupée depuis `v1.1.2`. Ce commit ne contient donc
  **aucun** des travaux 1.2.0 → 1.4.2 : pas de fonds d'items dans les menus
  radiaux (1.3.1/1.3.2), pas de solveur de layout anti-chevauchement (1.3.2),
  pas de masquage du Blob, pas de Writing Mode. Les binaires publiés
  (`JarvisSetup-1.5.0.exe`, zip portable) embarquent donc une interface
  antérieure à la 1.2.0. `main` (union réalisée par la PR #28) n'a, lui,
  **jamais** perdu ces fonctionnalités : son rendu est identique au pixel
  près à celui de `v1.3.2`, aux deux items Voice ajoutés par le Writing Mode
  près. Aucun code UI n'avait donc à être restauré ; c'est la **publication**
  qui était en cause. → republier depuis `main`.

### Ajouté

- `scripts/check_release_lineage.py` — garde-fou de publication : refuse
  d'étiqueter/publier un commit qui ne contient pas toutes les versions
  déjà livrées, et vérifie la présence du contrat UI (fonds d'items +
  anti-chevauchement). Exécuté par le job `build` sur les tags. Il aurait
  bloqué la v1.5.0 (`v1.2.0` … `v1.4.1` absents de la lignée).
- `tests/test_ui_menu_regression.py` — verrouillage explicite du contrat UI :
  présence des API (échoue sur tout arbre antérieur à la 1.3.2), fond peint
  derrière chaque item des 5 menus (mesure différentielle), absence de
  highlight permanent, survol distinct et localisé, disparition des fonds à
  la fermeture, et zéro chevauchement — dont le menu Voice à 12 items.
  Les 15 tests échouent sur l'arbre publié en v1.5.0.
- `scripts/verify_release_bundle.py` et le workflow `Verify Release`
  (ajoutés sur `main` juste après le tag `v1.5.1`, et utilisés pour certifier
  ses artefacts) — vérification **post-publication** : téléchargement des
  artefacts réellement publiés, recalcul des SHA-256 et comparaison au fichier
  `.sha256`, exécution de `Jarvis.exe --smoke-test`, puis lecture de l'archive
  PYZ embarquée dans l'exécutable pour prouver la présence des modules
  (`UI.*`, `src.writing.*`, `src.music.*`), des symboles du contrat
  (`_draw_hover_background`, `item_bg_opacity`, `_solve_layout_spacings`,
  `_menu_layout_overlaps`), de la version compilée et des 112 outils Gemini.
  C'est le contrôle qui manquait à l'autre bout de la chaîne : aucune
  vérification n'ouvrait le binaire produit. Sur un bundle construit depuis
  `68392d4`, il sort en erreur (Writing Mode absent, 8 symboles de menu
  absents, 11 outils absents).

### Inchangé

- **Deezer** (`src/music/**`, outils Gemini, `docs/DEEZER.md`), **Writing
  Mode** (`src/writing/**`) et l'ensemble des fonctionnalités 1.4.x sont
  repris tels quels depuis `main` : aucune modification fonctionnelle dans
  cette version. `v1.5.1` ne contient que le correctif de provenance, les
  garde-fous et les tests.

## [1.5.0] — 2026-09-25

### Ajouté

- **Intégration musicale Deezer** — première intégration musique native de Jarvis.
  - Architecture `MusicManager` → `DeezerProvider` (`src/music/`).
  - 15 outils Gemini Live : `music_play`, `music_search`, `music_pause`,
    `music_resume`, `music_next`, `music_previous`, `music_current`,
    `music_list_playlists`, `music_play_track/artist/album/playlist`,
    `music_status`, `music_stop`, `music_disconnect`.
  - Recherche catalogue public (artistes, morceaux, albums, playlists) via
    l'API officielle `api.deezer.com` **sans authentification**.
  - Lecture par deep-link `deezer://` (app desktop) ou URL web dans le
    navigateur **par défaut** (aucun navigateur hardcodé).
  - Contrôles pause / reprise / suivant / précédent via touches multimédia
    Windows.
  - Playlists personnelles si `DEEZER_ACCESS_TOKEN` (ou fichier local
    `deezer_auth.json`) est fourni.
  - Parseur d'intentions FR pour formulations naturelles
    (`src/music/intents.py`) + instructions Gemini Live dédiées.
  - Gestion d'ambiguïté (« Joue Halo ») : demande de précision au lieu d'un
    choix arbitraire.
  - Tolérance accents / casse / transcription partielle.
  - Deezer ajouté à la liste blanche des applications.
  - Outils musique bloqués en mode focus (cohérent avec les distractions).

### Documentation

- Section README « Musique Deezer (v1.5.0) » : capacités, auth, limitations
  honnêtes, commandes vocales.
- `.env.example` : variables `DEEZER_ACCESS_TOKEN` / `JARVIS_DEEZER_TOKEN`.
- `.gitignore` : `deezer_auth.json`.
- Ce CHANGELOG.

### Limitations connues (Deezer)

- Pas de streaming audio direct via l'API tierce (retiré par Deezer pour les
  apps individuelles).
- Pas de now-playing temps réel API : Jarvis maintient un état local de la
  dernière lecture lancée.
- Création de nouvelles apps OAuth Deezer restreinte côté Deezer (2025+) :
  les playlists personnelles nécessitent un token existant valide.
- Aucune simulation : si une fonction est indisponible, Jarvis le dit
  clairement.

### Tests

- `tests/test_music_intents.py` — formulations naturelles FR.
- `tests/test_music_deezer.py` — client HTTP mocké, recherche, lecture,
  contrôles, auth, ambiguïté, erreurs réseau/timeout, MusicManager, tools.
- Aucun compte Deezer réel requis pour la CI.

### Technique

- Version `src/version.py` → **1.5.0**.
- Dépendances inchangées (stdlib `urllib` pour Deezer, pas de package tiers).

## 1.4.2 — Writing Mode : le curseur par défaut

Release volontairement ciblée : un seul changement fonctionnel, dans le
système d'écriture. Le reste de Jarvis est inchangé.

### Changement

- **Le curseur actif devient la sortie par défaut.** « Écris-moi un message
  pour prévenir mon professeur », « rédige une lettre », « fais-moi une lettre
  de motivation » : Jarvis génère le texte et l'écrit à l'emplacement du
  curseur, sans créer de fichier.
- **Un fichier `.txt` n'est créé que sur demande explicite** : « dans un
  fichier », « un fichier texte », « un .txt », « un document à générer »,
  « un fichier à enregistrer / sauvegarder », « un contenu sous forme de
  fichier », « un fichier téléchargeable ».
- **Cas ambigu** (aucun format précisé) : le curseur est privilégié, aucune
  clarification n'est demandée. Un artefact rédactionnel seul (lettre, mail,
  message, CV, rapport, paragraphe, résumé…) ne déclenche plus de fichier.
- Filet local : si le modèle appelle `create_text_file` alors que la demande
  vise clairement le curseur, l'écriture part au curseur — aucun `.txt` n'est
  créé. Inversement, une sortie explicitement demandée n'est jamais remplacée
  par l'autre.

### Inchangé

Génération du texte, insertion au curseur (`SendInput`, presse-papiers en
secours), création des `.txt` dans `user_content/` (noms sûrs, pas
d'écrasement), interrupteurs Writing → Active field / Text files, refus des
questions et hypothèses, gestion des erreurs.

## 1.4.1 — Stabilisation (voix Desktop, persistance, tray)

Release de correction : pas de nouvelle grosse fonctionnalité. Writing Mode
est inchangé.

### Corrections

- **Voix Desktop** : le mode overlay utilise la voix, le volume, le débit et
  le mode de réponse choisis dans les paramètres (même pont `LIVE` que l'orbe
  et la console), au lieu de retomber sur la voix Gemini par défaut.
- **Persistance** : les réglages vocaux et le mode de réponse sont rechargés
  au démarrage des trois modes. Transparence et always-on-top sont appliqués
  dès l'ouverture de l'orbe.
- **Tray** : Masquer / Afficher / Quitter. L'état masqué n'est pas persisté :
  un relance réaffiche l'interface. Quitter depuis le tray termine le process.
- **Launcher** : refuse une double instance de Jarvis.
- **Cadre Desktop** : apparaît à l'écoute / la réflexion / la réponse, puis
  disparaît après la fenêtre de follow-up (y compris en écoute continue).
- **Menus** : fermeture sans surbrillance résiduelle ; always-on-top ne sort
  plus du plein écran.

## 1.4.0 — Writing System

Deux modes d'écriture, indépendants, uniquement sur ordre explicite. Une réponse
conversationnelle ne déclenche jamais d'écriture, et le texte produit n'est pas
relu à voix haute.

### Ajouts

- **Insertion dans le champ actif** : le texte généré est tapé au curseur de la
  fenêtre au premier plan (navigateur, mail, éditeur, Discord, Word,
  formulaire), sans sélectionner ni remplacer le texte déjà présent. Accents,
  paragraphes et textes longs sont conservés. Sous Windows, la saisie passe par
  `SendInput` ; le presse-papiers n'est utilisé qu'en secours, puis restauré.
- **Fichiers `.txt`** : création dans `user_content/` (dossier créé s'il manque,
  relatif à l'application — jamais un chemin absolu figé). Noms courts et sûrs
  (`trous_noirs.txt`, sinon `document.txt`). Jamais d'écrasement silencieux :
  `document_1.txt`, `document_2.txt`.
- **Réglages indépendants** dans le menu Voix, persistés, activés par défaut :
  Writing → Active field, Writing → Create text files.
- Assainissement des noms de fichiers, collisions, erreurs non fatales et tests.

## 1.3.2 — Layout sans chevauchement, opacité des fonds, démarrage plus rapide

### Ajouts

- **Appearance → « Item BG Opacity »** : nouveau slider (0–100 %) qui règle
  l'opacité des **fonds des items** des menus (pas le blob, ni le texte, ni
  le halo). Appliqué à l'image suivante, cohérent sur les 5 menus (même
  `appearance_state`), persisté avec les autres réglages Appearance dans
  `appearance_state.json`. Fichier d'avant 1.3.2 sans la clé → défaut
  identique au comportement 1.3.1 (`ITEM_BG_REST`).

### Corrections

- **Layout des menus sans chevauchement (systémique)** : le placement
  historique reste la base ; un solveur mesure les libellés avec les
  métriques de police exactes du rendu (pire cas survol) et élargit
  **seulement** l'espacement fautif (colonne ou pas de rang) quand deux
  zones se chevaucheraient. Corrige notamment le menu Voice (9 chevauchements
  mesurés en 1.3.1) **sans redessiner l'UI** — valable pour toutes les
  tailles de contenu et les deux orientations (grille / ligne).
- **Masquage jamais pris pour un démarrage** : `blob_hidden` n'est plus
  sérialisé (`state_to_dict`) et est ignoré au chargement (même ancien
  fichier `blob_hidden: true` → orbe **visible**). Masquer en session
  fonctionne exactement comme avant ; masquer → fermeture → relancement =
  visible, quels que soient les chemins de fermeture (Échap, System → Quit,
  icône de notification, `aboutToQuit`).

### Performance

- **Démarrage** : imports lourds déportés hors du chemin critique
  (`asyncio`, `audio`, `gemini_live`, `memory`, `scheduler` dans la boucle
  vocale ; `screen_halo_overlay` en mode desktop seul ; `UI` exports lazy
  PEP 562 ; construction du menu Routines via `list_routine_names()` sans
  importer `src.tools`). Mesure locale (offscreen) : « Interface démarrée »
  **≈ 0,68 s → ≈ 0,17 s**. Le launcher n'est pas concerné (hors périmètre).
  La voix reste prête au même instant absolu (~0,8 s) — présence « loading »
  pendant ~130 ms de plus, aucun écran de chargement ajouté.

### Qualité

- Tests : `test_menu_layout.py` (zéro chevauchement, 5 menus × 2
  orientations), `test_item_bg_opacity.py` (présentation, application,
  persistance, périmètre), réécriture de `test_blob_visibility.py`
  (contrat visible au démarrage + chemins de fermeture), différentiel des
  fonds patché sur `state.item_bg_opacity`.
- CI : étapes `diag:` pour les deux nouveaux fichiers de tests.

## 1.3.1 — Fonds des items réellement visibles

### Correction

- **Les fonds d'items des 5 menus sont enfin visibles.** En 1.3.0, le fond
  permanent ajouté ne concernait que la pastille du nœud : le rectangle
  derrière le libellé restait réservé au survol. À l'écran, rien ne
  changeait — mesuré sur un rendu réel : opacité du fond **5/255** sans
  survol, contre **224/255** au survol.
  Désormais chaque item affiche son rectangle dès l'ouverture du menu
  (**≈ 121/255**, soit 55 % du fond de survol), dans les 5 menus et
  indépendamment du survol ; le survol ne fait plus que le renforcer.
- Nouvelle constante `ITEM_BG_REST` (0.55) : `0.0` = comportement d'origine
  (fond uniquement au survol), `1.0` = fond identique au survol.

### Qualité

- **Garde-fou des outils** : tout outil implémenté doit être déclaré à
  Gemini (`tests/test_tool_declarations.py`). Un outil absent de
  `TOOL_DECLARATIONS` n'existe pas pour le modèle — il n'est jamais appelé à
  la voix, alors que son code fonctionne et que ses tests directs passent.
- **Test des fonds en différentiel** : le menu est rendu deux fois (avec puis
  sans le fond permanent) et on compte les pixels modifiés. Mesurer une
  valeur absolue ne prouvait rien ici, le texte et la pastille étant déjà
  opaques : c'est ainsi que la 1.3.0 passait ses propres tests sans qu'aucun
  fond ne soit visible. Le test échoue sur la 1.3.0 pour les 5 menus.
- Suite complète : **532 tests + 403 subtests, 0 échec**.

## 1.3.0 — Modes configurables, commandes d'affichage, fonds des menus

Version construite **au-dessus de la 1.2.0** (masquage du Blob, écoute
post-réponse, survol thématisé : tout est conservé).

### Ajouts

- **Listes d'applications des modes jeu/focus, personnalisables sans toucher
  au code** : menu `System` → `Mode Apps` (cases du catalogue + entrées
  « sur mesure ») ou à la voix (« dans le mode jeu, ne ferme pas Opera GX »,
  « ajoute Spotify à la liste du mode focus », « réinitialise les
  applications du mode jeu »).
  - Les deux modes ont des listes **indépendantes** et persistantes
    (`mode.json` v2 : `game_apps` / `focus_apps`) ; aucune exception codée en
    dur (ex. Opera GX n'est plus « protégé » : c'est un choix d'utilisateur,
    absent des valeurs par défaut du mode jeu).
  - Catalogue générique de ~28 apps (`src/mode_apps.py`, identification par
    image de processus) + applications sur mesure.
  - Migration automatique des configs v1 ; clé corrompue → valeurs par
    défaut ; liste vide conservée (ne rien fermer) ; application non ouverte
    → pas d'erreur (fermeture best effort).
- **Commandes d'affichage explicites** : « affiche le blob » / « montre
  l'orbe », « masque le blob », « affiche le menu *X* », « ferme le menu ».
  Une commande s'exécute **dans tous les états** (mode jeu/focus actif, orbe
  configuré caché, menu fermé) : la commande passe devant la politique de
  mode et le réglage « Blob Visible » de la 1.2.0 ; le masquage utilisateur
  (réglage ou commande) survit aux transitions de mode. Affichage/masquage
  centralisés dans un pont unique thread-safe (`UI/visibility_bridge.py`) —
  pas de sources contradictoires ; le menu ouvert est nettoyé en bloc si le
  blob est masqué. « Afficher Jarvis » de la zone de notification suit le
  même chemin.
- **9 nouveaux outils Gemini** : `show_blob`, `hide_blob`, `show_menu(menu)`,
  `hide_menu`, `get_ui_state`, `list_mode_applications`,
  `set_mode_applications`, `toggle_mode_application`,
  `reset_mode_applications` (total : 95 outils).

### Comportement des menus

- À l'ouverture d'un des 5 menus, **tous les fonds d'items sont visibles
  simultanément**, indépendamment du survol (le survol conserve son
  surcroît d'accent et le rectangle thématisé de la 1.2.0) ; à la fermeture,
  au changement de menu, ou au masquage par un mode, les fonds disparaissent
  **en bloc** : aucun résidu, pour n'importe quelle séquence.

### Détails techniques

- `UI/jarvis_menu.py` : politique de visibilité unique dans `tick()`
  (réglage `blob_hidden` + commande « masque le blob » + politique de mode,
  avec surcote d'affichage réinitialisée à la transition) ;
  `_reset_menu_visuals()` appelé sur tous les chemins de masquage.
- `UI/mode_apps_dialog.py` : dialogue des applications des modes (singleton,
  signal `destroyed` branché).
- `src/tools.py` : 9 nouveaux outils (déclarations + enregistrement), tous
  autorisés en mode jeu/focus (`MODE_CONTROL_TOOLS`).
- `src/gemini_live.py` : consignes système — listes de modes
  indépendantes/configurables/persistées (ne jamais préjuger), commandes
  d'affichage toujours exécutées.
- `UI/jarvis_menu.py` : normalisation des fins de ligne (fichier CRLF/LF
  mélangé).

### Tests

- 69 nouveaux tests : catalogue et persistance des modes, isolation
  jeu/focus, migration v1→v2 et corruption, pont de visibilité, rendu
  offscreen par pixels des 5 menus, intégration blob/menu, dialogue Qt.
- Suite complète : **524 tests + 113 subtests, 0 échec**.

## 1.2.0 — Nouveautés d'ergonomie de l'orbe et de l'écoute

### Ajouts

- **Masquer / réafficher l'orbe** (menu `Appearance` → `Blob Visible`).
  Un interrupteur masque l'orbe ou le fait réapparaître. L'état est conservé
  entre les sessions (`appearance_state.json`). Masqué, la fenêtre disparaît
  mais l'assistant (wake word, Gemini Live, routines, rappels, notifications)
  continue de tourner ; l'icône de notification (« Afficher Jarvis ») le
  réaffiche.
- **Écoute post-réponse activable/désactivable** (menu `Voice` →
  `Listen After Reply`, actif par défaut).
  - *Activé* (comportement historique) : après sa réponse, Jarvis reste à
    l'écoute pendant la fenêtre de suivi ; on peut enchaîner sans redire
    « Hey Jarvis ».
  - *Désactivé* : Jarvis retourne en veille dès la fin de sa réponse ; le wake
    word « Hey Jarvis » redevient obligatoire à chaque interaction.
  - `Always Listening` reste prioritaire si les deux sont actifs, et le wake
    word n'est pas altéré.
- **Véritable effet de survol dans les menus radiaux** : survoler un item
  affiche un rectangle sombre aux coins arrondis derrière son libellé, pour
  montrer clairement l'option ciblée. La couleur du fond est dérivée du thème
  courant de l'orbe (elle suit automatiquement un changement de couleur), reste
  sombre pour garder le texte lisible, et n'ajoute aucune marge : rien ne se
  déplace au survol.

### Détails techniques

- `UI/menu_state.py` : nouveau réglage persisté `post_response_listen` +
  pont `LiveControls.get/set_post_response_listen`, synchronisé au chargement.
- `src/audio.py` : `AudioIO` accepte `post_response_provider` ; `extend_listening`
  honore le réglage via un chemin de mise en veille unique (`_go_to_sleep`).
- `UI/appearance_actions.py` : nouveau champ persisté `blob_hidden` +
  `set_blob_hidden` / `toggle_blob_visibility`.
- `UI/jarvis_menu.py` : items `Blob Visible` et `Listen After Reply`,
  masquage de la fenêtre dans `tick()`, et helpers de survol
  `hover_background_color` / `hover_border_color` (dérivés du thème).
- `src/ui.py` / `src/main.py` : branchement du provider sur le moteur audio et
  restauration de l'orbe depuis l'icône de notification.

### Tests

- `tests/test_blob_visibility.py`, `tests/test_menu_hover.py`,
  `tests/test_menu_integration.py` (nouveaux) et extensions de
  `tests/test_audio_controls.py` / `tests/test_menu_state_bridge.py` :
  masquage + persistance, survol sombre et thématisé sans déplacement du texte,
  écoute post-réponse (activée/désactivée, priorité `Always Listening`,
  réversibilité) et intégration clic → pont → backend. La suite complète passe
  (459 tests, 6 sauts liés à l'absence d'`openwakeword` hors ligne).

## 1.1.2

Correctif anti-console (`CREATE_NO_WINDOW` au lancement UI/desktop).

## 1.1.1

Correctif OpenWakeWord du build distribué (modèles ONNX résolus explicitement).

## 1.1.0

Fonctionnalités d'écoute : interruption vocale, écoute continue, protocoles.

## 1.0.x

Fondations : distribution, launcher, mises à jour, mémoire, routines, rappels.

[1.5.0]: https://github.com/grimalkin2811/Jarvis_Live_Ready/compare/v1.4.1...v1.5.0

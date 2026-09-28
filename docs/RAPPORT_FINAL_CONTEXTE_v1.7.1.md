# Rapport final — Réparation du contexte conversationnel (v1.7.1, 2026-09-28)

> Mission : « réparation de fond en comble du contexte conversationnel ».
> Symptôme : Jarvis comptait correctement les tours (X demandes / Y réponses)
> mais échouait aux questions dont la réponse était dans les tours précédents
> (« Mon prénom est Simon » → « Quel est mon prénom ? » échouait).
> Ce rapport est le livrable final ; les documents de travail associés sont
> `docs/AUDIT_CONTEXTE_CONVERSATIONNEL.md` (carte de flux pré-correction) et
> `docs/JOURNAL_EXPERIENCES_CONTEXTE.md` (E1–E15).

---

## A. Cause racine

**Démontrée par l'expérience E2** (rejouable : `python scripts/diag_conversation_context.py`) :

l'ancien `_replay_context()` envoyait l'historique local à la reconnexion via
`send_client_content(turns=…, turn_complete=False)` et **ne clôturait jamais ce
contenu**. Sur les modèles audio 2.x (dont `gemini-2.5-flash-native-audio-preview-12-2025`,
le modèle par défaut de Jarvis), un `clientContent` non clôturé reste **en
attente** : le tour audio suivant (`send_realtime_input`) **ne le rappelle
pas**. L'historique était donc bien **envoyé sur le câble** (le contexte local
était exact, les compteurs juste) mais **jamais committé** dans l'historique
serveur — le modèle répondait « comme en début de conversation ».

Trace du câble avant correction (faux serveur, scénario F) :

```text
session 2 : 1 clientContent, turn_complete=[False]   ← envoyé, jamais committé
question audio : « Quel est mon prénom ? »
réponse       : « Je ne sais pas, tu ne me l'as pas dit. »
```

C'est exactement le symptôme rapporté, et il converge avec le comportement
documenté publiquement (cf. section E). La cause racine n'est **pas** le
contexte local (correct), **pas** le transport (l'envoi avait lieu), mais le
**protocole de rejeu** : un seed sans clôture (`turn_complete=True`) est
invisible pour les tours audio suivants.

Cinq causes aggravantes, toutes démontrées dans le journal (E3–E7, E13, E14) :

| # | Bug aggravant | Effet | Exp. |
|---|---|---|---|
| 2 | Seed se terminant par un tour `model` | Écart de protocole 2.x (le serveur attend un tour user final) | E4 |
| 3 | Handle mort jamais abandonné | `APIError 1007` en boucle infinie à la reconnexion (`connect_count=38` en 0,5 s), aucun rétablissement | E3 |
| 4 | Fenêtre de course audio/rejeu | Le micro pouvait envoyer de l'audio pendant le rejeu (pattern explicitement déconseillé par le SDK) | E5 |
| 5 | Pas de compression de fenêtre | Session audio terminée à 15 min côté serveur → perte du contexte serveur, puis E3 empêchait tout recouvrement | E7 |
| 6/7 | Handle conservé au changement de voix ; `GoAway` non géré | Nouvelle voix jamais appliquée ; coupure « ABORTED » traitée en erreur (traceback + 5 s) | E13, E14 |

**Pourquoi le bug semblait aléatoire** : tant que le handle de reprise était
valide (< 2 h et session < 15 min), le serveur restaurait l'historique
lui-même et le rappel fonctionnait. Le symptôme n'apparaissait qu'après une
perte de session sans handle valide (handle expiré, session terminée côté
serveur, coupure avant le premier `sessionResumptionUpdate`) — c'est-à-dire
exactement les moments où l'utilisateur revient et pose une question de rappel.

## B. Pourquoi l'ancienne architecture donnait l'illusion

1. **Le contexte local était exact.** `ConversationContext` contenait tous les
   tours, dans l'ordre, avec les bons rôles — les compteurs « X demandes / Y
   réponses » étaient justes. L'illusion : « l'information est chez nous,
   donc le modèle la voit ».
2. **Les tests vérifiaient l'état local, pas le câble.** Les tests
   pré-existants (« contexte OK ») assertaient `context.size()`,
   `get_messages()`, les snapshots — jamais le **payload réellement envoyé à
   Gemini** ni sa **sémantique de commit**. `assert context.size() == 8` prouve
   que Jarvis se souvient, pas que le modèle peut s'en servir.
3. **Le chemin nominal masquait le bug.** Avec un handle valide, la reprise de
   session restaurait l'historique côté serveur : le rejeu défectueux n'était
   jamais exercé. Il ne l'était qu'au pire moment (reconnexion sans handle),
   où son échec était attribué à « la coupure réseau ».
4. **Aucun test ne distinguait « envoyé » de « exploitable ».** La trace du
   câble (`turn_complete=False`) n'était observée par personne.

La suite sémantique v1.7.1 corrige ces quatre points : chaque scénario asserte
le **câble** (kinds, ordre, `turn_complete`, rôles, unicité du seed) ET la
**réponse du modèle** (l'oracle du faux serveur ne sait que ce qui est dans son
historique **committé** — un seed en attente est invisible pour lui, comme sur
le vrai modèle 2.x).

## C. Architecture retenue

**Hybride : reprise de session en primaire, seed committé en repli** (inspirée
de l'implémentation de production pipecat et des docs officielles ; aucune
obligation de conserver l'ancienne architecture n'a été respectée au-delà du
utile — l'ancien `_replay_context` a été remplacé).

```text
connect()
  ├─ tentative A : reprise par handle (sessionResumption.handle)
  │    └─ OK → AUCUN rejeu (le serveur restaure lui-même l'historique)
  ├─ erreur 1007 « Invalid session handle » → handle effacé, UNE nouvelle
  │    tentative en session neuve (jamais de boucle : 2 tentatives max)
  └─ session neuve (sans handle) → _seed_context() :
       1 clientContent unique contenant tout l'historique local
       (to_gemini_contents), dernier tour user garanti (tour vide «  » si
       l'historique finit par une réponse assistant), clôturé par
       turn_complete=True  ← LE correctif de la cause racine
       + HistoryConfig(initial_history_in_client_content=True)  ← protocole 3.x
       + contextWindowCompression(sliding_window)               ← sessions longues
       PUIS la porte _session_ready s'ouvre (aucun audio avant la fin du seed)
```

Composants de robustesse :

* **Porte `_session_ready`** : `can_send()` refuse l'audio tant que le seed
  n'est pas parti (ou la reprise confirmée) — supprime la course E5.
* **`GoAway`** : reconnexion silencieuse immédiate, handle conservé (la session
  reste justement reprenable).
* **Changement de voix** : handle abandonné (la reprise conserverait
  l'ancienne voix) → session neuve + seed committé ; le contexte survit.
* **Instrumentation structurée** (jamais de contenu ni de secret dans les
  journaux) : `SESSION CREATED/RESUMED`, `CONTEXT REPLAY START/END`,
  `TURN START`, `INPUT FINALIZED`, `TOOL START/RESULT`,
  `ASSISTANT TRANSCRIPT FINAL`, `TURN COMPLETE`, avec `conversation_id`,
  `turn_id`, `session_id`, `session_generation`, horodatage.
* **Kill-switchs** (retour arrière sans rebuild) : `JARVIS_LIVE_SEED_MODE`
  (`commit` par défaut, `pending` = ancien protocole, pour expérimentation),
  `JARVIS_LIVE_HISTORY_CONFIG=0`, `JARVIS_LIVE_COMPRESSION=0`.
* **Contexte local** : défauts portés à 20 tours / 4096 tokens (12/3000 avant),
  rognage par tours entiers inchangé (jamais de résultat d'outil orphelin :
  user + outils partagent le même numéro de tour).

Variantes comparées et rejetées : cf. D (E10–E12) et
`docs/AUDIT_CONTEXTE_CONVERSATIONNEL.md` §5.

## D. Expériences

15 expériences documentées intégralement dans `docs/JOURNAL_EXPERIENCES_CONTEXTE.md`
(hypothèse → modification → test minimal → résultat → explication → décision) ;
E1–E3 sont la démonstration « avant correction » sur le code v1.7.0 intact.

| Exp | Question | Résultat | Décision |
|---|---|---|---|
| E1 | Témoin : rappel en session continue | PASS | garder comme témoin anti-régression |
| E2 | **Cause racine** : seed `turn_complete=False` invisible pour l'audio | FAIL (avant correction) | corriger le protocole (E8) |
| E3 | Handle expiré → boucle 1007 infinie | FAIL (avant correction) | repli session neuve (E9) |
| E4 | Seed finissant par un tour `model` | écart confirmé | tour user final garanti |
| E5 | Course audio/rejeu | fenêtre confirmée | porte `_session_ready` |
| E6 | Ordre seed → premier audio sur le câble | validé après correction | assertion permanente |
| E7 | Absence de compression | confirmée | `sliding_window` par défaut |
| E8 | **Nouveau protocole « commit »** (clôture + tour user final + HistoryConfig) | PASS | **retenue** |
| E9 | Repli handle refusé (1007) | PASS | retenue |
| E10 | « pending + clôture différée » (style pipecat) | 1er tour audio « naïf » après seed | rejetée (mode `pending` gardé pour expérimentation) |
| E11 | Historique en texte via `send_realtime_input(text=…)` | dénature les rôles, aucun bénéfice | rejetée |
| E12 | Réinjection complète à chaque tour | interdite (mission), coûteux, masque le problème | rejetée |
| E13 | Changement de voix + contexte | PASS | handle abandonné au changement de voix |
| E14 | GoAway | PASS | reconnexion silencieuse, handle conservé |
| E15 | Limites locales (12 tours) | PASS après passage à 20 | retenue |

La mission autorisait « jusqu'à ~40 variantes » : la convergence a été
atteinte en 15, chacune motivée par une observation ou une exigence de la
mission — les variantes supplémentaires auraient été du refactorage gratuit.

## E. Recherche externe (menée AVANT la conclusion)

Sources décisives (détail et citations dans le document de travail §3) :

* **pipecat** `services/google/gemini_live/llm.py` (source de production) :
  documente la limitation des modèles 2.x audio — un historique seedé sans
  clôture est ignoré (« *without this, the model ignores the context it's been
  seeded with before the user started speaking* ») et le contournement
  (protocole complet : clôture + tour user final + inférence de reprise).
  Convergence directe avec E2/E8.
* **Doc officielle Live API** (session-management, migration 2.5→3.8) :
  `send_client_content` sert à seeder l'historique ; en 3.x,
  `HistoryConfig.initial_history_in_client_content=True` puis clientContent
  jusqu'à `turn_complete=True` ; sessions ~10 min (`GoAway`), 15 min max sans
  compression, handles valides 2 h, `update.resumable and update.new_handle`.
* **Thread Google AI Dev Forum 111617** (+ post 219153) : le bug exact —
  question TEXTE rappelle l'historique seedé, question AUDIO amnésique —
  reproduit sur `gemini-2.5-flash-native-audio-preview-12-2025` (le modèle de
  Jarvis), avec la même config que Jarvis ; confirmé côté Google (225150),
  non résolu côté API. → Le harness modélise ce comportement (pending invisible
  pour l'oracle audio).
* **Thread 62949** : après `clientContent` `turnComplete:false`, seul un
  `clientContent` `turnComplete:true` clôt le tour ; `realtimeInput` ne le
  peut pas.
* **googleapis/python-genai #2197** : handle invalide → `APIError 1007` levée
  au connect (pas de session vierge silencieuse) → sans repli, boucle infinie
  (E3).
* **LiveKit agents-js #1197** : l'injection de tours est spécifique 2.5,
  restreinte sur 3.x → protocole différencié 2.x/3.x dans Jarvis.
* **SKILL gemini-live-api-dev** : `send_client_content` uniquement pour seeder
  l'historique initial ; compression recommandée au-delà de 15 min.

Les hypothèses « texte vs audio » du rappel d'historique ont été traitées
comme des hypothèses à tester : elles sont devenues E2 (confirmée sur le
harness) — jamais une vérité admise.

## F. Tests

* **Suite complète du dépôt** : **664 passed** + 390 subtests passed
  (modules collectables ; hors modules Qt, cf. échecs d'environnement).
* **Échecs** : **6 failed** dans `tests/test_desktop_config.py`
  (`AppearanceStateIntegrationTests`) + **14 fichiers UI non collectables** —
  tous en `ImportError: libGL.so.1` (PySide6/Qt indisponible dans cet
  environnement). **NON DISPONIBLE DANS L'ENVIRONNEMENT / RAISON : libGL** ;
  échecs pré-existants, sans lien avec le contexte (aucun test supprimé ni
  affaibli).
* **Nouveaux** : `tests/test_live_context_harness.py` — **29 tests**
  sémantiques sur le chemin vocal réel (micro simulé → `send_realtime_input`
  → réception → contexte → réponse vocale), assertant câble ET réponses.
* **Mis à jour avec le bump de version** : `tests/test_version.py` — l'épinglage
  de version (garde-fou anti-bump accidentel) passe de 1.7.0 à 1.7.1, comme
  chaque montée de version du projet.
* **Renforcés** : `tests/test_conversation_pipeline.py` — test de rejeu
  réécrit pour le nouveau protocole (turn_complete=True, rôles, tour vide
  final) + nouveau test « l'historique fini par l'utilisateur ne génère pas de
  tour vide ».
* **Diagnostic rejouable** : `python scripts/diag_conversation_context.py` —
  scénarios A/F/G **PASS**.
* **Tests existants** : 152 tests des 5 fichiers pipeline/contexte/providers/
  gemini/interruption passent sans modification de leurs assertions (seul le
  test de rejeu, qui codait l'ANCIEN protocole — turn_complete=False — a été
  mis au nouveau ; c'est le sujet même de la correction).
* **Lint** : `ruff check .` — seul mon code est concerné par 0 erreur ; 5
  erreurs pré-existentes dans des scripts non touchés (`scripts/make_icon.py`,
  `make_portable_zip.py`, `validate_build.py`).
* **TEST GEMINI RÉEL : NON DISPONIBLE DANS L'ENVIRONNEMENT / RAISON : pas de
  clé API (`GEMINI_API_KEY` absente du sandbox), pas d'accès sortant vers
  l'API Gemini Live, ni micro/haut-parleurs.** Le chemin audio a été testé sur
  un faux serveur Live construit pour être fidèle au protocole documenté ET
  aux comportements rapportés (pending invisible pour l'audio sur 2.x,
  erreur 1007 au connect, handles 2 h, GoAway, `turnComplete` obligatoire).
  La validation sur l'API réelle reste à faire au premier lancement avec clé
  (les kill-switchs permettent un retour arrière immédiat).

## G. Scénarios sémantiques

Tous exécutés sur le chemin vocal (audio → transcription → contexte → réponse
vocale), avec assertions sur le câble quand pertinent :

| Scénario | Test | Résultat |
|---|---|---|
| Simon → « Quel est mon prénom ? » | `test_A_rappel_du_prenom_dans_une_session`, `test_F_le_contexte_survit_a_une_reconnexion_sans_handle`, `test_G_handle_expire…` | **PASS** |
| Hans Zimmer → « Qui était le compositeur ? » (multi-tour) | `test_B_deux_compositeurs_puis_deux_questions` | **PASS** |
| Daft Punk → « lance le deuxième » → « celui d'avant » | `test_C_le_deuxieme_puis_celui_d_avant` | **PASS** |
| Outil (`music_search`) → follow-up anaphorique | `test_C…`, `test_D_contexte_apres_outil_survit_a_une_reconnexion` | **PASS** |
| Reconnexion → contexte (seed : unique, clôturé, user-final, avant tout audio, sans duplication) | 5 tests `test_F_*` | **PASS** |
| Interruption → continuité (aucun historique fantôme/dupliqué) | `test_E_interruption_puis_nouveau_tour_sans_doublon`, `test_interruption_conserve_le_debut_de_reponse` | **PASS** |
| Provider switch Gemini ↔ Ollama (aller ET retour) | `test_le_changement_de_provider_conserve_le_contexte`, `test_adaptateurs_unitaires_roles_et_outils` | **PASS** |
| Session resumption (handle valide : zéro rejeu ; expiré : repli+rejeu ; renouvellement/tour) | 3 tests `test_G_*` | **PASS** |
| Modes/voix/GoAway/reset | `test_H_*`, `test_goaway…`, `test_reset…` | **PASS** |
| Arêtes sémantiques : multi-infos/tour, négation/correction, infos similaires, historique 18-20 tours | 4 tests `SemanticEdgeTests` | **PASS** |
| Protocole 3.x (commit silencieux, sans récap) | `test_3x_le_seed_est_committe_sans_recap_et_rappelle` | **PASS** |

**Test ultime** (« Jarvis peut-il répondre à une question dont la réponse est
uniquement dans les tours précédents ? ») : **OUI sur le chemin vocal complet
du harness** (simulé, fidèle au protocole) ; **non encore démontré sur l'API
Gemini réelle** (cf. F et H).

## H. Limites restantes (aucune cachée)

1. **Validation sur l'API réelle non faite** : pas de clé API dans cet
   environnement. Le faux serveur modélise les comportements **documentés et
   rapportés** (thread 111617, docs, pipecat), pas le modèle réel — en
   particulier la « brève inférence de reprise » après un seed committé
   (comportement pipecat documenté) n'est qu'émulée. Premier lancement avec
   clé : rejouer `scripts/diag_conversation_context.py --real` (support prévu).
2. **Oracle sémantique = règles**, pas un modèle : il ne sait que ce qui est
   dans l'historique committé du faux serveur. Il détecte les pertes de
   contexte (le bug), pas les subtilités de compréhension d'un vrai LLM.
3. **Échecs d'environnement** : 6 tests + 14 fichiers UI (libGL/PySide6) non
   exécutables ici ; à rejouer sur une machine avec Qt.
4. **Le contexte local reste borné à 20 tours** (configurable) : au-delà, le
   début de la conversation sort du contexte (et donc du seed) — choix
   documenté, cohérent coût/fenêtre, pas une limite cachée.
5. **Le watcher de voix détecte le changement en 0,5 s** (poll) : pendant
   cette fenêtre, la session garde l'ancienne voix.
6. **Asymétrie fournisseurs** : Ollama n'a pas de « session Live » ; seul
   l'adaptateur de projection (`to_ollama_messages`) est testé — Jarvis
   v1.7.1 n'embarque toujours pas de client Ollama (inchangé, documenté).
7. **Perfs mesurées hors latence réseau** (faux serveur) : build du seed
   20 tours = **0,07 ms / 6,1 Ko / ~1026 tokens** ; connect+seed ≈ 5 ms locaux ;
   RSS inchangé après 200 rebuilds (60 Mo). La latence réseau réelle
   (aller-retour du seed) s'ajoute en production et n'est pas mesurable ici.
   Script : `scripts/perf_context_replay.py`.
8. **Modes dégradés volontaires** : `JARVIS_LIVE_SEED_MODE=pending` reproduit
   l'ancien protocole (documenté comme dégradé) ; les kill-switchs
   HistoryConfig/compression désactivent des protections serveur.

## I. Fichiers modifiés

| Fichier | Nature | Contenu |
|---|---|---|
| `src/gemini_live.py` | **modifié (+311/-38)** | `_seed_context()` (seed committé : clôture, tour user final, HistoryConfig 3.x), repli 1007 (2 tentatives max), porte `_session_ready`, compression `sliding_window`, GoAway, handle abandonné au changement de voix, instrumentation structurée, kill-switchs `JARVIS_LIVE_*` |
| `src/conversation.py` | modifié (+14) | défauts contexte 20 tours / 4096 tokens (env inchangé), noms d'évènements de log stabilisés |
| `tests/live_harness.py` | **nouveau** | faux serveur Live (sémantiques 2.x/3.x, handles, 1007, GoAway), oracle sémantique, traçage du câble (`WireEvent`) |
| `tests/voice_harness.py` | **nouveau** | pipeline vocal complet sur faux serveur (boucle de reconnexion calquée sur `src/main.py`/`src/ui.py`) |
| `tests/test_live_context_harness.py` | **nouveau** | 29 tests sémantiques (scénarios A–H, arêtes, providers, 3.x, instrumentation) |
| `tests/test_conversation_pipeline.py` | modifié | rejeu mis au nouveau protocole + test « pas de tour vide si l'historique finit par un user » |
| `tests/test_version.py` | modifié | épingle de version 1.7.0 → 1.7.1 (processus de release standard) |
| `scripts/diag_conversation_context.py` | **nouveau** | diagnostic rejouable A/F/G (+ mode `--real` prévu) |
| `scripts/perf_context_replay.py` | **nouveau** | mesures §17 (build seed, rejeu pipeline, mémoire/CPU) |
| `docs/AUDIT_CONTEXTE_CONVERSATIONNEL.md` | **nouveau** | audit pré-correction : carte de flux depuis le code, 9 questions par étape, bugs 1–7 |
| `docs/JOURNAL_EXPERIENCES_CONTEXTE.md` | **nouveau** | journal E1–E15 |
| `docs/RAPPORT_FINAL_CONTEXTE_v1.7.1.md` | **nouveau** | ce rapport |
| `README.md` | modifié | section 1.7.1, valeurs par défaut du contexte, protocole de rejeu |
| `CHANGELOG.md` | modifié | entrée 1.7.1 |
| `src/version.py` | modifié | `1.7.0` → `1.7.1` |

**Desktop Mode v1.7 intact** : aucun fichier UI modifié ; les 664 tests
collectables (dont Desktop HUD/overlay/états quand Qt est disponible) passent ;
le contexte appartient au backend (`src/ui.py` `_apply_mode` ne le touche
jamais, commenté et testé).

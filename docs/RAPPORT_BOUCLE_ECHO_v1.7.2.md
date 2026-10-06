# Rapport final — Boucle d'écho « Je vous écoute » (v1.7.2)

**Bug critique :** après une requête utilisateur, Jarvis répétait « Je vous
écoute » environ toutes les 2 secondes sans qu'aucune parole humaine ne soit
dite, et la fenêtre d'écoute de 8 secondes était réarmée en boucle.

**Mission :** comprendre pourquoi les ~8 secondes se réarmaient toutes les
~2 secondes en silence utilisateur, puis supprimer définitivement la cause.
Correction architecturale et causale exigée — pas de rustine prompt/délai.

---

## Root cause

Le « timer de 8 secondes » n'est pas un `QTimer` : c'est l'échéance unique
`AudioIO.follow_up_until`, vérifiée à chaque tour de `_audio_worker`
(`_check_timeout`). Elle possède **quatre** points d'écriture, tous
canalisés depuis v1.7.2 par `_arm_follow_up()` (horodatés, numérotés,
journalisés) :

| Point | Déclencheur |
|---|---|
| `_wake()` | wake word détecté **ou** mode écoute continue |
| `extend_listening()` | **`turn_complete` du serveur Gemini** (fin de *génération*) |
| `clear_output()` | interruption (barge-in vocal, bouton Stop) |
| `_check_timeout()` | renouvellement du mode écoute continue |

La boucle provenait du **décalage entre deux notions différentes de « Jarvis
parle »** :

* `GeminiLive.speaking` suit l'état de **génération** du serveur : il passe à
  `False` dès `turn_complete` — c'est lui qui fermait le micro
  (`can_send()`) ;
* mais la **lecture locale** de la voix (file `q` + `tampon` de la carte
  son) traîne derrière : jusqu'à plusieurs secondes d'audio déjà reçues pas
  encore sorties des haut-parleurs (`MAX_QUEUED_SECONDS = 5`).

**Chaîne causale exacte (démontrée, voir Evidence)** :

```
turn_complete serveur (fin de GÉNÉRATION)
  → gemini.speaking = False                      [le micro se rouvre]
  → extend_listening() : follow_up_until = +8 s  [RESET n]
  MAIS il reste ~1,2 s de voix de Jarvis dans la file de sortie
  → les haut-parleurs jouent la fin de la réponse
  → le micro capte l'ÉCHO de Jarvis (RMS ≈ 1200, loin au-dessus du seuil VAD)
  → la VAD du serveur croit à une prise de parole utilisateur
  → le serveur commit un tour FANTÔME et le modèle répond « Je vous écoute. »
  → turn_complete → extend_listening → RESET n+1 → …  [boucle stable ≈ 1,7 s]
```

La période stable (~1,68 s dans le harness, « environ 2 secondes » perçues)
est le temps de lecture de la réponse (~1,2 s) + la détection de fin de
parole par la VAD serveur (~0,4 s) + la latence de génération. La phrase
exacte « Je vous écoute » s'auto-entretient : l'écho que le serveur entend
EST la propre voix de Jarvis, et la réponse naturelle du modèle à un écho
inintelligible (ou à entendre « je vous écoute ») est de le redire.

Deux défauts satellites ont été corrigés dans la même foulée (démontrés par
tests) :

1. **Comptage de file cassé** : `_queued_bytes` était décompté DEUX fois par
   octet (au passage `q → buffer` PUIS à la consommation par la carte son) ;
   il passait négatif, était ramené à 0 : le plafond de 5 s
   (`MAX_QUEUED_SECONDS`) ne jouait plus son rôle et **aucune primitive ne
   savait combien d'audio restait à jouer**.
2. **Réveil en rafale du mode écoute continue** : un Jarvis rendu endormi
   (perte de connexion, `audio.awake = False` des chemins d'erreur de
   `src/main.py`/`src/ui.py`) était réveilli à CHAQUE bloc de 80 ms tant que
   le mode restait actif — affichage « Je vous écoute » et fenêtre de 8 s
   réarmés en rafale, sans parole humaine (l'anti-rebond
   `WAKE_COOLDOWN_SECONDS` ne s'appliquait qu'au wake word).

## Evidence

Harness de reproduction dédié : `tests/echo_loop_harness.py` — le **vrai**
pipeline (vraie `AudioIO` avec ses threads, vrai pont micro
`can_send() → send_audio()`, vraie `GeminiLive` avec sa boucle de
reconnexion) sur une fausse carte son **threadée** (capture 80 ms,
restitution 42,67 ms), un modèle acoustique à écho (sortie haut-parleurs →
micro, gain 0,30, retard 40 ms) et un faux serveur Live doté d'une **VAD par
énergie** (seuil RMS 350, fin de parole = 5 blocs sous le seuil).

### Avant correctif (code v1.7.1, capture réelle du 2026-09-29)

Parole utilisateur 1,5 s puis **silence total 14 s** :

```
TOURS FANTOMES pendant le silence utilisateur : 7
PERIODE de la boucle : [1.68, 1.68, 1.68, 1.68, 1.68, 1.68]
RESETS du timer 8s pendant le silence : 10 (raisons: {turn_complete, interrupt})
```

Trace d'un cycle (instrumentation `JARVIS_AUDIO_TRACE=1`) — la preuve du
décalage génération/lecture :

```
[AUDIO] silence_timer RESET id=4 reason=turn_complete src=serveur
        out_pending_s=1.2  mic_age_s=0.002  since_last_reset_s=1.678
[AUDIO] output_drained   mic_rms=1171.6     ← l'écho capté pendant la vidange
[serveur] tour FANTÔME 'je vous écoute' (peak_rms≈1280) → réponse → RESET id=5
```

À chaque réarmement : `out_pending_s=1.2` (il restait 1,2 s de voix à
jouer) et `mic_age_s=0.002` (le micro traitait un bloc 2 ms plus tôt) — le
micro était ouvert au service du serveur pendant que les haut-parleurs
parlaient encore.

### Après correctif (même scénario, mêmes paramètres)

```
TOURS FANTOMES pendant le silence : 0
RESETS du timer pendant le silence : 1 (le tour légitime de l'utilisateur)
transitions porte micro : [(True, 0.0), (False, 1.16), (True, 0.0)]
[Jarvis] timeout de conversation → Retour en veille.
```

La porte micro se ferme à l'arrivée de la réponse (1,16 s en file), se
rouvre après la vidange + la traîne acoustique ; la fenêtre de 8 s **expire
normalement** ; Jarvis repasse en veille. `silence_timer_resets_after_speech
== 0` au-delà du tour légitime.

Chaque hypothèse alternative a été examinée puis écartée (voir
« Remaining risks » pour ce qui reste non vérifiable) : timers UI (audités :
purement affichage, jamais `follow_up_until`), reconnexion en boucle + seed
(le recap de rejeu est ponctuel, porté par des événements réseau, couvert
par le test G), scheduler/routines/protocoles (aucune source audio
périodique vers `AudioIO`), wake word s'auto-détectant dans le TTS
(impossible : endormi ⇒ file vidée depuis longtemps ; et la fenêtre de 8 s
maintient Jarvis éveillé après une réponse), `output_level_hook` (chemin
affichage seul, jamais le timer — prouvé par le test C).

## Fix

### `src/audio.py`

1. **Porte micro anti-écho** (la correction causale) :
   `output_pending_bytes()`/`output_pending_seconds()` — nouvelles
   primitives « combien de voix reste-t-il à jouer » — et `_mic_gate_open()` :
   le micro n'est **pas transmis à Gemini** tant que de la voix reste en
   file ou que la traîne acoustique (`MIC_ECHO_GUARD_SECONDS = 0,25 s` :
   réverbération de la pièce + latence du mixeur, pas un délai arbitraire)
   n'est pas écoulée. La porte vit dans `_handle_awake_block`, en aval de la
   détection d'interruption : **le barge-in local n'est pas concerné** (il
   lit le micro brut, vide la sortie, ce qui rouvre la porte immédiatement).
2. **Barge-in pendant la traîne audible** : `_voice_audible()` = génération
   OU sortie en attente. Avant, seule la génération comptait : pendant la
   traîne de lecture (jusqu'à 5 s), couper la parole à Jarvis était
   impossible autrement que par le bouton. Le plancher adaptatif anti
   auto-interruption continue de s'appliquer.
3. **`_check_timeout` cohérent** : la fenêtre de conversation est suspendue
   tant que la voix est *audible* (le commentaire historique « on ne
   retourne en veille que lorsque la réponse est finie » est enfin
   implémenté à la lettre).
4. **Comptage de file corrigé** : un seul décompte, à la consommation réelle
   par la carte son ; le plafond `MAX_QUEUED_SECONDS` fonctionne à nouveau.
5. **Garde `awake` sur `clear_output`** : un callback résiduel (vieille
   session, interruption tardive) ne peut plus rouvrir l'écoute d'un Jarvis
   endormi ni armer la fenêtre.
6. **Anti-rebond du réveil en écoute continue** : `WAKE_COOLDOWN_SECONDS`
   s'applique aussi au réveil automatique (fini le réveil tous les 80 ms).
7. **Instrumentation pérenne** (activée par `JARVIS_AUDIO_TRACE=1`) :
   `_arm_follow_up()` canalise TOUT réarmement avec identifiant (epoch),
   raison, thread, état, RMS/peak micro, âge du dernier bloc micro, audio
   restant en sortie, délai depuis le précédent réarmement ; événements
   `wake`, `sleep`, `speaking`, `output_drained`, `mic_gate`, `barge_in`,
   `clear_output ignore`. Historique borné (512 événements) consultable par
   `AudioIO.trace_events()` — chaque réarmement est expliqué a posteriori.

### Fichiers

* `src/audio.py` — corrections 1 à 7 (aucun changement de contrat public ;
  `mic_enabled`, `voice_hook`, `output_level_hook`, modes, volume : intacts).
* `tests/echo_loop_harness.py` — **nouveau** : carte son factice threadée,
  modèle acoustique à écho, serveur Live avec VAD, lab complet pilotable.
* `tests/test_echo_loop.py` — **nouveau** : 20 tests (voir ci-dessous).
* `version.py`, `src/version.py` — 1.7.2.
* `CHANGELOG.md`, `README.md` — entrée 1.7.2 et documentation du comportement.

## Audio lifecycle

Le cycle après correction — les deux notions de « Jarvis parle » sont
désormais explicites et cohérentes :

```
MIC (80 ms) ──► _in : RMS/peak mémorisés (contexte de trace)
                 │
                 ▼
         _audio_worker : _check_timeout (fenêtre suspendue tant que voix audible)
                 │  awake ? ── non ──► wake word / écoute continue (avec anti-rebond)
                 ▼ oui
         _handle_awake_block :
           1. _detect_barge_in   (micro BRUT, pendant génération OU traîne)
                └─ déclenché ──► clear_output + request_interrupt + rejeu du
                                 pré-tampon : la porte s'ouvre immédiatement
           2. PORTE ANTI-ÉCHO    (file de sortie vide + traîne écoulée ?)
                 └─ fermée ──► rien n'est transmis (l'écho ne peut pas partir)
                 └─ ouverte ──► on_input → can_send() → send_realtime_input
                                 ▲ can_send garde ses rôles : session prête,
                                 pas d'outil en cours, pas en génération
                                 (sauf fenêtre d'interruption)
                 ▼
         GEMINI LIVE (VAD serveur) ── n'entend QUE de la voix humaine
                 │
                 ▼
         model_turn → play() → file (plafond 5 s réel) → haut-parleurs
                 │
                 ├── turn_complete (fin de GÉNÉRATION) → speaking = False
                 │     └─ extend_listening : fenêtre +8 s (réarmement tracé)
                 │         MAIS porte micro FERMÉE tant que la file n'est pas vide
                 ▼
         haut-parleurs silencieux (vidange + 0,25 s) → porte OUVERTE
                 │
                 ▼
         8 s sans parole → timeout → HIDDEN (et hidden reste hidden :
           clear_output/extend_listening sont ignorés quand awake = False)
```

## Tests

* **Nouveaux tests écho (temps réel, `tests/test_echo_loop.py`) : 20/20
  passés** en 4 min 29 s —
  * A silence 20 s : `0` tour fantôme, `0` réarmement après le tour légitime,
    `0` redémarrage d'écoute, retour en veille ;
  * B deux phrases séparées : le 2ᵉ énoncé est détecté (2 tours utilisateur,
    0 fantôme) ;
  * C sortie TTS ≠ entrée utilisateur : **aucun** bloc micro transmis au
    serveur pendant la lecture + traîne (la porte coupe le flux, pas
    seulement le volume), puis seul le bruit de fond (< seuil VAD) ;
  * D interruption vocale pendant la réponse (traîne comprise) : barge-in,
    sortie coupée, « stop » entendu par le serveur, aucun timer résiduel ;
  * E fenêtre de suivi : expire vers HIDDEN, et HIDDEN ≠ nouveau LISTENING ;
  * F sans écoute post-réponse : réponse → veille immédiate, callbacks
    résiduels (`clear_output`, `extend_listening`) ignorés ;
  * G déconnexion/reconnexion PENDANT la lecture : continuité par handle OU
    rejeu, 0 fantôme, aucun micro transmis à la nouvelle session pendant la
    traîne de l'ancienne ;
  * mode écoute continue : le renouvellement de fenêtre est silencieux
    (aucun réveil, aucun son) ;
  * stabilité 30 s / 30 s / 60 s : compteurs EXACTS (3 réveils, 3 tours,
    6 réarmements tous prédits, époques 1→6 strictement croissantes = un
    seul timer logique, 1 session, 0 fantôme) — « aucune croissance
    inexpliquée » est une égalité exacte, pas une tendance ;
  * comptage de file : le compteur suit la file réelle (q + tampon), n'est
    jamais négatif, le plafond borne ;
  * performance : porte < 800 µs/appel mesurée sur 20 000 itérations avec
    file chargée (budget d'un bloc : 80 000 µs).
* **Suite non-UI complète : 688 passés, 390 subtests, 6 échecs** — les 6
  échecs sont les échecs `libGL` **pré-existants** de
  `tests/test_desktop_config.py` (limitation du sandbox Linux sans OpenGL,
  identiques à la baseline documentée ; aucune relation avec ce correctif).
* **Suite UI (Qt) : exécutée par la CI Windows** (voir Real hardware) — le
  sandbox Linux ne peut pas importer `PySide6.QtGui` sans `libGL`.
* `ruff` : vert sur tous les fichiers touchés.
* Tests audio existants (`test_audio_controls.py`, `test_interruption.py`,
  `test_gemini_live.py`, 33 tests protocolaires du faux serveur) : tous
  passent inchangés — aucun test affaibli ni supprimé.

## Real hardware

```
REAL MICROPHONE TEST: NOT TESTABLE IN SANDBOX
```

Le sandbox n'a ni micro, ni haut-parleur, ni accès réseau sortant : la
validation matérielle réelle (dire une phrase, attendre 30 s, vérifier
l'absence de « Je vous écoute » spontané) reste à faire sur la machine
cible. Le harness reproduit néanmoins la chaîne complète *logicielle*
(carte son threadée, écho acoustique modélisé, VAD serveur) et le pipeline
est celui de production. Pour un test manuel sur le vrai matériel :

1. lancer Jarvis (`python -m src.main`, ou `--desktop`) ;
2. dire une phrase, attendre la réponse ;
3. ne plus parler pendant 30 s ;
4. attendu : aucun « Je vous écoute » spontané, aucun restart d'écoute ;
   `JARVIS_AUDIO_TRACE=1` affiche chaque réarmement avec sa raison (il ne
   doit plus y en avoir après le tour légitime).

## Remaining risks

* **Vraie acoustique** : le gain d'écho réel (enceintes → micro) varie ; le
  harness utilise 0,30 (enceintes modérées sans casque). La porte coupe le
  flux micro tant que la file n'est pas vide : même un écho très fort ne
  peut plus partir. Seule la traîne (0,25 s) est une estimation physique —
  une pièce très réverbérante pourrait la dépasser ; le paramètre est une
  constante de classe documentée (`MIC_ECHO_GUARD_SECONDS`).
* **Parole douce pendant la traîne** : une parole utilisateur trop faible
  pour déclencher le barge-in local et prononcée par-dessus la traîne de
  lecture n'atteint pas le serveur avant la réouverture de la porte (au
  pire ~5 s de file + 0,25 s). Avant le correctif, ce cas partait mélangé à
  l'écho et la VAD serveur le commitait comme tour fantôme — le trade-off
  est délibéré ; l'interruption vocale reste la voie prévue pour parler
  par-dessus Jarvis.
* **VAD serveur sur bruit ambiant fort** : micro ouvert + pièce très bruyante
  peut produire des tours serveur non désirés — inhérent au protocole
  (VAD serveur), sans rapport avec la boucle d'écho (période stable ≈ 2 s =
  signature de l'écho, absente après correctif).
* **Recap de rejeu v1.7.1** : à une reconnexion sans handle, le rejeu clôturé
  déclenche une brève inférence de reprise (protocole 2.x, voulu) qui
  réarme la fenêtre une fois. Ponctuel par construction (les reconnexions
  sont pilotées par le réseau), couvert par le test G : aucun fantôme.
* **CI Windows** : les tests écho sont temps réel (threads cadencés) ; les
  timeouts internes sont larges (6 à 20 s) mais un runner Windows très
  chargé pourrait les rendre flaky — à surveiller sur les premiers runs.

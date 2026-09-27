# Desktop Mode — la présence de Jarvis sur votre bureau (v1.7.0)

> « Jarvis est là, mais jamais dans le chemin. »

Le Desktop Mode est l'interface **sans fenêtre** de Jarvis : pas d'orbe, pas de
menu radial, pas de barre flottante. Une lumière périphérique — le **halo** —
et quelques indicateurs discrets vous disent en permanence ce que Jarvis est en
train de faire, pendant que vous continuez à travailler, jouer ou regarder un
film.

Activation : icône de notification → **Interface → Desktop Mode**, ou à la voix
(« passe en mode desktop »), ou `Jarvis.bat --desktop`.

---

## 1. Ce qui change en 1.7.0

Jusqu'en 1.6.0, le cadre Desktop savait faire trois choses : s'allumer plus ou
moins fort. Les états « écoute », « réflexion » et « réponse » n'étaient que
trois intensités du **même** dessin, le halo repeignait tout l'écran 60 fois par
seconde, et le retour en veille reposait sur un minuteur de 8,4 s.

La 1.7.0 reprend le sujet à la base :

| | 1.6.0 | 1.7.0 |
|---|---|---|
| États | 3 intensités | **9 états nommés**, chacun avec sa couleur ET sa forme |
| Pilotage | présence + minuteur | **évènements réels** du pipeline (hotword, transcription, outil, TTS, interruption, erreur) |
| Rendu | plein écran, 8 dégradés radiaux / image | bandes mises en cache, **repaint limité aux bords** |
| Coût (1080p, pire cas) | ~18,9 ms / image | **~1,6 ms / image** |
| Contenu | halo seul | halo + état + transcription + outil + visualiseur + réponse + contrôles |
| Personnalisation | aucune | **éditeur complet** avec glisser-déposer |
| Clics | toujours traversants | 3 politiques, **traversant par défaut** |

---

## 2. Les états

Chaque état a une **signature visuelle** propre : la couleur change, mais la
*forme* de la lumière aussi. On reconnaît l'état du coin de l'œil, sans lire.

| État | Quand | Halo |
|---|---|---|
| `hidden` | Jarvis dort | **rien du tout** — pas un pixel, pas une animation |
| `loading` | chargement du wake word | lueur très faible en bas |
| `listening` | « Hey Jarvis » entendu, micro ouvert | lumière qui **monte du bas**, respire, réagit à votre voix |
| `thinking` | le modèle réfléchit | teinte **indigo**, deux accents **circulent** le long du périmètre |
| `tool_use` | un outil s'exécute | teinte **turquoise**, **segments** qui s'allument en séquence sur le bord haut |
| `speaking` | Jarvis répond | pulsation **gauche/droite** pilotée par le niveau réel de sa voix |
| `follow_up` | fenêtre d'écoute après la réponse | version atténuée de l'écoute + **ligne de compte à rebours** qui se résorbe |
| `interrupted` | vous avez coupé la parole | effondrement bref |
| `error` | incident backend | **rouge**, franc, puis disparition automatique (2,2 s) |

Les couleurs dérivent du **thème de l'orbe** (Appearance → Color) : changer le
thème change tout le Desktop Mode. Seul l'état `error` impose sa couleur —
l'ambiguïté y est interdite.

### Ce qui déclenche quoi

Aucun état n'est deviné. Le contrôleur écoute des faits :

```
AudioIO._wake()            → listening
transcription entrante     → listening (relance la fenêtre)
tool_call reçu             → tool_use  (avec le nom de l'outil)
fin d'outil                → thinking
premier audio du modèle    → speaking
turn_complete              → follow_up
interruption serveur       → interrupted
exception de connexion     → error
AudioIO._go_to_sleep()     → hidden
```

Les seuls minuteurs restants sont des **filets de sécurité** (fermeture de la
fenêtre de suivi, effacement d'une erreur). Leurs durées sont définies une
seule fois, dans `UI/desktop/state.py`.

---

## 3. Les éléments affichés

| Élément | Par défaut | Rôle |
|---|---|---|
| **Halo** | ✅ | la présence elle-même |
| **État** | ✅ | pastille « À l'écoute », « Réflexion », « Action »… |
| **Transcription** | ✅ | ce que Jarvis a compris — la **même** transcription que le contexte conversationnel 1.6.0, pas un second système |
| **Outil** | ✅ | nom de l'action en cours (`⚙ music_play`) |
| **Visualiseur** | ✅ | sept barres sur le niveau audio réel |
| **Réponse** | ❌ | résumé court de ce que Jarvis vient de dire |
| **Contrôles** | ❌ | boutons Stop / Micro / Masquer |

**Pourquoi « Réponse » et « Contrôles » sont éteints par défaut** — ce sont des
choix assumés, pas des oublis :

* Jarvis **parle**. Réafficher tout ce qu'il dit transformerait l'overlay en
  fil de discussion flottant, ce qu'il ne doit pas être.
* Tant que les contrôles sont éteints, **rien** sur l'écran n'intercepte la
  souris. C'est la garantie de non-intrusion la plus forte possible, et elle
  est vraie dès l'installation.

Les deux s'activent en deux clics dans l'éditeur.

---

## 4. Clics, focus et raccourcis

L'overlay principal est créé avec :

```
Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
| Qt.WindowDoesNotAcceptFocus | Qt.WindowTransparentForInput
WA_TranslucentBackground · WA_NoSystemBackground
WA_TransparentForMouseEvents · WA_ShowWithoutActivating · NoFocus
```

Conséquences : il **ne prend jamais le focus**, ne change jamais la fenêtre
active, n'intercepte ni le clavier, ni les raccourcis Windows, ni les entrées
d'un jeu.

Trois politiques sont proposées (éditeur → *Clics de la souris*) :

| Politique | Effet |
|---|---|
| **Toujours traversable** | absolument rien ne capte la souris, même les contrôles |
| **Widgets interactifs uniquement** *(défaut)* | seuls les boutons Stop/Micro/Masquer captent — et uniquement s'ils sont activés |
| **Overlay interactif** | l'écran entier capte la souris (cas particuliers : capture vidéo, écran tactile) |

> **Détail d'implémentation qui compte.** Les boutons cliquables ne sont *pas*
> posés sur l'overlay : ils vivent dans une petite fenêtre outil séparée,
> créée seulement si vous les activez. C'est ce qui permet d'avoir des
> contrôles cliquables **sans** rendre l'écran entier interceptant — et sans
> découper le halo, ce qu'un `setMask` sur la fenêtre principale ferait.

Le **mode jeu** conserve sa priorité : `should_suppress_visuals()` masque
intégralement le Desktop Mode, exactement comme en 1.3.0.

---

## 5. L'éditeur d'apparence

Deux chemins d'accès :

* **Blob Mode** → menu radial **Appearance** → **Desktop HUD** ;
* **icône de notification** → **Apparence Desktop…** (indispensable : en
  Desktop Mode il n'y a pas de menu radial).

Ce qu'on y fait :

* **activer / désactiver** chaque élément ;
* le **déplacer à la souris** dans l'aperçu — avec aimantation sur une grille
  3 × 3 ;
* régler sa **taille** ;
* cocher **dans quels états** il apparaît (6 états × 7 éléments) ;
* **prévisualiser** n'importe quel état ;
* régler le halo (force, épaisseur), la taille du texte, les **animations
  réduites** et la réactivité audio ;
* **Rétablir les valeurs d'origine**.

Chaque modification est **enregistrée immédiatement** et appliquée à l'overlay
en cours d'exécution — pas de bouton « Appliquer », pas de redémarrage.

### Où c'est rangé

Dans le fichier d'apparence **existant** :

```
%LOCALAPPDATA%\Jarvis\ui\appearance_state.json
└── "desktop": { "schema_version": 1, "interaction": …, "widgets": { … } }
```

Aucun second fichier de configuration n'a été créé : l'apparence du Desktop
Mode *est* de l'apparence. Un fichier écrit par la 1.6.0 n'a pas ce bloc — les
valeurs par défaut s'appliquent, et vos réglages 1.6.0 (thème, opacité des
fonds d'items) sont conservés à l'identique. `schema_version` rend une future
migration explicite plutôt que dispersée.

### Positions et multi-écran

Chaque élément est placé par un **centre normalisé** `(x, y)` dans `[0, 1]` de
l'écran. C'est indépendant de la résolution, du DPI et du nombre d'écrans :
passer d'un 1080p à un 4K conserve la composition. L'overlay suit l'écran sous
le curseur, et se recale automatiquement sur ajout/retrait d'écran ou
changement d'écran principal.

---

## 6. Accessibilité

* **Animations réduites** : tous les états restent distinguables (couleur,
  forme, position) mais les amplitudes sont divisées par ~3 et la cadence
  passe de 60 à 30 images/s.
* **Taille du texte** réglable de 80 % à 180 %.
* Contrastes : fond `#080D14` à 82 % d'opacité derrière chaque texte, quelle
  que soit la couleur du bureau.

---

## 7. Performance

Mesures réelles (moteur raster Qt, Linux x86-64, Python 3.11, un seul cœur),
`grab()` = repaint **plein écran**, le pire cas possible :

| Résolution | v1.6.0 | v1.7.0 | gain |
|---|---|---|---|
| 1920 × 1080 | 18,9 ms | **1,6 ms** | ×12 |
| 2560 × 1440 | 23,4 ms | **3,2 ms** | ×7,4 |
| 3840 × 2160 | 36,9 ms | **9,8 ms** | ×3,8 |

En fonctionnement normal, le repaint est **limité aux quatre bandes** du halo
(`update(region)`), jamais au centre de l'écran :

| Résolution | écoute | réflexion | action | réponse |
|---|---|---|---|---|
| 1920 × 1080 | 0,69 ms | 1,96 ms | 1,37 ms | 1,10 ms |
| 2560 × 1440 | 1,75 ms | 2,56 ms | 1,61 ms | 1,52 ms |
| 3840 × 2160 | 3,01 ms | 4,94 ms | 3,35 ms | 3,28 ms |

**À l'état `hidden`, le coût est nul** : le minuteur d'animation est arrêté, la
fenêtre est masquée, aucune image n'est produite. C'est vérifié par un test
(`test_hidden_costs_nothing`).

Comment : les bandes lumineuses sont pré-rendues dans des `QPixmap` mis en
cache (4 à 5 pixmaps vivants, invalidés seulement sur changement de taille, de
thème ou d'épaisseur) ; chaque image ne fait plus que les blitter avec une
opacité. Les accents animés réutilisent un unique sprite radial. Les
changements d'état sont un fondu enchaîné entre deux jeux de bandes.

---

## 8. Architecture

```
src/audio.py, src/gemini_live.py        (threads backend, sans Qt)
        │  presence_hook · voice_hook · output_level_hook
        │  on_tool_start · on_tool_end · on_user_transcript · on_assistant_transcript
        ▼
UI/desktop/events.py    DESKTOP_EVENTS — pont thread-safe, sans Qt
        ▼
src/ui.py               InterfaceModeController — signal Qt (thread graphique)
        ▼
UI/desktop/overlay.py   DesktopOverlayController — machine d'états + minuteurs
        ▼
UI/desktop/overlay.py   DesktopOverlay — fenêtre traversante
        ├── UI/desktop/halo.py      HaloRenderer (pixmaps en cache)
        └── UI/desktop/widgets.py   StatusChip · TranscriptCard · ResponseCard
                                    ToolChip · AudioBars · ControlsBar
```

| Module | Qt ? | Rôle |
|---|---|---|
| `UI/desktop/state.py` | non | états, évènements, transitions, durées |
| `UI/desktop/config.py` | non | réglages sérialisables, migration |
| `UI/desktop/events.py` | non | pont backend → interface |
| `UI/desktop/design.py` | données | **toutes** les durées, courbes, opacités, profils |
| `UI/desktop/halo.py` | oui | rendu du halo |
| `UI/desktop/widgets.py` | oui | widgets du HUD |
| `UI/desktop/overlay.py` | oui | fenêtre + contrôleur |
| `UI/desktop_appearance_dialog.py` | oui | éditeur |
| `UI/screen_halo_overlay.py` | oui | façade de compatibilité 1.5.3 → 1.7.0 |

`state`, `config` et `events` n'importent **pas** PySide6 : le backend vocal
peut les utiliser depuis ses threads sans payer Qt, et ils se testent sans
écran. Un test le vérifie (`test_state_and_config_have_no_qt_dependency`).

`UI/screen_halo_overlay.ScreenHaloOverlay` et `build_presence_hook` gardent
exactement leur contrat 1.6.0 : aucun appelant existant n'a été réécrit, et les
tests de non-régression 1.5.3 continuent de s'exécuter — sur la nouvelle
implémentation.

### Journalisation

Logger `jarvis.desktop` : changements d'état, écran cible (nom, taille, DPI),
politique d'interaction, création de la fenêtre de contrôles, chargement de la
configuration, erreurs de rendu.

**Jamais de contenu privé.** Les transcriptions et les réponses ne sont
journalisées que par leur **taille** (`transcription desktop chars=42`). Un
test le garantit (`test_no_private_content_in_logs`).

---

## 9. Ce que le Desktop Mode ne fait pas

* Ce n'est **pas** un chatbot flottant : aucun historique, aucun fil de
  discussion, aucun stockage de messages dans l'interface.
* Il ne possède **aucune** logique de conversation : il lit ce que le backend
  lui donne et l'affiche quelques secondes.
* Il ne duplique **aucune** transcription : les textes viennent du pipeline du
  contexte conversationnel 1.6.0.
* Il ne touche jamais au backend : passer de Blob à Desktop et revenir ne tue
  aucun processus, ne vide ni le contexte, ni la mémoire, ni les réglages.

---

## 10. Dépannage

| Symptôme | Cause probable |
|---|---|
| Rien ne s'affiche | mode jeu actif (`should_suppress_visuals`), ou halo désactivé dans l'éditeur |
| Le halo reste allumé | état `follow_up` : il s'éteint seul au bout de 8,4 s |
| Les boutons ne réagissent pas | politique « Toujours traversable », ou contrôles désactivés |
| Le halo est sur le mauvais écran | il suit l'écran du curseur : déplacez la souris et rouvrez l'overlay |
| Trop lumineux | éditeur → *Force du halo* / *Épaisseur du halo* |
| Trop d'animation | éditeur → *Animations réduites* |

# Navigation, mise en page et thèmes

Code : `UI/dashboard/window.py`, `pages_view.py`, `grid_view.py`,
`layout_model.py`, `presets.py`, `theme.py`, `painting.py`.

## 1. Fenêtre

* Fenêtre Qt classique **redimensionnable** (minimum 560 × 420), géométrie
  et état (normal / maximisé / plein écran) persistés.
* **Plein écran** : `F11` ou bouton ⤢ ; `Échap` ou `F11` ramène à l'état
  précédent (normal **ou** maximisé, mémorisé avant le passage en plein écran).
* **États de taille** calculés sur la largeur de la fenêtre :

| État | Largeur fenêtre | Navigation | Grille (selon largeur de la zone de grille) |
|---|---|---|---|
| étroit | < 900 px | barre latérale réduite aux icônes, en-tête compact | 4 colonnes (< 760 px) |
| normal | 900 – 1479 px | barre latérale complète | 8 colonnes (< 1240 px) |
| large | ≥ 1480 px | barre latérale complète | 12 colonnes |

  Les états étroit/normal **ne modifient jamais** la mise en page
  enregistrée : ils en calculent une projection (`layout_model.reflow`) qui
  respecte les tailles minimales des widgets. Le mode édition travaille
  toujours sur la grille canonique de 12 colonnes.

* En-tête : emblème animé du thème, titre, page courante, **pastille d'état**
  (santé globale réelle · état du moteur · présence ; clic → page Système),
  boutons OnePage, édition, notifications (non lues), plein écran,
  paramètres, et boutons **Orbe** / **Desktop** (changent le mode de
  l'interface principale sans toucher au moteur ; le Dashboard reste ouvert).
* Pied de barre latérale : rappel du mode de fermeture en vigueur.

## 2. Pages et OnePage

Pages intégrées (non supprimables, renommables) : **Accueil**, **Travail**,
**Tâches**, **Mémoire**, **Système**, **Paramètres**. Pages personnalisées :
créer, renommer, réordonner, supprimer (page Paramètres → Mise en page ;
12 au maximum ; supprimer une page ne supprime aucune donnée métier).

**OnePage** (`Ctrl+O`, persisté) : toutes les pages empilées en sections
dans une seule vue défilante, avec les **mêmes** widgets et services (aucun
code dupliqué). Ordre : pages dans l'ordre de la barre latérale, sauf la page
**Système (diagnostics) placée en dernier**, la page Paramètres juste avant.
La barre latérale reste visible et fait défiler jusqu'à la section choisie.

* Construction **progressive** : une section par tour de boucle Qt
  (l'interface reste réactive pendant la construction).
* Seules les sections proches de la zone visible ont des widgets actifs
  (marge de 200 px) ; les autres sont désactivées (0 minuteur, 0 abonnement).

## 3. Mode édition

`Ctrl+E` ou bouton « ✎ Éditer ». Barre d'édition : **+ Ajouter un widget**
(bibliothèque filtrable par catégorie, affichant description, capacités,
dépendances et tailles ; un widget déjà présent au maximum sur la page est
grisé), **Widgets masqués** (réafficher), **Réinitialiser la page** (depuis
le préréglage courant), **Annuler**, **Enregistrer**.

Sur chaque cadre : déplacer (glisser l'en-tête), redimensionner (poignée en
bas à droite), ⚙ réglages de l'instance (formulaire généré depuis les
`SettingField`), ◌ masquer, ✕ retirer de la page. Les collisions sont
résolues automatiquement (`layout_model.settle/pack`), les tailles min/max
des specs sont respectées.

* **Enregistrer** (ou `Ctrl+E`) : la disposition devient le préréglage
  « Personnalisée » (instantané restaurable) et est persistée.
* **Annuler** (ou `Échap`) : restaure exactement l'état d'avant l'édition.
* Masquer / retirer **ne supprime jamais** de données métier : seul le
  placement change.

## 4. Préréglages

| Préréglage | Intention |
|---|---|
| Cockpit (défaut) | Vue d'ensemble : état, conversation et tâches au premier plan. |
| Productivité | Conversation, notes, documents, listes de tâches. |
| Diagnostic | Santé des composants, ressources, journal, routage des modèles. |
| Personnalisée | Instantané de la dernière disposition enregistrée par l'utilisateur. |

Un préréglage ne remplace que la disposition des pages **intégrées** ; les
titres/icônes personnalisés et les pages personnalisées sont conservés.
Revenir à « Personnalisée » restaure l'instantané (testé :
`tests/test_dashboard_config.py`, `tests/test_dashboard_window.py`).

## 5. Thèmes : trois identités

| Thème | Identité visuelle |
|---|---|
| **JARVIS** | Bleu nuit et cyan ; fond d'**anneaux concentriques** avec graduations ; panneaux arrondis à liseré lumineux et arcs d'angle ; titres en capitales espacées ; transition fondu + zoom. |
| **EDITH** | Graphite tactique, rouge et ambre ; fond de **grille de visée** ; panneaux à **coins chanfreinés** avec repères d'angle ; titres capitales en chasse fixe ; transition par glissement latéral. |
| **FRIDAY** | Violet profond, rose corail et turquoise ; fond d'**aurores** ; panneaux « verre » très arrondis à ombre douce ; typographie fine sans capitales ; transition en fondu ascendant. |

Personnalisation (page Paramètres → Apparence, appliquée **à chaud** et
persistée) : thème, couleur d'accent (ou celle du thème), intensité de la
lueur (0–100), transparence des panneaux (40–100 %), densité (compacte /
normale / confortable : espacements, police, hauteur de ligne).

## 6. Animations et accessibilité

| Niveau | Emblème | Fond animé | Transitions de page |
|---|---|---|---|
| Léger | fixe (0 minuteur) | fixe | aucune |
| Normal (défaut) | 15 i/s | fixe | 200 ms |
| Poussé | 30 i/s | 8 i/s (réagit à la voix) | 300 ms |

* « Respecter les préférences d'accessibilité du système » (activé par
  défaut) : si le système demande moins d'animations, le niveau effectif
  devient **Léger** (l'interface l'indique). Windows :
  `SPI_GETCLIENTAREAANIMATION` (« Afficher les animations dans Windows ») ;
  ailleurs ou en test : variable `JARVIS_REDUCED_MOTION=1/0`.
* Toutes les animations s'arrêtent quand la fenêtre est masquée ou réduite.
* Les cadres de widgets sont peints dans un pixmap mis en cache : le fond
  animé ne force jamais le redessin complet des panneaux.
* Les graphes (ressources, audio) n'affichent que des mesures réelles ; sans
  donnée, un état vide explicite est affiché.

Mesures de coût : voir [PROTOCOLE_DE_TEST.md](PROTOCOLE_DE_TEST.md#3-performance).

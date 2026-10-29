"""Dashboard de Jarvis — troisième interface (avec l'orbe et le Desktop Mode).

Organisation du paquet
----------------------
Modules **sans Qt** (testables sans interface, importables depuis tout thread) :

* ``config``          — préférences versionnées, migrations, validation, reprise ;
* ``layout_model``    — modèle de pages/placements + algorithmes de grille ;
* ``presets``         — mises en page prédéfinies (Cockpit, Productivité…) ;
* ``security``        — niveaux de sécurité des actions (1 / 2 / 3) ;
* ``notification_center`` — catégories, préférences, anti-doublons ;
* ``log_buffer``      — tampon circulaire de journal (secrets masqués) ;
* ``memory_service``, ``tasks_service``, ``notes_service``, ``diagnostics``,
  ``resources``, ``actions`` — couche de services au-dessus des systèmes
  EXISTANTS de Jarvis (mémoire SQLite, Task Manager, notes, psutil…).

Modules **Qt** : ``hub`` (état réactif), ``theme``/``painting`` (identités
JARVIS / EDITH / FRIDAY), ``widget_api``/``registry`` (contrat des widgets),
``frame``/``grid_view``/``pages_view``/``window`` (fenêtre), ``dialogs``,
``controller`` (cycle de vie, points d'entrée), ``widgets/`` (widgets).

Le nom affiché est centralisé ici : le changer ne demande qu'une ligne.
"""

from __future__ import annotations

#: Nom affiché de cette interface (fenêtre, menus, notifications). Unique
#: source : ne jamais écrire « Dashboard » en dur ailleurs dans le paquet.
DASHBOARD_NAME = "Dashboard"

#: Version du schéma de préférences (``dashboard_state.json``).
CONFIG_SCHEMA_VERSION = 1


def notification_title(title: str) -> str:
    """Titre d'une notification émise par le Dashboard (préfixe explicite)."""
    return f"{DASHBOARD_NAME} — {title}"

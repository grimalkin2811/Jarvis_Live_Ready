"""Routines préconfigurées de Jarvis.

Quatorze enchaînements prêts à l'emploi, installés automatiquement dans
``~/.jarvis/routines.json`` au premier lancement (voir
``routines.RoutineManager.install_presets``). L'utilisateur n'a rien à
configurer : il lance une routine à la voix (« lance le mode travail ») ou
depuis le menu radial de l'orbe, et l'active/désactive à la voix
(« active la routine réveil », « désactive la bonne nuit »).

Principes :

* **aucune configuration** : uniquement des outils de la liste blanche,
  sans clé API, sans chemin absolu, sans dépendance installée ;
* **aucune surprise** : les routines à déclenchement automatique
  (``schedule``) sont livrées **désactivées** — rien ne s'exécute tout seul
  tant que l'utilisateur ne les a pas activées ; les routines manuelles
  (sans planification) ne font rien tant qu'on ne les appelle pas ;
* **respect du fichier utilisateur** : l'installation n'écrase jamais une
  routine existante de même nom, et une préconfiguration supprimée ne
  revient pas au redémarrage (voir ``presets_removed`` dans le JSON).

Les étapes sont écrites dans la mini-syntaxe des routines (celle qu'on
dicte), validées par ``routines.parse_steps`` au moment de l'installation :
une préconfiguration invalide est ignorée et signalée, jamais installée
en silence.
"""

from __future__ import annotations

#: Routines livrées avec Jarvis.
#:
#: Chaque entrée :
#: * ``name``        — nom prononçable, unique ;
#: * ``description`` — ce qu'elle fait, dit simplement ;
#: * ``steps``       — mini-syntaxe des outils (validée à l'installation) ;
#: * ``schedule``    — planification optionnelle (vide = manuelle) ;
#: * ``enabled``     — état par défaut. Toujours ``False`` quand une
#:   planification est définie : l'automatique est un choix explicite.
PRESET_ROUTINES: list[dict] = [
    {
        "name": "mode travail",
        "description": "Ouvre VS Code, laisse 2 secondes au système puis met le volume à 30 %.",
        "steps": 'open_application(application="vscode"); wait(2); set_volume(30)',
        "schedule": "",
        "enabled": True,
    },
    {
        "name": "focus",
        "description": "Coupe le son et lance un minuteur de concentration de 25 minutes.",
        "steps": 'mute_audio(); set_timer(minutes=25, label="fin du focus")',
        "schedule": "",
        "enabled": True,
    },
    {
        "name": "réunion",
        "description": "Prépare une réunion : son coupé, bureau nettoyé, minuteur de 45 minutes.",
        "steps": 'mute_audio(); show_desktop(); set_timer(minutes=45, label="fin de la réunion")',
        "schedule": "",
        "enabled": True,
    },
    {
        "name": "pause café",
        "description": "Rétablit le son et lance une vraie pause de 5 minutes.",
        "steps": 'unmute_audio(); set_timer(minutes=5, label="fin de la pause")',
        "schedule": "",
        "enabled": True,
    },
    {
        "name": "capture",
        "description": "Prend une capture d'écran puis ouvre le dossier Images pour la retrouver.",
        "steps": 'take_screenshot(); open_folder(folder="images")',
        "schedule": "",
        "enabled": True,
    },
    {
        "name": "musique",
        "description": "Ouvre Spotify dans le navigateur et met le volume à 40 %.",
        "steps": 'open_website(site="spotify"); set_volume(40)',
        "schedule": "",
        "enabled": True,
    },
    {
        "name": "actualités",
        "description": "Ouvre la presse du jour dans le navigateur : France Info puis Le Monde.",
        "steps": 'open_website(site="france info"); wait(1); open_website(site="le monde")',
        "schedule": "",
        "enabled": True,
    },
    {
        "name": "bonjour",
        "description": "Briefing complet : heure, date, météo et dernières notes enregistrées.",
        "steps": "get_local_time(); get_local_date(); get_weather(); read_notes(limit=3)",
        "schedule": "",
        "enabled": True,
    },
    {
        "name": "bilan système",
        "description": "Bilan de santé du PC : Internet, batterie, disque et ressources machine.",
        "steps": "check_internet(); get_battery_status(); get_disk_usage(); get_system_info()",
        "schedule": "",
        "enabled": True,
    },
    {
        "name": "je pars",
        "description": "Sécurise le poste : stoppe la musique, coupe le son et verrouille la session.",
        "steps": "media_stop(); mute_audio(); lock_workstation()",
        "schedule": "",
        "enabled": True,
    },
    {
        "name": "je reviens",
        "description": "Rétablit le son et redonne l'heure de retour.",
        "steps": "unmute_audio(); get_local_time()",
        "schedule": "",
        "enabled": True,
    },
    {
        "name": "mode cinéma",
        "description": "Nettoie le bureau et monte le volume à 80 % pour la séance.",
        "steps": "show_desktop(); set_volume(80)",
        "schedule": "",
        "enabled": True,
    },
    {
        "name": "bonne nuit",
        "description": (
            "Fin de journée : stoppe la musique, volume et luminosité au minimum, "
            "puis verrouille la session. Désactivée par défaut : active-la pour la "
            "déclencher automatiquement chaque soir."
        ),
        "steps": "media_stop(); set_volume(10); set_brightness(10); wait(2); lock_workstation()",
        "schedule": "tous les jours à 23h30",
        "enabled": False,
    },
    {
        "name": "réveil",
        "description": (
            "Réveil en douceur : rétablit le son, volume à 20 % et ouvre les actualités. "
            "Désactivée par défaut : active-la pour un déclenchement automatique."
        ),
        "steps": 'unmute_audio(); set_volume(20); open_website(site="france info")',
        "schedule": "tous les jours à 7h30",
        "enabled": False,
    },
]

"""Launcher de Jarvis — programme stable chargé de lancer Jarvis et de le
mettre à jour.

Organisation :

* ``launcher/core.py`` : logique métier (validation, version, mise à jour,
  lancement), bibliothèque standard uniquement ;
* ``launcher/gui.py`` : interface graphique PySide6 (tâches de fond en
  ``QThread``, aucune logique métier dupliquée) ;
* ``launcher/main.py`` : point d'entrée, GUI par défaut sans arguments,
  console sinon (scripts, diagnostic, CI).

Le launcher remplace l'application sans se remplacer lui-même : il reste
découplé du reste de Jarvis (aucune dépendance vers l'audio, Gemini ou
openwakeword).
"""

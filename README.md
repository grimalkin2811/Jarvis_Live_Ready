# Jarvis Live Ready

Assistant vocal Windows prêt à tester avec Gemini Live.

## Démarrage
1. Décompresse ce ZIP.
2. Lance `setup.bat`.
3. Entre ton nom et ta clé Gemini API.
4. Une fois terminé, lance `Jarvis.bat`.

## Interface graphique (PySide6)

Jarvis propose désormais une interface reprenant l'orbe morphing de `grimalkin2811/Jarvis` :

| Commande | Effet |
|---|---|
| `Jarvis.bat` | Assistant headless (console), comme avant. |
| `Jarvis.bat --ui` | Orbe morphing interactif + menus radiaux + assistant vocal. |
| `Jarvis.bat --desktop` | Overlay halo plein écran (transparent aux clics) reflétant l'état : écoute / parole / veille. |

En mode `--ui`, survole le bord de l'écran pour déplier les menus radiaux
(Voice, System, Memory, Appearance). `Échap` pour quitter.

## Fonctions
- Questions/réponses vocales normales
- Ouvrir une application
- Fermer une application
- Régler le volume
- Augmenter le volume
- Baisser le volume
- Couper le son
- Rétablir le son
- Donner l'heure
- Donner la date
- Ouvrir un site
- Rechercher sur le Web

Les applications et sites sont volontairement placés sur une liste blanche dans `src/tools.py`.

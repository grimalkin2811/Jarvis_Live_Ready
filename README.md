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

En mode `--ui`, survole les bords de l'orbe pour déplier les menus radiaux
(Voice, System, Memory, Appearance) et interagis directement avec les réglages :

| Contrôle | Interaction |
|---|---|
| **Toggles** | Clic pour activer/désactiver (indicateur vert = actif). |
| **Sliders** (TTS, Vitesse, Hotword, Transparence) | Glisse à la souris ou molette pour ajuster la valeur ; une barre affiche le niveau. |
| **Options** (Voice Select, Shortcuts) | Clic ou molette pour parcourir les choix. |
| **Response Mode** | Clic pour changer le mode de réponse (injecté dans le prompt Gemini). |
| **Mic Toggle** | Coupe/rétablit le micro en direct. |
| **Audio Test** | Émet un bip de test. |

Les réglages sont conservés dans `UI/menu_state.json` au redémarrage.
`Échap` pour quitter.

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

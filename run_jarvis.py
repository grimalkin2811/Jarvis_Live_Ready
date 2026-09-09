"""Point d'entrée de l'application compilée (PyInstaller).

`src/main.py` utilise des imports relatifs propres au paquet ``src`` ; en tant
de script top-level pour PyInstaller, on passe donc par ce petit module qui
importe le paquet. Invoqué aussi en développement : ``python run_jarvis.py``.
"""

from src.main import main

if __name__ == "__main__":
    raise SystemExit(main())

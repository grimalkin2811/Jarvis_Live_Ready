"""Déclencheurs cachés des Protocoles : codes tapés et gestes secrets.

Ce module ne contient **que** de la logique pure (aucun import Qt), ce qui le
rend directement testable. L'interface s'en sert pour reconnaître :

* un **code tapé** au clavier, sans champ de saisie ni menu — on écrit
  simplement ``wakeup`` (ou ``jarvis``) pendant que l'orbe a le focus ;
* un **geste secret** — trois clics rapides au cœur de l'orbe.

Les codes expirent : une lettre isolée tapée il y a dix secondes ne doit pas
participer à un code. C'est ce qui évite les déclenchements fantômes.
"""

from __future__ import annotations

import time
import unicodedata

#: Codes reconnus → protocole lancé. Le plus long l'emporte en cas de suffixe
#: commun, ce qui permet à « wakeup » et « wake » de coexister sereinement.
#: Aucun code ne commence par « m », « s » ou « d » : ce sont les raccourcis
#: d'une lettre déjà en place (micro, stop, debug), qui gardent la priorité.
SECRET_CODES: dict[str, str] = {
    "wakeup": "wake_up",
    "jarvis": "wake_up",
    "reveil": "wake_up",
    "bilan": "diagnostic",
    "focus": "focus",
    "veille": "stand_down",
}

#: Au-delà de ce délai entre deux frappes, la saisie en cours est oubliée.
KEY_TIMEOUT_SECONDS = 1.6

#: Nombre de clics et fenêtre de temps du geste secret.
GESTURE_CLICKS = 3
GESTURE_WINDOW_SECONDS = 1.2

#: Rayon (en fraction du rayon de l'orbe) considéré comme « le cœur ».
GESTURE_CORE_RATIO = 0.55

_MAX_BUFFER = max(len(code) for code in SECRET_CODES)


def _fold(char: str) -> str:
    """Minuscule sans accent : « É » et « e » comptent pareil."""
    text = unicodedata.normalize("NFKD", str(char or ""))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return text.lower()


class SecretCodeDetector:
    """Reconnaît un code tapé lettre à lettre.

    ``feed('w'), feed('a'), ...`` renvoie ``None`` puis, à la dernière lettre,
    l'identifiant du protocole à jouer. Le tampon est vidé après un succès
    pour qu'un même code puisse être retapé aussitôt.
    """

    def __init__(self, codes: dict[str, str] | None = None,
                 timeout: float = KEY_TIMEOUT_SECONDS) -> None:
        self.codes = dict(codes or SECRET_CODES)
        self.timeout = float(timeout)
        self._buffer = ""
        self._last_time = 0.0
        self._max_len = max((len(code) for code in self.codes), default=_MAX_BUFFER)

    @property
    def buffer(self) -> str:
        return self._buffer

    def reset(self) -> None:
        self._buffer = ""
        self._last_time = 0.0

    def is_partial(self) -> bool:
        """Vrai si la saisie en cours peut encore devenir un code.

        L'interface s'en sert pour **suspendre** ses raccourcis d'une lettre
        (``m`` micro, ``s`` stop, ``d`` debug) le temps qu'un code s'écrive :
        taper « jarvis » ne doit pas couper le micro au passage, alors qu'un
        « s » isolé garde son rôle habituel.
        """
        if not self._buffer:
            return False
        for start in range(len(self._buffer)):
            fragment = self._buffer[start:]
            if any(code.startswith(fragment) for code in self.codes):
                return True
        return False

    def feed(self, char: str, now: float | None = None) -> str | None:
        """Ajoute une frappe et renvoie le protocole déclenché, ou ``None``."""
        letter = _fold(char)
        if len(letter) != 1 or not letter.isalpha():
            # Une touche non alphabétique (espace, flèche…) coupe la saisie :
            # « w a k e » puis Entrée ne doit rien déclencher plus tard.
            self.reset()
            return None

        moment = time.monotonic() if now is None else float(now)
        if self._buffer and (moment - self._last_time) > self.timeout:
            self._buffer = ""
        self._last_time = moment

        self._buffer = (self._buffer + letter)[-self._max_len:]

        # Le code le plus long gagne : « reveil » plutôt qu'un suffixe court.
        for code in sorted(self.codes, key=len, reverse=True):
            if self._buffer.endswith(code):
                self.reset()
                return self.codes[code]
        return None


class SecretGestureDetector:
    """Reconnaît trois clics rapides au cœur de l'orbe."""

    def __init__(self, clicks: int = GESTURE_CLICKS,
                 window: float = GESTURE_WINDOW_SECONDS) -> None:
        self.clicks = int(clicks)
        self.window = float(window)
        self._times: list[float] = []

    def reset(self) -> None:
        self._times.clear()

    def feed(self, distance: float, radius: float,
             now: float | None = None) -> bool:
        """``distance`` = écart au centre, ``radius`` = rayon courant de l'orbe."""
        moment = time.monotonic() if now is None else float(now)
        if radius <= 0 or distance > radius * GESTURE_CORE_RATIO:
            self.reset()
            return False

        self._times = [t for t in self._times if (moment - t) <= self.window]
        self._times.append(moment)
        if len(self._times) >= self.clicks:
            self.reset()
            return True
        return False

"""Insertion de texte dans le champ actif, au niveau du système.

Le texte est saisi à l'emplacement du curseur. Le plan de frappe ne contient
jamais de sélection globale, de suppression ni de remplacement du champ.

Sous Windows, la voie principale est ``SendInput`` avec ``KEYEVENTF_UNICODE``
(accents, pas de presse-papiers). Les textes très longs, ou un échec de
l'injection, basculent sur un collage Ctrl+V après sauvegarde puis restauration
du presse-papiers. Hors Windows, l'action échoue proprement.
"""

from __future__ import annotations

import os
import sys
import time
from dataclasses import dataclass

# Au-delà, le collage (presse-papiers préservé) est plus fiable que des milliers
# de frappes. En dessous, on n'y touche pas.
UNICODE_LIMIT = 4000
UNICODE_CHUNK = 24
UNICODE_CHUNK_DELAY_S = 0.008
CLIPBOARD_PAUSE_S = 0.20
PRE_TYPE_DELAY_S = 0.04

VK_RETURN = 0x0D
VK_TAB = 0x09
VK_CONTROL = 0x11
VK_V = 0x56
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004

_DESTRUCTIVE = {"backspace", "delete", "select_all", "ctrl+a", "ctrl+v", "ctrl", "alt"}


@dataclass(frozen=True)
class InputEvent:
    """Événement logique d'insertion (indépendant de l'OS)."""

    kind: str  # "unicode" ou "key"
    value: str


@dataclass(frozen=True)
class KeyUnit:
    """Unité envoyée à Windows : caractère Unicode ou touche virtuelle."""

    kind: str  # "unicode", "vk_down", "vk_up"
    value: int


def plan_insertion(text: str) -> list[InputEvent]:
    """Plan d'insertion pure : caractères, Entrée pour les paragraphes, Tab.

    ``\\r\\n`` ne produit qu'une touche Entrée. Aucune touche destructive.
    """
    events: list[InputEvent] = []
    normalized = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    for char in normalized:
        if char == "\n":
            events.append(InputEvent("key", "enter"))
        elif char == "\t":
            events.append(InputEvent("key", "tab"))
        elif ord(char) < 32 or char == "\x7f":
            continue
        else:
            events.append(InputEvent("unicode", char))
    assert_plan_is_insert_only(events)
    return events


def assert_plan_is_insert_only(events: list[InputEvent]) -> None:
    """Refuse un plan qui pourrait effacer ou sélectionner le champ."""
    for event in events:
        if event.kind == "unicode":
            if not event.value or event.value in "\n\r\b\x7f":
                raise ValueError("caractère de contrôle interdit dans le plan d'insertion")
            continue
        if event.kind == "key" and event.value in {"enter", "tab"}:
            continue
        raise ValueError(f"plan d'insertion non sûr : {event.kind}:{event.value}")


def events_to_text(events: list[InputEvent]) -> str:
    """Reconstruit le texte qu'un champ recevrait en appliquant le plan."""
    parts: list[str] = []
    for event in events:
        if event.kind == "unicode":
            parts.append(event.value)
        elif event.value == "enter":
            parts.append("\n")
        elif event.value == "tab":
            parts.append("\t")
    return "".join(parts)


def apply_at_cursor(existing: str, cursor: int, events: list[InputEvent]) -> str:
    """Insère le plan dans un champ simulé, sans remplacer le texte déjà là."""
    cursor = max(0, min(int(cursor), len(existing)))
    inserted = events_to_text(events)
    return existing[:cursor] + inserted + existing[cursor:]


def encode_plan(events: list[InputEvent]) -> list[KeyUnit]:
    """Encode le plan en unités Windows. Entrée = VK_RETURN, pas un ``\\n`` Unicode."""
    assert_plan_is_insert_only(events)
    units: list[KeyUnit] = []
    for event in events:
        if event.kind == "key" and event.value == "enter":
            units.append(KeyUnit("vk_down", VK_RETURN))
            units.append(KeyUnit("vk_up", VK_RETURN))
            continue
        if event.kind == "key" and event.value == "tab":
            units.append(KeyUnit("vk_down", VK_TAB))
            units.append(KeyUnit("vk_up", VK_TAB))
            continue
        # Unités UTF-16 : les caractères hors BMP (emoji) partent en surrogate pair.
        encoded = event.value.encode("utf-16-le")
        for offset in range(0, len(encoded), 2):
            code = int.from_bytes(encoded[offset : offset + 2], "little")
            units.append(KeyUnit("unicode", code))
    return units


def encode_ctrl_v() -> list[KeyUnit]:
    """Ctrl+V pressé puis relâché (le modificateur ne reste pas enfoncé)."""
    return [
        KeyUnit("vk_down", VK_CONTROL),
        KeyUnit("vk_down", VK_V),
        KeyUnit("vk_up", VK_V),
        KeyUnit("vk_up", VK_CONTROL),
    ]


def windows_newlines(text: str) -> str:
    """Normalise les sauts de ligne pour un collage Windows."""
    return str(text or "").replace("\r\n", "\n").replace("\r", "\n").replace("\n", "\r\n")


class MemoryClipboard:
    """Presse-papiers en mémoire, pour les tests et le secours hors Win32."""

    def __init__(self, initial: str = "") -> None:
        self.value = initial
        self.fail_capture = False
        self.fail_restore = False

    def capture(self):
        if self.fail_capture:
            raise OSError("presse-papiers verrouillé")
        return self.value

    def set_text(self, text: str) -> None:
        self.value = text

    def restore(self, snapshot) -> None:
        if self.fail_restore:
            raise OSError("restauration impossible")
        self.value = snapshot


def insert_via_clipboard(text: str, clipboard, send_paste, sleep=time.sleep, pause: float = CLIPBOARD_PAUSE_S) -> dict:
    """Colle ``text`` puis restaure le presse-papiers, même si le collage échoue."""
    try:
        snapshot = clipboard.capture()
    except Exception as exc:
        return {
            "success": False,
            "reason": "clipboard_unavailable",
            "error": f"Presse-papiers inaccessible : {exc}",
            "clipboard_used": False,
            "clipboard_restored": True,
        }
    pasted = False
    paste_error = ""
    try:
        clipboard.set_text(windows_newlines(text))
        send_paste()
        pasted = True
        if pause:
            sleep(pause)
    except Exception as exc:
        paste_error = str(exc)
    restore_error = ""
    try:
        clipboard.restore(snapshot)
    except Exception as exc:
        restore_error = str(exc)
    if not pasted:
        return {
            "success": False,
            "reason": "paste_failed",
            "error": paste_error or "Collage impossible.",
            "clipboard_used": True,
            "clipboard_restored": not restore_error,
            "clipboard_warning": restore_error,
        }
    return {
        "success": True,
        "reason": "pasted",
        "method": "clipboard",
        "clipboard_used": True,
        "clipboard_restored": not restore_error,
        "clipboard_warning": restore_error,
    }


class ActiveFieldInserter:
    """Orchestre focus, frappe Unicode et secours presse-papiers.

    ``keyboard.send_units`` et ``focus`` sont injectables : les tests n'envoient
    aucune touche réelle.
    """

    def __init__(
        self,
        *,
        platform: str | None = None,
        keyboard=None,
        clipboard=None,
        focus=None,
        sleep=time.sleep,
        unicode_limit: int = UNICODE_LIMIT,
    ) -> None:
        self.platform = platform if platform is not None else os.name
        self.keyboard = keyboard if keyboard is not None else Win32Keyboard(sleep=sleep)
        self.clipboard = clipboard
        self.focus = focus if focus is not None else Win32Focus()
        self.sleep = sleep
        self.unicode_limit = unicode_limit

    def write(self, text: str) -> dict:
        if self.platform != "nt":
            return _fail(
                "unsupported_platform",
                "L'écriture dans le champ actif n'est disponible que sous Windows.",
            )
        try:
            if not self.focus.has_foreground():
                return _fail("inaccessible", "Champ actif inaccessible.")
            if self.focus.is_own_process():
                return _fail(
                    "own_process",
                    "Le curseur est dans Jarvis. Place-le dans l'application cible.",
                )
        except Exception as exc:
            return _fail("inaccessible", f"Champ actif inaccessible : {exc}")

        plan = plan_insertion(text)
        use_clipboard = len(text) > self.unicode_limit
        unicode_error = ""
        if not use_clipboard:
            try:
                ok, inserted = self.keyboard.send_units(encode_plan(plan))
            except Exception as exc:
                ok, inserted = False, 0
                unicode_error = str(exc)
            if ok:
                return {
                    "success": True,
                    "reason": "typed",
                    "method": "unicode",
                    "clipboard_used": False,
                    "clipboard_restored": True,
                    "caracteres": len(text),
                }
            # Une frappe partielle a déjà modifié le champ : ne pas recoller
            # tout le texte par-dessus.
            if inserted:
                return _fail(
                    "injection_failed",
                    unicode_error or "Injection clavier interrompue.",
                    partial=True,
                )
        if self.clipboard is None:
            return _fail(
                "injection_failed",
                unicode_error or "Injection clavier impossible.",
            )
        result = insert_via_clipboard(
            text,
            self.clipboard,
            self.keyboard.send_paste,
            sleep=self.sleep,
        )
        if result.get("success"):
            result["caracteres"] = len(text)
            result["method"] = "clipboard"
        elif result.get("reason") == "clipboard_unavailable":
            result["reason"] = "injection_failed"
        return result


def _fail(reason: str, error: str, **extra) -> dict:
    payload = {"success": False, "reason": reason, "error": error, "clipboard_used": False}
    payload.update(extra)
    return payload


class RecordingKeyboard:
    """Clavier de test : enregistre les unités, n'appelle pas le système."""

    def __init__(self, fail: bool = False, partial: int = 0) -> None:
        self.units: list[KeyUnit] = []
        self.pastes = 0
        self.fail = fail
        self.partial = partial

    def send_units(self, units: list[KeyUnit]) -> tuple[bool, int]:
        if self.fail:
            return False, 0
        self.units.extend(units)
        inserted = sum(1 for unit in units if unit.kind == "unicode")
        if self.partial:
            return False, self.partial
        return True, inserted

    def send_paste(self) -> None:
        self.pastes += 1


class ExternalFocus:
    """Focus de test : une autre application détient le curseur."""

    def __init__(self, foreground: bool = True, own: bool = False) -> None:
        self.foreground = foreground
        self.own = own

    def has_foreground(self) -> bool:
        return self.foreground

    def is_own_process(self) -> bool:
        return self.own


class Win32Focus:
    """Fenêtre au premier plan, via user32. Inerte hors Windows."""

    def has_foreground(self) -> bool:
        user32 = _user32()
        return bool(user32.GetForegroundWindow())

    def is_own_process(self) -> bool:
        user32 = _user32()
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return False
        import ctypes
        from ctypes import wintypes

        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        return int(pid.value) == os.getpid()


class Win32Keyboard:
    """Envoi réel via SendInput. N'est appelé que sous Windows."""

    def __init__(self, sleep=time.sleep) -> None:
        self.sleep = sleep

    def send_units(self, units: list[KeyUnit]) -> tuple[bool, int]:
        if not units:
            return True, 0
        release_modifiers()
        if PRE_TYPE_DELAY_S:
            self.sleep(PRE_TYPE_DELAY_S)
        sent_chars = 0
        batch: list[KeyUnit] = []
        logical = 0
        for unit in units:
            batch.append(unit)
            if unit.kind == "unicode":
                logical += 1
            if logical >= UNICODE_CHUNK:
                ok, count = _send_unit_batch(batch)
                sent_chars += count
                if not ok:
                    return False, sent_chars
                batch = []
                logical = 0
                if UNICODE_CHUNK_DELAY_S:
                    self.sleep(UNICODE_CHUNK_DELAY_S)
        if batch:
            ok, count = _send_unit_batch(batch)
            sent_chars += count
            if not ok:
                return False, sent_chars
        return True, sent_chars

    def send_paste(self) -> None:
        ok, _count = self.send_units(encode_ctrl_v())
        if not ok:
            raise OSError("Impossible d'envoyer Ctrl+V.")


class Win32Clipboard:
    """Presse-papiers Win32 : capture des formats HGLOBAL, puis restauration."""

    def capture(self):
        return _clipboard_snapshot()

    def set_text(self, text: str) -> None:
        _clipboard_set_text(text)

    def restore(self, snapshot) -> None:
        _clipboard_restore(snapshot)


def platform_inserter() -> ActiveFieldInserter:
    """Inserteur réel : Win32 sous Windows, refus propre ailleurs."""
    if os.name != "nt":
        return ActiveFieldInserter(platform=os.name, keyboard=RecordingKeyboard(), focus=ExternalFocus(False))
    return ActiveFieldInserter(
        platform="nt",
        keyboard=Win32Keyboard(),
        clipboard=Win32Clipboard(),
        focus=Win32Focus(),
    )


def _user32():
    import ctypes

    return ctypes.WinDLL("user32", use_last_error=True)


def _kernel32():
    import ctypes

    return ctypes.WinDLL("kernel32", use_last_error=True)


def _input_types():
    """Structures INPUT dont la taille doit être 40 octets en 64 bits, 28 en 32 bits."""
    import ctypes
    from ctypes import wintypes

    ulong_ptr = ctypes.c_ulonglong if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_ulong

    class KEYBDINPUT(ctypes.Structure):
        _fields_ = [
            ("wVk", wintypes.WORD),
            ("wScan", wintypes.WORD),
            ("dwFlags", wintypes.DWORD),
            ("time", wintypes.DWORD),
            ("dwExtraInfo", ulong_ptr),
        ]

    class MOUSEINPUT(ctypes.Structure):
        _fields_ = [
            ("dx", wintypes.LONG),
            ("dy", wintypes.LONG),
            ("mouseData", wintypes.DWORD),
            ("dwFlags", wintypes.DWORD),
            ("time", wintypes.DWORD),
            ("dwExtraInfo", ulong_ptr),
        ]

    class HARDWAREINPUT(ctypes.Structure):
        _fields_ = [
            ("uMsg", wintypes.DWORD),
            ("wParamL", wintypes.WORD),
            ("wParamH", wintypes.WORD),
        ]

    class INPUT(ctypes.Structure):
        class _U(ctypes.Union):
            _fields_ = [
                ("ki", KEYBDINPUT),
                ("mi", MOUSEINPUT),
                ("hi", HARDWAREINPUT),
            ]

        _anonymous_ = ("u",)
        _fields_ = [("type", wintypes.DWORD), ("u", _U)]

    expected = 40 if ctypes.sizeof(ctypes.c_void_p) == 8 else 28
    if ctypes.sizeof(INPUT) != expected:
        raise OSError(
            f"Structure INPUT inattendue ({ctypes.sizeof(INPUT)} octets, attendu {expected})."
        )
    return INPUT, KEYBDINPUT


def _send_unit_batch(units: list[KeyUnit]) -> tuple[bool, int]:
    import ctypes
    from ctypes import wintypes

    input_cls, _keybd = _input_types()
    user32 = _user32()
    user32.SendInput.argtypes = (wintypes.UINT, ctypes.POINTER(input_cls), ctypes.c_int)
    user32.SendInput.restype = wintypes.UINT
    events = []
    chars = 0
    for unit in units:
        events.extend(_units_to_inputs(unit, input_cls))
        if unit.kind == "unicode":
            chars += 1
    if not events:
        return True, 0
    array = (input_cls * len(events))(*events)
    inserted = int(user32.SendInput(len(events), array, ctypes.sizeof(input_cls)))
    if inserted != len(events):
        # Caractères effectivement partis : chaque Unicode = down+up.
        return False, min(chars, inserted // 2)
    return True, chars


def _units_to_inputs(unit: KeyUnit, input_cls):
    input_keyboard = 1
    if unit.kind == "unicode":
        return [
            _make_input(input_cls, input_keyboard, 0, unit.value, KEYEVENTF_UNICODE),
            _make_input(
                input_cls,
                input_keyboard,
                0,
                unit.value,
                KEYEVENTF_UNICODE | KEYEVENTF_KEYUP,
            ),
        ]
    flags = KEYEVENTF_KEYUP if unit.kind == "vk_up" else 0
    return [_make_input(input_cls, input_keyboard, unit.value, 0, flags)]


def _make_input(input_cls, input_type: int, vk: int, scan: int, flags: int):
    event = input_cls()
    event.type = input_type
    event.ki.wVk = vk
    event.ki.wScan = scan
    event.ki.dwFlags = flags
    event.ki.time = 0
    event.ki.dwExtraInfo = 0
    return event


def release_modifiers() -> None:
    """Relâche Ctrl/Alt/Shift/Win pour qu'ils ne transforment pas la frappe."""
    if os.name != "nt":
        return
    try:
        import ctypes
        from ctypes import wintypes

        input_cls, _keybd = _input_types()
        user32 = _user32()
        user32.SendInput.argtypes = (wintypes.UINT, ctypes.POINTER(input_cls), ctypes.c_int)
        user32.SendInput.restype = wintypes.UINT
        modifiers = (0x10, 0x11, 0x12, 0x5B, 0x5C, 0xA0, 0xA1, 0xA2, 0xA3, 0xA4, 0xA5)
        events = [_make_input(input_cls, 1, vk, 0, KEYEVENTF_KEYUP) for vk in modifiers]
        array = (input_cls * len(events))(*events)
        user32.SendInput(len(events), array, ctypes.sizeof(input_cls))
    except Exception:
        return


def _open_clipboard(retries: int = 8) -> bool:
    user32 = _user32()
    for _ in range(retries):
        if user32.OpenClipboard(0):
            return True
        time.sleep(0.02)
    return False


def _clipboard_snapshot():
    import ctypes

    user32 = _user32()
    kernel32 = _kernel32()
    if not _open_clipboard():
        raise OSError("presse-papiers verrouillé")
    captured = []
    try:
        fmt = 0
        while True:
            fmt = int(user32.EnumClipboardFormats(fmt))
            if not fmt:
                break
            handle = user32.GetClipboardData(fmt)
            if not handle:
                continue
            size = int(kernel32.GlobalSize(handle) or 0)
            if not size or size > 16 * 1024 * 1024:
                continue
            pointer = kernel32.GlobalLock(handle)
            if not pointer:
                continue
            try:
                captured.append((fmt, ctypes.string_at(pointer, size)))
            finally:
                kernel32.GlobalUnlock(handle)
    finally:
        user32.CloseClipboard()
    return captured


def _clipboard_set_text(text: str) -> None:
    user32 = _user32()
    if not _open_clipboard():
        raise OSError("presse-papiers verrouillé")
    try:
        if not user32.EmptyClipboard():
            raise OSError("impossible de vider le presse-papiers")
        _set_format(13, (str(text) + "\0").encode("utf-16-le"))  # CF_UNICODETEXT
    finally:
        user32.CloseClipboard()


def _clipboard_restore(snapshot) -> None:
    user32 = _user32()
    if not _open_clipboard():
        raise OSError("presse-papiers verrouillé")
    try:
        user32.EmptyClipboard()
        for fmt, data in snapshot or []:
            _set_format(int(fmt), data)
    finally:
        user32.CloseClipboard()


def _set_format(fmt: int, data: bytes) -> None:
    import ctypes
    from ctypes import wintypes

    user32 = _user32()
    kernel32 = _kernel32()
    kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
    handle = kernel32.GlobalAlloc(0x0002, len(data))  # GMEM_MOVEABLE
    if not handle:
        raise OSError("allocation presse-papiers impossible")
    pointer = kernel32.GlobalLock(handle)
    if not pointer:
        kernel32.GlobalFree(handle)
        raise OSError("verrouillage presse-papiers impossible")
    try:
        ctypes.memmove(pointer, data, len(data))
    finally:
        kernel32.GlobalUnlock(handle)
    user32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
    user32.SetClipboardData.restype = wintypes.HANDLE
    if not user32.SetClipboardData(fmt, handle):
        kernel32.GlobalFree(handle)
        raise OSError("SetClipboardData a échoué")


def running_on_windows() -> bool:
    return sys.platform.startswith("win")

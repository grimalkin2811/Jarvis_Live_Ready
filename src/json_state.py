"""Persistance JSON locale des fichiers d'état (menu, apparence, système, modes).

Quatre modules réimplémentaient la même mécanique : lire un petit fichier JSON
en tolérant son absence ou sa corruption (valeur par défaut silencieuse),
l'écrire en créant le dossier au besoin. Ce module centralise UNIQUEMENT cette
mécanique d'entrées/sorties ; chaque appelant conserve son schéma, sa
validation champ par champ et ses valeurs par défaut.

Lecture partagée : sous Windows, un lecteur qui ouvre le fichier « à
l'ancienne » (sans partage ``FILE_SHARE_DELETE``) **interdit tout renommage
par-dessus**, y compris par ``os.replace`` et par le remplacement POSIX
(sondages ``_probe5``/``_probe13`` : WinError 5/32 tant que le handle vit).
Tous les lecteurs de l'application passent donc par ``open_text``/``read_text``
qui ouvrent avec ``FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE`` :
un lecteur ne bloque plus jamais l'écriture, et les handles déjà ouverts
continuent de voir l'ancien contenu pendant que le chemin pointe vers le
nouveau — le chemin de destination n'a aucun instantané d'absence.

Écriture atomique : le contenu est écrit dans un temporaire unique du même
dossier, persisté (``flush`` + ``fsync``), puis remplacé — sous Windows par
``SetFileInformationByHandle(FileRenameInfoEx, REPLACE_IF_EXISTS |
FILE_FLAG_POSIX_SEMANTICS)`` (sondé ``_probe12`` : 180 lectures concrètes sans
trou ni erreur), avec repli ``os.replace`` si l'API est indisponible. En cas
d'échec la destination est **toujours préservée** (le renommage est atomique :
il réussit entièrement ou ne change rien) et le temporaire est **toujours
nettoyé**. Une interruption en cours d'écriture ne peut donc plus laisser un
fichier d'état tronqué.

Les verrous incompatibles (lecteur externe sans partage ``DELETE``) ne sont
JAMAIS résolus par la répétition tant que le handle existe : ``atomic_replace``
réessaie les seules erreurs transitoires avec un backoff borné (~0,3 s), puis
échoue en préservant la destination.

``load_dict``/``save_dict`` ne propagent aucune exception : un état local
illisible ou non inscriptible ne doit jamais empêcher Jarvis de fonctionner
(l'appelant garde ses valeurs en mémoire). ``read_text``/``write_text_atomic``
lèvent, eux, ``OSError`` normalement — briques de base pour les appelants qui
DOIVENT échouer bruyamment (``src.settings``) ou diagnostiquer
(``UI.dashboard.config``, ``src.routines``).
"""

from __future__ import annotations

import errno
import json
import os
import tempfile
import time
from typing import Any

IS_WINDOWS = os.name == "nt"

if IS_WINDOWS:  # pragma: no cover - exercé seulement sous Windows
    import ctypes
    import msvcrt

    #: ``CreateFileW`` : accès lecture seule.
    _GENERIC_READ = 0x80000000
    #: ``CreateFileW`` : accès exigés pour renommer la source.
    _DELETE = 0x00010000
    _FILE_READ_ATTRIBUTES = 0x0080
    #: Partage lecture + écriture + **suppression**. C'est le partage
    #: ``FILE_SHARE_DELETE`` qui autorise un remplacement atomique par-dessus
    #: pendant que ce handle est ouvert.
    _FILE_SHARE_ALL = 0x1 | 0x2 | 0x4
    _OPEN_EXISTING = 3
    _FILE_ATTRIBUTE_NORMAL = 0x80
    _INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

    #: ``FILE_INFORMATION_CLASS.FileRenameInfoEx`` (Windows 10 1607+).
    _FileRenameInfoEx = 22
    #: ``FILE_RENAME_INFO.Flags`` : remplacer si existe + sémantique POSIX.
    _RENAME_REPLACE_IF_EXISTS = 0x00000001
    _RENAME_POSIX_SEMANTICS = 0x00000002

    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _kernel32.CreateFileW.argtypes = [
        ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p,
        ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p,
    ]
    _kernel32.CreateFileW.restype = ctypes.c_void_p
    _kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    _kernel32.CloseHandle.restype = ctypes.c_bool
    _kernel32.SetFileInformationByHandle.argtypes = [
        ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32,
    ]
    _kernel32.SetFileInformationByHandle.restype = ctypes.c_bool

#: Codes Windows de verrou **transitoires** : accès refusé (5), partage refusé
#: (32), verrou (33). Renvoyés quand un handle incompatible (lecteur externe,
#: antivirus) tient le fichier : seule sa fermeture les résout — la
#: répétition, jamais à elle seule (sondage ``_probe13``).
_TRANSIENT_WINERRORS = frozenset({5, 32, 33})
#: ``errno`` POSIX transitoires pour ``rename`` (fichier occupé).
_TRANSIENT_ERRNOS = frozenset({errno.EBUSY, errno.ETXTBSY})
#: Codes Windows signifiant « ``FileRenameInfoEx`` non supporté » :
#: fonction non valide (1), paramètre invalide (87), non supporté (50),
#: appel non implémenté (120). Le repli ``os.replace`` prend alors le relais.
_UNSUPPORTED_WINERRORS = frozenset({1, 87, 50, 120})

#: Retries bornés pour ``atomic_replace`` : 4 essais, ~0,22 s de backoff au
#: total. Au-delà, l'échec est durable (handle incompatible ou permission
#: réelle) : on préserve la destination et on nettoie le temporaire.
_REPLACE_BACKOFF = (0.02, 0.05, 0.15)


def _is_transient(exc: OSError) -> bool:
    """Vrai si ``exc`` correspond à un verrou susceptible de se libérer.

    Seules ces erreurs sont réessayées ; tout le reste (fichier absent,
    destination invalide, permission stable) est permanent et remonte
    immédiatement sans gaspiller de temps.
    """
    winerror = getattr(exc, "winerror", None)
    if winerror is not None:
        return winerror in _TRANSIENT_WINERRORS
    return getattr(exc, "errno", None) in _TRANSIENT_ERRNOS


def _win_last_error() -> OSError:
    return ctypes.WinError(ctypes.get_last_error())


def open_text(path: str | os.PathLike, encoding: str = "utf-8"):
    """Ouvre ``path`` en lecture texte **sans bloquer les remplacements**.

    Sous Windows, ouvre avec ``FILE_SHARE_READ | FILE_SHARE_WRITE |
    FILE_SHARE_DELETE`` : l'écriture atomique peut remplacer le fichier pendant
    que ce handle est vivant, et ce handle continue de voir l'ancien contenu.
    Ailleurs, équivalent de ``open(path, "r")``. Lève ``OSError`` (dont
    ``FileNotFoundError``) et ``UnicodeDecodeError`` comme ``open`` standard.
    """
    if not IS_WINDOWS:
        return open(path, "r", encoding=encoding)
    handle = _kernel32.CreateFileW(
        str(path), _GENERIC_READ, _FILE_SHARE_ALL, None,
        _OPEN_EXISTING, _FILE_ATTRIBUTE_NORMAL, None,
    )
    if not handle or handle == _INVALID_HANDLE_VALUE:
        raise _win_last_error()
    try:
        fd = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
    except OSError:
        _kernel32.CloseHandle(handle)
        raise
    # ``fdopen`` devient propriétaire du handle : sa fermeture le libère.
    return os.fdopen(fd, "r", encoding=encoding)


def read_text(path: str | os.PathLike, encoding: str = "utf-8") -> str:
    """Lit entièrement ``path`` sans jamais bloquer un remplacement atomique."""
    with open_text(path, encoding=encoding) as handle:
        return handle.read()


def load_dict(path: str | os.PathLike | None) -> dict[str, Any]:
    """Charge un objet JSON, ou ``{}`` si rien d'exploitable n'est lisible.

    Couvre : chemin vide, fichier absent, fichier illisible (``OSError``),
    contenu invalide ou encodage invalide (``ValueError``, dont
    ``json.JSONDecodeError`` et ``UnicodeDecodeError``), et racine JSON qui
    n'est pas un objet (liste, chaîne, nombre).

    La lecture passe par ``open_text`` : concurrente d'un remplacement
    atomique, elle ne voit ni fichier absent ni JSON tronqué, ni erreur de
    partage Windows.
    """
    if not path:
        return {}
    try:
        payload = json.loads(read_text(path))
    except (OSError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _windows_posix_replace(source: str, destination: str) -> None:
    """Remplacement ``FileRenameInfoEx`` POSIX (cœur de la correction).

    Ouvre la source avec accès ``DELETE``, puis renomme par-dessus la
    destination avec ``REPLACE_IF_EXISTS | FILE_FLAG_POSIX_SEMANTICS``.
    Résultat atomique : soit la destination pointe sur le nouveau contenu,
    soit rien ne change. Les handles de lecteurs ouverts continuent de voir
    l'ancien fichier ; le chemin n'est jamais absent.
    """
    handle = _kernel32.CreateFileW(
        str(source), _DELETE | _FILE_READ_ATTRIBUTES, _FILE_SHARE_ALL, None,
        _OPEN_EXISTING, _FILE_ATTRIBUTE_NORMAL, None,
    )
    if not handle or handle == _INVALID_HANDLE_VALUE:
        raise _win_last_error()
    try:
        # Le nom DOIT être terminé par un nul UTF-16 : le noyau détermine la
        # longueur par wcslen et l'absence de terminateur le fait lire au-delà
        # du tampon (octets résiduels de tas → suffixe corrompu ou WinError 123,
        # selon ce qui suit le tampon dans la mémoire). FileNameLength n'inclut
        # PAS le terminateur (MSDN), mais la taille du tampon oui.
        raw = str(destination).encode("utf-16-le") + b"\x00\x00"
        buf = bytearray()
        buf += int.to_bytes(
            _RENAME_REPLACE_IF_EXISTS | _RENAME_POSIX_SEMANTICS, 4, "little"
        )
        buf += b"\x00" * 4  # alignement union (DWORD -> HANDLE)
        buf += (0).to_bytes(8, "little")  # RootDirectory = NULL
        buf += (len(raw) - 2).to_bytes(4, "little")  # FileNameLength sans nul
        buf += raw  # chemin absolu UTF-16LE nul-terminé
        holder = (ctypes.c_char * len(buf)).from_buffer(buf)
        if not _kernel32.SetFileInformationByHandle(
            handle, _FileRenameInfoEx, holder, len(buf)
        ):
            raise _win_last_error()
    finally:
        _kernel32.CloseHandle(handle)


def atomic_replace(source: str | os.PathLike, destination: str | os.PathLike) -> None:
    """Remplace ``destination`` par ``source`` de façon atomique.

    Contrats vérifiés par les sondages (``_probe12``/``_probe13``/
    ``_probe15``) :

    * **La destination n'est jamais absente** : le renommage est un point
      instantané, sans fenêtre « fichier introuvable ».
    * **Un échec préserve la destination** (contenu inchangé) et laisse
      ``source`` intact pour l'appelant nettoyer.
    * **Une destination-répertoire est refusée avant tout essai** : le
      renommage POSIX signalerait un succès en détruisant la source
      (sondage ``_probe15`` Q2).
    * Les chemins sont résolus en **absolu** : un chemin relatif serait perdu
      par l'API Windows (sondage ``_probe15`` Q3).

    Sous Windows, ``FileRenameInfoEx`` POSIX avec repli ``os.replace`` si
    l'API n'existe pas ; ailleurs, ``os.replace``. Seules les erreurs
    transitoires (verrou de partage) sont réessayées avec un backoff borné ;
    les erreurs permanentes remontent immédiatement.
    """
    source = os.path.abspath(os.fspath(source))
    destination = os.path.abspath(os.fspath(destination))
    if os.path.isdir(destination):
        raise IsADirectoryError(f"La destination est un répertoire : {destination}")

    def attempt() -> None:
        if IS_WINDOWS:
            try:
                _windows_posix_replace(source, destination)
                return
            except OSError as exc:
                if getattr(exc, "winerror", None) not in _UNSUPPORTED_WINERRORS:
                    raise
                # FileRenameInfoEx absent (Windows < 1607) : repli portable.
        os.replace(source, destination)

    delay_index = 0
    while True:
        try:
            attempt()
            return
        except OSError as exc:
            if not _is_transient(exc) or delay_index >= len(_REPLACE_BACKOFF):
                raise
            time.sleep(_REPLACE_BACKOFF[delay_index])
            delay_index += 1


def write_text_atomic(
    path: str | os.PathLike, text: str, encoding: str = "utf-8"
) -> None:
    """Écrit ``text`` dans ``path`` de façon atomique (dossier créé au besoin).

    Écrit dans un temporaire **unique** du dossier de destination, force la
    persistance (``flush`` + ``fsync``), puis ``atomic_replace``. En cas
    d'échec quelconque, le temporaire est **toujours supprimé** et la
    destination reste inchangée ; l'erreur ``OSError`` remonte à l'appelant.
    """
    path_str = os.fspath(path)
    # Encodage validé AVANT toute création : un nom invalide lèverait
    # ``LookupError`` après ``mkstemp``, laissant fd + temporaire orphelins.
    text.encode(encoding)
    directory = os.path.dirname(os.path.abspath(path_str))
    os.makedirs(directory, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(
        prefix=f".{os.path.basename(path_str)}.", suffix=".tmp", dir=directory
    )
    try:
        with os.fdopen(fd, "w", encoding=encoding) as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        atomic_replace(temp_name, path_str)
    except BaseException:
        # Nettoyage garanti du temporaire, quelle que soit la cause
        # (écriture interrompue, remplacement refusé, Ctrl+C…).
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise


def save_dict(path: str | os.PathLike | None, payload: dict[str, Any]) -> bool:
    """Écrit ``payload`` en JSON (dossier créé au besoin), en remplacement atomique.

    Retourne ``True`` si le fichier a bien été écrit, ``False`` sinon (le
    détail est laissé au système : l'état reste valable en mémoire). En cas
    d'échec, la destination existante est préservée et aucun temporaire ne
    subsiste.
    """
    if not path:
        return False
    try:
        text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
        write_text_atomic(path, text)
    except (OSError, TypeError, ValueError):
        return False
    return True


__all__ = [
    "IS_WINDOWS",
    "atomic_replace",
    "load_dict",
    "open_text",
    "read_text",
    "save_dict",
    "write_text_atomic",
]

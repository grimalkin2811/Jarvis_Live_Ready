import datetime as dt
import os
import subprocess
import urllib.parse
import webbrowser

try:
    from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume
    from comtypes import CLSCTX_ALL
except Exception:
    AudioUtilities = None
    IAudioEndpointVolume = None
    CLSCTX_ALL = None

APPS = {
    "bloc-notes": "notepad.exe",
    "notepad": "notepad.exe",
    "calculatrice": "calc.exe",
    "calculator": "calc.exe",
    "explorateur": "explorer.exe",
}
SITES = {
    "youtube": "https://youtube.com",
    "google": "https://google.com",
    "github": "https://github.com",
    "wikipedia": "https://fr.wikipedia.org",
}


def endpoint():
    if AudioUtilities is None or IAudioEndpointVolume is None or CLSCTX_ALL is None:
        raise RuntimeError("Contrôle audio indisponible sur cet environnement.")

    device = AudioUtilities.GetSpeakers()
    interface = device.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
    return interface.QueryInterface(IAudioEndpointVolume)


def _normalize_name(value):
    return str(value or "").strip().lower()


def open_application(application):
    app_name = _normalize_name(application)
    executable = APPS.get(app_name)
    if not executable:
        return {"success": False, "error": "Application non autorisee"}

    try:
        subprocess.Popen([executable])
        return {"success": True}
    except Exception as exc:
        return {"success": False, "error": str(exc)}


def close_application(application):
    app_name = _normalize_name(application)
    executable = APPS.get(app_name)
    if not executable:
        return {"success": False, "error": "Application non autorisee"}

    result = subprocess.run(
        ["taskkill", "/IM", os.path.basename(executable), "/F"],
        capture_output=True,
        text=True,
    )
    return {"success": result.returncode == 0, "message": result.stdout or result.stderr}


def _safe_volume_level():
    try:
        current = endpoint().GetMasterVolumeLevelScalar()
        return int(round(current * 100))
    except Exception:
        return 0


def set_volume(volume):
    try:
        value = max(0, min(100, int(volume)))
        audio = endpoint()
        audio.SetMute(0, None)
        audio.SetMasterVolumeLevelScalar(value / 100, None)
        return {"success": True, "volume": value}
    except Exception as exc:
        return {"success": False, "error": str(exc)}


def volume_up(amount=10):
    try:
        current = _safe_volume_level()
        return set_volume(current + int(amount))
    except Exception as exc:
        return {"success": False, "error": str(exc)}


def volume_down(amount=10):
    try:
        current = _safe_volume_level()
        return set_volume(current - int(amount))
    except Exception as exc:
        return {"success": False, "error": str(exc)}


def mute_audio():
    try:
        endpoint().SetMute(1, None)
        return {"success": True}
    except Exception as exc:
        return {"success": False, "error": str(exc)}


def unmute_audio():
    try:
        endpoint().SetMute(0, None)
        return {"success": True}
    except Exception as exc:
        return {"success": False, "error": str(exc)}


def get_local_time():
    return {"success": True, "time": dt.datetime.now().strftime("%H:%M")}


def get_local_date():
    return {"success": True, "date": dt.datetime.now().strftime("%d/%m/%Y")}


def open_website(site):
    key = _normalize_name(site)
    url = SITES.get(key)
    if not url:
        return {"success": False, "error": "Site non autorise"}

    webbrowser.open(url)
    return {"success": True}


def web_search(query):
    q = str(query or "").strip()
    if not q:
        return {"success": False, "error": "Requete vide"}
    webbrowser.open(f"https://www.google.com/search?q={urllib.parse.quote(q)}")
    return {"success": True}


TOOL_FUNCTIONS = {
    "open_application": open_application,
    "close_application": close_application,
    "set_volume": set_volume,
    "volume_up": volume_up,
    "volume_down": volume_down,
    "mute_audio": mute_audio,
    "unmute_audio": unmute_audio,
    "get_local_time": get_local_time,
    "get_local_date": get_local_date,
    "open_website": open_website,
    "web_search": web_search,
}

TOOL_DECLARATIONS = [
    {"name": "open_application", "description": "Ouvre une application autorisee.", "parameters": {"type": "object", "properties": {"application": {"type": "string"}}, "required": ["application"]}},
    {"name": "close_application", "description": "Ferme une application autorisee.", "parameters": {"type": "object", "properties": {"application": {"type": "string"}}, "required": ["application"]}},
    {"name": "set_volume", "description": "Regle le volume de 0 a 100.", "parameters": {"type": "object", "properties": {"volume": {"type": "integer"}}, "required": ["volume"]}},
    {"name": "volume_up", "description": "Augmente le volume.", "parameters": {"type": "object", "properties": {"amount": {"type": "integer"}}}},
    {"name": "volume_down", "description": "Baisse le volume.", "parameters": {"type": "object", "properties": {"amount": {"type": "integer"}}}},
    {"name": "mute_audio", "description": "Coupe le son.", "parameters": {"type": "object", "properties": {}}},
    {"name": "unmute_audio", "description": "Retablit le son.", "parameters": {"type": "object", "properties": {}}},
    {"name": "get_local_time", "description": "Donne l heure locale.", "parameters": {"type": "object", "properties": {}}},
    {"name": "get_local_date", "description": "Donne la date locale.", "parameters": {"type": "object", "properties": {}}},
    {"name": "open_website", "description": "Ouvre un site autorise.", "parameters": {"type": "object", "properties": {"site": {"type": "string"}}, "required": ["site"]}},
    {"name": "web_search", "description": "Recherche sur le Web dans le navigateur.", "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}},
]

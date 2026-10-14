"""Prévisions Open-Meteo : uniquement des URL fixes, réponse bornée, sans clé API."""
import datetime as dt
import json
import math
import urllib.parse
import urllib.request

WMO_CODES = {
    0: "ciel dégagé", 1: "plutôt dégagé", 2: "partiellement nuageux", 3: "couvert",
    45: "brouillard", 48: "brouillard givrant",
    51: "bruine légère", 53: "bruine", 55: "bruine dense",
    56: "bruine verglaçante", 57: "bruine verglaçante dense",
    61: "pluie faible", 63: "pluie", 65: "pluie forte",
    66: "pluie verglaçante", 67: "pluie verglaçante forte",
    71: "neige faible", 73: "neige", 75: "neige forte", 77: "grains de neige",
    80: "averses faibles", 81: "averses", 82: "fortes averses",
    85: "averses de neige", 86: "fortes averses de neige",
    95: "orage", 96: "orage avec grêle", 99: "orage violent avec grêle",
}



def _fetch_json(url):
    request = urllib.request.Request(url, headers={"User-Agent": "Jarvis/1.8"})
    with urllib.request.urlopen(request, timeout=10) as response:
        data = response.read(1024 * 1024 + 1)
    if len(data) > 1024 * 1024:
        raise ValueError("Réponse trop grande")
    return json.loads(data)


def get_forecast(city="Paris", days=7):
    if not isinstance(city, str) or not city.strip() or len(city) > 120:
        return {"success": False, "error": "Ville invalide."}
    if type(days) is not int or not 1 <= days <= 7:
        return {"success": False, "error": "Durée attendue entre 1 et 7 jours."}
    try:
        geo = _fetch_json("https://geocoding-api.open-meteo.com/v1/search?" + urllib.parse.urlencode(
            {"name": city.strip(), "count": 1, "language": "fr", "format": "json"}))
        place = geo["results"][0]
        latitude, longitude = float(place["latitude"]), float(place["longitude"])
        if not -90 <= latitude <= 90 or not -180 <= longitude <= 180:
            raise ValueError("Coordonnées invalides")
        payload = _fetch_json("https://api.open-meteo.com/v1/forecast?" + urllib.parse.urlencode({
            "latitude": latitude, "longitude": longitude, "timezone": "auto", "forecast_days": days,
            "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max"}))
        daily = payload["daily"]
        entries = []
        for i, date in enumerate(daily["time"][:days]):
            dt.date.fromisoformat(date)
            low, high = float(daily["temperature_2m_min"][i]), float(daily["temperature_2m_max"][i])
            if not math.isfinite(low) or not math.isfinite(high):
                raise ValueError("Température invalide")
            entries.append({"date": date, "min_c": low, "max_c": high,
                            "description": WMO_CODES.get(daily["weather_code"][i], "code météo inconnu"),
                            "pluie_pourcent": daily["precipitation_probability_max"][i]})
        if not entries:
            raise ValueError("Prévisions vides")
        return {"success": True, "ville": place.get("name", city), "pays": place.get("country", ""),
                "jours": entries, "count": len(entries), "source": "Open-Meteo", "partiel": len(entries) < days}
    except Exception:
        return {"success": False, "error": "Prévisions indisponibles ou réponse du fournisseur invalide."}

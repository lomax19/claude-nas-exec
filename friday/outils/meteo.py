#!/usr/bin/env python3
"""Meteo pour Friday (28/10/2026) - source Open-Meteo (gratuit, sans compte).
Usage : meteo.py            -> maison (ta ville)
        meteo.py <lieu>     -> n'importe quelle ville (France d'abord)"""
import json, sys, time, urllib.parse, urllib.request

MAISON = ("ta ville (maison)", 45.29821, 1.57879)
WMO = {0: "ciel dégagé", 1: "plutôt ensoleillé", 2: "éclaircies et nuages", 3: "temps couvert", 45: "brouillard",
       48: "brouillard givrant", 51: "bruine légère", 53: "bruine", 55: "forte bruine", 56: "bruine verglaçante",
       57: "bruine verglaçante", 61: "pluie faible", 63: "pluie", 65: "forte pluie", 66: "pluie verglaçante",
       67: "pluie verglaçante", 71: "neige faible", 73: "neige", 75: "forte neige", 77: "grésil", 80: "quelques averses",
       81: "averses", 82: "fortes averses", 85: "averses de neige", 86: "fortes averses de neige", 95: "orages",
       96: "orages avec grêle", 99: "orages avec grêle"}
JOURS = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]


def get(url, q):
    return json.load(urllib.request.urlopen(url + "?" + urllib.parse.urlencode(q), timeout=15))


lieu = " ".join(sys.argv[1:]).strip()
if lieu and lieu.lower() not in ("maison", "chez moi", "ici a la maison"):
    res = []
    for pays in ("FR", None):
        q = {"name": lieu, "count": 1, "language": "fr"}
        if pays:
            q["countryCode"] = pays
        res = get("https://geocoding-api.open-meteo.com/v1/search", q).get("results") or []
        if res:
            break
    if not res:
        print("Lieu introuvable :", lieu); sys.exit(1)
    r = res[0]
    nom = r["name"] + (" (" + r["admin2"] + ")" if r.get("admin2") else (" (" + r.get("country", "") + ")"))
    lat, lon = r["latitude"], r["longitude"]
else:
    nom, lat, lon = MAISON

d = get("https://api.open-meteo.com/v1/forecast", {
    "latitude": lat, "longitude": lon, "timezone": "Europe/Paris", "forecast_days": 5,
    "current": "temperature_2m,weather_code,wind_speed_10m",
    "daily": "weather_code,temperature_2m_min,temperature_2m_max,precipitation_sum,precipitation_probability_max,"
             "wind_speed_10m_max,wind_gusts_10m_max"})
c = d["current"]
print("Météo à %s (Open-Meteo). Maintenant (%s) : %d °C, %s, vent %d km/h." % (
    nom, c["time"][11:16], round(c["temperature_2m"]), WMO.get(c["weather_code"], "?"), round(c["wind_speed_10m"])))
j = d["daily"]
for i, day in enumerate(j["time"]):
    t = time.strptime(day, "%Y-%m-%d")
    nomj = {0: "Aujourd'hui", 1: "Demain"}.get(i, "") 
    lib = ("%s %s %d" % (nomj, JOURS[t.tm_wday], t.tm_mday)).strip()
    pluie = j["precipitation_sum"][i] or 0
    proba = j["precipitation_probability_max"][i]
    txt = "%s : %s, de %d à %d °C" % (lib, WMO.get(j["weather_code"][i], "?"), round(j["temperature_2m_min"][i]),
                                       round(j["temperature_2m_max"][i]))
    txt += (", pluie %.0f mm" % pluie if pluie >= 0.5 else ", sec") + (" (risque %d %%)" % proba if proba is not None else "")
    txt += ", vent jusqu'à %d km/h, rafales %d km/h." % (round(j["wind_speed_10m_max"][i]), round(j["wind_gusts_10m_max"][i]))
    print(txt)

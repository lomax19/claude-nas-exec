#!/usr/bin/env python3
"""Prepare la prochaine mission de Loulou EN UNE SEULE COMMANDE (29/10/2026).

1. Lit logiciel-missions avec missions.py (LECTURE SEULE ; seul appui : le bouton « i » de la 1re fiche, gere par missions.py).
   Si la liaison avec le telephone coupe (erreur rapide), on relance jusqu'a 2 fois.
2. Extrait de la fiche detaillee : type, entreprise, adresse, telephones, client, memo.
3. Trouve le point GPS de l'adresse (Base Adresse Nationale, puis OpenStreetMap en secours).
4. Lance le GPS Eurowag EN ARRIERE-PLAN (gps.sh) : la reponse n'attend pas le GPS.
   Si le guidage ne demarre pas, Loulou est prevenu a la voix (dire.py) et c'est note dans prepare-mission.log.

Usage : prepare-mission.py [--sans-gps] [--rapide] [--tout]  (--tout = toute la liste dans l'ordre, sans GPS)
        [--cible "phrase de Loulou"]  (03/11/2026 : ouvre l'etape qu'il nomme : « la deuxieme », « celle qui va
        a Messia », « chez United Caps », un n°, « le dechargement » ; sinon la plus tot, comme avant)
        prepare-mission.py --test FICHIER   (lit une sortie de missions.py enregistree : rien sur le telephone)
"""
import json, os, re, subprocess, sys, time, urllib.parse, urllib.request

OUT = os.path.dirname(os.path.abspath(__file__))
MISSIONS = os.path.join(OUT, "missions.py")
GPS = os.path.join(OUT, "gps.sh")
LOG = os.path.join(OUT, "prepare-mission.log")
DIRE = "/home/USER/jarvis/dire.py"


def log(m):
    try:
        with open(LOG, "a") as f:
            f.write(time.strftime("%d/%m %H:%M:%S ") + m + "\n")
    except OSError:
        pass


def lire_logiciel-missions(args):
    """-> (texte, erreur). Relance si la liaison telephone a coupe (echec en moins de 20 s)."""
    texte = ""
    for essai in range(3):
        t0 = time.time()
        try:
            r = subprocess.run([sys.executable, MISSIONS] + args, capture_output=True, text=True, timeout=200)
            texte = r.stdout.strip()
        except subprocess.TimeoutExpired:
            return "", "ERREUR lecture d'logiciel-missions trop longue (plus de 3 minutes)"
        duree = time.time() - t0
        log("lecture logiciel-missions essai %d : code %s en %.0f s" % (essai + 1, r.returncode, duree))
        if r.returncode == 0 and "=== N°" in texte:
            return texte, None
        if "SHIZUKU_ARRETE" in texte:  # 03/11/2026 : inutile de relancer
            return "", [l for l in texte.splitlines() if "SHIZUKU_ARRETE" in l][-1]
        if duree > 20 or essai == 2:
            break
        time.sleep(4)
    return texte, (texte.splitlines()[-1] if texte else "ERREUR lecture d'logiciel-missions impossible")


TEL_RE = re.compile(r"\b0\d(?:[ .]?\d\d){4}\b")
CP_RE = re.compile(r"^(\d{5})\s+(.+)$")


def ville_propre(v):
    v = v.strip()
    m = re.match(r"^(.*?)\s*\((LA|LE|LES|L')\)$", v, re.I)
    if m:
        art = m.group(2).upper()
        v = (art + m.group(1)) if art == "L'" else (art + " " + m.group(1))
    return v.title().replace("'S", "'s")


def analyser(texte):
    """Sortie de missions.py -> dict de la 1re etape."""
    m = {"liste": [], "detail": [], "pal": "", "venant": "", "vers": ""}
    lignes = texte.splitlines()
    dans_detail = False
    for l in lignes:
        if l.startswith("=== FICHE DETAILLEE"):
            dans_detail = True
            m["entete"] = l.strip("= ").replace("FICHE DETAILLEE DE LA 1re ETAPE ", "").replace(
                "FICHE DETAILLEE DE L'ETAPE DEMANDEE ", "")
            continue
        if not dans_detail and l.startswith("CHOIX :"):
            m["choix"] = l.strip()
            continue
        (m["detail"] if dans_detail else m["liste"]).append(l)
    d = [l.strip() for l in m["detail"] if l.strip()]
    if not d or d[0].startswith("ERREUR"):
        m["detail_ok"] = False
        m["detail_erreur"] = d[0] if d else "fiche detaillee absente"
    else:
        m["detail_ok"] = True
    # infos de la liste pour l'etape dont la fiche a ete ouverte (03/11/2026 : pas forcement la 1re)
    vise = m.get("entete", "").strip("() ")
    premier = False
    for l in m["liste"]:
        if l.startswith("=== N°"):
            premier = ("tete" not in m) and (l.startswith("=== " + vise) if vise else True)
            if not premier:
                continue
            p = [x.strip() for x in l.strip("= ").split("|")]
            m["tete"] = p[0]
            m["date"] = p[1] if len(p) > 1 else ""
            m["creneau"] = p[2] if len(p) > 2 else ""
            m["type"] = p[3] if len(p) > 3 else ""
        elif premier and "lieu de l'etape" in l:
            m["lieu_liste"] = l.split(":", 1)[1].strip()
            pal = re.search(r"Pal\s*:\s*(\d+)", l)
            m["pal"] = pal.group(1) if pal else ""
        elif premier and "marchandise venant de" in l:
            m["venant"] = l.split(":", 1)[1].strip()
        elif premier and "marchandise a livrer a" in l:
            m["vers"] = l.split(":", 1)[1].strip()
    if not m["detail_ok"]:
        return m
    # fiche detaillee
    try:
        i = next(k for k, x in enumerate(d) if x in ("Chargement", "Déchargement", "Dechargement"))
        m["type_fiche"] = d[i]
        m["entreprise"] = d[i + 1] if i + 1 < len(d) else ""
    except StopIteration:
        i = 0
    try:
        a = d.index("Adresse")
    except ValueError:
        a = i + 1
    try:
        f = d.index("Informations de l'ordre")
    except ValueError:
        f = len(d)
    rue, cp, ville = [], "", ""
    for x in d[a + 1:f]:
        c = CP_RE.match(x)
        if c:
            cp, ville = c.group(1), c.group(2)
        elif TEL_RE.search(x) or "@" in x or re.match(r"^(Fixe|Portable|Email|Service|Contact)\b", x):
            continue
        elif not cp:
            rue.append(x)
    m["rue"] = " ".join(rue)
    m["cp"], m["ville"] = cp, ville_propre(ville) if ville else ""
    m["tels"] = sorted(set(t for x in d for t in TEL_RE.findall(x)), key=lambda t: d.index(next(x for x in d if t in x)))
    m["client"] = next((x.split(":", 1)[1].strip() for x in d[f:] if x.startswith("Client")), "")
    try:
        k = next(k for k, x in enumerate(d) if x.startswith("Mémo") or x.startswith("Memo"))
        memo = d[k].split(":", 1)[1].strip() if ":" in d[k] else ""
        m["memo"] = " ".join([memo] + d[k + 1:]).strip()
    except StopIteration:
        m["memo"] = ""
    m["remarques"] = [x for x in d if x.startswith("(")]
    return m


def http_json(url, timeout=8):
    req = urllib.request.Request(url, headers={"User-Agent": "friday-freeplex/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def geocoder(m):
    """-> (lat, lon, libelle, precision) ou None."""
    cp, ville, rue = m.get("cp", ""), m.get("ville", ""), m.get("rue", "")
    essais = []
    if rue:
        essais.append(("%s %s %s" % (rue, cp, ville), "adresse"))
    if ville:
        essais.append(("%s %s" % (cp, ville), "commune"))
    for q, niveau in essais:
        try:
            u = "https://api-adresse.data.gouv.fr/search/?" + urllib.parse.urlencode(
                {"q": q, "limit": 1, **({"postcode": cp} if cp else {})})
            f = (http_json(u).get("features") or [None])[0]
        except Exception as e:  # noqa: BLE001
            log("BAN %r : %r" % (q, e))
            f = None
        if f and f["properties"].get("score", 0) >= 0.45:
            lon, lat = f["geometry"]["coordinates"]
            typ = f["properties"].get("type")
            prec = "adresse exacte" if typ == "housenumber" else ("rue" if typ == "street" else "centre de la commune")
            if niveau == "commune" and m.get("entreprise"):
                # pas d'adresse precise : on tente l'entreprise dans OpenStreetMap
                try:
                    u = "https://nominatim.openstreetmap.org/search?" + urllib.parse.urlencode(
                        {"q": "%s %s" % (m["entreprise"], ville), "format": "json", "limit": 1, "countrycodes": "fr"})
                    r = http_json(u)
                    if r:
                        return float(r[0]["lat"]), float(r[0]["lon"]), r[0]["display_name"][:120], "entreprise (OpenStreetMap)"
                except Exception as e:  # noqa: BLE001
                    log("OSM %r" % e)
            return lat, lon, f["properties"].get("label", q), prec
    return None


def gps_arriere_plan(lat, lon, nom, adresse):
    nom_c = re.sub(r"[^A-Za-z0-9 _.-]", "", nom)[:40] or "Destination"
    # 29/10/2026 : le controle de gps.sh peut se tromper (ecran Eurowag change) -> message prudent, pas « n'a pas demarre »
    msg = ("Loulou, je n'ai pas pu confirmer que le guidage vers %s a démarré. Regarde ton GPS : "
           "si rien ne s'affiche, l'adresse est %s.") % (nom, adresse)
    script = (
        'R=$(timeout 150 %s %s %s "%s" 2>&1); echo "$(date +%%H:%%M:%%S) gps $R" >> %s; '
        'case "$R" in OK*) ;; *) python3 %s "$1" >/dev/null 2>&1 ;; esac'
        % (GPS, lat, lon, nom_c, LOG, DIRE))
    subprocess.Popen(["setsid", "sh", "-c", script, "sh", msg], stdin=subprocess.DEVNULL,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)


def revenir():
    """logiciel-missions est reste devant (mode itineraire) mais aucun GPS ne part : on revient a l'appli d'avant (Friday)."""
    cmd = ('P=$(cat /sdcard/akprev.txt 2>/dev/null); if [ "$P" = com.android.chrome ]; then input keyevent 4; '
           'elif [ -n "$P" ] && [ "$P" != com.logiciel-missions.xmobile ]; then monkey -p "$P" 1 >/dev/null 2>&1; fi')
    try:
        subprocess.run(["ssh", "-i", "/home/USER/.ssh/id_ed25519", "-o", "UserKnownHostsFile=/home/USER/.ssh/known_hosts",
                        "-o", "ConnectTimeout=8", "-o", "BatchMode=yes", "-p", "8022", "100.122.224.83",
                        "rish -c '%s'" % cmd.replace("'", "'\\''")], capture_output=True, timeout=30)
    except Exception as e:  # noqa: BLE001
        log("retour a Friday : %r" % e)


def main():
    a = sys.argv[1:]
    t0 = time.time()
    if "--tout" in a:  # 29/10/2026 : « regarde mes missions » = toute la liste, dans l'ordre (missions-tout.py)
        r = subprocess.run([sys.executable, os.path.join(OUT, "missions-tout.py")], capture_output=True, text=True, timeout=260)
        print(r.stdout.strip() or "ERREUR lecture de la liste impossible (rien n'a ete touche dans logiciel-missions)")
        log("liste complete terminee en %.0f s (code %s)" % (time.time() - t0, r.returncode))
        sys.exit(r.returncode)
    if "--test" in a:
        texte, err = open(a[a.index("--test") + 1], encoding="utf-8").read(), None
    else:
        cible = a[a.index("--cible") + 1] if "--cible" in a[:-1] else ""
        if cible:
            log("cible : %s" % cible[:200])
        texte, err = lire_logiciel-missions((["--rapide"] if "--rapide" in a else ([] if "--sans-gps" in a else ["--itineraire"]))
                                 + (["--cible", cible] if cible else []))
    if err:
        print(err)
        print("(Rien n'a été touché dans logiciel-missions. Propose à Loulou de relancer.)" if "SHIZUKU" not in err else
              "(Rien n'a été touché. Ne propose PAS de relancer : Loulou doit d'abord relancer l'appli Shizuku, en Wi-Fi.)")
        sys.exit(1)
    if "--rapide" in a:
        print(texte)
        return
    m = analyser(texte)
    if m.get("choix"):
        print(m["choix"])
    print("=== ÉTAPE DEMANDÉE ===" if m.get("choix", "").startswith("CHOIX : etape choisie") else "=== PROCHAINE ÉTAPE ===")
    print("Mission %s : %s %s, créneau %s" % (m.get("tete", "?"), m.get("type", "?"), m.get("date", "?").lower(),
                                                  m.get("creneau", "?").replace("De ", "de ")))
    if not m["detail_ok"]:
        print("Fiche détaillée NON lue : %s" % m.get("detail_erreur"))
        print("Lieu d'après la liste : %s" % m.get("lieu_liste", "?"))
    else:
        print("Entreprise : %s" % m.get("entreprise", "?"))
        adr = ", ".join(x for x in (m.get("rue"), (m.get("cp", "") + " " + m.get("ville", "")).strip()) if x)
        print("Adresse : %s" % (adr or "non indiquée dans la fiche"))
        print("Téléphone(s) : %s" % (", ".join(m.get("tels") or []) or "aucun"))
        print("Client : %s" % (m.get("client") or "?"))
        print("Mémo : %s" % (m.get("memo") or "rien"))
    if m.get("pal"):
        print("Palettes : %s" % m["pal"])
    if m.get("venant"):
        print("La marchandise vient de : %s" % m["venant"])
    if m.get("vers"):
        print("La marchandise va à : %s" % m["vers"])
    for r in m.get("remarques", []):
        print(r)
    # GPS
    if m["detail_ok"] and "--sans-gps" not in a and "--test" not in a:
        g = geocoder(m)
        if g:
            lat, lon, lib, prec = g
            gps_arriere_plan(lat, lon, m.get("entreprise", "Destination"), lib)
            print("GPS : destination en cours d'envoi à Eurowag (%s : %s). Démarrage du guidage PAS ENCORE confirmé ; "
                  "s'il échoue, Loulou sera prévenu à la voix dans 1 à 2 minutes." % (prec, lib))
            log("gps lance vers %s (%s, %s) %s" % (lib, lat, lon, prec))
        else:
            print("GPS : adresse introuvable sur la carte, rien envoyé au GPS.")
            revenir()
    elif "--sans-gps" in a:
        print("GPS : pas demandé cette fois (le GPS part seulement s'il dit « lance la prochaine mission »).")
    elif "--test" in a and m["detail_ok"]:
        g = geocoder(m)
        print("TEST point GPS :", g)
    log("prepare-mission termine en %.0f s" % (time.time() - t0))
    print("--- lignes brutes de la fiche (pour vérifier) ---")
    for l in m["detail"]:
        if l.strip():
            print(l)


if __name__ == "__main__":
    main()

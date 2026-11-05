# Tourne SUR LE TELEPHONE (Termux), lance par /home/USER/jarvis/outils/missions-tout.py (NAS). 29/10/2026.
# Lit TOUTE la liste « Missions en cours » d'logiciel-missions en la faisant defiler, et renvoie les captures (JSON sur stdout).
# SECURITE : AUCUN appui (tap). Defilement uniquement par de longs gestes sur le bord droit de l'ecran (x=1150),
# comme missions.py depuis le 27/10. Si un autre ecran d'logiciel-missions est ouvert (Detail, Evenement) : touche Retour.
# A la fin : retour en haut de la liste, puis retour a l'appli d'avant (Chrome = Friday -> touche Retour).
import html, json, os, re, subprocess, sys, time

MOTIF = re.compile(r"""text=(?:"([^"]*)"|'([^']*)')[^>]*bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]" """.strip())
RE_TETE = re.compile(r"^N°\d+\nLe \d\d/\d\d/\d{4}\nDe \d\d:\d\d")
journal = []
SHIZUKU = "SHIZUKU_ARRETE : Shizuku est arrete sur le telephone de Loulou (souvent apres un redemarrage du telephone). Rien ne peut etre lu ni touche (logiciel-missions, GPS, appels, SMS) tant qu'il ne l'a pas relance lui-meme, en Wi-Fi."


def rish(c):
    r = subprocess.run(["rish", "-c", c], capture_output=True, text=True, timeout=30)
    if "not running" in r.stdout + r.stderr:  # 03/11/2026 : Shizuku arrete (telephone redemarre) -> le dire, et ne jamais relire une ancienne capture
        fin({"ok": False, "erreur": SHIZUKU})
    return r.stdout


def cap():
    for _ in range(4):
        t0 = time.time()
        rish("rm -f /sdcard/akl.xml; uiautomator dump /sdcard/akl.xml >/dev/null 2>&1")
        try:
            # 03/11/2026 : si le dump echoue, l'ancien fichier reste -> on ne lit qu'une capture faite maintenant
            x = open("/sdcard/akl.xml", encoding="utf-8", errors="replace").read() if os.path.getmtime("/sdcard/akl.xml") >= t0 - 2 else ""
        except OSError:
            x = ""
        if "</hierarchy>" in x:
            out = []
            for m in MOTIF.finditer(x):
                t = html.unescape(m.group(1) if m.group(1) is not None else m.group(2)).replace("\r", "").strip()
                if t:
                    out.append((t, *map(int, m.group(3, 4, 5, 6))))
            if out:
                return out
        time.sleep(0.5)
    return []


def fin(res):
    res["journal"] = journal
    print(json.dumps(res, ensure_ascii=False))
    sys.exit(0)


prev = ""
m = re.search(r"u0 ([^ /]+)", rish("dumpsys activity activities | grep -m1 topResumedActivity"))
if m:
    prev = m.group(1)
journal.append("appli d'avant : %s" % prev)
rish("cmd statusbar collapse >/dev/null 2>&1; monkey -p com.logiciel-missions.xmobile 1 >/dev/null 2>&1")
time.sleep(2.5)

ecrans, vus, rien_de_neuf, haut_fait = [], set(), 0, False
for k in range(14):
    n = cap()
    t = [x[0] for x in n]
    if "Missions en cours" not in t or "Mission - Détail" in t:
        journal.append("ecran %d inattendu : %s" % (k, t[:3]))
        if any(("Mission - D" in x) or ("nement" in x) for x in t):
            rish("input keyevent 4")
        time.sleep(1.2)
        continue
    if not haut_fait:  # partir du haut de la liste
        rish("input swipe 1150 1000 1150 2300 250"); time.sleep(0.6)
        rish("input swipe 1150 1000 1150 2300 250"); time.sleep(1.0)
        haut_fait = True
        continue
    tetes = {x[0] for x in n if RE_TETE.match(x[0])}
    ecrans.append(n)
    journal.append("ecran %d : %d fiches (%s)" % (k, len(tetes), ", ".join(sorted(h.split("\n")[0] for h in tetes))))
    if tetes and tetes <= vus:
        rien_de_neuf += 1
    else:
        rien_de_neuf = 0
    vus |= tetes
    if rien_de_neuf >= 1 or (not tetes and ecrans[:-1]):
        break  # le defilement n'apporte plus rien : bas de la liste
    rish("input swipe 1150 2000 1150 1300 600")
    time.sleep(1.0)

# remettre la liste en haut, puis revenir a l'appli d'avant
for _ in range(3):
    rish("input swipe 1150 1000 1150 2300 250"); time.sleep(0.4)
if prev == "com.android.chrome":
    rish("input keyevent 4")
elif prev and prev != "com.logiciel-missions.xmobile":
    rish("monkey -p %s 1 >/dev/null 2>&1" % prev)
if not ecrans:
    fin({"ok": False, "erreur": "liste « Missions en cours » non lue (rien touche)"})
fin({"ok": True, "ecrans": ecrans, "fiches": len(vus)})

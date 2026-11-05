# Tourne SUR LE TELEPHONE (Termux), lance par /home/USER/jarvis/outils/missions.py (NAS). 29/10/2026.
# Ouvre la fiche « Mission - Detail » d'UNE etape d'logiciel-missions et en renvoie le texte (JSON sur stdout).
# SECURITE : un seul appui (tap), au centre du bouton « i » de la fiche visee, seulement si 2 captures de suite
# de l'ecran « Missions en cours » donnent exactement la meme position ; sortie par la touche Retour uniquement.
# Si l'appui n'ouvre pas « Mission - Detail » -> touche Retour et arret. Aucun autre appui, jamais.
import html, json, os, re, subprocess, sys, time

TETE = sys.argv[1]  # en-tete exacte de la fiche : "N°707522\nLe 29/10/2026\nDe 15:00 à 16:00"
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
        rish("rm -f /sdcard/akd.xml; uiautomator dump /sdcard/akd.xml >/dev/null 2>&1")
        try:
            # 03/11/2026 : si le dump echoue, l'ancien fichier reste -> on ne lit qu'une capture faite maintenant
            x = open("/sdcard/akd.xml", encoding="utf-8", errors="replace").read() if os.path.getmtime("/sdcard/akd.xml") >= t0 - 2 else ""
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


def textes(n):
    return [x[0] for x in n]


def bouton_i(n):
    if "Missions en cours" not in textes(n) or "Mission - Détail" in textes(n):
        return None
    heads = sorted([x for x in n if RE_TETE.match(x[0])], key=lambda x: x[2])
    for k, h in enumerate(heads):
        if h[0] != TETE:
            continue
        y_fin = heads[k + 1][2] if k + 1 < len(heads) else 99999
        bt = [x for x in n if x[0] == "i" and 450 <= x[1] <= 750 and h[2] <= x[2] < y_fin]
        if len(bt) == 1 and bt[0][2] >= 345 and bt[0][4] <= 2590:
            return tuple(bt[0][1:])
    return None


def fin(res):
    res["journal"] = journal
    print(json.dumps(res, ensure_ascii=False))
    sys.exit(0)


# 1) reperer le bouton « i » : 2 captures identiques de suite
pos, prec, defile = None, None, False
for essai in range(6):
    n = cap()
    b = bouton_i(n)
    journal.append("capture %d : i=%s titre=%s" % (essai, b, "Missions en cours" in textes(n)))
    if b and b == prec:
        pos = b
        break
    prec = b
    if not b and not defile and any(x[0] == TETE for x in n):
        rish("input swipe 1150 2000 1150 1300 600"); defile = True; prec = None; time.sleep(1.2)
    time.sleep(0.4)
if not pos:
    fin({"ok": False, "erreur": "bouton « i » pas repere de facon sure : rien touche"})

# 2) UN appui, puis attente de la fiche complete (2 captures identiques)
x1, y1, x2, y2 = pos
journal.append("appui %d %d" % ((x1 + x2) // 2, (y1 + y2) // 2))
rish("input tap %d %d" % ((x1 + x2) // 2, (y1 + y2) // 2))
time.sleep(1.5)
n, avant = [], None
for _ in range(7):
    n = cap()
    if "Mission - Détail" in textes(n):
        if textes(n) == avant:
            break
        avant = textes(n)
    time.sleep(0.8)
journal.append("apres appui : %d textes, titre detail=%s" % (len(n), "Mission - Détail" in textes(n)))
if "Mission - Détail" not in textes(n):
    rish("input keyevent 4")
    fin({"ok": False, "erreur": "l'appui n'a pas ouvert la fiche (ecran : %s) ; touche Retour faite, rien valide" % textes(n)[:2]})

lignes = [x for x in sorted(n, key=lambda x: (x[2], x[1])) if x[2] >= 334 and x[0] not in ("Mission - Détail", "i")]
coupe = any(x[4] > 2300 for x in lignes if x[1] < 990)

# 3) sortie : touche Retour uniquement, puis controle
retour_ok = False
for _ in range(2):
    rish("input keyevent 4"); time.sleep(1.2)
    if "Mission - Détail" not in textes(cap()):
        retour_ok = True
        break
fin({"ok": True, "textes": [x[0] for x in lignes], "coupe": coupe, "retour_liste": retour_ok})

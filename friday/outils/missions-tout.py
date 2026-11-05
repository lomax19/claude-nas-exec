#!/usr/bin/env python3
"""Lit TOUTES les etapes de la liste logiciel-missions de Loulou (29/10/2026), dans l'ordre chronologique. LECTURE SEULE.

Le travail se fait sur le telephone (akliste-telephone.py, Python de Termux) : aucun appui, seulement des
defilements sur le bord droit de l'ecran. Lecture d'une fiche (regle de missions.py, verifiee le 27/10/2026) :
fiche avec « Dest : » = CHARGEMENT ; fiche avec « Exp : » = DECHARGEMENT ; la ligne « Ref » = le lieu de l'etape.
"""
import base64, json, re, subprocess, sys, time

OUT = "/home/USER/jarvis/outils"
TEL = OUT + "/akliste-telephone.py"
LOG = OUT + "/prepare-mission.log"
SSH = ["ssh", "-i", "/home/USER/.ssh/id_ed25519", "-o", "UserKnownHostsFile=/home/USER/.ssh/known_hosts",
       "-o", "ConnectTimeout=8", "-o", "BatchMode=yes", "-p", "8022", "100.122.224.83"]
TETE = re.compile(r"^N°\d+\nLe \d\d/\d\d/\d{4}\nDe \d\d:\d\d")
JOURS = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]


def log(m):
    try:
        with open(LOG, "a") as f:
            f.write(time.strftime("%d/%m %H:%M:%S ") + "liste complete : " + m + "\n")
    except OSError:
        pass


def ranger(n, cartes):
    heads = sorted([x for x in n if TETE.match(x[0])], key=lambda x: x[2])
    for k, h in enumerate(heads):
        y_fin = heads[k + 1][2] if k + 1 < len(heads) else 99999
        bloc = [x for x in n if x[1] >= 500 and h[2] - 5 <= x[2] < y_fin and x[0] != "i"]
        statut = [x[0] for x in n if x[1] < 500 and h[2] < x[2] < y_fin and not x[0].startswith("N°")]
        c = cartes.setdefault(h[0], {"tete": h[0], "statut": "", "ref": "", "dest": "", "exp": ""})
        if statut and not c["statut"]:
            c["statut"] = statut[0]
        for x in bloc:
            for champ, pre in (("ref", "Ref"), ("dest", "Dest"), ("exp", "Exp")):
                if x[0].startswith(pre) and len(x[0]) > len(c[champ]):
                    c[champ] = x[0]  # la version la plus complete (fiche parfois coupee en bas d'un ecran)


def propre(v, prefixe):
    v = re.sub(r"^%s\s*:\s*" % prefixe, "", v)
    return " | ".join(p.strip() for p in v.split("\n") if p.strip())


# 03/11/2026 : meme ordre que missions.py (date, debut, puis fin du creneau)
from cible import cle  # noqa: E402


def lire():
    code = base64.b64encode(open(TEL, "rb").read()).decode()
    for essai in range(3):
        t0 = time.time()
        r = subprocess.run(SSH + ["echo %s | base64 -d > ~/akliste.py; python ~/akliste.py" % code],
                           capture_output=True, text=True, timeout=200)
        try:
            res = json.loads(r.stdout.strip().splitlines()[-1])
        except Exception:  # noqa: BLE001
            res = {"ok": False, "erreur": "reponse du telephone illisible (%s)" % (r.stderr.strip()[-120:] or "vide")}
        log("essai %d en %.0f s : %s" % (essai + 1, time.time() - t0, "; ".join(res.get("journal", []))[-400:]))
        if res.get("ok") or time.time() - t0 > 20 or "SHIZUKU" in res.get("erreur", ""):
            return res
        time.sleep(4)  # coupure de la liaison : on relance
    return res


def main():
    if "--test" in sys.argv:
        res = json.load(open(sys.argv[sys.argv.index("--test") + 1]))
    else:
        res = lire()
    if not res.get("ok"):
        print("ERREUR " + res.get("erreur", "lecture impossible") + " (rien n'a ete touche dans logiciel-missions)")
        sys.exit(1)
    cartes = {}
    for n in res["ecrans"]:
        ranger([tuple(x) for x in n], cartes)
    if not cartes:
        print("Aucune etape dans la liste logiciel-missions « Missions en cours ».")
        return
    jour_prec = None
    print("=== TOUTES LES ETAPES DE LA LISTE logiciel-missions, DANS L'ORDRE (%d) ===" % len(cartes))
    for c in sorted(cartes.values(), key=cle):
        k = cle(c)
        if len(k) > 1:
            j = "%s/%s/%s" % (k[2], k[1], k[0])
            if j != jour_prec:
                wd = JOURS[time.strptime(j, "%d/%m/%Y").tm_wday]
                print("\n--- %s %s ---" % (wd, j))
                jour_prec = j
        typ = "CHARGEMENT" if c["dest"] else ("DECHARGEMENT" if c["exp"] else "TYPE INCONNU (voir logiciel-missions)")
        tete = c["tete"].split("\n")
        print("* %s, %s, mission %s, statut %s" % (tete[2] if len(tete) > 2 else "?", typ, tete[0],
                                                   c["statut"].replace("\n", " ")))
        print("  lieu : " + (propre(c["ref"], "Ref") or "non affiche"))
        if c["dest"]:
            print("  marchandise a livrer a : " + propre(c["dest"], "Dest"))
        if c["exp"]:
            print("  marchandise venant de : " + propre(c["exp"], "Exp"))
    print("\n(Statut MRECU/MLU = mission recue/lue, PAS faite. Pds sans unite. Pal = palettes. "
          "Adresse complete : seulement dans la fiche, lue par « lance la prochaine mission ».)")


if __name__ == "__main__":
    main()

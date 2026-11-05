#!/usr/bin/env python3
"""Lit les missions de Loulou dans l'appli logiciel-missions (com.logiciel-missions.xmobile) de son telephone. LECTURE SEULE.

SECURITE (27/10/2026) : logiciel-missions sert a declarer les evenements de mission a l'employeur (enlevement conforme,
depose a quai...). Une version precedente qui appuyait sur les fiches a ouvert l'ecran « Evenement ».
=> Lecture de la liste : AUCUN appui (tap), defilement par le bord droit de l'ecran.
=> 29/10/2026, demande de Loulou : ouverture de la fiche de la 1re etape (adresse complete, telephones, memo).
   UN SEUL tap par lancement, uniquement au centre du bouton « i » de cette fiche, seulement si l'ecran
   « Missions en cours » est stable (2 captures identiques) et le bouton entier a l'ecran ; apres le tap,
   si l'ecran n'est pas « Mission - Detail » -> touche Retour et arret. Sortie du detail par la touche Retour,
   jamais par un tap. Aucun autre tap a ajouter ici (les icones a droite des fiches declarent des evenements).

Lecture d'une fiche (regle verifiee le 27/10/2026 sur les 4 fiches, contre l'ecran de detail) :
  une fiche = une etape ; la ligne « Ref » = le lieu de l'etape ;
  fiche avec « Dest : » = CHARGEMENT (la marchandise part vers Dest) ;
  fiche avec « Exp : »  = DECHARGEMENT (la marchandise vient d'Exp).
"""
import html, re, subprocess, sys, time

SSH = ["ssh", "-i", "/home/USER/.ssh/id_ed25519", "-o", "UserKnownHostsFile=/home/USER/.ssh/known_hosts",
       "-o", "ConnectTimeout=8", "-o", "BatchMode=yes", "-p", "8022", "100.122.224.83"]


MOTIF = re.compile(r"""text=(?:"([^"]*)"|'([^']*)')[^>]*bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]" """.strip())
TETE = re.compile(r"^N°\d+\nLe \d\d/\d\d/\d{4}\nDe \d\d:\d\d")


SHIZUKU = "SHIZUKU_ARRETE : Shizuku est arrete sur le telephone de Loulou (souvent apres un redemarrage du telephone). Rien ne peut etre lu ni touche (logiciel-missions, GPS, appels, SMS) tant qu'il ne l'a pas relance lui-meme, en Wi-Fi."  # 03/11/2026 : Shizuku arrete (telephone redemarre) -> le dire, et ne jamais relire une ancienne capture


def sh(cmd, timeout=40):
    r = subprocess.run(SSH + ["rish -c '%s'" % cmd.replace("'", "'\\''")], capture_output=True, text=True, timeout=timeout)
    if "Connection refused" in r.stderr or "timed out" in r.stderr:
        print("ERREUR telephone injoignable (Termux ferme ?)"); sys.exit(1)
    if "not running" in r.stdout + r.stderr:
        print(SHIZUKU); sys.exit(3)
    return r.stdout


def _dump1():
    x = sh("rm -f /sdcard/akd.xml; uiautomator dump /sdcard/akd.xml >/dev/null 2>&1; cat /sdcard/akd.xml")
    out = []
    if "</hierarchy>" not in x:  # fichier coupe en cours d'ecriture : capture a refaire
        return []
    # uiautomator met l'attribut entre apostrophes quand le texte contient des guillemets (ex. SITE ... DIT "...")
    for m in re.finditer(MOTIF, x):
        t = html.unescape(m.group(1) if m.group(1) is not None else m.group(2)).replace("\r", "").strip()
        if t:
            out.append((t, *map(int, m.group(3, 4, 5, 6))))
    return out


def dump():
    """Capture complete : autant d'en-tetes « N° » que de lignes « Ref » (uiautomator rate souvent)."""
    best = []
    for _ in range(6):
        # si Loulou (ou une autre appli) est repasse devant, on remet logiciel-missions au premier plan avant de lire
        if "com.logiciel-missions.xmobile" not in sh("dumpsys activity activities | grep -m1 topResumedActivity"):
            sh("monkey -p com.logiciel-missions.xmobile 1 >/dev/null 2>&1"); time.sleep(3)
        n = _dump1()
        heads = sum(1 for x in n if TETE.match(x[0]))
        refs = sum(1 for x in n if x[0].startswith("Ref"))
        if n and heads >= refs:
            return n
        best = n or best
        time.sleep(1.5)
    return best


def scroll(vers_le_bas):
    # bord droit de l'ecran (x=1150), loin des textes et des boutons ; long geste = jamais un appui
    if vers_le_bas:
        sh("input swipe 1150 2000 1150 1300 600")  # petit pas : aucune fiche coupee deux fois
    else:
        sh("input swipe 1150 1000 1150 2300 300")  # partir du milieu : un geste parti du haut ouvre le volet Honor


PHONE = r"""
PREV=$(dumpsys activity activities | grep -m1 topResumedActivity | grep -o "u0 [^ /]*" | cut -d" " -f2)
echo "$PREV" > /sdcard/akprev.txt
cmd statusbar collapse >/dev/null 2>&1
monkey -p com.logiciel-missions.xmobile 1 >/dev/null 2>&1
sleep 2.5
input swipe 1150 1000 1150 2300 250
sleep 1.2
for k in 1 2 3 4; do
  rm -f /sdcard/akd.xml; uiautomator dump /sdcard/akd.xml >/dev/null 2>&1
  H=$(grep -o "N°[0-9]" /sdcard/akd.xml 2>/dev/null | wc -l); R=$(grep -o "Ref :" /sdcard/akd.xml 2>/dev/null | wc -l)
  grep -q "</hierarchy>" /sdcard/akd.xml 2>/dev/null && grep -q "Missions en cours" /sdcard/akd.xml && [ "$H" -ge "$R" ] && [ "$H" -gt 0 ] && break
  grep -q -e "Mission - D" -e "nement" /sdcard/akd.xml 2>/dev/null && input keyevent 4
  sleep 1
done
# retour a l'appli d'avant (27/10/2026) : Friday tourne DANS Chrome (appli web), donc PREV=com.android.chrome ;
# relancer Chrome par son nom ouvrait la page Google au lieu de Friday -> pour Chrome on fait « Retour », qui ramene
# a la tache precedente (Friday). Pour un itineraire (RETOUR=0) on ne revient pas : Eurowag passe devant juste apres.
if [ "$RETOUR" = 1 ] && [ -n "$PREV" ] && [ "$PREV" != com.logiciel-missions.xmobile ]; then
  if [ "$PREV" = com.android.chrome ]; then input keyevent 4; else monkey -p "$PREV" 1 >/dev/null 2>&1; fi
fi
"""


def parse(x):
    out = []
    if "</hierarchy>" not in x:
        return out
    for m in re.finditer(MOTIF, x):
        t = html.unescape(m.group(1) if m.group(1) is not None else m.group(2)).replace("\r", "").strip()
        if t:
            out.append((t, *map(int, m.group(3, 4, 5, 6))))
    return out


def ranger(n, cartes):
    heads = sorted([x for x in n if TETE.match(x[0])], key=lambda x: x[2])
    for k, h in enumerate(heads):
        y_fin = heads[k + 1][2] if k + 1 < len(heads) else 99999
        bloc = [x for x in n if x[1] >= 500 and h[2] - 5 <= x[2] < y_fin and x[0] != "i"]
        statut = [x[0] for x in n if x[1] < 500 and h[2] < x[2] < y_fin and not x[0].startswith("N°")]
        c = cartes.setdefault(h[0], {"tete": h[0], "statut": "", "ref": "", "dest": "", "exp": ""})
        if statut: c["statut"] = statut[0]
        for x in bloc:
            if x[0].startswith("Ref"): c["ref"] = x[0]
            elif x[0].startswith("Dest"): c["dest"] = x[0]
            elif x[0].startswith("Exp"): c["exp"] = x[0]


cartes = {}
# Tout se fait en UNE seule session sur le telephone (chaque commande separee coute 2 a 4 s) : environ 15 s.
import base64
ITI = "--itineraire" in sys.argv  # --itineraire : rester sur le telephone pour le GPS
# 03/11/2026 : --cible "phrase de Loulou" -> ouvrir l'etape qu'il nomme (cible.py), pas toujours la plus tot
CIBLE = sys.argv[sys.argv.index("--cible") + 1] if "--cible" in sys.argv[:-1] else ""
DETAIL = "--rapide" not in sys.argv  # 29/10/2026 : par defaut on ouvre la fiche de la 1re etape (adresse, tel, memo)
RETOUR = "0" if (ITI or DETAIL) else "1"
b = base64.b64encode(("RETOUR=%s\n" % RETOUR + PHONE).encode()).decode()
# 03/11/2026 : Shizuku arrete (telephone redemarre) -> le dire, et ne jamais relire une ancienne capture
r = subprocess.run(SSH + ["echo %s | base64 -d > /sdcard/akfast.sh; T0=$(date +%%s); "
                          "rish -c 'sh /sdcard/akfast.sh' 2>&1 | grep -q 'not running' && echo SHIZUKU_ARRETE; "
                          "[ \"$(stat -c %%Y /sdcard/akd.xml 2>/dev/null || echo 0)\" -ge \"$T0\" ] && echo AKD_NEUF; "
                          "cat /sdcard/akd.xml" % b],
                   capture_output=True, text=True, timeout=90)
if "Connection refused" in r.stderr or "timed out" in r.stderr:
    print("ERREUR telephone injoignable (Termux ferme ?)"); sys.exit(1)
if "SHIZUKU_ARRETE" in r.stdout:
    print(SHIZUKU); sys.exit(3)
if "AKD_NEUF" not in r.stdout:
    print("ERREUR capture de l'ecran du telephone pas faite (ancienne capture ignoree, rien touche) : redemander"); sys.exit(1)
nodes = parse(r.stdout)
if not any(x[0] == "Missions en cours" for x in nodes):
    print("ERREUR liste « Missions en cours » non lue dans logiciel-missions (rien touche) : redemander"); sys.exit(1)
ranger(nodes, cartes)

if not cartes:
    print("Aucune mission lue"); sys.exit(1)


def propre(v, prefixe):
    v = re.sub(r"^%s\s*:\s*" % prefixe, "", v)
    return " | ".join(p.strip() for p in v.split("\n") if p.strip())


# 03/11/2026 : ordre date, debut, puis FIN du creneau (meme debut : celle qui ferme en premier) -> cible.py
from cible import cle, choisir  # noqa: E402


manque = []
num = lambda c: c["tete"].split("\n")[0]
for n_ in sorted({num(c) for c in cartes.values()}):
    types = {("C" if c["dest"] else "D" if c["exp"] else "?") for c in cartes.values() if num(c) == n_}
    if "D" in types and "C" not in types:
        manque.append("ATTENTION fiche de CHARGEMENT de la mission %s non lue (la marchandise vient de : %s) : verifier dans logiciel-missions"
                      % (n_, "; ".join(propre(c["exp"], "Exp") for c in cartes.values() if num(c) == n_ and c["exp"])))
    if "C" in types and "D" not in types:
        manque.append("ATTENTION fiche de DECHARGEMENT de la mission %s non lue : verifier dans logiciel-missions" % n_)

# lecture rapide : une fiche coupee en bas de l'ecran (ni Dest ni Exp) n'est pas fiable -> on la laisse de cote
cartes = {k: c for k, c in cartes.items() if c["dest"] or c["exp"]}
manque = []  # en lecture rapide, les fiches plus bas dans la liste ne sont pas lues : c'est normal
for c in sorted(cartes.values(), key=cle):
    typ = "CHARGEMENT" if c["dest"] else ("DECHARGEMENT" if c["exp"] else "TYPE INCONNU (voir logiciel-missions)")
    print("=== %s | %s | statut %s" % (c["tete"].replace("\n", " | "), typ, c["statut"].replace("\n", " ")))
    print("  lieu de l'etape : " + (propre(c["ref"], "Ref") or "non affiche dans la liste"))
    if c["dest"]: print("  marchandise a livrer a : " + propre(c["dest"], "Dest"))
    if c["exp"]: print("  marchandise venant de : " + propre(c["exp"], "Exp"))
for m in manque:
    print(m)
print("(Lecture rapide : seulement les fiches visibles en haut de la liste, donc les prochaines etapes)")
print("(Pds = poids indique par logiciel-missions, sans unite ; Pal = nombre de palettes)")


# ---------------- 29/10/2026 : fiche detaillee de la 1re etape ----------------
# Le travail se fait SUR LE TELEPHONE (akdetail-telephone.py, Python de Termux) : captures en ~1 s au lieu de 5.
# Regles de securite : voir l'en-tete de akdetail-telephone.py (un seul appui, sur le « i », sous controle).
import json
LOG = "/home/USER/jarvis/outils/missions-detail.log"
TEL = "/home/USER/jarvis/outils/akdetail-telephone.py"


def log(m):
    try:
        with open(LOG, "a") as f:
            f.write(time.strftime("%d/%m %H:%M:%S ") + m + "\n")
    except Exception:
        pass


def revenir():
    if ITI:
        return
    prev = sh("cat /sdcard/akprev.txt").strip()
    if prev and prev != "com.logiciel-missions.xmobile":
        if prev == "com.android.chrome":
            sh("input keyevent 4")
        else:
            sh("monkey -p %s 1 >/dev/null 2>&1" % prev)


def lire_detail(cible):
    code = base64.b64encode(open(TEL, "rb").read()).decode()
    tete = base64.b64encode(cible["tete"].encode()).decode()
    log("fiche visee : " + cible["tete"].replace("\n", " | "))
    r = subprocess.run(SSH + ['echo %s | base64 -d > ~/akdetail.py; python ~/akdetail.py "$(echo %s | base64 -d)"' % (code, tete)],
                       capture_output=True, text=True, timeout=150)
    try:
        res = json.loads(r.stdout.strip().splitlines()[-1])
    except Exception:
        log("reponse illisible : %r %r" % (r.stdout[-300:], r.stderr[-300:]))
        return None, "ERREUR lecture de la fiche (reponse du telephone illisible) ; voir logiciel-missions"
    for j in res.get("journal", []):
        log("  " + j)
    if not res.get("ok"):
        return None, res.get("erreur", "ERREUR inconnue")
    lignes, vus = [], set()
    for t in res["textes"]:
        for l in t.split("\n"):
            l = l.strip()
            if not l or re.match(r"^(Email|Fixe|Portable|Service|Ref|Contact)\s*:?$", l) or l in vus:
                continue  # champ vide ou doublon
            vus.add(l); lignes.append(l)
    if res.get("coupe"):
        lignes.append("(la fiche continue peut-etre plus bas : verifier le memo dans logiciel-missions)")
    if not res.get("retour_liste"):
        lignes.append("(logiciel-missions est reste sur la fiche ; rien n'a ete valide)")
    return lignes, None


if DETAIL:
    premiere, note_choix = choisir(CIBLE, cartes)
    if note_choix:
        print(note_choix)
    try:
        lignes, err = lire_detail(premiere)
    except Exception as e:
        lignes, err = None, "ERREUR lecture de la fiche : %s" % e
    print()
    print("=== FICHE DETAILLEE DE %s (%s) ===" % ("L'ETAPE DEMANDEE" if note_choix.startswith("CHOIX : etape choisie")
                                                else "LA 1re ETAPE", premiere["tete"].replace("\n", " | ")))
    if err:
        print(err)
    else:
        for l in lignes:
            print("  " + l)
    revenir()

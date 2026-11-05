#!/usr/bin/env python3
"""03/11/2026 (demande de Loulou) : Friday lance un appel depuis le telephone de Loulou ; c'est lui qui parle.
Usage : appel.py "nom" [--chercher]  |  appel.py --numero 06XXXXXXXX     (--chercher = trouve le contact sans appeler)
Sorties : APPEL <nom> | <numero>  /  PLUSIEURS <nom1> ; <nom2>...  /  AUCUN  /  ERREUR ...
Contacts lus a chaque fois dans le telephone (rien n'est copie sur le NAS)."""
import re, subprocess, sys, unicodedata
SSH = ["ssh", "-i", "/home/USER/.ssh/id_ed25519", "-o", "UserKnownHostsFile=/home/USER/.ssh/known_hosts",
       "-o", "ConnectTimeout=8", "-o", "BatchMode=yes", "-p", "8022", "100.122.224.83"]


def sh(cmd, timeout=25):
    r = subprocess.run(SSH + ["rish -c '%s'" % cmd], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=timeout)
    # rish envoie une partie de la sortie sur stderr : on lit les deux, sinon la liste des contacts est coupee
    if r.returncode == 255:
        print("ERREUR telephone injoignable"); sys.exit(1)
    if "Server is not running" in r.stdout:  # 03/11/2026 : Shizuku arrete (telephone redemarre) -> le dire, et ne jamais relire une ancienne capture
        print("SHIZUKU_ARRETE"); sys.exit(1)
    return r.stdout


def simple(t):
    t = unicodedata.normalize("NFD", t.lower())
    return re.sub(r"[^a-z0-9 ]", " ", "".join(c for c in t if unicodedata.category(c) != "Mn")).split()


def numero(n):
    d = re.sub(r"\D", "", n)
    if d.startswith("33") and len(d) == 11:
        d = "0" + d[2:]
    return d


def contacts():
    c = {}
    for l in sh("content query --uri content://com.android.contacts/data/phones --projection display_name:data1").splitlines():
        m = re.search(r"display_name=(.*), data1=(.*)$", l)
        if m and len(numero(m.group(2))) >= 10:
            c.setdefault(m.group(1).strip(), set()).add(numero(m.group(2)))
    return c


def trouver(q, c):
    mots = [w for w in simple(q) if w not in ("le", "la", "les", "de", "du", "a", "mon", "ma")]
    if not mots:
        return []
    exacts = [n for n in c if simple(n) == mots]
    if exacts:
        return exacts
    return [n for n in c if all(any(x.startswith(w) for x in simple(n)) for w in mots)]


HP_RE = re.compile(r'<node [^>]*checked="(true|false)"[^>]*clickable="true"[^>]*bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"[^>]*>\s*'
                   r'<node [^>]*content-desc="Haut-parleur"')


def bouton_hp():
    x = sh("rm -f /sdcard/hpd.xml; uiautomator dump /sdcard/hpd.xml >/dev/null 2>&1; cat /sdcard/hpd.xml")
    m = HP_RE.search(x)
    if not m:
        return None, None
    x1, y1, x2, y2 = map(int, m.groups()[1:])
    return m.group(1) == "true", ((x1 + x2) // 2, (y1 + y2) // 2)


def haut_parleur():
    """03/11/2026 (Loulou) : appel lance par Friday -> haut-parleur active. L'etat se lit sur le bouton de l'ecran
    d'appel (checked="true" = haut-parleur allume, verifie sur un vrai appel) : on n'appuie que s'il est eteint."""
    import time
    sh("for i in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20; do dumpsys telephony.registry | grep -q mCallState=2 && break; sleep 0.5; done; sleep 1")
    for _ in range(3):
        actif, pos = bouton_hp()
        if actif:
            return "HAUT-PARLEUR OK"
        if pos:
            sh("input tap %d %d" % pos)
        time.sleep(1.2)
    actif, _ = bouton_hp()
    return "HAUT-PARLEUR OK" if actif else "HAUT-PARLEUR NON (bouton introuvable ou pas allume)"

if "--numero" in sys.argv:  # appel direct d'un numero deja choisi (utilise par Friday)
    num = numero(sys.argv[sys.argv.index("--numero") + 1])
    if len(num) < 10:
        print("ERREUR numero invalide"); sys.exit(1)
    sh("am start -a android.intent.action.CALL -d tel:%s" % num)
    print("APPEL | %s | %s" % (num, haut_parleur())); sys.exit(0)
q = " ".join(a for a in sys.argv[1:] if not a.startswith("--"))
c = contacts()
r = trouver(q, c)
if not r:
    print("AUCUN"); sys.exit(0)
if len(r) > 1:
    print("PLUSIEURS " + " ; ".join(sorted(r))); sys.exit(0)
nom = r[0]
nums = sorted(c[nom], key=lambda n: (not n.startswith(("06", "07")), n))
if len([n for n in nums if n.startswith(("06", "07"))]) > 1:
    print("PLUSIEURS " + " ; ".join("%s (%s)" % (nom, n) for n in nums)); sys.exit(0)
if "--chercher" not in sys.argv:
    sh("am start -a android.intent.action.CALL -d tel:%s" % nums[0])
print("APPEL %s | %s" % (nom, nums[0]))

#!/usr/bin/env python3
"""03/11/2026 (demande de Loulou) : Friday envoie un SMS depuis le telephone de Loulou, APRES sa confirmation.
  sms.py --analyser "PRENOM_A pour lui dire que je rentre vers 19 h"
      -> CONTACT <nom> | <numero> | <texte>   /  PLUSIEURS <choix1> ; <choix2>  /  AUCUN  /  SANSTEXTE <nom>
  sms.py --envoyer <numero> <texte>     (refuse sans FRIDAY_SMS=raccourci : seul le serveur Friday envoie)
Envoi : appli Messages de Google ouverte avec le texte, appui sur « Envoyer », verification, retour a l'appli d'avant.
Pas de Termux:API : sa version Google Play n'existe pas (Termux de Loulou vient du Play Store)."""
import base64, os, re, subprocess, sys, time, unicodedata
SSH = ["ssh", "-i", "/home/USER/.ssh/id_ed25519", "-o", "UserKnownHostsFile=/home/USER/.ssh/known_hosts",
       "-o", "ConnectTimeout=8", "-o", "BatchMode=yes", "-p", "8022", "100.122.224.83"]
MSG = "com.google.android.apps.messaging"


def sh(cmd, timeout=30):
    # rish envoie une partie de la sortie sur stderr : on lit les deux
    r = subprocess.run(SSH + ["rish -c '%s'" % cmd.replace("'", "'\\''")], stdout=subprocess.PIPE,
                       stderr=subprocess.STDOUT, text=True, timeout=timeout)
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
    return "0" + d[2:] if d.startswith("33") and len(d) == 11 else d


def contacts():
    c = {}
    for l in sh("content query --uri content://com.android.contacts/data/phones --projection display_name:data1").splitlines():
        m = re.search(r"display_name=(.*), data1=(.*)$", l)
        if m and len(numero(m.group(2))) >= 10:
            c.setdefault(m.group(1).strip(), set()).add(numero(m.group(2)))
    return c


def trouver(mots, c):
    mots = [w for w in mots if w not in ("le", "la", "les", "de", "du", "mon", "ma")]
    if not mots:
        return []
    exacts = [n for n in c if simple(n) == mots]
    return exacts or [n for n in c if all(any(x.startswith(w) for x in simple(n)) for w in mots)]


SEP = re.compile(r"^\s*(?:[:,.\-]\s*)?(?:pour (?:lui |leur )?dire(?: que| qu')?|pour (?:lui |leur )?(?:demander|annoncer)|"
                 r"(?:en )?(?:lui |leur )?disant(?: que| qu')?|que |qu'|:)?\s*", re.I)


def analyser(reste):
    c = contacts()
    mots_orig = reste.split()
    for k in (3, 2, 1):  # le nom le plus long qui correspond a un contact
        if len(mots_orig) <= k - 1:
            continue
        r = trouver(simple(" ".join(mots_orig[:k])), c)
        if not r:
            continue
        texte = SEP.sub("", " ".join(mots_orig[k:]), count=1).strip()
        if len(r) > 1:
            return "PLUSIEURS " + " ; ".join(sorted(r)) + " || " + texte
        nom = r[0]
        mob = sorted(n for n in c[nom] if n.startswith(("06", "07")))
        if len(mob) > 1:
            return "PLUSIEURS " + " ; ".join("%s (%s)" % (nom, n) for n in mob) + " || " + texte
        num = (mob or sorted(c[nom]))[0]
        if not texte:
            return "SANSTEXTE %s | %s" % (nom, num)
        return "CONTACT %s | %s | %s" % (nom, num, texte[0].upper() + texte[1:])
    return "AUCUN"


def bouton_envoyer(xml):
    for m in re.finditer(r"<node [^>]*>", xml):
        n = m.group(0)
        if re.search(r'resource-id="%s:id/(send_message_button\w*|compose_message_send\w*)"' % re.escape(MSG), n) or \
           re.search(r'content-desc="(Envoyer[^"]*|Send SMS|Send message)"', n):
            b = re.search(r'bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"', n)
            if b:
                x1, y1, x2, y2 = map(int, b.groups())
                return (x1 + x2) // 2, (y1 + y2) // 2
    return None


def dump():
    return sh("rm -f /sdcard/smsd.xml; uiautomator dump /sdcard/smsd.xml >/dev/null 2>&1; cat /sdcard/smsd.xml")


def envoyer(num, texte):
    if os.environ.get("FRIDAY_SMS") != "raccourci":
        print("ERREUR envoi refusé : seul Friday (après confirmation de Loulou) peut envoyer un SMS"); sys.exit(1)
    num = numero(num)
    b64 = base64.b64encode(texte.encode()).decode()
    sh('P=$(dumpsys activity activities | grep -m1 topResumedActivity | grep -o "u0 [^ /]*" | cut -d" " -f2); '
       'echo "$P" > /sdcard/smsprev.txt; cmd statusbar collapse >/dev/null 2>&1; '
       'am start -a android.intent.action.SENDTO -d smsto:%s --es sms_body "$(echo %s | base64 -d)" >/dev/null 2>&1' % (num, b64))
    pos = None
    for _ in range(6):
        time.sleep(1.2)
        x = dump()
        if MSG in x:
            pos = bouton_envoyer(x)
            if pos:
                break
    if not pos:
        print("ERREUR bouton Envoyer introuvable : message prêt dans Messages, pas envoyé"); sys.exit(1)
    sh("input tap %d %d" % pos)
    time.sleep(2.5)
    x = dump()
    # 03/11/2026 : preuve = le SMS apparait (SMS classique) dans le journal des SMS du telephone (envoye ou en cours d'envoi)
    envoye = False
    for _ in range(4):
        j = sh('content query --uri content://sms --projection address:body:type --sort "date DESC LIMIT 5"')
        if any(texte[:30] in l and re.search(r"type=(2|4|6)\b", l) for l in j.splitlines()):
            envoye = True
            break
        time.sleep(2)
    if not envoye:  # envoi en « chat » (RCS) : absent du journal des SMS -> preuve a l'ecran (texte sorti de la zone de saisie, visible dans la conversation)
        x = dump()
        champ = "".join(re.findall(r'<node [^>]*class="android.widget.EditText"[^>]*>', x))
        envoye = MSG in x and texte[:25] in x.replace(champ, "") and texte[:15] not in champ
    sh('P=$(cat /sdcard/smsprev.txt 2>/dev/null); if [ -n "$P" ] && [ "$P" != %s ]; then monkey -p "$P" 1 >/dev/null 2>&1; fi' % MSG)
    champ = re.search(r'<node [^>]*class="android.widget.EditText"[^>]*>', x)
    if champ and texte[:15] in champ.group(0):
        print("ERREUR le texte est resté dans la zone de saisie : pas envoyé"); sys.exit(1)
    print("ENVOYE %s" % num if envoye else "ENVOYE? appui fait, mais le SMS n'apparait pas dans le journal des SMS")


if __name__ == "__main__":
    if "--analyser" in sys.argv:
        print(analyser(sys.argv[sys.argv.index("--analyser") + 1]))
    elif "--envoyer" in sys.argv:
        i = sys.argv.index("--envoyer")
        envoyer(sys.argv[i + 1], sys.argv[i + 2])
    else:
        print(__doc__)

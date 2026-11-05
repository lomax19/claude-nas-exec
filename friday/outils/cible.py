"""Choix de l'etape logiciel-missions d'apres ce que Loulou a dit (03/11/2026). Utilise par missions.py (--cible "sa phrase").

Avant : l'outil prenait TOUJOURS l'etape la plus tot, meme si Loulou disait « la deuxieme », « celle qui va a
Messia-sur-Sorne » ou « chez United Caps » (2 chargements le meme jour a 8 h -> c'etait toujours le 1er de l'ecran).
Regles :
  - un n° de mission (5 chiffres ou plus) l'emporte ;
  - sinon les noms (ville, entreprise...) : seuls comptent les mots qui departagent les missions
    (« ta ville » ou un code postal commun aux deux ne servent a rien) ; la dictee deforme : tolerance sur les mots longs ;
  - sinon un rang : « la deuxieme / troisieme / derniere » = dans l'ordre de la liste logiciel-missions ; « l'autre » (2 missions) ;
    « premiere » et « prochaine » = l'etape la plus tot (comportement d'avant) ;
  - « dechargement / livraison » ou « chargement » choisit l'etape dans la mission (sinon la plus tot de la mission).
Ordre « le plus tot » : date, heure de debut, puis heure de FIN (meme debut : celle qui ferme en premier d'abord).
"""
import difflib, re, unicodedata

VIDES = set((
    "friday mission missions etape etapes logiciel-missions lance lancer lancez lances moi toi celle celui cette ces "
    "regarde regardes mets met prochaine prochain suivante suivant premiere premier deuxieme seconde second "
    "troisieme quatrieme derniere dernier autre chargement chargements dechargement dechargements livraison livraisons "
    "charger decharger livrer qui que quoi quel quelle est pas oui non bon bien fait dit faut faudrait veux "
    "voudrais peux tout tous toute toutes deux trois une des les pour chez dans sur avec sans mon mes ton tes "
    "son ses leur elle elles lui nous vous ils gps itineraire trompe trompee trompes ouvre donne aujourd hui demain "
    "matin soir apres midi alors donc mais aussi deja encore plus moins puis ensuite maintenant rue route "
    "avenue chemin impasse boulevard place zone zac quai lieu ref dest exp pal pds part vient aller allez "
    "va vont cela ceci voila celle-la plait merci svp ici la-bas cette-la").split())
ORDINAL = {"deuxieme": 1, "seconde": 1, "second": 1, "2e": 1, "2eme": 1, "troisieme": 2, "3e": 2, "3eme": 2,
           "quatrieme": 3, "4e": 3, "derniere": -1, "dernier": -1}
MARQUE_CIBLE = re.compile(r"\bqui (va|vont|part|vient|livre)\b|\bchez\b|\bcelle\b|n ?°|\bnumero\b|\d{5,}")


def sa(s):
    s = unicodedata.normalize("NFD", (s or "").lower())
    return "".join(ch for ch in s if unicodedata.category(ch) != "Mn")


def mots(s):
    return re.findall(r"[a-z0-9]+", sa(s).replace("°", " "))


def cle(c):
    m = re.search(r"Le (\d\d)/(\d\d)/(\d{4}).*?De (\d\d):(\d\d)(?:\D+(\d\d):(\d\d))?", c["tete"].replace("\n", " "))
    if not m:
        return ("9999",)
    return (m.group(3), m.group(2), m.group(1), m.group(4), m.group(5), m.group(6) or "99", m.group(7) or "99")


def num(c):
    return c["tete"].split("\n")[0]


def typ(c):
    return "C" if c.get("dest") else ("D" if c.get("exp") else "?")


def _proche(x, t):
    return x == t or (len(x) >= 5 and len(t) >= 5 and difflib.SequenceMatcher(None, x, t).ratio() >= 0.8)


def choisir(phrase, cartes):
    """-> (carte, note). note vide = rien de particulier demande (etape la plus tot, comme avant)."""
    toutes = sorted(cartes.values(), key=cle)
    defaut = toutes[0]
    w = mots(phrase)
    if not w:
        return defaut, ""
    ordre = []  # missions dans l'ordre de l'ecran logiciel-missions
    for c in cartes.values():
        if num(c) not in ordre:
            ordre.append(num(c))
    txt = {n_: set(mots(" ".join(c["tete"] + " " + c["ref"] + " " + c["dest"] + " " + c["exp"]
                                for c in cartes.values() if num(c) == n_))) for n_ in ordre}
    choisie, raison = None, ""
    # 1) numero de mission
    for x in w:
        if len(x) >= 5 and x.isdigit():
            for n_ in ordre:
                if x in n_:
                    choisie, raison = n_, "numero %s" % x
    # 2) noms : seulement les mots qui departagent les missions
    communs = []
    if not choisie:
        score, vus = {n_: 0 for n_ in ordre}, {n_: [] for n_ in ordre}
        for x in dict.fromkeys(w):
            if len(x) < 3 or x in VIDES or x.isdigit():
                continue
            ok = [n_ for n_ in ordre if any(_proche(x, t) for t in txt[n_])]
            if ok and len(ok) == len(ordre) > 1:
                communs.append(x)
            if ok and (len(ok) < len(ordre) or len(ordre) == 1):
                for n_ in ok:
                    score[n_] += 1
                    vus[n_].append(x)
        best = max(score.values())
        gagnants = [n_ for n_ in ordre if score[n_] == best]
        if best > 0 and len(gagnants) == 1:
            choisie, raison = gagnants[0], "mots reconnus : %s" % ", ".join(vus[gagnants[0]])
    # 3) rang dans la liste logiciel-missions, ou « l'autre »
    rang_impossible = ""
    if not choisie:
        for x in w:
            if x in ORDINAL:
                k = ORDINAL[x]
                if -len(ordre) <= k < len(ordre):
                    choisie, raison = ordre[k], "« %s » mission de la liste logiciel-missions" % x
                else:
                    rang_impossible = x
                break
    if not choisie and "autre" in w and len(ordre) == 2:
        choisie, raison = next(n_ for n_ in ordre if n_ != num(defaut)), "« l'autre » mission"
    # type d'etape demande
    t = " ".join(w)
    veut = "D" if re.search(r"\b(decharg|livraison|livrer)", t) else ("C" if re.search(r"\b(charg|enlev)", t) else "")
    pool = [c for c in toutes if not choisie or num(c) == choisie]
    note_type = ""
    if veut and any(typ(c) == veut for c in pool):
        pool = [c for c in pool if typ(c) == veut]
    elif veut and choisie:
        note_type = (" ; ATTENTION : la fiche de %s de cette mission n'est pas visible en haut de la liste logiciel-missions, "
                     "c'est l'autre etape qui est donnee" % ("DECHARGEMENT" if veut == "D" else "CHARGEMENT"))
    carte = pool[0]
    if choisie or (veut and carte is not defaut):
        return carte, ("CHOIX : etape choisie d'apres sa demande (%s) : mission %s, %s%s"
                       % (raison or ("dechargement" if veut == "D" else "chargement"), num(carte),
                          "DECHARGEMENT" if typ(carte) == "D" else "CHARGEMENT", note_type))
    if rang_impossible:
        return defaut, ("CHOIX : il a demande la « %s » mission mais il n'y en a que %d en haut de la liste logiciel-missions : "
                        "c'est l'etape la plus tot qui est donnee" % (rang_impossible, len(ordre)))
    if communs:
        return defaut, ("CHOIX : « %s » se retrouve dans toutes ses missions, ca ne permet pas de choisir : c'est l'etape "
                        "la plus tot qui est donnee ; demande-lui le nom de l'entreprise ou de la ville de chargement"
                        % ", ".join(communs))
    if MARQUE_CIBLE.search(sa(phrase)):
        return defaut, ("CHOIX : sa demande (« %s ») ne correspond a aucune etape visible en haut de la liste logiciel-missions : "
                        "c'est l'etape la plus tot qui est donnee ; propose-lui « regarde mes missions »" % phrase[:120])
    return defaut, ""

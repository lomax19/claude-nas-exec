#!/usr/bin/env python3
"""Friday : lire les mails des 3 boites Gmail de Loulou (ton-compte, ton-compte, grtransports). LECTURE SEULE.
Utilise les acces du tri local (/home/USER/gmail-triage/token_*.json) sans jamais les reecrire
(le renouvellement est fait en memoire ; c'est gmail_agent.py qui garde les fichiers a jour).

  mails.py ["<requete gmail>"] [--compte ton-compte|ton-compte|grtransports|tous] [--max N]
      defaut : "newer_than:2d -in:sent" (le tri range les mails hors de la reception), les 3 boites, 8 mails par boite
  mails.py --lire <compte> <id>      texte complet d'un mail
28/10/2026 : le connecteur Gmail de Claude n'ouvre que ton-compte ; cet outil couvre les 3 boites."""
import argparse
import os
import warnings

os.environ["PYTHONWARNINGS"] = "ignore"
warnings.simplefilter("ignore")
import base64
import html
import re
import sys

warnings.filterwarnings("ignore")
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

BASE = "/home/USER/gmail-triage"
COMPTES = ["ton-compte", "ton-compte", "grtransports"]


def service(compte):
    creds = Credentials.from_authorized_user_file("%s/token_%s.json" % (BASE, compte))
    if not creds.valid and creds.refresh_token:
        creds.refresh(Request())  # en memoire seulement
    return build("gmail", "v1", credentials=creds, cache_discovery=False)


def entete(m, nom):
    return next((h["value"] for h in m.get("payload", {}).get("headers", []) if h["name"].lower() == nom), "")


def corps(part):
    """Texte brut du mail (text/plain, sinon html nettoye)."""
    plain, htm = [], []

    def parcours(p):
        mt, data = p.get("mimeType", ""), (p.get("body") or {}).get("data")
        if data and mt == "text/plain":
            plain.append(base64.urlsafe_b64decode(data).decode("utf-8", "replace"))
        elif data and mt == "text/html":
            htm.append(base64.urlsafe_b64decode(data).decode("utf-8", "replace"))
        for q in p.get("parts") or []:
            parcours(q)
    parcours(part)
    if plain:
        t = "\n".join(plain)
    else:
        t = re.sub(r"(?is)<(script|style).*?</\1>", " ", "\n".join(htm))
        t = html.unescape(re.sub(r"<[^>]+>", " ", re.sub(r"(?i)<br\s*/?>|</p>|</div>|</tr>", "\n", t)))
    return re.sub(r"\n\s*\n+", "\n\n", re.sub(r"[ \t\xa0]+", " ", t)).strip()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("requete", nargs="?", default="newer_than:2d -in:sent")
    ap.add_argument("--compte", default="tous")
    ap.add_argument("--max", type=int, default=8)
    ap.add_argument("--lire", nargs=2, metavar=("COMPTE", "ID"))
    a = ap.parse_args()
    if a.lire:
        compte, mid = a.lire
        if compte not in COMPTES:
            sys.exit("compte inconnu : " + compte)
        m = service(compte).users().messages().get(userId="me", id=mid, format="full").execute()
        print("Boîte : %s\nDe : %s\nÀ : %s\nDate : %s\nSujet : %s\n" % (
            compte, entete(m, "from"), entete(m, "to"), entete(m, "date"), entete(m, "subject")))
        print(corps(m.get("payload", {}))[:8000])
        return
    comptes = COMPTES if a.compte == "tous" else [a.compte]
    for compte in comptes:
        if compte not in COMPTES:
            print("== %s : compte inconnu" % compte)
            continue
        try:
            s = service(compte)
            r = s.users().messages().list(userId="me", q=a.requete, maxResults=a.max).execute()
            ids = [x["id"] for x in r.get("messages", [])]
            print("== %s : %s mail(s)%s" % (compte, len(ids), " (au moins)" if r.get("nextPageToken") else ""))
            for mid in ids:
                m = s.users().messages().get(userId="me", id=mid, format="metadata",
                                             metadataHeaders=["From", "Subject", "Date"]).execute()
                lu = "" if "UNREAD" not in m.get("labelIds", []) else " [NON LU]"
                imp = " [IMPORTANT]" if "IMPORTANT" in m.get("labelIds", []) else ""
                print("- id %s | %s | de %s | %s%s%s\n  %s" % (
                    mid, entete(m, "date")[:25], entete(m, "from")[:60], entete(m, "subject")[:120], lu, imp,
                    html.unescape(m.get("snippet", ""))[:200]))
        except Exception as e:  # noqa: BLE001
            print("== %s : ERREUR %s" % (compte, repr(e)[:200]))


if __name__ == "__main__":
    main()

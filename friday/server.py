#!/usr/bin/env python3
"""Jarvis - interface unique pour discuter avec Freeplex NAS (Claude Code sur l'hote).

- page web (halo + conversation) servie sur PORT
- /api/ask : aiguille chaque message (28/10/2026) : Gemini pour le simple (maison, heure, rappels,
  bavardage), sinon `claude -p` (reprend la conversation) dont le travail est suivi en direct
- hooks Claude Code (/hook) : affiche aussi ce que fait Freeplex NAS depuis l'appli Claude
- voix : Piper (Wyoming) pour la page, notification TTS Home Assistant pour le telephone
- dictee : Whisper (Wyoming) si la page a acces au micro (https)
Bibliotheque standard uniquement."""
import io
import json
import os
import queue
import random
import re
import secrets
import signal
import socket
import subprocess
import threading
import time
import unicodedata
import urllib.error
import urllib.request
import uuid
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.abspath(__file__))
E = os.environ.get
PORT = int(E("PORT", "8790"))
PIPER = (E("PIPER_HOST", "127.0.0.1"), int(E("PIPER_PORT", "10200")))
WHISPER = (E("WHISPER_HOST", "127.0.0.1"), int(E("WHISPER_PORT", "10300")))
SPEAK_MAX = int(E("SPEAK_MAX", "700"))
HA_URL = E("HA_URL", "http://127.0.0.1:8123").rstrip("/")
HA_TOKEN_FILE = E("HA_TOKEN_FILE", "/home/USER/.ha_token")
PHONE_NOTIFY = E("PHONE_NOTIFY", "mobile_app_tel_loulou")
PHONE_SWITCH = E("PHONE_SWITCH", "input_boolean.lecture_vocale_telephone")
PHONE_MAX = int(E("PHONE_MAX", "500"))
CLAUDE_BIN = E("CLAUDE_BIN", "/home/USER/.local/bin/claude")
CLAUDE_CWD = E("CLAUDE_CWD", "/home/USER")
RUN_TIMEOUT = int(E("RUN_TIMEOUT", "1200"))
PAUSE_NOUVELLE = int(E("FRIDAY_PAUSE_NOUVELLE", "1800"))  # 30 min sans parler = conversation neuve
CONNECTEURS_RETIRES = ["mcp__claude_ai_" + n for n in (
    "Canva", "Higgsfield", "Runway", "ElevenLabs", "Spotify", "Uber", "Uber_Eats", "Tripadvisor",
    "Google_Slides", "Google_Sheets", "Google_Docs", "Claude_Docs",
    # 28/10/2026 : le connecteur Gmail n'ouvre que ton-compte -> Friday concluait « pas de mail ». Lecture = outils/mails.py
    # (les 3 boites) ; le connecteur ne garde que l'ecriture / l'envoi.
    "Gmail__search_threads", "Gmail__get_thread", "Gmail__get_message", "Gmail__list_drafts", "Gmail__get_draft")]
# 24/10/2026 : aiguilleur de modele (haiku / sonnet / opus) choisi a chaque message
MODELE_DEFAUT = E("FRIDAY_MODELE_DEFAUT", "sonnet")  # si le tri echoue
TRI_TIMEOUT = int(E("FRIDAY_TRI_TIMEOUT", "25"))
SECOURS = {"opus": "sonnet", "sonnet": "opus", "haiku": "sonnet"}  # si le modele choisi est sature
HOOK_TOKEN_FILE = os.path.join(ROOT, ".token")
KEY_FILE = os.path.join(ROOT, ".key")
STATE_FILE = os.path.join(ROOT, "state.json")
PROMPT_FILE = os.path.join(ROOT, "consignes.txt")
EL_KEY_FILE = os.path.join(ROOT, ".elevenlabs")
VOICE_FILE = os.path.join(ROOT, "voix.json")
CHOICES_FILE = os.path.join(ROOT, "voix-choix.json")
PREVIEW_DIR = os.path.join(ROOT, "voix")
UPLOAD_DIR = os.path.join(ROOT, "recus")  # fichiers envoyes depuis la page (bouton trombone)
UPLOAD_MAX = int(E("UPLOAD_MAX", str(200 * 1024 * 1024)))
EL_MODEL = E("EL_MODEL", "eleven_v4_turbo")  # 24/10/2026 : abonnement Starter, meme cout que flash, voix plus naturelle (avant : eleven_flash_v2_5)
EL_API = "https://api.elevenlabs.io"


def read(path, default=""):
    try:
        with open(path, encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return default


HOOK_TOKEN = read(HOOK_TOKEN_FILE)
KEY = read(KEY_FILE)

lock = threading.Lock()
clients = set()
current = {"type": "state", "state": "idle", "detail": "", "ts": time.time()}
audio_store, audio_order = {}, []
speech_gen = 0
stats = {"hooks": 0, "asks": 0, "clips": 0, "errors": 0, "last_error": "",
         "phone_sent": 0, "phone_skipped": 0, "phone_last": ""}
run = {"proc": None, "busy": False, "started": 0}
bad_keys = {}

try:
    with open(STATE_FILE, encoding="utf-8") as f:
        saved = json.load(f)
except (OSError, ValueError):
    saved = {}
session_id = saved.get("session_id") or ""
history = saved.get("history") or []


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def save_state():
    with lock:
        data = {"session_id": session_id, "history": history[-120:]}
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    os.replace(tmp, STATE_FILE)


def broadcast(ev):
    data = ("data: " + json.dumps(ev, ensure_ascii=False) + "\n\n").encode()
    with lock:
        for q in list(clients):
            try:
                q.put_nowait(data)
            except queue.Full:
                clients.discard(q)


def set_state(state, detail=""):
    with lock:
        current.update(state=state, detail=detail[:180], ts=time.time(), busy=run["busy"])
        snap = dict(current)
    broadcast(snap)


def add_msg(role, text, source="page", moteur=None, modele=None):
    m = {"type": "msg", "id": uuid.uuid4().hex[:10], "role": role, "text": text[:12000],
         "source": source, "ts": time.time()}
    if moteur:
        m["moteur"] = moteur  # 28/10/2026 : "gemini" ou "claude" (qui a repondu)
    if modele:
        m["modele"] = modele
    with lock:
        history.append(m)
        del history[:-120]
    broadcast(m)
    try:
        save_state()
    except OSError as e:
        log("sauvegarde", repr(e))
    return m


# ---------------------------------------------------------------- texte -> parole
def clean_for_speech(text, limit=None):
    limit = limit or SPEAK_MAX
    t = re.sub(r"```.*?```", " ", text, flags=re.S)
    t = re.split(r"\n\s*\**Sources?\s*:?\**\s*:?\s*\n", t, maxsplit=1)[0]
    lines = []
    for line in t.splitlines():
        s = line.strip()
        if not s or s.startswith("|") or re.fullmatch(r"[-=*_ ]{3,}", s):
            continue
        s = re.sub(r"^#+\s*", "", s)
        s = re.sub(r"^>\s*", "", s)
        s = re.sub(r"^(\d+[.)]|[-*•])\s+", "", s)
        lines.append(s)
    t = "\n".join(lines)
    t = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", t)
    t = re.sub(r"https?://\S+", "", t)
    t = re.sub(r"(?<![\w/])/[\w.@{}~-]+(?:/[\w.@{}~-]*)*", "", t)
    t = re.sub(r"\b[\w.-]+(?:/[\w.@{}~-]+){2,}", "", t)
    t = re.sub(r"`([^`]{1,30})`", lambda m: "" if "/" in m.group(1) else m.group(1), t)
    t = re.sub(r"`[^`]*`", "", t)
    t = re.sub(r"<[^>]+>", "", t)
    t = t.replace("**", "").replace("__", "").replace("*", "")
    t = t.replace("→", ", ").replace("≈", "environ ").replace("°C", " degrés").replace("&", " et ")
    t = re.sub(r"[\U0001F000-\U0001FAFF☀-➿️]", "", t)
    out = []
    for s in t.split("\n"):
        s = s.strip()
        if s:
            out.append(s if s[-1] in ".!?:…;," else s + ".")
    t = re.sub(r"\s+", " ", " ".join(out)).strip()
    t = re.sub(r"\s+([,.;:!?])", r"\1", t)
    t = re.sub(r"([,;:])\s*([.!?])", r"\2", t)
    t = re.sub(r",\s*,", ",", t)
    t = re.sub(r"\(\s*\)", "", t)
    t = re.sub(r"\s+([,.;:!?])", r"\1", t)
    if len(t) > limit:
        cut = t[:limit]
        end = max(cut.rfind(". "), cut.rfind("! "), cut.rfind("? "))
        t = cut[: end + 1] if end > 80 else cut.rsplit(" ", 1)[0] + "."
    return t


def split_sentences(t):
    out, buf = [], ""
    for p in re.split(r"(?<=[.!?…:])\s+", t):
        buf = (buf + " " + p).strip()
        if len(buf) >= 60:
            out.append(buf)
            buf = ""
    if buf:
        if out and len(buf) < 25:
            out[-1] += " " + buf
        else:
            out.append(buf)
    return out


# ---------------------------------------------------------------- wyoming
def wy_send(s, etype, data=None, payload=b""):
    h = {"type": etype}
    if data is not None:
        h["data"] = data
    if payload:
        h["payload_length"] = len(payload)
    s.sendall((json.dumps(h) + "\n").encode() + payload)


def wy_recv(f):
    line = f.readline()
    if not line:
        raise IOError("connexion fermee")
    h = json.loads(line)
    d = h.get("data") or {}
    if h.get("data_length"):
        d.update(json.loads(f.read(h["data_length"])))
    p = f.read(h["payload_length"]) if h.get("payload_length") else b""
    return h["type"], d, p


def synth(text):
    s = socket.create_connection(PIPER, timeout=30)
    try:
        f = s.makefile("rb")
        wy_send(s, "synthesize", {"text": text})
        pcm, fmt = io.BytesIO(), [22050, 2, 1]
        while True:
            t, d, p = wy_recv(f)
            if t == "audio-start":
                fmt = [d.get("rate", 22050), d.get("width", 2), d.get("channels", 1)]
            elif t == "audio-chunk":
                pcm.write(p)
            elif t == "audio-stop":
                break
            elif t == "error":
                raise IOError("piper: " + str(d))
    finally:
        s.close()
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setframerate(fmt[0])
        w.setsampwidth(fmt[1])
        w.setnchannels(fmt[2])
        w.writeframes(pcm.getvalue())
    return out.getvalue()


# 03/11/2026 (accord de Loulou) : dictee par ElevenLabs Scribe (0,6-1,5 s, bien plus juste, compris dans
# l'abonnement : compteur inchange apres essai) ; Whisper du NAS en secours si ElevenLabs echoue ou tarde.
SCRIBE_ACTIF = E("FRIDAY_SCRIBE", "1") == "1"
SCRIBE_MOTS = ["Friday", "logiciel-missions", "Eurowag", "Freeplex", "PRENOM_C", "PRENOM_B", "clim"]
SCRIBE_TIMEOUT = float(E("FRIDAY_SCRIBE_TIMEOUT", "6"))
transcripteur = {"dernier": ""}


def transcribe_scribe(pcm16k):
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000); w.writeframes(pcm16k)
    b = uuid.uuid4().hex
    champs = [("model_id", "scribe_v2"), ("language_code", "fra"), ("tag_audio_events", "false")] + [("keyterms", m) for m in SCRIBE_MOTS]
    corps = b"".join(('--%s\r\nContent-Disposition: form-data; name="%s"\r\n\r\n%s\r\n' % (b, k, v)).encode() for k, v in champs)
    corps += ('--%s\r\nContent-Disposition: form-data; name="file"; filename="dictee.wav"\r\nContent-Type: audio/wav\r\n\r\n' % b).encode()
    corps += buf.getvalue() + ("\r\n--%s--\r\n" % b).encode()
    req = urllib.request.Request(EL_API + "/v1/speech-to-text", data=corps,
                                 headers={"xi-api-key": read(EL_KEY_FILE), "Content-Type": "multipart/form-data; boundary=" + b})
    with urllib.request.urlopen(req, timeout=SCRIBE_TIMEOUT) as r:
        return (json.loads(r.read()).get("text") or "").strip()


def transcribe(pcm16k):
    if SCRIBE_ACTIF:
        try:
            t = transcribe_scribe(pcm16k)
            transcripteur["dernier"] = "elevenlabs"
            return t
        except Exception as e:  # noqa: BLE001  (panne, quota, reseau : Whisper du NAS prend le relais)
            log("dictée ElevenLabs en échec, Whisper du NAS prend le relais :", repr(e)[:150])
            compte("dictee_secours_whisper")
    transcripteur["dernier"] = "whisper"
    return transcribe_whisper(pcm16k)


def transcribe_whisper(pcm16k):
    s = socket.create_connection(WHISPER, timeout=60)
    try:
        f = s.makefile("rb")
        fmt = {"rate": 16000, "width": 2, "channels": 1}
        wy_send(s, "transcribe", {"language": "fr"})
        wy_send(s, "audio-start", fmt)
        for i in range(0, len(pcm16k), 32000):
            wy_send(s, "audio-chunk", fmt, pcm16k[i:i + 32000])
        wy_send(s, "audio-stop", {})
        while True:
            t, d, _ = wy_recv(f)
            if t == "transcript":
                return (d.get("text") or "").strip()
            if t == "error":
                raise IOError("whisper: " + str(d))
    finally:
        s.close()


# 03/11/2026 : nettoyage de la dictee (hallucinations classiques de Whisper sur du bruit, noms deformes)
HALLU_RE = re.compile(r"sous-titr|amara\.org|merci d.avoir regard|abonnez-vous|vous abonner", re.I)
CORRECTIONS = [(re.compile(r"\b(akaneya|akanéa|a canéa|canéa|carnailla)\b", re.I), "logiciel-missions")]


def nettoyer_dictee(t):
    t = (t or "").strip()
    if HALLU_RE.search(t):
        return ""
    morceaux = [m.strip(" .!?,").lower() for m in re.split(r"[.!?]", t) if m.strip(" .!?,")]
    if len(morceaux) >= 3 and len(set(morceaux)) == 1:  # « D'accord. D'accord. D'accord. » = bruit
        return ""
    for motif, bon in CORRECTIONS:
        t = motif.sub(bon, t)
    return t


# ---------------------------------------------------------------- ElevenLabs
def voice_cfg():
    try:
        with open(VOICE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {"engine": "piper", "name": "Piper (NAS)"}


def save_voice(cfg):
    tmp = VOICE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False)
    os.replace(tmp, VOICE_FILE)


def choices():
    try:
        with open(CHOICES_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return []


def el_call(method, path, body=None, timeout=30, raw=False, url=None):
    target = url or (EL_API + path)
    headers = {"Content-Type": "application/json", "Accept": "*/*"}
    host = urllib.request.urlparse(target).hostname or ""
    if host == "elevenlabs.io" or host.endswith(".elevenlabs.io"):
        headers["xi-api-key"] = read(EL_KEY_FILE)  # jamais envoyee a un autre site
    req = urllib.request.Request(target, method=method,
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = r.read()
        return data if raw else (json.loads(data) if data else None)


EL_SPEED = float(E("EL_SPEED", "1.12"))  # debit de la voix (22/10/2026, Loulou : « version 2 »)
_el_settings = {}


def el_settings(voice_id):
    """Reglages enregistres de la voix (stabilite, etc.) + vitesse EL_SPEED, lus une fois par voix."""
    if voice_id not in _el_settings:
        try:
            _el_settings[voice_id] = el_call("GET", "/v1/voices/%s/settings" % voice_id) or {}
        except Exception:  # noqa: BLE001
            return {"speed": EL_SPEED}
    vs = dict(_el_settings[voice_id])
    vs["speed"] = EL_SPEED
    return vs


def el_tts(text, voice_id):
    return el_call("POST", "/v1/text-to-speech/%s?output_format=mp3_44100_128" % voice_id,
                   {"text": text, "model_id": EL_MODEL, "language_code": "fr",
                    "voice_settings": el_settings(voice_id)}, raw=True)


def tts(text):
    """(audio, type mime) : ElevenLabs si choisi, sinon (ou en cas d'echec) Piper."""
    cfg = voice_cfg()
    if cfg.get("engine") == "elevenlabs" and cfg.get("voice_id") and read(EL_KEY_FILE):
        try:
            data = el_tts(text, cfg["voice_id"])
            stats["el_ok"] = stats.get("el_ok", 0) + 1
            stats["el_chars"] = stats.get("el_chars", 0) + len(text)
            return data, "audio/mpeg"
        except Exception as e:  # noqa: BLE001
            msg = repr(e)
            try:
                msg += " " + e.read().decode()[:200]  # corps de l'erreur HTTP
            except Exception:  # noqa: BLE001
                pass
            stats["el_err"] = stats.get("el_err", 0) + 1
            stats["el_last_error"] = time.strftime("%H:%M:%S ") + msg
            log("elevenlabs", msg, "-> Piper")
    return synth(text), "audio/wav"


# ---------------------------------------------------------------- reponses d'attente
ACKS = [
    "Bien entendu, monsieur, je m'en occupe.",
    "Tout de suite, monsieur.",
    "Très bien, je regarde ça immédiatement.",
    "C'est noté, monsieur. Un instant.",
    "Je m'en charge, monsieur.",
    "Bien sûr. Laissez-moi vérifier.",
    "Avec plaisir, monsieur. Je m'occupe de tout.",
    "Entendu. Je regarde cela de près.",
    "Un instant, monsieur, je consulte le système.",
    "Parfait, je lance les vérifications.",
    "Oui, monsieur. Donnez-moi quelques secondes.",
    "Je suis dessus, monsieur.",
]
ACK_DIR = os.path.join(ROOT, "attente")
last_ack = [-1]
ack_lock = threading.Lock()


def ack_audio(i):
    """Phrase d'attente i dans la voix actuelle : generee une seule fois puis gardee sur disque."""
    cfg = voice_cfg()
    key = cfg.get("voice_id") if cfg.get("engine") == "elevenlabs" else "piper"
    d = os.path.join(ACK_DIR, re.sub(r"[^A-Za-z0-9-]", "", key or "piper"))
    for ext, mime in ((".mp3", "audio/mpeg"), (".wav", "audio/wav")):
        fp = os.path.join(d, "%02d%s" % (i, ext))
        if os.path.isfile(fp):
            with open(fp, "rb") as f:
                return f.read(), mime
    data, mime = tts(ACKS[i])
    if (mime == "audio/mpeg") == (key != "piper"):  # pas de cache si ElevenLabs est retombe sur Piper
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "%02d%s" % (i, ".mp3" if mime == "audio/mpeg" else ".wav")), "wb") as f:
            f.write(data)
    return data, mime


def warm_acks():
    with ack_lock:
        for i in range(len(ACKS)):
            try:
                ack_audio(i)
            except Exception as e:  # noqa: BLE001
                log("phrases d'attente", repr(e))
                return


ACK_CTX = ("Tu es Friday, l'assistante vocale de Loulou (chauffeur routier). Il vient de te dire : « {q} »\n"
           "Écris UNE seule phrase très courte (4 à 10 mots), dite tout de suite pour le faire patienter pendant que tu "
           "cherches. Elle reprend précisément le sujet de sa demande, par exemple : « Je regarde la météo de demain à Brive. », "
           "« Je cherche le film Dune dans ta bibliothèque. », « Je vérifie tes mails tout de suite. », « J'allume la clim du salon. »\n"
           "Interdit : donner la réponse, inventer un chiffre ou un fait, poser une question, émoji, guillemets. "
           "Réponds seulement par la phrase.")


def ack_contexte(text):
    """28/10/2026 : phrase d'attente adaptee a la question (Gemini rapide), None si rien d'utilisable."""
    rep, _ = gemini_post(["gemini-2.5-flash-lite", "gemini-flash-lite-latest"],  # le plus rapide d'abord (~2 s)
                         {"contents": [{"role": "user", "parts": [{"text": ACK_CTX.replace("{q}", text[:300])}]}],
                          "generationConfig": {"temperature": 0.4, "maxOutputTokens": 40}}, timeout=2.5)
    p = (gemini_texte(rep) or "").strip().splitlines()
    p = p[0].strip().strip('"«» ') if p else ""
    if not p or len(p) > 90 or "?" in p or len(p.split()) < 3:
        return None
    return p


def play_ack(text=""):
    global speech_gen
    t0 = time.time()
    data = None
    if text:
        try:
            phrase = ack_contexte(text)
            if not phrase or time.time() - t0 >= 4.5 or not run["busy"]:
                log("attente adaptée abandonnée (%.1f s, %r)" % (time.time() - t0, phrase))
            if run.get("moteur") == "gemini":  # reponse rapide : la phrase d'attente ne servirait a rien
                log("attente sautée (réponse rapide Gemini)")
                return
            if phrase and time.time() - t0 < 4.5 and run["busy"]:
                data, mime = tts(phrase)
                stats["acks_ctx"] = stats.get("acks_ctx", 0) + 1
                log("attente adaptée (%.1f s) : %s" % (time.time() - t0, phrase))
        except Exception as e:  # noqa: BLE001
            log("attente adaptée", repr(e)[:200])
            data = None
    if data is None:
        try:
            i = random.choice([k for k in range(len(ACKS)) if k != last_ack[0]])
            last_ack[0] = i
            data, mime = ack_audio(i)
        except Exception as e:  # noqa: BLE001
            log("attente", repr(e))
            return
    if not run["busy"]:
        return  # la vraie reponse est deja arrivee
    speech_gen += 1
    g = speech_gen
    broadcast({"type": "speak", "gen": g})
    broadcast({"type": "audio", "gen": g, "seq": 0, "url": store_audio(data, mime)})
    broadcast({"type": "audio", "gen": g, "end": True, "count": 1})
    stats["acks"] = stats.get("acks", 0) + 1


def store_audio(data, mime):
    aid = uuid.uuid4().hex[:12]
    with lock:
        audio_store[aid] = (data, mime)
        audio_order.append(aid)
        while len(audio_order) > 40:
            audio_store.pop(audio_order.pop(0), None)
    return "/audio/" + aid


def speak(text, gen):
    chunks = split_sentences(clean_for_speech(text)) if text else []
    n = 0
    for i, c in enumerate(chunks):
        if gen != speech_gen:
            return
        try:
            data, mime = tts(c)
        except Exception as e:  # noqa: BLE001
            stats["errors"] += 1
            stats["last_error"] = "voix: " + repr(e)
            log("voix", repr(e))
            break
        url = store_audio(data, mime)
        stats["clips"] += 1
        n += 1
        broadcast({"type": "audio", "gen": gen, "seq": i, "url": url})
    broadcast({"type": "audio", "gen": gen, "end": True, "count": n})
    if gen == speech_gen and not run["busy"]:
        set_state("idle")


def ha_call(method, path, body=None):
    req = urllib.request.Request(HA_URL + path, method=method,
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Authorization": "Bearer " + read(HA_TOKEN_FILE),
                                          "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as r:
        raw = r.read()
        return r.status, (json.loads(raw) if raw else None)


def phone_say(text):
    try:
        _, st = ha_call("GET", "/api/states/" + PHONE_SWITCH)
        if (st or {}).get("state") != "on":
            stats["phone_skipped"] += 1
            stats["phone_last"] = time.strftime("%H:%M:%S") + " coupe (interrupteur)"
            return
        spoken = clean_for_speech(text, PHONE_MAX)
        if not spoken:
            return
        code, _ = ha_call("POST", "/api/services/notify/" + PHONE_NOTIFY,
                          {"message": "TTS", "data": {"tts_text": spoken, "priority": "high", "ttl": 0}})
        stats["phone_sent"] += 1
        stats["phone_last"] = time.strftime("%H:%M:%S") + " envoye http %s (%d car.)" % (code, len(spoken))
        log("telephone", stats["phone_last"])
    except Exception as e:  # noqa: BLE001
        stats["errors"] += 1
        stats["last_error"] = "telephone: " + repr(e)
        stats["phone_last"] = time.strftime("%H:%M:%S") + " erreur " + repr(e)
        log("telephone", repr(e))


def reply_done(text, phone):
    global speech_gen
    speech_gen += 1
    g = speech_gen
    set_state("done")
    broadcast({"type": "speak", "gen": g})
    threading.Thread(target=speak, args=(text, g), daemon=True).start()
    if phone and text:
        threading.Thread(target=phone_say, args=(text,), daemon=True).start()


# ---------------------------------------------------------------- outils -> libelle
TOOL_LABELS = {"Bash": "Commande", "Read": "Lecture", "Edit": "Modification", "MultiEdit": "Modification",
               "Write": "Écriture", "Grep": "Recherche", "Glob": "Recherche", "WebFetch": "Web",
               "WebSearch": "Recherche web", "Task": "Agent", "Agent": "Agent", "TodoWrite": "Liste de tâches"}


def tool_label(name, detail=""):
    label = TOOL_LABELS.get(name) or ("Outil " + name.split("__")[-1] if name.startswith("mcp__") else name)
    return label + (" : " + detail if detail else "")


def tool_detail(ti):
    if not isinstance(ti, dict):
        return ""
    for k in ("description", "file_path", "pattern", "query", "url", "command", "prompt"):
        v = ti.get(k)
        if isinstance(v, str) and v.strip():
            v = v.strip().splitlines()[0]
            return (os.path.basename(v) if k == "file_path" else v)[:120]
    return ""


# ---------------------------------------------------------------- aiguilleur de modele
TRI_CONSIGNE = """Tu es l'aiguilleur de Friday, l'assistante de Loulou qui pilote son NAS Freeplex et sa maison (Home Assistant).
Choisis le modèle qui répondra au DERNIER message de Loulou. Réponds par UN SEUL mot : haiku, sonnet ou opus.
- haiku : demande simple ou rapide : allumer/éteindre/régler un appareil, état d'un capteur, heure, météo, rappel, petite question de culture générale, bavardage, merci.
- sonnet : dépannage ou vérification sur le NAS, les conteneurs, Plex/Radarr/Sonarr, tâche sur un site web, recherche, rédaction d'un message, question qui demande plusieurs vérifications. En cas de doute : sonnet.
- opus : gros chantier : écrire ou modifier un programme, panne compliquée ou qui résiste, analyse longue, plan d'un projet, décision importante.
Si le message est une suite courte (« oui », « vas-y », « fais-le ») d'un échange qui portait sur un gros chantier, garde le niveau de ce chantier.
Si le message ou l'échange précédent parle d'une adresse, d'un lieu, d'une entreprise à trouver ou du GPS : sonnet au minimum (la dictée est souvent déformée, il faut chercher)."""
RANG = {"haiku": 0, "sonnet": 1, "opus": 2}


def forcage(text):
    """Loulou impose le modele en le nommant (ou « réfléchis bien »)."""
    t = text.lower()
    if re.search(r"\bopus\b|r[ée]fl[ée]chis bien|r[ée]fl[ée]chis [àa] fond", t):
        return "opus"
    if re.search(r"\bsonnet\b", t):
        return "sonnet"
    if re.search(r"\bha[iï]ku\b", t):
        return "haiku"
    # 27/10/2026 : adresses et GPS = recherche + interpretation d'une dictee souvent deformee -> jamais haiku
    if re.search(r"\bgps\b|adresse|itin[ée]raire|emm[eè]ne[- ]moi|navigation|point gps|guidage", t):
        return "sonnet"
    # 28/10/2026 : missions logiciel-missions / boulot = toujours Claude (sonnet), jamais Gemini ni le tri
    if MISSION_RE.search(sans_accents(text)):
        return "sonnet"
    return ""


def choisir_modele(text, avec_fichiers=False):
    m = forcage(text)
    if m:
        return m, "demandé"
    with lock:
        avant = [h for h in history if h.get("role") in ("user", "assistant") and h.get("source") != "appli"][-3:-1]  # echange precedent
    prev = "\n".join(("Loulou: " if h["role"] == "user" else "Friday: ") + h["text"][:300] for h in avant)
    msg = TRI_CONSIGNE + ("\n\nÉchange précédent (extrait) :\n" + prev if prev else "") + \
        "\n\nDernier message de Loulou :\n" + text[:1500]
    cmd = [CLAUDE_BIN, "-p", msg, "--model", "haiku", "--output-format", "json", "--no-session-persistence",
           "--tools", "", "--strict-mcp-config", "--disable-slash-commands", "--setting-sources", ""]
    try:
        p = subprocess.run(cmd, cwd="/tmp", env=dict(os.environ, HOME="/home/USER"),
                           stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=TRI_TIMEOUT)
        rep = (json.loads(p.stdout).get("result") or "").strip().lower()
        m = next((k for k in ("opus", "sonnet", "haiku") if k in rep), "")
    except Exception as e:  # noqa: BLE001
        log("tri modele", repr(e)[:200])
        m = ""
    if not m:
        if 'rep' in locals() and rep:
            log("tri modele: reponse inattendue", rep[:150])
        return MODELE_DEFAUT, "tri en échec"
    if avec_fichiers and RANG[m] < RANG["sonnet"]:
        return "sonnet", "fichier joint"
    return m, "tri"


MOIS = {"jan": "janvier", "feb": "février", "mar": "mars", "apr": "avril", "may": "mai", "jun": "juin",
        "jul": "juillet", "aug": "août", "sep": "septembre", "oct": "octobre", "nov": "novembre", "dec": "décembre"}
QUOTA_RE = re.compile(r"hit your .{0,30}limit|usage limit reached", re.I)


def quota_fr(text):
    """27/10/2026 : message de quota Claude (en anglais) -> phrase francaise lisible par Friday."""
    quand = ""
    m = re.search(r"resets\s+(?:([A-Za-z]{3})[a-z]*\s+(\d{1,2}),?\s+)?(\d{1,2})(?::(\d\d))?\s*(am|pm)", text, re.I)
    if m:
        h = int(m.group(3)) % 12 + (12 if m.group(5).lower() == "pm" else 0)
        heure = "%d h" % h + (" " + m.group(4) if m.group(4) and m.group(4) != "00" else "")
        if m.group(1):
            quand = " Il revient le %s %s à %s." % (int(m.group(2)), MOIS.get(m.group(1).lower(), m.group(1)), heure)
        else:
            quand = " Il revient à %s." % heure
    quoi = "de la semaine" if "weekly" in text.lower() else "du moment"
    return "Je ne peux pas répondre : le quota Claude " + quoi + " est épuisé." + quand


# ---------------------------------------------------------------- melange Gemini / Claude (28/10/2026)
# Accord de Loulou : Gemini (ses 3 cles gratuites) repond au simple (maison, heure, rappels, bavardage)
# et fait le tri ; Claude garde le vrai travail. Ce qui est sensible (banque, mails, sante, Doctolib...)
# ou touche au boulot (missions logiciel-missions, GPS) ne passe JAMAIS par Google.
GEMINI_KEY_FILE = os.path.join(ROOT, ".gemini")
GEMINI_API = "https://generativelanguage.googleapis.com/v1beta/models/%s:generateContent"
GEMINI_MODELES = [m for m in E("FRIDAY_GEMINI_MODELES", "gemini-2.5-flash,gemini-flash-lite-latest").split(",") if m]
GEMINI_TRI_MODELES = [m for m in E("FRIDAY_GEMINI_TRI", "gemini-flash-lite-latest,gemini-2.5-flash-lite").split(",") if m]
GEMINI_TIMEOUT = int(E("FRIDAY_GEMINI_TIMEOUT", "20"))
GEMINI_ACTIF = E("FRIDAY_GEMINI", "1") == "1"
COMPTEUR_FILE = os.path.join(ROOT, "compteur-moteurs.json")
RAPPEL_SH = os.path.join(ROOT, "outils", "rappel.sh")
gemini_cle = [0]


def sans_accents(t):
    return unicodedata.normalize("NFKD", (t or "").lower()).encode("ascii", "ignore").decode()


# sujets sensibles -> Claude seulement (le texte ne part jamais chez Google)
SENSIBLE_RE = re.compile(
    r"\b(?:banque|bancaire|solde|virement|carte bleue|carte bancaire|credit agricole|paypal|floa|cdiscount"
    r"|paiements?|prelevements?|echeances?|argent|salaire|impots?|facture|e-?mails?|mails?|courriels?|gmail"
    r"|boite de reception|doctolib|medecin|docteur|sante|malad|ordonnance|pharmac|rendez-vous|rdv"
    r"|mot de passe|identifiant|code pin|code secret|agenda|calendrier|drive|fichiers?|documents?"
    r"|ecran|papiers|assurance|mutuelle|telephone de|contacts?)")
# boulot (missions logiciel-missions et ses deformations de dictee) -> Claude sonnet d'office
MISSION_RE = re.compile(
    r"\bmissions?\b|akan|a ?cane+n|roacan|cannelle|\b(?:de)?chargements?\b|\blivraisons?\b|\benlevements?\b"
    r"|\bpatron\b|\bemployeur\b|ton employeur|\btournee\b|\bplanning\b")


def compte(cle):
    """Compteur par moteur, en memoire (/health) et par jour sur disque (verification sur plusieurs jours)."""
    stats.setdefault("moteurs", {})
    stats["moteurs"][cle] = stats["moteurs"].get(cle, 0) + 1
    try:
        try:
            with open(COMPTEUR_FILE, encoding="utf-8") as f:
                c = json.load(f)
        except (OSError, ValueError):
            c = {}
        jour = c.setdefault(time.strftime("%Y-%m-%d"), {})
        jour[cle] = jour.get(cle, 0) + 1
        tmp = COMPTEUR_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(c, f, ensure_ascii=False, indent=1)
        os.replace(tmp, COMPTEUR_FILE)
    except OSError as e:
        log("compteur", repr(e))


def gemini_post(modeles, payload, timeout=None):
    """Essaie chaque modele, et pour chacun les cles a tour de role (quota depasse = cle suivante)."""
    cles = [k.strip() for k in read(GEMINI_KEY_FILE).splitlines() if k.strip()]
    if not cles:
        raise IOError("aucune cle Gemini")
    dernier = ""
    for modele in modeles:
        for _ in range(len(cles)):
            i = gemini_cle[0] % len(cles)
            corps_req = payload
            if "2.5-flash" in modele:  # 03/11/2026 : pas de reflexion -> plus rapide et plus direct
                corps_req = dict(payload, generationConfig=dict(payload.get("generationConfig") or {},
                                                                 thinkingConfig={"thinkingBudget": 0}))
            req = urllib.request.Request(GEMINI_API % modele, data=json.dumps(corps_req).encode(),
                                         headers={"Content-Type": "application/json", "x-goog-api-key": cles[i]})
            try:
                with urllib.request.urlopen(req, timeout=timeout or GEMINI_TIMEOUT) as r:
                    return json.loads(r.read()), modele
            except urllib.error.HTTPError as e:
                corps = ""
                try:
                    corps = e.read().decode()[:160]
                except Exception:  # noqa: BLE001
                    pass
                dernier = "%s http %s %s" % (modele, e.code, corps)
                if e.code in (429, 403, 401):
                    gemini_cle[0] += 1  # cle a bout ou refusee : la suivante
                    continue
                break  # surcharge ou erreur du modele : modele suivant
            except Exception as e:  # noqa: BLE001
                dernier = "%s %r" % (modele, e)
                break
    raise IOError(dernier[:300])


def gemini_texte(rep):
    parts = (((rep.get("candidates") or [{}])[0].get("content") or {}).get("parts")) or []
    return "".join(p.get("text", "") for p in parts if isinstance(p, dict) and not p.get("thought")).strip()


TRI_GEMINI = """Tu es l'aiguilleur de Friday, l'assistante de Loulou qui pilote son NAS Freeplex et sa maison (Home Assistant).
Choisis qui répondra au DERNIER message de Loulou. Réponds par UN SEUL mot : gemini, haiku, sonnet ou opus.
- gemini : allumer, éteindre ou régler un appareil de la maison (clims, lumières, prises, aspirateurs, télé, enceintes), état ou température d'un appareil ou d'un capteur de la maison, heure ou date, rappel ou minuteur, météo d'un lieu (prévisions), bonjour, merci, bavardage, petite question de culture générale qui ne demande aucune information récente.
- haiku : question simple qui demande internet ou une information récente (actualité, sport, horaires), piloter le PC (volume, ouvrir une appli).
- sonnet : dépannage ou vérification sur le NAS, les conteneurs, Plex/Radarr/Sonarr, films et séries, tâche sur un site web, recherche, rédaction d'un message, question qui demande plusieurs vérifications. En cas de doute : sonnet.
- opus : gros chantier : écrire ou modifier un programme, panne compliquée ou qui résiste, analyse longue, plan d'un projet, décision importante.
Si le message est une suite courte (« oui », « vas-y », « fais-le ») d'un échange, garde le niveau de cet échange."""


def tri_gemini(text):
    with lock:
        avant = [h for h in history if h.get("role") in ("user", "assistant") and h.get("source") != "appli"][-3:-1]
    prev = "\n".join(("Loulou: " if h["role"] == "user" else "Friday: ") + h["text"][:300] for h in avant)
    msg = TRI_GEMINI + ("\n\nÉchange précédent (extrait) :\n" + prev if prev else "") + \
        "\n\nDernier message de Loulou :\n" + text[:1500]
    rep, _ = gemini_post(GEMINI_TRI_MODELES, {"contents": [{"role": "user", "parts": [{"text": msg}]}],
                                               "generationConfig": {"temperature": 0}}, timeout=5)
    r = sans_accents(gemini_texte(rep))
    return next((k for k in ("gemini", "opus", "sonnet", "haiku") if k in r), "")


METEO_RE = re.compile(r"\bmeteo\b|\bpleuv|\bpluie\b|\bneige\b|\bneiger|\bquel temps\b|\btemps (qu.il )?(fait|fera|va faire)|\bprevision|\bgel\b|\bverglas")


# 03/11/2026 : commande maison evidente -> Gemini direct (economise l'appel de tri, 1 a 12 s mesures)
MAISON_ACTION_RE = re.compile(r"\b(allume|allumer|rallume|eteins|eteint|eteindre|coupe|couper|mets|met|baisse|baisser|monte|monter|augmente|diminue|regle|regler|lance l.aspirateur)\b")
MAISON_OBJET_RE = re.compile(r"\b(clims?|climes?|climatisations?|chauffage|teles?|tv|television|fire ?tv|lumieres?|lampes?|projecteurs?|prises?|multiprise|aspirateurs?|deebot|enceintes?)\b")
MAISON_ETAT_RE = re.compile(r"\b(temperature|degres?|fait.il|chaud|froid)\b.*\b(salon|cuisine|chambre|chambre1|chambre2|maison|pieces?)\b")


def aiguiller(text, avec_fichiers):
    """-> ("gemini", raison) ou (modele Claude, raison) ; None comme modele = tri Claude classique."""
    m = forcage(text)
    if m:
        return m, "demandé"
    if not GEMINI_ACTIF:
        return None, "gemini coupé"
    if avec_fichiers:
        return None, "fichier joint"
    if SENSIBLE_RE.search(sans_accents(text)):
        return None, "sujet sensible"
    if METEO_RE.search(sans_accents(text).lower()):  # 28/10/2026 : meteo -> Gemini + outil meteo, jamais une recherche web
        return "gemini", "météo"
    ts = sans_accents(text).lower()
    if (MAISON_ACTION_RE.search(ts) and MAISON_OBJET_RE.search(ts)) or MAISON_ETAT_RE.search(ts):
        return "gemini", "maison direct"
    with lock:
        msgs = [h for h in history if h.get("role") in ("user", "assistant")]
    der = msgs[-2] if len(msgs) >= 2 and msgs[-1].get("role") == "user" else None
    if der and der.get("role") == "assistant" and der.get("moteur") in ("claude", "raccourci") and len(text.split()) <= 6 \
            and time.time() - der.get("ts", 0) < PAUSE_NOUVELLE:
        return der.get("modele") or MODELE_DEFAUT, "suite d'un échange Claude"
    try:
        m = tri_gemini(text)
    except Exception as e:  # noqa: BLE001
        log("tri gemini", repr(e)[:200])
        compte("tri_gemini_echec")
        return None, "tri gemini en échec"
    compte("tri_gemini")
    if not m:
        return None, "tri gemini illisible"
    return m, "tri gemini"


# outils donnes a Gemini : la maison (Home Assistant) et les rappels, rien d'autre
HA_DOMAINES_ACTION = {"light", "switch", "climate", "fan", "cover", "vacuum", "media_player", "input_boolean",
                      "scene", "humidifier", "water_heater"}
HA_ATTRS = ("friendly_name", "unit_of_measurement", "hvac_mode", "hvac_modes", "temperature",
            "current_temperature", "fan_mode", "preset_mode", "brightness", "volume_level", "media_title",
            "source", "battery_level", "device_class")


# 03/11/2026 : appareils que Gemini ne touche jamais (cameras/Frigate, sirenes, electromenager, compteur, reseau)
HA_INTOUCHABLES_RE = re.compile(r"^switch\.(cam_|cour_|salon_(detect|motion|recordings|snapshots|review))|shellyplug|frigo|compteur|zigbee|freebox|sirene")


def ha_resume(e):
    a = e.get("attributes") or {}
    return {"entity_id": e.get("entity_id"), "etat": e.get("state"),
            **{k: a[k] for k in HA_ATTRS if k in a}}


def outil_gemini(nom, args):
    try:
        if nom == "maison_chercher":
            mots = [w for w in sans_accents(args.get("mots", "")).split() if len(w) > 1]
            _, tous = ha_call("GET", "/api/states")
            res = []
            for e in tous or []:
                if e["entity_id"].split(".")[0] in ("camera", "image", "update", "automation", "tts", "stt",
                                                    "conversation", "event", "ai_task", "notify"):
                    continue
                cible = sans_accents(e["entity_id"] + " " + str((e.get("attributes") or {}).get("friendly_name", "")))
                cible = cible.replace("_", " ").replace(".", " ")
                if all(w in cible for w in mots):
                    res.append(ha_resume(e))
            return {"nombre": len(res), "appareils": res[:30]}
        if nom == "maison_etat":
            _, e = ha_call("GET", "/api/states/" + args.get("entity_id", ""))
            return ha_resume(e or {})
        if nom == "maison_action":
            # 03/11/2026 : un ou plusieurs appareils du meme type en UN appel, une seule relecture a la fin
            brut = args.get("entity_id", "")
            ids = [x.strip() for x in (brut if isinstance(brut, list) else str(brut).split(",")) if x.strip()]
            dom = args.get("domaine") or (ids[0].split(".")[0] if ids else "")
            if not ids or dom not in HA_DOMAINES_ACTION or any(i.split(".")[0] != dom for i in ids):
                return {"erreur": "action interdite, ou appareils de types différents : un appel par type d'appareil"}
            interdits = [i for i in ids if HA_INTOUCHABLES_RE.search(i)]
            if interdits:
                return {"erreur": "appareil protégé (caméra, sirène, électroménager, compteur, réseau) : passe la main à Claude",
                        "appareils": interdits}
            data = dict(args.get("donnees") or {})
            data["entity_id"] = ids
            code, _ = ha_call("POST", "/api/services/%s/%s" % (dom, args.get("service", "")), data)
            time.sleep(2)
            etats = []
            for i in ids:
                _, e = ha_call("GET", "/api/states/" + i)
                etats.append(ha_resume(e or {}))
            return {"http": code, "etat_relu_2s_apres": etats}
        if nom == "meteo":  # 28/10/2026 : previsions Open-Meteo
            p = subprocess.run([os.path.join(ROOT, "outils", "meteo.py"), str(args.get("lieu", "") or "")],
                               capture_output=True, text=True, timeout=30)
            return {"code": p.returncode, "previsions": (p.stdout + p.stderr).strip()[-2000:]}
        if nom == "rappel":
            act = args.get("action", "creer")
            if act == "liste":
                cmd = [RAPPEL_SH, "liste"]
            elif act == "annuler":
                cmd = [RAPPEL_SH, "annule", str(args.get("numero", ""))]
            else:
                cmd = [RAPPEL_SH, str(args.get("delai", "")), str(args.get("texte", ""))]
            p = subprocess.run(cmd, capture_output=True, text=True, timeout=20, env=dict(os.environ, HOME="/home/USER"))
            return {"code": p.returncode, "sortie": (p.stdout + p.stderr).strip()[-600:]}
    except urllib.error.HTTPError as e:
        return {"erreur": "http %s" % e.code}
    except Exception as e:  # noqa: BLE001
        return {"erreur": repr(e)[:200]}
    return {"erreur": "outil inconnu"}


OUTILS_GEMINI = [{"functionDeclarations": [
    {"name": "maison_chercher", "description": "Cherche des appareils ou capteurs de la maison (Home Assistant) dont le nom contient TOUS ces mots (ex. 'clime salon', 'lumiere', 'aspirateur'). Renvoie entity_id, état et réglages.",
     "parameters": {"type": "object", "properties": {"mots": {"type": "string"}}, "required": ["mots"]}},
    {"name": "maison_etat", "description": "État actuel d'un appareil ou capteur par son entity_id exact.",
     "parameters": {"type": "object", "properties": {"entity_id": {"type": "string"}}, "required": ["entity_id"]}},
    {"name": "maison_action", "description": "Commande un ou plusieurs appareils du même type en UN seul appel (entity_id séparés par des virgules, ex. toutes les clims), puis relit leur état 2 s après. Ex. domaine climate, service set_temperature, donnees {temperature: 22} ; domaine light, service turn_on ; domaine climate, service set_hvac_mode, donnees {hvac_mode: 'cool'}.",
     "parameters": {"type": "object", "properties": {"domaine": {"type": "string"}, "service": {"type": "string"},
                                                     "entity_id": {"type": "string"}, "donnees": {"type": "object"}},
                    "required": ["domaine", "service", "entity_id"]}},
    {"name": "rappel", "description": "Rappel parlé sur le téléphone de Loulou. action creer (delai : 90s, 20m, 2h ou HH:MM ; texte), liste, ou annuler (numero).",
     "parameters": {"type": "object", "properties": {"action": {"type": "string", "enum": ["creer", "liste", "annuler"]},
                                                     "delai": {"type": "string"}, "texte": {"type": "string"},
                                                     "numero": {"type": "string"}}, "required": ["action"]}},
    {"name": "meteo", "description": "Prévisions météo (Open-Meteo) : maintenant + 5 jours. lieu vide = maison de Loulou (ta ville) ; sinon le nom de la ville.",
     "parameters": {"type": "object", "properties": {"lieu": {"type": "string"}}}},
    {"name": "passer_a_claude", "description": "Passe la demande à Claude (l'assistant complet du NAS) : à utiliser dès que la demande sort de la maison, de l'heure, des rappels et du bavardage, ou au moindre doute.",
     "parameters": {"type": "object", "properties": {"raison": {"type": "string"}}, "required": ["raison"]}},
]}]

JOURS = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
CATALOGUE = {"t": 0, "txt": ""}
CATALOGUE_BRUIT_RE = re.compile(r"mode_avance|nettoyage|aspiration_auto|preference|truedetect|permit_join")


def catalogue_maison():
    """03/11/2026 : appareils pilotables + etat actuel (cache 2 min) -> Gemini repond sans appel de recherche."""
    if CATALOGUE["txt"] and time.time() - CATALOGUE["t"] < 120:
        return CATALOGUE["txt"]
    try:
        _, tous = ha_call("GET", "/api/states")
    except Exception as e:  # noqa: BLE001
        log("catalogue maison", repr(e)[:150])
        return CATALOGUE["txt"]
    lignes = []
    for e in tous or []:
        eid = e.get("entity_id", "")
        d = eid.split(".")[0]
        a = e.get("attributes") or {}
        if d not in ("climate", "light", "switch", "vacuum", "media_player", "input_boolean") or HA_INTOUCHABLES_RE.search(eid):
            continue
        if (d == "media_player" and e.get("state") == "unavailable") or (d == "switch" and CATALOGUE_BRUIT_RE.search(eid)):
            continue
        etat = str(e.get("state"))
        if d == "climate":
            etat += ", consigne %s °C, mesuré %s °C" % (a.get("temperature"), a.get("current_temperature"))
        lignes.append("%s = %s : %s" % (a.get("friendly_name", eid), eid, etat))
    CATALOGUE.update(t=time.time(), txt="\n".join(lignes))
    return CATALOGUE["txt"]



def consigne_gemini():
    lt = time.localtime()
    quand = "%s %d %s %d, %d h %02d" % (JOURS[lt.tm_wday], lt.tm_mday, list(MOIS.values())[lt.tm_mon - 1],
                                         lt.tm_year, lt.tm_hour, lt.tm_min)
    return ("Tu es Friday, l'assistante de Loulou (comme FRIDAY dans Iron Man) ; parle de toi au féminin. "
            "Loulou te parle depuis l'interface vocale Friday, souvent dans son camion. Ta réponse est lue à voix haute.\n"
            "Nous sommes le " + quand + " (heure de Paris).\n"
            "- Réponds en français, comme dans une conversation : 1 à 3 phrases courtes, langage simple, sans jargon, "
            "sans liste, sans gras, sans émoji.\n"
            "- Tu t'occupes seulement de la maison (Home Assistant), de l'heure et de la date, des rappels, de la météo et du bavardage. "
            "Pour tout le reste (NAS, films, internet, PC, téléphone, GPS, missions, mails, banque...) ou au moindre doute, "
            "appelle passer_a_claude sans rien répondre d'autre.\n"
            "- Maison : la liste des appareils, avec leur état actuel, est à la fin de ces consignes : utilise directement "
            "ces entity_id (maison_chercher seulement pour un appareil absent de la liste) ; n'invente jamais un état. "
            "Plusieurs appareils du même type : UN SEUL appel maison_action, entity_id séparés par des virgules. "
            "« Allume la clim » sans mode ni température : service turn_on (elle reprend son dernier réglage) ; ne choisis "
            "jamais toi-même un mode ou une température que Loulou n'a pas dits. Après une action, "
            "dis ce que montre l'état relu. Clims Daikin (pièces salon, petit_salon, cuisine, chambre1, chambre2) : "
            "climate.clime_<pièce>_room_temperature ; elles passent par internet, le changement peut mettre quelques secondes : "
            "si l'état relu n'a pas encore changé, dis que la demande est envoyée. Le mode « cool » est le mode demandé, "
            "pas la preuve que le compresseur tourne. Un appareil « unavailable » : dis-le simplement.\n"
            "- Jamais d'action sur une alarme, une serrure, une caméra ou une sirène : passer_a_claude.\n"
            "- Rappel : redis l'heure exacte retenue par l'outil.\n"
            "- Météo : appelle l'outil meteo (lieu vide = sa maison ; s'il nomme une ville, passe-la). Réponds en 1 ou 2 phrases "
            "avec le ciel, les températures, la pluie et le vent s'il est fort, pour le jour demandé. Si la dictée a déformé le lieu, "
            "prends la ville qui sonne pareil.\n"
            "- Quand tu attends une réponse de Loulou, termine par une vraie question avec un point d'interrogation.\n"
            "Appareils de la maison (nom = entity_id : état) :\n" + catalogue_maison())


def run_gemini(text):
    """-> (texte, None) si Gemini a repondu ; (None, raison) s'il faut passer a Claude."""
    with lock:
        msgs = [h for h in history if h.get("role") in ("user", "assistant") and h.get("source") != "appli"][:-1][-8:]
    contents = []
    for h in msgs:
        if time.time() - h.get("ts", 0) > PAUSE_NOUVELLE:
            continue
        role = "user" if h["role"] == "user" else "model"
        if contents and contents[-1]["role"] == role:
            contents[-1]["parts"][0]["text"] += "\n" + h["text"][:1500]
        else:
            contents.append({"role": role, "parts": [{"text": h["text"][:1500]}]})
    while contents and contents[0]["role"] != "user":
        contents.pop(0)
    if contents and contents[-1]["role"] == "user":
        contents[-1]["parts"][0]["text"] += "\n" + text
    else:
        contents.append({"role": "user", "parts": [{"text": text}]})
    payload = {"systemInstruction": {"parts": [{"text": consigne_gemini()}]}, "contents": contents,
               "tools": OUTILS_GEMINI, "generationConfig": {"temperature": 0.1}}
    modeles = list(GEMINI_MODELES)
    for _ in range(8):
        rep, modele = gemini_post(modeles, payload)
        modeles = [modele]  # garder le meme modele pendant tout l'echange avec les outils
        cand = (rep.get("candidates") or [{}])[0]
        parts = (cand.get("content") or {}).get("parts") or []
        appels = [p["functionCall"] for p in parts if isinstance(p, dict) and p.get("functionCall")]
        if not appels:
            t = gemini_texte(rep)
            if not t:
                return None, "réponse vide (%s)" % cand.get("finishReason", "?")
            log("gemini (%s): %s" % (modele, t[:120]))
            return t, None
        payload["contents"].append({"role": "model", "parts": parts})
        reponses = []
        for a in appels:
            nom, args = a.get("name", ""), a.get("args") or {}
            if nom == "passer_a_claude":
                return None, "Gemini passe la main : " + str(args.get("raison", ""))[:150]
            set_state("working", "Maison : " + (args.get("entity_id") or args.get("mots") or nom)[:100])
            r = outil_gemini(nom, args)
            log("gemini outil", nom, json.dumps(args, ensure_ascii=False)[:150], "->", json.dumps(r, ensure_ascii=False)[:200])
            fr = {"name": nom, "response": {"resultat": r}}
            if a.get("id"):
                fr["id"] = a["id"]
            reponses.append({"functionResponse": fr})
        payload["contents"].append({"role": "user", "parts": reponses})
    return None, "trop d'étapes"


def contexte_gemini():
    """Echanges traites par Gemini depuis la derniere reponse de Claude, pour que Claude suive la conversation."""
    with lock:
        msgs = [h for h in history if h.get("role") in ("user", "assistant") and h.get("source") != "appli"][:-1]
    bloc = []
    for h in reversed(msgs):
        if h.get("role") == "assistant" and h.get("moteur") not in ("gemini", "raccourci"):
            break
        if time.time() - h.get("ts", 0) > PAUSE_NOUVELLE:
            break
        bloc.append(h)
    bloc = list(reversed(bloc))
    if not any(h.get("moteur") in ("gemini", "raccourci") for h in bloc):
        return ""
    lignes = [("Loulou : " if h["role"] == "user" else "Friday : ") + h["text"][:400] for h in bloc[-8:]]
    return ("(Pour info : ces derniers échanges avec Loulou ont été traités par Friday sans toi :\n"
            + "\n".join(lignes) + ")\n\nNouveau message de Loulou :\n")


# ---------------------------------------------------------------- raccourci missions (29/10/2026)
# « prépare ma mission » : le serveur lance lui-même l'outil tout-en-un (logiciel-missions + adresse + GPS en arrière-plan),
# puis Claude (sonnet, UN seul tour, sans outils) rédige la réponse. Avant : Claude enchaînait les commandes
# et attendait le GPS (jusqu'à 3 min le 29/10). FRIDAY_RACCOURCI_MISSION=0 pour revenir à l'ancien chemin.
PREP_MISSION = os.path.join(ROOT, "outils", "prepare-mission.py")
RACCOURCI_ACTIF = E("FRIDAY_RACCOURCI_MISSION", "1") == "1"
logiciel-missions_RE = re.compile(r"akan|a ?cane+n|roacan|cannelle|\bcanea|caneya|carnail|kanea")
MISSION_MOT_RE = re.compile(r"\bmissions?\b|\b(?:de)?chargements?\b|\blivraisons?\b|\benlevements?\b")
MISSION_ACTION_RE = re.compile(
    r"prepar|prochaine?|suivante?|premiere|\blis\b|\blire\b|regarde|\bouvre|donne|quelle|quel est|c.est quoi"
    r"|\bou\b|adresse|itineraire|\bgps\b|demain|aujourd|ce matin|ce soir|cet apres|\blance|\bmets")
# 29/10/2026 (Loulou) : « lance la prochaine mission » = fiche de l'étape suivante + GPS ;
# « regarde mes missions » = TOUTE la liste logiciel-missions dite dans l'ordre chronologique.
MISSION_LISTE_RE = re.compile(r"regarde|\bquelles?\b|\bliste\b|\btoutes\b|\bcombien\b|\bmes missions\b|programme"
                              r"|journee|planning|qu.est.ce que j.ai|j.ai quoi|a faire|\bmissions\b")
MISSION_PREP_RE = re.compile(r"\blance|prochaine?|suivante?|premiere|prepar|adresse|\bgps\b|itineraire|emmene|guidage")
# 29/10/2026 18h (Loulou : « j'ai dit regarde, pas lance ») : le GPS part SEULEMENT sur ordre explicite
MISSION_GPS_RE = re.compile(r"\blance|\bgps\b|itineraire|emmene|guidage|\bnavig")
# 03/11/2026 (Loulou : « elle me met toujours la première mission ») : une mission précise nommée -> CETTE mission
MISSION_CIBLE_RE = re.compile(r"\b(?:deuxieme|seconde?|2e|2eme|troisieme|3e|3eme|quatrieme|derniere|dernier|autre)\b"
                              r"|\bcelle\b|\bqui (?:va|vont|part|vient|livre)\b|\bchez\b|\bnumero\b|\d{5,}")
MISSION_EXCLU_RE = re.compile(r"\brappels?\b|colis|amazon|commande|cdiscount|\bmail|agenda|calendrier|declar|valid|\bfini|termine|annul|\bsupprim")
REDAC_MISSION = """Tu es Friday, l'assistante vocale de Loulou, chauffeur routier. Ta réponse sera lue à voix haute dans son camion.
Nous sommes le {now}. Il vient de te dire : « {q} » (la dictée déforme souvent les noms ; logiciel-missions est l'appli de ses missions).
L'outil vient de lire logiciel-missions sur son téléphone (l'étape qu'il a demandée, sinon la prochaine) et de s'occuper du GPS. Voici ce qu'il a trouvé :
---
{out}
---
Réponds en français, en 2 à 5 phrases courtes, comme à l'oral : pas de liste, pas de gras, pas de jargon.
- Dis l'étape : chargement ou déchargement, le jour et le créneau, l'entreprise et l'adresse complète, le nombre de palettes.
- Donne les numéros de téléphone écrits par paires séparées d'un espace (ex. 01 64 45 30 30).
- Signale clairement toute subtilité de la fiche : mémo, horaire pile ou rendez-vous, colis hors norme (« 1 COLIS 3M50X1M20 » = un colis de 3,50 m sur 1,20 m), contact à appeler avant d'arriver, adresse différente de la liste. Si le mémo est obscur, dis ce qu'il contient et ce que tu en comprends, sans inventer.
- Dis en quelques mots que la destination part dans son GPS (le démarrage du guidage n'est pas encore confirmé : ne dis pas qu'il a démarré ; s'il échoue, il sera prévenu à la voix), ou pourquoi rien n'est parti. Si le GPS n'a pas été demandé, n'en parle pas du tout.
- Ligne « CHOIX : etape choisie d'apres sa demande » : c'est bien l'étape qu'il veut, présente-la directement, sans dire que ce n'est pas la prochaine. Autre ligne « CHOIX : » (demande non trouvée, rang impossible, mot commun à toutes ses missions, fiche manquante) : commence par le dire en une phrase simple, puis donne l'étape lue.
Interdit : déduire du statut (MRECU, MLU…) qu'une étape est faite ; dire le poids (« Pds » n'a pas d'unité) ; inventer quoi que ce soit.
Si l'étape est déjà passée par rapport à maintenant, ou s'il y a un doute qu'elle soit faite, termine par une courte question (« Tu as déjà chargé chez X ? »).
Si l'outil écrit SHIZUKU_ARRETE : commence par « Shizuku est éteint sur ton téléphone » (dis bien « éteint », c'est le mot de Loulou), puis dis en une phrase courte que tu ne peux donc rien lire dans logiciel-missions, et qu'il doit relancer l'appli Shizuku dès qu'il est sur un Wi-Fi ; ne propose pas de relancer. Sinon, si la lecture a échoué, dis-le en une phrase, donne ce que tu as quand même, et termine par « Je relance ? ».
Réponds seulement par le texte à dire."""
REDAC_LISTE = """Tu es Friday, l'assistante vocale de Loulou, chauffeur routier. Ta réponse sera lue à voix haute dans son camion.
Nous sommes le {now}. Il vient de te dire : « {q} » : il veut savoir ce qu'il a à faire, c'est-à-dire TOUT ce qui est dans logiciel-missions.
Voici toutes les étapes de sa liste logiciel-missions, déjà rangées dans l'ordre chronologique (une mission = une étape de chargement + une étape de déchargement) :
---
{out}
---
Dis-lui TOUTES les étapes, dans cet ordre chronologique, comme à l'oral : pas de liste à puces, pas de gras, des phrases courtes.
Pour chaque jour, annonce le jour (« aujourd'hui », « demain », ou le nom du jour et la date), puis chaque étape : l'heure, chargement ou déchargement, l'entreprise et la ville, le nombre de palettes s'il est indiqué, et d'où vient ou où va la marchandise.
Les étapes dont l'heure est déjà passée restent dans la liste : dis-les quand même, sans dire qu'elles sont faites (le statut MRECU, MLU… veut seulement dire reçue et lue).
Ne dis pas le poids ni les numéros de référence. Si l'outil écrit SHIZUKU_ARRETE : commence par « Shizuku est éteint sur ton téléphone » (dis bien « éteint », c'est le mot de Loulou), puis dis en une phrase courte que tu ne peux donc rien lire dans logiciel-missions, et qu'il doit relancer l'appli Shizuku dès qu'il est sur un Wi-Fi ; ne propose pas de relancer. Sinon, si la lecture a échoué, dis-le en une phrase et termine par « Je relance ? ».
Réponds seulement par le texte à dire."""


def mission_brute(out):
    """Secours si Claude ne répond pas : les lignes utiles de l'outil, telles quelles."""
    lignes = []
    for l in out.splitlines():
        if l.startswith("--- lignes brutes"):
            break
        if l.strip() and not l.startswith("==="):
            lignes.append(l.strip())
    return ". ".join(lignes[:12]) or "Je n'ai rien pu lire dans logiciel-missions. Je relance ?"


# 03/11/2026 (demande de Loulou) : « Friday, appelle PRENOM_A » -> Friday compose le numero sur son telephone,
# c'est Loulou qui parle. Aucun modele d'IA : contacts lus dans le telephone par outils/appel.py, rien ne part ailleurs.
APPEL_OUTIL = os.path.join(ROOT, "outils", "appel.py")
# 03/11/2026 : Shizuku arrete (telephone redemarre) -> le dire, et ne jamais relire une ancienne capture (outils : sortie SHIZUKU_ARRETE)
SHIZUKU_MSG = "Shizuku est éteint sur ton téléphone, souvent parce qu'il a redémarré : je ne peux rien y faire. Relance l'appli Shizuku dès que tu es sur un Wi-Fi, puis redemande-moi."
APPEL_RE = re.compile(r"^(?:(?:hey|he|eh) )?(?:friday[ ,]+)?(?:tu peux |peux-tu |peux tu |est-ce que tu peux )?"
                      r"(?:appelle|appeler|appelles|telephone a|telephoner a|passe(?:-moi| moi)? un (?:coup de fil|appel) a|"
                      r"lance un appel (?:a|vers)|compose le numero de)\s+(.+)$")
APPEL_ATTENTE = {"choix": [], "t": 0}
ORDINAUX = {"premier": 0, "premiere": 0, "1": 0, "deuxieme": 1, "second": 1, "seconde": 1, "2": 1,
            "troisieme": 2, "3": 2, "quatrieme": 3, "4": 3}  # pas « un/deux » : trop courants (« mets un film »)


def appel_outil(nom, chercher):
    p = subprocess.run(["python3", APPEL_OUTIL, nom] + (["--chercher"] if chercher else []), capture_output=True,
                       text=True, timeout=40, env=dict(os.environ, HOME="/home/USER"))
    return (p.stdout.strip().splitlines() or ["ERREUR pas de réponse"])[-1]


def raccourci_appel(text, avec_fichiers):
    """True si la demande etait un appel telephonique (traite sans IA)."""
    if avec_fichiers:
        return False
    t = re.sub(r"[.!?]+$", "", sans_accents(text).lower().strip())
    nom = None
    m = APPEL_RE.match(t)
    if m:
        nom = re.sub(r"\b(s.il te plait|stp|pour moi|tout de suite|maintenant|sur son portable)\b", "", m.group(1))
        nom = re.sub(r"\s+", " ", nom.replace(",", " ")).strip()
        if not nom or nom.split()[0] in ("moi", "me", "nous"):
            return False
    elif APPEL_ATTENTE["choix"] and time.time() - APPEL_ATTENTE["t"] < 120 and len(t.split()) <= 6:
        i = next((v for k, v in ORDINAUX.items() if re.search(r"\b%s\b" % k, t)), None)
        if i is None or i >= len(APPEL_ATTENTE["choix"]):
            APPEL_ATTENTE.update(choix=[], t=0)  # autre sujet : on oublie le choix en attente
            return False
        nom = APPEL_ATTENTE["choix"][i]
    else:
        APPEL_ATTENTE.update(choix=[], t=0)
        return False
    APPEL_ATTENTE.update(choix=[], t=0)
    run["moteur"] = "gemini"  # pas de phrase d'attente : la reponse est immediate
    stats["modele"] = "raccourci"
    compte("raccourci_appel")
    set_state("working", "Je cherche %s dans tes contacts" % nom[:60])
    numero = None
    m2 = re.match(r"^(.*) \((0\d{9})\)$", nom)
    if m2:  # choix entre plusieurs numeros d'un meme contact
        nom, numero = m2.group(1), m2.group(2)
    try:
        r = appel_outil(nom, chercher=True) if not numero else "APPEL %s | %s" % (nom, numero)
    except Exception as e:  # noqa: BLE001
        r = "ERREUR " + repr(e)[:150]
    log("raccourci appel « %s » -> %s" % (nom, r[:120]))
    lancer = None
    if r.startswith("APPEL "):
        qui = r[6:].split(" | ")[0]
        rep_txt = "J'appelle %s." % qui
        lancer = (qui, (r.split(" | ") + [""])[1])
    elif r.startswith("PLUSIEURS "):
        choix = r[10:].split(" ; ")[:4]
        APPEL_ATTENTE.update(choix=choix, t=time.time())
        noms = ["le %s : %s" % (["premier", "deuxième", "troisième", "quatrième"][i], c) for i, c in enumerate(choix)]
        rep_txt = "J'ai plusieurs contacts : %s. Lequel j'appelle ?" % ", ".join(noms)
    elif r.startswith("AUCUN"):
        rep_txt = "Je ne trouve pas « %s » dans tes contacts. Tu peux me redire le nom ?" % nom
    elif r.startswith("SHIZUKU"):
        rep_txt = SHIZUKU_MSG
    else:
        rep_txt = "Je n'arrive pas à joindre ton téléphone pour lancer l'appel."
    run["busy"] = False
    add_msg("assistant", rep_txt, moteur="appel", modele="appel")
    reply_done(rep_txt, phone=False)
    if lancer:
        def composer(qui, num):
            time.sleep(3)  # laisser Friday dire « J'appelle ... » avant que le telephone passe sur l'appel
            try:
                out = subprocess.run(["python3", APPEL_OUTIL, "--numero", num], capture_output=True, text=True, timeout=40,
                                     env=dict(os.environ, HOME="/home/USER", FRIDAY_APPEL="raccourci")).stdout
                log("appel lancé vers %s (%s)" % (qui, num[:2] + "…" + num[-2:]), out.strip()[-80:])
            except Exception as e:  # noqa: BLE001
                log("appel", repr(e)[:150])
        threading.Thread(target=composer, args=lancer, daemon=True).start()
    return True


# 03/11/2026 (demande de Loulou) : SMS. « Envoie un message à PRENOM_A pour lui dire que... » -> Friday relit
# le message et attend « oui » avant d'envoyer (un SMS ne se reprend pas). Sans IA : outils/sms.py.
SMS_OUTIL = os.path.join(ROOT, "outils", "sms.py")
SMS_RE = re.compile(r"^(?:(?:hey|hé|he|eh)\s+)?(?:friday[\s,]+)?(?:tu peux |peux-tu |peux tu )?(?:envoie|envoies|envoyer|écris|ecris)"
                    r"(?:-lui)?\s+(?:un |une )?(?:sms|message|texto)\s+(?:à|a|au|pour)\s+(.+)$", re.I)
SMS_OUI_RE = re.compile(r"^(oui|ouais|vas[- ]y|envoie|envoie[- ]le|c.est bon|ok|okay|d.accord|parfait|exact|c.est ca)\b")
SMS_NON_RE = re.compile(r"^(non|annule|laisse tomber|stop|pas maintenant|n.envoie pas)\b")
SMS_ATTENTE = {"etape": "", "t": 0}


def sms_outil(*args, envoi=False):
    env = dict(os.environ, HOME="/home/USER")
    if envoi:
        env["FRIDAY_SMS"] = "raccourci"
    p = subprocess.run(["python3", SMS_OUTIL] + list(args), capture_output=True, text=True, timeout=90, env=env)
    return (p.stdout.strip().splitlines() or ["ERREUR pas de réponse"])[-1]


def sms_dire(txt):
    run["busy"] = False
    add_msg("assistant", txt, moteur="sms", modele="sms")
    reply_done(txt, phone=False)


def sms_resultat(r, texte=""):
    """Traite une sortie de sms.py --analyser et prepare l'etape suivante."""
    if r.startswith("SHIZUKU"):
        return SHIZUKU_MSG
    if r.startswith("CONTACT "):
        nom, num, texte = (r[8:].split(" | ", 2) + ["", ""])[:3]
        SMS_ATTENTE.update(etape="confirmer", nom=nom, num=num, texte=texte, t=time.time())
        return "Message pour %s : « %s ». Je l'envoie ?" % (nom, texte)
    if r.startswith("SANSTEXTE "):
        nom, num = (r[10:].split(" | ") + [""])[:2]
        SMS_ATTENTE.update(etape="texte", nom=nom, num=num, t=time.time())
        return "Qu'est-ce que je dis à %s ?" % nom
    if r.startswith("PLUSIEURS "):
        choix, _, texte = r[10:].partition("||")
        choix, texte = [c.strip() for c in choix.split(" ; ")][:4], texte.strip()
        SMS_ATTENTE.update(etape="choisir", choix=choix, texte=texte, t=time.time())
        return "J'ai plusieurs contacts : %s. Lequel ?" % ", ".join(
            "le %s : %s" % (["premier", "deuxième", "troisième", "quatrième"][i], c) for i, c in enumerate(choix))
    if r.startswith("AUCUN"):
        return "Je ne trouve pas ce nom dans tes contacts. Tu peux me redire le message avec le nom ?"
    return "Je n'arrive pas à joindre ton téléphone pour le message."


def raccourci_sms(text, avec_fichiers):
    """True si la demande concernait un SMS (traite sans IA)."""
    if avec_fichiers:
        return False
    brut = re.sub(r"[?!.]+$", "", text.strip())
    t = sans_accents(brut).lower()
    etape = SMS_ATTENTE.get("etape") if time.time() - SMS_ATTENTE.get("t", 0) < 120 else ""
    m = SMS_RE.match(brut)
    if m:
        SMS_ATTENTE.update(etape="", t=0)
        run["moteur"] = "gemini"  # reponse immediate, pas de phrase d'attente
        compte("raccourci_sms")
        set_state("working", "Je prépare ton message")
        try:
            r = sms_outil("--analyser", m.group(1))
        except Exception as e:  # noqa: BLE001
            r = "ERREUR " + repr(e)[:150]
        log("raccourci sms -> %s" % r[:60])
        sms_dire(sms_resultat(r))
        return True
    if not etape:
        return False
    if etape == "confirmer":
        if SMS_OUI_RE.search(t):
            nom, num, texte = SMS_ATTENTE["nom"], SMS_ATTENTE["num"], SMS_ATTENTE["texte"]
            SMS_ATTENTE.update(etape="", t=0)
            run["moteur"] = "gemini"
            set_state("working", "J'envoie le message à %s" % nom[:60])
            try:
                r = sms_outil("--envoyer", num, texte, envoi=True)
            except Exception as e:  # noqa: BLE001
                r = "ERREUR " + repr(e)[:150]
            log("sms envoyé à %s ? -> %s" % (nom, r[:100]))
            if r.startswith("ENVOYE "):
                sms_dire("C'est envoyé à %s." % nom)
            elif r.startswith("ENVOYE?"):
                sms_dire("J'ai appuyé sur envoyer pour %s, mais je n'ai pas pu vérifier qu'il est parti. Regarde dans Messages." % nom)
            elif r.startswith("SHIZUKU"):
                sms_dire(SHIZUKU_MSG)
            else:
                sms_dire("Je n'ai pas pu envoyer le message à %s. Il est peut-être resté prêt dans Messages, sans être envoyé." % nom)
            return True
        SMS_ATTENTE.update(etape="", t=0)
        if SMS_NON_RE.search(t):
            run["moteur"] = "gemini"
            sms_dire("D'accord, je n'envoie rien.")
            return True
        return False  # autre sujet : message abandonne, rien n'est envoye
    if etape == "texte":
        SMS_ATTENTE.update(etape="", t=0)
        if SMS_NON_RE.search(t):
            run["moteur"] = "gemini"
            sms_dire("D'accord, je n'envoie rien.")
            return True
        run["moteur"] = "gemini"
        texte = brut[0].upper() + brut[1:] if brut else brut
        sms_dire(sms_resultat("CONTACT %s | %s | %s" % (SMS_ATTENTE["nom"], SMS_ATTENTE["num"], texte)))
        return True
    if etape == "choisir":
        i = next((v for k, v in ORDINAUX.items() if re.search(r"\b%s\b" % k, t)), None)
        choix = SMS_ATTENTE.get("choix", [])
        SMS_ATTENTE.update(etape="", t=0)
        if i is None or i >= len(choix):
            return False
        run["moteur"] = "gemini"
        c, texte = choix[i], SMS_ATTENTE.get("texte", "")
        m2 = re.match(r"^(.*) \((0\d{9})\)$", c)
        if m2:
            r = ("CONTACT %s | %s | %s" % (m2.group(1), m2.group(2), texte[:1].upper() + texte[1:])) if texte else \
                "SANSTEXTE %s | %s" % (m2.group(1), m2.group(2))
        else:
            r = sms_outil("--analyser", c + " " + texte)
        sms_dire(sms_resultat(r))
        return True
    return False


def raccourci_mission(text, avec_fichiers):
    """True si la demande a été traitée par le raccourci missions."""
    if not RACCOURCI_ACTIF or avec_fichiers:
        return False
    t = sans_accents(text)
    if len(t.split()) > 25 or MISSION_EXCLU_RE.search(t):
        return False
    if not (MISSION_MOT_RE.search(t) or logiciel-missions_RE.search(t)):
        return False
    # 29/10/2026 (Loulou) : « mission » = logiciel-missions ; le mot suffit, pas besoin de dire logiciel-missions ni un verbe
    if not (re.search(r"\bmissions?\b", t) or MISSION_ACTION_RE.search(t) or logiciel-missions_RE.search(t)):
        return False
    t0 = time.time()
    if MISSION_GPS_RE.search(t):
        liste = False
    elif MISSION_CIBLE_RE.search(t):  # 03/11/2026 : « la deuxième », « celle qui va à X », « chez X », un n°
        liste = False
    elif re.search(r"regarde|\bquelles?\b|\bliste\b|\btoutes\b|\bcombien\b|programme|journee|planning", t):
        liste = True
    else:
        liste = bool(MISSION_LISTE_RE.search(t)) and not MISSION_PREP_RE.search(t)
    avec_gps = not liste and bool(MISSION_GPS_RE.search(t)) and not re.search(r"sans (le )?gps|pas (de|le) gps", t)
    log("raccourci mission (%s)" % ("toute la liste" if liste else ("prochaine étape + GPS" if avec_gps else "prochaine étape, sans GPS")))
    compte("raccourci_mission")
    run["moteur"] = "claude"
    stats["modele"] = "raccourci"
    set_state("working", "Je lis logiciel-missions sur ton téléphone")
    # 03/11/2026 : sa phrase part avec (--cible) : l'outil ouvre l'étape qu'il nomme, et met CETTE adresse dans le GPS
    args = ["python3", PREP_MISSION] + (["--tout"] if liste else
                                        ([] if avec_gps else ["--sans-gps"]) + ["--cible", text[:300]])
    out = ""
    try:
        p = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             text=True, start_new_session=True, env=dict(os.environ, HOME="/home/USER"))
        run["proc"] = p
        try:
            out, _ = p.communicate(timeout=300)
        except subprocess.TimeoutExpired:
            os.killpg(p.pid, signal.SIGTERM)
            out = "ERREUR lecture d'logiciel-missions trop longue (5 minutes), arrêtée"
    except Exception as e:  # noqa: BLE001
        out = "ERREUR outil mission : " + repr(e)[:200]
    finally:
        run["proc"] = None
    t_outil = time.time() - t0
    if run.get("stopped"):
        run["stopped"] = False
        run["busy"] = False
        add_msg("system", "Arrêté.")
        set_state("idle")
        return True
    set_state("thinking", "Je prépare ta réponse")
    now = "%s %s, %s" % (JOURS[time.localtime().tm_wday], time.strftime("%d/%m/%Y"), time.strftime("%H:%M"))
    msg = (REDAC_LISTE if liste else REDAC_MISSION).replace("{now}", now).replace("{q}", text[:300]).replace("{out}", out[:6000])
    cmd = [CLAUDE_BIN, "-p", msg, "--model", "sonnet", "--fallback-model", "opus", "--output-format", "json",
           "--no-session-persistence", "--tools", "", "--strict-mcp-config", "--disable-slash-commands",
           "--setting-sources", ""]
    reponse = ""
    try:
        p = subprocess.Popen(cmd, cwd="/tmp", env=dict(os.environ, HOME="/home/USER"), stdin=subprocess.DEVNULL,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True)
        run["proc"] = p
        try:
            so, _ = p.communicate(timeout=90)
            reponse = (json.loads(so).get("result") or "").strip()
        except subprocess.TimeoutExpired:
            os.killpg(p.pid, signal.SIGTERM)
    except Exception as e:  # noqa: BLE001
        log("raccourci rédaction", repr(e)[:200])
    finally:
        run["proc"] = None
    if run.get("stopped"):
        run["stopped"] = False
        run["busy"] = False
        add_msg("system", "Arrêté.")
        set_state("idle")
        return True
    if not reponse or (QUOTA_RE.search(reponse) and len(reponse) < 300):
        log("raccourci rédaction en échec, réponse brute :", reponse[:150])
        reponse = mission_brute(out)
    run["busy"] = False
    add_msg("assistant", reponse, moteur="raccourci", modele="sonnet")
    if avec_gps and run.get("android"):
        # 29/10/2026 : Eurowag est devant Friday -> la voix passe par Termux (play-audio), comme les messages de Claude
        set_state("done")
        def voix_tel(txt=reponse):
            try:
                r = subprocess.run(["python3", os.path.join(ROOT, "dire.py"), "--tel", "--long", txt],
                                   capture_output=True, text=True, timeout=120, env=dict(os.environ, HOME="/home/USER"))
                log("raccourci voix téléphone :", (r.stdout.strip().splitlines() or ["?"])[-1][:120])
            except Exception as e:  # noqa: BLE001
                log("raccourci voix téléphone", repr(e)[:150])
            if not run["busy"]:
                set_state("idle")
        threading.Thread(target=voix_tel, daemon=True).start()
    else:
        reply_done(reponse, phone=False)
    log("raccourci mission: outil %.0f s, réponse au total en %.0f s" % (t_outil, time.time() - t0))
    return True


def friday_repondre(text, avec_fichiers=False):
    """Point d'entree de chaque message : aiguillage puis Gemini ou Claude."""
    stats["asks"] += 1
    try:
        fait = raccourci_sms(text, avec_fichiers) or raccourci_appel(text, avec_fichiers) or raccourci_mission(text, avec_fichiers)
    except Exception as e:  # noqa: BLE001  (jamais bloquer Friday : on retombe sur le chemin normal)
        log("raccourci mission", repr(e)[:200])
        run["proc"] = None
        fait = False
    if fait:
        return
    t0 = time.time()
    try:
        choix, pourquoi = aiguiller(text, avec_fichiers)
    except Exception as e:  # noqa: BLE001  (jamais bloquer Friday : on retombe sur Claude)
        log("aiguillage", repr(e)[:200])
        choix, pourquoi = None, "aiguillage en échec"
    log("aiguillage: %s (%s, %.1f s)" % (choix or "tri claude", pourquoi, time.time() - t0))
    run["moteur"] = "gemini" if choix == "gemini" else "claude"  # 28/10 : pas de phrase d'attente si Gemini repond vite
    if choix == "gemini":
        try:
            rep, raison = run_gemini(text)
        except Exception as e:  # noqa: BLE001
            rep, raison = None, "erreur " + repr(e)[:200]
        if rep and not run.get("stopped"):
            compte("gemini")
            stats["modele"] = "gemini"
            run["busy"] = False
            add_msg("assistant", rep, moteur="gemini", modele="gemini")
            reply_done(rep, phone=False)
            log("gemini: réponse en %.1f s" % (time.time() - t0))
            return
        if run.get("stopped"):
            run["stopped"] = False
            run["busy"] = False
            add_msg("system", "Arrêté.")
            set_state("idle")
            return
        compte("gemini_vers_claude")
        log("gemini -> claude :", raison)
        if pourquoi == "météo":  # 28/10/2026 : Gemini en panne -> Claude recoit deja les vraies previsions
            try:
                p = subprocess.run([os.path.join(ROOT, "outils", "meteo.py")], capture_output=True, text=True, timeout=30)
                text += ("\n\n(Note de Friday : question de MÉTÉO. Prévisions de outils/meteo.py pour sa maison ci-dessous ; "
                         "s'il parle d'une autre ville, lance /home/USER/jarvis/outils/meteo.py <ville>. "
                         "Pas de recherche web, pas de liste de sources.)\n" + p.stdout)
            except Exception as e:  # noqa: BLE001
                log("meteo pour claude", repr(e)[:200])
        choix, pourquoi = None, "après Gemini"
    run_claude(text, avec_fichiers, choix=(choix, pourquoi) if choix else None)


# ---------------------------------------------------------------- claude
def run_claude(text, avec_fichiers=False, choix=None):
    global session_id
    t0 = time.time()
    modele, pourquoi = choix or choisir_modele(text, avec_fichiers)
    if modele not in SECOURS:
        modele = MODELE_DEFAUT
    compte("claude_" + modele)
    stats["modele"] = modele
    stats["modeles"] = stats.get("modeles") or {}
    stats["modeles"][modele] = stats["modeles"].get(modele, 0) + 1
    log("modele: %s (%s, %.1f s)" % (modele, pourquoi, time.time() - t0))
    text = contexte_gemini() + text
    cmd = [CLAUDE_BIN, "-p", text, "--output-format", "stream-json", "--verbose",
           "--permission-mode", "bypassPermissions",
           "--model", modele, "--fallback-model", SECOURS[modele]]
    # 27/10/2026 (accord de Loulou) : Friday ne garde que les connecteurs utiles au quotidien
    # (Memoire, Gmail, Agenda, Drive). Les autres pesaient ~13 000 morceaux de texte a CHAQUE etape.
    cmd += ["--disallowedTools"] + CONNECTEURS_RETIRES
    consignes = read(PROMPT_FILE)
    if consignes:
        cmd += ["--append-system-prompt", consignes]
    # 27/10/2026 : apres une pause de plus de PAUSE_NOUVELLE secondes, on repart d'une conversation neuve.
    # Une conversation qui s'allonge relit tout son passe a chaque etape et use vite le quota hebdo de Loulou.
    with lock:
        msgs = [h for h in history if h.get("role") in ("user", "assistant")]
    if session_id and len(msgs) >= 2 and time.time() - msgs[-2].get("ts", 0) > PAUSE_NOUVELLE:
        log("conversation neuve (pause de %d min)" % ((time.time() - msgs[-2].get("ts", 0)) // 60))
        session_id = ""
    if session_id:
        cmd += ["--resume", session_id]
    env = dict(os.environ, JARVIS_SOURCE="page", HOME="/home/USER")
    final, err = "", ""
    try:
        p = subprocess.Popen(cmd, cwd=CLAUDE_CWD, env=env, stdin=subprocess.DEVNULL,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                             bufsize=1, start_new_session=True)
        run["proc"] = p
        killer = threading.Timer(RUN_TIMEOUT, lambda: stop_run("délai dépassé"))
        killer.start()
        for line in p.stdout:
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            t = ev.get("type")
            if t == "system" and ev.get("session_id"):
                session_id = ev["session_id"]
            elif t == "assistant":
                for b in (ev.get("message") or {}).get("content") or []:
                    if not isinstance(b, dict):
                        continue
                    if b.get("type") == "tool_use":
                        set_state("working", tool_label(b.get("name", ""), tool_detail(b.get("input"))))
                    elif b.get("type") == "text" and b.get("text", "").strip():
                        set_state("thinking", b["text"].strip().splitlines()[0][:160])
            elif t == "result":
                final = ev.get("result") or ""
                if ev.get("session_id"):
                    session_id = ev["session_id"]
                if ev.get("is_error") and not final:
                    err = ev.get("subtype") or "erreur"
        p.wait(timeout=30)
        killer.cancel()
        if p.returncode not in (0, None) and not final:
            err = err or (p.stderr.read() or "").strip()[-400:] or "code %s" % p.returncode
    except Exception as e:  # noqa: BLE001
        err = repr(e)
    finally:
        run["proc"] = None
        run["busy"] = False
    if run.get("stopped"):
        run["stopped"] = False
        add_msg("system", "Arrêté.")
        set_state("idle")
        return
    if final and QUOTA_RE.search(final) and len(final) < 300:
        log("quota claude:", final[:150])
        stats["last_error"] = "quota: " + final[:150]
        final = quota_fr(final)
    if final:
        add_msg("assistant", final, moteur="claude", modele=modele)
        reply_done(final, phone=False)
    else:
        stats["errors"] += 1
        stats["last_error"] = "claude: " + err
        log("claude", err)
        add_msg("system", "Friday n'a pas répondu (" + err[:200] + ").")
        set_state("idle")
    save_state()


def stop_run(why="arrêt"):
    p = run.get("proc")
    if p and p.poll() is None:
        run["stopped"] = True
        try:
            os.killpg(p.pid, signal.SIGTERM)
        except OSError:
            pass
        log("arret claude:", why)


# ---------------------------------------------------------------- hooks (appli Claude)
def handle_hook(d):
    stats["hooks"] += 1
    if d.get("source") == "page":
        return  # deja suivi directement par run_claude
    ev = d.get("event")
    if ev == "prompt":
        add_msg("user", d.get("text") or "", source="appli")
        set_state("thinking")
    elif ev == "tool":
        set_state("working", tool_label(d.get("tool") or "", (d.get("detail") or "").strip()))
    elif ev == "stop":
        text = (d.get("text") or "").strip()
        if text:
            add_msg("assistant", text, source="appli")
        reply_done(text, phone=True)
    elif ev == "notify":
        set_state("attention", d.get("text") or "")


# ---------------------------------------------------------------- http
class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *a):
        pass

    def _send(self, code, body=b"", ctype="text/plain; charset=utf-8"):
        if isinstance(body, str):
            body = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if body:
            self.wfile.write(body)

    def _json(self, code, obj):
        self._send(code, json.dumps(obj, ensure_ascii=False), "application/json")

    def _query(self):
        q = {}
        if "?" in self.path:
            for part in self.path.split("?", 1)[1].split("&"):
                k, _, v = part.partition("=")
                q[k] = urllib.request.unquote(v)
        return q

    def _authed(self):
        ip = self.client_address[0]
        if bad_keys.get(ip, (0, 0))[0] >= 10 and time.time() - bad_keys[ip][1] < 600:
            return False
        k = self.headers.get("X-Jarvis-Key") or self._query().get("k", "")
        if KEY and secrets.compare_digest(k, KEY):
            return True
        n, _ = bad_keys.get(ip, (0, 0))
        bad_keys[ip] = (n + 1, time.time())
        return False

    def _body(self, limit=8_000_000):
        n = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(min(n, limit)) if n else b""

    def _save_upload(self):
        """Corps brut -> fichier dans recus/, lu par morceaux (pas tout en memoire)."""
        n = int(self.headers.get("Content-Length") or 0)
        if n <= 0:
            return None, (400, "fichier vide")
        if n > UPLOAD_MAX:
            self.close_connection = True
            return None, (413, "fichier trop gros (max %d Mo)" % (UPLOAD_MAX // 1048576))
        name = urllib.request.unquote(self.headers.get("X-File-Name") or "fichier")
        name = os.path.basename(name.replace("\\", "/")).strip() or "fichier"
        name = re.sub(r"[^\w.\-]+", "_", name).strip("._")[-90:] or "fichier"
        os.makedirs(UPLOAD_DIR, exist_ok=True)
        fp = os.path.join(UPLOAD_DIR, time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:4] + "-" + name)
        left = n
        try:
            with open(fp + ".part", "wb") as f:
                while left > 0:
                    chunk = self.rfile.read(min(left, 1 << 20))
                    if not chunk:
                        break
                    f.write(chunk)
                    left -= len(chunk)
            if left:
                os.remove(fp + ".part")
                self.close_connection = True
                return None, (400, "envoi coupé")
            os.replace(fp + ".part", fp)
        except OSError as e:
            return None, (500, "écriture impossible " + repr(e)[:100])
        stats["uploads"] = stats.get("uploads", 0) + 1
        log("fichier recu", fp, n)
        return {"path": fp, "name": name, "size": n}, None

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path in ("/", "/index.html"):
            with open(os.path.join(ROOT, "index.html"), "rb") as f:
                return self._send(200, f.read(), "text/html; charset=utf-8")
        if path == "/manifest.webmanifest":
            man = {"name": "Friday", "short_name": "Friday", "start_url": "/", "scope": "/",
                   "display": "standalone", "background_color": "#02060c", "theme_color": "#02060c",
                   "icons": [{"src": "/img/icon-192.png", "sizes": "192x192", "type": "image/png", "purpose": "any"},
                             {"src": "/img/icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any"}]}
            return self._send(200, json.dumps(man), "application/manifest+json")
        if path.startswith("/img/"):
            name = os.path.basename(path)
            types = {".png": "image/png", ".ico": "image/x-icon"}
            ext = os.path.splitext(name)[1]
            fp = os.path.join(ROOT, "img", name)
            if ext in types and os.path.isfile(fp):
                with open(fp, "rb") as f:
                    return self._send(200, f.read(), types[ext])
            return self._send(404, "introuvable")
        if path.startswith("/oww/"):  # 28/10/2026 : modeles "Hey Friday" pour l'ecoute dans la page
            name = os.path.basename(path)
            fp = os.path.join(ROOT, "oww", name)
            if name in ("melspectrogram.onnx", "embedding_model.onnx", "hey_friday.onnx") and os.path.isfile(fp):
                with open(fp, "rb") as f:
                    return self._send(200, f.read(), "application/octet-stream")
            return self._send(404, "introuvable")
        if path == "/health":
            with lock:
                body = {"ok": True, "clients": len(clients), "state": current.get("state"),
                        "busy": run["busy"], "session": bool(session_id), **stats}
            return self._json(200, body)
        if not self._authed():
            return self._send(401, "clé invalide")
        if path == "/api/history":
            with lock:
                return self._json(200, {"history": history[-80:], "state": current, "busy": run["busy"]})
        if path.startswith("/audio/"):
            item = audio_store.get(path[7:].split(".")[0])
            return self._send(200, item[0], item[1]) if item else self._send(404, "absent")
        if path == "/api/voices":
            quota = None
            if read(EL_KEY_FILE):
                try:
                    sub = el_call("GET", "/v1/user/subscription", timeout=8)
                    quota = {"used": sub.get("character_count"), "limit": sub.get("character_limit"),
                             "tier": sub.get("tier"), "reset": sub.get("next_character_count_reset_unix")}
                except Exception as e:  # noqa: BLE001
                    quota = {"error": repr(e)[:120]}
            ch = [{k: v.get(k) for k in ("name", "desc", "voice_id")} for v in choices()]
            return self._json(200, {"current": voice_cfg(), "choices": ch, "quota": quota})
        if path.startswith("/api/preview/"):
            vid = re.sub(r"[^A-Za-z0-9-]", "", path[13:])
            fp = os.path.join(PREVIEW_DIR, vid + ".mp3")
            if not os.path.isfile(fp):
                v = next((c for c in choices() if c["voice_id"] == vid), None)
                if not v or not v.get("preview"):
                    return self._send(404, "absent")
                try:
                    data = el_call("GET", "", raw=True, url=v["preview"])
                except Exception as e:  # noqa: BLE001
                    return self._send(502, "extrait indisponible " + repr(e)[:80])
                os.makedirs(PREVIEW_DIR, exist_ok=True)
                with open(fp, "wb") as f:
                    f.write(data)
            with open(fp, "rb") as f:
                return self._send(200, f.read(), "audio/mpeg")
        if path == "/events":
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Accel-Buffering", "no")
            self.end_headers()
            q = queue.Queue(maxsize=500)
            with lock:
                clients.add(q)
                snap = dict(current, busy=run["busy"])
            try:
                self.wfile.write(b"retry: 3000\n\n")
                self.wfile.write(("data: " + json.dumps(snap, ensure_ascii=False) + "\n\n").encode())
                self.wfile.flush()
                while True:
                    try:
                        data = q.get(timeout=15)
                    except queue.Empty:
                        data = b": ping\n\n"
                    self.wfile.write(data)
                    self.wfile.flush()
            except OSError:
                pass
            finally:
                with lock:
                    clients.discard(q)
            return
        self._send(404, "introuvable")

    def do_POST(self):
        global session_id, speech_gen
        path = self.path.split("?", 1)[0]
        if path == "/hook":
            if HOOK_TOKEN and self.headers.get("X-Halo-Token", "") != HOOK_TOKEN:
                return self._send(403, "jeton invalide")
            try:
                d = json.loads(self._body(200_000) or b"{}")
            except ValueError:
                return self._send(400, "json invalide")
            self._send(204)
            try:
                handle_hook(d)
            except Exception as e:  # noqa: BLE001
                stats["errors"] += 1
                stats["last_error"] = "hook: " + repr(e)
            return
        if not self._authed():
            return self._send(401, "clé invalide")
        if path == "/api/upload":
            info, err = self._save_upload()
            if err:
                return self._json(err[0], {"error": err[1]})
            return self._json(200, info)
        if path == "/api/ask":
            try:
                body = json.loads(self._body(100_000) or b"{}")
            except ValueError:
                return self._send(400, "json invalide")
            text = (body.get("text") or "").strip()
            files = []
            for p in body.get("files") or []:
                rp = os.path.realpath(str(p))
                if os.path.dirname(rp) == os.path.realpath(UPLOAD_DIR) and os.path.isfile(rp):
                    files.append(rp)
            if not text and not files:
                return self._json(400, {"error": "message vide"})
            shown = text
            if files:
                text = (text or "Regarde ce que je t'envoie et dis-moi ce que c'est.") + \
                    "\n\n(Fichiers joints par Loulou depuis Friday, enregistrés sur le NAS. Ouvre-les avec " \
                    "l'outil Read ; pour un format qu'il ne lit pas, convertis-le avec une commande.)\n" + \
                    "\n".join("- " + p for p in files)
                shown = (shown + "\n" if shown else "") + "\n".join(
                    "📎 " + os.path.basename(p).split("-", 3)[-1] for p in files)
            with lock:
                if run["busy"]:
                    return self._json(409, {"error": "Freeplex NAS travaille déjà, attends ou arrête-le."})
                run["busy"] = True
                run["started"] = time.time()
            speech_gen += 1
            add_msg("user", shown)
            set_state("thinking", "")
            run["moteur"] = None  # rempli par l'aiguillage ; lu par la phrase d'attente
            run["android"] = "Android" in (self.headers.get("User-Agent") or "")  # 29/10 : voix par-dessus le GPS
            threading.Thread(target=play_ack, args=(shown or text,), daemon=True).start()
            threading.Thread(target=friday_repondre, args=(text, bool(files)), daemon=True).start()
            return self._json(202, {"ok": True})
        if path == "/api/voice":
            try:
                vid = (json.loads(self._body(10_000) or b"{}").get("voice_id") or "").strip()
            except ValueError:
                return self._send(400, "json invalide")
            if vid == "piper":
                save_voice({"engine": "piper", "name": "Piper (NAS)"})
                cfg = voice_cfg()
            else:
                v = next((c for c in choices() if c["voice_id"] == vid), None)
                if not v:
                    return self._json(404, {"error": "voix inconnue"})
                use_id = vid
                if v.get("design"):
                    # voix creee sur mesure : on l'enregistre une fois dans le compte ElevenLabs
                    try:
                        r = el_call("POST", "/v1/text-to-voice", {"voice_name": "Freeplex " + v["name"],
                                    "voice_description": v.get("design_desc") or "Voix Freeplex",
                                    "generated_voice_id": v["design"]}, timeout=60)
                        use_id = r["voice_id"]
                    except Exception as e:  # noqa: BLE001
                        body = ""
                        try:
                            body = e.read().decode()[:300]
                        except Exception:  # noqa: BLE001
                            pass
                        return self._json(502, {"error": "Création de la voix refusée : " + (body or repr(e))[:300]})
                    allc = choices()
                    for c in allc:
                        if c["voice_id"] == vid:
                            old = os.path.join(PREVIEW_DIR, vid + ".mp3")
                            if os.path.isfile(old):
                                os.replace(old, os.path.join(PREVIEW_DIR, use_id + ".mp3"))
                            c["voice_id"] = use_id
                            c.pop("design", None)
                    with open(CHOICES_FILE, "w", encoding="utf-8") as f:
                        json.dump(allc, f, ensure_ascii=False, indent=1)
                try:
                    if v.get("owner") and not v.get("premade"):
                        r = el_call("POST", "/v1/voices/add/%s/%s" % (v["owner"], vid), {"new_name": "Freeplex " + v["name"]})
                        use_id = (r or {}).get("voice_id") or vid
                except Exception as e:  # noqa: BLE001
                    body = ""
                    try:
                        body = e.read().decode()[:300]
                    except Exception:  # noqa: BLE001
                        pass
                    if "already" not in body.lower():
                        log("ajout voix", repr(e), body)
                try:
                    el_tts("Test.", use_id)
                except Exception as e:  # noqa: BLE001
                    body = ""
                    try:
                        body = e.read().decode()[:300]
                    except Exception:  # noqa: BLE001
                        pass
                    return self._json(502, {"error": "ElevenLabs refuse cette voix : " + (body or repr(e))[:300]})
                save_voice({"engine": "elevenlabs", "voice_id": use_id, "name": v["name"], "model": EL_MODEL})
                cfg = voice_cfg()
            reply_done("Bonjour Loulou, c'est moi, ta nouvelle voix. Je suis là.", phone=False)
            threading.Thread(target=warm_acks, daemon=True).start()
            return self._json(200, {"ok": True, "current": cfg})
        if path == "/api/stop":
            stop_run("bouton")
            return self._json(200, {"ok": True})
        if path == "/api/new":
            if run["busy"]:
                return self._json(409, {"error": "occupé"})
            session_id = ""
            with lock:
                history.clear()
            save_state()
            broadcast({"type": "reset"})
            set_state("idle")
            return self._json(200, {"ok": True})
        if path == "/api/hflog":  # 28/10/2026 : compte rendu de l'ecoute "Hey Friday" de la page (diagnostic)
            try:
                msg = str(json.loads(self._body(4000) or b"{}").get("msg", ""))[:400]
            except ValueError:
                msg = "?"
            log("hey-friday page", self.client_address[0], (self.headers.get("User-Agent") or "")[:60], "|", msg)
            return self._json(200, {"ok": True})
        if path == "/api/wake":  # 28/10/2026 : ecoute "Hey Friday" du PC -> halo en ecoute / fin
            try:
                st = (json.loads(self._body(2000) or b"{}").get("state") or "listening")
            except ValueError:
                return self._send(400, "json invalide")
            if st == "suivi":  # 28/10/2026 : la page du PC a fini de dire une question -> l'ecoute du PC prend la reponse
                broadcast({"type": "suivi"})
                return self._json(200, {"ok": True})
            if st not in ("listening", "thinking", "done"):
                return self._json(400, {"error": "etat inconnu"})
            if st == "listening" and run["busy"]:
                return self._json(409, {"error": "occupé"})
            set_state(st, {"listening": "Je t'écoute", "thinking": "Je transcris…", "done": "Rien compris"}[st])
            return self._json(200, {"ok": True, "busy": run["busy"]})
        if path == "/api/listen":
            pcm = self._body()
            if len(pcm) < 8000:
                return self._json(400, {"error": "trop court"})
            try:
                t_tr = time.time()
                txt = transcribe(pcm)
                log("transcription %s %.1f s (%.1f s de son) : %s" % (transcripteur["dernier"], time.time() - t_tr, len(pcm) / 32000, txt[:80]))
                try:  # 28/10/2026 : on garde les 30 derniers enregistrements pour diagnostiquer les erreurs de transcription
                    dd = os.path.join(ROOT, "ecoute-debug"); os.makedirs(dd, exist_ok=True)
                    base = os.path.join(dd, time.strftime("%Y%m%d-%H%M%S"))
                    with wave.open(base + ".wav", "wb") as w:
                        w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000); w.writeframes(pcm)
                    with open(base + ".txt", "w", encoding="utf-8") as f:
                        f.write((self.headers.get("User-Agent") or "")[:80] + "\n" + txt + "\n")
                    for old in sorted(os.listdir(dd))[:-60]:
                        os.remove(os.path.join(dd, old))
                except Exception as e:  # noqa: BLE001
                    log("ecoute-debug", repr(e))
                propre = nettoyer_dictee(txt)
                if propre != txt:
                    log("dictée nettoyée : %r -> %r" % (txt[:80], propre[:80]))
                return self._json(200, {"text": propre})
            except Exception as e:  # noqa: BLE001
                stats["errors"] += 1
                stats["last_error"] = "whisper: " + repr(e)
                return self._json(502, {"error": "transcription impossible"})
        self._send(404, "introuvable")


if __name__ == "__main__":
    srv = ThreadingHTTPServer(("0.0.0.0", PORT), H)
    srv.daemon_threads = True
    threading.Thread(target=warm_acks, daemon=True).start()
    log("jarvis sur", PORT, "| cle", "oui" if KEY else "NON", "| session", session_id or "aucune")
    srv.serve_forever()

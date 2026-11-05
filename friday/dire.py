#!/usr/bin/env python3
"""Lit un texte sur le telephone de Loulou avec la voix de Jarvis (Paul, ElevenLabs).
Chemin : synthese sur le NAS -> fichier envoye au telephone (SSH Termux) -> play-audio.
Repli : notification TTS Home Assistant (voix Android) si le telephone n'est pas joignable.
Respecte l'interrupteur input_boolean.lecture_vocale_telephone. Usage : dire.py "texte" """
import subprocess, sys, time
sys.path.insert(0, "/home/USER/jarvis")
import server

# pauses (22/10/2026, Loulou : « version 2 ») : la vitesse est reglee chez ElevenLabs (server.EL_SPEED),
# ici on raccourcit seulement les vrais blancs (> 0,45 s ramenes a 0,35 s) sans toucher aux sons faibles des mots
FILTRE = ("silenceremove=start_periods=1:start_threshold=-55dB:stop_periods=-1:stop_duration=0.45:"
          "stop_threshold=-55dB:stop_silence=0.35,loudnorm=I=-16:TP=-1.5:LRA=11")


PC = "loulou@100.83.180.41"  # pc-perso (DreamQuest Max), Tailscale
SSH_PC = ["ssh", "-i", "/home/USER/.ssh/id_ed25519", "-o", "UserKnownHostsFile=/home/USER/.ssh/known_hosts",
          "-o", "ConnectTimeout=5", "-o", "BatchMode=yes", PC]
INACTIF_MAX_MS = 300000  # 5 min sans clavier/souris = Loulou n'est plus devant le PC

def devant_le_pc():
    """Vrai si le PC repond, ecran non verrouille et clavier/souris touches depuis moins de 5 min."""
    cmd = ("export DISPLAY=:0 XAUTHORITY=$HOME/.Xauthority; "
           "cinnamon-screensaver-command -q 2>/dev/null | grep -q 'est actif' && echo VERROU; "
           "echo UPTIME $(cut -d. -f1 /proc/uptime); xprintidle")
    try:
        r = subprocess.run(SSH_PC + [cmd], capture_output=True, timeout=12)
        out = r.stdout.decode()
        if "VERROU" in out:
            return False, "ecran verrouille"
        # 28/10/2026 : un PC tout juste reveille (WoL par Claude) a un compteur d'inactivite
        # remis a zero au demarrage de la session -> pris a tort pour "Loulou est devant".
        up = [l for l in out.splitlines() if l.startswith("UPTIME ")]
        if up and int(up[0].split()[1]) < 600:
            return False, "PC demarre depuis moins de 10 min"
        ms = int(out.strip().splitlines()[-1])
        return ms < INACTIF_MAX_MS, "inactif depuis %d s" % (ms // 1000)
    except Exception as e:
        return False, "PC injoignable (%r)" % e

args = sys.argv[1:]
limit = 600
gratuit = False
cible = "auto"  # 24/10/2026, Loulou : devant le PC -> voix sur le PC, sinon -> telephone
while args and args[0] in ("--long", "--gratuit", "--pc", "--tel"):
    if args[0] == "--long":  # resume long demande explicitement
        limit = 1600
    elif args[0] in ("--pc", "--tel"):  # forcer la cible
        cible = args[0][2:]
    else:  # voix gratuite du NAS (Piper), sans credits ElevenLabs : veille cyber (22/10/2026)
        gratuit = True
    args = args[1:]
text = " ".join(args).strip()
try:
    _, st = server.ha_call("GET", "/api/states/" + server.PHONE_SWITCH)
    if (st or {}).get("state") != "on":
        print("coupe (interrupteur eteint)"); sys.exit(0)
except Exception as e:
    print("interrupteur illisible", repr(e))
spoken = server.clean_for_speech(text, limit)
try:
    # voix propre aux resumes de Claude (Jarvis garde la sienne) : /home/USER/jarvis/voix-claude.json
    import json
    try:
        vc = json.load(open("/home/USER/jarvis/voix-claude.json", encoding="utf-8"))
    except (OSError, ValueError):
        vc = {}
    audio = None
    if gratuit:
        audio, mime, vname = server.synth(spoken), "audio/wav", "Piper (gratuite)"
    elif vc.get("voice_id"):
        try:
            audio, mime, vname = server.el_tts(spoken, vc["voice_id"]), "audio/mpeg", vc.get("name")
        except Exception as e:
            print("voix", vc.get("name"), "indisponible :", repr(e))
    if audio is None:
        audio, mime = server.tts(spoken)
        vname = server.voice_cfg().get("name")
    # meme volume a chaque phrase : normalisation du niveau sonore (-16 LUFS)
    try:
        n = subprocess.run(["ffmpeg", "-v", "error", "-i", "pipe:0", "-af", FILTRE,
                            "-ar", "44100", "-ac", "1", "-b:a", "128k", "-f", "mp3", "pipe:1"],
                           input=audio, capture_output=True, timeout=30)
        if n.returncode == 0 and len(n.stdout) > 1000:
            audio, mime = n.stdout, "audio/mpeg"
        else:
            print("normalisation ignoree :", n.stderr.decode()[-200:])
    except Exception as e:
        print("normalisation ignoree :", repr(e))
    ext = "mp3" if mime == "audio/mpeg" else "wav"
    if cible == "pc" or (cible == "auto" and devant_le_pc()[0]):
        cpc = ("f=/tmp/jarvis-dire.%s; cat > $f; [ -f /tmp/jarvis-dire.pid ] && kill $(cat /tmp/jarvis-dire.pid) 2>/dev/null; "
               "XDG_RUNTIME_DIR=/run/user/$(id -u) nohup mpv --no-video --really-quiet $f >/dev/null 2>&1 & "
               "echo $! > /tmp/jarvis-dire.pid; echo joue" % ext)
        r = subprocess.run(SSH_PC + [cpc], input=audio, capture_output=True, timeout=30)
        if r.returncode == 0 and b"joue" in r.stdout:
            print(time.strftime("%H:%M:%S"), "voix %s jouee sur le PC, %d car." % (vname, len(spoken)))
            sys.exit(0)
        print("PC en echec, repli telephone :", r.stderr.decode()[-200:].strip())
    cmd = ("f=$PREFIX/tmp/jarvis-dire.%s; cat > $f; pkill -x play-audio 2>/dev/null; "
           "(nohup play-audio $f >/dev/null 2>&1 &); echo joue" % ext)
    for essai in range(3):  # 29/10/2026 : la liaison avec le telephone coupe parfois une seconde -> on relance
        r = subprocess.run(["ssh", "-i", "/home/USER/.ssh/id_ed25519", "-o", "UserKnownHostsFile=/home/USER/.ssh/known_hosts",
                            "-o", "ConnectTimeout=6", "-o", "BatchMode=yes", "-p", "8022", "100.122.224.83", cmd],
                           input=audio, capture_output=True, timeout=30)
        if r.returncode == 0 and b"joue" in r.stdout:
            break
        print("essai %d vers le telephone en echec : %s" % (essai + 1, r.stderr.decode()[-150:].strip()))
        time.sleep(2)
    if r.returncode == 0 and b"joue" in r.stdout:
        print(time.strftime("%H:%M:%S"), "voix %s jouee sur le telephone, %d car." % (vname, len(spoken)))
        sys.exit(0)
    print("telephone injoignable en SSH :", r.stderr.decode()[-200:].strip())
except Exception as e:
    print("echec voix Jarvis :", repr(e))
server.phone_say(spoken)  # repli : voix Android
print("repli voix Android :", server.stats["phone_last"])

#!/bin/sh
# Friday : exécute une commande dans la session graphique de pc-perso (lancer une appli, volume, fenêtres, stats)
[ -n "$1" ] || { echo 'usage: pc.sh "<commande>"' >&2; exit 2; }
exec ssh -o ConnectTimeout=8 -o BatchMode=yes -i /home/USER/.ssh/id_ed25519 loulou@100.83.180.41 "export DISPLAY=:0 XDG_RUNTIME_DIR=/run/user/\$(id -u); $*"

#!/bin/sh
# Friday : capture l'écran de pc-perso et la rapatrie sur le NAS ; affiche le chemin de l'image (à lire ensuite)
D=/home/USER/jarvis/recus; F=$D/ecran-$(date +%Y%m%d-%H%M%S).png
ssh -o ConnectTimeout=8 -o BatchMode=yes -i /home/USER/.ssh/id_ed25519 loulou@100.83.180.41 'DISPLAY=:0 import -window root png:-' > "$F" 2>/dev/null
if [ -s "$F" ]; then echo "$F"; else rm -f "$F"; echo "ERREUR: PC éteint/injoignable ou capture impossible" >&2; exit 1; fi
ls -1t $D/ecran-*.png 2>/dev/null | tail -n +11 | xargs -r rm -f

#!/bin/sh
# Friday : rappel parlé sur le téléphone de Loulou.
# usage: rappel.sh <délai> "<texte>"   délai = 90s | 10m | 2h | HH:MM (prochaine occurrence)
#        rappel.sh liste               (rappels en attente)
#        rappel.sh annule <numéro>
L=/home/USER/jarvis/rappels.txt; touch $L
case "$1" in
  liste) [ -s $L ] && cat $L || echo "aucun rappel en attente"; exit 0;;
  annule) P=$(awk -F'|' -v n="$2" '$1==n{print $2}' $L); [ -n "$P" ] && kill "$P" 2>/dev/null; sed -i "/^$2|/d" $L; echo "rappel $2 annulé"; exit 0;;
esac
D="$1"; T="$2"; [ -n "$D" ] && [ -n "$T" ] || { echo 'usage: rappel.sh <délai> "<texte>"' >&2; exit 2; }
case "$D" in
  *:*) S=$(( $(date -d "$D" +%s) - $(date +%s) )); [ $S -le 0 ] && S=$(( S + 86400 ));;
  *h) S=$(( ${D%h} * 3600 ));; *m) S=$(( ${D%m} * 60 ));; *s) S=${D%s};;
  *) S=$(( D * 60 ));;
esac
QUAND=$(date -d "@$(( $(date +%s) + S ))" '+%d/%m %H:%M')
N=$(date +%s%N | cut -c8-13)
setsid nohup sh -c "sleep $S; python3 /home/USER/jarvis/dire.py --long \"Rappel : \$1\" >> /home/USER/jarvis/rappels.log 2>&1; sed -i '/^$N|/d' $L" _ "$T" >/dev/null 2>&1 &
echo "$N|$!|$QUAND|$T" >> $L
echo "rappel $N programmé pour $QUAND : $T"

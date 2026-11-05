#!/bin/sh
# Envoie une destination dans Eurowag Navigation (ex-RoadLord, com.roadlords.android) sur le telephone de Loulou
# et lance le guidage. Methode validee en vrai le 27/10/2026 (Pasdelou, Coulans-sur-Gee) :
# 29/10/2026 : l'ecran de guidage d'Eurowag a change (plus de primaryDirectionView) -> on reconnait aussi
#  BottomSheetInfoBar.RemainingDistanceAndEta / « Prochaine direction » (guidage reel vers La Genevraye).
#  lien geo:0,0?q=LAT,LON(NOM) -> fiche du lieu -> appui sur « Obtenir un itineraire » -> le guidage demarre seul.
# Usage : gps.sh <latitude> <longitude> ["nom du lieu"]
PKG=com.roadlords.android
[ $# -ge 2 ] || { echo "usage: gps.sh <lat> <lon> [\"nom\"]"; exit 2; }
LAT=$(printf '%s' "$1" | tr ',' '.'); LON=$(printf '%s' "$2" | tr ',' '.')
echo "$LAT" | grep -Eq '^-?[0-9]+(\.[0-9]+)?$' && echo "$LON" | grep -Eq '^-?[0-9]+(\.[0-9]+)?$' || { echo "ERREUR coordonnees invalides"; exit 2; }
NOM=$(printf '%s' "${3:-Destination}" | tr -cd 'A-Za-z0-9 _.-' | cut -c1-40)
URI="geo:0,0?q=$LAT,$LON($NOM)"
PH='am start -a android.intent.action.VIEW -d "'"$URI"'" -p '$PKG' >/dev/null 2>&1
B=""
for i in 1 2 3 4 5 6 7 8 9 10; do sleep 2; uiautomator dump /sdcard/gps1.xml >/dev/null 2>&1
  B=$(grep -o "text=\"Obtenir un itin[^\"]*\"[^>]*bounds=\"[^\"]*\"" /sdcard/gps1.xml | grep -o "bounds=\"[^\"]*\"" | head -1); [ -n "$B" ] && break; done
[ -z "$B" ] && { echo "ETAPE1_KO bouton Obtenir un itineraire introuvable"; exit 1; }
set -- $(echo "$B" | grep -oE "[0-9]+"); input tap $((($1+$3)/2)) $((($2+$4)/2))
for i in $(seq 1 15); do sleep 3; uiautomator dump /sdcard/gps2.xml >/dev/null 2>&1
  grep -q -e "primaryDirectionView" -e "BottomSheetInfoBar.RemainingDistanceAndEta" -e "Prochaine direction" /sdcard/gps2.xml && { echo "GUIDAGE_OK"; exit 0; }
  P=$(grep -o "text=\"D[ée]marrer[^\"]*\"[^>]*bounds=\"[^\"]*\"" /sdcard/gps2.xml | grep -o "bounds=\"[^\"]*\"" | head -1)
  [ -n "$P" ] && { set -- $(echo "$P" | grep -oE "[0-9]+"); input tap $((($1+$3)/2)) $((($2+$4)/2)); }
done
echo "ETAPE2_KO itineraire demande mais guidage non demarre"; exit 1'
B64=$(printf '%s' "$PH" | base64 -w0)
R=$(ssh -i /home/USER/.ssh/id_ed25519 -o UserKnownHostsFile=/home/USER/.ssh/known_hosts \
    -o ConnectTimeout=8 -o BatchMode=yes -p 8022 100.122.224.83 \
    "echo $B64 | base64 -d > /sdcard/gpsrun.sh; rish -c 'sh /sdcard/gpsrun.sh'" 2>&1)
case "$R" in
  *GUIDAGE_OK*) echo "OK guidage lance dans Eurowag Navigation vers $NOM ($LAT,$LON)";;
  *"not running"*) echo "ERREUR SHIZUKU_ARRETE : Shizuku est arrete sur le telephone, GPS impossible"; exit 1;;  # 03/11/2026
  *"Connection refused"*|*"timed out"*) echo "ERREUR telephone injoignable (Termux ferme ?)"; exit 1;;
  *) echo "ERREUR $R"; exit 1;;
esac

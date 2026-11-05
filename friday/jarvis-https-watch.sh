#!/bin/sh
# Attend que HTTPS soit active dans Tailscale, puis publie Jarvis en https et previent Loulou.
L=/home/USER/jarvis/https-watch.log
N=freeplex.taild96231.ts.net
K=$(cat /home/USER/jarvis/.key)
echo "$(date +%T) debut surveillance" >> $L
i=0
while [ $i -lt 480 ]; do
  i=$((i+1))
  if timeout 60 tailscale cert --cert-file /tmp/jv.crt --key-file /tmp/jv.key $N >> $L 2>&1; then
    rm -f /tmp/jv.crt /tmp/jv.key
    echo "$(date +%T) certificat OK" >> $L
    tailscale serve --bg --https=8443 http://127.0.0.1:8790 >> $L 2>&1
    sleep 5
    tailscale serve status >> $L 2>&1
    C=$(curl -s -o /dev/null -w "%{http_code}" --resolve $N:8443:100.x.y.z https://$N:8443/health)
    echo "$(date +%T) test https = $C" >> $L
    if [ "$C" = "200" ]; then
      T=$(cat /home/USER/.ha_token)
      U="https://$N:8443/#k=$K"
      curl -s -o /dev/null -w "$(date +%T) notif %{http_code}\n" -X POST -H "Authorization: Bearer $T" -H "Content-Type: application/json" \
        -d "{\"title\":\"Freeplex : micro prêt\",\"message\":\"L'adresse sécurisée marche. Touche ici pour ouvrir Jarvis.\",\"data\":{\"clickAction\":\"$U\",\"url\":\"$U\",\"priority\":\"high\",\"ttl\":0}}" \
        http://127.0.0.1:8123/api/services/notify/mobile_app_tel_loulou >> $L 2>&1
      echo "FIN OK" >> $L
    else
      echo "FIN ECHEC test https" >> $L
    fi
    exit 0
  fi
  sleep 30
done
echo "FIN delai depasse (4 h)" >> $L

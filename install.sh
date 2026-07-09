#!/usr/bin/env bash
# Déploie exec-api en conteneur Docker permanent (restart: unless-stopped).
# Usage : ./install.sh
set -euo pipefail
cd "$(dirname "$0")"

command -v docker >/dev/null || { echo "Docker requis. Installe Docker puis relance."; exit 1; }
docker compose version >/dev/null 2>&1 || { echo "Docker Compose v2 requis."; exit 1; }

# .env + token
if [ ! -f .env ]; then cp .env.example .env; fi
if grep -q "remplacez_par" .env; then
  TOK=$(openssl rand -hex 32)
  sed -i.bak "s|EXEC_API_TOKEN=.*|EXEC_API_TOKEN=$TOK|" .env && rm -f .env.bak
  echo ">> Token généré : $TOK"
  echo "   >>> NOTE-LE, il te faut pour Claude <<<"
fi

echo ">> Build + démarrage (permanent)"
docker compose up -d --build

echo ">> Test /health"
sleep 3
curl -s http://127.0.0.1:5555/health && echo
cat <<MSG

>> exec-api tourne (redémarre automatiquement au boot).
>> Expose-le en HTTPS de façon permanente :
   - Sans reverse proxy : tunnel/cloudflared-permanent.md   (recommandé)
   - Avec Nginx déjà en place : nginx/exec.location.conf
MSG

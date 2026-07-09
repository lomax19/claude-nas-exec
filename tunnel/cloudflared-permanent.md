# Exposition permanente sans reverse proxy : Cloudflare Tunnel nommé (service)

Voie recommandée si tu n'as PAS déjà Nginx+certbot. URL HTTPS fixe sur ton
domaine, démarrage au boot, aucun port ouvert sur la box.

Prérequis : un domaine géré sur Cloudflare (gratuit).

    # 1. Installer cloudflared (Debian/Ubuntu)
    curl -L https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64 -o /usr/local/bin/cloudflared
    chmod +x /usr/local/bin/cloudflared

    # 2. Authentifier + créer le tunnel
    cloudflared tunnel login
    cloudflared tunnel create exec-api
    cloudflared tunnel route dns exec-api exec.ton-domaine.com

    # 3. Config  (~/.cloudflared/config.yml)
    #   tunnel: exec-api
    #   credentials-file: /root/.cloudflared/<UUID>.json
    #   ingress:
    #     - hostname: exec.ton-domaine.com
    #       service: http://127.0.0.1:5555
    #     - service: http_status:404

    # 4. Installer comme service permanent (démarre au boot)
    sudo cloudflared service install
    sudo systemctl enable --now cloudflared

Endpoint : https://exec.ton-domaine.com/exec

Durcissement conseillé : une Cloudflare Access policy devant l'hostname ajoute
une 2e couche d'auth en plus du token.

# claude-nas-exec

Donne à Claude (claude.ai) un accès direct à ton NAS via une API HTTP sécurisée.
Plus besoin de copier-coller des commandes — Claude les exécute lui-même.

## Comment ça fonctionne

Un conteneur Docker expose une API Flask sur ton NAS. Claude l'appelle depuis son environnement via HTTPS. Le Docker CLI est inclus dans le conteneur pour piloter les autres services.

    Claude (claude.ai) → HTTPS → Nginx → exec-api (Flask) → shell NAS

## Prérequis

- NAS avec Docker et Docker Compose
- Un domaine avec HTTPS déjà configuré (Nginx + certbot ou équivalent)
- Accès SSH au NAS

## Installation

### 1. Copier les fichiers sur le NAS

    mkdir -p ~/docker/exec-api
    # Copiez app.py et Dockerfile dans ~/docker/exec-api/

### 2. Générer un token secret

    openssl rand -hex 32

### 3. Créer le fichier .env

    cp .env.example .env
    nano .env

### 4. Ajouter le service dans votre docker-compose.yml

Copiez le contenu de docker-compose-snippet.yml dans votre compose existant.
Adaptez les noms de réseaux à votre configuration.

### 5. Ajouter le bloc Nginx

    location = /exec {
        proxy_pass http://exec-api:5555/exec;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_read_timeout 35s;
    }

### 6. Build et démarrage

    docker compose -f ~/docker-compose.yml build exec-api
    docker compose -f ~/docker-compose.yml up -d exec-api
    docker exec votre-nginx nginx -s reload

### 7. Test

    curl -s -X POST https://votre-domaine.com/exec \
      -H "X-Token: VOTRE_TOKEN" \
      -H "Content-Type: application/json" \
      -d '{"cmd":"whoami"}'

## Utilisation avec Claude

En début de conversation sur claude.ai, donnez ce prompt à Claude :

    Mon NAS est accessible via une API HTTP.
    URL : POST https://votre-domaine.com/exec
    Header : X-Token: VOTRE_TOKEN
    Body JSON : {"cmd":"ta commande"}
    Docker CLI est disponible. Utilise le bash_tool avec Python urllib pour exécuter les commandes.

## Sécurité

- Token fort obligatoire : openssl rand -hex 32
- HTTPS uniquement
- Ne commitez jamais votre .env (il est dans .gitignore)
- Volumes montés en lecture seule (:ro)
- Gardez votre token privé

## Structure

    claude-nas-exec/
    ├── app.py
    ├── Dockerfile
    ├── docker-compose-snippet.yml
    ├── .env.example
    ├── .gitignore
    └── README.md

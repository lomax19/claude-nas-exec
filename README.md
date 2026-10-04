# claude-nas-exec

Donne à **Claude** (claude.ai) un accès shell à ton **NAS / serveur Linux**, en
permanence. Claude exécute les commandes lui-même au lieu de te faire copier-coller.
Le Docker CLI est embarqué : Claude peut aussi piloter tes autres conteneurs.

Version **serveur/permanent** : le service tourne 24/7 et redémarre au boot.
Pour un simple PC (Windows/mac/Linux, exposition à la demande), vois plutôt
[claude-pc-exec](https://github.com/lomax19/claude-pc-exec).

```
Claude (claude.ai) → HTTPS → tunnel ou reverse proxy → exec-api (Flask) → shell NAS
```

Le token en en-tête `X-Token` est la clé d'accès.

---

## Prérequis

- NAS/serveur Linux avec **Docker + Docker Compose v2**
- Accès SSH
- Une voie d'exposition HTTPS (au choix) :
  - **Cloudflare Tunnel** — si tu n'as pas de reverse proxy (le plus simple)
  - **Reverse proxy existant** (Nginx/Caddy/Traefik) + domaine HTTPS

## Installation

### 1. Récupérer et déployer

```bash
git clone https://github.com/lomax19/claude-nas-exec.git
cd claude-nas-exec
./install.sh
```

`install.sh` génère le token, build l'image et démarre le conteneur en
**permanent** (`restart: unless-stopped`), puis teste `/health`. **Note le token.**

> Déjà un compose avec reverse proxy conteneurisé ? Utilise plutôt
> [`docker-compose-snippet.yml`](docker-compose-snippet.yml) (à insérer dans ton
> compose) au lieu du déploiement autonome.

### 2. Exposer en HTTPS (permanent)

| Situation | Voie | Guide |
|---|---|---|
| Pas de reverse proxy | Cloudflare Tunnel nommé (service) | [tunnel/cloudflared-permanent.md](tunnel/cloudflared-permanent.md) |
| Nginx déjà en place | bloc `location /exec` | [nginx/exec.location.conf](nginx/exec.location.conf) |

### 3. Tester

```bash
curl -s -X POST https://ton-domaine.com/exec \
  -H "X-Token: TON_TOKEN" -H "Content-Type: application/json" \
  -d '{"cmd":"whoami"}'
```

### 4. Dire à Claude d'utiliser ton NAS

```
Mon NAS est accessible via une API HTTP.
URL : POST https://ton-domaine.com/exec
Header : X-Token: TON_TOKEN
Body JSON : {"cmd":"la commande"}
Docker CLI est disponible. Utilise ton outil bash pour exécuter les commandes toi-même.
```

---

## Ce que tu peux demander à Claude

- **Piloter Docker** : « redémarre le conteneur plex », « quels conteneurs sont
  down ? », « montre les logs de sonarr ».
- **Maintenance** : « espace disque restant ? », « nettoie les images docker
  inutilisées », « qu'est-ce qui charge le CPU ? ».
- **Fichiers/médias** : « trouve les doublons dans /mnt/films », « renomme cette
  série au bon format ».
- **Automatiser** : « écris un script de sauvegarde nocturne et planifie-le ».

---

## Endpoints

| Méthode | Chemin | Rôle |
|---|---|---|
| POST | `/exec` | exécute `cmd`, renvoie `stdout` / `stderr` / `returncode` |
| GET | `/health` | statut (monitoring / Uptime Kuma) |

## Configuration (`.env`)

| Variable | Défaut | Rôle |
|---|---|---|
| `EXEC_API_TOKEN` | — | **obligatoire**, >= 32 hex (`openssl rand -hex 32`) |
| `EXEC_API_HOST` | `0.0.0.0` | conteneur derrière reverse proxy |
| `EXEC_API_PORT` | `5555` | port d'écoute |
| `EXEC_API_TIMEOUT` | `30` | timeout par commande (s) |
| `EXEC_API_MAXOUT` | `100000` | troncature des sorties (caractères) |
| `EXEC_API_CWD` | `~` | répertoire de travail |
| `EXEC_API_TRUSTED_PROXIES` | `1` | proxies de confiance devant l'API (vraie IP dans les logs / fail2ban) ; `0` si exposée sans proxy |
| `EXEC_API_MAXBODY` | `1048576` | taille max du corps de requête (octets) |

---

## /!\ Sécurité

Cet endpoint **exécute des commandes shell arbitraires** avec accès au socket
Docker. Exposé en permanence, **le token est l'unique barrière**.

**Accès au socket Docker = root sur l'hôte.** Le `:ro` sur `docker.sock`
n'empêche PAS d'appeler l'API Docker : avec le token, on peut lancer
`docker run --privileged -v /:/host ...` et prendre la main sur la machine.
Le `:ro` des autres volumes ne protège donc les fichiers que contre une écriture
directe, pas contre un attaquant qui a le token.

- Token fort obligatoire : `openssl rand -hex 32` (l'installeur le fait).
- HTTPS uniquement, jamais en HTTP clair.
- Ne commite jamais `.env` (déjà dans `.gitignore`).
- Volumes en lecture seule (`:ro`) — limite les erreurs, pas un attaquant (cf. ci-dessus).
- Fais tourner le token régulièrement, et immédiatement s'il a pu fuiter
  (collé dans un chat, une capture, un dépôt...).
- Le service journalise chaque commande : `docker logs exec-api`.
- 2e couche conseillée : Cloudflare Access, ou restriction IP côté reverse proxy.
- Coupe le service quand tu ne t'en sers pas de façon prolongée :
  `docker compose stop exec-api`.

---

## Mise à jour / désinstallation

```bash
# mise à jour
git pull && docker compose up -d --build

# arrêt
docker compose stop exec-api

# suppression complète
docker compose down && docker rmi claude-nas-exec-exec-api
```

## Structure

```
claude-nas-exec/
├── app.py                       # API Flask durcie (audit, /health, timeout, token temps constant)
├── requirements.txt
├── Dockerfile                   # Flask + Docker CLI
├── install.sh                   # déploiement Docker permanent clé en main
├── docker-compose.yml           # déploiement autonome
├── docker-compose-snippet.yml   # à insérer dans un compose existant (reverse proxy)
├── nginx/exec.location.conf     # bloc reverse proxy Nginx
├── tunnel/cloudflared-permanent.md
├── .env.example
├── .gitignore
└── LICENSE
```

## Licence

MIT — voir [LICENSE](LICENSE). Réutilise, modifie et partage librement.

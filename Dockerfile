FROM python:3.12-slim
WORKDIR /app

# Docker CLI pour piloter les autres conteneurs depuis l'API
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl ca-certificates \
    && install -m 0755 -d /etc/apt/keyrings \
    && curl -fsSL https://download.docker.com/linux/debian/gpg -o /etc/apt/keyrings/docker.asc \
    && echo "deb [arch=amd64 signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/debian bookworm stable" \
       > /etc/apt/sources.list.d/docker.list \
    && apt-get update \
    && apt-get install -y docker-ce-cli \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app.py .
# gunicorn (gthread) : plusieurs commandes en parallèle ; son timeout worker
# doit dépasser EXEC_API_TIMEOUT sinon il tue la requête avant le 504 propre.
CMD ["sh", "-c", "exec gunicorn --bind ${EXEC_API_HOST:-0.0.0.0}:${EXEC_API_PORT:-5555} --worker-class gthread --workers 1 --threads 8 --timeout $(( ${EXEC_API_TIMEOUT:-30} + 15 )) --access-logfile - app:app"]

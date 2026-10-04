from flask import Flask, request, jsonify
import subprocess, os, signal, hmac, logging, platform, sys

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from werkzeug.middleware.proxy_fix import ProxyFix

app = Flask(__name__)

TOKEN   = os.environ.get("EXEC_API_TOKEN", "")
HOST    = os.environ.get("EXEC_API_HOST", "0.0.0.0")     # derrière reverse proxy / réseau docker
PORT    = int(os.environ.get("EXEC_API_PORT", "5555"))
TIMEOUT = int(os.environ.get("EXEC_API_TIMEOUT", "30"))
MAXOUT  = int(os.environ.get("EXEC_API_MAXOUT", "100000"))
WORKDIR = os.environ.get("EXEC_API_CWD", os.path.expanduser("~"))
# Nb de reverse proxies de confiance devant l'API (tunnel / Nginx) : sert à
# journaliser la vraie IP client (X-Forwarded-For) pour fail2ban. 0 = désactivé.
PROXIES = int(os.environ.get("EXEC_API_TRUSTED_PROXIES", "1"))
MAXBODY = int(os.environ.get("EXEC_API_MAXBODY", "1048576"))  # 1 Mio

app.config["MAX_CONTENT_LENGTH"] = MAXBODY
if PROXIES > 0:
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=PROXIES)

if not TOKEN or len(TOKEN) < 32:
    raise RuntimeError("EXEC_API_TOKEN absent ou trop court (>= 32 hex). Arrêt.")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("exec-api")


def _authorized(req) -> bool:
    return hmac.compare_digest(req.headers.get("X-Token", ""), TOKEN)


def _trunc(s: str) -> str:
    return s if len(s) <= MAXOUT else s[:MAXOUT] + f"\n...[tronqué à {MAXOUT} caractères]"


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok", "os": platform.system()})


@app.route("/exec", methods=["POST"])
def exec_cmd():
    if not _authorized(request):
        log.warning("401 depuis %s", request.remote_addr)
        return jsonify({"error": "unauthorized"}), 401

    data = request.get_json(silent=True) or {}
    cmd = data.get("cmd", "")
    if not cmd:
        return jsonify({"error": "no cmd"}), 400

    log.info("EXEC: %s", cmd if len(cmd) < 500 else cmd[:500] + "…")
    # Groupe de processus dédié : au timeout on tue le shell ET ses enfants
    # (docker exec, find, rsync...), sinon ils continuent en orphelins.
    proc = subprocess.Popen(
        cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, cwd=WORKDIR, start_new_session=True,
    )
    try:
        stdout, stderr = proc.communicate(timeout=TIMEOUT)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        proc.communicate()
        log.warning("TIMEOUT (%ss): %s", TIMEOUT, cmd[:200])
        return jsonify({"error": "timeout", "timeout": TIMEOUT}), 504

    return jsonify({
        "stdout": _trunc(stdout),
        "stderr": _trunc(stderr),
        "returncode": proc.returncode,
    })


if __name__ == "__main__":
    # Serveur de dev Flask : test local uniquement. En prod (Dockerfile) : gunicorn.
    log.info("exec-api démarre sur %s:%s (os=%s, cwd=%s)", HOST, PORT, platform.system(), WORKDIR)
    app.run(host=HOST, port=PORT)

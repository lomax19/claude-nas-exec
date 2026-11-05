#!/usr/bin/env python3
# Serveur MCP "nas-vocal" (21/10/2026) : donne a Claude (appli, mode vocal) un shell sur le NAS
# via l'exec-api existante. Streamable HTTP sans etat, reponses JSON, stdlib uniquement.
# Garde-fous : jeton Bearer injecte par nginx, interrupteur HA input_boolean.acces_nas_vocal
# relu a chaque requete (ferme si HA injoignable), journal de chaque commande.
import base64, datetime, hmac, json, os, secrets, time, urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BEARER = os.environ["NAS_VOCAL_BEARER"]
EXEC_URL = os.environ.get("EXEC_URL", "http://exec-api:5555/exec")
EXEC_TOKENS = "/secrets/exec_tokens.txt"
HA_URL = os.environ.get("HA_URL", "http://192.168.1.X:8123")
HA_TOKEN_PATH = "/secrets/ha_token"
SWITCH = os.environ.get("SWITCH", "input_boolean.acces_nas_vocal")
DATA = "/data"                                   # = /home/USER/docker/nas-vocal-mcp/data
HOST_DATA = "/home/USER/docker/nas-vocal-mcp/data"  # meme dossier vu par l'exec-api
JOURNAL = DATA + "/commands.log"
MAX_WAIT = 45        # nginx coupe a 60 s : au-dela, la commande continue en arriere-plan
MAX_OUT = 15000
PROTOCOLS = ("2025-06-18", "2025-03-26", "2024-11-05")

TOOLS = [
    {
        "name": "run_command",
        "description": (
            "Execute une commande bash sur le NAS Freeplex (utilisateur loulou, dossier ~). "
            "Renvoie stdout, stderr et le code retour. Si la commande depasse wait_seconds "
            "(45 s max), elle continue en arriere-plan : utiliser read_job avec le job_id renvoye."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "Commande bash a executer"},
                "wait_seconds": {"type": "integer", "minimum": 1, "maximum": MAX_WAIT,
                                 "description": "Attente max avant de rendre la main (defaut 40)"},
            },
            "required": ["command"],
        },
    },
    {
        "name": "read_job",
        "description": "Relit l'etat et la sortie (fin du log) d'une commande lancee par run_command.",
        "inputSchema": {
            "type": "object",
            "properties": {"job_id": {"type": "string"}},
            "required": ["job_id"],
        },
    },
]


def _now():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def journal(line):
    try:
        with open(JOURNAL, "a", encoding="utf-8") as f:
            f.write("{} | {}\n".format(_now(), line.replace("\n", "\\n")))
    except Exception:
        pass


def switch_on():
    # Ferme par defaut : toute erreur = acces refuse
    try:
        tok = open(HA_TOKEN_PATH).read().strip()
        req = urllib.request.Request(HA_URL + "/api/states/" + SWITCH,
                                     headers={"Authorization": "Bearer " + tok})
        with urllib.request.urlopen(req, timeout=5) as r:
            return json.load(r).get("state") == "on"
    except Exception:
        return False


def exec_api(cmd):
    tok = next(l.strip() for l in open(EXEC_TOKENS) if l.strip() and not l.startswith("#"))
    req = urllib.request.Request(EXEC_URL, data=json.dumps({"cmd": cmd}).encode(),
                                 headers={"Content-Type": "application/json", "X-Token": tok})
    with urllib.request.urlopen(req, timeout=58) as r:
        return json.load(r)


def _tail(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            s = f.read()
    except FileNotFoundError:
        return ""
    return s if len(s) <= MAX_OUT else "[... debut tronque ...]\n" + s[-MAX_OUT:]


def job_state(job_id):
    p = DATA + "/jobs/" + job_id
    if not os.path.exists(p + ".sh"):
        return {"error": "job inconnu : " + job_id}
    res = {"job_id": job_id, "stdout": _tail(p + ".out"), "stderr": _tail(p + ".err"),
           "log_stdout": HOST_DATA + "/jobs/" + job_id + ".out",
           "log_stderr": HOST_DATA + "/jobs/" + job_id + ".err"}
    if os.path.exists(p + ".rc"):
        res["status"] = "termine"
        res["returncode"] = int(open(p + ".rc").read().strip() or -1)
    else:
        res["status"] = "en cours"
    return res


def purge_jobs():
    limit = time.time() - 7 * 86400
    for n in os.listdir(DATA + "/jobs"):
        try:
            if os.path.getmtime(DATA + "/jobs/" + n) < limit:
                os.remove(DATA + "/jobs/" + n)
        except OSError:
            pass


def run_command(args):
    cmd = (args.get("command") or "").strip()
    if not cmd:
        return {"error": "commande vide"}
    wait = max(1, min(int(args.get("wait_seconds") or 40), MAX_WAIT))
    job_id = datetime.datetime.now().strftime("%Y%m%d-%H%M%S-") + secrets.token_hex(3)
    journal("{} | {}".format(job_id, cmd))
    purge_jobs()
    j = HOST_DATA + "/jobs/" + job_id
    b64 = base64.b64encode(cmd.encode()).decode()
    # setsid + nohup : la commande survit a la fin de l'appel exec-api (coupure 60 s)
    launch = (
        "cd ~ && echo {b} | base64 -d > {j}.sh && "
        "{{ setsid nohup bash -c 'bash {j}.sh > {j}.out 2> {j}.err < /dev/null; "
        "echo $? > {j}.rc.tmp; mv {j}.rc.tmp {j}.rc' > /dev/null 2>&1 < /dev/null & }} && echo lance"
    ).format(b=b64, j=j)
    r = exec_api(launch)
    if r.get("returncode") != 0:
        return {"error": "lancement echoue", "detail": r}
    deadline = time.time() + wait
    while time.time() < deadline:
        if os.path.exists(DATA + "/jobs/" + job_id + ".rc"):
            break
        time.sleep(0.3)
    res = job_state(job_id)
    if res.get("status") == "en cours":
        res["note"] = ("Toujours en cours apres {} s, continue en arriere-plan. "
                       "Relire avec read_job job_id={}".format(wait, job_id))
    return res


def call_tool(name, args):
    if name == "run_command":
        return run_command(args)
    if name == "read_job":
        return job_state(os.path.basename(str(args.get("job_id", ""))))
    raise KeyError(name)


def handle_rpc(msg):
    mid, method = msg.get("id"), msg.get("method", "")
    if mid is None:      # notification
        return None
    try:
        if method == "initialize":
            v = (msg.get("params") or {}).get("protocolVersion")
            result = {"protocolVersion": v if v in PROTOCOLS else PROTOCOLS[0],
                      "capabilities": {"tools": {"listChanged": False}},
                      "serverInfo": {"name": "nas-vocal", "version": "1.0"},
                      "instructions": "Shell du NAS Freeplex de Loulou. Commandes longues : read_job."}
        elif method == "ping":
            result = {}
        elif method == "tools/list":
            result = {"tools": TOOLS}
        elif method == "tools/call":
            p = msg.get("params") or {}
            try:
                out = call_tool(p.get("name"), p.get("arguments") or {})
            except KeyError:
                return {"jsonrpc": "2.0", "id": mid,
                        "error": {"code": -32602, "message": "outil inconnu"}}
            except Exception as e:
                out = {"error": "{}: {}".format(type(e).__name__, e)}
            result = {"content": [{"type": "text", "text": json.dumps(out, ensure_ascii=False, indent=1)}],
                      "isError": "error" in out}
        else:
            return {"jsonrpc": "2.0", "id": mid,
                    "error": {"code": -32601, "message": "methode inconnue : " + method}}
        return {"jsonrpc": "2.0", "id": mid, "result": result}
    except Exception as e:
        return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32603, "message": str(e)}}


class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *a):
        pass

    def _send(self, code, body=None):
        data = b"" if body is None else json.dumps(body, ensure_ascii=False).encode()
        self.send_response(code)
        if body is not None:
            self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path == "/healthz":
            return self._send(200, {"ok": True})
        self._send(405, {"error": "SSE non supporte"})

    do_DELETE = do_GET

    def do_POST(self):
        if self.path.split("?")[0] != "/mcp":
            return self._send(404, {"error": "not_found"})
        auth = self.headers.get("Authorization", "")
        if not hmac.compare_digest(auth, "Bearer " + BEARER):
            journal("REFUS jeton invalide")
            return self._send(401, {"error": "unauthorized"})
        if not switch_on():
            journal("REFUS interrupteur " + SWITCH + " eteint")
            return self._send(403, {"error": "acces NAS vocal coupe (interrupteur Home Assistant)"})
        try:
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"null")
        except Exception:
            return self._send(400, {"jsonrpc": "2.0", "id": None,
                                    "error": {"code": -32700, "message": "JSON invalide"}})
        if isinstance(body, list):
            out = [r for r in (handle_rpc(m) for m in body) if r is not None]
        else:
            out = handle_rpc(body or {})
        if not out:
            return self._send(202)
        self._send(200, out)


if __name__ == "__main__":
    os.makedirs(DATA + "/jobs", exist_ok=True)
    ThreadingHTTPServer(("0.0.0.0", 8768), H).serve_forever()

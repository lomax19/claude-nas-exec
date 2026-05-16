from flask import Flask, request, jsonify
import subprocess, os

app = Flask(__name__)
TOKEN = os.environ.get("EXEC_API_TOKEN", "")

if not TOKEN:
    raise RuntimeError("EXEC_API_TOKEN non défini — arrêt du serveur.")

@app.route("/exec", methods=["POST"])
def exec_cmd():
    if request.headers.get("X-Token") != TOKEN:
        return jsonify({"error": "unauthorized"}), 401
    cmd = request.json.get("cmd", "")
    if not cmd:
        return jsonify({"error": "no cmd"}), 400
    result = subprocess.run(
        cmd, shell=True, capture_output=True, text=True,
        timeout=30, cwd=os.path.expanduser("~")
    )
    return jsonify({
        "stdout": result.stdout,
        "stderr": result.stderr,
        "returncode": result.returncode
    })

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5555)

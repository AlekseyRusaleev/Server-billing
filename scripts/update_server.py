from __future__ import annotations

import json
import os
import subprocess
import threading
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

INSTALL_DIR = Path(os.environ.get("INSTALL_DIR", "/repo")).resolve()
TOKEN = os.environ.get("UPDATER_TOKEN", "")
PORT = int(os.environ.get("UPDATER_PORT", "8765"))
LOG_PATH = INSTALL_DIR / "data" / "last_update.log"

lock = threading.Lock()
running = False


def write_log(text: str) -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOG_PATH.open("a", encoding="utf-8") as file:
        file.write(text)


def run_command(command: list[str]) -> None:
    write_log(f"\n$ {' '.join(command)}\n")
    completed = subprocess.run(
        command,
        cwd=INSTALL_DIR,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    write_log(completed.stdout)
    if completed.returncode != 0:
        raise RuntimeError(f"Command failed: {' '.join(command)}")


def update_repository() -> None:
    global running
    try:
        LOG_PATH.write_text(
            f"Update started at {datetime.utcnow().isoformat()}Z\n",
            encoding="utf-8",
        )
        run_command(["git", "config", "--global", "--add", "safe.directory", str(INSTALL_DIR)])
        run_command(["git", "pull", "--ff-only"])
        run_command(["docker", "compose", "-f", "docker-compose.prod.yml", "up", "-d", "--build"])
        write_log(f"\nUpdate finished at {datetime.utcnow().isoformat()}Z\n")
    except Exception as error:
        write_log(f"\nUpdate failed: {error}\n")
    finally:
        with lock:
            running = False


class UpdateHandler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:
        global running
        if self.path != "/update":
            self.respond(404, {"ok": False, "message": "Not found."})
            return
        if not TOKEN or self.headers.get("X-Update-Token") != TOKEN:
            self.respond(403, {"ok": False, "message": "Forbidden."})
            return
        with lock:
            if running:
                self.respond(200, {"ok": True, "message": "Update is already running."})
                return
            running = True
        threading.Thread(target=update_repository, daemon=True).start()
        self.respond(202, {"ok": True, "message": "Update started."})

    def log_message(self, format: str, *args: object) -> None:
        return

    def respond(self, status: int, payload: dict[str, object]) -> None:
        raw = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


if __name__ == "__main__":
    server = ThreadingHTTPServer(("0.0.0.0", PORT), UpdateHandler)
    server.serve_forever()

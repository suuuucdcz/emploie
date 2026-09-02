"""Serveur local de l'emploi du temps Auriga.

Deux roles :
  1. servir la PWA (dossier public/) ;
  2. recuperer le flux ICS cote serveur (pas de CORS) et le renvoyer en JSON.

Uniquement de la bibliotheque standard : `python server.py` et c'est parti.
"""

import argparse
import json
import mimetypes
import os
import ssl
import sys
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import ics

ROOT = os.path.dirname(os.path.abspath(__file__))
PUBLIC_DIR = os.path.join(ROOT, "public")
CACHE_DIR = os.path.join(ROOT, "cache")
CACHE_ICS = os.path.join(CACHE_DIR, "schedule.ics")
CONFIG_PATH = os.path.join(ROOT, "config.json")
SAMPLE_ICS = os.path.join(ROOT, "sample.ics")

DEFAULTS = {
    "ics_url": "",
    "port": 8787,
    "refresh_seconds": 900,
    "timeout_seconds": 20,
}

_lock = threading.Lock()
_memory = {"fetched_at": 0.0, "events": None, "source": "", "stale": False}


# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------

def load_config(argv=None):
    config = dict(DEFAULTS)

    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as handle:
                config.update(json.load(handle))
        except (OSError, ValueError) as exc:
            print("[config] config.json illisible (%s), valeurs par defaut" % exc)

    if os.environ.get("AURIGA_ICS_URL"):
        config["ics_url"] = os.environ["AURIGA_ICS_URL"]
    if os.environ.get("PORT"):
        config["port"] = int(os.environ["PORT"])
    elif os.environ.get("AURIGA_PORT"):
        config["port"] = int(os.environ["AURIGA_PORT"])

    parser = argparse.ArgumentParser(description="Emploi du temps Auriga")
    parser.add_argument("--ics-url", help="URL du flux ICS Aurion")
    parser.add_argument("--ics-file", help="Fichier .ics local (mode hors ligne)")
    parser.add_argument("--port", type=int, help="Port d'ecoute")
    parser.add_argument("--insecure", action="store_true",
                        help="Ignorer la verification du certificat TLS")
    args = parser.parse_args(argv)

    if args.ics_url:
        config["ics_url"] = args.ics_url
    if args.ics_file:
        config["ics_file"] = args.ics_file
    if args.port and not os.environ.get('PORT'):
        config["port"] = args.port
    config["insecure"] = bool(args.insecure)

    return config


# --------------------------------------------------------------------------
# Recuperation du flux
# --------------------------------------------------------------------------

_memories = {}

def fetch_ics(config, email):
    if not email:
        raise ValueError("Email manquant")
        
    supabase_url = os.environ.get("SUPABASE_URL")
    supabase_key = os.environ.get("SUPABASE_KEY")
    
    if supabase_url and supabase_key:
        req = urllib.request.Request(f"{supabase_url}/rest/v1/schedules?email=eq.{email}&select=ics_content", headers={
            "apikey": supabase_key,
            "Authorization": f"Bearer {supabase_key}"
        })
        try:
            with urllib.request.urlopen(req) as response:
                data = json.loads(response.read().decode('utf-8'))
                if data and len(data) > 0:
                    return data[0]["ics_content"], "base de données Supabase"
        except Exception as e:
            pass
            
    # Fallback local
    safe_email = email.replace('@', '_').replace('.', '_')
    path = f"cache/{safe_email}.ics"
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            return handle.read(), f"cache local ({safe_email})"
            
    raise ValueError("Aucun agenda pour cet utilisateur. (Veuillez synchroniser)")

def get_schedule(config, force=False, email=None):
    ttl = config.get("refresh_seconds", 900)
    
    if email not in _memories:
        _memories[email] = {"fetched_at": 0, "events": None, "source": None, "stale": False}
    _memory = _memories[email]

    with _lock:
        fresh_enough = (
            _memory["events"] is not None
            and not force
            and (time.time() - _memory["fetched_at"]) < ttl
        )
        if fresh_enough:
            return _snapshot(_memory)

        try:
            text, source = fetch_ics(config, email)
            events = ics.parse(text)
            _memory.update(
                fetched_at=time.time(), events=events, source=source, stale=False
            )
            return _snapshot(_memory)
        except Exception as exc:
            if _memory["events"] is not None:
                _memory["stale"] = True
                return _snapshot(_memory, error=str(exc))
            cached = _load_disk_cache(email)
            if cached is not None:
                safe_email = email.replace('@', '_').replace('.', '_')
                _memory.update(
                    fetched_at=os.path.getmtime(f"cache/{safe_email}.ics"),
                    events=cached,
                    source="cache disque",
                    stale=True,
                )
                return _snapshot(_memory, error=str(exc))
            raise

def _load_disk_cache(email):
    if not email: return None
    safe_email = email.replace('@', '_').replace('.', '_')
    path = f"cache/{safe_email}.ics"
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return ics.parse(handle.read())
    except (OSError, ValueError):
        return None

def _snapshot(_memory, error=None):
    fetched = datetime.fromtimestamp(_memory["fetched_at"], tz=timezone.utc)
    return {
        "events": _memory["events"] or [],
        "fetchedAt": fetched.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": _memory["source"],
        "stale": _memory["stale"],
        "error": error,
    }


# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------

import sync_worker

import urllib.parse

class Handler(BaseHTTPRequestHandler):
    config = {}

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        query = dict(urllib.parse.parse_qsl(parsed.query))

        if path == "/api/schedule":
            self._serve_schedule(force="refresh" in query, email=query.get("email"))
        elif path == "/api/health":
            self._send_json(200, {"ok": True})
        elif path == "/api/sync/status":
            self._send_json(200, sync_worker.get_status(query.get("email")))
        else:
            self._serve_static(path)

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        if path == "/api/sync/start":
            content_length = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(content_length)
            data = json.loads(body)
            success = sync_worker.start_sync(data.get("email"), data.get("password"))
            self._send_json(200, {"success": success})
        else:
            self.send_error(404, "Not Found")

    def _serve_schedule(self, force, email):
        try:
            payload = get_schedule(self.config, force=force, email=email)
            self._send_json(200, payload)
        except Exception as exc:
            self._send_json(502, {
                "events": [],
                "error": str(exc),
                "hint": "Aucun fichier trouvé pour cet utilisateur.",
            })

    def _serve_static(self, path):
        rel = "index.html" if path == "/" else path.lstrip("/")
        target = os.path.normpath(os.path.join(PUBLIC_DIR, rel))
        if not target.startswith(PUBLIC_DIR) or not os.path.isfile(target):
            self.send_error(404, "Not Found")
            return

        ctype, _ = mimetypes.guess_type(target)
        if target.endswith(".webmanifest"):
            ctype = "application/manifest+json"
        with open(target, "rb") as handle:
            body = handle.read()

        self.send_response(200)
        self.send_header("Content-Type", ctype or "application/octet-stream")
        self.send_header("Content-Length", str(len(body)))
        # le service worker doit pouvoir se mettre a jour sans vider le cache
        if target.endswith("sw.js"):
            self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, status, payload):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        pass  # les logs utiles sont deja affiches par get_schedule


def main():
    config = load_config()
    Handler.config = config
    port = config["port"]

    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    configured = bool(config.get("ics_url") or config.get("ics_file"))

    print("Emploi du temps Auriga")
    print("  local    : http://localhost:%d" % port)
    print("  telephone: http://<ip-de-ce-pc>:%d (meme wifi)" % port)
    if not configured:
        print("  /!\\ mode demo : aucune URL ICS configuree, sample.ics est utilise.")
        print("      Renseigne 'ics_url' dans config.json pour ton vrai agenda.")
    print("Ctrl+C pour arreter.")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nArret.")
        server.server_close()


if __name__ == "__main__":
    sys.exit(main())

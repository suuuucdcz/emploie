"""Serveur de l'emploi du temps Auriga.

Trois roles :
  1. servir la PWA (dossier public/) ;
  2. exposer /api/schedule : l'agenda enregistre, converti en JSON ;
  3. piloter la synchronisation Playwright via /api/sync/*.

Lancement : `python server.py` (port 8787 par defaut, ou $PORT).
"""

import argparse
import json
import mimetypes
import os
import sys
import threading
import time
import urllib.parse
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import ics
import storage
import sync_worker

ROOT = os.path.dirname(os.path.abspath(__file__))
PUBLIC_DIR = os.path.join(ROOT, "public")
CONFIG_PATH = os.path.join(ROOT, "config.json")

DEFAULTS = {
    "port": 8787,
    "refresh_seconds": 900,
}

# Corps JSON accepte sur /api/sync/start : de quoi tenir un email et un mot de
# passe, rien de plus.
MAX_BODY_BYTES = 4096

# Un utilisateur = une entree de cache. Garde-fou memoire.
MAX_CACHED_USERS = 100

_registry_lock = threading.Lock()
_caches = {}


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

    parser = argparse.ArgumentParser(description="Emploi du temps Auriga")
    parser.add_argument("--port", type=int, help="Port d'ecoute")
    args = parser.parse_args(argv)

    if args.port:
        config["port"] = args.port

    # L'hebergeur a le dernier mot : Render impose le port via $PORT.
    hosted_port = os.environ.get("PORT") or os.environ.get("AURIGA_PORT")
    if hosted_port:
        config["port"] = int(hosted_port)

    return config


# --------------------------------------------------------------------------
# Cache des agendas
# --------------------------------------------------------------------------

def _entry(email):
    """Entree de cache de cet utilisateur, creee au besoin.

    Chaque entree porte son propre verrou : deux utilisateurs ne s'attendent
    jamais l'un l'autre pendant un aller-retour Supabase.
    """
    with _registry_lock:
        entry = _caches.get(email)
        if entry is None:
            if len(_caches) >= MAX_CACHED_USERS:
                oldest = min(_caches, key=lambda key: _caches[key]["fetched_at"])
                del _caches[oldest]
            entry = {
                "lock": threading.Lock(),
                "fetched_at": 0.0,
                "events": None,
                "source": "",
                "stale": False,
            }
            _caches[email] = entry
        return entry


def _snapshot(entry, error=None):
    fetched = datetime.fromtimestamp(entry["fetched_at"], tz=timezone.utc)
    return {
        "events": entry["events"] or [],
        "fetchedAt": fetched.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": entry["source"],
        "stale": entry["stale"],
        "error": error,
    }


def get_schedule(config, email, force=False):
    """Agenda de l'utilisateur, depuis le cache memoire ou le stockage.

    En cas d'echec on ressert la derniere version connue, marquee `stale`,
    plutot que de casser l'affichage.
    """
    ttl = config.get("refresh_seconds", DEFAULTS["refresh_seconds"])
    entry = _entry(email)

    with entry["lock"]:
        fresh_enough = (
            entry["events"] is not None
            and not force
            and (time.time() - entry["fetched_at"]) < ttl
        )
        if fresh_enough:
            return _snapshot(entry)

        try:
            text, source = storage.load_schedule(email)
        except Exception as exc:
            if entry["events"] is None:
                raise
            entry["stale"] = True
            return _snapshot(entry, error=str(exc))

        entry.update(fetched_at=time.time(), events=ics.parse(text),
                     source=source, stale=False)
        return _snapshot(entry)


# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------

class Handler(BaseHTTPRequestHandler):
    config = dict(DEFAULTS)
    server_version = "Auriga"

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        query = dict(urllib.parse.parse_qsl(parsed.query))

        if parsed.path == "/api/schedule":
            self._serve_schedule(query.get("email"), force="refresh" in query)
        elif parsed.path == "/api/health":
            self._send_json(200, {"ok": True})
        elif parsed.path == "/api/sync/status":
            self._send_json(200, sync_worker.get_status(query.get("id")))
        else:
            self._serve_static(parsed.path)

    def do_POST(self):
        if urllib.parse.urlparse(self.path).path != "/api/sync/start":
            self.send_error(404, "Not Found")
            return

        payload = self._read_json_body()
        if payload is None:
            return

        try:
            sync_id = sync_worker.start_sync(payload.get("email"),
                                             payload.get("password"))
        except sync_worker.SyncBusy as exc:
            self._send_json(429, {"success": False, "error": str(exc)})
            return
        except ValueError as exc:
            self._send_json(400, {"success": False, "error": str(exc)})
            return
        self._send_json(200, {"success": True, "syncId": sync_id})

    # -- helpers ----------------------------------------------------------

    def _read_json_body(self):
        """Corps JSON de la requete, ou None (la reponse d'erreur est envoyee)."""
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = -1
        if length <= 0 or length > MAX_BODY_BYTES:
            self._send_json(400, {"success": False, "error": "Requete invalide."})
            return None
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            self._send_json(400, {"success": False, "error": "JSON invalide."})
            return None

    def _serve_schedule(self, email, force):
        try:
            self._send_json(200, get_schedule(self.config, email, force=force))
        except (storage.NoScheduleError, ValueError) as exc:
            self._send_json(404, {
                "events": [],
                "error": str(exc),
                "hint": "Lance une synchronisation pour recuperer ton planning.",
            })
        except Exception as exc:
            self._send_json(502, {"events": [], "error": str(exc)})

    def _serve_static(self, path):
        rel = "index.html" if path == "/" else urllib.parse.unquote(path).lstrip("/")
        target = os.path.normpath(os.path.join(PUBLIC_DIR, rel))
        if not target.startswith(PUBLIC_DIR + os.sep) or not os.path.isfile(target):
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
        pass  # les logs utiles viennent de sync_worker et storage


def main():
    config = load_config()
    Handler.config = config
    port = config["port"]

    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)

    print("Emploi du temps Auriga")
    print("  local     : http://localhost:%d" % port)
    print("  telephone : http://<ip-de-ce-pc>:%d (meme wifi)" % port)
    if not storage.supabase_config()[0]:
        print("  stockage  : cache disque (%s)" % storage.CACHE_DIR)
        print("              definis SUPABASE_URL et SUPABASE_KEY pour Supabase.")
    print("Ctrl+C pour arreter.")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nArret.")
    finally:
        server.server_close()


if __name__ == "__main__":
    sys.exit(main())

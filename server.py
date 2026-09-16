"""Serveur de l'emploi du temps Auriga / Edusign.

Trois roles :
  1. Servir la PWA securisee (dossier public/) ;
  2. Exposer /api/schedule : l'agenda enregistre, converti en JSON ;
  3. Piloter la synchronisation Edusign et les sessions via /api/sync/* et /api/session/*.

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

# Garde-fous requetes et memoire
MAX_BODY_BYTES = 4096
MAX_CACHED_USERS = 100

# Limiteur de requetes pour eviter le flood de synchronisation
_rate_lock = threading.Lock()
_rate_limits = {}
RATE_LIMIT_WINDOW = 60  # secondes
RATE_LIMIT_MAX = 6      # max tentatives de sync par IP par minute

# Surcharge MIME pour Windows (ou .js est parfois declare en text/plain)
MIME_OVERRIDES = {
    ".js": "text/javascript",
    ".mjs": "text/javascript",
    ".css": "text/css",
    ".html": "text/html; charset=utf-8",
    ".json": "application/json",
    ".webmanifest": "application/manifest+json",
    ".svg": "image/svg+xml",
}

ASSET_MAX_AGE = 86400
REVALIDATE_SUFFIXES = (".html", ".js", ".css", ".webmanifest", ".json")

_registry_lock = threading.Lock()
_caches = {}


def _check_rate_limit(ip):
    """Verifie que l'IP ne depasse pas le quota de requetes par fenetre."""
    now = time.time()
    with _rate_lock:
        timestamps = _rate_limits.get(ip, [])
        timestamps = [t for t in timestamps if now - t < RATE_LIMIT_WINDOW]
        if len(timestamps) >= RATE_LIMIT_MAX:
            _rate_limits[ip] = timestamps
            return False
        timestamps.append(now)
        _rate_limits[ip] = timestamps
        return True


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

    parser = argparse.ArgumentParser(description="Emploi du temps Auriga / Edusign")
    parser.add_argument("--port", type=int, help="Port d'ecoute")
    args = parser.parse_args(argv)

    if args.port:
        config["port"] = args.port

    hosted_port = os.environ.get("PORT") or os.environ.get("AURIGA_PORT")
    if hosted_port:
        config["port"] = int(hosted_port)

    return config


# --------------------------------------------------------------------------
# Cache memoire des agendas
# --------------------------------------------------------------------------

def _entry(clean_email):
    with _registry_lock:
        entry = _caches.get(clean_email)
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
            _caches[clean_email] = entry
        return entry


def _snapshot(entry, error=None, email=None):
    fetched = datetime.fromtimestamp(entry["fetched_at"], tz=timezone.utc)
    return {
        "events": entry["events"] or [],
        "fetchedAt": fetched.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": entry["source"],
        "stale": entry["stale"],
        "hasSession": storage.has_session(email) if email else False,
        "error": error,
    }


def get_schedule(config, email, force=False):
    clean_email = storage.validate_and_normalize_email(email)
    ttl = config.get("refresh_seconds", DEFAULTS["refresh_seconds"])
    entry = _entry(clean_email)

    with entry["lock"]:
        fresh_enough = (
            entry["events"] is not None
            and not force
            and (time.time() - entry["fetched_at"]) < ttl
        )
        if fresh_enough:
            return _snapshot(entry, email=clean_email)

        try:
            text, source = storage.load_schedule(clean_email)
        except Exception as exc:
            if entry["events"] is None:
                raise
            entry["stale"] = True
            return _snapshot(entry, error=str(exc), email=clean_email)

        entry.update(
            fetched_at=time.time(),
            events=ics.parse(text),
            source=source,
            stale=False,
        )
        return _snapshot(entry, email=clean_email)


# --------------------------------------------------------------------------
# HTTP Handler
# --------------------------------------------------------------------------

class Handler(BaseHTTPRequestHandler):
    config = dict(DEFAULTS)
    server_version = "EDT-Edusign"

    def _send_security_headers(self):
        """En-tetes HTTP de durcissement et securite."""
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "strict-origin-when-cross-origin")
        self.send_header("X-XSS-Protection", "1; mode=block")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self'",
        )

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        query = dict(urllib.parse.parse_qsl(parsed.query))

        if parsed.path == "/api/schedule":
            self._serve_schedule(query.get("email"), force="refresh" in query)
        elif parsed.path == "/api/absences":
            self._serve_absences(query.get("email"))
        elif parsed.path == "/api/health":
            self._send_json(200, {"ok": True})
        elif parsed.path == "/api/sync/status":
            self._send_json(200, sync_worker.get_status(query.get("id")))
        else:
            self._serve_static(parsed.path)

    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path

        if path == "/api/sync/start":
            client_ip = self.client_address[0] if self.client_address else "unknown"
            if not _check_rate_limit(client_ip):
                self._send_json(429, {"success": False, "error": "Trop de requetes. Veuillez patienter 1 minute."})
                return

            payload = self._read_json_body()
            if payload is None:
                return

            try:
                sync_id = sync_worker.start_sync(payload.get("email"), payload.get("password"))
            except sync_worker.SyncBusy as exc:
                self._send_json(429, {"success": False, "error": str(exc)})
                return
            except ValueError as exc:
                self._send_json(400, {"success": False, "error": str(exc)})
                return
            self._send_json(200, {"success": True, "syncId": sync_id})

        elif path == "/api/session/clear":
            payload = self._read_json_body()
            if payload is None:
                return
            email = payload.get("email")
            if email:
                try:
                    storage.clear_session(email)
                except Exception:
                    pass
            self._send_json(200, {"success": True})

        else:
            self.send_error(404, "Not Found")

    # -- helpers ----------------------------------------------------------

    def _read_json_body(self):
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = -1
        if length <= 0 or length > MAX_BODY_BYTES:
            self._send_json(400, {"success": False, "error": "Requete invalide ou trop volumineuse."})
            return None
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            self._send_json(400, {"success": False, "error": "Corps JSON invalide."})
            return None

    def _serve_schedule(self, email, force):
        if not email:
            self._send_json(400, {"events": [], "error": "Adresse email requise."})
            return
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

    def _serve_absences(self, email):
        if not email:
            self._send_json(400, {"success": False, "error": "Email requis"})
            return
        try:
            clean_email = storage.validate_and_normalize_email(email)
        except ValueError as exc:
            self._send_json(400, {"success": False, "error": str(exc)})
            return

        # 1. Tenter de charger le cache officiel sauvegarde
        cached = storage.load_absences(clean_email)

        # 2. Obtenir les cours pour calculer / enrichir les statistiques
        try:
            snapshot = get_schedule(self.config, clean_email)
            events = snapshot.get("events", [])
        except Exception:
            events = []

        now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        past_events = [e for e in events if e.get("end", "") <= now_iso]

        presences = sum(1 for e in past_events if e.get("attendance") == "present")
        absences_list = [
            e for e in past_events
            if e.get("attendance") == "absent" or (e.get("attendance") != "present" and not e.get("canSign"))
        ]
        total_past = len(past_events)
        computed_ratio = round((presences / total_past * 100), 1) if total_past > 0 else 100.0

        stats = {
            "totalCourses": total_past,
            "presences": presences,
            "presenceRatio": computed_ratio,
            "absences": len(absences_list),
            "justified": sum(1 for e in absences_list if e.get("isJustified")),
            "delays": 0,
            "pending": 0,
            "absencesList": [
                {
                    "title": e.get("title") or e.get("rawTitle"),
                    "start": e.get("start"),
                    "end": e.get("end"),
                    "location": e.get("location"),
                    "isJustified": e.get("isJustified", False),
                }
                for e in absences_list
            ],
        }

        # Fusion si l'API Edusign a renvoye des donnees directes
        if isinstance(cached, dict):
            c_stats = cached.get("statistics") if isinstance(cached.get("statistics"), dict) else cached
            if isinstance(c_stats, dict):
                for k in ("totalCourses", "presences", "presenceRatio", "absences", "justified", "delays", "pending"):
                    if c_stats.get(k) is not None:
                        stats[k] = c_stats[k]
            if "absences" in cached and isinstance(cached["absences"], list):
                stats["absencesList"] = cached["absences"]

        self._send_json(200, {"success": True, "statistics": stats})

    def _serve_static(self, path):
        rel = "index.html" if path == "/" else urllib.parse.unquote(path).lstrip("/")
        target = os.path.normpath(os.path.join(PUBLIC_DIR, rel))
        abs_public = os.path.abspath(PUBLIC_DIR)
        abs_target = os.path.abspath(target)

        try:
            if os.path.commonpath([abs_public, abs_target]) != abs_public or not os.path.isfile(abs_target):
                self.send_error(404, "Not Found")
                return
        except (ValueError, OSError):
            self.send_error(404, "Not Found")
            return

        stat = os.stat(abs_target)
        etag = '"%x-%x"' % (int(stat.st_mtime), stat.st_size)
        extension = os.path.splitext(abs_target)[1].lower()

        if extension in REVALIDATE_SUFFIXES:
            cache_control = "no-cache"
        else:
            cache_control = "public, max-age=%d" % ASSET_MAX_AGE

        if self.headers.get("If-None-Match") == etag:
            self.send_response(304)
            self.send_header("ETag", etag)
            self.send_header("Cache-Control", cache_control)
            self._send_security_headers()
            self.end_headers()
            return

        ctype = MIME_OVERRIDES.get(extension) or mimetypes.guess_type(abs_target)[0]
        with open(abs_target, "rb") as handle:
            body = handle.read()

        self.send_response(200)
        self.send_header("Content-Type", ctype or "application/octet-stream")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("ETag", etag)
        self.send_header("Cache-Control", cache_control)
        self.send_header("Last-Modified", self.date_time_string(stat.st_mtime))
        self._send_security_headers()
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, status, payload):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self._send_security_headers()
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        pass


def main():
    config = load_config()
    Handler.config = config
    port = config["port"]

    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)

    print("Emploi du temps Auriga / Edusign")
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

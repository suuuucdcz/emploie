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

# Limiteur de requetes pour eviter le flood de synchronisation et d'API
_rate_lock = threading.Lock()
_rate_limits = {}
RATE_LIMIT_SYNC_WINDOW = 60    # secondes
RATE_LIMIT_SYNC_MAX = 6        # max tentatives de sync par IP par minute
RATE_LIMIT_API_WINDOW = 60     # secondes
RATE_LIMIT_API_MAX = 60        # max requetes GET API par IP par minute

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


def _check_rate_limit(ip, scope="sync", max_requests=RATE_LIMIT_SYNC_MAX, window=RATE_LIMIT_SYNC_WINDOW):
    """Verifie que l'IP ne depasse pas le quota de requetes par fenetre et par scope."""
    now = time.time()
    key = (ip, scope)
    with _rate_lock:
        if len(_rate_limits) > 500:
            cutoff = now - max(RATE_LIMIT_SYNC_WINDOW, RATE_LIMIT_API_WINDOW)
            expired = [k for k, v in _rate_limits.items() if not v or v[-1] < cutoff]
            for k in expired:
                del _rate_limits[k]
        timestamps = _rate_limits.get(key, [])
        timestamps = [t for t in timestamps if now - t < window]
        if len(timestamps) >= max_requests:
            _rate_limits[key] = timestamps
            return False
        timestamps.append(now)
        _rate_limits[key] = timestamps
        return True


def _is_past_event(evt, now_dt):
    """Verifie si un cours est passe en comparant proprement les horodatages ISO 8601."""
    end_str = evt.get("end")
    if not isinstance(end_str, str) or not end_str:
        return False
    try:
        clean = end_str.replace("Z", "+00:00")
        dt = datetime.fromisoformat(clean)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt <= now_dt
    except Exception:
        return end_str <= now_dt.strftime("%Y-%m-%dT%H:%M:%SZ")


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

    if args.port is not None:
        config["port"] = args.port

    hosted_port = os.environ.get("PORT") or os.environ.get("AURIGA_PORT")
    if hosted_port:
        config["port"] = hosted_port

    try:
        port = int(config["port"])
    except (TypeError, ValueError):
        raise ValueError("Le port doit etre un entier entre 1 et 65535.")
    if not 1 <= port <= 65535:
        raise ValueError("Le port doit etre un entier entre 1 et 65535.")
    config["port"] = port

    try:
        refresh_seconds = int(config.get("refresh_seconds", DEFAULTS["refresh_seconds"]))
    except (TypeError, ValueError):
        raise ValueError("refresh_seconds doit etre un entier positif.")
    if refresh_seconds <= 0:
        raise ValueError("refresh_seconds doit etre un entier positif.")
    config["refresh_seconds"] = refresh_seconds

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
            "default-src 'self'; base-uri 'none'; object-src 'none'; frame-ancestors 'none'; "
            "form-action 'self'; connect-src 'self'; img-src 'self' data:; "
            "style-src 'self' 'unsafe-inline'; script-src 'self'",
        )
        self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=()")

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        query = dict(urllib.parse.parse_qsl(parsed.query))
        client_ip = self.client_address[0] if self.client_address else "unknown"

        if parsed.path in ("/api/schedule", "/api/absences"):
            if not _check_rate_limit(client_ip, scope="api", max_requests=RATE_LIMIT_API_MAX, window=RATE_LIMIT_API_WINDOW):
                self._send_json(429, {"success": False, "error": "Trop de requetes. Veuillez patienter un instant."})
                return

        if parsed.path == "/api/schedule":
            self._serve_schedule(query.get("email"), force="refresh" in query)
        elif parsed.path == "/api/absences":
            self._serve_absences(query.get("email"))
        elif parsed.path == "/api/health":
            self._send_json(200, {"ok": True})
        elif parsed.path == "/api/sync/status":
            self._send_json(200, sync_worker.get_status(query.get("id"), self._device_id()))
        else:
            self._serve_static(parsed.path)

    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path

        if path == "/api/sync/start":
            client_ip = self.client_address[0] if self.client_address else "unknown"
            if not _check_rate_limit(client_ip, scope="sync", max_requests=RATE_LIMIT_SYNC_MAX, window=RATE_LIMIT_SYNC_WINDOW):
                self._send_json(429, {"success": False, "error": "Trop de requetes. Veuillez patienter 1 minute."})
                return

            payload = self._read_json_body()
            if payload is None:
                return

            try:
                sync_id = sync_worker.start_sync(
                    payload.get("email"), payload.get("password"), payload.get("deviceId")
                )
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
            if not email:
                self._send_json(400, {"success": False, "error": "Adresse email requise."})
                return
            try:
                clean_email = storage.validate_and_normalize_email(email)
            except ValueError as exc:
                self._send_json(400, {"success": False, "error": str(exc)})
                return
            if not storage.session_matches_device(clean_email, self._device_id()):
                self._send_json(401, {"success": False, "error": "Connexion requise pour cet appareil."})
                return
            storage.clear_session(clean_email)
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
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            self._send_json(400, {"success": False, "error": "Corps JSON invalide."})
            return None
        if not isinstance(payload, dict):
            self._send_json(400, {"success": False, "error": "Le corps JSON doit etre un objet."})
            return None
        return payload

    def _device_id(self):
        """Identifiant aleatoire du navigateur, transporte hors de l'URL."""
        value = self.headers.get("X-Auriga-Device-Id")
        try:
            return storage.validate_device_id(value)
        except ValueError:
            return None

    def _serve_schedule(self, email, force):
        if not email:
            self._send_json(400, {"events": [], "error": "Adresse email requise."})
            return
        try:
            clean_email = storage.validate_and_normalize_email(email)
        except (storage.NoScheduleError, ValueError) as exc:
            self._send_json(404, {
                "events": [],
                "error": str(exc),
                "hint": "Lance une synchronisation pour recuperer ton planning.",
            })
            return
        if not storage.session_matches_device(clean_email, self._device_id()):
            self._send_json(401, {"events": [], "error": "Connexion requise pour cet appareil."})
            return
        try:
            self._send_json(200, get_schedule(self.config, clean_email, force=force))
        except Exception as exc:
            print("[server] lecture planning impossible : %s" % exc, file=sys.stderr)
            self._send_json(502, {"events": [], "error": "Planning temporairement indisponible."})

    def _serve_absences(self, email):
        if not email:
            self._send_json(400, {"success": False, "error": "Email requis"})
            return
        try:
            clean_email = storage.validate_and_normalize_email(email)
        except ValueError as exc:
            self._send_json(400, {"success": False, "error": str(exc)})
            return
        if not storage.session_matches_device(clean_email, self._device_id()):
            self._send_json(401, {"success": False, "error": "Connexion requise pour cet appareil."})
            return

        # 1. Tenter de charger le cache officiel sauvegarde
        cached = storage.load_absences(clean_email)

        # 2. Obtenir les cours pour calculer / enrichir les statistiques
        try:
            snapshot = get_schedule(self.config, clean_email)
            events = snapshot.get("events", [])
        except Exception:
            events = []

        now_dt = datetime.now(timezone.utc)
        past_events = [e for e in events if _is_past_event(e, now_dt)]

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

        # L'API renvoie parfois un bilan global qui inclut des creneaux a
        # venir. Les compteurs lies a l'assiduite restent donc derives du
        # planning filtre ci-dessus ; seules les donnees non calculables localement
        # (retards et elements en attente) sont reprises du cache officiel.
        if isinstance(cached, dict):
            c_stats = cached.get("statistics") if isinstance(cached.get("statistics"), dict) else cached
            if isinstance(c_stats, dict):
                for k in ("delays", "pending"):
                    if c_stats.get(k) is not None:
                        stats[k] = c_stats[k]

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
        self.send_header("Vary", "X-Auriga-Device-Id")
        self._send_security_headers()
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        pass


def main():
    try:
        config = load_config()
    except ValueError as exc:
        print("[config] %s" % exc, file=sys.stderr)
        return 2
    Handler.config = config
    port = config["port"]

    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    server.daemon_threads = True

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

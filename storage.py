"""Stockage des agendas et sessions Edusign : Supabase si configure, sinon cache local.

Un seul endroit decide ou vivent les fichiers ICS et les jetons de session,
comment une adresse email est validee et normalisee, et comment on communique avec Supabase.
"""

import hashlib
import hmac
import json
import os
import re
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import uuid

import envfile

# Chargement du fichier .env local au demarrage (silencieux si absent)
envfile.load()

ROOT = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.path.join(ROOT, "cache")

_EMAIL_RE = re.compile(r"^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+(?:\.[a-zA-Z0-9-]+)+$")
_LEGACY_UNSAFE = re.compile(r"[^a-z0-9._-]+")


class NoScheduleError(Exception):
    """Aucun agenda connu pour cet utilisateur."""


def supabase_config():
    """(url, key) si Supabase est configure, sinon (None, None)."""
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_KEY")
    return (url.rstrip("/"), key) if url and key else (None, None)


def validate_and_normalize_email(email):
    """Valide et normalise une adresse email (minuscules, sans espaces)."""
    if not email or not isinstance(email, str):
        raise ValueError("Adresse email requise.")
    cleaned = email.strip().lower()
    if len(cleaned) > 254 or not _EMAIL_RE.match(cleaned):
        raise ValueError("Format d'adresse email invalide.")
    return cleaned


def cache_key(email):
    """Adresse email -> identifiant de fichier stable, non revelateur et sans collision."""
    cleaned = validate_and_normalize_email(email)
    return hashlib.sha256(cleaned.encode("utf-8")).hexdigest()


def _legacy_cache_key(email):
    """Ancien nom de fichier, conserve uniquement pour lire les caches existants."""
    return _LEGACY_UNSAFE.sub("_", validate_and_normalize_email(email))


def cache_path(email):
    return os.path.join(CACHE_DIR, "%s.ics" % cache_key(email))


def session_path(email):
    return os.path.join(CACHE_DIR, "%s.session.json" % cache_key(email))


def absences_path(email):
    return os.path.join(CACHE_DIR, "%s.absences.json" % cache_key(email))


def _legacy_paths(email, suffix):
    return os.path.join(CACHE_DIR, "%s%s" % (_legacy_cache_key(email), suffix))


def validate_device_id(device_id):
    """Valide un identifiant de navigateur aleatoire au format UUID."""
    if not isinstance(device_id, str):
        raise ValueError("Identifiant d'appareil requis.")
    try:
        return str(uuid.UUID(device_id))
    except (ValueError, AttributeError, TypeError):
        raise ValueError("Identifiant d'appareil invalide.")


def session_matches_device_id(expected_device_id, device_id):
    """Compare deux identifiants d'appareil sans fuite temporelle exploitable."""
    try:
        return hmac.compare_digest(str(expected_device_id), validate_device_id(device_id))
    except (TypeError, ValueError):
        return False


def _atomic_write(target_path, data, mode="w", encoding="utf-8", secure_permissions=False):
    """Ecriture atomique via un fichier temporaire pour eviter toute corruption."""
    os.makedirs(os.path.dirname(target_path), exist_ok=True)
    dir_name = os.path.dirname(target_path)
    fd, tmp_path = tempfile.mkstemp(dir=dir_name, prefix=".tmp_")
    try:
        with open(fd, mode, encoding=encoding) as handle:
            handle.write(data)
        if secure_permissions and hasattr(os, "chmod"):
            try:
                os.chmod(tmp_path, 0o600)
            except OSError:
                pass
        os.replace(tmp_path, target_path)
    except Exception:
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass
        raise


def _supabase_headers(key, write=False):
    headers = {
        "apikey": key,
        "Authorization": "Bearer %s" % key,
        "Accept": "application/json",
    }
    if write:
        headers["Content-Type"] = "application/json"
        headers["Prefer"] = "resolution=merge-duplicates"
    return headers


def _supabase_load(url, key, email):
    query = urllib.parse.urlencode({
        "email": "eq.%s" % email,
        "select": "ics_content",
    })
    req = urllib.request.Request("%s/rest/v1/schedules?%s" % (url, query),
                                 headers=_supabase_headers(key))
    with urllib.request.urlopen(req, timeout=20) as response:
        rows = json.loads(response.read().decode("utf-8"))
    return rows[0]["ics_content"] if rows else None


def _supabase_save(url, key, email, ics_content, refresh_token=None, device_id=None):
    payload = {"email": email, "ics_content": ics_content}
    if refresh_token and device_id:
        payload["refresh_token"] = refresh_token
        payload["device_id"] = device_id

    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request("%s/rest/v1/schedules" % url, data=body,
                                 headers=_supabase_headers(key, write=True),
                                 method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30):
            pass
    except urllib.error.HTTPError as exc:
        # Si les colonnes refresh_token/device_id n'existent pas encore dans Supabase
        if payload.get("refresh_token") and exc.code in (400, 404):
            fallback_body = json.dumps({"email": email, "ics_content": ics_content}).encode("utf-8")
            fallback_req = urllib.request.Request("%s/rest/v1/schedules" % url, data=fallback_body,
                                                 headers=_supabase_headers(key, write=True),
                                                 method="POST")
            with urllib.request.urlopen(fallback_req, timeout=30):
                pass
        else:
            raise


def load_schedule(email):
    """Renvoie (texte_ics, description_de_la_source).
    
    Leve NoScheduleError si l'agenda n'a jamais ete synchronise.
    """
    clean_email = validate_and_normalize_email(email)

    url, api_key = supabase_config()
    if url:
        try:
            content = _supabase_load(url, api_key, clean_email)
            if content:
                return content, "base de donnees Supabase"
        except Exception as exc:
            print("[storage] lecture Supabase impossible (%s), repli local" % exc)

    for path in (cache_path(clean_email), _legacy_paths(clean_email, ".ics")):
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8", errors="replace") as handle:
                return handle.read(), "cache local"

    raise NoScheduleError("Aucun agenda pour cet utilisateur. Veuillez synchroniser.")


def save_schedule(email, ics_content, refresh_token=None, device_id=None):
    """Enregistre l'agenda et optionnellement les jetons de session (Option B)."""
    clean_email = validate_and_normalize_email(email)

    # Sauvegarde locale atomique de secours
    _atomic_write(cache_path(clean_email), ics_content, secure_permissions=True)

    if refresh_token and device_id:
        try:
            session_data = json.dumps({"refresh_token": refresh_token, "device_id": device_id})
            _atomic_write(session_path(clean_email), session_data, secure_permissions=True)
        except Exception as exc:
            print("[storage] erreur ecriture session locale : %s" % exc)

    url, api_key = supabase_config()
    if url:
        try:
            _supabase_save(url, api_key, clean_email, ics_content, refresh_token, device_id)
            return "Supabase"
        except Exception as exc:
            print("[storage] ecriture Supabase impossible (%s), repli local" % exc)

    return "cache local"


def get_session(email):
    """Recupere (refresh_token, device_id) si une session existe pour cet utilisateur."""
    try:
        clean_email = validate_and_normalize_email(email)
    except ValueError:
        return None, None

    url, api_key = supabase_config()
    if url:
        try:
            query = urllib.parse.urlencode({
                "email": "eq.%s" % clean_email,
                "select": "refresh_token,device_id",
            })
            req = urllib.request.Request("%s/rest/v1/schedules?%s" % (url, query),
                                         headers=_supabase_headers(api_key))
            with urllib.request.urlopen(req, timeout=15) as resp:
                rows = json.loads(resp.read().decode("utf-8"))
                if rows:
                    rt = rows[0].get("refresh_token")
                    did = rows[0].get("device_id")
                    if rt and did:
                        return rt, did
        except Exception:
            # Colonnes pas encore creees ou erreur reseau : repli local
            pass

    for path in (session_path(clean_email), _legacy_paths(clean_email, ".session.json")):
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as handle:
                    data = json.load(handle)
                    rt = data.get("refresh_token")
                    did = data.get("device_id")
                    if rt and did:
                        return rt, did
            except Exception:
                pass

    return None, None


def has_session(email):
    """Vrai si une session est memorisee pour cet email."""
    try:
        rt, did = get_session(email)
        return bool(rt and did)
    except Exception:
        return False


def session_matches_device(email, device_id):
    """Verifie que l'appareil qui appelle l'API possede la session de cet email."""
    try:
        _, saved_device_id = get_session(email)
        return bool(saved_device_id and session_matches_device_id(saved_device_id, device_id))
    except (TypeError, ValueError):
        return False


def clear_session(email):
    """Supprime la session memorisee (deconnexion)."""
    try:
        clean_email = validate_and_normalize_email(email)
        for path in (
            session_path(clean_email),
            _legacy_paths(clean_email, ".session.json"),
            absences_path(clean_email),
            _legacy_paths(clean_email, ".absences.json"),
        ):
            if os.path.exists(path):
                os.remove(path)

        url, api_key = supabase_config()
        if url:
            body = json.dumps({"refresh_token": None, "device_id": None}).encode("utf-8")
            query = urllib.parse.urlencode({"email": "eq.%s" % clean_email})
            req = urllib.request.Request("%s/rest/v1/schedules?%s" % (url, query),
                                         data=body,
                                         headers=_supabase_headers(api_key, write=True),
                                         method="PATCH")
            with urllib.request.urlopen(req, timeout=15):
                pass
    except Exception as exc:
        print("[storage] erreur suppression session : %s" % exc)


def save_absences(email, data):
    """Enregistre les statistiques et le bilan d'absences en cache."""
    try:
        clean_email = validate_and_normalize_email(email)
        raw = json.dumps(data, ensure_ascii=False)
        _atomic_write(absences_path(clean_email), raw, secure_permissions=True)
    except Exception as exc:
        print("[storage] erreur sauvegarde absences : %s" % exc)


def load_absences(email):
    """Charge le bilan d'absences depuis le cache, ou None."""
    try:
        clean_email = validate_and_normalize_email(email)
        for path in (absences_path(clean_email), _legacy_paths(clean_email, ".absences.json")):
            if os.path.exists(path):
                with open(path, "r", encoding="utf-8") as handle:
                    return json.load(handle)
    except Exception:
        pass
    return None

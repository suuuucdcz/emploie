"""Stockage des agendas : Supabase si configure, sinon cache disque.

Un seul endroit decide ou vit un ICS, comment une adresse email devient un nom
de fichier, et comment on parle a Supabase. server.py lit, sync_worker.py ecrit.
"""

import json
import os
import re
import urllib.parse
import urllib.request

import envfile

# Tout passe par ce module pour lire ou ecrire un agenda : c'est donc ici que
# le .env local doit etre charge, avant la premiere lecture d'os.environ.
envfile.load()

ROOT = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.path.join(ROOT, "cache")

_UNSAFE = re.compile(r"[^a-z0-9._-]+")


class NoScheduleError(Exception):
    """Aucun agenda connu pour cet utilisateur."""


def supabase_config():
    """(url, key) si Supabase est configure, sinon (None, None)."""
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_KEY")
    return (url, key) if url and key else (None, None)


def cache_key(email):
    """Adresse email -> identifiant de fichier sur, sans separateur de chemin."""
    if not email or "@" not in email:
        raise ValueError("Adresse email invalide")
    return _UNSAFE.sub("_", email.strip().lower())


def cache_path(email):
    return os.path.join(CACHE_DIR, "%s.ics" % cache_key(email))


def _supabase_headers(key, write=False):
    headers = {"apikey": key, "Authorization": "Bearer %s" % key}
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


def _supabase_save(url, key, email, ics_content):
    body = json.dumps({"email": email, "ics_content": ics_content}).encode("utf-8")
    req = urllib.request.Request("%s/rest/v1/schedules" % url, data=body,
                                 headers=_supabase_headers(key, write=True),
                                 method="POST")
    with urllib.request.urlopen(req, timeout=30):
        pass


def load_schedule(email):
    """Renvoie (texte_ics, description_de_la_source).

    Leve NoScheduleError si l'utilisateur n'a jamais synchronise.
    """
    key = cache_key(email)  # valide l'email avant tout appel reseau

    url, api_key = supabase_config()
    if url:
        try:
            content = _supabase_load(url, api_key, email)
            if content:
                return content, "base de donnees Supabase"
        except Exception as exc:
            print("[storage] lecture Supabase impossible (%s), repli local" % exc)

    path = cache_path(email)
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            return handle.read(), "cache local (%s)" % key

    raise NoScheduleError("Aucun agenda pour cet utilisateur. Veuillez synchroniser.")


def save_schedule(email, ics_content):
    """Enregistre l'ICS. Renvoie la description de la destination utilisee."""
    cache_key(email)

    url, api_key = supabase_config()
    if url:
        try:
            _supabase_save(url, api_key, email, ics_content)
            return "Supabase"
        except Exception as exc:
            print("[storage] ecriture Supabase impossible (%s), repli local" % exc)

    os.makedirs(CACHE_DIR, exist_ok=True)
    with open(cache_path(email), "w", encoding="utf-8", newline="") as handle:
        handle.write(ics_content)
    return "cache local"


def cached_mtime(email):
    """Date de derniere ecriture du cache disque, ou None."""
    try:
        return os.path.getmtime(cache_path(email))
    except (OSError, ValueError):
        return None

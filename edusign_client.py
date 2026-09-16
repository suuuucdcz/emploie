"""Connecteur API Edusign - Synchronisation legere avec session persistante (Option B).

Supporte :
  1. Connexion par identifiants (POST /student/account/getByCredentials)
  2. Renouvellement silencieux sans mot de passe (POST /student/account/auth/refresh)
  3. Telechargement du planning (GET /student/planning)
  4. Resolution des professeurs (POST /student/professors)
  5. Conversion ICS RFC 5545 et sauvegarde
"""

import json
import urllib.error
import urllib.request
import uuid
from datetime import date, datetime, timezone

import ics_builder
import storage

API_BASE = "https://api.edusign.fr/student"
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)


class EdusignError(Exception):
    """Erreur lors d'un appel a l'API Edusign."""


def _http_request(url, method="GET", data=None, headers=None, timeout=25):
    """Effectue une requete HTTP JSON vers l'API Edusign."""
    req_headers = {
        "Accept": "application/json, text/plain, */*",
        "User-Agent": DEFAULT_USER_AGENT,
        "Origin": "https://edusign.app",
        "Referer": "https://edusign.app/",
    }
    if headers:
        req_headers.update(headers)

    body = None
    if data is not None:
        body = json.dumps(data).encode("utf-8")
        req_headers["Content-Type"] = "application/json"

    req = urllib.request.Request(url, data=body, headers=req_headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            err_json = json.loads(exc.read().decode("utf-8"))
            msg = err_json.get("message") or err_json.get("errorCode") or str(exc)
        except Exception:
            msg = str(exc)
        raise EdusignError(msg)
    except Exception as exc:
        raise EdusignError(f"Erreur reseau vers Edusign : {exc}")


def login(email, password, device_id=None):
    """Connexion classique Edusign. Renvoie (access_token, refresh_token, device_id, user_info)."""
    email = storage.validate_and_normalize_email(email)
    device_id = device_id or str(uuid.uuid4())
    payload = {
        "EMAIL": email,
        "PASSWORD": password,
        "LANGUAGE": "fr",
    }
    headers = {"x-device-id": device_id}
    res = _http_request(f"{API_BASE}/account/getByCredentials", method="POST", data=payload, headers=headers)
    if res.get("status") != "success" or "result" not in res:
        raise EdusignError(res.get("message") or "Identifiants Edusign incorrects.")

    data = res["result"]
    token = data.get("ACCESS_TOKEN") or data.get("TOKEN")
    refresh_token = data.get("REFRESH_TOKEN")
    user = {
        "id": data.get("ID"),
        "email": data.get("EMAIL"),
        "firstName": data.get("FIRSTNAME"),
        "lastName": data.get("LASTNAME"),
        "schoolId": data.get("SCHOOL_ID"),
    }
    return token, refresh_token, device_id, user


def refresh_tokens(refresh_token, device_id):
    """Renouvelle la session a partir du refresh token. Renvoie (access_token, new_refresh_token)."""
    headers = {"x-device-id": device_id}
    payload = {"refresh_token": refresh_token}
    res = _http_request(f"{API_BASE}/account/auth/refresh", method="POST", data=payload, headers=headers)
    if res.get("status") != "success" or "result" not in res:
        raise EdusignError("Session expiree ou invalide.")

    data = res["result"]
    new_access_token = data.get("access_token")
    new_refresh_token = data.get("refresh_token") or refresh_token
    return new_access_token, new_refresh_token


def fetch_planning(token, device_id, start_iso, end_iso):
    """Recupere la liste des cours entre start_iso et end_iso."""
    headers = {
        "Authorization": f"Bearer {token}",
        "x-device-id": device_id,
    }
    url = f"{API_BASE}/planning?start={start_iso}&end={end_iso}"
    res = _http_request(url, method="GET", headers=headers, timeout=30)
    return res.get("result", [])


def fetch_professors(token, device_id, professor_ids):
    """Associe chaque ID de professeur a son nom complet."""
    if not professor_ids:
        return {}
    headers = {
        "Authorization": f"Bearer {token}",
        "x-device-id": device_id,
    }
    res = _http_request(
        f"{API_BASE}/professors",
        method="POST",
        data={"ids": list(set(professor_ids))},
        headers=headers,
    )
    result = {}
    for p in res.get("result", []):
        name = f"{p.get('FIRSTNAME', '')} {p.get('LASTNAME', '')}".strip()
        if name:
            result[p.get("ID")] = name
    return result


def edusign_to_events(courses, professors):
    """Convertit la reponse brute Edusign en evenements normalises pour l'ICS."""
    events = []
    for c in courses:
        cid = c.get("ID")
        start = c.get("START")
        end = c.get("END")
        if not cid or not start or not end:
            continue

        name = (c.get("NAME") or "Cours").strip()
        prof_id = c.get("PROFESSOR")
        prof_name = professors.get(prof_id, "")

        description_parts = []
        if prof_name:
            description_parts.append(f"Enseignant : {prof_name}")
        if c.get("DESCRIPTION"):
            description_parts.append(c["DESCRIPTION"].strip())

        events.append({
            "uid": f"{cid}@edusign",
            "start": start,
            "end": end,
            "summary": name,
            "description": "\n".join(description_parts),
            "location": (c.get("CLASSROOM") or "").strip(),
        })
    return events


def default_academic_dates():
    """Periode par defaut : du 1er aout precedent au 1er aout de l'annee suivante."""
    today = date.today()
    start_year = today.year if today.month >= 8 else today.year - 1
    start_dt = datetime(start_year, 8, 1, 0, 0, 0, tzinfo=timezone.utc)
    end_dt = datetime(start_year + 1, 8, 1, 0, 0, 0, tzinfo=timezone.utc)
    return start_dt.strftime("%Y-%m-%dT%H:%M:%S.000Z"), end_dt.strftime("%Y-%m-%dT%H:%M:%S.000Z")


def sync_schedule(email, password=None, refresh_token=None, device_id=None):
    """Synchronise l'emploi du temps.
    
    1. Si une session (refresh_token + device_id) existe, tente un renouvellement silencieux.
    2. Sinon, ou en cas d'echec de renouvellement, utilise le mot de passe s'il est fourni.
    3. Sauvegarde le planning et les jetons de session mis a jour.
    """
    email = storage.validate_and_normalize_email(email)
    token = None
    new_refresh_token = None
    user = {}
    auth_method = "token"

    # Verifier si une session existe deja
    if not refresh_token or not device_id:
        cached_rt, cached_did = storage.get_session(email)
        if cached_rt and cached_did:
            refresh_token, device_id = cached_rt, cached_did

    # Tentative de renouvellement silencieux sans mot de passe
    if refresh_token and device_id:
        try:
            token, new_refresh_token = refresh_tokens(refresh_token, device_id)
        except EdusignError:
            # Token invalide ou expire : on retombe sur le mot de passe
            storage.clear_session(email)
            token = None

    # Si pas de token actif, connexion par mot de passe obligatoire
    if not token:
        if not password:
            raise EdusignError("Session expiree. Veuillez saisir votre mot de passe Edusign.")
        token, new_refresh_token, device_id, user = login(email, password, device_id)
        auth_method = "credentials"

    start_iso, end_iso = default_academic_dates()
    courses = fetch_planning(token, device_id, start_iso, end_iso)
    if not courses:
        raise EdusignError("Aucun cours trouve sur Edusign pour cette annee.")

    prof_ids = [c.get("PROFESSOR") for c in courses if c.get("PROFESSOR")]
    professors = fetch_professors(token, device_id, prof_ids)

    events = edusign_to_events(courses, professors)
    ics_text = ics_builder.build_ics(events)

    # Sauvegarde de l'agenda ET des jetons de session
    destination = storage.save_schedule(
        email, ics_text, refresh_token=new_refresh_token, device_id=device_id
    )

    return {
        "success": True,
        "count": len(events),
        "destination": destination,
        "authMethod": auth_method,
        "user": user,
    }

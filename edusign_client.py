"""Connecteur API Edusign - Synchronisation ultra-rapide sans navigateur.

Interroge directement l'API REST officielle d'Edusign :
  1. Authentification directe (POST /student/account/getByCredentials)
  2. Telechargement des cours (GET /student/planning)
  3. Resolution des enseignants (POST /student/professors)
  4. Serialisation au format ICS standard (RFC 5545) et sauvegarde
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


def _http_request(url, method="GET", data=None, headers=None, timeout=20):
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
    """Connexion classique Edusign. Renvoie (token, refresh_token, device_id, user_info)."""
    device_id = device_id or str(uuid.uuid4())
    payload = {
        "EMAIL": email.strip(),
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


def sync_schedule(email, password, device_id=None):
    """Execute la synchronisation complete en arriere-plan.
    
    Renvoie un dictionnaire avec le nombre de cours et la destination.
    """
    token, refresh_token, device_id, user = login(email, password, device_id)
    start_iso, end_iso = default_academic_dates()

    courses = fetch_planning(token, device_id, start_iso, end_iso)
    if not courses:
        raise EdusignError("Aucun cours trouve sur Edusign pour cette annee.")

    prof_ids = [c.get("PROFESSOR") for c in courses if c.get("PROFESSOR")]
    professors = fetch_professors(token, device_id, prof_ids)

    events = edusign_to_events(courses, professors)
    ics_text = ics_builder.build_ics(events)

    destination = storage.save_schedule(email, ics_text)
    return {
        "success": True,
        "count": len(events),
        "destination": destination,
        "user": user,
    }

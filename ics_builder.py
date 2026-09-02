"""Construction du fichier ICS a partir des reponses de l'API Auriga.

Ce module est la seule source de verite pour :
  - interroger `/api/plannings/me` (fetch_month) ;
  - transformer les "interventions" en evenements (extract_events) ;
  - serialiser ces evenements en ICS conforme RFC 5545 (build_ics).

Il etait auparavant duplique dans sync_worker.py, capture_api.py,
update_planning.py et convert_json_to_ics.py.
"""

import json
import re
import urllib.request
from datetime import date, datetime, timedelta, timezone

API_URL = "https://auriga.ipsa.fr/api/plannings/me"
PRODID = "-//Auriga//NONSGML v1.0//EN"

# Fenetre de recuperation : annee universitaire en cours + la suivante.
ACADEMIC_START_MONTH = 8
YEARS_AHEAD = 2
CHUNK_DAYS = 28


# --------------------------------------------------------------------------
# API
# --------------------------------------------------------------------------

def fetch_month(token, start_date, end_date, timeout=30):
    """Recupere un intervalle de planning. Renvoie None en cas d'echec."""
    days = "&".join("days=%d" % day for day in range(1, 8))
    url = "%s?%s&startDate=%s&endDate=%s" % (API_URL, days, start_date, end_date)
    req = urllib.request.Request(url, headers={
        "Authorization": token,
        "Accept": "application/json",
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except Exception as exc:
        print("[ics_builder] echec %s -> %s : %s" % (start_date, end_date, exc))
        return None


def academic_range(today=None):
    """Debut et fin de la fenetre a aspirer, calcules depuis la date du jour.

    Avant aout on repart de l'aout precedent, sinon on perdrait le second
    semestre de l'annee en cours.
    """
    today = today or date.today()
    year = today.year if today.month >= ACADEMIC_START_MONTH else today.year - 1
    start = date(year, ACADEMIC_START_MONTH, 1)
    return start, date(year + YEARS_AHEAD, ACADEMIC_START_MONTH, 1)


def iter_chunks(start, end, size_days=CHUNK_DAYS):
    """Decoupe [start, end) en tranches de `size_days` jours."""
    current = start
    while current < end:
        chunk_end = min(current + timedelta(days=size_days - 1), end)
        yield current, chunk_end
        current = chunk_end + timedelta(days=1)


def fetch_all(token, today=None):
    """Aspire toute la fenetre universitaire. Renvoie la liste des reponses."""
    start, end = academic_range(today)
    payloads = []
    for chunk_start, chunk_end in iter_chunks(start, end):
        data = fetch_month(token, chunk_start.isoformat(), chunk_end.isoformat())
        if data:
            payloads.append(data)
    return payloads


# --------------------------------------------------------------------------
# Normalisation
# --------------------------------------------------------------------------

def _caption(node):
    """Extrait le libelle francais d'un noeud portant un `caption`."""
    if not isinstance(node, dict):
        return ""
    return (node.get("caption") or {}).get("fr", "") or ""


def _intervention_to_event(evt):
    units = evt.get("interventionPedagogicalUnits") or []
    matter = _caption(units[0].get("pedagogicalUnit")) if units else ""
    if not matter:
        matter = evt.get("description") or ""

    act_type = (evt.get("activityType") or {}).get("code", "") or ""
    if act_type and act_type != matter:
        summary = "%s - %s" % (act_type, matter)
    else:
        summary = matter

    teachers = []
    for inst in evt.get("interventionInstructors") or []:
        person = inst.get("person") or {}
        name = "%s %s" % (person.get("currentFirstName", ""),
                          person.get("currentLastName", ""))
        name = name.strip()
        if name:
            teachers.append(name)

    rooms = []
    for res in evt.get("interventionResources") or []:
        resource = res.get("resource") or {}
        if resource.get("isRoom"):
            room = _caption(resource)
            if room:
                rooms.append(room)

    # Le prefixe « Enseignant » n'est pas decoratif : c'est ce que cherche
    # ics._guess_teacher pour afficher le nom sur la carte de cours.
    description = ""
    if teachers:
        label = "Enseignants" if len(teachers) > 1 else "Enseignant"
        description = "%s : %s" % (label, ", ".join(teachers))

    return {
        "uid": "%s@auriga" % evt["id"],
        "start": evt["startDateTime"],
        "end": evt["endDateTime"],
        "summary": summary,
        "description": description,
        "location": ", ".join(rooms),
    }


def extract_events(payloads):
    """Aplatit les reponses de l'API en evenements, dedupliques par id."""
    unique = {}
    for payload in payloads:
        # Tolere la reponse brute comme l'ancien format {"data": ...}.
        data = payload.get("data", payload) if isinstance(payload, dict) else payload
        if not isinstance(data, dict):
            continue
        for evt in data.get("interventions") or []:
            if evt.get("id") is None or not evt.get("startDateTime"):
                continue
            # Les tranches de dates se recouvrent : on garde la premiere
            # occurrence, la plus complete.
            unique.setdefault(evt["id"], evt)
    return [_intervention_to_event(evt) for evt in unique.values()]


# --------------------------------------------------------------------------
# Serialisation ICS (RFC 5545)
# --------------------------------------------------------------------------

_ISO = re.compile(
    r"^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2}):(\d{2})"
    r"(?:\.\d+)?(Z|[+-]\d{2}:?\d{2})?$"
)


def _to_utc_stamp(value):
    """ISO 8601 -> horodatage ICS UTC (`20260907T080000Z`).

    Une valeur sans fuseau est consideree comme deja en UTC : c'est ce que
    renvoie l'API Auriga.
    """
    match = _ISO.match(str(value).strip())
    if not match:
        raise ValueError("date ISO 8601 invalide : %r" % (value,))

    year, month, day, hour, minute, second = (int(g) for g in match.groups()[:6])
    moment = datetime(year, month, day, hour, minute, second)

    offset = match.group(7)
    if offset and offset != "Z":
        sign = 1 if offset[0] == "+" else -1
        digits = offset[1:].replace(":", "")
        moment -= sign * timedelta(hours=int(digits[:2]), minutes=int(digits[2:]))

    return moment.strftime("%Y%m%dT%H%M%SZ")


def escape_text(value):
    """Echappement RFC 5545 des proprietes TEXT."""
    return (str(value or "")
            .replace("\\", "\\\\")
            .replace(";", "\\;")
            .replace(",", "\\,")
            .replace("\r\n", "\\n")
            .replace("\n", "\\n")
            .replace("\r", "\\n"))


def fold(line):
    """Replie une ligne a 75 octets, continuation prefixee d'une espace."""
    raw = line.encode("utf-8")
    if len(raw) <= 75:
        return line

    chunks = []
    start = 0
    limit = 75
    while start < len(raw):
        end = min(start + limit, len(raw))
        # Ne jamais couper au milieu d'un caractere multi-octets.
        while start < end < len(raw) and (raw[end] & 0xC0) == 0x80:
            end -= 1
        chunks.append(raw[start:end].decode("utf-8"))
        start = end
        limit = 74  # les lignes suivantes perdent un octet pour l'espace
    return "\r\n ".join(chunks)


def build_ics(events, now=None):
    """Serialise des evenements normalises en un calendrier ICS complet."""
    stamp = (now or datetime.now(timezone.utc)).strftime("%Y%m%dT%H%M%SZ")

    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:%s" % PRODID,
        "CALSCALE:GREGORIAN",
    ]
    for evt in events:
        try:
            start = _to_utc_stamp(evt["start"])
            end = _to_utc_stamp(evt["end"])
        except (KeyError, ValueError) as exc:
            print("[ics_builder] evenement ignore : %s" % exc)
            continue

        lines.extend([
            "BEGIN:VEVENT",
            "UID:%s" % escape_text(evt["uid"]),
            "DTSTAMP:%s" % stamp,
            "DTSTART:%s" % start,
            "DTEND:%s" % end,
            "SUMMARY:%s" % escape_text(evt.get("summary")),
            "DESCRIPTION:%s" % escape_text(evt.get("description")),
            "LOCATION:%s" % escape_text(evt.get("location")),
            "END:VEVENT",
        ])
    lines.append("END:VCALENDAR")

    return "\r\n".join(fold(line) for line in lines) + "\r\n"


def build_ics_from_payloads(payloads, now=None):
    """Raccourci : reponses brutes de l'API -> texte ICS."""
    return build_ics(extract_events(payloads), now=now)

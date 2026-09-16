"""Construction du fichier ICS (RFC 5545) a partir des cours normalises.

Ce module est responsable de la serialisation des evenements d'emploi du temps
en un calendrier conforme au standard RFC 5545 :
  - En-tetes VCALENDAR (PRODID, VERSION 2.0, CALSCALE)
  - Blocs VEVENT avec UID, DTSTAMP, DTSTART, DTEND, SUMMARY, DESCRIPTION, LOCATION
  - Echappement des caracteres speciaux RFC 5545
  - Pliage des lignes a 75 octets maximum
"""

import re
from datetime import datetime, timedelta, timezone

PRODID = "-//Edusign//NONSGML v1.0//EN"

_ISO = re.compile(
    r"^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2}):(\d{2})"
    r"(?:\.\d+)?(Z|[+-]\d{2}:?\d{2})?$"
)


def _to_utc_stamp(value):
    """Convertit une chaine ISO 8601 en horodatage ICS UTC (`20260907T080000Z`).

    Une valeur sans fuseau est consideree comme deja en UTC.
    """
    match = _ISO.match(str(value).strip())
    if not match:
        raise ValueError("Date ISO 8601 invalide : %r" % (value,))

    year, month, day, hour, minute, second = (int(g) for g in match.groups()[:6])
    moment = datetime(year, month, day, hour, minute, second)

    offset = match.group(7)
    if offset and offset != "Z":
        sign = 1 if offset[0] == "+" else -1
        digits = offset[1:].replace(":", "")
        moment -= sign * timedelta(hours=int(digits[:2]), minutes=int(digits[2:]))

    return moment.strftime("%Y%m%dT%H%M%SZ")


def escape_text(value):
    """Echappe les proprietes de texte selon la norme RFC 5545."""
    return (
        str(value or "")
        .replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\r\n", "\\n")
        .replace("\n", "\\n")
        .replace("\r", "\\n")
    )


def fold(line):
    """Replie une ligne a 75 octets max, continuation prefixee d'un espace (RFC 5545)."""
    raw = line.encode("utf-8")
    if len(raw) <= 75:
        return line

    chunks = []
    start = 0
    limit = 75
    while start < len(raw):
        end = min(start + limit, len(raw))
        # Ne jamais couper au milieu d'un caractere UTF-8 multi-octets
        while start < end < len(raw) and (raw[end] & 0xC0) == 0x80:
            end -= 1
        chunks.append(raw[start:end].decode("utf-8"))
        start = end
        limit = 74  # les lignes suivantes perdent 1 octet pour l'espace de continuation
    return "\r\n ".join(chunks)


def build_ics(events, now=None):
    """Serialise une liste d'evenements normalises en contenu de calendrier ICS complet.

    Chaque evenement attendu est un dictionnaire contenant :
      - uid (str)
      - start (chaine ISO 8601)
      - end (chaine ISO 8601)
      - summary (str)
      - description (str, optionnel)
      - location (str, optionnel)
    """
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
            print("[ics_builder] evenement ignore (%s)" % exc)
            continue

        vevent_lines = [
            "BEGIN:VEVENT",
            "UID:%s" % escape_text(evt["uid"]),
            "DTSTAMP:%s" % stamp,
            "DTSTART:%s" % start,
            "DTEND:%s" % end,
            "SUMMARY:%s" % escape_text(evt.get("summary")),
            "DESCRIPTION:%s" % escape_text(evt.get("description")),
            "LOCATION:%s" % escape_text(evt.get("location")),
        ]
        if evt.get("attendance"):
            vevent_lines.append("X-EDUSIGN-ATTENDANCE:%s" % escape_text(evt["attendance"]))
        if evt.get("can_sign") is not None:
            vevent_lines.append("X-EDUSIGN-CAN-SIGN:%s" % ("TRUE" if evt["can_sign"] else "FALSE"))
        if evt.get("is_justified") is not None:
            vevent_lines.append("X-EDUSIGN-JUSTIFIED:%s" % ("TRUE" if evt["is_justified"] else "FALSE"))
        vevent_lines.append("END:VEVENT")
        lines.extend(vevent_lines)
    lines.append("END:VCALENDAR")

    return "\r\n".join(fold(line) for line in lines) + "\r\n"

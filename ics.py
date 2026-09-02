"""Parseur ICS minimal, sans dependance externe.

Suffisant pour un export d'emploi du temps Aurion : VEVENT, DTSTART/DTEND,
SUMMARY/LOCATION/DESCRIPTION, et un support RRULE restreint aux cas courants
(FREQ=DAILY/WEEKLY avec INTERVAL, COUNT, UNTIL, BYDAY) + EXDATE.

Toutes les dates sont normalisees en UTC : le navigateur les reaffiche dans le
fuseau local, ce qui evite de dependre de tzdata (souvent absent sous Windows).
"""

from datetime import date, datetime, timedelta, timezone

MAX_OCCURRENCES = 400  # garde-fou : une RRULE sans fin ne doit pas exploser

_WEEKDAYS = {"MO": 0, "TU": 1, "WE": 2, "TH": 3, "FR": 4, "SA": 5, "SU": 6}


# --------------------------------------------------------------------------
# Fuseaux horaires
# --------------------------------------------------------------------------

def _last_sunday(year, month):
    nxt = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)
    last = nxt - timedelta(days=1)
    return last - timedelta(days=(last.weekday() + 1) % 7)


def _paris_to_utc(naive):
    """Heure locale Paris -> UTC via les regles UE (aucun tzdata requis).

    Ete (UTC+2) du dernier dimanche de mars 01:00 UTC au dernier dimanche
    d'octobre 01:00 UTC, hiver (UTC+1) le reste de l'annee.
    """
    year = naive.year
    dst_start = datetime(year, 3, _last_sunday(year, 3).day, 1, 0)
    dst_end = datetime(year, 10, _last_sunday(year, 10).day, 1, 0)
    as_summer = naive - timedelta(hours=2)
    if dst_start <= as_summer < dst_end:
        return as_summer
    return naive - timedelta(hours=1)


def _local_to_utc(naive, tzid):
    if not tzid:
        return _paris_to_utc(naive)  # heure flottante : l'ecole est a Paris
    try:
        from zoneinfo import ZoneInfo

        aware = naive.replace(tzinfo=ZoneInfo(tzid))
        return aware.astimezone(timezone.utc).replace(tzinfo=None)
    except Exception:
        # tzdata absent : repli sur les regles UE, correctes pour Europe/Paris
        if tzid.startswith("Europe/"):
            return _paris_to_utc(naive)
        return naive


# --------------------------------------------------------------------------
# Lecture bas niveau
# --------------------------------------------------------------------------

def _unfold(text):
    """Recolle les lignes repliees (RFC 5545 : continuation = espace ou tab)."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    out = []
    for line in text.split("\n"):
        if line[:1] in (" ", "\t") and out:
            out[-1] += line[1:]
        else:
            out.append(line)
    return out


def _split_line(line):
    """'DTSTART;TZID=Europe/Paris:20260901T080000' -> (nom, params, valeur)."""
    idx = len(line)
    in_quotes = False
    for i, ch in enumerate(line):
        if ch == '"':
            in_quotes = not in_quotes
        elif ch == ":" and not in_quotes:
            idx = i
            break
    head, value = line[:idx], line[idx + 1:]
    parts = head.split(";")
    name = parts[0].upper()
    params = {}
    for part in parts[1:]:
        if "=" in part:
            key, val = part.split("=", 1)
            params[key.upper()] = val.strip('"')
    return name, params, value


def _unescape(value):
    out = []
    i = 0
    while i < len(value):
        ch = value[i]
        if ch == "\\" and i + 1 < len(value):
            nxt = value[i + 1]
            out.append({"n": "\n", "N": "\n"}.get(nxt, nxt))
            i += 2
        else:
            out.append(ch)
            i += 1
    return "".join(out)


def _parse_dt(value, params):
    """Retourne (datetime UTC naive | date, all_day)."""
    value = value.strip()
    if params.get("VALUE") == "DATE" or (len(value) == 8 and "T" not in value):
        return datetime.strptime(value, "%Y%m%d").date(), True
    if value.endswith("Z"):
        return datetime.strptime(value, "%Y%m%dT%H%M%SZ"), False
    naive = datetime.strptime(value, "%Y%m%dT%H%M%S")
    return _local_to_utc(naive, params.get("TZID")), False


# --------------------------------------------------------------------------
# Recurrences
# --------------------------------------------------------------------------

def _parse_rrule(value):
    rule = {}
    for part in value.split(";"):
        if "=" in part:
            key, val = part.split("=", 1)
            rule[key.upper()] = val
    return rule


def _expand(start, end, rrule, exdates):
    """Developpe une RRULE simple en liste de couples (debut, fin)."""
    freq = rrule.get("FREQ", "").upper()
    if freq not in ("DAILY", "WEEKLY"):
        return [(start, end)]  # non gere : on garde l'occurrence de base

    interval = max(1, int(rrule.get("INTERVAL") or 1))
    count = int(rrule["COUNT"]) if rrule.get("COUNT") else None

    until = None
    if rrule.get("UNTIL"):
        try:
            until, _ = _parse_dt(rrule["UNTIL"], {})
            if not isinstance(until, datetime):
                until = datetime.combine(until, datetime.max.time())
        except ValueError:
            until = None

    bydays = [
        _WEEKDAYS[token[-2:].upper()]
        for token in rrule.get("BYDAY", "").split(",")
        if token and token[-2:].upper() in _WEEKDAYS
    ]

    duration = end - start
    occurrences = []
    generated = 0  # COUNT borne les occurrences de la regle, EXDATE retire ensuite
    horizon = start + timedelta(days=400)
    cursor = start

    while generated < MAX_OCCURRENCES and cursor <= horizon:
        if freq == "DAILY":
            candidates = [cursor]
            step = timedelta(days=interval)
        else:
            week_start = cursor - timedelta(days=cursor.weekday())
            targets = sorted(bydays or [start.weekday()])
            candidates = [week_start + timedelta(days=day) for day in targets]
            step = timedelta(weeks=interval)

        for candidate in candidates:
            if candidate < start:
                continue
            if until and candidate > until:
                return occurrences
            generated += 1
            if candidate.date() not in exdates:
                occurrences.append((candidate, candidate + duration))
            if count and generated >= count:
                return occurrences
            if generated >= MAX_OCCURRENCES:
                return occurrences
        cursor = cursor + step

    return occurrences


# --------------------------------------------------------------------------
# API publique
# --------------------------------------------------------------------------

def parse(text):
    """Texte ICS -> liste de dicts serialisables en JSON, tries par debut."""
    events = []
    current = None

    for line in _unfold(text):
        if not line.strip():
            continue
        name, params, value = _split_line(line)

        if name == "BEGIN" and value.upper() == "VEVENT":
            current = {"exdates": set(), "rrule": None}
            continue
        if name == "END" and value.upper() == "VEVENT":
            if current is not None:
                events.extend(_finalize(current))
            current = None
            continue
        if current is None:
            continue

        try:
            if name == "DTSTART":
                current["start"], current["all_day"] = _parse_dt(value, params)
            elif name == "DTEND":
                current["end"], _ = _parse_dt(value, params)
            elif name == "SUMMARY":
                current["summary"] = _unescape(value)
            elif name == "LOCATION":
                current["location"] = _unescape(value)
            elif name == "DESCRIPTION":
                current["description"] = _unescape(value)
            elif name == "UID":
                current["uid"] = value
            elif name == "CATEGORIES":
                current["categories"] = _unescape(value)
            elif name == "RRULE":
                current["rrule"] = _parse_rrule(value)
            elif name == "EXDATE":
                for chunk in value.split(","):
                    parsed, _ = _parse_dt(chunk, params)
                    current["exdates"].add(
                        parsed.date() if isinstance(parsed, datetime) else parsed
                    )
        except (ValueError, KeyError):
            # une propriete illisible ne doit pas faire sauter tout l'agenda
            continue

    events.sort(key=lambda evt: evt["start"])
    return events


def _finalize(raw):
    start = raw.get("start")
    if start is None:
        return []

    all_day = raw.get("all_day", False)
    if all_day:
        start_dt = datetime.combine(start, datetime.min.time())
        end_raw = raw.get("end")
        if isinstance(end_raw, datetime):
            end_dt = end_raw
        elif isinstance(end_raw, date):
            end_dt = datetime.combine(end_raw, datetime.min.time())
        else:
            end_dt = start_dt + timedelta(days=1)
    else:
        start_dt = start
        end_dt = raw.get("end") or start_dt + timedelta(hours=1)

    if end_dt < start_dt:
        end_dt = start_dt + timedelta(hours=1)

    pairs = (
        _expand(start_dt, end_dt, raw["rrule"], raw["exdates"])
        if raw.get("rrule")
        else [(start_dt, end_dt)]
    )

    summary = raw.get("summary", "").strip()
    description = raw.get("description", "").strip()
    location = raw.get("location", "").strip()
    kind = _guess_kind(summary, description, raw.get("categories", ""))
    teacher = _guess_teacher(description)
    base_uid = raw.get("uid", "evt")

    return [
        {
            "uid": "%s-%d" % (base_uid, index),
            "start": occ_start.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "end": occ_end.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "allDay": all_day,
            "title": _clean_title(summary),
            "rawTitle": summary,
            "location": location,
            "description": description,
            "teacher": teacher,
            "kind": kind,
        }
        for index, (occ_start, occ_end) in enumerate(pairs)
    ]


_KINDS = [
    ("EXAM", ("examen", "partiel", "controle", "contrôle", "evaluation",
              "évaluation", "exam")),
    ("PROJET", ("projet", "soutenance")),
    ("TP", ("travaux pratiques", "tp")),
    ("TD", ("travaux diriges", "travaux dirigés", "td")),
    ("CM", ("cours magistral", "magistral", "amphi", "cm")),
]


# Aurion prefixe le libelle par le code exact du type ("TD - Bases de
# donnees"). Cette information est fiable, contrairement a la recherche de
# mots-cles : un TD intitule "Bases de donnees et projet" etait classe PROJET
# parce que le nom de la matiere contient le mot.
_PREFIX_CODES = {
    "CM": "CM", "COURS": "CM", "AMPHI": "CM",
    "TD": "TD",
    "TP": "TP",
    "EXAM": "EXAM", "EXAMEN": "EXAM", "DS": "EXAM", "PARTIEL": "EXAM",
    "PROJET": "PROJET", "PRJ": "PROJET", "SOUT": "PROJET",
}


def _type_prefix(summary):
    """Code de type en tete de libelle, ou None."""
    for sep in (" - ", " : ", " – ", " | "):
        head, found, tail = summary.partition(sep)
        if found and tail.strip():
            return _PREFIX_CODES.get(head.strip().upper())
    return None


def _guess_kind(summary, description, categories):
    """Devine CM/TD/TP/exam. Le prefixe de type fait foi ; a defaut seulement,
    on cherche des mots-cles. Sans correspondance on renvoie AUTRE et
    l'interface affiche le libelle brut."""
    prefix = _type_prefix(summary)
    if prefix:
        return prefix

    haystack = " %s %s %s " % (summary, description, categories)
    haystack = haystack.lower()
    for kind, needles in _KINDS:
        for needle in needles:
            if len(needle) <= 2:
                if any(
                    pattern in haystack
                    for pattern in (" %s " % needle, "(%s)" % needle,
                                    "[%s]" % needle, "%s-" % needle,
                                    "%s:" % needle)
                ):
                    return kind
            elif needle in haystack:
                return kind
    return "AUTRE"


def _clean_title(summary):
    """Retire les prefixes de type ('CM - ', 'TD : ') pour un titre lisible."""
    for sep in (" - ", " : ", " – ", " | "):
        head, found, tail = summary.partition(sep)
        if found and len(head) <= 12 and tail.strip():
            return tail.strip()
    return summary.strip()


def _guess_teacher(description):
    for line in description.split("\n"):
        lowered = line.lower().strip()
        for label in ("enseignant", "intervenant", "professeur", "formateur"):
            if lowered.startswith(label):
                _, _, value = line.partition(":")
                if value.strip():
                    return value.strip()
    return ""

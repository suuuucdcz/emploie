"""Tests du parseur ICS : python test_ics.py"""

import ics

BASE = """BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VEVENT
UID:1@test
DTSTART;TZID=Europe/Paris:20260901T083000
DTEND;TZID=Europe/Paris:20260901T100000
SUMMARY:CM - Aerodynamique
LOCATION:Amphi A
DESCRIPTION:Enseignant : M. Durand\nGroupe : AERO3
END:VEVENT
END:VCALENDAR
"""

FOLDED = """BEGIN:VCALENDAR
BEGIN:VEVENT
UID:2@test
DTSTART:20260115T090000Z
DTEND:20260115T110000Z
SUMMARY:TP - Un titre vraiment tres long qui a ete
  replie sur deux lignes
END:VEVENT
END:VCALENDAR
"""

RECURRING = """BEGIN:VCALENDAR
BEGIN:VEVENT
UID:3@test
DTSTART;TZID=Europe/Paris:20260907T140000
DTEND;TZID=Europe/Paris:20260907T160000
SUMMARY:TD - Automatique
RRULE:FREQ=WEEKLY;COUNT=4;BYDAY=MO
EXDATE;TZID=Europe/Paris:20260914T140000
END:VEVENT
END:VCALENDAR
"""

ALLDAY = """BEGIN:VCALENDAR
BEGIN:VEVENT
UID:4@test
DTSTART;VALUE=DATE:20260501
DTEND;VALUE=DATE:20260502
SUMMARY:Ferie
END:VEVENT
END:VCALENDAR
"""

failures = []


def check(label, actual, expected):
    if actual != expected:
        failures.append("%s : attendu %r, obtenu %r" % (label, expected, actual))
    else:
        print("  ok  %s" % label)


print("heure d'ete (CEST = UTC+2)")
evt = ics.parse(BASE)[0]
check("debut converti en UTC", evt["start"], "2026-09-01T06:30:00Z")
check("fin converti en UTC", evt["end"], "2026-09-01T08:00:00Z")
check("prefixe de type retire", evt["title"], "Aerodynamique")
check("type detecte", evt["kind"], "CM")
check("salle", evt["location"], "Amphi A")
check("enseignant extrait", evt["teacher"], "M. Durand")

print("heure d'hiver (CET = UTC+1) et lignes repliees")
evt = ics.parse(FOLDED)[0]
check("horodatage Z inchange", evt["start"], "2026-01-15T09:00:00Z")
check("ligne recollee", evt["title"],
      "Un titre vraiment tres long qui a ete replie sur deux lignes")
check("type TP", evt["kind"], "TP")

print("recurrence hebdomadaire")
events = ics.parse(RECURRING)
check("4 occurrences moins 1 EXDATE", len(events), 3)
check("1re occurrence", events[0]["start"], "2026-09-07T12:00:00Z")
check("EXDATE du 14 exclue",
      [e["start"][:10] for e in events], ["2026-09-07", "2026-09-21", "2026-09-28"])

print("journee entiere")
evt = ics.parse(ALLDAY)[0]
check("marquee allDay", evt["allDay"], True)
check("debut", evt["start"], "2026-05-01T00:00:00Z")

print("robustesse")
check("ICS vide", ics.parse(""), [])
check("VEVENT sans DTSTART ignore",
      ics.parse("BEGIN:VCALENDAR\nBEGIN:VEVENT\nSUMMARY:x\nEND:VEVENT\nEND:VCALENDAR"), [])

print()
if failures:
    print("ECHECS (%d) :" % len(failures))
    for line in failures:
        print("  -", line)
    raise SystemExit(1)
print("Tous les tests passent.")

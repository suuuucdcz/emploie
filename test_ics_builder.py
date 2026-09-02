"""Tests du generateur ICS : python test_ics_builder.py"""

from datetime import date, datetime, timedelta, timezone

import ics
import ics_builder

FAILURES = []


def check(label, got, expected):
    if got == expected:
        print("  ok  %s" % label)
    else:
        FAILURES.append(label)
        print("  KO  %s\n      attendu : %r\n      obtenu  : %r" % (label, expected, got))


NOW = datetime(2026, 9, 1, 12, 0, 0, tzinfo=timezone.utc)

PAYLOAD = {
    "interventions": [
        {
            "id": 42,
            "startDateTime": "2026-09-07T08:00:00Z",
            "endDateTime": "2026-09-07T10:00:00Z",
            "activityType": {"code": "TD"},
            "interventionPedagogicalUnits": [
                {"pedagogicalUnit": {"caption": {"fr": "Bases de donnees, SQL; niveau 2"}}}
            ],
            "interventionInstructors": [
                {"person": {"currentFirstName": "Ada", "currentLastName": "Lovelace"}}
            ],
            "interventionResources": [
                {"resource": {"isRoom": True, "caption": {"fr": "T- 2.0"}}},
                {"resource": {"isRoom": False, "caption": {"fr": "Videoprojecteur"}}},
            ],
        },
        # Doublon : la meme intervention revient dans deux tranches de dates.
        {
            "id": 42,
            "startDateTime": "2026-09-07T08:00:00Z",
            "endDateTime": "2026-09-07T10:00:00Z",
        },
    ]
}


print("fenetre universitaire")
check("depuis septembre", ics_builder.academic_range(date(2026, 9, 15))[0], date(2026, 8, 1))
check("depuis fevrier", ics_builder.academic_range(date(2027, 2, 3))[0], date(2026, 8, 1))
check("deux ans de large", ics_builder.academic_range(date(2026, 9, 15))[1], date(2028, 8, 1))
chunks = list(ics_builder.iter_chunks(date(2026, 8, 1), date(2026, 9, 30)))
check("tranches jointives", chunks[1][0], chunks[0][1] + timedelta(days=1))
check("derniere tranche bornee", chunks[-1][1], date(2026, 9, 30))

print("normalisation")
events = ics_builder.extract_events([PAYLOAD])
check("doublon supprime", len(events), 1)
check("titre = type + matiere", events[0]["summary"], "TD - Bases de donnees, SQL; niveau 2")
check("enseignant etiquete", events[0]["description"], "Enseignant : Ada Lovelace")
check("seules les salles", events[0]["location"], "T- 2.0")

print("horodatages")
check("suffixe Z", ics_builder._to_utc_stamp("2026-09-07T08:00:00Z"), "20260907T080000Z")
check("decalage +02:00", ics_builder._to_utc_stamp("2026-09-07T10:00:00+02:00"), "20260907T080000Z")
check("sans fuseau = UTC", ics_builder._to_utc_stamp("2026-09-07T08:00:00"), "20260907T080000Z")

print("echappement RFC 5545")
check("virgule et point-virgule", ics_builder.escape_text("a, b; c"), "a\\, b\\; c")
check("antislash", ics_builder.escape_text("a\\b"), "a\\\\b")
check("saut de ligne", ics_builder.escape_text("a\r\nb"), "a\\nb")

print("serialisation")
text = ics_builder.build_ics(events, now=NOW)
check("fins de ligne CRLF", "\n" in text.replace("\r\n", ""), False)
check("DTSTAMP present", "DTSTAMP:20260901T120000Z" in text, True)
check("virgule echappee", "Bases de donnees\\, SQL\\; niveau 2" in text, True)
check("lignes <= 75 octets",
      max(len(line.encode("utf-8")) for line in text.split("\r\n")) <= 75, True)

long_event = [{"uid": "x@auriga", "start": "2026-09-07T08:00:00Z",
               "end": "2026-09-07T10:00:00Z", "summary": "Mecanique " * 12,
               "description": "", "location": ""}]
folded = ics_builder.build_ics(long_event, now=NOW)
check("ligne longue repliee", "\r\n " in folded, True)

print("relecture par le parseur")
parsed = ics.parse(text)
check("un evenement relu", len(parsed), 1)
check("titre restitue", parsed[0]["rawTitle"], "TD - Bases de donnees, SQL; niveau 2")
check("salle restituee", parsed[0]["location"], "T- 2.0")
check("enseignant restitue", parsed[0]["teacher"], "Ada Lovelace")
reparsed = ics.parse(folded)
check("ligne repliee recollee", reparsed[0]["rawTitle"].strip(), ("Mecanique " * 12).strip())

print()
if FAILURES:
    raise SystemExit("%d test(s) en echec : %s" % (len(FAILURES), ", ".join(FAILURES)))
print("Tous les tests passent.")

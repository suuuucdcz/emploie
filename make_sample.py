"""Genere sample.ics : une semaine de cours fictifs calee sur la semaine en
cours, pour developper l'interface sans avoir a etre connecte a Aurion.

    python make_sample.py
"""

import os
from datetime import date, datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(ROOT, "sample.ics")

# (jour de la semaine, debut, fin, resume, salle, enseignant, groupe)
WEEK = [
    (0, "08:30", "10:00", "CM - Aerodynamique subsonique", "Ivry - Amphi A", "M. Durand", "AERO3-A"),
    (0, "10:15", "12:15", "TD - Mecanique du vol", "Ivry - B204", "Mme Leroy", "AERO3-A2"),
    (0, "13:30", "17:30", "TP - Structures aeronautiques", "Ivry - Labo Meca", "M. Benali", "AERO3-A2"),
    (1, "09:00", "12:00", "CM - Mathematiques appliquees", "Ivry - Amphi B", "Mme Chevalier", "AERO3"),
    (1, "14:00", "16:00", "TD - Automatique", "Ivry - C112", "M. Moreau", "AERO3-A2"),
    (2, "08:30", "10:30", "CM - Propulsion et turbomachines", "Ivry - Amphi A", "M. Perrin", "AERO3"),
    (2, "10:45", "12:45", "TD - Anglais technique", "Ivry - D305", "Ms. Carter", "AERO3-ANG2"),
    (3, "09:00", "13:00", "TP - Informatique embarquee", "Ivry - Salle info 2", "M. Nguyen", "AERO3-A2"),
    (3, "14:00", "16:00", "CM - Materiaux composites", "Ivry - Amphi C", "Mme Fontaine", "AERO3"),
    (4, "08:30", "11:30", "Projet - Bureau d'etudes", "Ivry - B210", "M. Benali", "AERO3-A2"),
    (4, "13:30", "15:30", "Examen - Partiel Aerodynamique", "Ivry - Amphi A", "M. Durand", "AERO3"),
]


def escape(value):
    return value.replace("\\", "\\\\").replace(",", "\\,").replace(";", "\\;")


def main():
    today = date.today()
    monday = today - timedelta(days=today.weekday())

    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//auriga-edt//demo//FR",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "X-WR-CALNAME:Emploi du temps (demo)",
    ]

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    for index, (weekday, start, end, summary, room, teacher, group) in enumerate(WEEK):
        day = monday + timedelta(days=weekday)
        start_dt = "%sT%s00" % (day.strftime("%Y%m%d"), start.replace(":", ""))
        end_dt = "%sT%s00" % (day.strftime("%Y%m%d"), end.replace(":", ""))
        description = "Enseignant : %s\\nGroupe : %s" % (teacher, group)

        lines += [
            "BEGIN:VEVENT",
            "UID:demo-%d@auriga.local" % index,
            "DTSTAMP:%s" % stamp,
            "DTSTART;TZID=Europe/Paris:%s" % start_dt,
            "DTEND;TZID=Europe/Paris:%s" % end_dt,
            "SUMMARY:%s" % escape(summary),
            "LOCATION:%s" % escape(room),
            "DESCRIPTION:%s" % description,
            "END:VEVENT",
        ]

    lines.append("END:VCALENDAR")

    with open(OUT, "w", encoding="utf-8", newline="\r\n") as handle:
        handle.write("\r\n".join(lines) + "\r\n")

    print("sample.ics genere : %d cours, semaine du %s" % (len(WEEK), monday))


if __name__ == "__main__":
    main()

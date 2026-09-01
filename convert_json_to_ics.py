import json
from datetime import datetime
import uuid

def main():
    import os
    path = os.path.expanduser('~/planning_capture.json')
    if not os.path.exists(path):
        path = 'planning_capture.json'
    with open(path, 'r', encoding='utf-8') as f:
        data = json.load(f)

    events = []
    
    # Collect all interventions from all planning requests
    for req in data:
        if 'plannings/me' in req['url']:
            interventions = req['data'].get('interventions', [])
            for evt in interventions:
                events.append(evt)

    # Remove duplicates by ID
    unique_events = {}
    for evt in events:
        unique_events[evt['id']] = evt
    
    events = list(unique_events.values())
    print(f"Trouvé {len(events)} événements uniques.")

    with open('mon_agenda_genere.ics', 'w', encoding='utf-8') as f:
        f.write("BEGIN:VCALENDAR\n")
        f.write("VERSION:2.0\n")
        f.write("PRODID:-//Auriga//NONSGML v1.0//EN\n")

        for evt in events:
            start = evt['startDateTime'].replace('-', '').replace(':', '')
            end = evt['endDateTime'].replace('-', '').replace(':', '')
            
            # Type (ex: CM, TD)
            act_type = evt.get('activityType', {}).get('code', '')
            
            # Matière (PedagogicalUnit) ou Description
            units = evt.get('interventionPedagogicalUnits', [])
            matter = ""
            if units and len(units) > 0:
                matter = units[0].get('pedagogicalUnit', {}).get('caption', {}).get('fr', '')
            
            if not matter:
                matter = evt.get('description', '')
                
            summary = matter
            if act_type and act_type != matter:
                summary = f"{act_type} - {matter}"

            # Prof (Instructors)
            instructors = evt.get('interventionInstructors', [])
            prof_names = []
            for inst in instructors:
                p = inst.get('person', {})
                prof_names.append(f"{p.get('currentFirstName', '')} {p.get('currentLastName', '')}")
            description = ", ".join(prof_names)

            # Salle (Resources)
            resources = evt.get('interventionResources', [])
            rooms = []
            for res in resources:
                if res.get('resource', {}).get('isRoom'):
                    rooms.append(res['resource'].get('caption', {}).get('fr', ''))
            location = ", ".join(rooms)

            f.write("BEGIN:VEVENT\n")
            f.write(f"UID:{evt['id']}@auriga\n")
            f.write(f"DTSTAMP:{datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')}\n")
            f.write(f"DTSTART:{start}\n")
            f.write(f"DTEND:{end}\n")
            f.write(f"SUMMARY:{summary}\n")
            f.write(f"DESCRIPTION:{description}\n")
            f.write(f"LOCATION:{location}\n")
            f.write("END:VEVENT\n")

        f.write("END:VCALENDAR\n")
    
    print("Fichier 'mon_agenda_genere.ics' créé avec succès !")

if __name__ == '__main__':
    main()

import json
import time
import datetime
import urllib.request
import os
from playwright.sync_api import sync_playwright

# --- TES IDENTIFIANTS ---
EMAIL = "mathis.derory@ipsa.fr"
PASSWORD = "MOT_DE_PASSE_RETIRE"

def fetch_month(token, start_date, end_date):
    url = f"https://auriga.ipsa.fr/api/plannings/me?days=1&days=2&days=3&days=4&days=5&days=6&days=7&startDate={start_date}&endDate={end_date}"
    req = urllib.request.Request(url, headers={
        "Authorization": token,
        "Accept": "application/json"
    })
    try:
        with urllib.request.urlopen(req) as response:
            return json.loads(response.read().decode('utf-8'))
    except Exception as e:
        print(f"Erreur lors de la récupération de {start_date}: {e}")
        return None

def convert_to_ics(data_list):
    events = []
    for req in data_list:
        interventions = req.get('data', {}).get('interventions', [])
        for evt in interventions:
            events.append(evt)

    unique_events = {}
    for evt in events:
        unique_events[evt['id']] = evt
    
    events = list(unique_events.values())
    print(f"[{len(events)} cours trouvés au total]")

    with open('mon_agenda_genere.ics', 'w', encoding='utf-8') as f:
        f.write("BEGIN:VCALENDAR\nVERSION:2.0\nPRODID:-//Auriga//NONSGML v1.0//EN\n")

        for evt in events:
            start = evt['startDateTime'].replace('-', '').replace(':', '')
            end = evt['endDateTime'].replace('-', '').replace(':', '')
            
            act_type = evt.get('activityType', {}).get('code', '')
            
            units = evt.get('interventionPedagogicalUnits', [])
            matter = ""
            if units and len(units) > 0:
                matter = units[0].get('pedagogicalUnit', {}).get('caption', {}).get('fr', '')
            
            if not matter:
                matter = evt.get('description', '')
                
            summary = matter
            if act_type and act_type != matter:
                summary = f"{act_type} - {matter}"

            instructors = evt.get('interventionInstructors', [])
            prof_names = []
            for inst in instructors:
                p = inst.get('person', {})
                prof_names.append(f"{p.get('currentFirstName', '')} {p.get('currentLastName', '')}")
            description = ", ".join(prof_names)

            resources = evt.get('interventionResources', [])
            rooms = []
            for res in resources:
                if res.get('resource', {}).get('isRoom'):
                    rooms.append(res['resource'].get('caption', {}).get('fr', ''))
            location = ", ".join(rooms)

            f.write("BEGIN:VEVENT\n")
            f.write(f"UID:{evt['id']}@auriga\n")
            f.write(f"DTSTART:{start}\n")
            f.write(f"DTEND:{end}\n")
            f.write(f"SUMMARY:{summary}\n")
            f.write(f"DESCRIPTION:{description}\n")
            f.write(f"LOCATION:{location}\n")
            f.write("END:VEVENT\n")

        f.write("END:VCALENDAR\n")

def run(playwright):
    print("=====================================================")
    print("MISE A JOUR AUTO DE L'EMPLOI DU TEMPS")
    print("=====================================================")
    
    browser = playwright.chromium.launch(headless=False)
    context = browser.new_context(viewport={"width": 800, "height": 800})
    page = context.new_page()

    captured_data = []
    token_found = [None]

    def handle_request(request):
        if "plannings/me" in request.url and not token_found[0]:
            token = request.headers.get("authorization")
            if token:
                token_found[0] = token
                print("\n[!] A2F Validée ! Téléchargement en cours...")

    page.on("request", handle_request)
    
    print("1. Connexion au portail...")
    page.goto("https://auriga.ipsa.fr/")
    
    try:
        # 1.5 - Cliquer sur le bouton Microsoft si présent (Keycloak)
        try:
            page.locator("text=Microsoft").wait_for(timeout=4000)
            page.locator("text=Microsoft").first.click()
            print(" -> Clic sur 'Se connecter avec Microsoft'...")
        except:
            # Essai alternatif (des fois c'est 'Office 365' ou un autre nom)
            try:
                page.locator("text=Office 365").first.click()
                print(" -> Clic sur 'Office 365'...")
            except:
                pass

        # Remplissage automatique Microsoft
        print(" -> Remplissage de l'email...")
        page.locator('input[type="email"]').wait_for(timeout=10000)
        page.locator('input[type="email"]').fill(EMAIL)
        page.locator('input[type="submit"]').click()
        
        print(" -> Remplissage du mot de passe...")
        page.locator('input[type="password"]').wait_for(timeout=10000)
        page.locator('input[type="password"]').fill(PASSWORD)
        page.locator('input[type="submit"]').click()
        
        # Récupération du code de validation A2F (Microsoft Authenticator Number Matching)
        try:
            # Le numéro apparait souvent dans une div avec la classe displaySign
            a2f_elem = page.locator('.displaySign')
            a2f_elem.wait_for(timeout=5000)
            code = a2f_elem.text_content().strip()
            print(f"\n==================================================")
            print(f"📱 TAPE CE NUMERO SUR TON TELEPHONE : {code}")
            print(f"==================================================\n")
        except Exception:
            print("\n⏳ EN ATTENTE DE LA VALIDATION A2F SUR TON TELEPHONE...")
            
        # Accepter "Rester connecté ?" s'il apparait
        try:
            kmsi = page.locator('input[type="button"][value="Oui"], input[type="submit"][value="Oui"], input[id="idSIButton9"]')
            kmsi.wait_for(timeout=15000) # Laisse le temps de valider l'A2F
            kmsi.click()
            print(" -> 'Rester connecté' validé.")
        except:
            pass

        # Une fois revenu sur l'application de l'école, on s'assure d'ouvrir le planning
        try:
            print(" -> Ouverture de la page 'Mon planning'...")
            # On cherche le texte "Mon planning" dans le menu de gauche
            planning_link = page.locator('text=Mon planning').first
            planning_link.wait_for(timeout=10000)
            planning_link.click()
        except Exception:
            # Plan B : navigation directe
            page.goto("https://auriga.ipsa.fr/#/mainContent/menuEntry/227/planning")

    except Exception as e:
        print("-> (Le script a passé certaines étapes auto, probablement normal)")

    # Attend de trouver le token (donc attend que l'utilisateur valide l'A2F)
    while not token_found[0]:
        page.wait_for_timeout(500)
        try:
            if page.is_closed():
                break
        except Exception:
            break
            
    if token_found[0]:
        # On voit grand : d'août 2026 à août 2028 (2 ans)
        current = datetime.date(2026, 8, 1)
        end_year = datetime.date(2028, 8, 1)
        
        while current < end_year:
            chunk_end = current + datetime.timedelta(days=27)
            s_str = current.strftime("%Y-%m-%d")
            e_str = chunk_end.strftime("%Y-%m-%d")
            
            print(f" -> Période du {s_str} au {e_str}...")
            data = fetch_month(token_found[0], s_str, e_str)
            if data:
                captured_data.append({"data": data})
            
            current = chunk_end + datetime.timedelta(days=1)
            time.sleep(0.5)

        print("\n[!] Conversion de l'agenda en cours...")
        convert_to_ics(captured_data)
        
        if os.path.exists("cache/schedule.ics"):
            os.remove("cache/schedule.ics")
            
        print("\n=====================================================")
        print("✅ TERMINE ! Ton planning a été mis à jour.")
        print("=====================================================")
        time.sleep(2) # Petit délai pour laisser le temps de lire
    else:
        print("Annulé.")

    browser.close()

with sync_playwright() as playwright:
    run(playwright)

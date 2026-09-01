import json
import threading
import time
import datetime
import urllib.request
import os
from playwright.sync_api import sync_playwright

SYNC_STATES = {}

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
        return None

def convert_to_ics(data_list, email):
    events = []
    for req in data_list:
        interventions = req.get('data', {}).get('interventions', [])
        for evt in interventions:
            events.append(evt)
    unique_events = {}
    for evt in events:
        unique_events[evt['id']] = evt
    events = list(unique_events.values())
    
    ics_lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Auriga//NONSGML v1.0//EN"]
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
        ics_lines.append("BEGIN:VEVENT")
        ics_lines.append(f"UID:{evt['id']}@auriga")
        ics_lines.append(f"DTSTART:{start}")
        ics_lines.append(f"DTEND:{end}")
        ics_lines.append(f"SUMMARY:{summary}")
        ics_lines.append(f"DESCRIPTION:{description}")
        ics_lines.append(f"LOCATION:{location}")
        ics_lines.append("END:VEVENT")
    ics_lines.append("END:VCALENDAR")
    
    ics_content = "\n".join(ics_lines)
    
    # --- SAUVEGARDE (SUPABASE OU LOCAL) ---
    supabase_url = os.environ.get("SUPABASE_URL")
    supabase_key = os.environ.get("SUPABASE_KEY")
    
    if supabase_url and supabase_key:
        print(f" -> Envoi vers Supabase pour {email}...")
        req = urllib.request.Request(f"{supabase_url}/rest/v1/schedules", headers={
            "apikey": supabase_key,
            "Authorization": f"Bearer {supabase_key}",
            "Content-Type": "application/json",
            "Prefer": "resolution=merge-duplicates"
        }, data=json.dumps({"email": email, "ics_content": ics_content}).encode("utf-8"))
        req.get_method = lambda: 'POST'
        try:
            urllib.request.urlopen(req)
        except Exception as e:
            print(f"Erreur Supabase: {e}")
    else:
        # Fallback local
        os.makedirs('cache', exist_ok=True)
        safe_email = email.replace('@', '_').replace('.', '_')
        filename = f"cache/{safe_email}.ics"
        with open(filename, 'w', encoding='utf-8') as f:
            f.write(ics_content)

def _run_playwright(email, password):
    global SYNC_STATES
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            context = browser.new_context(viewport={"width": 800, "height": 800})
            page = context.new_page()

            token_found = [None]
            def handle_request(request):
                if "plannings/me" in request.url and not token_found[0]:
                    token = request.headers.get("authorization")
                    if token:
                        token_found[0] = token
            page.on("request", handle_request)
            
            SYNC_STATES[email]["status"] = "logging_in"
            page.goto("https://auriga.ipsa.fr/")
            
            try:
                page.locator("text=Microsoft").wait_for(timeout=4000)
                page.locator("text=Microsoft").first.click()
            except:
                pass

            page.locator('input[type="email"]').wait_for(timeout=10000)
            page.locator('input[type="email"]').fill(email)
            page.locator('input[type="submit"]').click()
            
            page.locator('input[type="password"]').wait_for(timeout=10000)
            page.locator('input[type="password"]').fill(password)
            page.locator('input[type="submit"]').click()
            
            try:
                a2f_elem = page.locator('.displaySign')
                a2f_elem.wait_for(timeout=5000)
                code = a2f_elem.text_content().strip()
                SYNC_STATES[email]["status"] = "waiting_2fa"
                SYNC_STATES[email]["code"] = code
            except Exception:
                SYNC_STATES[email]["status"] = "waiting_2fa"
                SYNC_STATES[email]["code"] = "Approuvez (pas de numéro)"

            try:
                kmsi = page.locator('input[type="button"][value="Oui"], input[type="submit"][value="Oui"], input[id="idSIButton9"]')
                kmsi.wait_for(timeout=20000)
                kmsi.click()
            except:
                pass

            try:
                planning_link = page.locator('text=Mon planning').first
                planning_link.wait_for(timeout=10000)
                planning_link.click()
            except Exception:
                page.goto("https://auriga.ipsa.fr/#/mainContent/menuEntry/227/planning")

            timeout = 30
            while not token_found[0] and timeout > 0:
                page.wait_for_timeout(1000)
                timeout -= 1
                
            if not token_found[0]:
                SYNC_STATES[email]["status"] = "error"
                SYNC_STATES[email]["error_msg"] = "Délai d'attente A2F"
                browser.close()
                return

            SYNC_STATES[email]["status"] = "downloading"
            
            captured_data = []
            current = datetime.date(2026, 8, 1)
            end_year = datetime.date(2028, 8, 1)
            while current < end_year:
                chunk_end = current + datetime.timedelta(days=27)
                s_str = current.strftime("%Y-%m-%d")
                e_str = chunk_end.strftime("%Y-%m-%d")
                data = fetch_month(token_found[0], s_str, e_str)
                if data:
                    captured_data.append({"data": data})
                current = chunk_end + datetime.timedelta(days=1)

            convert_to_ics(captured_data, email)
            SYNC_STATES[email]["status"] = "success"
            browser.close()
            
    except Exception as e:
        SYNC_STATES[email]["status"] = "error"
        SYNC_STATES[email]["error_msg"] = str(e)

def start_sync(email, password):
    global SYNC_STATES
    if email not in SYNC_STATES:
        SYNC_STATES[email] = {}
        
    if SYNC_STATES[email].get("status") in ["logging_in", "waiting_2fa", "downloading"]:
        return False
    
    SYNC_STATES[email] = {
        "status": "idle",
        "code": None,
        "error_msg": None
    }
    
    t = threading.Thread(target=_run_playwright, args=(email, password))
    t.start()
    return True

def get_status(email):
    return SYNC_STATES.get(email, {"status": "idle"})

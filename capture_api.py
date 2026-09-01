import json
import time
import datetime
import urllib.request
import urllib.error
from playwright.sync_api import sync_playwright

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

def run(playwright):
    browser = playwright.chromium.launch(headless=False)
    context = browser.new_context(viewport={"width": 1200, "height": 800})
    page = context.new_page()

    print("Ouverture du navigateur...")
    print("-> Connecte-toi avec tes identifiants.")
    print("-> Va sur la page de ton planning.")
    print("-> Patiente ! Le script va automatiquement aspirer TOUTE l'année une fois connecté.")

    captured_data = []
    token_found = [None]

    def handle_request(request):
        if "plannings/me" in request.url and not token_found[0]:
            token = request.headers.get("authorization")
            if token:
                token_found[0] = token
                print("\n[!] Clé d'accès trouvée ! Téléchargement de toute l'année...")

    page.on("request", handle_request)
    page.goto("https://auriga.ipsa.fr/")
    
    # Attend de trouver le token
    while not token_found[0]:
        page.wait_for_timeout(500)
        try:
            if page.is_closed():
                break
        except Exception:
            break
            
    if token_found[0]:
        # On va chercher par blocs de 4 semaines (28 jours)
        current = datetime.date(2026, 8, 24)
        end_year = datetime.date(2027, 7, 31)
        
        while current < end_year:
            chunk_end = current + datetime.timedelta(days=27)
            s_str = current.strftime("%Y-%m-%d")
            e_str = chunk_end.strftime("%Y-%m-%d")
            
            print(f"Récupération du {s_str} au {e_str}...")
            data = fetch_month(token_found[0], s_str, e_str)
            if data:
                captured_data.append({
                    "url": f"https://auriga.ipsa.fr/api/plannings/me?startDate={s_str}",
                    "data": data
                })
            
            current = chunk_end + datetime.timedelta(days=1)
            time.sleep(0.5)

        # Sauvegarde
        with open("planning_capture.json", "w", encoding="utf-8") as f:
            json.dump(captured_data, f, indent=4, ensure_ascii=False)
        
        print("\n[SUCCES] L'année complète a été sauvegardée dans 'planning_capture.json' !")
    else:
        print("Aucun accès trouvé. Navigateur fermé prématurément.")

    browser.close()

with sync_playwright() as playwright:
    run(playwright)

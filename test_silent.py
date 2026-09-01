import json
from playwright.sync_api import sync_playwright
import os

def run(playwright):
    if not os.path.exists("session_state.json"):
        print("Erreur: session_state.json manquant")
        return

    print("Lancement invisible (headless)...")
    browser = playwright.chromium.launch(headless=True)
    # On charge la session sauvegardée
    context = browser.new_context(storage_state="session_state.json")
    page = context.new_page()

    token_found = [None]
    def handle_request(request):
        if "plannings/me" in request.url and not token_found[0]:
            token = request.headers.get("authorization")
            if token:
                token_found[0] = token
                print("[!] Récupération invisible réussie !")

    page.on("request", handle_request)
    page.goto("https://auriga.ipsa.fr/")
    
    # On attend max 10 secondes pour voir si ça marche
    for _ in range(20):
        page.wait_for_timeout(500)
        if token_found[0]:
            break
            
    if token_found[0]:
        print("La session sauvegardée permet de rafraîchir sans mot de passe !")
    else:
        print("Échec: la session a probablement expiré ou n'est pas valide.")

    browser.close()

with sync_playwright() as playwright:
    run(playwright)

import json
from playwright.sync_api import sync_playwright

def run(playwright):
    browser = playwright.chromium.launch(headless=False)
    context = browser.new_context()
    page = context.new_page()

    print("-> Connecte-toi...")
    page.goto("https://auriga.ipsa.fr/")
    
    # On attend de voir un "plannings/me" avec un token
    token_found = [None]
    def handle_request(request):
        if "plannings/me" in request.url and not token_found[0]:
            token = request.headers.get("authorization")
            if token:
                token_found[0] = token
                print("[!] Token trouvé !")

    page.on("request", handle_request)
    
    while not token_found[0]:
        page.wait_for_timeout(500)
        try:
            if page.is_closed():
                break
        except Exception:
            break

    if token_found[0]:
        # Sauvegarde de la session (cookies, local storage)
        context.storage_state(path="session_state.json")
        print("[SUCCES] Session sauvegardée dans 'session_state.json' !")
        
    browser.close()

with sync_playwright() as playwright:
    run(playwright)

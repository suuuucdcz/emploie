"""Synchronisation Auriga : pilote un Chromium headless jusqu'au token, puis
aspire le planning via l'API et l'enregistre.

L'etat d'une synchronisation est expose sous un identifiant aleatoire, et non
sous l'adresse email : les captures d'ecran et le code A2F ne doivent pas etre
lisibles par quiconque connait l'email de quelqu'un.
"""

import base64
import secrets
import threading
import time

from playwright.sync_api import sync_playwright

import ics_builder
import storage

PORTAL_URL = "https://auriga.ipsa.fr/"
PLANNING_URL = "https://auriga.ipsa.fr/#/mainContent/menuEntry/227/planning"

ACTIVE_STATUSES = ("starting", "logging_in", "waiting_2fa", "downloading")
MAX_CONCURRENT_SYNCS = 2
MAX_STATES = 50
STATE_TTL_SECONDS = 3600

# Attente de la validation A2F : 15 tours de 2 s.
A2F_POLL_ROUNDS = 15
A2F_POLL_INTERVAL_MS = 2000
TOKEN_TIMEOUT_SECONDS = 30

_states = {}
_lock = threading.Lock()


class SyncBusy(Exception):
    """Le serveur ne peut pas accepter une synchronisation de plus."""


# --------------------------------------------------------------------------
# Etat partage
# --------------------------------------------------------------------------

def _prune_locked():
    """Purge les etats termines depuis longtemps, puis les plus anciens."""
    cutoff = time.time() - STATE_TTL_SECONDS
    for sync_id in [k for k, v in _states.items()
                    if v["status"] not in ACTIVE_STATUSES and v["updated_at"] < cutoff]:
        del _states[sync_id]

    if len(_states) > MAX_STATES:
        stale = sorted(_states.items(), key=lambda item: item[1]["updated_at"])
        for sync_id, state in stale[:len(_states) - MAX_STATES]:
            if state["status"] not in ACTIVE_STATUSES:
                del _states[sync_id]


def _update(sync_id, **fields):
    with _lock:
        state = _states.get(sync_id)
        if state is None:
            return
        state.update({k: v for k, v in fields.items() if v is not None})
        state["updated_at"] = time.time()


def _drop_screenshot(sync_id):
    """Libere la capture (plusieurs centaines de Ko) une fois la sync finie."""
    with _lock:
        if sync_id in _states:
            _states[sync_id]["screenshot"] = None


def get_status(sync_id):
    """Etat public d'une synchronisation, sans les champs internes."""
    with _lock:
        state = _states.get(sync_id)
        if state is None:
            return {"status": "unknown"}
        return {k: v for k, v in state.items() if k not in ("email", "created_at")}


def _active_count_locked():
    return sum(1 for s in _states.values() if s["status"] in ACTIVE_STATUSES)


# --------------------------------------------------------------------------
# Pilotage du navigateur
# --------------------------------------------------------------------------

def _screenshot(page):
    """Capture PNG en data URI, ou None si la page n'est plus capturable."""
    try:
        return "data:image/png;base64," + base64.b64encode(page.screenshot()).decode("ascii")
    except Exception:
        return None


def _click_microsoft(page, progress):
    """Passe l'ecran Keycloak « Se connecter avec Microsoft », s'il existe."""
    for label in ("Microsoft", "Office 365"):
        try:
            button = page.locator("text=%s" % label).first
            button.wait_for(timeout=4000)
            button.click()
            page.wait_for_timeout(3000)
            progress("Clic sur %s effectue" % label, page)
            return
        except Exception:
            continue
    progress("Aucun bouton Microsoft, page de login directe", page)


def _pick_2fa_method(page, progress):
    """Microsoft demande parfois de choisir la methode A2F avant d'afficher le code."""
    selector = ('div[data-value="PhoneAppNotification"], div[data-value="PhoneAppOTP"], '
                'div.table-row:has-text("Approve a request"), '
                'div.table-row:has-text("Approuver")')
    try:
        option = page.locator(selector).first
        if option.count() > 0:
            progress("Choix de la methode A2F...", page)
            option.click()
            page.wait_for_timeout(2000)
    except Exception:
        pass


def _read_2fa_code(page):
    """Numero a taper sur le telephone (number matching), ou None."""
    try:
        element = page.locator(".displaySign")
        element.wait_for(timeout=8000)
        return (element.text_content() or "").strip() or None
    except Exception:
        return None


def _confirm_stay_signed_in(page, progress):
    """Attend la validation A2F puis coche « Rester connecte ». True si trouve."""
    selector = ('input[type="button"][value="Oui"], input[type="submit"][value="Oui"], '
                'input[id="idSIButton9"]')
    for _ in range(A2F_POLL_ROUNDS):
        try:
            button = page.locator(selector)
            if button.count() > 0 and button.first.is_visible():
                button.first.click()
                progress("Rester connecte : OK", page)
                return True
        except Exception:
            pass
        progress("Attente de la validation A2F...", page)
        page.wait_for_timeout(A2F_POLL_INTERVAL_MS)
    return False


def _open_planning(page, progress):
    try:
        link = page.locator("text=Mon planning").first
        link.wait_for(timeout=10000)
        link.click()
        progress("Ouverture de Mon planning", page)
    except Exception:
        page.goto(PLANNING_URL)
        progress("Navigation directe vers le planning", page)
    page.wait_for_timeout(3000)


def _login(page, sync_id, email, password, progress, fail):
    """Deroule le login Microsoft. Renvoie False si une etape bloquante echoue."""
    progress("Ouverture du portail Auriga...", page)
    page.goto(PORTAL_URL, timeout=15000)
    page.wait_for_timeout(3000)

    _click_microsoft(page, progress)

    try:
        page.locator('input[type="email"]').wait_for(timeout=10000)
        page.locator('input[type="email"]').fill(email)
        page.locator('input[type="submit"]').click()
        page.wait_for_timeout(2000)
        progress("Email envoye, attente du mot de passe...", page)
    except Exception as exc:
        fail("Champ email introuvable : %s" % exc, page)
        return False

    try:
        page.locator('input[type="password"]').wait_for(timeout=10000)
        page.locator('input[type="password"]').fill(password)
        page.locator('input[type="submit"]').click()
    except Exception as exc:
        fail("Champ mot de passe introuvable : %s" % exc, page)
        return False

    _pick_2fa_method(page, progress)

    code = _read_2fa_code(page)
    if code:
        _update(sync_id, status="waiting_2fa", code=code,
                detail="Code A2F : %s" % code, screenshot=_screenshot(page))
    else:
        _update(sync_id, status="waiting_2fa",
                code="Approuvez sur votre telephone",
                detail="Aucun numero affiche", screenshot=_screenshot(page))

    _confirm_stay_signed_in(page, progress)
    return True


def _wait_for_token(page, token_box, progress):
    for remaining in range(TOKEN_TIMEOUT_SECONDS, 0, -1):
        if token_box[0]:
            return True
        progress("Attente du token... (%ds)" % remaining, page)
        page.wait_for_timeout(1000)
    return bool(token_box[0])


def _run_sync(sync_id, email, password):
    def progress(detail, page=None, status="logging_in"):
        _update(sync_id, status=status, detail=detail,
                screenshot=_screenshot(page) if page else None)
        print("[sync %s] %s" % (sync_id[:8], detail))

    def fail(message, page=None):
        _update(sync_id, status="error", error_msg=message,
                screenshot=_screenshot(page) if page else None)
        print("[sync %s] erreur : %s" % (sync_id[:8], message))

    browser = None
    try:
        with sync_playwright() as playwright:
            progress("Lancement du navigateur...")
            browser = playwright.chromium.launch(headless=True)
            context = browser.new_context(viewport={"width": 800, "height": 800})
            page = context.new_page()

            token_box = [None]

            def capture_token(request):
                if "plannings/me" in request.url and not token_box[0]:
                    token_box[0] = request.headers.get("authorization")

            page.on("request", capture_token)

            if not _login(page, sync_id, email, password, progress, fail):
                return

            _open_planning(page, progress)

            progress("Attente du token d'authentification...")
            if not _wait_for_token(page, token_box, progress):
                fail("Token non recupere apres %ds (A2F non validee ?)"
                     % TOKEN_TIMEOUT_SECONDS, page)
                return

            progress("Telechargement du planning...", status="downloading")
            payloads = ics_builder.fetch_all(token_box[0])
            events = ics_builder.extract_events(payloads)
            if not events:
                fail("Aucun cours recupere depuis l'API Auriga.", page)
                return

            destination = storage.save_schedule(email, ics_builder.build_ics(events))
            _drop_screenshot(sync_id)
            _update(sync_id, status="success",
                    detail="%d cours enregistres (%s)" % (len(events), destination))
            print("[sync %s] termine : %d cours -> %s"
                  % (sync_id[:8], len(events), destination))
    except Exception as exc:
        fail(str(exc))
    finally:
        if browser is not None:
            try:
                browser.close()
            except Exception:
                pass


# --------------------------------------------------------------------------
# API publique
# --------------------------------------------------------------------------

def start_sync(email, password):
    """Lance une synchronisation et renvoie son identifiant.

    Leve ValueError si les identifiants sont absents ou malformes, SyncBusy si
    le serveur est deja occupe.
    """
    if not email or not password:
        raise ValueError("Email et mot de passe requis.")
    storage.cache_key(email)  # leve ValueError si l'email est invalide

    sync_id = secrets.token_urlsafe(24)
    with _lock:
        _prune_locked()
        if _active_count_locked() >= MAX_CONCURRENT_SYNCS:
            raise SyncBusy("Trop de synchronisations en cours, reessaie dans une minute.")
        if any(s["email"] == email and s["status"] in ACTIVE_STATUSES
               for s in _states.values()):
            raise SyncBusy("Une synchronisation est deja en cours pour ce compte.")

        _states[sync_id] = {
            "status": "starting",
            "code": None,
            "detail": "Demarrage...",
            "error_msg": None,
            "screenshot": None,
            "email": email,
            "created_at": time.time(),
            "updated_at": time.time(),
        }

    thread = threading.Thread(target=_run_sync, args=(sync_id, email, password),
                              daemon=True)
    thread.start()
    return sync_id

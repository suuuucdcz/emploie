"""Moteur de synchronisation Edusign.

Remplace l'ancien ouvrier Playwright lourd par un connecteur HTTP direct :
  - Connexion immediate (0.1s)
  - Telechargement complet de l'annee scolaire (< 0.5s)
  - Zero consommation de RAM Chromium
  - Aucun A2F requis
"""

import secrets
import threading
import time

import edusign_client
import storage

ACTIVE_STATUSES = ("starting", "downloading")
MAX_CONCURRENT_SYNCS = 4
MAX_STATES = 50
STATE_TTL_SECONDS = 3600
MAX_ACTIVE_TIMEOUT = 180  # 3 minutes max

_states = {}
_lock = threading.Lock()


class SyncBusy(Exception):
    """Le serveur ne peut pas accepter une synchronisation de plus."""


def _prune_locked():
    """Purge les etats termines ou abandonnes."""
    now = time.time()
    # Passer en erreur les synchronisations qui depassent le delai maximal
    for sync_id, state in _states.items():
        if state["status"] in ACTIVE_STATUSES and (now - state["updated_at"]) > MAX_ACTIVE_TIMEOUT:
            state["status"] = "error"
            state["error_msg"] = "Délai de synchronisation dépassé (timeout)."
            state["timed_out"] = True
            state["updated_at"] = now

    cutoff = now - STATE_TTL_SECONDS
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
        if state.get("timed_out"):
            return
        state.update({k: v for k, v in fields.items() if v is not None})
        state["updated_at"] = time.time()


def get_status(sync_id, device_id=None):
    """Etat public d'une synchronisation."""
    with _lock:
        _prune_locked()
        state = _states.get(sync_id)
        if state is None or not storage.session_matches_device_id(state.get("device_id"), device_id):
            return {"status": "unknown"}
        return {k: v for k, v in state.items()
                if k not in ("email", "device_id", "created_at", "updated_at", "timed_out")}


def _active_count_locked():
    return sum(1 for s in _states.values() if s["status"] in ACTIVE_STATUSES)


def _is_timed_out(sync_id):
    with _lock:
        state = _states.get(sync_id)
        return bool(state and state.get("timed_out"))


def _run_sync(sync_id, email, password, device_id):
    def progress(detail, status="downloading"):
        _update(sync_id, status=status, detail=detail)
        print(f"[sync {sync_id[:8]}] {detail}")

    def fail(message):
        _update(sync_id, status="error", error_msg=message)
        print(f"[sync {sync_id[:8]}] erreur : {message}")

    try:
        progress("Connexion a Edusign...")
        result = edusign_client.sync_schedule(
            email, password, device_id=device_id,
            is_cancelled=lambda: _is_timed_out(sync_id),
        )
        password = None  # purge immediate du mot de passe en memoire
        count = result["count"]
        destination = result["destination"]
        user = result.get("user", {})
        user_name = f"{user.get('firstName', '')} {user.get('lastName', '')}".strip()

        detail_msg = f"{count} cours synchronises ({destination})"
        if user_name:
            detail_msg = f"Compte de {user_name} : {detail_msg}"

        _update(
            sync_id,
            status="success",
            detail=detail_msg,
            count=count,
            destination=destination,
            user=user,
        )
        print(f"[sync {sync_id[:8]}] termine : {count} cours -> {destination}")
    except edusign_client.EdusignError as exc:
        fail(str(exc))
    except Exception as exc:
        fail(f"Erreur inattendue : {exc}")
    finally:
        password = None


def start_sync(email, password=None, device_id=None):
    """Lance une synchronisation Edusign et renvoie son identifiant.
    
    Le mot de passe est optionnel si une session active (Option B) est enregistree.
    """
    clean_email = storage.validate_and_normalize_email(email)
    clean_device_id = storage.validate_device_id(device_id)

    # Sans mot de passe, seule la meme installation de navigateur peut reutiliser
    # la session memorisee. L'email seul ne doit jamais donner acces a un agenda.
    if not password and not storage.session_matches_device(clean_email, clean_device_id):
        raise ValueError("Mot de passe Edusign requis pour cet appareil.")

    sync_id = secrets.token_urlsafe(24)
    with _lock:
        _prune_locked()
        if _active_count_locked() >= MAX_CONCURRENT_SYNCS:
            raise SyncBusy("Trop de synchronisations en cours, reessaie dans quelques secondes.")
        if any(s["email"] == clean_email and s["status"] in ACTIVE_STATUSES
               for s in _states.values()):
            raise SyncBusy("Une synchronisation est deja en cours pour ce compte.")

        _states[sync_id] = {
            "status": "starting",
            "detail": "Demarrage de la synchronisation...",
            "error_msg": None,
            "email": clean_email,
            "device_id": clean_device_id,
            "created_at": time.time(),
            "updated_at": time.time(),
        }

    thread = threading.Thread(
        target=_run_sync, args=(sync_id, clean_email, password, clean_device_id), daemon=True
    )
    thread.start()
    return sync_id

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

_states = {}
_lock = threading.Lock()


class SyncBusy(Exception):
    """Le serveur ne peut pas accepter une synchronisation de plus."""


def _prune_locked():
    """Purge les etats termines depuis longtemps."""
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


def get_status(sync_id):
    """Etat public d'une synchronisation."""
    with _lock:
        state = _states.get(sync_id)
        if state is None:
            return {"status": "unknown"}
        return {k: v for k, v in state.items() if k not in ("email", "created_at")}


def _active_count_locked():
    return sum(1 for s in _states.values() if s["status"] in ACTIVE_STATUSES)


def _run_sync(sync_id, email, password):
    def progress(detail, status="downloading"):
        _update(sync_id, status=status, detail=detail)
        print(f"[sync {sync_id[:8]}] {detail}")

    def fail(message):
        _update(sync_id, status="error", error_msg=message)
        print(f"[sync {sync_id[:8]}] erreur : {message}")

    try:
        progress("Connexion a Edusign...")
        result = edusign_client.sync_schedule(email, password)
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


def start_sync(email, password):
    """Lance une synchronisation Edusign et renvoie son identifiant."""
    if not email or not password:
        raise ValueError("Email et mot de passe requis.")
    storage.cache_key(email)  # valide le format d'email

    sync_id = secrets.token_urlsafe(24)
    with _lock:
        _prune_locked()
        if _active_count_locked() >= MAX_CONCURRENT_SYNCS:
            raise SyncBusy("Trop de synchronisations en cours, reessaie dans quelques secondes.")
        if any(s["email"] == email and s["status"] in ACTIVE_STATUSES
               for s in _states.values()):
            raise SyncBusy("Une synchronisation est deja en cours pour ce compte.")

        _states[sync_id] = {
            "status": "starting",
            "detail": "Demarrage de la synchronisation...",
            "error_msg": None,
            "email": email,
            "created_at": time.time(),
            "updated_at": time.time(),
        }

    thread = threading.Thread(target=_run_sync, args=(sync_id, email, password), daemon=True)
    thread.start()
    return sync_id

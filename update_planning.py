"""Mise a jour manuelle de l'emploi du temps via Edusign.

Synchronise directement les cours depuis l'API Edusign :
    python update_planning.py

Les identifiants peuvent provenir des variables EDUSIGN_EMAIL / EDUSIGN_PASSWORD
ou AURIGA_EMAIL / AURIGA_PASSWORD (fichier .env ou environnement).
A defaut, ils sont demandes interactivement.
Si une session est deja active (Option B), le mot de passe est optionnel !
"""

import getpass
import os
import sys
import time
import uuid

import envfile
import storage
import sync_worker

POLL_SECONDS = 0.5
TERMINAL_STATUSES = ("success", "error", "unknown")


def credentials():
    """(email, mot de passe, appareil), depuis l'environnement ou saisis au clavier."""
    envfile.load()
    email = (
        os.environ.get("EDUSIGN_EMAIL")
        or os.environ.get("AURIGA_EMAIL")
        or input("Email de l'ecole : ").strip()
    )
    email = storage.validate_and_normalize_email(email)

    password = os.environ.get("EDUSIGN_PASSWORD") or os.environ.get("AURIGA_PASSWORD")
    if not password:
        if storage.has_session(email):
            pwd = getpass.getpass("Mot de passe Edusign (laisser vide pour actualiser via la session) : ")
            password = pwd if pwd else None
        else:
            password = getpass.getpass("Mot de passe Edusign : ")

    # Le CLI reutilise l'identifiant Edusign deja memorise ou en cree un lors
    # de la premiere connexion. Il reste ainsi compatible avec la protection
    # des sessions liees a un appareil du serveur HTTP.
    _, device_id = storage.get_session(email)
    return email, password, device_id or str(uuid.uuid4())


def main():
    print("=" * 55)
    print("MISE A JOUR DE L'EMPLOI DU TEMPS VIA EDUSIGN")
    print("=" * 55)

    try:
        email, password, device_id = credentials()
        sync_id = sync_worker.start_sync(email, password, device_id)
    except (ValueError, sync_worker.SyncBusy) as exc:
        print("Impossible de demarrer : %s" % exc)
        return 1

    last_detail = None
    while True:
        state = sync_worker.get_status(sync_id)
        detail = state.get("detail")
        if detail and detail != last_detail:
            last_detail = detail
            print(" -> %s" % detail)

        if state["status"] in TERMINAL_STATUSES:
            break
        time.sleep(POLL_SECONDS)

    if state["status"] == "success":
        print("\nTERMINE ! Ton planning Edusign est a jour.")
        return 0

    print("\nECHEC : %s" % (state.get("error_msg") or state["status"]))
    return 1


if __name__ == "__main__":
    sys.exit(main())

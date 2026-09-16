"""Mise a jour manuelle de l'emploi du temps via Edusign.

Synchronise directement les cours depuis l'API Edusign :
    python update_planning.py

Les identifiants proviennent de AURIGA_EMAIL et AURIGA_PASSWORD (fichier .env ou
variables d'environnement). A defaut, ils sont demandes au clavier.
"""

import getpass
import os
import sys
import time

import envfile
import sync_worker

POLL_SECONDS = 0.5
TERMINAL_STATUSES = ("success", "error", "unknown")


def credentials():
    """(email, mot de passe), depuis l'environnement ou saisis au clavier."""
    envfile.load()
    email = os.environ.get("AURIGA_EMAIL") or input("Email de l'ecole : ").strip()
    password = os.environ.get("AURIGA_PASSWORD") or getpass.getpass("Mot de passe Edusign : ")
    return email, password


def main():
    print("=" * 55)
    print("MISE A JOUR DE L'EMPLOI DU TEMPS VIA EDUSIGN")
    print("=" * 55)

    email, password = credentials()
    try:
        sync_id = sync_worker.start_sync(email, password)
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

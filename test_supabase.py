"""Verifie l'aller-retour Supabase via storage.py : python test_supabase.py

Les identifiants sont en dur ici pour pouvoir tester sans configurer
l'environnement ; ils ne sont utilises que si SUPABASE_URL / SUPABASE_KEY ne
sont pas deja definis.
"""

import os

os.environ.setdefault("SUPABASE_URL", "https://dzcixbkxzjmtoiqgibni.supabase.co")
os.environ.setdefault(
    "SUPABASE_KEY",
    "CLE_SUPABASE_RETIREE"
    "CLE_SUPABASE_RETIREE"
    "CLE_SUPABASE_RETIREE"
    "CLE_SUPABASE_RETIREE",
)

import storage  # noqa: E402  (doit voir les variables ci-dessus)

TEST_EMAIL = "test@ipsa.fr"
TEST_ICS = "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nEND:VCALENDAR\r\n"


def main():
    url, _ = storage.supabase_config()
    if not url:
        print("Supabase non configure.")
        return 1

    print("Ecriture...")
    print("  -> %s" % storage.save_schedule(TEST_EMAIL, TEST_ICS))

    print("Lecture...")
    content, source = storage.load_schedule(TEST_EMAIL)

    if content.strip() != TEST_ICS.strip():
        print("ECHEC : contenu relu different (source : %s)" % source)
        return 1

    print("OK : aller-retour reussi depuis %s" % source)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

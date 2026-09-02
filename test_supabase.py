"""Verifie l'aller-retour Supabase via storage.py : python test_supabase.py

Necessite SUPABASE_URL et SUPABASE_KEY (fichier .env local ou variables
d'environnement). Ce test ecrit vraiment dans la table `schedules`, sur la
ligne `test@ipsa.fr`.
"""

import storage

TEST_EMAIL = "test@ipsa.fr"
TEST_ICS = "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nEND:VCALENDAR\r\n"


def main():
    url, _ = storage.supabase_config()
    if not url:
        print("Supabase non configure : definis SUPABASE_URL et SUPABASE_KEY")
        print("(dans un fichier .env a la racine, voir .env.example).")
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

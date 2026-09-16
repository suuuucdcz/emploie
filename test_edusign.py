import edusign_client
import ics
import storage

MOCK_COURSES = [
    {
        "ID": "course_1",
        "NAME": "Travaux diriges - Mecanique du vol",
        "START": "2026-09-30T10:00:00.000Z",
        "END": "2026-09-30T12:00:00.000Z",
        "CLASSROOM": "Amphi 3",
        "PROFESSOR": "prof_1",
        "DESCRIPTION": "Groupe AERO4",
    },
    {
        "ID": "course_2",
        "NAME": "Cours magistral - Aerodynamique",
        "START": "2026-10-01T08:00:00.000Z",
        "END": "2026-10-01T10:00:00.000Z",
        "CLASSROOM": "Salle 102",
        "PROFESSOR": "prof_2",
        "DESCRIPTION": "",
    },
]

MOCK_PROFS = {
    "prof_1": "Jean DUPONT",
    "prof_2": "Marie CURIE",
}


def test():
    print("securite et validation des entrees")
    # Normalisation
    assert storage.validate_and_normalize_email("  Etudiant.Test@IPSA.fr  ") == "etudiant.test@ipsa.fr"
    # Rejets d'adresses invalides
    for bad in ["", "notanemail", "../traversal@test.com", "user@domain", "a" * 300 + "@ipsa.fr"]:
        try:
            storage.validate_and_normalize_email(bad)
            assert False, f"L'adresse invalide {bad!r} aurait du etre rejetee"
        except ValueError:
            pass
    print("  ok  validation stricte des adresses email")

    print("conversion edusign -> evenements normalises")
    events = edusign_client.edusign_to_events(MOCK_COURSES, MOCK_PROFS)
    assert len(events) == 2, f"Attendu 2 cours, obtenu {len(events)}"
    assert events[0]["location"] == "Amphi 3"
    assert "Enseignant : Jean DUPONT" in events[0]["description"]
    assert "Groupe AERO4" in events[0]["description"]
    print("  ok  evenements correctement formates")

    print("serialisation ICS et relecture")
    ics_text = edusign_client.ics_builder.build_ics(events)
    parsed = ics.parse(ics_text)
    assert len(parsed) == 2, f"Attendu 2 cours relus, obtenu {len(parsed)}"
    assert parsed[0]["location"] == "Amphi 3"
    assert parsed[0]["teacher"] == "Jean DUPONT"
    assert parsed[0]["kind"] == "TD"
    print("  ok  relecture par le parseur interne reussie")

    print("dates universitaires")
    start_iso, end_iso = edusign_client.default_academic_dates()
    assert start_iso.endswith("T00:00:00.000Z")
    assert end_iso.endswith("T00:00:00.000Z")
    print("  ok  dates universitaires conformes")

    print("gestion de session (Option B)")
    test_email = "Test_User_Session@ipsa.fr"
    test_rt = "mock_refresh_token_12345"
    test_did = "mock_device_id_abcdef"

    storage.save_schedule(test_email, ics_text, refresh_token=test_rt, device_id=test_did)
    # Verifier acces insensible a la casse
    assert storage.has_session("test_user_session@ipsa.fr") is True
    rt_loaded, did_loaded = storage.get_session("  TEST_USER_SESSION@ipsa.fr ")
    assert rt_loaded == test_rt
    assert did_loaded == test_did
    print("  ok  sauvegarde et relecture session reussies (avec normalisation)")

    storage.clear_session("test_user_session@ipsa.fr")
    assert storage.has_session(test_email) is False
    print("  ok  suppression de session reussie")

    print("\nTous les tests Edusign, Sessions et Securite passent avec succes.")


if __name__ == "__main__":
    test()

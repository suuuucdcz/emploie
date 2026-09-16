"""Tests de regression pour les correctifs issus de l'audit."""

import json
import tempfile
import threading
import time
import unittest
from http.client import HTTPConnection
from unittest import mock
from urllib.parse import urlencode

import edusign_client
import ics
import server
import storage
import sync_worker


EMAIL = "etudiant@example.edu"
DEVICE_ID = "67e55044-10b1-426f-9247-bb680e5fe0c8"
OTHER_DEVICE_ID = "c56a4180-65aa-42ec-a945-5fd21dec0538c"
SIMPLE_ICS = """BEGIN:VCALENDAR\r
BEGIN:VEVENT\r
UID:example\r
DTSTART:20260907T080000Z\r
DTEND:20260907T100000Z\r
SUMMARY:Test\r
END:VEVENT\r
END:VCALENDAR\r
"""


class LocalStorageTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cache_dir = mock.patch.object(storage, "CACHE_DIR", self.tmp.name)
        self.no_supabase = mock.patch.object(storage, "supabase_config", return_value=(None, None))
        self.cache_dir.start()
        self.no_supabase.start()

    def tearDown(self):
        self.no_supabase.stop()
        self.cache_dir.stop()
        self.tmp.cleanup()

    def test_cache_key_is_stable_private_and_collision_resistant(self):
        self.assertEqual(storage.cache_key(EMAIL), storage.cache_key(EMAIL.upper()))
        self.assertNotIn("etudiant", storage.cache_path(EMAIL))
        self.assertNotEqual(
            storage.cache_key("a+b@example.edu"),
            storage.cache_key("a_b@example.edu"),
        )

    def test_session_is_bound_to_its_browser_device(self):
        storage.save_schedule(EMAIL, SIMPLE_ICS, refresh_token="secret", device_id=DEVICE_ID)
        self.assertTrue(storage.session_matches_device(EMAIL, DEVICE_ID))
        self.assertFalse(storage.session_matches_device(EMAIL, OTHER_DEVICE_ID))

    def test_schedule_endpoint_requires_the_bound_device(self):
        storage.save_schedule(EMAIL, SIMPLE_ICS, refresh_token="secret", device_id=DEVICE_ID)
        server._caches.clear()
        original_config = server.Handler.config
        server.Handler.config = dict(server.DEFAULTS)
        httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        httpd.daemon_threads = True
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            path = "/api/schedule?" + urlencode({"email": EMAIL})
            conn = HTTPConnection("127.0.0.1", httpd.server_address[1], timeout=3)
            conn.request("GET", path)
            response = conn.getresponse()
            self.assertEqual(response.status, 401)
            self.assertEqual(json.loads(response.read()), {
                "events": [], "error": "Connexion requise pour cet appareil.",
            })
            conn.close()

            conn = HTTPConnection("127.0.0.1", httpd.server_address[1], timeout=3)
            conn.request("GET", path, headers={"X-Auriga-Device-Id": DEVICE_ID})
            response = conn.getresponse()
            payload = json.loads(response.read())
            self.assertEqual(response.status, 200)
            self.assertEqual(len(payload["events"]), 1)
            self.assertEqual(response.getheader("Vary"), "X-Auriga-Device-Id")
            conn.close()
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=3)
            server.Handler.config = original_config
            server._caches.clear()


class SyncAndCalendarTestCase(unittest.TestCase):
    def setUp(self):
        self.old_states = sync_worker._states.copy()
        sync_worker._states.clear()

    def tearDown(self):
        sync_worker._states.clear()
        sync_worker._states.update(self.old_states)

    def test_sync_status_is_private_and_timeout_is_observable(self):
        sync_id = "sync-id"
        sync_worker._states[sync_id] = {
            "status": "starting",
            "detail": "Demarrage",
            "error_msg": None,
            "email": EMAIL,
            "device_id": DEVICE_ID,
            "updated_at": time.time() - sync_worker.MAX_ACTIVE_TIMEOUT - 1,
        }
        self.assertEqual(sync_worker.get_status(sync_id, OTHER_DEVICE_ID), {"status": "unknown"})
        status = sync_worker.get_status(sync_id, DEVICE_ID)
        self.assertEqual(set(status), {"status", "detail", "error_msg"})
        self.assertEqual(status["status"], "error")
        self.assertIn("timeout", status["error_msg"])

    def test_password_login_does_not_reuse_a_different_device_session(self):
        course = {
            "ID": "1", "START": "2026-09-07T08:00:00Z", "END": "2026-09-07T10:00:00Z",
            "NAME": "Maths",
        }
        with mock.patch("edusign_client.login", return_value=("access", "refresh", DEVICE_ID, {})) as login, \
             mock.patch("edusign_client.fetch_planning", return_value=[course]), \
             mock.patch("edusign_client.fetch_professors", return_value={}), \
             mock.patch("edusign_client.fetch_absence_statistics", return_value=None), \
             mock.patch("edusign_client.storage.save_schedule", return_value="cache local"), \
             mock.patch("edusign_client.storage.get_session") as get_session:
            edusign_client.sync_schedule(EMAIL, password="mot-de-passe", device_id=DEVICE_ID)

        login.assert_called_once_with(EMAIL, "mot-de-passe", DEVICE_ID)
        get_session.assert_not_called()

    def test_daily_byday_rule_filters_weekdays(self):
        calendar = """BEGIN:VCALENDAR\r
BEGIN:VEVENT\r
UID:daily\r
DTSTART:20260907T080000Z\r
DTEND:20260907T090000Z\r
RRULE:FREQ=DAILY;BYDAY=MO,WE;COUNT=3\r
SUMMARY:Atelier\r
END:VEVENT\r
END:VCALENDAR\r
"""
        events = ics.parse(calendar)
        self.assertEqual(
            [event["start"][:10] for event in events],
            ["2026-09-07", "2026-09-09", "2026-09-14"],
        )

    def test_config_rejects_an_invalid_host_port(self):
        with mock.patch.dict("os.environ", {"PORT": "70000"}, clear=False):
            with self.assertRaises(ValueError):
                server.load_config([])


if __name__ == "__main__":
    unittest.main()

import urllib.request
import json
import os

url = "https://dzcixbkxzjmtoiqgibni.supabase.co"
key = "CLE_SUPABASE_RETIREE"

try:
    print("Test POST...")
    req = urllib.request.Request(f"{url}/rest/v1/schedules", headers={
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "Prefer": "resolution=merge-duplicates"
    }, data=json.dumps({"email": "test@ipsa.fr", "ics_content": "BEGIN:VCALENDAR"}).encode("utf-8"))
    req.get_method = lambda: 'POST'
    urllib.request.urlopen(req)
    print("POST: OK")

    print("Test GET...")
    req2 = urllib.request.Request(f"{url}/rest/v1/schedules?email=eq.test@ipsa.fr&select=ics_content", headers={
        "apikey": key,
        "Authorization": f"Bearer {key}"
    })
    res = urllib.request.urlopen(req2).read().decode('utf-8')
    print("GET:", res)

except Exception as e:
    if hasattr(e, 'read'):
        print("Erreur body:", e.read().decode('utf-8'))
    print("Erreur:", str(e))

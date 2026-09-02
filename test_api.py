import urllib.request
import urllib.error

try:
    print(urllib.request.urlopen('https://emploie-ls9z.onrender.com/api/schedule?email=mathis.derory@ipsa.fr').read().decode('utf-8'))
except urllib.error.HTTPError as e:
    print(e.read().decode('utf-8'))

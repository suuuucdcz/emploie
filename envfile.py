"""Chargement d'un fichier `.env` local, sans dependance externe.

En hebergement (Render) les variables sont fournies par la plateforme et ce
fichier n'existe pas : `load()` ne fait alors rien. En local il evite d'ecrire
le moindre identifiant dans le code.

Une variable deja presente dans l'environnement n'est jamais ecrasee.
"""

import os

ROOT = os.path.dirname(os.path.abspath(__file__))
ENV_PATH = os.path.join(ROOT, ".env")

_loaded = False


def load(path=ENV_PATH):
    """Lit `path` et complete os.environ. Idempotent, silencieux si absent."""
    global _loaded
    if _loaded:
        return
    _loaded = True

    if not os.path.exists(path):
        return

    try:
        with open(path, "r", encoding="utf-8") as handle:
            lines = handle.readlines()
    except OSError as exc:
        print("[envfile] %s illisible : %s" % (path, exc))
        return

    for number, line in enumerate(lines, 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, sep, value = line.partition("=")
        if not sep:
            print("[envfile] ligne %d ignoree (pas de '=')" % number)
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        os.environ.setdefault(key.strip(), value)

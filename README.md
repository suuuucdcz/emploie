# Emploi du temps IPSA (Edusign & Auriga)

Une PWA ultra-rapide pour consulter son emploi du temps IPSA sur téléphone et ordinateur, sans passer par les portails lents ou les validations Microsoft A2F répétitives.

L'application communique directement avec l'API REST Edusign :
1. **Connexion directe** : Récupération instantanée du token d'accès (< 0.2s) sans passer par Microsoft SSO ni A2F.
2. **Session persistante (Option B)** : Le `refresh_token` est conservé de façon sécurisée (Supabase ou cache local chiffré/protégé). L'actualisation de l'agenda se fait ensuite **en 1 clic sans retaper son mot de passe**.
3. **Synchronisation annuelle complète** : L'intégralité de l'année scolaire (plus de 180 cours) et la liste des professeurs sont téléchargées en un seul appel (< 0.5s).
4. **Zéro dépendance externe** : 100% bibliothèque standard Python (aucun navigateur Chromium ni Playwright requis, consommation RAM minime ~30 Mo).

---

## Démarrage rapide

```bash
python server.py
```

Puis ouvre <http://localhost:8787>, saisis ton email de l'école et ton mot de passe Edusign.
Dès la première synchronisation :
- Ton planning est téléchargé et mis en cache.
- Une session est mémorisée : les prochaines actualisations se feront en 1 clic ("Mettre à jour en 1 clic") sans mot de passe !

---

## Configuration

`config.json` configure les paramètres locaux de base :

| Clé | Défaut | Rôle |
| --- | --- | --- |
| `port` | `8787` | port d'écoute (`$PORT` de l'hébergeur a la priorité) |
| `refresh_seconds` | `900` | durée de vie du cache mémoire d'un agenda (15 min) |

Toutes les variables sensibles passent par `.env` (en local, voir `.env.example`) ou par le tableau de bord de l'hébergeur (Render) :

| Variable | Effet |
| --- | --- |
| `SUPABASE_URL` + `SUPABASE_KEY` | Persistance distante des agendas et sessions dans la table `schedules` |
| (aucune des deux) | Repli automatique sur le stockage local `cache/` |
| `EDUSIGN_EMAIL` + `EDUSIGN_PASSWORD` | Identifiants pour `update_planning.py` (ou `AURIGA_EMAIL` / `AURIGA_PASSWORD`) |
| `PORT` | Port d'écoute imposé par Render |

### Schéma Supabase recommandé (Option B)

Dans votre projet Supabase (éditeur SQL) :
```sql
create table if not exists schedules (
  email text primary key,
  ics_content text not null,
  refresh_token text,
  device_id text,
  updated_at timestamp with time zone default timezone('utc'::text, now()) not null
);
```

---

## Sécurité et Confidentialité

- **Mots de passe** : Le mot de passe ne transite qu'en mémoire vive lors de la connexion initiale vers l'API officielle Edusign en HTTPS. Il n'est **jamais** écrit sur disque, jamais journalisé et jamais renvoyé au navigateur.
- **Sessions & Tokens (Option B)** : Seuls le `refresh_token` et le `device_id` sont conservés. L'utilisateur peut à tout moment révoquer et effacer sa session via le bouton *"Oublier la session"* dans l'interface ou via `POST /api/session/clear`.
- **Validation stricte des entrées** : Toutes les adresses email sont strictement validées (regex RFC) et normalisées en minuscules pour interdire toute injection PostgREST ou path traversal.
- **En-têtes HTTP de durcissement** : `Content-Security-Policy`, `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: strict-origin-when-cross-origin`.
- **Limiteur de débit (Rate Limiting)** : Protection intégrée contre le bruteforce ou le spam d'actualisations.

---

## Architecture des fichiers

| Fichier | Rôle |
| --- | --- |
| `server.py` | Serveur HTTP sécurisé : PWA, `/api/schedule`, `/api/sync/*`, `/api/session/*` |
| `edusign_client.py` | Client REST Edusign : login, refresh de jeton, planning et professeurs |
| `storage.py` | Gestionnaire de persistance sécurisé (Supabase + repli cache local atomique) |
| `sync_worker.py` | Orchestrateur de synchronisation asynchrone en arrière-plan |
| `ics_builder.py` | Sérialiseur ICS conforme RFC 5545 (pliage 75 octets, horodatages UTC) |
| `ics.py` | Analyseur RFC 5545 autonome : événements, récurrences RRULE, détection CM/TD/TP |
| `public/` | Interface PWA progressive (`index.html`, `app.js`, `styles.css`, `sw.js`) |
| `update_planning.py` | Outil CLI pour synchroniser manuellement son planning |

---

## Tests unitaires

Pour lancer l'ensemble des suites de tests automatisés :

```bash
python test_edusign.py
python test_ics.py
python test_ics_builder.py
```

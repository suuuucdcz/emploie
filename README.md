# Emploi du temps Auriga

Une PWA pour consulter son emploi du temps Aurion (portail `auriga.ipsa.fr`)
sur telephone, sans repasser par le portail web a chaque fois.

Aurion n'expose pas de flux iCal utilisable. L'appli contourne ca en pilotant
un navigateur headless : il se connecte au portail avec tes identifiants
Microsoft, intercepte le token de l'API interne `/api/plannings/me`, aspire
deux ans de planning, et enregistre le tout en ICS. La PWA lit ensuite cet ICS
converti en JSON.

## Demarrage

```bash
pip install -r requirements.txt
playwright install chromium
python server.py
```

Puis ouvre <http://localhost:8787>, saisis ton email et ton mot de passe de
l'ecole, et lance la recuperation. Le robot affiche en direct ce qu'il voit et
le numero A2F a taper sur ton telephone. Une fois termine, l'agenda est
enregistre et la page se recharge.

Seul l'email est memorise dans le navigateur : le mot de passe n'est jamais
stocke, il faut le retaper a chaque synchronisation.

## Configuration

`config.json` ne contient aucun secret :

| Cle | Defaut | Role |
| --- | --- | --- |
| `port` | `8787` | port d'ecoute (`$PORT` de l'hebergeur a la priorite) |
| `refresh_seconds` | `900` | duree de vie du cache memoire d'un agenda |

Tout le reste passe par des variables d'environnement. En local, copie
`.env.example` en `.env` et remplis-le — le fichier est lu au demarrage et il
est dans le `.gitignore`. En hebergement, definis-les dans le dashboard Render.

| Variable | Effet |
| --- | --- |
| `SUPABASE_URL` + `SUPABASE_KEY` | agendas ranges dans la table `schedules` |
| (aucune des deux) | repli sur le dossier local `cache/` |
| `AURIGA_EMAIL` + `AURIGA_PASSWORD` | identifiants de `update_planning.py` ; sinon demandes au clavier |
| `PORT` | surcharge le port de `config.json` |

Supabase est necessaire en hebergement : le disque de Render est ephemere, un
redemarrage effacerait `cache/`.

> Aucun identifiant ne doit revenir dans le code. Les seuls endroits ou ils ont
> le droit d'exister sont `.env` (local, ignore) et le dashboard de
> l'hebergeur.

## Depuis le telephone

Le serveur ecoute sur toutes les interfaces. Sur le meme wifi :

1. recupere l'IP du PC : `ipconfig` (ligne « Adresse IPv4 »),
2. sur le telephone, ouvre `http://192.168.x.x:8787`,
3. « Ajouter a l'ecran d'accueil » pour avoir une icone.

**Limite a connaitre :** en `http://` sur une IP locale, le navigateur refuse
d'enregistrer le service worker (reserve aux origines securisees). L'appli
fonctionne, mais **sans cache hors ligne ni vraie installation PWA** : Android
ne proposera qu'un raccourci. Pour une vraie installation il faut du HTTPS —
un tunnel Cloudflare, ou l'hebergement.

### Installer l'appli (et pas un raccourci)

En HTTPS, une barre « Installer l'application » apparait sous l'entete :

- **Android / Chrome** : le bouton declenche la vraie invite d'installation
  (WebAPK) — icone adaptative, pas de barre d'adresse, entree dans le tiroir
  d'applications. C'est different du « Ajouter a l'ecran d'accueil » du menu
  du navigateur, qui ne cree qu'un raccourci.
- **iOS / Safari** : il n'existe aucune API d'installation, la barre affiche
  donc la marche a suivre (Partager, puis « Sur l'ecran d'accueil »). Le
  resultat est bien une appli plein ecran grace a `display: standalone` et aux
  metas `apple-mobile-web-app-*`.

La barre se ferme d'un clic sur la croix et ne revient plus (memorise dans le
navigateur), et ne s'affiche jamais si l'appli est deja installee.

Si Android ne propose qu'un raccourci, c'est qu'un critere manque : origine non
HTTPS, service worker non enregistre, ou manifeste invalide. Les trois se
verifient dans Chrome DevTools, onglet *Application*.

## Fonctionnement

| Fichier | Role |
| --- | --- |
| `server.py` | Serveur HTTP : la PWA, `/api/schedule`, `/api/sync/*` |
| `sync_worker.py` | Robot Playwright : login Microsoft, A2F, capture du token |
| `ics_builder.py` | Appels API Auriga + generation de l'ICS (RFC 5545) |
| `storage.py` | Ou vit un agenda : Supabase ou `cache/` |
| `envfile.py` | Chargement du `.env` local |
| `ics.py` | Parseur ICS : VEVENT, fuseaux, RRULE/EXDATE, detection CM/TD/TP |
| `public/` | La PWA (`index.html`, `app.js`, `styles.css`, `sw.js`, manifeste) |
| `update_planning.py` | Synchronisation manuelle en ligne de commande |
| `make_icons.py` | Regenere les icones PNG depuis le meme dessin que `icon.svg` |

### API HTTP

| Route | Role |
| --- | --- |
| `GET /api/schedule?email=…` | agenda en JSON (`&refresh=1` force la relecture) |
| `POST /api/sync/start` | `{email, password}` -> `{success, syncId}` |
| `GET /api/sync/status?id=…` | avancement, code A2F, capture d'ecran |
| `GET /api/health` | sonde de vie |

L'etat d'une synchronisation est adresse par un identifiant aleatoire, pas par
l'email : les captures d'ecran et le code A2F ne doivent pas etre lisibles par
quiconque connait l'adresse de quelqu'un.

### Details qui comptent

- **Fuseaux horaires.** L'API renvoie de l'UTC, l'ICS reste en UTC, le
  navigateur reaffiche en heure locale. Le parseur code en dur les regles de
  changement d'heure europeennes, donc pas besoin du paquet `tzdata` (souvent
  absent sous Windows).
- **Cache.** Trois niveaux : memoire serveur (`refresh_seconds`), `localStorage`
  du navigateur, et le service worker. Si le reseau tombe, la derniere version
  connue est servie et marquee « donnees en cache » plutot qu'une erreur.
- **Types de cours.** CM / TD / TP / examen / projet sont devines depuis le
  libelle et la description, avec un code couleur. Si l'heuristique ne reconnait
  rien, le libelle brut est affiche tel quel — rien n'est masque.

## Interface

- vue **jour** (par defaut) et vue **semaine** ;
- bandeau « en cours » / « prochain cours » avec le temps restant ;
- balayage horizontal ou fleches gauche/droite pour changer de jour ;
- theme clair/sombre automatique.

## Tests

```bash
python test_ics.py
python test_ics_builder.py
```

`test_supabase.py` fait un vrai aller-retour sur la base : a lancer seulement
quand on touche a `storage.py`.

## Limites actuelles

- **Aucune authentification.** N'importe qui connaissant une adresse email peut
  lire l'agenda correspondant via `/api/schedule`, et `/api/sync/start` est
  ouvert (plafonne a 2 synchronisations simultanees, sans plus). Acceptable pour
  un usage perso, pas pour une mise a disposition large.
- Emploi du temps uniquement. Notes et absences demanderaient d'autres endpoints
  de l'API Aurion.
- Le robot depend de la mise en page de la connexion Microsoft : si Microsoft la
  change, les selecteurs de `sync_worker.py` sont a reprendre.
- Les RRULE complexes (`FREQ=MONTHLY`, `BYSETPOS`…) ne sont pas developpees :
  l'evenement de base est conserve, il n'y a pas de perte silencieuse.

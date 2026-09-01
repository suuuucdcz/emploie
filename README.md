# Emploi du temps Auriga

Une PWA pour consulter son emploi du temps Aurion (portail `auriga.ipsa.fr`) sur
telephone, sans passer par le portail web a chaque fois.

Le principe : le portail expose (en general) un flux **iCal**. Un petit serveur
Python le recupere cote serveur — ce qui evite le blocage CORS — le convertit en
JSON propre, et sert une interface pensee pour le mobile.

**100 % bibliotheque standard Python.** Aucun `pip install`, aucun `npm install`.

## Demarrage

```bash
python server.py
```

Puis ouvre <http://localhost:8787>. Sans configuration, l'appli tourne en **mode
demo** sur `sample.ics` (une semaine de cours fictifs) : pratique pour voir a quoi
ca ressemble avant de brancher le vrai agenda.

## Brancher ton vrai emploi du temps

1. Connecte-toi sur <https://auriga.ipsa.fr>.
2. Va sur ton emploi du temps et cherche une option d'export : selon les
   installations Aurion elle s'appelle « Exporter », « iCal », « Synchroniser mon
   agenda » ou « S'abonner au calendrier ». Elle donne une URL en `.ics`.
3. Copie `config.example.json` en `config.json` et colle l'URL :

```json
{ "ics_url": "https://auriga.ipsa.fr/....ics" }
```

4. Relance `python server.py`.

Tu peux aussi passer par la ligne de commande, sans fichier de config :

```bash
python server.py --ics-url "https://auriga.ipsa.fr/....ics"
```

Ou travailler sur un fichier deja telecharge :

```bash
python server.py --ics-file mon-agenda.ics
```

> **Cette URL vaut un mot de passe** : elle donne acces a ton agenda sans
> authentification. `config.json` est dans le `.gitignore`, ne la partage pas et
> ne la commite pas.

## Depuis le telephone

Le serveur ecoute sur toutes les interfaces. Sur le meme wifi :

1. recupere l'IP du PC : `ipconfig` (ligne « Adresse IPv4 »),
2. sur le telephone, ouvre `http://192.168.x.x:8787`,
3. « Ajouter a l'ecran d'accueil » pour avoir une icone.

**Limite a connaitre :** en `http://` sur une IP locale, le navigateur refuse
d'enregistrer le service worker (reserve aux origines securisees). L'appli
fonctionne, mais **sans cache hors ligne ni vraie installation PWA**. Sur iOS le
raccourci ecran d'accueil marche quand meme. Pour avoir l'offline complet il faut
servir en HTTPS — un tunnel type Cloudflare Tunnel, ou un hebergement.

## Fonctionnement

| Fichier | Role |
| --- | --- |
| `server.py` | Serveur HTTP : sert la PWA + `/api/schedule` (recuperation, cache, JSON) |
| `ics.py` | Parseur ICS : VEVENT, fuseaux, RRULE/EXDATE, detection CM/TD/TP |
| `public/` | La PWA (`index.html`, `app.js`, `styles.css`, `sw.js`, manifeste) |
| `make_sample.py` | Regenere `sample.ics` sur la semaine en cours |
| `make_icons.py` | Regenere les icones PNG depuis le meme dessin que `icon.svg` |
| `test_ics.py` | Tests du parseur (`python test_ics.py`) |

Details qui comptent :

- **Fuseaux horaires.** Le serveur normalise tout en UTC, le navigateur reaffiche
  en heure locale. Les regles de changement d'heure europeennes sont codees en
  dur, donc pas besoin du paquet `tzdata` (souvent absent sous Windows).
- **Cache.** Le flux est rafraichi toutes les 15 min (`refresh_seconds`). Si le
  reseau tombe, la derniere version connue est servie et marquee « donnees en
  cache » plutot que d'afficher une erreur.
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
```

## Limites actuelles

- Emploi du temps uniquement. Notes et absences demanderaient de taper l'API
  Aurion authentifiee, ce qui est un autre chantier.
- Les RRULE complexes (`FREQ=MONTHLY`, `BYSETPOS`…) ne sont pas developpees :
  l'evenement de base est conserve, il n'y a pas de perte silencieuse.
- Le lien ICS peut expirer : dans ce cas `/api/schedule` renvoie une erreur
  explicite et il faut regenerer l'URL depuis le portail.

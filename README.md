# wow-squad

Le QG de notre bande sur **World of Warcraft: Forever** : qui peut grouper ce soir, le journal de nos soirées,
la course au niveau, les trophées, et un récap chaque semaine sur Telegram ou Discord.

## Ce que fait la page

| Zone | Contenu |
|---|---|
| **Ce soir** | Objectifs de raid, donjon ou PvP avec inscriptions et composition, activité récente, puis un donjon pour chacun de vos groupes fixes, puis les autres combinaisons possibles ; alerte s'il manque un tank ou un soigneur |
| **Journal** | Moments forts, activité des dernières 24 h et résumé par jour : qui a joué, soirées passées ensemble, paliers, hauts faits, montures, morts |
| **Progression** | Objectif commun et arrivée estimée, course au niveau sur 30 jours, classement **au rythme** (niveaux par jour sur 7 jours, date d'arrivée estimée) puis au niveau |
| **Palmarès** | Trophées de la semaine, panthéon des premières fois (premier 10, 20… 60, première monture), records |
| **La squad** | Lier son compte Battle.net, ajouter un perso (prénom + nom), régler l'objectif, gérer les groupes fixes |

Chaque perso a sa fiche : rythme, courbes niveau et ilvl, XP, quêtes, boss, rang PvP, métiers, écurie, son histoire, et la **photo du joueur**
(une par joueur, partagée par tous ses persos ; zoom et cadrage avant l'envoi, réencodée en WebP 256×256 sans
métadonnées, stockée dans Postgres).

Comme on n'a pas tous commencé le même jour, le rythme (niveaux gagnés par jour sur les 7 derniers jours) compte
autant que le niveau : c'est lui qui désigne le « meilleur rythme » et qui estime quand chacun atteindra l'objectif.

## Comptes Battle.net : ce qui est stocké (et ce qui ne l'est pas)

- La liaison passe par la **page officielle Blizzard** (OAuth). Le mot de passe n'arrive jamais sur l'app.
- Blizzard fournit un jeton en lecture seule valable 24 h, **sans jeton de renouvellement** : c'est une limite
  de leur API. On ne le garde qu'en mémoire, jamais en base. L’identifiant du compte et le BattleTag sont enregistrés.
- Chaque ami ouvre le QG avec le mot de passe partagé, puis utilise **Connexion Battle.net** dans l'en-tête.
  La page **Mes personnages** affiche ses personnages Forever, rattachés à l'identifiant stable du compte.
  Les fiches restent visibles dans le QG et seul le compte associé peut modifier ou retirer un perso importé.
- Les joueurs déjà liés doivent se reconnecter une fois pour rattacher leurs persos existants à leur espace.
- **Lier une fois suffit ensuite** : la liaison sert à découvrir les persos. Le suivi se fait ensuite avec le jeton
  applicatif de l'app, renouvelé automatiquement, sans limite de durée. On ne revient lier son compte que pour
  récupérer un nouveau reroll.
- Sans liaison, on peut ajouter un perso avec son prénom et son nom (« Mars » + « Blackmon »).

Blizzard n'expose un perso qu'à partir du **niveau 10**, après une déconnexion.

## Déploiement Railway

1. **New Project → Deploy from GitHub repo → `wow-squad`**. `railway.json` fournit la commande (`python -m app`)
   et le healthcheck.
2. **Ajouter un service PostgreSQL** (New → Database → PostgreSQL). Pas besoin de volume. Dans les variables
   du service web : `DATABASE_URL=${{Postgres.DATABASE_URL}}`.
3. **Settings → Networking → Generate Domain** pour obtenir l'URL publique.
4. Variables (voir `.env.example`) : au minimum `BLIZZARD_CLIENT_ID`, `BLIZZARD_CLIENT_SECRET`, `SECRET_KEY`,
   `SITE_PASSWORD`. Ajouter `TELEGRAM_*` et/ou `DISCORD_WEBHOOK_URL` pour les notifications.
5. Sur [develop.battle.net](https://develop.battle.net/access/clients), dans le client API, ajouter l'URL de
   redirection `https://<ton-domaine>/auth/callback`.
6. Une seule instance (le worker tourne dans le même process que le site).

## En local

```bash
python -m venv .venv
.venv/Scripts/pip install -r requirements-dev.txt     # Windows ; bin/pip ailleurs
.venv/Scripts/python -m app.demo                      # fausse squad pour voir le design (SQLite uniquement)
.venv/Scripts/python -m app                           # http://localhost:8000
.venv/Scripts/python -m pytest -q
```

## Architecture

```
app/
  config.py     variables d'environnement
  db.py         modèles SQLAlchemy (persos, snapshots, événements, comptes liés)
  blizzard.py   client API Battle.net (namespaces *-forever-{region}, OAuth)
  collector.py  lecture d'un perso -> état -> événements (fonctions pures + orchestration)
  insights.py   vie de groupe : journal, soirées, rythme, objectif, groupes, premières fois, records
  squad.py      réglages modifiables depuis le site (objectif, groupes fixes)
  photos.py     photos de profil des joueurs (validation, recadrage, WebP)
  stats.py      semaine en cours, classement, texte du récap
  worker.py     boucle de fond : synchro, notifications en direct, récap hebdo
  notify.py     Telegram / Discord
  ui.py         filtres d'affichage, couleurs de classe, graphiques SVG accessibles
  web.py        routes
  templates/ static/
```

## Accessibilité

Visé : WCAG 2.1 AA. Vérifié avec axe-core sur toutes les pages et tous les onglets, dans les deux thèmes.

- Navigation au clavier complète, lien d'évitement, focus toujours visible, cibles tactiles d'au moins 44 px.
- Onglets selon le motif ARIA (flèches, Début/Fin) ; sans JavaScript, ce sont de simples ancres et tout s'affiche.
- Graphiques en SVG avec titre, description et tableau de données équivalent ; la couleur n'est jamais le seul indice.
- Erreurs de formulaire reliées aux champs (`aria-invalid`, `aria-describedby`), messages annoncés (`role="status"`).
- Thèmes sombre et clair (suit le système, bascule mémorisée), `prefers-reduced-motion`, mode contraste élevé.


## Objectifs de la squad

Depuis « Ce soir », un joueur lié peut proposer un raid, un donjon, une sortie PvP ou une activité libre.
Il peut fixer une date, un niveau minimum, un effectif et ajouter des notes de préparation. Chaque compte
s’inscrit avec un de ses personnages et un rôle. Le tableau montre les places remplies, les niveaux et
les écarts de faction ou de mode. Le créateur peut modifier l’objectif et le marquer comme terminé.

Les compteurs de la fiche sont ceux remontés par Battle.net. Le journal affiche les hausses détectées
entre relevés de quêtes, de boss et de victoires honorables. Le premier relevé initialise les compteurs.


## Administration

Pour ouvrir l’administration à ton seul compte, lie ton compte Battle.net puis copie l’identifiant
affiché dans « Mes personnages » → « Identifiant Battle.net ». Dans les variables du service web Railway,
ajoute `ADMIN_BATTLE_ID` avec cet identifiant. Sans cette variable, les actions administratives sont verrouillées.
La création des défis, les réglages des groupes et le retrait d’un personnage passent par « Administration ».
Les inscriptions aux sorties et les propositions de sorties restent accessibles aux joueurs connectés.


Le formulaire de sortie propose aussi un catalogue de donjons et de raids Forever. Choisir une instance
préremplit le titre, le type, l’effectif et le niveau conseillé. La fiche d’accès et de préparation reste
accessible sur la sortie publiée. Le catalogue inclut les raids annoncés, dont la date d’ouverture est indiquée.


## Préparer et raconter une sortie

- « Ce soir » utilise les disponibilités déclarées le jour même. Les propositions privilégient les groupes
  complets, les confirmations et les rôles couverts, avec une seule place par compte et des personnages compatibles.
- La préparation est confirmée par personnage et par sortie. Les validations de quêtes Blizzard sont distinctes
  des confirmations manuelles. La couverture des quêtes est volontairement limitée aux repères documentés.
- L’organisateur et les inscrits peuvent enrichir le récit d’une sortie terminée. Les relevés Blizzard sont
  rattachés manuellement. Les captures sont réencodées et conservées dans la base, avec une limite de 50 souvenirs
  par sortie et 5 Mo par fichier source.
- La carte affiche une grille de coordonnées par zone et des filtres. Chaque repère renvoie à son guide.

# wow-squad

Le QG de notre bande sur **World of Warcraft: Forever** : qui peut grouper ce soir, le journal de nos soirées,
la course au niveau, les trophées, et un récap chaque semaine sur Telegram ou Discord.

## Ce que fait la page

| Zone | Contenu |
|---|---|
| **À la une** (toujours visible) | Objectif commun « toute la squad niveau 60 », derniers moments forts, qui a joué ces dernières 24 h |
| **Ce soir** | Groupes possibles par faction et tranche de niveaux, donjon conseillé, alerte s'il manque un tank ou un soigneur |
| **Journal** | Un résumé par jour : qui a joué, soirées passées ensemble, paliers, hauts faits, montures, morts |
| **Progression** | Course au niveau sur 30 jours (avec tableau de données) et classement |
| **Palmarès** | Trophées de la semaine, panthéon des premières fois (premier 10, 20… 60, première monture), records |
| **La squad** | Lier son compte Battle.net, ajouter un perso à la main (prénom + nom), voir qui est suivi |

Chaque perso a sa fiche : courbes niveau et ilvl, métiers, écurie, et son histoire.

## Comptes Battle.net : ce qui est stocké (et ce qui ne l'est pas)

- La liaison passe par la **page officielle Blizzard** (OAuth). Le mot de passe n'arrive jamais sur l'app.
- Blizzard fournit un jeton en lecture seule valable 24 h, **sans jeton de renouvellement** : c'est une limite
  de leur API. On ne le garde qu'en mémoire, jamais en base. Seul le BattleTag est enregistré.
- **Lier une fois suffit** : la liaison sert à découvrir les persos. Le suivi se fait ensuite avec le jeton
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
  insights.py   vie de groupe : journal, soirées ensemble, idées de groupe, premières fois, records
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

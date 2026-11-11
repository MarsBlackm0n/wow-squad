"""Remplit une base SQLite locale avec une fausse squad, pour travailler le design sans l'API.

    python -m app.demo          # puis : python -m app
Refuse de tourner sur autre chose que SQLite (jamais sur la base Railway).
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from .collector import apply_state
from .config import settings
from .db import Base, Character, Event, SessionLocal, engine

SQUAD = [
    # nom, surnom, joueur, classe, race, faction, niveau de départ, niveaux/jour
    ("Grokna", "Brise-Crâne", "Max", "Guerrier", "Orc", "Horde", 12, 3.2),
    ("Zul'vex", "Chante-Ombre", "Léa", "Chaman", "Troll", "Horde", 11, 2.8),
    ("Morvane", "Cendrelune", "Jordan", "Démoniste", "Mort-vivant", "Horde", 10, 3.6),
    ("Thokk", "Pied-Ferré", "Sam", "Druide", "Tauren", "Horde", 10, 2.1),
    ("Elyra", "Aube-Claire", "Inès", "Prêtresse", "Mort-vivante", "Horde", 13, 2.5),
]
ACHIEVEMENTS = ["Niveau 10", "Niveau 20", "Explorateur de Durotar", "Les Tarides", "Gouffre de Ragefeu",
                "Cavernes des lamentations", "Apprenti cuisinier", "Niveau 30"]
MOUNTS = ["Loup gris rapide", "Raptor turquoise", "Kodo brun"]


def main() -> None:
    if not settings.database_url.startswith("sqlite"):
        raise SystemExit("La démo ne tourne que sur SQLite (DATABASE_URL pointe ailleurs).")
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    rng = random.Random(42)
    # Minuit (heure de Paris) il y a 10 jours : la dernière soirée simulée tombe hier soir.
    paris = ZoneInfo(settings.timezone)
    start = datetime.now(paris).replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=10)

    with SessionLocal() as db:
        chars = []
        for name, surname, player, cls, race, faction, lvl, _ in SQUAD:
            c = Character(region="eu", slug=f"{name}-{surname}".lower(), name=name, surname=surname, player=player,
                          state={}, status="ok")
            db.add(c)
            chars.append(c)
        db.flush()

        states = {}
        for c, (name, surname, _, cls, race, faction, lvl, _speed) in zip(chars, SQUAD):
            states[c.id] = {"name": name, "surname": surname, "level": lvl, "class_name": cls, "race": race,
                            "faction": faction, "guild": "Les Gros Loups", "ruleset": "PvP",
                            "achievement_points": 50, "ilvl": 8.0, "deaths": 2, "honorable_kills": 0,
                            "achievements": {}, "mounts": {}, "professions": {"Cuisine": 15, "Herboristerie": 20},
                            "last_login": int((start - timedelta(hours=2)).timestamp() * 1000)}

        for day in range(10):
            evening = start + timedelta(days=day, hours=rng.randint(19, 21))
            players = [c for c in chars if rng.random() < 0.75]
            for c in players:
                speed = SQUAD[chars.index(c)][7]
                at = evening + timedelta(minutes=rng.randint(0, 80))
                s = dict(states[c.id])
                s["level"] = min(60, s["level"] + max(1, round(speed + rng.uniform(-1, 1))))
                s["ilvl"] = round(s["ilvl"] + rng.uniform(0.5, 2.5), 1)
                s["deaths"] += rng.randint(0, 4)
                s["achievement_points"] += 10 * rng.randint(0, 2)
                s["honorable_kills"] += rng.randint(0, 6)
                s["professions"] = {k: min(300, v + rng.randint(5, 30)) for k, v in s["professions"].items()}
                s["last_login"] = int(at.timestamp() * 1000)
                if rng.random() < 0.35:
                    ach = rng.choice(ACHIEVEMENTS)
                    s["achievements"] = {**s["achievements"], str(ACHIEVEMENTS.index(ach)): ach}
                if s["level"] >= 40 and not s["mounts"]:
                    s["mounts"] = {"1": rng.choice(MOUNTS)}
                states[c.id] = s
                new_events = apply_state(db, c, dict(s), "ok")
                for e in new_events:
                    e.at = at
                    e.notified = True
                for snap in c.snapshots[-1:]:
                    snap.taken_at = at
                db.flush()
        db.query(Event).filter(Event.kind == "joined").update({"at": start})
        db.commit()
    print(f"Démo prête : {len(SQUAD)} persos sur 10 jours. Lance `python -m app` puis ouvre http://localhost:8000")


if __name__ == "__main__":
    main()

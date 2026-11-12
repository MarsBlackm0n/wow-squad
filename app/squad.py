"""Réglages de la squad modifiables depuis le site : objectif commun et groupes fixes (stockés dans la table kv)."""

from __future__ import annotations

import secrets
from math import ceil
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from sqlalchemy.orm import Session, joinedload

from .db import Event, MissionSignup, SquadMission, kv_get, kv_set, select, utcnow
from .insights import MAX_LEVEL, Pace
from .ui import faction_key, ruleset_label

MAX_GROUPS = 12


@dataclass
class Goal:
    target: int = MAX_LEVEL
    deadline: date | None = None


def get_goal(db: Session) -> Goal:
    raw = kv_get(db, "goal") or {}
    deadline = raw.get("deadline")
    try:
        deadline = date.fromisoformat(deadline) if deadline else None
    except ValueError:
        deadline = None
    return Goal(int(raw.get("target") or MAX_LEVEL), deadline)


def set_goal(db: Session, target: int, deadline: date | None) -> None:
    kv_set(db, "goal", {"target": max(2, min(target, MAX_LEVEL)),
                        "deadline": deadline.isoformat() if deadline else None})


def get_groups(db: Session) -> list[dict]:
    return list((kv_get(db, "groups") or {}).get("items", []))


def save_group(db: Session, name: str, members: list[int], group_id: str | None = None) -> None:
    groups = [g for g in get_groups(db) if g.get("id") != group_id]
    groups.append({"id": group_id or secrets.token_hex(4), "name": name.strip()[:40], "members": members[:40]})
    kv_set(db, "groups", {"items": groups[-MAX_GROUPS:]})


def delete_group(db: Session, group_id: str) -> None:
    kv_set(db, "groups", {"items": [g for g in get_groups(db) if g.get("id") != group_id]})

MISSION_KINDS = {"raid": "Raid", "dungeon": "Donjon", "pvp": "PvP", "other": "Autre"}
MISSION_ROLES = {"tank": "Tank", "heal": "Soins", "damage": "Dégâts", "flex": "Flexible"}


@dataclass
class MissionRoster:
    mission: SquadMission
    signups: list[MissionSignup]
    level_dates: dict[int, date] = field(default_factory=dict)

    @property
    def roles(self) -> Counter:
        return Counter(row.role for row in self.signups)

    @property
    def missing_levels(self) -> list[MissionSignup]:
        return [row for row in self.signups if row.character.level < self.mission.min_level]

    @property
    def open_slots(self) -> int:
        return max(self.mission.target_size - len(self.signups), 0)

    @property
    def warnings(self) -> list[str]:
        factions = {faction_key(row.character.faction) for row in self.signups} - {"neutral"}
        modes = {ruleset_label(row.character.ruleset) for row in self.signups if row.character.ruleset}
        warnings = []
        if len(factions) > 1:
            warnings.append("Alliance et Horde inscrites")
        if len(modes) > 1:
            warnings.append("Plusieurs modes de jeu")
        if self.signups and self.mission.kind in ("raid", "dungeon"):
            roles = self.roles
            if not roles["tank"]:
                warnings.append("Tank à désigner")
            if not roles["heal"]:
                warnings.append("Soigneur à désigner")
        return warnings


def mission_level_dates(roster: MissionRoster, paces: dict[int, Pace], today: date) -> dict[int, date]:
    dates = {}
    for signup in roster.missing_levels:
        char = signup.character
        pace = paces.get(char.id)
        if char.level and pace and pace.per_day > 0:
            dates[char.id] = today + timedelta(days=ceil((roster.mission.min_level - char.level) / pace.per_day))
    return dates


def mission_rosters(db: Session, paces: dict[int, Pace], today: date) -> tuple[list[MissionRoster], list[SquadMission]]:
    opened = db.scalars(select(SquadMission).where(SquadMission.completed_at.is_(None))
                        .order_by(SquadMission.starts_at.asc().nulls_last(), SquadMission.id.desc())).all()
    completed = db.scalars(select(SquadMission).where(SquadMission.completed_at.is_not(None))
                           .order_by(SquadMission.completed_at.desc()).limit(5)).all()
    if not opened:
        return [], list(completed)
    signups = db.scalars(select(MissionSignup).where(MissionSignup.mission_id.in_([m.id for m in opened]))
                         .options(joinedload(MissionSignup.character, innerjoin=True))
                         .order_by(MissionSignup.account_id)).all()
    by_mission = defaultdict(list)
    for row in signups:
        by_mission[row.mission_id].append(row)
    rosters = [MissionRoster(m, by_mission[m.id]) for m in opened]
    for roster in rosters:
        roster.level_dates = mission_level_dates(roster, paces, today)
    return rosters, list(completed)

COMMUNITY_METRICS = {
    "quests": "Quêtes terminées", "encounter": "Boss vaincus", "pvp": "Victoires honorables",
    "outings": "Sorties accomplies", "level": "Niveaux gagnés",
}


def community_targets(db: Session) -> list[dict]:
    targets = (kv_get(db, "community_targets") or {}).get("items", [])
    results = []
    for target in targets:
        start = datetime.fromisoformat(target["start"])
        kind = target["kind"]
        if kind == "outings":
            progress = len(db.scalars(select(SquadMission.id).where(SquadMission.completed_at >= start)).all())
        else:
            kinds = ("level", "level_milestone") if kind == "level" else (kind,)
            rows = db.scalars(select(Event.data).where(Event.kind.in_(kinds), Event.at >= start)).all()
            progress = sum(max(0, row.get("to", 0) - row.get("from", 0)) if kind == "level"
                           else row.get("count", 0) for row in rows if row)
        results.append({**target, "label": COMMUNITY_METRICS[kind], "progress": progress})
    return results


def add_community_target(db: Session, title: str, kind: str, amount: int) -> bool:
    items = list((kv_get(db, "community_targets") or {}).get("items", []))
    if len(items) >= 12:
        return False
    items.append({"id": secrets.token_hex(8), "title": title, "kind": kind, "amount": amount,
                  "start": utcnow().isoformat()})
    kv_set(db, "community_targets", {"items": items})
    return True


def remove_community_target(db: Session, target_id: str) -> None:
    items = (kv_get(db, "community_targets") or {}).get("items", [])
    kv_set(db, "community_targets", {"items": [item for item in items if item["id"] != target_id]})

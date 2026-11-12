"""Compositions de la soirée, préparation et sélection des souvenirs de sortie."""

from collections import defaultdict
from dataclasses import dataclass
from datetime import timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import joinedload

from .db import Event, MissionSignup
from .insights import HEAL_CLASSES, TANK_CLASSES
from .instances import DUNGEON_CATALOG, DUNGEON_GUIDES
from .ui import _fold, class_key, faction_key, ruleset_label

AVAILABILITY = {"yes": "Disponible", "maybe": "Peut-être", "no": "Absent"}
PREFERENCES = {"group": "Former un groupe", "quests": "Avancer les quêtes", "discover": "Découvrir"}
ROLES = ("tank", "heal", "damage", "damage", "damage")
QUESTS = {
    "deadmines": (166, "La Confrérie défias", "alliance"),
    "wailing": (914, "Les chefs du Croc", "horde"),
    "shadowfang": (1014, "Arugal doit mourir", "horde"),
}
CHECKS = {
    "default": (("access", "Accès et rendez-vous vérifiés"), ("supplies", "Équipement et consommables préparés")),
    "onyxia": (("amulet", "Amulette Drakefeu dans l’inventaire"), ("fire", "Résistance au Feu préparée")),
}


@dataclass
class Slot:
    character: object
    role: str
    tentative: bool
    quest: bool
    discovery: bool


def preparation_checks(mission):
    return CHECKS["default"] + CHECKS.get(mission.instance_key, ())


def quest_status(char, instance_key):
    quest = QUESTS.get(instance_key)
    if not quest or faction_key(char.faction) != quest[2]:
        return None
    completed = (char.state or {}).get("completed_quest_ids")
    return quest, None if completed is None else quest[0] in completed


def discovery_candidate(char, instance):
    records = (char.state or {}).get("dungeon_encounters")
    if records is None or any(not row.get("instance") for row in records.values()):
        return False
    def normalized(name):
        return "".join(c for c in _fold(name) if c.isalnum())
    names = {normalized(instance.name), normalized(DUNGEON_GUIDES[instance.key])}
    return not any(row["count"] and normalized(row["instance"]) in names for row in records.values())


def _eligible_roles(char):
    cls = class_key(char.class_name)
    return {"damage"} | ({"tank"} if cls in TANK_CLASSES else set()) | ({"heal"} if cls in HEAL_CLASSES else set())


def _score(team, priority):
    filled = [slot for slot in team if slot]
    gain = sum(slot.quest if priority == "quests" else slot.discovery if priority == "discover" else 0 for slot in filled)
    return len(filled), sum(not slot.tentative for slot in filled), sum(slot.role != "damage" for slot in filled), gain


def evening_suggestions(rows, chars, priority):
    chars_by_id = {c.id: c for c in chars}
    pools = defaultdict(lambda: defaultdict(list))
    for row in rows:
        if row.status == "no":
            continue
        for char_id in row.character_ids:
            char = chars_by_id.get(char_id)
            if not char or char.account_id != row.account_id or not char.level:
                continue
            side, mode = faction_key(char.faction), ruleset_label(char.ruleset)
            if side != "neutral" and mode in ("Normal", "PvP", "RP", "RP + PvP"):
                pools[(char.region, side, mode)][row.account_id].append((char, row))
    candidates = []
    for instance in DUNGEON_CATALOG:
        if instance.size != 5:
            continue
        best = None
        for pool, players in pools.items():
            _, side, mode = pool
            if instance.key in {"ragefire", "stockade"} and side != {"ragefire": "horde", "stockade": "alliance"}[instance.key]:
                continue
            states = {0: [None] * len(ROLES)}
            for options in players.values():
                next_states = states.copy()
                for char, row in options:
                    if char.level < instance.level:
                        continue
                    roles = _eligible_roles(char)
                    if row.role != "flex":
                        roles &= {row.role}
                    quest = quest_status(char, instance.key)
                    discovery = discovery_candidate(char, instance)
                    for mask, lineup in states.items():
                        for i, role in enumerate(ROLES):
                            if mask & (1 << i) or role not in roles:
                                continue
                            filled = lineup.copy()
                            filled[i] = Slot(char, role, row.status == "maybe", bool(quest and quest[1] is False), discovery)
                            new_mask = mask | (1 << i)
                            if new_mask not in next_states or _score(filled, priority) > _score(next_states[new_mask], priority):
                                next_states[new_mask] = filled
                states = next_states
            team = max(states.values(), key=lambda lineup: _score(lineup, priority))
            score = _score(team, priority)
            if score[0] >= 2 and (best is None or score > best["score"]):
                best = {"instance": instance, "team": team, "side": side, "mode": mode, "score": score, "pool": pool}
        if best:
            best["alternatives"] = alternatives(best["team"], pools[best.pop("pool")], instance)
            candidates.append(best)
    return sorted(candidates, key=lambda item: item["score"], reverse=True)[:3]


def alternatives(team, players, instance):
    assigned = {slot.character.account_id for slot in team if slot}
    results = []
    for role, slot in zip(ROLES, team):
        choices = []
        for account_id, options in players.items():
            if account_id in assigned and (not slot or slot.character.account_id != account_id):
                continue
            choices.extend({"character": char, "tentative": row.status == "maybe"} for char, row in options
                           if char.level >= instance.level and role in _eligible_roles(char)
                           and row.role in ("flex", role) and (not slot or slot.character.id != char.id))
        results.append(choices)
    return results


def mission_signups(db, mission_id):
    return db.scalars(select(MissionSignup).where(MissionSignup.mission_id == mission_id)
                      .options(joinedload(MissionSignup.character, innerjoin=True))).all()


def story_candidates(db, mission, signups):
    if not mission.completed_at or not signups:
        return []
    end = mission.completed_at
    start = mission.starts_at or end - timedelta(hours=8)
    if (start if start.tzinfo else start.replace(tzinfo=timezone.utc)) > (end if end.tzinfo else end.replace(tzinfo=timezone.utc)):
        start = end - timedelta(hours=8)
    return db.scalars(select(Event).where(Event.character_id.in_([s.character_id for s in signups]),
                      Event.at >= start - timedelta(hours=1), Event.at <= end + timedelta(hours=12),
                      Event.kind.in_(("encounter", "achievement", "deaths", "level_milestone", "mount")))
                      .order_by(Event.at.desc()).limit(35)).all()

"""La vie de groupe : journal quotidien, soirées ensemble, qui peut grouper où, premières fois et records.

Tout est calculé à partir des persos et du fil d'événements, sans appel réseau.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from .db import Character, Event, select, utcnow
from .ui import class_key, faction_key

MAX_LEVEL = 60
TOGETHER_WINDOW = timedelta(minutes=90)
HIGHLIGHT_KINDS = ("level_milestone", "achievement", "mount", "profession_rank", "guild", "joined")
TANK_CLASSES = {"warrior", "paladin", "druid"}
HEAL_CLASSES = {"priest", "paladin", "druid", "shaman"}

# Donjons de l'Azeroth de 2004 (tranche de niveaux conseillée). Liste à ajuster si Forever en ajoute.
DUNGEONS = [
    ("Gouffre de Ragefeu", 13, 18, "horde"),
    ("Les Mortemines", 17, 26, None),
    ("Cavernes des lamentations", 17, 24, None),
    ("Donjon d'Ombrecroc", 22, 30, None),
    ("Profondeurs de Brassenoire", 24, 32, None),
    ("La Prison", 24, 32, "alliance"),
    ("Gnomeregan", 29, 38, None),
    ("Kraal de Tranchebauge", 29, 38, None),
    ("Monastère écarlate", 34, 45, None),
    ("Souilles de Tranchebauge", 37, 46, None),
    ("Uldaman", 41, 51, None),
    ("Zul'Farrak", 44, 54, None),
    ("Maraudon", 46, 55, None),
    ("Temple d'Atal'Hakkar", 50, 56, None),
    ("Profondeurs de Rochenoire", 52, 60, None),
    ("Pic Rochenoire inférieur", 55, 60, None),
    ("Hache-Tripes", 55, 60, None),
    ("Scholomance", 58, 60, None),
    ("Stratholme", 58, 60, None),
]


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


# --- Journal de la squad ------------------------------------------------------------------------

@dataclass
class Evening:
    """Plusieurs persos en jeu au même moment : une soirée ensemble."""
    start: datetime
    members: list[Character]


@dataclass
class Day:
    day: date
    players: list[Character] = field(default_factory=list)
    levels: int = 0
    achievements: int = 0
    deaths: int = 0
    highlights: list[Event] = field(default_factory=list)
    events: list[Event] = field(default_factory=list)
    evenings: list[Evening] = field(default_factory=list)

    @property
    def headline(self) -> str:
        parts = []
        n = len(self.players)
        parts.append(f"{n} aventurier{'s' if n > 1 else ''} en jeu")
        if self.levels:
            parts.append(f"+{self.levels} niveau{'x' if self.levels > 1 else ''}")
        if self.achievements:
            parts.append(f"{self.achievements} haut{'s' if self.achievements > 1 else ''} fait{'s' if self.achievements > 1 else ''}")
        if self.deaths:
            parts.append(f"{self.deaths} mort{'s' if self.deaths > 1 else ''}")
        return " · ".join(parts)


def chronicle(events: list[Event], tz_name: str, days: int = 7) -> list[Day]:
    tz = ZoneInfo(tz_name)
    by_day: dict[date, Day] = {}
    sessions: dict[date, list[tuple[datetime, Character]]] = defaultdict(list)
    for e in sorted(events, key=lambda e: _aware(e.at)):
        local = _aware(e.at).astimezone(tz)
        d = by_day.setdefault(local.date(), Day(local.date()))
        if e.character and e.character not in d.players:
            d.players.append(e.character)
        data = e.data or {}
        if e.kind in ("level", "level_milestone"):
            d.levels += max(int(data.get("to", 0)) - int(data.get("from", 0)), 0)
        elif e.kind == "achievement":
            d.achievements += int(data.get("count", 1))
        elif e.kind == "deaths":
            d.deaths += int(data.get("count", 1))
        elif e.kind == "session" and e.character:
            login = datetime.fromtimestamp(int(data.get("login", 0)) / 1000, tz=timezone.utc).astimezone(tz)
            sessions[login.date()].append((login, e.character))
        if e.kind != "session":
            d.events.insert(0, e)
        if e.kind in HIGHLIGHT_KINDS:
            d.highlights.append(e)

    for day, items in sessions.items():
        if day in by_day:
            by_day[day].evenings = _evenings(items)
    ordered = sorted(by_day.values(), key=lambda d: d.day, reverse=True)[:days]
    for d in ordered:
        d.highlights = sorted(d.highlights, key=lambda e: HIGHLIGHT_KINDS.index(e.kind))[:6]
    return ordered


def _evenings(items: list[tuple[datetime, Character]]) -> list[Evening]:
    """Regroupe les connexions proches dans le temps ; on ne garde que les groupes d'au moins deux joueurs."""
    items.sort(key=lambda x: x[0])
    groups: list[list[tuple[datetime, Character]]] = []
    for login, char in items:
        if groups and login - groups[-1][-1][0] <= TOGETHER_WINDOW:
            groups[-1].append((login, char))
        else:
            groups.append([(login, char)])
    evenings = []
    for g in groups:
        members = list({c.id: c for _, c in g}.values())
        if len(members) >= 2:
            evenings.append(Evening(g[0][0], members))
    return evenings


# --- Qui peut grouper ce soir --------------------------------------------------------------------

@dataclass
class GroupIdea:
    dungeon: str
    min_level: int
    max_level: int
    faction: str
    members: list[Character]
    has_tank: bool
    has_heal: bool
    alternatives: list[str] = field(default_factory=list)

    @property
    def roles_hint(self) -> str:
        if self.has_tank and self.has_heal:
            return "Tank et soigneur possibles"
        if not self.has_tank and not self.has_heal:
            return "Ni tank ni soigneur dans le lot"
        return "Pas de tank naturel" if not self.has_tank else "Pas de soigneur naturel"


def group_ideas(chars: list[Character], limit: int = 4) -> list[GroupIdea]:
    playable = [c for c in chars if c.status != "pending" and c.level]
    ideas = []
    for name, lo, hi, only in DUNGEONS:
        by_faction: dict[str, list[Character]] = defaultdict(list)
        for c in playable:
            if lo - 2 <= c.level <= hi:
                by_faction[faction_key(c.faction)].append(c)
        for faction, members in by_faction.items():
            if len(members) < 2 or (only and faction not in (only, "neutral")):
                continue
            classes = {class_key(m.class_name) for m in members}
            ideas.append(GroupIdea(name, lo, hi, faction, sorted(members, key=lambda m: -m.level),
                                   bool(classes & TANK_CLASSES), bool(classes & HEAL_CLASSES)))
    # Le plus de monde d'abord, puis le donjon le plus « pile dans la tranche » pour le groupe.
    ideas.sort(key=lambda i: (-len(i.members), abs((i.min_level + i.max_level) / 2
                                                   - sum(m.level for m in i.members) / len(i.members))))
    # Un même groupe de joueurs = une seule carte, les autres donjons possibles en alternatives.
    unique: dict[frozenset, GroupIdea] = {}
    for idea in ideas:
        key = frozenset(m.id for m in idea.members)
        if key in unique:
            unique[key].alternatives.append(idea.dungeon)
        else:
            unique[key] = idea
    return list(unique.values())[:limit]


# --- Objectif collectif, premières fois, records ---------------------------------------------------

@dataclass
class SquadGoal:
    members: int
    average_level: float
    progress: float  # 0-100, vers « tout le monde au niveau max »
    at_max: int
    laggard: Character | None
    leader: Character | None

    @property
    def gap(self) -> int:
        return (self.leader.level - self.laggard.level) if self.leader and self.laggard else 0


def squad_goal(chars: list[Character]) -> SquadGoal | None:
    active = [c for c in chars if c.status != "pending" and c.level]
    if not active:
        return None
    total = sum(min(c.level, MAX_LEVEL) for c in active)
    return SquadGoal(
        members=len(active),
        average_level=total / len(active),
        progress=round(total / (len(active) * MAX_LEVEL) * 100, 1),
        at_max=sum(1 for c in active if c.level >= MAX_LEVEL),
        laggard=min(active, key=lambda c: c.level),
        leader=max(active, key=lambda c: c.level),
    )


@dataclass
class First:
    label: str
    character: Character
    at: datetime


def firsts(db: Session) -> list[First]:
    """Le premier de la squad à atteindre chaque palier de 10 niveaux, et la première monture."""
    out: dict[str, First] = {}
    rows = db.scalars(select(Event).where(Event.kind.in_(("level", "level_milestone", "mount")))
                      .order_by(Event.at)).all()
    for e in rows:
        data = e.data or {}
        if e.kind == "mount":
            out.setdefault("mount", First("Première monture", e.character, e.at))
            continue
        for milestone in range(10, MAX_LEVEL + 1, 10):
            if int(data.get("from", 0)) < milestone <= int(data.get("to", 0)):
                out.setdefault(f"lvl{milestone}", First(f"Niveau {milestone}", e.character, e.at))
    order = [f"lvl{m}" for m in range(10, MAX_LEVEL + 1, 10)] + ["mount"]
    return [out[k] for k in order if k in out]


@dataclass
class Record:
    icon: str
    label: str
    character: Character
    value: str
    day: date


def records(events: list[Event], tz_name: str) -> list[Record]:
    """Meilleure journée de leveling et pire hécatombe, tous persos confondus."""
    tz = ZoneInfo(tz_name)
    levels: dict[tuple[int, date], int] = defaultdict(int)
    deaths: dict[tuple[int, date], int] = defaultdict(int)
    chars: dict[int, Character] = {}
    for e in events:
        if not e.character:
            continue
        key = (e.character.id, _aware(e.at).astimezone(tz).date())
        chars[e.character.id] = e.character
        data = e.data or {}
        if e.kind in ("level", "level_milestone"):
            levels[key] += max(int(data.get("to", 0)) - int(data.get("from", 0)), 0)
        elif e.kind == "deaths":
            deaths[key] += int(data.get("count", 1))
    out = []
    if levels:
        (cid, day), n = max(levels.items(), key=lambda kv: kv[1])
        if n:
            out.append(Record("⚡", "Plus grosse journée de leveling", chars[cid], f"+{n} niveaux", day))
    if deaths:
        (cid, day), n = max(deaths.items(), key=lambda kv: kv[1])
        if n:
            out.append(Record("🪦", "Pire hécatombe en une journée", chars[cid], f"{n} morts", day))
    return out


def headlines(events: list[Event], hours: int = 72, limit: int = 3) -> list[Event]:
    """« À la une » : les plus beaux moments récents (paliers, hauts faits, montures), les plus récents d'abord."""
    since = utcnow() - timedelta(hours=hours)
    weight = {"level_milestone": 0, "mount": 1, "achievement": 2, "guild": 3}
    recent = [e for e in events if e.kind in weight and _aware(e.at) >= since]
    recent.sort(key=lambda e: (weight[e.kind], -_aware(e.at).timestamp()))
    return sorted(recent[:limit], key=lambda e: _aware(e.at), reverse=True)


def recently_seen(chars: list[Character], hours: int = 24) -> list[Character]:
    since = utcnow() - timedelta(hours=hours)
    seen = [c for c in chars if c.last_login and _aware(c.last_login) >= since]
    return sorted(seen, key=lambda c: _aware(c.last_login), reverse=True)


def load_events(db: Session, days: int = 30) -> list[Event]:
    events = db.scalars(select(Event).where(Event.at >= utcnow() - timedelta(days=days))
                        .order_by(Event.at.desc())).all()
    for e in events:
        _ = e.character  # chargé avant la fermeture de la session
    return list(events)

"""La vie de groupe : journal quotidien, soirées ensemble, qui peut grouper où, premières fois et records.

Tout est calculé à partir des persos et du fil d'événements, sans appel réseau.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from .db import Character, Event, Snapshot, select, utcnow
from .ui import FOREVER_LAUNCH, class_key, faction_key

MAX_LEVEL = 60
TOGETHER_WINDOW = timedelta(minutes=90)
HIGHLIGHT_KINDS = ("level_milestone", "encounter", "achievement", "mount", "profession_rank", "guild", "joined")
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


def activity_totals(events: list[Event], days: int = 7) -> dict[str, int]:
    since = utcnow() - timedelta(days=days)
    totals = {"quests": 0, "encounter": 0, "pvp": 0}
    for event in events:
        if event.kind in totals and _aware(event.at) >= since:
            totals[event.kind] += (event.data or {}).get("count", 0)
    return totals


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


def character_roles(char: Character, evening_role: str = "flex") -> set[str]:
    cls = class_key(char.class_name)
    roles = {"damage"}
    if cls in TANK_CLASSES:
        roles.add("tank")
    if cls in HEAL_CLASSES:
        roles.add("heal")
    preferred = getattr(char, "preferred_role", None)
    if preferred in ("tank", "heal", "damage"):
        roles.add(preferred)
    if evening_role != "flex":
        return roles & {evening_role}
    return {preferred} if preferred in roles else roles


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
            roles = set().union(*(character_roles(m) for m in members))
            ideas.append(GroupIdea(name, lo, hi, faction, sorted(members, key=lambda m: -m.level),
                                   "tank" in roles, "heal" in roles))
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
class Pace:
    """Rythme d'un perso sur la fenêtre récente : juste même si on n'a pas tous commencé le même jour."""
    per_day: float        # niveaux par jour
    gained: int           # niveaux gagnés sur la fenêtre
    eta: date | None      # date estimée pour atteindre l'objectif (None : à l'arrêt ou déjà arrivé)
    reached: bool
    observed_days: float
    estimated: bool = False
    weekly_estimate: int | None = None


def paces(db: Session, chars: list[Character], target: int, tz_name: str, days: int = 7) -> dict[int, Pace]:
    now = utcnow()
    since = now - timedelta(days=days)
    today = now.astimezone(ZoneInfo(tz_name)).date()
    out = {}
    for c in chars:
        if c.status == "pending" or not c.level:
            continue
        window = select(Snapshot).where(Snapshot.character_id == c.id, Snapshot.taken_at >= since,
                                        Snapshot.taken_at <= now, Snapshot.level > 0)
        base = db.scalars(window.order_by(Snapshot.taken_at).limit(1)).first()
        latest = db.scalars(window.order_by(Snapshot.taken_at.desc()).limit(1)).first()
        if base is None or latest is None:
            continue
        elapsed = (_aware(latest.taken_at) - _aware(base.taken_at)).total_seconds() / 86400
        estimated = elapsed < 1
        weekly_estimate = None
        if estimated:
            elapsed = (_aware(latest.taken_at) - FOREVER_LAUNCH).total_seconds() / 86400
            if elapsed <= 0:
                continue
            # Projette la portion de la progression estimée qui tombe dans la fenêtre.
            covered = (_aware(latest.taken_at) - max(since, FOREVER_LAUNCH)).total_seconds() / 86400
            weekly_estimate = round(latest.level * min(max(covered / elapsed, 0), 1))
            elapsed = max(elapsed, 1)
            gained = latest.level
        else:
            gained = max(latest.level - base.level, 0)
        per_day = gained / elapsed
        reached = c.level >= target
        eta = None
        if not reached and per_day > 0.05:
            eta = today + timedelta(days=round((target - c.level) / per_day))
        out[c.id] = Pace(round(per_day, 1), gained, eta, reached, elapsed, estimated, weekly_estimate)
    return out


@dataclass
class SquadGoal:
    target: int
    deadline: date | None
    members: int
    average_level: float
    progress: float  # 0-100, vers « tout le monde au niveau cible »
    at_target: int
    average_pace: float | None
    projected: date | None   # quand le dernier y arrivera, au rythme actuel
    stalled: int             # persos sous l’objectif sans gain de niveau observé
    fastest: Character | None
    fastest_pace: float
    average_estimated: bool
    fastest_estimated: bool

    @property
    def days_left(self) -> int | None:
        return (self.deadline - utcnow().date()).days if self.deadline else None

    @property
    def on_track(self) -> bool | None:
        if not self.deadline or self.projected is None:
            return None
        return self.projected <= self.deadline


def squad_goal(chars: list[Character], target: int, deadline: date | None,
               pace_by_char: dict[int, Pace]) -> SquadGoal | None:
    active = [c for c in chars if c.status != "pending" and c.level]
    if not active:
        return None
    total = sum(min(c.level, target) for c in active)
    rated = [(c, pace_by_char[c.id]) for c in active if c.id in pace_by_char]
    pending = [p for c, p in rated if not p.reached]
    etas = [p.eta for p in pending if p.eta]
    fastest = max(rated, key=lambda cp: cp[1].per_day, default=(None, None))
    return SquadGoal(
        target=target, deadline=deadline, members=len(active),
        average_level=total / len(active),
        progress=round(total / (len(active) * target) * 100, 1),
        at_target=sum(1 for c in active if c.level >= target),
        average_pace=round(sum(p.per_day for _, p in rated) / len(rated), 1) if rated else None,
        projected=max(etas) if etas and len(etas) == sum(c.level < target for c in active) else None,
        stalled=sum(1 for p in pending if not p.estimated and p.gained == 0),
        fastest=fastest[0] if fastest[1] and fastest[1].per_day > 0 else None,
        fastest_pace=fastest[1].per_day if fastest[1] else 0.0,
        average_estimated=any(p.estimated for _, p in rated),
        fastest_estimated=bool(fastest[1] and fastest[1].estimated),
    )


# --- Groupes fixes de la squad (paramétrés dans « La squad ») ------------------------------------

@dataclass
class GroupPlan:
    name: str
    group_id: str
    members: list[Character]
    dungeon: str | None
    min_level: int
    max_level: int
    fits: list[Character]        # dans la tranche (à 2 niveaux près)
    out: list[Character]         # trop bas ou trop haut pour ce donjon
    mixed_factions: bool
    has_tank: bool
    has_heal: bool

    @property
    def spread(self) -> int:
        levels = [m.level for m in self.members if m.level]
        return max(levels) - min(levels) if levels else 0


def group_plans(groups: list[dict], chars: list[Character]) -> list[GroupPlan]:
    by_id = {c.id: c for c in chars}
    plans = []
    for g in groups:
        members = [by_id[i] for i in g.get("members", []) if i in by_id]
        playable = [m for m in members if m.status != "pending" and m.level]
        factions = {faction_key(m.faction) for m in playable} - {"neutral"}
        best = None
        for name, lo, hi, only in DUNGEONS:
            if only and factions and factions != {only}:
                continue
            fits = [m for m in playable if lo - 2 <= m.level <= hi]
            score = (len(fits), -abs((lo + hi) / 2 - (sum(m.level for m in fits) / len(fits) if fits else 0)))
            if fits and (best is None or score > best[0]):
                best = (score, name, lo, hi, fits)
        roles = set().union(*(character_roles(m) for m in playable))
        if best:
            _, name, lo, hi, fits = best
            out = [m for m in playable if m not in fits]
        else:
            name, lo, hi, fits, out = None, 0, 0, [], playable
        plans.append(GroupPlan(g.get("name", "Groupe"), g.get("id", ""), members, name, lo, hi, fits, out,
                               len(factions) > 1, "tank" in roles, "heal" in roles))
    return plans

@dataclass
class First:
    label: str
    character: Character
    at: datetime


def encounter_kind(event: Event) -> str | None:
    kind = (event.data or {}).get("instance_kind")
    if kind in ("donjon", "raid"):
        return kind
    # Les premières versions n’enregistraient le type que dans le texte.
    for kind in ("donjon", "raid"):
        if f"boss vaincu(s) en {kind}" in event.text:
            return kind
    return None


def firsts(db: Session) -> list[First]:
    """Le premier de la squad à atteindre chaque palier de 10 niveaux, et la première monture."""
    out: dict[str, First] = {}
    rows = db.scalars(select(Event).where(Event.kind.in_(("level", "level_milestone", "mount", "encounter")))
                      .order_by(Event.at)).all()
    for e in rows:
        data = e.data or {}
        if e.kind == "mount":
            out.setdefault("mount", First("Première monture", e.character, e.at))
            continue
        if e.kind == "encounter":
            if kind := encounter_kind(e):
                out.setdefault(kind, First(f"Premier boss relevé en {kind}", e.character, e.at))
            continue
        for milestone in range(10, MAX_LEVEL + 1, 10):
            if int(data.get("from", 0)) < milestone <= int(data.get("to", 0)):
                out.setdefault(f"lvl{milestone}", First(f"Niveau {milestone}", e.character, e.at))
    order = [f"lvl{m}" for m in range(10, MAX_LEVEL + 1, 10)] + ["mount", "donjon", "raid"]
    return [out[k] for k in order if k in out]


@dataclass
class Record:
    icon: str  # nom d'icône du sprite (templates/_icons.html)
    label: str
    character: Character
    value: str
    day: date


def records(events: list[Event], tz_name: str) -> list[Record]:
    """Meilleure journée de leveling et pire hécatombe, tous persos confondus."""
    tz = ZoneInfo(tz_name)
    levels: dict[tuple[int, date], int] = defaultdict(int)
    deaths: dict[tuple[int, date], int] = defaultdict(int)
    encounters = {"donjon": defaultdict(int), "raid": defaultdict(int)}
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
        elif e.kind == "encounter" and (kind := encounter_kind(e)):
            encounters[kind][key] += int(data.get("count", 0))
    out = []
    if levels:
        (cid, day), n = max(levels.items(), key=lambda kv: kv[1])
        if n:
            out.append(Record("bolt", "Plus grosse journée de leveling", chars[cid], f"+{n} niveaux", day))
    if deaths:
        (cid, day), n = max(deaths.items(), key=lambda kv: kv[1])
        if n:
            out.append(Record("skull", "Pire hécatombe en une journée", chars[cid], f"{n} morts", day))
    for kind, scores in encounters.items():
        if scores:
            (cid, day), n = max(scores.items(), key=lambda kv: kv[1])
            if n:
                out.append(Record("trophy", f"Boss en {kind} sur une journée", chars[cid], f"{n} boss vaincus", day))
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

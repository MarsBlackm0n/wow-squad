"""Agrégats partagés par le site (bloc « Cette semaine ») et le récap hebdo envoyé sur Telegram/Discord."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from .db import Character, Event, select, utcnow
from .notify import esc


@dataclass
class Gains:
    character: Character
    levels: int = 0
    achievements: int = 0
    deaths: int = 0
    mounts: int = 0
    ilvl: float = 0.0

    @property
    def active(self) -> bool:
        return bool(self.levels or self.achievements or self.deaths or self.mounts or self.ilvl)


@dataclass
class Award:
    key: str
    icon: str
    title: str
    winner: Character
    detail: str


@dataclass
class Week:
    since: datetime
    gains: list[Gains] = field(default_factory=list)
    awards: list[Award] = field(default_factory=list)

    @property
    def total_levels(self) -> int:
        return sum(g.levels for g in self.gains)

    @property
    def total_deaths(self) -> int:
        return sum(g.deaths for g in self.gains)

    @property
    def total_achievements(self) -> int:
        return sum(g.achievements for g in self.gains)


def compute_week(db: Session, since: datetime | None = None) -> Week:
    since = since or utcnow() - timedelta(days=7)
    chars = db.scalars(select(Character)).all()
    gains = {c.id: Gains(c) for c in chars}
    for ev in db.scalars(select(Event).where(Event.at >= since)).all():
        g = gains.get(ev.character_id)
        if g is None:
            continue
        data = ev.data or {}
        if ev.kind in ("level", "level_milestone"):
            g.levels += max(int(data.get("to", 0)) - int(data.get("from", 0)), 0)
        elif ev.kind == "achievement":
            g.achievements += int(data.get("count", 1))
        elif ev.kind == "deaths":
            g.deaths += int(data.get("count", 1))
        elif ev.kind == "mount":
            g.mounts += 1
        elif ev.kind == "ilvl":
            g.ilvl += float(data.get("to", 0)) - float(data.get("from", 0))

    week = Week(since=since, gains=sorted(gains.values(), key=lambda g: (g.levels, g.achievements), reverse=True))
    week.awards = _awards(week.gains)
    return week


def _awards(gains: list[Gains]) -> list[Award]:
    def best(attr: str) -> Gains | None:
        top = max(gains, key=lambda g: getattr(g, attr), default=None)
        return top if top and getattr(top, attr) > 0 else None

    awards = []
    if g := best("levels"):
        awards.append(Award("rocket", "🚀", "La fusée", g.character, f"+{g.levels} niveaux"))
    if g := best("achievements"):
        awards.append(Award("hunter", "🏆", "Chasseur de hauts faits", g.character, f"{g.achievements} hauts faits"))
    if g := best("mounts"):
        awards.append(Award("collector", "🐎", "L'écurie", g.character, f"{g.mounts} montures"))
    if g := best("deaths"):
        awards.append(Award("graveyard", "💀", "Abonné au cimetière", g.character, f"{g.deaths} morts"))
    return awards


def leaderboard(db: Session) -> list[Character]:
    chars = db.scalars(select(Character)).all()
    return sorted(chars, key=lambda c: (c.status != "pending", c.level, c.ilvl, c.achievement_points), reverse=True)


def recap_message(db: Session, squad_name: str, public_url: str = "") -> str:
    week = compute_week(db)
    lines = [f"📜 <b>Récap de la semaine — {esc(squad_name)}</b>", ""]
    active = [g for g in week.gains if g.active]
    if not active:
        lines.append("Semaine calme : personne n'a bougé. Ce soir, on se connecte ? 👀")
    else:
        lines.append(f"À vous tous : <b>+{week.total_levels}</b> niveaux, <b>{week.total_achievements}</b> hauts faits, "
                     f"<b>{week.total_deaths}</b> morts.")
        lines.append("")
        for a in week.awards:
            lines.append(f"{a.icon} <b>{esc(a.title)}</b> : {esc(a.winner.display_name)} ({esc(a.detail)})")
        lines.append("")
        lines.append("<b>Classement</b>")
        for i, c in enumerate([c for c in leaderboard(db) if c.status != "pending"][:10], 1):
            medal = {1: "🥇", 2: "🥈", 3: "🥉"}.get(i, f"{i}.")
            lines.append(f"{medal} {esc(c.display_name)} · niv. {c.level}" + (f" · {c.ilvl:g} ilvl" if c.ilvl else ""))
    if public_url:
        lines += ["", f'<a href="{esc(public_url)}">Voir le QG</a>']
    return "\n".join(lines)


def event_message(event: Event) -> str:
    return f"{event.icon} {esc(event.text)}"

"""Présentation des profils de guilde Forever et cache à courte durée."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from time import monotonic

from .blizzard import Blizzard, BlizzardError, character_slug
from .collector import _int, _name, _ruleset_name
from .db import SessionLocal, kv_get, kv_set, utcnow

log = logging.getLogger(__name__)
ROSTER_HISTORY = "guild_roster_history"
ROSTER_INTERVAL = 6 * 60 * 60
ROSTER_WINDOW = 7 * 24 * 60 * 60

_cache: dict[tuple[str, str], tuple[float, dict]] = {}
_cache_lock = asyncio.Lock()


def _timestamp(raw) -> datetime | None:
    value = _int(raw)
    if value is None or value <= 0:
        return None
    try:
        return datetime.fromtimestamp(value / 1000, tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None


def guild_details(profile: dict | None, roster: dict | None, activity: dict | None,
                  achievements: dict | None) -> dict:
    profile = profile or {}
    achievements = achievements or {}
    members = []
    for row in (roster or {}).get("members") or []:
        char = row.get("character") or {}
        name, surname = _name(char.get("name")), _name(char.get("surname"))
        if name:
            members.append({"name": f"{name} {surname}".strip(),
                            "slug": char.get("slug") or character_slug(name, surname),
                            "level": _int(char.get("level"))})
    members.sort(key=lambda member: (-(member["level"] or 0), member["name"].casefold()))
    levels = [member["level"] for member in members if member["level"] is not None]
    level_bands = [(f"{low}–{low + 9}", sum(low <= level < low + 10 for level in levels))
                   for low in range(10, 60, 10)]
    level_bands = [("1–9", sum(1 <= level < 10 for level in levels)), *level_bands,
                   ("60", sum(level == 60 for level in levels))]
    recent = []
    for event in (activity or {}).get("activities") or []:
        earned = event.get("character_achievement") or event.get("guild_achievement") or {}
        completed = event.get("encounter_completed") or {}
        boss = _name(completed.get("encounter"))
        achievement = _name(earned.get("achievement"))
        at = _timestamp(event.get("timestamp"))
        if at and (boss or achievement):
            char = earned.get("character") or {}
            player = f"{_name(char.get('name'))} {_name(char.get('surname'))}".strip()
            detail = f"{boss} vaincu" if boss else f"{player} · {achievement}" if player else achievement
            recent.append({"at": at, "detail": detail})
    faction = profile.get("faction") or ((roster or {}).get("guild") or {}).get("faction")
    return {"name": _name(profile.get("name")), "faction": _ruleset_name(faction),
            "ruleset": _ruleset_name(profile.get("ruleset")), "created": _timestamp(profile.get("created_timestamp")),
            "count": _int(profile.get("member_count")), "roster_available": isinstance((roster or {}).get("members"), list),
            "levels_known": len(levels), "level_bands": level_bands,
            "average_level": sum(levels) / len(levels) if levels else None,
            "max_level_count": sum(level == 60 for level in levels),
            "members": members, "recent": sorted(recent, key=lambda item: item["at"], reverse=True)[:30],
            "achievement_points": _int(achievements.get("total_points"))}


def guild_slug(character) -> str:
    return (character.state or {}).get("guild_slug") or character_slug(character.guild)


def tracked_guilds(characters) -> list[list]:
    groups = {}
    for character in characters:
        if character.guild:
            groups.setdefault((character.region, guild_slug(character)), []).append(character)
    for members in groups.values():
        members.sort(key=lambda member: member.display_name.casefold())
    return sorted(groups.values(), key=lambda members: (members[0].guild.casefold(), members[0].region))


def roster_key(region: str, slug: str) -> str:
    return f"{region}:{slug}"


def roster_movements(db, region: str, slug: str) -> list:
    history = (kv_get(db, ROSTER_HISTORY) or {}).get(roster_key(region, slug), {})
    cutoff = utcnow().timestamp() - ROSTER_WINDOW
    return [{**change, "at": datetime.fromtimestamp(change["at"], timezone.utc)}
            for change in history.get("changes", []) if change["at"] >= cutoff]


def compare_roster(previous: dict, members: dict, now: float) -> list:
    old = previous.get("members")
    cutoff = now - ROSTER_WINDOW
    if old is None or previous.get("observed_at", 0) < cutoff:
        return []
    changes = [{"at": now, "joined": True, "name": members[slug]} for slug in sorted(members.keys() - old.keys())]
    changes.extend({"at": now, "joined": False, "name": old[slug]} for slug in sorted(old.keys() - members.keys()))
    return (changes + [change for change in previous.get("changes", []) if change["at"] >= cutoff])[:100]


async def refresh_rosters(bz: Blizzard, characters) -> None:
    with SessionLocal() as db:
        previous = kv_get(db, ROSTER_HISTORY) or {}
    now = utcnow().timestamp()
    history = {}
    for group in tracked_guilds(characters):
        char = group[0]
        if char.region != bz.region:
            continue
        slug = guild_slug(char)
        key = roster_key(char.region, slug)
        entry = dict(previous.get(key, {}))
        if entry.get("observed_at", 0) < now - ROSTER_WINDOW:
            entry.pop("members", None)
            entry.pop("changes", None)
        history[key] = entry
        if now - entry.get("checked_at", 0) < ROSTER_INTERVAL:
            continue
        entry["checked_at"] = now
        try:
            guild = await fetch_guild(bz, slug)
        except BlizzardError as exc:
            log.info("Roster de guilde indisponible pour %s : %s", slug, exc)
            continue
        members = {member["slug"]: member["name"] for member in guild["members"]}
        if not guild["roster_available"] or not members or len(members) != guild["count"]:
            continue
        entry.update(changes=compare_roster(entry, members, now), members=members, observed_at=now)
    if history != previous:
        with SessionLocal() as db:
            kv_set(db, ROSTER_HISTORY, history)
            db.commit()


async def fetch_guild(bz: Blizzard, slug: str) -> dict:
    key = (bz.region, slug)
    async with _cache_lock:
        now = monotonic()
        cached = _cache.get(key)
        if cached and cached[0] > now:
            return cached[1]
        result = await asyncio.gather(*(bz.guild_part(slug, suffix) for suffix in ("", "/roster", "/activity", "/achievements")))
        data = guild_details(*result)
        if len(_cache) >= 32:
            _cache.pop(min(_cache, key=lambda entry: _cache[entry][0]))
        _cache[key] = (monotonic() + (300 if any(item is not None for item in result) else 60), data)
        return data

"""Présentation des profils de guilde Forever et cache à courte durée."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from time import monotonic

from .blizzard import Blizzard, character_slug
from .collector import _int, _name, _ruleset_name

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
    recent = []
    for event in (activity or {}).get("activities") or []:
        earned = event.get("character_achievement") or {}
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
            "count": _int(profile.get("member_count")), "roster_available": roster is not None,
            "levels_known": len(levels),
            "average_level": sum(levels) / len(levels) if levels else None,
            "max_level_count": sum(level == 60 for level in levels),
            "members": members, "recent": sorted(recent, key=lambda item: item["at"], reverse=True)[:8],
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

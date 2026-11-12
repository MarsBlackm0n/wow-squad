"""Récupère l'état d'un perso, le compare au précédent et en tire des événements (fil d'activité).

Les fonctions `parse_*` et `diff_events` sont pures (testées sans réseau). Les noms de champs suivent l'API
Profile WoW ; Forever en change quelques-uns (surnom et ruleset au lieu du royaume), d'où les lectures tolérantes.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from urllib.parse import unquote, urlparse

from sqlalchemy.orm import Session

from . import photos
from .blizzard import Blizzard, NotFound, character_slug
from .db import Account, Character, Event, PlayerPhoto, Snapshot, select, utcnow

log = logging.getLogger(__name__)

STAT_TOTAL_DEATHS = 60
STAT_HONORABLE_KILLS = 588
DEATH_NAMES = ("total deaths", "morts totales", "nombre total de morts", "décès")
HK_NAMES = ("honorable kills", "victoires honorables", "victoires honorables totales")
IGNORED_SLOTS = {"SHIRT", "TABARD"}
PROFESSION_RANKS = (75, 150, 225, 300, 375, 450)
# Événements poussés en direct sur Telegram / Discord (les autres restent dans le fil et le récap).
LIVE_KINDS = {"joined", "level_milestone", "achievement", "mount", "profession_rank", "guild", "ilvl"}


def _name(value) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        inner = value.get("name", value)
        if isinstance(inner, str):
            return inner
        if isinstance(inner, dict):  # nom localisé sans `locale` : {"fr_FR": "...", ...}
            return inner.get("fr_FR") or inner.get("en_GB") or inner.get("en_US") or next(
                (v for v in inner.values() if isinstance(v, str)), "")
    return str(value)


def _int(value) -> int | None:
    if isinstance(value, dict):
        value = value.get("value")
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def parse_profile(profile: dict) -> dict:
    ilvl = profile.get("equipped_item_level") or profile.get("average_item_level") or 0
    last_login = profile.get("last_login_timestamp")
    return {
        "name": _name(profile.get("name")),
        "surname": _name(profile.get("surname")),
        "level": _int(profile.get("level")) or 0,
        "class_name": _name(profile.get("character_class") or profile.get("playable_class")),
        "race": _name(profile.get("race") or profile.get("playable_race")),
        "faction": _name(profile.get("faction")),
        "guild": _name(profile.get("guild")),
        "ruleset": _name(profile.get("ruleset")),
        "achievement_points": _int(profile.get("achievement_points")) or 0,
        "ilvl": float(ilvl or 0),
        "last_login": int(last_login) if last_login else None,
    }


def parse_equipment(equipment: dict | None) -> float | None:
    if not equipment:
        return None
    levels = []
    for item in equipment.get("equipped_items") or []:
        slot = (item.get("slot") or {}).get("type", "")
        level = _int(item.get("level"))
        if slot not in IGNORED_SLOTS and level:
            levels.append(level)
    return round(sum(levels) / len(levels), 1) if levels else None


def parse_media(media: dict | None) -> str:
    if not media:
        return ""
    assets = {a.get("key"): a.get("value") for a in media.get("assets") or []}
    return assets.get("avatar") or assets.get("inset") or media.get("avatar_url") or ""


def _walk_statistics(node: dict):
    for stat in node.get("statistics") or []:
        yield stat
    for key in ("categories", "sub_categories"):
        for child in node.get(key) or []:
            yield from _walk_statistics(child)


def parse_statistics(stats: dict | None) -> dict:
    out: dict[str, int | None] = {"deaths": None, "honorable_kills": None}
    if not stats:
        return out
    for stat in _walk_statistics(stats):
        sid, name = stat.get("id"), _name(stat).lower()
        qty = _int(stat.get("quantity"))
        if qty is None:
            continue
        if sid == STAT_TOTAL_DEATHS or (out["deaths"] is None and name in DEATH_NAMES):
            out["deaths"] = qty
        elif sid == STAT_HONORABLE_KILLS or (out["honorable_kills"] is None and name in HK_NAMES):
            out["honorable_kills"] = qty
    return out


def parse_achievements(data: dict | None) -> dict | None:
    """{id: nom} des hauts faits terminés."""
    if not data:
        return None
    done = {}
    for entry in data.get("achievements") or []:
        completed = entry.get("completed_timestamp") or (entry.get("criteria") or {}).get("is_completed")
        if completed:
            ach = entry.get("achievement") or {}
            done[str(entry.get("id") or ach.get("id"))] = _name(ach) or f"Haut fait {entry.get('id')}"
    return done


def parse_mounts(data: dict | None) -> dict | None:
    if not data:
        return None
    return {str((m.get("mount") or {}).get("id")): _name(m.get("mount")) for m in data.get("mounts") or []}


def parse_professions(data: dict | None) -> dict | None:
    """{métier: compétence} (on garde le meilleur palier quand il y en a plusieurs)."""
    if not data:
        return None
    out = {}
    for group in ("primaries", "secondaries"):
        for p in data.get(group) or []:
            name = _name(p.get("profession"))
            skills = [_int(t.get("skill_points")) or 0 for t in p.get("tiers") or []]
            skills.append(_int(p.get("skill_points")) or 0)
            if name:
                out[name] = max(skills)
    return out


@dataclass
class NewEvent:
    kind: str
    icon: str
    text: str
    data: dict = field(default_factory=dict)


def diff_events(who: str, old: dict, new: dict) -> list[NewEvent]:
    """Compare deux états (même forme que `Character.state`) et liste ce qui mérite d'être raconté."""
    if not old.get("synced"):
        return [NewEvent("joined", "👋", f"{who} rejoint la squad (niveau {new.get('level', 0)})")]

    events: list[NewEvent] = []
    old_lvl, new_lvl = old.get("level") or 0, new.get("level") or 0
    if new_lvl > old_lvl:
        milestone = next((m for m in range(new_lvl, old_lvl, -1) if m % 10 == 0), None)
        if milestone:
            events.append(NewEvent("level_milestone", "🎉", f"{who} atteint le niveau {new_lvl} !",
                                   {"from": old_lvl, "to": new_lvl}))
        else:
            gain = new_lvl - old_lvl
            events.append(NewEvent("level", "⬆️", f"{who} passe niveau {new_lvl}" + (f" (+{gain})" if gain > 1 else ""),
                                   {"from": old_lvl, "to": new_lvl}))

    def added(key: str) -> dict:
        before, after = old.get(key), new.get(key)
        if before is None or after is None:  # source pas encore disponible : pas de faux « nouveau »
            return {}
        return {k: v for k, v in after.items() if k not in before}

    new_ach = added("achievements")
    for ach_id, name in list(new_ach.items())[:5]:
        events.append(NewEvent("achievement", "🏆", f"{who} obtient le haut fait « {name} »", {"id": ach_id}))
    if len(new_ach) > 5:
        events.append(NewEvent("achievement", "🏆", f"{who} enchaîne {len(new_ach) - 5} autres hauts faits",
                               {"count": len(new_ach) - 5}))

    for mount_id, name in added("mounts").items():
        events.append(NewEvent("mount", "🐎", f"{who} obtient la monture « {name} »", {"id": mount_id}))

    old_prof, new_prof = old.get("professions") or {}, new.get("professions") or {}
    for prof, skill in new_prof.items():
        before = old_prof.get(prof)
        if before is None and old.get("professions") is not None:
            events.append(NewEvent("profession", "🛠️", f"{who} apprend {prof}", {"profession": prof}))
            continue
        rank = next((r for r in reversed(PROFESSION_RANKS) if (before or 0) < r <= skill), None)
        if rank:
            events.append(NewEvent("profession_rank", "🛠️", f"{who} atteint {skill} en {prof}",
                                   {"profession": prof, "skill": skill}))

    old_ilvl, new_ilvl = old.get("ilvl") or 0, new.get("ilvl") or 0
    if old_ilvl and new_ilvl - old_ilvl >= 1:
        events.append(NewEvent("ilvl", "🛡️", f"{who} monte à {new_ilvl:g} d'ilvl (+{new_ilvl - old_ilvl:.1f})",
                               {"from": old_ilvl, "to": new_ilvl}))

    old_deaths, new_deaths = old.get("deaths"), new.get("deaths")
    if old_deaths is not None and new_deaths is not None and new_deaths > old_deaths:
        n = new_deaths - old_deaths
        events.append(NewEvent("deaths", "💀", f"{who} est mort {n} fois" if n > 1 else f"{who} est mort",
                               {"count": n, "total": new_deaths}))

    # Une nouvelle date de connexion = une session de jeu (sert au journal et à détecter les soirées en groupe).
    if new.get("last_login") and old.get("last_login") and new["last_login"] > old["last_login"]:
        events.append(NewEvent("session", "🎮", f"{who} était en jeu", {"login": new["last_login"]}))

    if new.get("guild") != old.get("guild") and new.get("guild") is not None:
        if new.get("guild"):
            events.append(NewEvent("guild", "🏰", f"{who} rejoint la guilde <{new['guild']}>", {"guild": new["guild"]}))
        else:
            events.append(NewEvent("guild", "🏰", f"{who} quitte la guilde <{old.get('guild')}>", {}))
    return events


def slug_from_href(href: str) -> str:
    path = urlparse(href or "").path
    return unquote(path.rsplit("/character/", 1)[-1]).lower() if "/character/" in path else ""


def account_entry_slug(entry: dict) -> str:
    surname = _name(entry.get("surname"))
    if surname:
        return character_slug(_name(entry.get("name")), surname)
    return slug_from_href((entry.get("character") or {}).get("href", "")) or character_slug(_name(entry.get("name")))


def account_entry_summary(entry: dict) -> dict:
    return {
        "name": _name(entry.get("name")),
        "surname": _name(entry.get("surname")),
        "level": _int(entry.get("level")) or 0,
        "class_name": _name(entry.get("playable_class")),
        "race": _name(entry.get("playable_race")),
        "faction": _name(entry.get("faction")),
    }


# --- Orchestration (réseau + base) ------------------------------------------------------------------

async def fetch_state(bz: Blizzard, char: Character) -> tuple[dict, str]:
    """Nouvel état du perso et son statut. Lève NotFound si l'API ne connaît pas (encore) le perso."""
    profile = parse_profile(await bz.character_profile(char.slug))
    parts = ("/equipment", "/character-media", "/achievements", "/statistics", "/professions",
             "/collections/mounts", "/pvp-summary")
    equipment, media, achievements, statistics, professions, mounts, pvp = await asyncio.gather(
        *(bz.character_part(char.slug, p) for p in parts))
    state = dict(profile)
    state["ilvl"] = parse_equipment(equipment) or profile["ilvl"]
    state["avatar_url"] = parse_media(media)
    state.update(parse_statistics(statistics))
    if state.get("honorable_kills") is None and pvp:
        state["honorable_kills"] = _int(pvp.get("honorable_kills"))
    state["achievements"] = parse_achievements(achievements)
    state["mounts"] = parse_mounts(mounts)
    state["professions"] = parse_professions(professions)
    return state, "ok"


def apply_state(db: Session, char: Character, state: dict, status: str, message: str = "") -> list[Event]:
    old = dict(char.state or {})
    # On conserve les collections déjà connues si l'endpoint n'a pas répondu cette fois-ci.
    for key in ("achievements", "mounts", "professions", "deaths", "honorable_kills", "avatar_url"):
        if state.get(key) is None and old.get(key) is not None:
            state[key] = old[key]
    state["synced"] = True
    if old.get("account_summary"):
        state["account_summary"] = old["account_summary"]

    new_events = []
    for ev in diff_events(char.display_name or state.get("name", char.slug), old, state):
        event = Event(character=char, kind=ev.kind, icon=ev.icon, text=ev.text, data=ev.data)
        db.add(event)
        new_events.append(event)

    char.name = state.get("name") or char.name
    char.surname = state.get("surname") or char.surname
    char.level = state.get("level") or char.level
    char.class_name = state.get("class_name") or char.class_name
    char.race = state.get("race") or char.race
    char.faction = state.get("faction") or char.faction
    char.guild = state.get("guild") or ""
    char.ruleset = state.get("ruleset") or char.ruleset
    char.achievement_points = state.get("achievement_points") or char.achievement_points
    char.ilvl = state.get("ilvl") or char.ilvl
    char.deaths = state.get("deaths")
    char.avatar_url = state.get("avatar_url") or char.avatar_url
    if state.get("last_login"):
        char.last_login = datetime.fromtimestamp(state["last_login"] / 1000, tz=timezone.utc)
    char.status, char.status_message = status, message
    char.state = state
    char.refreshed_at = utcnow()
    _maybe_snapshot(db, char, state)
    return new_events


def _maybe_snapshot(db: Session, char: Character, state: dict) -> None:
    last = db.scalars(select(Snapshot).where(Snapshot.character_id == char.id)
                      .order_by(Snapshot.taken_at.desc()).limit(1)).first()
    values = {
        "level": char.level, "ilvl": char.ilvl, "achievement_points": char.achievement_points,
        "deaths": char.deaths, "honorable_kills": state.get("honorable_kills"),
        "mounts": len(state["mounts"]) if state.get("mounts") is not None else None,
    }
    if last:
        unchanged = all(getattr(last, k) == v for k, v in values.items())
        taken_at = last.taken_at if last.taken_at.tzinfo else last.taken_at.replace(tzinfo=timezone.utc)
        if unchanged and utcnow() - taken_at < timedelta(hours=12):
            return
    db.add(Snapshot(character=char, **values))


async def refresh_character(db: Session, bz: Blizzard, char: Character) -> list[Event]:
    try:
        state, status = await fetch_state(bz, char)
        return apply_state(db, char, state, status)
    except NotFound:
        summary = (char.state or {}).get("account_summary")
        if summary:  # l'endpoint public ne répond pas mais le compte Battle.net du pote, si
            state = {k: v for k, v in (char.state or {}).items() if k not in ("synced",)}
            state.update(summary)
            state["synced"] = (char.state or {}).get("synced", False)
            return apply_state(db, char, state, "ok", "Données issues du compte Battle.net (profil public indisponible)")
        char.status = "pending"
        char.status_message = ("Pas encore visible dans l'API : il faut être niveau 10 et s'être déconnecté. "
                               "Vérifie aussi l'orthographe nom-surnom.")
        char.refreshed_at = utcnow()
        return []
    except Exception as exc:  # noqa: BLE001 - un perso en erreur ne doit pas bloquer les autres
        log.warning("Rafraîchissement de %s échoué : %s", char.slug, exc)
        char.status, char.status_message = "error", str(exc)[:300]
        char.refreshed_at = utcnow()
        return []


async def sync_account(db: Session, bz: Blizzard, account: Account, user_token: str, add_missing: bool = False) -> int:
    """Met à jour le résumé (niveau, classe...) des persos Forever du compte. Renvoie le nombre de persos ajoutés."""
    entries = await bz.account_characters(user_token)
    db.refresh(account)
    return apply_account_characters(db, bz.region, account, entries, add_missing)


def apply_account_characters(db: Session, region: str, account: Account, entries: list[dict],
                             add_missing: bool = False) -> int:
    added = 0
    first_import = add_missing and db.scalar(select(Character.id).where(Character.account_id == account.id).limit(1)) is None
    for entry in entries:
        slug = account_entry_slug(entry)
        if not slug:
            continue
        char = db.scalars(select(Character).where(Character.region == region, Character.slug == slug)).first()
        summary = account_entry_summary(entry)
        if char is None:
            if not add_missing:
                continue
            char = Character(region=region, slug=slug, name=summary["name"] or slug, surname=summary["surname"],
                             player=account.battletag.split("#")[0], battletag=account.battletag, state={})
            db.add(char)
            db.flush()
            added += 1
        elif char.account_id != account.id and (char.account_id is not None or not add_missing):
            continue
        if first_import and char.account_id is None:
            old_key = photos.character_key(char)
            previous_photo = db.get(PlayerPhoto, old_key) if old_key else None
            if previous_photo:
                new_key = f"account/{account.id}"
                if not db.get(PlayerPhoto, new_key):
                    db.add(PlayerPhoto(key=new_key, image=previous_photo.image, updated_at=previous_photo.updated_at))
                first_import = False
        char.account_id = account.id
        char.battletag = account.battletag
        state = dict(char.state or {})
        state["account_summary"] = summary
        char.state = state
    account.last_sync_at = utcnow()
    return added

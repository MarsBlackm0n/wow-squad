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
from .db import Account, Character, Event, PlayerPhoto, Snapshot, kv_get, kv_set, select, utcnow
from .ui import _fold, class_key

log = logging.getLogger(__name__)

STAT_TOTAL_DEATHS = 60
STAT_HONORABLE_KILLS = 588
DEATH_NAMES = ("total deaths", "morts totales", "nombre total de morts", "total des morts", "nombre total de deces", "deces")
HK_NAMES = ("honorable kills", "victoires honorables", "victoires honorables totales")
IGNORED_SLOTS = {"SHIRT", "TABARD"}
PROFESSION_RANKS = (75, 150, 225, 300, 375, 450)
# Événements poussés en direct sur Telegram / Discord (les autres restent dans le fil et le récap).
LIVE_KINDS = {"joined", "level_milestone", "achievement", "mount", "profession_rank", "guild", "ilvl", "deaths"}


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
    last_login = _int(profile.get("last_login_timestamp"))
    return {
        "name": _name(profile.get("name")),
        "surname": _name(profile.get("surname")),
        "level": _int(profile.get("level")) or 0,
        "experience": _int(profile.get("experience")),
        "class_name": _name(profile.get("character_class") or profile.get("playable_class")),
        "race": _name(profile.get("race") or profile.get("playable_race")),
        "faction": _name(profile.get("faction")),
        "guild": _name(profile.get("guild")),
        "guild_slug": _name((profile.get("guild") or {}).get("slug")) if isinstance(profile.get("guild"), dict) else "",
        "ruleset": _ruleset_name(profile.get("ruleset")),
        "achievement_points": _int(profile.get("achievement_points")) or 0,
        "ilvl": float(ilvl or 0),
        "last_login": last_login if last_login and last_login > 0 else None,
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


def equipped_items(data: dict | None) -> dict | None:
    if data is None or "equipped_items" not in data:
        return None
    slots = {}
    for item in data["equipped_items"] or []:
        slot = item.get("slot") or {}
        name = _name(item.get("name") or item.get("item"))
        if slot.get("type") and name:
            slots[slot["type"]] = {"name": name, "slot": _name(slot) or slot["type"],
                                   "id": (item.get("item") or {}).get("id"), "level": _int(item.get("level"))}
    return slots


def character_build(data: dict | None) -> list | None:
    if data is None or "specializations" not in data:
        return None
    builds = []
    for spec in data["specializations"] or []:
        name = _name(spec.get("specialization") or spec.get("talent_tree"))
        trees = [spec, *(spec.get("loadouts") or spec.get("talent_loadouts") or [])]
        for tree in trees:
            talents = []
            for talent in tree.get("talents") or tree.get("selected_talents") or []:
                tooltip = talent.get("spell_tooltip") or {}
                title = _name(talent.get("talent") or tooltip.get("spell") or talent.get("spell") or talent.get("name"))
                if title:
                    talents.append({"name": title, "rank": _int(talent.get("rank") or talent.get("points_spent"))})
            if talents or (tree is spec and name and len(trees) == 1):
                builds.append({"name": _name(tree.get("name")) or name, "talents": talents})
    return builds


def character_reputations(data: dict | None) -> list | None:
    if data is None or "reputations" not in data:
        return None
    result = []
    for row in data["reputations"] or []:
        name = _name(row.get("faction"))
        standing = row.get("standing") or {}
        current, maximum = _int(standing.get("value")), _int(standing.get("max"))
        remaining = maximum - current if current is not None and maximum is not None and 0 <= current < maximum else None
        if name:
            result.append({"name": name, "standing": _name(standing), "remaining": remaining})
    return sorted(result, key=lambda row: (row["remaining"] is None, row["remaining"] or 0, row["name"].casefold()))


def profession_recipes(data: dict | None) -> dict | None:
    result = {}
    available = False
    for group in ("primaries", "secondaries"):
        for profession in (data or {}).get(group) or []:
            name = _name(profession.get("profession"))
            for tier in [profession, *(profession.get("tiers") or [])]:
                if "known_recipes" in tier:
                    available = True
                    if name:
                        result.setdefault(name, set()).update(filter(None, map(_name, tier["known_recipes"] or [])))
    return {name: sorted(recipes, key=str.casefold) for name, recipes in result.items()} if available else None


def completed_quest_ids(data: dict | None) -> list[int] | None:
    if data is None or not ("quests" in data or "character" in data):
        return None
    return sorted({quest["id"] for quest in data.get("quests") or [] if isinstance(quest.get("id"), int)})


def completed_quests(data: dict | None) -> int | None:
    ids = completed_quest_ids(data)
    return len(ids) if ids is not None else None


def encounter_counts(data: dict | None) -> dict | None:
    """Victoires par boss et difficulté, telles qu'exposées par l'armurerie."""
    if data is None or not ("character" in data or "expansions" in data):
        return None
    counts = {}
    for expansion in data.get("expansions") or []:
        for instance in expansion.get("instances") or []:
            instance_id = (instance.get("instance") or {}).get("id")
            for mode in instance.get("modes") or []:
                difficulty = (mode.get("difficulty") or {}).get("type") or "default"
                for row in (mode.get("progress") or {}).get("encounters") or []:
                    encounter = row.get("encounter") or {}
                    count = _int(row.get("completed_count"))
                    if encounter.get("id") is not None and count is not None:
                        key = f"{instance_id}:{difficulty}:{encounter['id']}"
                        counts[key] = {"count": count, "name": _name(encounter)}
                        if instance_name := _name(instance.get("instance")):
                            counts[key]["instance"] = instance_name
                        if label := _name(mode.get("difficulty")):
                            counts[key]["difficulty"] = label
    return counts


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
        sid, name = _int(stat.get("id")), _fold(_name(stat)).strip()
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


DEATH_CLASS_LINES = {
    "hunter": ("Le pet a tenu plus longtemps que son maître.", "Feindre la mort. Le mot important, c’était « feindre »."),
    "warrior": ("La charge était impeccable. Le retour, à pied.", "Le cimetière a pris l’aggro."),
    "mage": ("Pierre de foyer annulée pour cause de décès.", "Le bloc de glace était probablement en recharge."),
    "rogue": ("Disparition réussie. Réapparition au cimetière.", "Furtivité : 100 %. Survie : à revoir."),
    "priest": ("Le rez commence toujours par un volontaire.", "La bulle avait visiblement pris sa pause."),
    "paladin": ("Bubulle + pierre de foyer : il manquait la bubulle.", "La Lumière a laissé un message d’absence."),
    "warlock": ("La pierre d’âme était dans l’autre sac.", "Même le diablotin a lâché un « gg »."),
    "shaman": ("Le totem n’a rien vu, rien entendu.", "Le totem de vie n’a pas reçu le mémo."),
    "druid": ("La forme de voyage n’était pas censée mener au cimetière.", "Nouvelle forme débloquée : esprit."),
}
DEATH_RACE_LINES = (
    (("troll",), ("Les Loas ont demandé une rediffusion.", "Bwonsamdi note le rendez-vous.")),
    (("orc",), ("Pour la Horde ! Et pour la réparatrice.", "Lok’tar ! On retentera le « ogar ».")),
    (("tauren",), ("La Terre-Mère avait dit de regarder où poser les sabots.", "La Terre-Mère a vu le pull. Elle préfère garder le silence.")),
    (("mort vivant", "mort-vivant", "undead", "reprouve"), ("Le cimetière : comme à la maison.", "Même pour un Réprouvé, ça compte.")),
    (("nain", "dwarf"), ("Le tavernier garde une chope au frais.", "Forme de pierre, très convaincante cette fois.")),
    (("gnome",), ("L’ingénierie décline toute responsabilité.", "Le plan B était encore au stade du prototype.")),
    (("elfe", "elf"), ("Elune a regardé ailleurs au mauvais moment.", "La prochaine fois, on demandera un buff avant le pull.")),
    (("humain", "human"), ("Hurlevent recommande un pull de moins.", "La garde de Hurlevent prend les paris sur le prochain pull.")),
)
DEATH_GENERIC_LINES = (
    "Le cimetière a débloqué un nouveau point de vol.",
    "Aggro prise. Respawn commandé.",
    "Le réparateur vient de commander une deuxième échoppe.",
)


def death_message(who: str, new: dict, count: int) -> str:
    class_lines = DEATH_CLASS_LINES.get(class_key(new.get("class_name") or ""), ())
    race = _fold(new.get("race") or "")
    race_lines = next((lines for aliases, lines in DEATH_RACE_LINES if any(alias in race for alias in aliases)), ())
    lines = class_lines + race_lines or DEATH_GENERIC_LINES
    joke = lines[(new["deaths"] - 1) % len(lines)]
    label = f"{count} morts relevées" if count > 1 else "1 mort relevée"
    return f"{who} · {label}. {joke}"


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
        events.append(NewEvent("deaths", "💀", death_message(who, new, n),
                               {"count": n, "total": new_deaths}))

    for key, kind, icon, label in (
        ("quests_completed", "quests", "📜", "quête(s) terminée(s)"),
        ("honorable_kills", "pvp", "⚔️", "victoire(s) honorable(s)"),
    ):
        before, after = old.get(key), new.get(key)
        if before is not None and after is not None and after > before:
            gain = after - before
            events.append(NewEvent(kind, icon, f"{who} · +{gain} {label}", {"count": gain}))

    for key, label in (("dungeon_encounters", "donjon"), ("raid_encounters", "raid")):
        before, after = old.get(key), new.get(key)
        if before is None or after is None:
            continue
        gains = [(row["name"], row["count"] - before.get(boss, {}).get("count", 0))
                 for boss, row in after.items() if row["count"] > before.get(boss, {}).get("count", 0)]
        if gains:
            total = sum(gain for _, gain in gains)
            names = list(dict.fromkeys(name for name, _ in gains if name))
            detail = f" · {', '.join(names[:3])}" if names else ""
            events.append(NewEvent("encounter", "⚔️", f"{who} · {total} boss vaincu(s) en {label}{detail}",
                                   {"count": total, "instance_kind": label, "bosses": names[:3]}))

    # Une nouvelle date de connexion = une session de jeu (sert au journal et à détecter les soirées en groupe).
    if new.get("last_login") and old.get("last_login") and new["last_login"] > old["last_login"]:
        events.append(NewEvent("session", "🎮", f"{who} était en jeu", {"login": new["last_login"]}))

    if new.get("guild") != old.get("guild") and new.get("guild") is not None:
        if new.get("guild"):
            events.append(NewEvent("guild", "🏰", f"{who} rejoint la guilde {new['guild']}", {"guild": new["guild"]}))
        else:
            events.append(NewEvent("guild", "🏰", f"{who} quitte la guilde {old.get('guild')}", {}))
    return events


def slug_from_href(href: str) -> str:
    path = urlparse(href or "").path
    prefix = "/profile/wow/character/"
    if not path.startswith(prefix):
        return ""
    slug = unquote(path.removeprefix(prefix).strip("/")).lower()
    return slug if "-" in slug and 1 < len(slug) <= 80 and "/" not in slug else ""


def account_entry_slug(entry: dict) -> str:
    href = (entry.get("character") or {}).get("href", "")
    slug = slug_from_href(href)
    if slug:
        return slug
    slug = character_slug(_name(entry.get("name")), _name(entry.get("surname")))
    return slug if "-" in slug else ""


def _ruleset_name(value) -> str:
    if isinstance(value, dict):
        return _name(value.get("name")) or _name(value.get("type"))
    return _name(value)


def account_entry_summary(entry: dict) -> dict:
    summary = parse_profile(entry)
    return {key: summary[key] for key in
            ("name", "surname", "level", "class_name", "race", "faction", "ruleset", "experience", "last_login")}



# --- Orchestration (réseau + base) ------------------------------------------------------------------

async def fetch_state(bz: Blizzard, char: Character) -> tuple[dict, str]:
    """Nouvel état du perso et son statut. Lève NotFound si l'API ne connaît pas (encore) le perso."""
    profile = parse_profile(await bz.character_profile(char.slug))
    parts = ("/equipment", "/character-media", "/achievements", "/statistics", "/professions",
             "/collections/mounts", "/pvp-summary", "/achievements/statistics", "/quests/completed", "/encounters/dungeons", "/encounters/raids",
             "/specializations", "/reputations", "/titles")
    equipment, media, achievements, statistics, professions, mounts, pvp, achievement_stats, quests, dungeons, raids, specs, reputations, titles = await asyncio.gather(
        *(bz.character_part(char.slug, p) for p in parts))
    state = dict(profile)
    summary = (char.state or {}).get("account_summary") or {}
    if summary.get("level") == profile["level"] and profile["experience"] is None:
        state["experience"] = summary.get("experience")
    if profile["last_login"] is None:
        state["last_login"] = summary.get("last_login")
    state["ilvl"] = parse_equipment(equipment) or profile["ilvl"]
    state["avatar_url"] = parse_media(media)
    state.update(parse_statistics(statistics))
    state.update({key: value for key, value in parse_statistics(achievement_stats).items() if value is not None})
    if state.get("honorable_kills") is None and pvp:
        state["honorable_kills"] = _int(pvp.get("honorable_kills"))
    state["pvp_rank"] = _int((pvp or {}).get("pvp_rank"))
    state["completed_quest_ids"] = completed_quest_ids(quests)
    state["quests_completed"] = len(state["completed_quest_ids"]) if state["completed_quest_ids"] is not None else None
    state["dungeon_encounters"] = encounter_counts(dungeons)
    state["raid_encounters"] = encounter_counts(raids)
    state["achievements"] = parse_achievements(achievements)
    state["mounts"] = parse_mounts(mounts)
    state["professions"] = parse_professions(professions)
    return state, "ok"


def apply_state(db: Session, char: Character, state: dict, status: str, message: str = "") -> list[Event]:
    state["builds"] = character_build(specs)
    state["reputations"] = character_reputations(reputations)
    state["titles"] = (sorted({_name(title) for title in titles["titles"] or [] if _name(title)}, key=str.casefold)
                       if titles is not None and "titles" in titles else None)
    state["recipes"] = profession_recipes(professions)
    state["equipped_items"] = equipped_items(equipment)
    old = dict(char.state or {})
    state["equipment_changes"] = old.get("equipment_changes")
    before, after = old.get("equipped_items"), state["equipped_items"]
    if before is not None and after is not None:
        changes = [{"before": before.get(slot), "after": after.get(slot)}
                   for slot in sorted(before.keys() | after.keys())
                   if before.get(slot) != after.get(slot)]
        if changes:
            state["equipment_changes"] = changes
    # On conserve les collections déjà connues si l'endpoint n'a pas répondu cette fois-ci.
    for key in ("achievements", "mounts", "professions", "deaths", "honorable_kills", "avatar_url",
                "pvp_rank", "quests_completed", "completed_quest_ids", "dungeon_encounters", "raid_encounters",
                "builds", "reputations", "titles", "recipes", "equipped_items"):
        if state.get(key) is None and old.get(key) is not None:
            state[key] = old[key]
    if state.get("last_login") is None and old.get("last_login") is not None:
        state["last_login"] = old["last_login"]
    if state.get("level") == old.get("level") and state.get("experience") is None:
        state["experience"] = old.get("experience")
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
        "level": char.level, "ilvl": char.ilvl,
        "experience": state.get("experience") if state.get("level") == char.level else None, "achievement_points": char.achievement_points,
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
        if summary:
            old = char.state or {}
            state = dict(old)
            if summary.get("level", 0) >= char.level:
                if summary["level"] > char.level:
                    state["experience"] = None
                state.update({key: value for key, value in summary.items() if value is not None and value != ""})
            if 0 < state.get("level", 0) < 10:
                message = ("Blizzard a renvoyé le résumé du compte. Le profil détaillé devient accessible "
                           "à partir du niveau 10, après une déconnexion du jeu.")
            else:
                message = ("Profil public Blizzard indisponible. Les informations détaillées peuvent manquer "
                           "ou dater du dernier relevé.")
            return apply_state(db, char, state, "ok", message)
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
                             add_missing: bool = False, *, reimport_removed: bool = False) -> int:
    added = 0
    removed = dict((kv_get(db, "removed_characters") or {}).get("items", {}))
    restored = False
    first_import = add_missing and db.scalar(select(Character.id).where(Character.account_id == account.id).limit(1)) is None
    for entry in entries:
        slug = account_entry_slug(entry)
        removal_key = f"{region}:{slug}"
        if not slug or (removed.get(removal_key) and not (add_missing and reimport_removed)):
            continue
        char = db.scalars(select(Character).where(Character.region == region, Character.slug == slug)).first()
        summary = account_entry_summary(entry)
        if char is None:
            legacy_slug = character_slug(summary["name"], summary["surname"])
            if legacy_slug and legacy_slug != slug:
                char = db.scalar(select(Character).where(Character.region == region, Character.slug == legacy_slug,
                                                        Character.account_id == account.id))
                if char is not None:
                    char.slug = slug
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
        if reimport_removed and removed.pop(removal_key, None):
            restored = True
    if restored:
        kv_set(db, "removed_characters", {"items": removed})
    account.last_sync_at = utcnow()
    return added

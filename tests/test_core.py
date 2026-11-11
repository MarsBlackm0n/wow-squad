import os
from datetime import datetime, timedelta, timezone

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

from app.blizzard import character_slug, split_slug  # noqa: E402
from app.collector import (account_entry_slug, diff_events, parse_achievements, parse_equipment,  # noqa: E402
                           parse_profile, parse_professions, parse_statistics)
from app.db import Character  # noqa: E402
from app.insights import _evenings, group_ideas, squad_goal  # noqa: E402
from app.ui import class_key, frdate, line_chart, Series  # noqa: E402


def test_slug_and_split():
    assert character_slug("Thrall", "Fils de Durotan") == "thrall-fils-de-durotan"
    assert split_slug("Thrall Fils-de-Durotan") == ("Thrall", "Fils-de-Durotan")
    assert split_slug("Thrall-Fils-de-Durotan") == ("Thrall", "Fils-de-Durotan")
    assert split_slug("Thrall") == ("Thrall", "")


def test_account_entry_slug_from_href():
    entry = {"name": "Zul'vex", "character": {"href": "https://eu.api.blizzard.com/profile/wow/character/zul%27vex-chante-ombre?namespace=profile-forever-eu"}}
    assert account_entry_slug(entry) == "zul'vex-chante-ombre"


def test_parse_profile_forever_shape():
    p = parse_profile({"name": "Grokna", "surname": "Brise-Crâne", "level": 23,
                       "character_class": {"name": "Guerrier"}, "race": {"name": "Orc"},
                       "faction": {"type": "HORDE", "name": "Horde"}, "ruleset": {"name": "PvP", "id": 3},
                       "achievement_points": 120, "last_login_timestamp": 1731000000000})
    assert p["surname"] == "Brise-Crâne" and p["ruleset"] == "PvP" and p["class_name"] == "Guerrier"
    assert p["level"] == 23 and p["last_login"] == 1731000000000


def test_parse_parts():
    eq = {"equipped_items": [{"slot": {"type": "HEAD"}, "level": {"value": 20}},
                             {"slot": {"type": "SHIRT"}, "level": {"value": 1}},
                             {"slot": {"type": "CHEST"}, "level": {"value": 24}}]}
    assert parse_equipment(eq) == 22.0
    stats = {"categories": [{"sub_categories": [{"statistics": [{"id": 60, "name": "Morts totales", "quantity": 17}]}]}]}
    assert parse_statistics(stats)["deaths"] == 17
    ach = {"achievements": [{"id": 6, "achievement": {"name": "Niveau 10"}, "completed_timestamp": 1},
                            {"id": 7, "achievement": {"name": "Niveau 20"}}]}
    assert parse_achievements(ach) == {"6": "Niveau 10"}
    prof = {"primaries": [{"profession": {"name": "Minage"}, "tiers": [{"skill_points": 120}]}]}
    assert parse_professions(prof) == {"Minage": 120}


def test_diff_first_sync_only_joins():
    events = diff_events("Grokna", {}, {"level": 12})
    assert [e.kind for e in events] == ["joined"]


def test_diff_detects_progress():
    old = {"synced": True, "level": 18, "achievements": {}, "mounts": {}, "professions": {"Minage": 70},
           "deaths": 3, "guild": "", "ilvl": 10.0, "last_login": 1}
    new = {"level": 21, "achievements": {"1": "Niveau 20"}, "mounts": {}, "professions": {"Minage": 80},
           "deaths": 6, "guild": "Les Gros Loups", "ilvl": 12.5, "last_login": 2}
    kinds = [e.kind for e in diff_events("Grokna", old, new)]
    assert kinds == ["level_milestone", "achievement", "profession_rank", "ilvl", "deaths", "session", "guild"]


def test_diff_unknown_collections_do_not_flood():
    old = {"synced": True, "level": 20, "achievements": None}
    new = {"level": 20, "achievements": {str(i): "x" for i in range(40)}}
    assert diff_events("A", old, new) == []


def _char(i, level, cls="Guerrier", faction="Horde"):
    return Character(id=i, name=f"P{i}", surname="X", level=level, class_name=cls, faction=faction, status="ok")


def test_group_ideas_respect_faction_and_roles():
    chars = [_char(1, 20, "Guerrier"), _char(2, 22, "Prêtre"), _char(3, 21, "Mage", "Alliance")]
    ideas = group_ideas(chars)
    assert ideas and {m.id for m in ideas[0].members} == {1, 2}
    assert ideas[0].has_tank and ideas[0].has_heal


def test_evenings_cluster_logins():
    t = datetime(2026, 11, 10, 20, 0, tzinfo=timezone.utc)
    a, b, c = _char(1, 10), _char(2, 10), _char(3, 10)
    evenings = _evenings([(t, a), (t + timedelta(minutes=40), b), (t + timedelta(hours=5), c)])
    assert len(evenings) == 1 and {m.id for m in evenings[0].members} == {1, 2}


def test_squad_goal():
    goal = squad_goal([_char(1, 30), _char(2, 60)])
    assert goal.progress == 75.0 and goal.at_max == 1 and goal.gap == 30


def test_class_key_french_and_english():
    assert class_key("Chasseur de démons") == "demonhunter"
    assert class_key("Chasseresse") == "hunter"
    assert class_key("Prêtresse") == "priest"
    assert class_key("Warlock") == "warlock"


def test_frdate_today():
    from zoneinfo import ZoneInfo
    assert frdate(datetime.now(ZoneInfo("Europe/Paris")).date()) == "Aujourd'hui"


def test_line_chart_is_accessible_svg():
    t = datetime(2026, 11, 1, tzinfo=timezone.utc)
    svg = str(line_chart([Series("A", "#fff", [(t, 10), (t + timedelta(days=1), 14)])], "Titre", "Desc", "c"))
    assert 'role="img"' in svg and "<title id=\"c-title\">Titre</title>" in svg and "<desc" in svg

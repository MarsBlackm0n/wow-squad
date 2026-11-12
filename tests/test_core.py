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


def test_squad_goal_uses_target_and_pace():
    from datetime import date
    from app.insights import Pace
    chars = [_char(1, 30), _char(2, 40), _char(3, 45)]
    paces = {1: Pace(3.0, 21, date(2026, 11, 20), False, 7), 2: Pace(1.0, 7, date(2026, 11, 25), False, 7),
             3: Pace(0.0, 0, None, True, 7)}
    goal = squad_goal(chars, 45, None, paces)
    assert goal.target == 45 and goal.at_target == 1
    assert goal.progress == round((30 + 40 + 45) / (3 * 45) * 100, 1)
    assert goal.fastest.id == 1 and goal.projected == date(2026, 11, 25) and goal.stalled == 0
    assert squad_goal(chars, 45, None, {1: paces[1]}).projected is None
    assert squad_goal(chars, 45, None, {}).average_pace is None


def test_group_plans_pick_common_dungeon():
    from app.insights import group_plans
    chars = [_char(1, 20, "Guerrier"), _char(2, 22, "Prêtre"), _char(3, 45, "Mage")]
    plan = group_plans([{"id": "a", "name": "Mardi", "members": [1, 2, 3]}], chars)[0]
    assert plan.dungeon and {m.id for m in plan.fits} == {1, 2} and [m.id for m in plan.out] == [3]
    assert plan.has_tank and plan.has_heal and plan.spread == 25


def test_crop_box_matches_zoom_and_center():
    from app.photos import crop_box
    assert crop_box(1000, 500) == (250, 0, 750, 500)
    assert crop_box(1000, 500, zoom=2, cx=0, cy=0) == (0, 0, 250, 250)
    assert crop_box(1000, 500, zoom=2, cx=1, cy=1) == (750, 250, 1000, 500)


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


def test_photo_is_square_webp_without_metadata():
    import io
    from PIL import Image
    from app.photos import SIZE, process
    src = io.BytesIO()
    exif = Image.Exif()
    exif[0x8825] = {2: (48.0, 51.0, 24.0)}  # bloc GPS factice
    Image.new("RGB", (900, 500), "orange").save(src, "JPEG", exif=exif)
    out = Image.open(io.BytesIO(process(src.getvalue())))
    assert out.format == "WEBP" and out.size == (SIZE, SIZE)
    assert not out.getexif()


def test_photo_rejects_non_images_and_big_files():
    import pytest
    from app.photos import MAX_UPLOAD_BYTES, player_key, process
    with pytest.raises(ValueError):
        process(b"pas une image")
    with pytest.raises(ValueError):
        process(b"x" * (MAX_UPLOAD_BYTES + 1))
    assert player_key("  Max   Le Grand ") == "max le grand"

def test_account_import_links_existing_characters_and_keeps_ownership():
    from sqlalchemy import create_engine, select
    from sqlalchemy.orm import Session

    from app.collector import apply_account_characters
    from app.db import Account, Base, PlayerPhoto
    from app.photos import character_key

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        owner = Account(battle_id=1, battletag="Ami#123")
        other = Account(battle_id=2, battletag="Ami#456")
        main = Character(region="eu", slug="mars-blackmon", name="Mars", surname="Blackmon", player="Ami",
                         battletag="Ancien#123", state={"synced": True})
        db.add_all([owner, other, main, PlayerPhoto(key="ami", image=b"legacy-photo")])
        db.flush()
        entry = {"name": "Mars", "surname": "Blackmon"}
        reroll = {"name": "Nova", "surname": "Blackmon"}

        assert apply_account_characters(db, "eu", owner, [entry, reroll], add_missing=True) == 1
        db.flush()
        assert main.account_id == owner.id
        assert main.state["synced"] is True
        assert db.get(PlayerPhoto, character_key(main)).image == b"legacy-photo"
        assert len(db.scalars(select(Character).where(Character.account_id == owner.id)).all()) == 2

        assert apply_account_characters(db, "eu", other, [entry], add_missing=True) == 0
        assert main.account_id == owner.id
        owner.battletag = "Nouveau#123"
        assert apply_account_characters(db, "eu", owner, [entry, reroll], add_missing=True) == 0
        assert main.battletag == "Nouveau#123"


def test_ruleset_label_and_account_summary():
    from app.collector import account_entry_summary
    from app.ui import ruleset_label

    assert ruleset_label("Normal") == "Normal"
    assert ruleset_label("JcJ") == "PvP"
    assert ruleset_label("Jeu de rôle") == "RP"
    assert ruleset_label("RP-PvP") == "RP + PvP"
    assert ruleset_label("JdR + JcJ") == "RP + PvP"
    assert ruleset_label("Hardcore") == "Hardcore"
    assert ruleset_label("") == "Non renseigné"
    assert account_entry_summary({"ruleset": {"type": "RP_PVP"}})["ruleset"] == "RP_PVP"
    assert parse_profile({"ruleset": {"name": {"fr_FR": "Normal"}}})["ruleset"] == "Normal"


def test_pace_estimates_until_one_day_of_snapshots(monkeypatch):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    from app.db import Base, Snapshot
    from app import insights
    from app.insights import paces
    from app.ui import FOREVER_LAUNCH

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        char = _char(1, 21)
        db.add(char)
        now = FOREVER_LAUNCH + timedelta(days=7)
        monkeypatch.setattr(insights, "utcnow", lambda: now)
        db.add(Snapshot(character_id=char.id, taken_at=now - timedelta(days=8), level=10))
        db.add(Snapshot(character_id=char.id, taken_at=now - timedelta(days=2), level=20))
        assert paces(db, [char], 60, "UTC")[char.id].estimated

        db.add(Snapshot(character_id=char.id, taken_at=now - timedelta(hours=36), level=20))
        assert paces(db, [char], 60, "UTC")[char.id].estimated

        db.add(Snapshot(character_id=char.id, taken_at=now - timedelta(hours=24), level=21))
        pace = paces(db, [char], 60, "UTC")[char.id]
        assert pace.gained == 1 and pace.per_day == 1 and pace.observed_days == 1 and not pace.estimated


def test_activity_uses_comparable_blizzard_counters():
    from app.collector import completed_quests, encounter_counts

    assert parse_profile({"level": 21, "experience": 0})["experience"] == 0
    assert completed_quests(None) is None
    assert completed_quests({"quests": [{"id": 1}, {"id": 1}, {"id": 2}]}) == 2
    assert encounter_counts(None) is None
    assert encounter_counts({"character": {}}) == {}
    bosses = encounter_counts({"expansions": [{"instances": [{
        "instance": {"id": 1},
        "modes": [{"difficulty": {"type": "NORMAL"}, "progress": {"encounters": [
            {"encounter": {"id": 2, "name": "Boss"}, "completed_count": 2},
        ]}}],
    }]}]})
    assert bosses == {"1:NORMAL:2": {"count": 2, "name": "Boss"}}
    current = {"quests_completed": 32, "honorable_kills": 23, "dungeon_encounters": bosses}
    assert diff_events("Gates", {"synced": True}, current) == []
    events = diff_events("Gates", {"synced": True, "quests_completed": 30, "honorable_kills": 20,
                                  "dungeon_encounters": {}}, current)
    assert [(event.kind, event.data["count"]) for event in events] == [
        ("quests", 2), ("pvp", 3), ("encounter", 2),
    ]


def test_mission_roster_counts_roles_and_flags_preparation():
    from app.db import MissionSignup, SquadMission
    from app.insights import Pace
    from app.squad import MissionRoster, mission_level_dates

    low = _char(1, 20, faction="Horde")
    low.ruleset = "JcJ"
    high = _char(2, 30, faction="Alliance")
    high.ruleset = "Normal"
    roster = MissionRoster(SquadMission(kind="dungeon", target_size=5, min_level=25), [
        MissionSignup(character=low, role="damage"), MissionSignup(character=high, role="tank"),
    ])
    assert roster.open_slots == 3
    assert roster.missing_levels[0].character is low
    assert roster.roles["tank"] == 1
    assert roster.warnings == ["Alliance et Horde inscrites", "Plusieurs modes de jeu", "Soigneur à désigner"]
    today = datetime(2026, 11, 12).date()
    assert mission_level_dates(roster, {low.id: Pace(2.0, 4, None, False, 2)}, today) == {
        low.id: today + timedelta(days=3),
    }


def test_forever_xp_progress_requires_a_valid_reading():
    from app.ui import xp_progress

    xp = xp_progress(21, 3656)
    assert xp["required"] == 25200 and xp["remaining"] == 21544
    assert round(xp["percent"], 1) == 14.5
    assert xp_progress(4, 0)["percent"] == 0
    assert xp_progress(4, None) is None
    assert xp_progress(4, 2101) is None
    assert xp_progress(60, 0) is None


def test_account_entry_prefers_public_profile_href():
    from app.collector import account_entry_summary

    entry = {"name": "Johnny", "surname": "Alias", "level": 4, "experience": 0,
             "character": {"href": "https://eu.api.blizzard.com/profile/wow/character/johnny-surname/?namespace=profile-forever-eu"}}
    assert account_entry_slug(entry) == "johnny-surname"
    assert account_entry_slug({"name": "Johnny"}) == ""
    assert account_entry_summary(entry)["experience"] == 0
    assert account_entry_summary(entry)["last_login"] is None


def test_level_and_equipment_curves_share_launch_anchor():
    from types import SimpleNamespace
    from app.ui import FOREVER_LAUNCH, progression_points

    first = FOREVER_LAUNCH + timedelta(days=1)
    second = first + timedelta(hours=2)
    snapshots = [SimpleNamespace(taken_at=first, level=4, ilvl=0),
                 SimpleNamespace(taken_at=second, level=5, ilvl=3.5)]
    assert progression_points(snapshots, "level") == ([(FOREVER_LAUNCH, 0), (first, 4), (second, 5)], True)
    assert progression_points(snapshots, "ilvl") == ([(FOREVER_LAUNCH, 0), (second, 3.5)], True)


def test_evening_suggestions_respect_roles_modes_and_unique_accounts():
    from types import SimpleNamespace

    from app.expedition import evening_suggestions

    def player(cid, account_id, cls, mode="PvP"):
        char = _char(cid, 24, cls)
        char.account_id, char.ruleset, char.region = account_id, mode, "eu"
        return char

    chars = [player(1, 1, "Guerrier"), player(2, 1, "Prêtre"), player(3, 2, "Prêtre"),
             player(4, 3, "Mage"), player(5, 4, "Chasseur"), player(6, 5, "Voleur", "Normal")]
    rows = [SimpleNamespace(account_id=i, status="yes", role="flex", character_ids=ids)
            for i, ids in [(1, [1, 2]), (2, [3]), (3, [4]), (4, [5]), (5, [6])]]
    ideas = evening_suggestions(rows, chars, "group")
    assert ideas and ideas[0]["score"][:3] == (4, 4, 2)
    chosen = [slot.character for slot in ideas[0]["team"] if slot]
    assert len({c.account_id for c in chosen}) == 4
    assert all(c.ruleset == "PvP" for c in chosen)


def test_map_has_verified_coordinates_for_each_filter():
    from app.worldmap import KINDS, points

    items = points()
    assert len({point["key"] for point in items}) == len(items)
    assert set(KINDS) <= {point["kind"] for point in items if point["xy"]}
    assert all(0 <= coordinate <= 100 for point in items for coordinate in (point["xy"] or []))
    assert next(p for p in items if p["key"] == "deadmines")["xy"] == [38.2, 77.5]

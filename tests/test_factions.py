import asyncio
import os
from datetime import timedelta
from types import SimpleNamespace
from pathlib import Path

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from starlette.requests import Request
from starlette.routing import Mount, Router
from starlette.staticfiles import StaticFiles

from app import web
from app.db import Account, Base, Character, Event, Snapshot, utcnow


def request_for(query="", session=None):
    return Request({
        "type": "http", "method": "GET", "path": "/", "scheme": "http",
        "server": ("testserver", 80), "headers": [], "query_string": query.encode(),
        "session": session if session is not None else {},
        "app": SimpleNamespace(state=SimpleNamespace(worker=SimpleNamespace(last_cycle=None, bz=None))),
    })


@pytest.mark.parametrize("value,expected", [
    ("alliance", {"alliance"}), ("horde", {"horde"}),
    ("alliance,horde", {"alliance", "horde"}), ("none", set()),
])
def test_faction_preference_survives_navigation(value, expected):
    session = {}
    assert web.selected_factions(request_for("factions=" + value, session)) == expected
    assert web.selected_factions(request_for(session=session)) == expected
    assert web.selected_factions(request_for("factions=invalid", session)) == expected


def test_unknown_faction_is_only_in_the_unfiltered_view():
    chars = [SimpleNamespace(faction=value) for value in ("Alliance", "Horde", "")]
    assert web.faction_characters(chars, {"alliance", "horde"}) == chars
    assert web.faction_characters(chars, {"alliance"}) == chars[:1]
    assert web.faction_characters(chars, {"horde"}) == chars[1:2]
    assert web.faction_characters(chars, set()) == []


@pytest.mark.parametrize("choice,expected_ids,weekly_gain", [
    ("alliance", {1}, 2), ("horde", {2}, 8),
    ("alliance,horde", {1, 2}, 10), ("none", set(), 0),
])
def test_home_filters_charts_rankings_and_awards_together(monkeypatch, choice, expected_ids, weekly_gain):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    now = utcnow()
    with sessions() as db:
        for char_id, faction, level, gain in ((1, "Alliance", 22, 2), (2, "Horde", 38, 8)):
            db.add(Account(id=char_id, battle_id=char_id, battletag=f"Player{char_id}",
                           main_character_id=char_id))
            db.add(Character(id=char_id, region="eu", slug=f"hero-{char_id}",
                             name=f"Hero{char_id}", faction=faction, status="ok",
                             level=level, ilvl=level - 2, account_id=char_id, guild=f"Guild{char_id}", state={}))
            db.add(Snapshot(character_id=char_id, taken_at=now - timedelta(days=2),
                            level=level - gain, ilvl=level - gain))
            db.add(Snapshot(character_id=char_id, taken_at=now, level=level, ilvl=level - 2))
            db.add(Event(character_id=char_id, at=now, kind="level", text=f"Hero{char_id} levels",
                         data={"from": level - gain, "to": level}))
        db.commit()
    monkeypatch.setattr(web, "SessionLocal", sessions)
    original_render = web.render
    monkeypatch.setattr(web, "render", lambda request, template, **context: context)
    # Personal character access must survive excluding that character's faction.
    context = asyncio.run(web.home(request_for("factions=" + choice, {"account_id": 1})))
    assert {c.id for c in context["chars"]} == expected_ids
    assert {row["id"] for row in context["race_charts"]} == expected_ids
    assert {c.id for c in context["by_pace"]} == expected_ids
    assert {g.character.id for g in context["week"].gains} == expected_ids
    assert context["week"].total_levels == weekly_gain
    assert {a.winner.id for a in context["week"].awards} <= expected_ids
    assert {item.character.id for item in context["firsts"]} <= expected_ids
    assert [c.id for c in context["mission_chars"]] == [1]
    assert context["has_characters"]
    request = request_for("factions=" + choice)
    request.scope["router"] = Router(routes=[
        Mount("/static", app=StaticFiles(directory=Path(web.__file__).parent / "static"), name="static")
    ])
    response = original_render(request, "index.html", **context)
    assert response.status_code == 200
    assert b'data-faction-form' in response.body
    assert b'data-tabs' in response.body
    assert b'Bienvenue au QG' not in response.body
    guild_context = asyncio.run(web.guild_page(request_for("factions=" + choice), 1))
    assert guild_context["guild_visible"] == (1 in expected_ids)
    guild_response = original_render(request, "guild.html", **guild_context)
    assert guild_response.status_code == 200
    assert b'data-faction-form' in guild_response.body
    engine.dispose()

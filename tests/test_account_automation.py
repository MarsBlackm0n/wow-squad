import asyncio
import os
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

import httpx
import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from starlette.requests import Request

from app import db as storage, web, worker as worker_module
from app.blizzard import UserAuthorizationExpired
from app.db import Account, Base, Character
from app.worker import Worker


@pytest.fixture
def account_db(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    with sessions() as db:
        db.add(Account(id=1, battle_id=101, battletag="Player#1", main_character_id=1))
        db.add(Account(id=2, battle_id=202, battletag="Other#2"))
        for char_id, side, owner in ((1, "Alliance", 1), (2, "Horde", 1), (3, "Alliance", 1), (4, "Horde", 2)):
            db.add(Character(id=char_id, region="eu", slug=f"hero-{char_id}", name=f"Hero{char_id}",
                             faction=side, account_id=owner, level=20, status="ok", state={}))
        db.commit()
    monkeypatch.setattr(web, "SessionLocal", sessions)
    monkeypatch.setattr(worker_module, "SessionLocal", sessions)
    yield sessions
    engine.dispose()


def make_request(worker=None, session=None, path="/", query=""):
    return Request({"type": "http", "method": "GET", "path": path, "scheme": "http",
                    "server": ("test", 80), "headers": [], "query_string": query.encode(),
                    "session": session if session is not None else {"account_id": 1, "auth": True, "csrf": "test"},
                    "app": SimpleNamespace(state=SimpleNamespace(worker=worker))})


def test_two_mains_preserve_legacy_selection_and_can_both_be_cleared(account_db):
    with account_db() as db:
        account = db.get(Account, 1)
        assert account.main_ids == {1}
        web.assign_main(db, account, 2, "horde")
        db.commit()
        assert account.main_ids == {1, 2}
        web.assign_main(db, account, 3, "alliance")
        db.commit()
        assert account.main_ids == {2, 3}
        web.assign_main(db, account, 0, "alliance")
        assert account.main_ids == {2}
        web.assign_main(db, account, 0, "horde")
        db.commit()
    with account_db() as db:
        assert db.get(Account, 1).main_ids == set()


@pytest.mark.parametrize("character_id,side", [(4, "horde"), (2, "alliance"), (999, "horde")])
def test_main_selection_rejects_other_players_and_wrong_faction(account_db, character_id, side):
    with account_db() as db:
        account = db.get(Account, 1)
        with pytest.raises(HTTPException):
            web.assign_main(db, account, character_id, side)
        assert account.main_ids == {1}


def test_both_mains_are_featured_and_default_on_charts(account_db, monkeypatch):
    with account_db() as db:
        web.assign_main(db, db.get(Account, 1), 2, "horde")
        db.commit()
    monkeypatch.setattr(web, "render", lambda request, template, **context: context)
    request = make_request(SimpleNamespace(last_cycle=None))
    context = asyncio.run(web.home(request))
    assert context["main_character_ids"] == {1, 2}
    group = next(g for g in context["player_groups"] if g["account"].id == 1)
    assert {c.id for c in group["mains"]} == {1, 2}
    assert [c.id for c in group["characters"]] == [3]
    assert context["goal"].members == 2
    assert not context["show_main_prompt"]


def test_admin_and_player_use_the_same_per_faction_selection(account_db, monkeypatch):
    monkeypatch.setattr(web, "require_admin", lambda request: None)
    request = make_request()
    asyncio.run(web.admin_main_character(request, 1, personnage=2, faction="horde", csrf="test"))
    asyncio.run(web.set_main_character(request, personnage=3, faction="alliance", csrf="test", action="save"))
    with account_db() as db:
        assert db.get(Account, 1).main_ids == {2, 3}


def test_migration_keeps_the_old_main_and_is_repeatable(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE accounts (id INTEGER PRIMARY KEY, battle_id BIGINT NOT NULL, "
                                "main_character_id INTEGER, battletag VARCHAR(64) NOT NULL, "
                                "linked_at DATETIME NOT NULL, last_sync_at DATETIME)"))
        connection.execute(text("INSERT INTO accounts (id, battle_id, main_character_id, battletag, linked_at) "
                                "VALUES (1, 101, 7, 'Test', '2026-11-18 12:00:00')"))
    monkeypatch.setattr(storage, "engine", engine)
    storage.init_db()
    storage.init_db()
    with sessionmaker(bind=engine)() as db:
        assert db.get(Account, 1).main_ids == {7}
    engine.dispose()


def enabled_settings(monkeypatch):
    settings = replace(web.settings, blizzard_client_id="client", blizzard_client_secret="secret",
                       public_url="https://qg.example")
    monkeypatch.setattr(web, "settings", settings)
    return settings


def test_expired_account_authorization_renews_on_visit_without_redirect_loop(account_db, monkeypatch):
    enabled_settings(monkeypatch)
    worker = SimpleNamespace(bz=object(), user_token=lambda battle_id: None, trigger_account_sync=Mock())
    request = make_request(worker, query="factions=horde")
    response = web.automatic_account_sync(request)
    assert response.headers["location"] == "/auth/battlenet?automatique=1"
    assert request.session["oauth_auto_battle_id"] == 101
    assert request.session["oauth_auto_return"] == "/?factions=horde"
    assert web.automatic_account_sync(request) is None


def test_valid_authorization_queues_background_import(account_db, monkeypatch):
    enabled_settings(monkeypatch)
    worker = SimpleNamespace(bz=object(), user_token=lambda battle_id: "valid", trigger_account_sync=Mock())
    assert web.automatic_account_sync(make_request(worker)) is None
    worker.trigger_account_sync.assert_called_once_with(1)


def test_automatic_callback_cannot_switch_to_another_battlenet_account(account_db, monkeypatch):
    enabled_settings(monkeypatch)
    bz = SimpleNamespace(exchange_code=AsyncMock(return_value={"access_token": "valid"}),
                         userinfo=AsyncMock(return_value={"id": 202, "battletag": "Other#2"}),
                         account_characters=AsyncMock())
    request = make_request(SimpleNamespace(bz=bz))
    request.session.update(oauth_state="state", oauth_auto_return="/", oauth_auto_battle_id=101)
    response = asyncio.run(web.battlenet_callback(request, code="code", state="state"))
    assert response.headers["location"] == "/"
    assert request.session["account_id"] == 1
    bz.account_characters.assert_not_called()


def test_automatic_callback_preserves_factions_and_does_not_restore_deleted_characters(account_db, monkeypatch):
    enabled_settings(monkeypatch)
    apply = Mock(return_value=0)
    monkeypatch.setattr(web, "apply_account_characters", apply)
    bz = SimpleNamespace(region="eu", exchange_code=AsyncMock(return_value={"access_token": "valid", "expires_in": 86400}),
                         userinfo=AsyncMock(return_value={"id": 101, "battletag": "Player#1"}),
                         account_characters=AsyncMock(return_value=[]))
    worker = SimpleNamespace(bz=bz, remember_user_token=Mock(), trigger_cycle=Mock())
    request = make_request(worker)
    request.session.update(oauth_state="state", oauth_auto_return="/?factions=horde",
                           oauth_auto_battle_id=101, factions=["horde"])
    response = asyncio.run(web.battlenet_callback(request, code="code", state="state"))
    assert response.headers["location"] == "/?factions=horde"
    assert request.session["factions"] == ["horde"]
    assert apply.call_args.kwargs["reimport_removed"] is False
    worker.remember_user_token.assert_called_once()


def test_user_token_is_invalidated_when_blizzard_revokes_it(account_db, monkeypatch):
    settings = enabled_settings(monkeypatch)
    async def run():
        async with httpx.AsyncClient(trust_env=False) as http:
            worker = Worker(settings, http)
            worker.remember_user_token(101, "revoked", 3600)
            worker.bz = SimpleNamespace(account_characters=AsyncMock(side_effect=UserAuthorizationExpired("expired")))
            await worker._sync_one_account(1)
            assert worker.user_token(101) is None
    asyncio.run(run())


def test_account_sync_detects_new_character_and_visits_are_coalesced(account_db, monkeypatch):
    settings = enabled_settings(monkeypatch)
    async def run():
        async with httpx.AsyncClient(trust_env=False) as http:
            worker = Worker(settings, http)
            worker.remember_user_token(101, "valid", 3600)
            entries = [{"name": "New Hero", "level": 1,
                        "character": {"href": "https://eu.api.blizzard.com/profile/wow/character/new-hero"}}]
            bz = SimpleNamespace(region="eu", account_characters=AsyncMock(return_value=entries))
            worker.bz = bz
            monkeypatch.setattr(worker_module, "refresh_character", AsyncMock())
            worker.trigger_account_sync(1)
            worker.trigger_account_sync(1)
            await asyncio.gather(*worker._tasks)
            bz.account_characters.assert_awaited_once()
            with account_db() as db:
                char = db.scalar(storage.select(Character).where(Character.slug == "new-hero"))
                assert char is not None and char.account_id == 1
    asyncio.run(run())

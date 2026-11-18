"""Tâche de fond : rafraîchit les persos, pousse les nouveautés et envoie le récap hebdo."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import httpx

from .blizzard import Blizzard, UserAuthorizationExpired
from .collector import LIVE_KINDS, refresh_character, sync_account
from .config import Settings
from .db import Account, Character, Event, SessionLocal, kv_get, kv_set, select, utcnow
from .guilds import refresh_rosters
from .notify import Notifier, esc
from .stats import event_message, recap_message

log = logging.getLogger(__name__)
GROUPED_THRESHOLD = 6  # au-delà, les nouveautés d'un passage partent en un seul message


class Worker:
    def __init__(self, settings: Settings, http: httpx.AsyncClient):
        self.settings = settings
        self.http = http
        self.notifier = Notifier(settings, http)
        self.bz = (Blizzard(settings.blizzard_client_id, settings.blizzard_client_secret, settings.region,
                            settings.locale, http) if settings.blizzard_enabled else None)
        self.last_cycle: datetime | None = None
        self._lock = asyncio.Lock()
        self._tasks: set[asyncio.Task] = set()
        # Jetons OAuth des comptes liés (lecture seule, 24 h) : jamais écrits en base, perdus au redémarrage.
        self._user_tokens: dict[int, tuple[str, datetime]] = {}
        self._account_checks: dict[int, datetime] = {}
        self._cycle_task: asyncio.Task | None = None

    def remember_user_token(self, battle_id: int, token: str, expires_in: int) -> None:
        self._user_tokens[battle_id] = (token, utcnow() + timedelta(seconds=expires_in))

    def user_token(self, battle_id: int) -> str | None:
        token, expires = self._user_tokens.get(battle_id, (None, None))
        if token and expires > utcnow():
            return token
        self._user_tokens.pop(battle_id, None)
        return None

    async def _sync_account(self, db, account: Account) -> int:
        token = self.user_token(account.battle_id)
        if not token:
            return 0
        try:
            added = await sync_account(db, self.bz, account, token, add_missing=True)
            db.commit()
            return added
        except UserAuthorizationExpired:
            db.rollback()
            self._user_tokens.pop(account.battle_id, None)
            log.info("Autorisation du compte %s à renouveler.", account.battletag)
        except Exception as exc:
            db.rollback()
            log.info("Synchro du compte %s impossible : %s", account.battletag, exc)
        return 0

    def trigger_account_sync(self, account_id: int) -> None:
        now = utcnow()
        if self._account_checks.get(account_id, now) > now:
            return
        self._account_checks[account_id] = now + timedelta(minutes=5)
        task = asyncio.create_task(self._sync_one_account(account_id))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _sync_one_account(self, account_id: int) -> None:
        if not self.bz:
            return
        async with self._lock:
            with SessionLocal() as db:
                account = db.get(Account, account_id)
                if account is None:
                    return
                await self._sync_account(db, account)
                pending = db.scalars(select(Character).where(
                    Character.account_id == account_id, Character.status == "pending")).all()
                for char in pending:
                    await refresh_character(db, self.bz, char)
                    db.commit()

    async def run_forever(self) -> None:
        if not self.bz:
            log.warning("BLIZZARD_CLIENT_ID / BLIZZARD_CLIENT_SECRET absents : aucun rafraîchissement automatique.")
        while True:
            try:
                await self.cycle()
                await self.maybe_send_recap()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - la boucle ne doit jamais mourir
                log.exception("Cycle de rafraîchissement en échec")
            await asyncio.sleep(self.settings.refresh_interval)

    async def cycle(self) -> None:
        if not self.bz:
            return
        async with self._lock:
            with SessionLocal() as db:
                for account in db.scalars(select(Account)).all():
                    await self._sync_account(db, account)
                characters = db.scalars(select(Character)).all()
                for char in characters:
                    await refresh_character(db, self.bz, char)
                    db.commit()
                    await asyncio.sleep(0.3)  # on reste poli avec l'API (36 000 requêtes/h max)
                self.last_cycle = utcnow()
            await self.flush_events()
            await refresh_rosters(self.bz, characters)

    def trigger_cycle(self) -> None:
        """Lance un passage complet en arrière-plan (sans faire attendre la requête HTTP)."""
        if self._cycle_task is not None and not self._cycle_task.done():
            return
        task = self._cycle_task = asyncio.create_task(self.cycle())
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def refresh_one(self, character_id: int) -> None:
        if not self.bz:
            return
        async with self._lock:
            with SessionLocal() as db:
                char = db.get(Character, character_id)
                if char:
                    await refresh_character(db, self.bz, char)
                    db.commit()
        await self.flush_events()

    async def flush_events(self) -> None:
        with SessionLocal() as db:
            pending = db.scalars(select(Event).where(Event.notified.is_(False)).order_by(Event.at)).all()
            if not pending:
                return
            live = [e for e in pending if e.kind in LIVE_KINDS] if self.settings.live_events else []
            if len(live) > GROUPED_THRESHOLD:
                await self.notifier.send(f"✨ <b>{esc(self.settings.squad_name)}</b>\n"
                                         + "\n".join(event_message(e) for e in live[:25]))
            else:
                for e in live:
                    await self.notifier.send(event_message(e))
            for e in pending:
                e.notified = True
            db.commit()

    async def maybe_send_recap(self) -> None:
        s = self.settings
        if s.recap_weekday < 0:
            return
        local = datetime.now(ZoneInfo(s.timezone))
        if local.weekday() != s.recap_weekday or local.hour < s.recap_hour:
            return
        with SessionLocal() as db:
            today = local.date().isoformat()
            if (kv_get(db, "recap") or {}).get("last") == today:
                return
            await self.notifier.send(recap_message(db, s.squad_name, s.public_url))
            kv_set(db, "recap", {"last": today})
            db.commit()

    async def send_recap_now(self) -> None:
        with SessionLocal() as db:
            await self.notifier.send(recap_message(db, self.settings.squad_name, self.settings.public_url))

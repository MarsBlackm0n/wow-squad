"""Tâche de fond : rafraîchit les persos, pousse les nouveautés et envoie le récap hebdo."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import httpx

from .blizzard import Blizzard
from .collector import LIVE_KINDS, refresh_character, sync_account
from .config import Settings
from .db import Account, Character, Event, SessionLocal, kv_get, kv_set, select, utcnow
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

    def remember_user_token(self, battle_id: int, token: str, expires_in: int) -> None:
        self._user_tokens[battle_id] = (token, utcnow() + timedelta(seconds=expires_in))

    def user_token(self, battle_id: int) -> str | None:
        token, expires = self._user_tokens.get(battle_id, (None, None))
        if token and expires > utcnow():
            return token
        self._user_tokens.pop(battle_id, None)
        return None

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
                    token = self.user_token(account.battle_id)
                    if token:
                        try:
                            await sync_account(db, self.bz, account, token)
                            db.commit()
                        except Exception as exc:  # noqa: BLE001
                            db.rollback()
                            log.info("Synchro du compte %s impossible : %s", account.battletag, exc)
                for char in db.scalars(select(Character)).all():
                    await refresh_character(db, self.bz, char)
                    db.commit()
                    await asyncio.sleep(0.3)  # on reste poli avec l'API (36 000 requêtes/h max)
                self.last_cycle = utcnow()
            await self.flush_events()

    def trigger_cycle(self) -> None:
        """Lance un passage complet en arrière-plan (sans faire attendre la requête HTTP)."""
        task = asyncio.create_task(self.cycle())
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

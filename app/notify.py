"""Envoi des messages vers Telegram et/ou un webhook Discord. Sans configuration, on affiche en console."""

from __future__ import annotations

import html
import logging
import re

import httpx

from .config import Settings

log = logging.getLogger(__name__)


def esc(text: str) -> str:
    return html.escape(text or "", quote=False)


def _html_to_markdown(text: str) -> str:
    """Les messages sont écrits en HTML Telegram ; Discord veut du Markdown."""
    text = re.sub(r"</?b>", "**", text)
    text = re.sub(r"</?i>", "*", text)
    text = re.sub(r'<a href="([^"]+)">([^<]+)</a>', r"[\2](\1)", text)
    text = re.sub(r"<[^>]+>", "", text)
    return html.unescape(text)


class Notifier:
    def __init__(self, settings: Settings, http: httpx.AsyncClient):
        self.settings = settings
        self.http = http

    @property
    def enabled(self) -> bool:
        s = self.settings
        return bool((s.telegram_bot_token and s.telegram_chat_id) or s.discord_webhook_url)

    async def send(self, text_html: str) -> bool:
        """`text_html` : HTML Telegram (b, i, a) déjà échappé."""
        if not self.enabled:
            print("\n----- [NOTIFICATION] -----\n" + text_html + "\n--------------------------")
            return True
        ok = True
        s = self.settings
        if s.telegram_bot_token and s.telegram_chat_id:
            ok &= await self._telegram(text_html)
        if s.discord_webhook_url:
            ok &= await self._discord(_html_to_markdown(text_html))
        return ok

    async def _telegram(self, text: str) -> bool:
        try:
            resp = await self.http.post(
                f"https://api.telegram.org/bot{self.settings.telegram_bot_token}/sendMessage",
                json={"chat_id": self.settings.telegram_chat_id, "text": text[:4096], "parse_mode": "HTML",
                      "disable_web_page_preview": True})
            if resp.status_code != 200:
                log.error("Telegram %s : %s", resp.status_code, resp.text[:300])
            return resp.status_code == 200
        except httpx.HTTPError as exc:
            log.warning("Envoi Telegram échoué : %s", exc)
            return False

    async def _discord(self, text: str) -> bool:
        try:
            resp = await self.http.post(self.settings.discord_webhook_url,
                                        json={"content": text[:2000], "allowed_mentions": {"parse": []}})
            if resp.status_code >= 300:
                log.error("Discord %s : %s", resp.status_code, resp.text[:300])
            return resp.status_code < 300
        except httpx.HTTPError as exc:
            log.warning("Envoi Discord échoué : %s", exc)
            return False

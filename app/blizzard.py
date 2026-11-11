"""Client API Battle.net pour WoW: Forever.

Forever a ses propres namespaces (static-, dynamic-, profile-forever-{region}) et identifie un perso par
« nom-surnom » au lieu de « royaume/nom ». Un perso n'apparaît qu'à partir du niveau 10, après une déconnexion.
"""

from __future__ import annotations

import asyncio
import time
from urllib.parse import quote, urlencode

import httpx

OAUTH_HOST = "https://oauth.battle.net"
USER_AGENT = "wow-squad/1.0 (+https://github.com/MarsBlackm0n/wow-squad)"


class NotFound(Exception):
    """404 : perso inconnu, sous le niveau 10, pas encore déconnecté, ou endpoint pas encore ouvert."""


class BlizzardError(Exception):
    pass


def character_slug(name: str, surname: str = "") -> str:
    """« Thrall », « Fils-de-Durotan » -> « thrall-fils-de-durotan »."""
    parts = [p.strip().lower() for p in (name, surname) if p and p.strip()]
    return "-".join("-".join(p.split()) for p in parts)


def split_slug(text: str) -> tuple[str, str]:
    """« Thrall-Fils-de-Durotan » ou « Thrall Fils-de-Durotan » -> (« Thrall », « Fils-de-Durotan »)."""
    text = text.strip()
    for sep in (" ", "-"):
        if sep in text:
            name, surname = text.split(sep, 1)
            return name.strip(), surname.strip()
    return text, ""


def make_http() -> httpx.AsyncClient:
    return httpx.AsyncClient(headers={"User-Agent": USER_AGENT}, timeout=httpx.Timeout(20.0),
                             follow_redirects=True)


class Blizzard:
    def __init__(self, client_id: str, client_secret: str, region: str, locale: str, http: httpx.AsyncClient):
        self.client_id = client_id
        self.client_secret = client_secret
        self.region = region
        self.locale = locale
        self.http = http
        self._token: str | None = None
        self._token_exp = 0.0

    @property
    def base(self) -> str:
        return f"https://{self.region}.api.blizzard.com"

    def namespace(self, kind: str) -> str:
        return f"{kind}-forever-{self.region}"

    async def _app_token(self) -> str:
        if self._token and time.time() < self._token_exp - 300:
            return self._token
        resp = await self.http.post(f"{OAUTH_HOST}/token", data={"grant_type": "client_credentials"},
                                    auth=(self.client_id, self.client_secret))
        if resp.status_code != 200:
            raise BlizzardError(f"OAuth client_credentials : HTTP {resp.status_code}")
        data = resp.json()
        self._token = data["access_token"]
        self._token_exp = time.time() + data.get("expires_in", 86400)
        return self._token

    async def get(self, path: str, namespace: str = "profile", token: str | None = None) -> dict:
        url = path if path.startswith("http") else self.base + path
        parsed = httpx.URL(url)
        # Fusion manuelle : `params=` écraserait le namespace déjà présent dans les `href` de l'API.
        params = dict(parsed.params)
        params.setdefault("namespace", self.namespace(namespace))
        params.setdefault("locale", self.locale)
        bearer = token or await self._app_token()
        for attempt in range(3):
            resp = await self.http.get(parsed.copy_with(params=params), headers={"Authorization": f"Bearer {bearer}"})
            if resp.status_code == 429 and attempt < 2:
                await asyncio.sleep(1.5 * (attempt + 1))
                continue
            break
        if resp.status_code == 404:
            raise NotFound(path)
        if resp.status_code == 401 and token is None:
            self._token = None
        if resp.status_code != 200:
            raise BlizzardError(f"{path} : HTTP {resp.status_code}")
        return resp.json()

    # --- Persos (jeton applicatif) -------------------------------------------------------------

    def _char_path(self, slug: str, suffix: str = "") -> str:
        return f"/profile/wow/character/{quote(slug.lower())}{suffix}"

    async def character_profile(self, slug: str) -> dict:
        return await self.get(self._char_path(slug))

    async def character_part(self, slug: str, suffix: str) -> dict | None:
        """Sous-ressource optionnelle (/equipment, /statistics...) : None si elle n'est pas (encore) servie."""
        try:
            return await self.get(self._char_path(slug, suffix))
        except (NotFound, BlizzardError):
            return None

    # --- Compte (jeton utilisateur, scope wow.profile) -----------------------------------------

    async def account_characters(self, user_token: str) -> list[dict]:
        data = await self.get("/profile/user/wow", token=user_token)
        chars = []
        for account in data.get("wow_accounts") or []:
            chars.extend(account.get("characters") or [])
        return chars

    # --- OAuth Battle.net ----------------------------------------------------------------------

    def authorize_url(self, redirect_uri: str, state: str) -> str:
        query = urlencode({"client_id": self.client_id, "redirect_uri": redirect_uri, "response_type": "code",
                           "scope": "openid wow.profile", "state": state})
        return f"{OAUTH_HOST}/authorize?{query}"

    async def exchange_code(self, code: str, redirect_uri: str) -> dict:
        resp = await self.http.post(f"{OAUTH_HOST}/token", auth=(self.client_id, self.client_secret),
                                    data={"grant_type": "authorization_code", "code": code,
                                          "redirect_uri": redirect_uri, "scope": "openid wow.profile"})
        if resp.status_code != 200:
            raise BlizzardError(f"Échange du code OAuth : HTTP {resp.status_code} {resp.text[:200]}")
        return resp.json()

    async def userinfo(self, user_token: str) -> dict:
        resp = await self.http.get(f"{OAUTH_HOST}/userinfo", headers={"Authorization": f"Bearer {user_token}"})
        if resp.status_code != 200:
            raise BlizzardError(f"userinfo : HTTP {resp.status_code}")
        return resp.json()


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


class BlizzardCredentialsError(BlizzardError):
    """Battle.net a refusé l'authentification du client OAuth."""

    def __init__(self, grant_type: str):
        super().__init__(f"OAuth {grant_type} : HTTP 401. Vérifie que BLIZZARD_CLIENT_ID et "
                         "BLIZZARD_CLIENT_SECRET correspondent au même client API Battle.net.")


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
        self._token_lock = asyncio.Lock()

    @property
    def base(self) -> str:
        return f"https://{self.region}.api.blizzard.com"

    def namespace(self, kind: str) -> str:
        return f"{kind}-forever-{self.region}"

    async def _request_token(self, grant_type: str, **fields: str) -> dict:
        resp = await self.http.post(f"{OAUTH_HOST}/token", data={"grant_type": grant_type, **fields},
                                    auth=(self.client_id, self.client_secret), follow_redirects=False)
        if resp.status_code == 401:
            raise BlizzardCredentialsError(grant_type)
        if resp.status_code != 200:
            raise BlizzardError(f"OAuth {grant_type} : HTTP {resp.status_code}")
        return resp.json()

    async def _app_token(self, rejected_token: str | None = None) -> str:
        async with self._token_lock:
            if self._token and self._token != rejected_token and time.time() < self._token_exp - 300:
                return self._token
            self._token = None
            data = await self._request_token("client_credentials")
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
        bearer = token if token is not None else await self._app_token()
        url = parsed.copy_with(params=params)
        renewed = False
        rate_limit_retries = 0
        while True:
            resp = await self.http.get(url, headers={"Authorization": f"Bearer {bearer}"})
            if resp.status_code == 401 and token is None and not renewed:
                bearer = await self._app_token(rejected_token=bearer)
                renewed = True
                continue
            if resp.status_code == 429 and rate_limit_retries < 2:
                rate_limit_retries += 1
                await asyncio.sleep(1.5 * rate_limit_retries)
                continue
            break
        if resp.status_code == 404:
            raise NotFound(path)
        if resp.status_code != 200:
            raise BlizzardError(f"API Blizzard {parsed.path} : HTTP {resp.status_code}")
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
        except BlizzardCredentialsError:
            raise
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
        return await self._request_token("authorization_code", code=code, redirect_uri=redirect_uri)

    async def userinfo(self, user_token: str) -> dict:
        resp = await self.http.get(f"{OAUTH_HOST}/userinfo", headers={"Authorization": f"Bearer {user_token}"})
        if resp.status_code != 200:
            raise BlizzardError(f"userinfo : HTTP {resp.status_code}")
        return resp.json()


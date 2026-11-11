"""WoW Squad : le QG de la bande sur WoW: Forever (progression, fil d'activité, récap hebdo).

Lancement : `python -m app` (voir railway.json). Le worker tourne dans le même process que le site.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException
from starlette.middleware.sessions import SessionMiddleware

from .blizzard import make_http
from .config import settings
from .db import init_db
from .web import is_authenticated, render, router
from .worker import Worker

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
OPEN_PATHS = ("/connexion", "/static/", "/healthz")


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    async with make_http() as http:
        worker = Worker(settings, http)
        app.state.worker = worker
        task = asyncio.create_task(worker.run_forever())
        try:
            yield
        finally:
            task.cancel()


app = FastAPI(title="WoW Squad", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
app.mount("/static", StaticFiles(directory=str(Path(__file__).parent / "static")), name="static")
app.include_router(router)


@app.middleware("http")
async def guard(request: Request, call_next):
    path = request.url.path
    if not is_authenticated(request) and not path.startswith(OPEN_PATHS):
        return RedirectResponse(f"/connexion?suite={quote(path)}", status_code=303)
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "same-origin")
    response.headers.setdefault("X-Frame-Options", "DENY")
    return response


# Ajouté après `guard` pour l'envelopper : la session est donc disponible dans `guard`.
app.add_middleware(SessionMiddleware, secret_key=settings.secret_key, session_cookie="squad",
                   max_age=60 * 60 * 24 * 60, same_site="lax", https_only=settings.public_url.startswith("https"))


@app.exception_handler(HTTPException)
async def http_error(request: Request, exc: HTTPException):
    titles = {404: "Page introuvable", 400: "Requête invalide", 405: "Action impossible"}
    defaults = {404: "Cette page n'existe pas (ou plus).", 405: "Cette adresse ne s'utilise pas comme ça."}
    message = exc.detail if exc.detail not in ("Not Found", "Method Not Allowed") else defaults[exc.status_code]
    return render(request, "error.html", status_code=exc.status_code,
                  code=exc.status_code, title=titles.get(exc.status_code, "Oups"), message=message)

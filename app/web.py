"""Routes du site : QG de la squad, fiche perso, ajout/suppression, connexion Battle.net."""

from __future__ import annotations

import hmac
import logging
import secrets
from datetime import timedelta
from pathlib import Path

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates

from . import insights
from .blizzard import BlizzardError, character_slug, split_slug
from .collector import sync_account
from .config import settings
from .db import Account, Character, Event, SessionLocal, Snapshot, select, utcnow
from .stats import compute_week, leaderboard
from .ui import Series, class_color, class_key, faction_key, frdate, line_chart, make_formatters, number

log = logging.getLogger(__name__)
router = APIRouter()

templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
localtime, time_tag = make_formatters(settings.timezone)
templates.env.filters.update(localtime=localtime, time_tag=time_tag, number=number, class_color=class_color,
                             class_key=class_key, faction_key=faction_key,
                             frdate=lambda d: frdate(d, settings.timezone))
templates.env.globals.update(settings=settings, max_level=insights.MAX_LEVEL)


# --- Sessions, CSRF, messages -------------------------------------------------------------------

def csrf_token(request: Request) -> str:
    token = request.session.get("csrf")
    if not token:
        token = request.session["csrf"] = secrets.token_urlsafe(24)
    return token


def check_csrf(request: Request, token: str) -> None:
    if not hmac.compare_digest(token or "", request.session.get("csrf", "")):
        raise HTTPException(400, "Formulaire expiré, recharge la page.")


def flash(request: Request, message: str, kind: str = "success") -> None:
    request.session.setdefault("flash", []).append({"message": message, "kind": kind})


def render(request: Request, template: str, status_code: int = 200, **context) -> HTMLResponse:
    context.update(request=request, csrf=csrf_token(request), flashes=request.session.pop("flash", []),
                   logged_in=bool(request.session.get("auth")), battletag=request.session.get("battletag"))
    return templates.TemplateResponse(request, template, context, status_code=status_code)


def is_authenticated(request: Request) -> bool:
    return not settings.site_password or bool(request.session.get("auth"))


def back(path: str = "/") -> RedirectResponse:
    return RedirectResponse(path, status_code=303)


# --- Accès protégé par mot de passe partagé ------------------------------------------------------

@router.get("/connexion", response_class=HTMLResponse)
async def login_form(request: Request, suite: str = "/"):
    if is_authenticated(request):
        return back(suite if suite.startswith("/") else "/")
    return render(request, "login.html", suite=suite)


@router.post("/connexion")
async def login(request: Request, password: str = Form(""), csrf: str = Form(""), suite: str = Form("/")):
    check_csrf(request, csrf)
    if settings.site_password and hmac.compare_digest(password, settings.site_password):
        request.session["auth"] = True
        return back(suite if suite.startswith("/") and not suite.startswith("//") else "/")
    return render(request, "login.html", status_code=401, suite=suite,
                  error="Mot de passe incorrect. Demande-le à la personne qui a créé le QG.")


@router.post("/deconnexion")
async def logout(request: Request, csrf: str = Form("")):
    check_csrf(request, csrf)
    request.session.clear()
    return back("/connexion")


# --- Pages ---------------------------------------------------------------------------------------

@router.get("/", response_class=HTMLResponse)
async def home(request: Request):
    tz = settings.timezone
    with SessionLocal() as db:
        chars = leaderboard(db)
        week = compute_week(db)
        events = insights.load_events(db, days=30)
        race_chart, race_rows, race_series = _race_chart(db, chars)
        firsts = insights.firsts(db)
        accounts = db.scalars(select(Account).order_by(Account.battletag)).all()
    worker = request.app.state.worker
    return render(request, "index.html", chars=chars, week=week, race_chart=race_chart, race_rows=race_rows, race_series=race_series,
                  days=insights.chronicle(events, tz), ideas=insights.group_ideas(chars),
                  goal=insights.squad_goal(chars), firsts=firsts, records=insights.records(events, tz),
                  seen=insights.recently_seen(chars), headlines=insights.headlines(events), accounts=accounts,
                  last_cycle=worker.last_cycle,
                  blizzard_ready=settings.blizzard_enabled, add_form=request.session.pop("add_form", None) or {})


def _race_chart(db, chars: list[Character]):
    since = utcnow() - timedelta(days=30)
    series = []
    for c in chars:
        if c.status == "pending":
            continue
        snaps = db.scalars(select(Snapshot).where(Snapshot.character_id == c.id, Snapshot.taken_at >= since)
                           .order_by(Snapshot.taken_at)).all()
        points = [(s.taken_at, s.level) for s in snaps if s.level]
        if points:
            series.append(Series(c.name, class_color(c.class_name), points))
    if not series:
        return None, [], []
    leader = max(series, key=lambda s: s.points[-1][1])
    desc = (f"Évolution du niveau de {len(series)} personnages sur les 30 derniers jours. "
            f"En tête : {leader.label}, niveau {int(leader.points[-1][1])}.")
    ranked = sorted(series, key=lambda s: -s.points[-1][1])
    rows = [(s.label, int(s.points[0][1]), int(s.points[-1][1]), f"+{int(s.points[-1][1] - s.points[0][1])}")
            for s in ranked]
    chart = line_chart(series, "Course au niveau", desc, "race", y_label="Niveau", tz_name=settings.timezone)
    return chart, rows, ranked


@router.get("/perso/{char_id}", response_class=HTMLResponse)
async def character_page(request: Request, char_id: int):
    with SessionLocal() as db:
        char = db.get(Character, char_id)
        if not char:
            raise HTTPException(404, "Ce perso n'existe pas (ou plus).")
        snaps = db.scalars(select(Snapshot).where(Snapshot.character_id == char.id)
                           .order_by(Snapshot.taken_at)).all()
        events = db.scalars(select(Event).where(Event.character_id == char.id)
                            .order_by(Event.at.desc()).limit(60)).all()
    color = class_color(char.class_name)
    level_chart = ilvl_chart = None
    if len(snaps) > 1:
        level_chart = line_chart([Series(char.display_name, color, [(s.taken_at, s.level) for s in snaps])],
                                 f"Niveau de {char.display_name}",
                                 f"Du niveau {snaps[0].level} au niveau {snaps[-1].level} "
                                 f"entre le {localtime(snaps[0].taken_at, '%d/%m')} et le "
                                 f"{localtime(snaps[-1].taken_at, '%d/%m')}.",
                                 "level", y_label="Niveau", y_min=0, tz_name=settings.timezone)
        ilvl_points = [(s.taken_at, s.ilvl) for s in snaps if s.ilvl]
        if len(ilvl_points) > 1:
            ilvl_chart = line_chart([Series(char.display_name, color, ilvl_points)],
                                    f"Niveau d'objet de {char.display_name}",
                                    f"De {number(ilvl_points[0][1], 1)} à {number(ilvl_points[-1][1], 1)} "
                                    f"de niveau d'objet moyen.", "ilvl", y_label="ilvl", step=False,
                                    tz_name=settings.timezone)
    state = char.state or {}
    return render(request, "character.html", char=char, snaps=snaps, events=events, level_chart=level_chart,
                  ilvl_chart=ilvl_chart, professions=state.get("professions") or {},
                  mounts=state.get("mounts"), achievements=state.get("achievements"),
                  honorable_kills=state.get("honorable_kills"))


# --- Actions -------------------------------------------------------------------------------------

@router.post("/persos")
async def add_character(request: Request, prenom: str = Form(""), nom: str = Form(""), joueur: str = Form(""),
                        csrf: str = Form("")):
    check_csrf(request, csrf)
    name, surname = prenom.strip(), nom.strip()
    if name and not surname and " " in name:  # « Mars Blackmon » collé dans le premier champ
        name, surname = split_slug(name)
    errors = {}
    if not name:
        errors["prenom"] = "Indique le prénom du perso."
    if not surname:
        errors["nom"] = "Indique le nom du perso : sur Forever, chaque perso a un prénom et un nom."
    if errors:
        request.session["add_form"] = {"errors": errors, "prenom": prenom[:40], "nom": nom[:40], "joueur": joueur[:40]}
        return back("/#squad")
    slug = character_slug(name, surname)
    with SessionLocal() as db:
        existing = db.scalars(select(Character).where(Character.region == settings.region,
                                                      Character.slug == slug)).first()
        if existing:
            flash(request, f"{existing.display_name} fait déjà partie de la squad.", "info")
            return back(f"/perso/{existing.id}")
        char = Character(region=settings.region, slug=slug, name=name[:1].upper() + name[1:], surname=surname,
                         player=joueur.strip()[:40], state={})
        db.add(char)
        db.commit()
        char_id = char.id
    await request.app.state.worker.refresh_one(char_id)
    flash(request, f"{name} {surname} a rejoint la squad.")
    return back(f"/perso/{char_id}")


@router.post("/perso/{char_id}/rafraichir")
async def refresh_character_route(request: Request, char_id: int, csrf: str = Form("")):
    check_csrf(request, csrf)
    await request.app.state.worker.refresh_one(char_id)
    flash(request, "Données mises à jour.")
    return back(f"/perso/{char_id}")


@router.post("/perso/{char_id}/joueur")
async def set_player(request: Request, char_id: int, joueur: str = Form(""), csrf: str = Form("")):
    check_csrf(request, csrf)
    with SessionLocal() as db:
        char = db.get(Character, char_id)
        if char:
            char.player = joueur.strip()[:40]
            db.commit()
    flash(request, "Joueur mis à jour.")
    return back(f"/perso/{char_id}")


@router.post("/perso/{char_id}/supprimer")
async def delete_character(request: Request, char_id: int, csrf: str = Form("")):
    check_csrf(request, csrf)
    with SessionLocal() as db:
        char = db.get(Character, char_id)
        if char:
            name = char.display_name
            db.delete(char)
            db.commit()
            flash(request, f"{name} a quitté la squad (historique supprimé).")
    return back("/")


@router.post("/recap/envoyer")
async def send_recap(request: Request, csrf: str = Form("")):
    check_csrf(request, csrf)
    await request.app.state.worker.send_recap_now()
    flash(request, "Récap envoyé sur le canal configuré." if request.app.state.worker.notifier.enabled
           else "Aucun canal configuré : le récap a été affiché dans les logs du serveur.", "info")
    return back("/")


# --- Connexion Battle.net (ajoute automatiquement ses persos Forever) ----------------------------

@router.get("/auth/battlenet")
async def battlenet_login(request: Request):
    worker = request.app.state.worker
    if not settings.battlenet_login_enabled or not worker.bz:
        flash(request, "La connexion Battle.net n'est pas configurée (PUBLIC_URL et identifiants API).", "error")
        return back("/")
    state = secrets.token_urlsafe(16)
    request.session["oauth_state"] = state
    return RedirectResponse(worker.bz.authorize_url(settings.oauth_redirect_uri, state), status_code=302)


@router.get("/auth/callback")
async def battlenet_callback(request: Request, code: str = "", state: str = "", error: str = ""):
    worker = request.app.state.worker
    if error or not code or not hmac.compare_digest(state, request.session.pop("oauth_state", "")):
        flash(request, "Connexion Battle.net annulée ou expirée.", "error")
        return back("/")
    try:
        token = await worker.bz.exchange_code(code, settings.oauth_redirect_uri)
        info = await worker.bz.userinfo(token["access_token"])
    except BlizzardError as exc:
        log.warning("OAuth Battle.net : %s", exc)
        flash(request, "Battle.net a refusé la connexion. Réessaie dans un instant.", "error")
        return back("/")

    with SessionLocal() as db:
        account = db.scalars(select(Account).where(Account.battle_id == int(info["id"]))).first()
        if not account:
            account = Account(battle_id=int(info["id"]), battletag=info.get("battletag", "Inconnu"))
            db.add(account)
        account.battletag = info.get("battletag", account.battletag)
        worker.remember_user_token(account.battle_id, token["access_token"], int(token.get("expires_in", 86400)))
        try:
            added = await sync_account(db, worker.bz, account, token["access_token"], add_missing=True)
        except BlizzardError as exc:
            log.warning("Liste des persos du compte : %s", exc)
            added = -1
        db.commit()
    request.session["battletag"] = info.get("battletag")
    if added > 0:
        flash(request, f"Compte lié : {added} perso(s) Forever ajouté(s). Ils seront suivis en continu, sans reconnexion.")
        worker.trigger_cycle()
    elif added == 0:
        flash(request, "Compte lié. Aucun nouveau perso : ils sont déjà suivis, ou pas encore niveau 10.", "info")
    else:
        flash(request, "Compte lié, mais Battle.net ne renvoie pas encore tes persos Forever. Ajoute-les à la main en attendant.", "error")
    return back("/#squad")


@router.get("/healthz")
async def healthz() -> Response:
    return Response("ok", media_type="text/plain")

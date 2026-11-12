"""Routes du site : QG de la squad, fiche perso, ajout/suppression, connexion Battle.net."""

from __future__ import annotations

import hmac
import logging
import secrets
from datetime import date, timedelta
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates

from . import insights, photos, squad
from .blizzard import BlizzardCredentialsError, BlizzardError, character_slug, split_slug
from .collector import sync_account
from .config import settings
from .db import Account, Character, Event, PlayerPhoto, SessionLocal, Snapshot, select, utcnow
from .stats import compute_week, leaderboard
from .ui import Series, class_color, class_key, faction_key, frdate, line_chart, make_formatters, number

log = logging.getLogger(__name__)
router = APIRouter()

templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
localtime, time_tag = make_formatters(settings.timezone)
templates.env.filters.update(localtime=localtime, time_tag=time_tag, number=number, class_color=class_color,
                             class_key=class_key, faction_key=faction_key,
                             frdate=lambda d: frdate(d, settings.timezone))
templates.env.globals.update(settings=settings, max_level=insights.MAX_LEVEL, photo_url=photos.url_for)


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
        goal_cfg, groups = squad.get_goal(db), squad.get_groups(db)
        pace_by_char = insights.paces(db, chars, goal_cfg.target, tz)
    worker = request.app.state.worker
    by_pace = sorted((c for c in chars if c.id in pace_by_char), key=lambda c: -pace_by_char[c.id].per_day)
    return render(request, "index.html", chars=chars, week=week, race_chart=race_chart, race_rows=race_rows,
                  race_series=race_series, days=insights.chronicle(events, tz), ideas=insights.group_ideas(chars),
                  goal=insights.squad_goal(chars, goal_cfg.target, goal_cfg.deadline, pace_by_char),
                  goal_cfg=goal_cfg, groups=groups, plans=insights.group_plans(groups, chars),
                  paces=pace_by_char, by_pace=by_pace, firsts=firsts, records=insights.records(events, tz),
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
    with SessionLocal() as db:
        goal_cfg = squad.get_goal(db)
        pace = insights.paces(db, [char], goal_cfg.target, settings.timezone).get(char.id)
    return render(request, "character.html", char=char, pace=pace, goal_cfg=goal_cfg, snaps=snaps, events=events, level_chart=level_chart,
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

# --- Réglages de la squad : objectif et groupes ---------------------------------------------------

@router.post("/reglages/objectif")
async def set_goal(request: Request, niveau: str = Form(""), echeance: str = Form(""), csrf: str = Form("")):
    check_csrf(request, csrf)
    try:
        target = int(niveau)
        deadline = date.fromisoformat(echeance) if echeance else None
    except ValueError:
        flash(request, "Objectif invalide : indique un niveau entre 2 et 60 et une date valide.", "error")
        return back("/#squad")
    with SessionLocal() as db:
        squad.set_goal(db, target, deadline)
        db.commit()
    flash(request, f"Objectif enregistré : niveau {max(2, min(target, insights.MAX_LEVEL))}"
                   + (f" pour le {deadline.strftime('%d/%m/%Y')}." if deadline else "."))
    return back("/#squad")


@router.post("/groupes")
async def save_group(request: Request, csrf: str = Form(""), nom: str = Form(""), groupe: str = Form("")):
    check_csrf(request, csrf)
    form = await request.form()
    members = [int(v) for v in form.getlist("membres") if str(v).isdigit()]
    if not nom.strip() or len(members) < 2:
        flash(request, "Un groupe a besoin d'un nom et d'au moins deux persos.", "error")
        return back("/#squad")
    with SessionLocal() as db:
        squad.save_group(db, nom, members, groupe or None)
        db.commit()
    flash(request, f"Groupe « {nom.strip()[:40]} » enregistré. Il apparaît en tête de « Ce soir ».")
    return back("/#ce-soir")


@router.post("/groupes/{group_id}/supprimer")
async def delete_group(request: Request, group_id: str, csrf: str = Form("")):
    check_csrf(request, csrf)
    with SessionLocal() as db:
        squad.delete_group(db, group_id)
        db.commit()
    flash(request, "Groupe supprimé.")
    return back("/#squad")


# --- Photos de profil (par joueur, partagées par tous ses persos) --------------------------------

@router.post("/perso/{char_id}/photo")
async def upload_photo(request: Request, char_id: int, photo: UploadFile | None = File(None), csrf: str = Form(""),
                       zoom: float = Form(1.0), pos_x: float = Form(50), pos_y: float = Form(50)):
    check_csrf(request, csrf)
    with SessionLocal() as db:
        char = db.get(Character, char_id)
        key = photos.player_key(char.player if char else "")
        if not key:
            flash(request, "Indique d'abord le joueur derrière ce perso : la photo appartient au joueur.", "error")
            return back(f"/perso/{char_id}#photo")
        try:
            if photo is None or not photo.filename:
                raise ValueError("Choisis une image avant d'envoyer.")
            image = photos.process(await photo.read(photos.MAX_UPLOAD_BYTES + 1), zoom, pos_x / 100, pos_y / 100)
        except ValueError as exc:
            flash(request, str(exc), "error")
            return back(f"/perso/{char_id}#photo")
        row = db.get(PlayerPhoto, key)
        if row:
            row.image, row.updated_at = image, utcnow()
        else:
            db.add(PlayerPhoto(key=key, image=image))
        db.commit()
        photos.refresh(db)
        flash(request, f"Photo de {char.player} enregistrée. Elle s'affiche sur tous ses persos.")
    return back(f"/perso/{char_id}")


@router.post("/perso/{char_id}/photo/supprimer")
async def delete_photo(request: Request, char_id: int, csrf: str = Form("")):
    check_csrf(request, csrf)
    with SessionLocal() as db:
        char = db.get(Character, char_id)
        row = db.get(PlayerPhoto, photos.player_key(char.player if char else ""))
        if row:
            db.delete(row)
            db.commit()
            photos.refresh(db)
            flash(request, f"Photo de {char.player} retirée.")
    return back(f"/perso/{char_id}")


@router.get("/photo/{key}")
async def player_photo(key: str) -> Response:
    with SessionLocal() as db:
        row = db.get(PlayerPhoto, photos.player_key(key))
    if not row:
        raise HTTPException(404, "Pas de photo pour ce joueur.")
    # L'URL change à chaque nouvelle photo (?v=...) : on peut la mettre en cache longtemps.
    return Response(row.image, media_type="image/webp",
                    headers={"Cache-Control": "private, max-age=31536000, immutable"})


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
    expected_state = request.session.pop("oauth_state", "")
    if error or not code or not expected_state or not hmac.compare_digest(
        state.encode("utf-8"), expected_state.encode("utf-8")
    ) or not worker.bz:
        flash(request, "Connexion Battle.net annulée ou expirée.", "error")
        return back("/")
    try:
        token = await worker.bz.exchange_code(code, settings.oauth_redirect_uri)
        info = await worker.bz.userinfo(token["access_token"])
    except BlizzardError as exc:
        log.warning("OAuth Battle.net : %s", exc)
        message = (str(exc) if isinstance(exc, BlizzardCredentialsError)
                   else f"Battle.net a refusé la connexion ({exc}).")
        flash(request, message, "error")
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

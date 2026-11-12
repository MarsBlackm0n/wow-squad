"""Routes du site : QG de la squad, fiche perso, ajout/suppression, connexion Battle.net."""

from __future__ import annotations

import hmac
import logging
import os
import secrets
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from sqlalchemy import delete
from sqlalchemy.exc import IntegrityError

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates

from . import expedition, guilds, insights, photos, squad, worldmap
from .instances import DUNGEON_CATALOG, INSTANCE_CATALOG, RAID_CATALOG
from .blizzard import BlizzardCredentialsError, BlizzardError, character_slug, split_slug
from .collector import apply_account_characters
from .config import settings
from .db import Account, Character, EveningAvailability, Event, MissionMemory, MissionPreparation, MissionSignup, PlayerPhoto, SessionLocal, Snapshot, SquadMission, kv_get, kv_set, select, utcnow
from .stats import compute_week, leaderboard
from .ui import Series, class_color, class_key, faction_key, frdate, line_chart, progression_points, progression_replay, make_formatters, number, ruleset_label, squad_chart_color, sum_progression, xp_progress

log = logging.getLogger(__name__)
router = APIRouter()

templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
localtime, time_tag = make_formatters(settings.timezone)
templates.env.filters.update(localtime=localtime, time_tag=time_tag, number=number, class_color=class_color,
                             class_key=class_key, faction_key=faction_key, ruleset_label=ruleset_label,
                             frdate=lambda d: frdate(d, settings.timezone))
templates.env.globals.update(settings=settings, max_level=insights.MAX_LEVEL, photo_url=photos.url_for)


# --- Sessions, CSRF, messages -------------------------------------------------------------------

def csrf_token(request: Request) -> str:
    token = request.session.get("csrf")
    if not token:
        token = request.session["csrf"] = secrets.token_urlsafe(24)
    return token


def check_csrf(request: Request, token: str) -> None:
    expected = request.session.get("csrf")
    if not expected or not hmac.compare_digest(token.encode(), expected.encode()):
        raise HTTPException(400, "Formulaire expiré, recharge la page.")


def flash(request: Request, message: str, kind: str = "success") -> None:
    request.session.setdefault("flash", []).append({"message": message, "kind": kind})


def render(request: Request, template: str, status_code: int = 200, **context) -> HTMLResponse:
    context.update(request=request, csrf=csrf_token(request), flashes=request.session.pop("flash", []),
                   logged_in=bool(request.session.get("auth")), qg_access=is_authenticated(request),
                   account_connected=bool(request.session.get("account_id")), admin=is_admin(request))
    return templates.TemplateResponse(request, template, context, status_code=status_code)


def is_authenticated(request: Request) -> bool:
    return not settings.site_password or bool(request.session.get("auth"))


def is_admin(request: Request) -> bool:
    expected = os.environ.get("ADMIN_BATTLE_ID", "").strip()
    account_id = request.session.get("account_id")
    if not expected.isascii() or not expected.isdecimal() or account_id is None:
        return False
    with SessionLocal() as db:
        account = db.get(Account, account_id)
        return account is not None and str(account.battle_id) == expected


def require_admin(request: Request) -> None:
    if not is_admin(request):
        raise HTTPException(403, "Cette action est réservée à l’administration de la squad.")


def can_manage_character(request: Request, char: Character) -> bool:
    if char.account_id is not None:
        return char.account_id == request.session.get("account_id")
    return is_admin(request) and not char.battletag


def require_character_access(request: Request, char: Character | None) -> Character:
    if char is None:
        raise HTTPException(404, "Ce perso n'existe pas (ou plus).")
    if not can_manage_character(request, char):
        raise HTTPException(403, "Connecte le compte Battle.net associé pour modifier ce perso.")
    return char


def back(path: str = "/") -> RedirectResponse:
    return RedirectResponse(path, status_code=303)


# --- Accès protégé par mot de passe partagé ------------------------------------------------------

@router.get("/connexion", response_class=HTMLResponse)
async def login_form(request: Request, suite: str = "/"):
    if is_authenticated(request):
        return back(suite if suite.startswith("/") and not suite.startswith(("//", "/\\")) else "/")
    return render(request, "login.html", suite=suite)


@router.post("/connexion")
async def login(request: Request, password: str = Form(""), csrf: str = Form(""), suite: str = Form("/")):
    check_csrf(request, csrf)
    if settings.site_password and hmac.compare_digest(password, settings.site_password):
        request.session["auth"] = True
        return back(suite if suite.startswith("/") and not suite.startswith(("//", "/\\")) else "/")
    return render(request, "login.html", status_code=401, suite=suite,
                  error="Mot de passe incorrect. Demande-le à la personne qui a créé le QG.")


@router.post("/deconnexion")
async def logout(request: Request, csrf: str = Form("")):
    check_csrf(request, csrf)
    request.session.clear()
    return back("/connexion")


# --- Pages ---------------------------------------------------------------------------------------

@router.get("/", response_class=HTMLResponse)
async def home(request: Request, priorite: str = "group", suggestion: str = ""):
    tz = settings.timezone
    today = utcnow().astimezone(ZoneInfo(tz)).date()
    priority = priorite if priorite in expedition.PREFERENCES else "group"
    with SessionLocal() as db:
        chars = leaderboard(db)
        week = compute_week(db)
        events = insights.load_events(db, days=30)
        race_charts, race_rows, race_comparison, race_total, race_total_level, race_replay = _race_chart(db, chars)
        firsts = insights.firsts(db)
        accounts = db.scalars(select(Account).order_by(Account.battletag)).all()
        goal_cfg, groups = squad.get_goal(db), squad.get_groups(db)
        pace_by_char = insights.paces(db, chars, goal_cfg.target, tz)
        targets = squad.community_targets(db)
        missions, completed_missions = squad.mission_rosters(db, pace_by_char, utcnow().astimezone(ZoneInfo(tz)).date())
        account_id = request.session.get("account_id")
        mission_chars = [c for c in chars if account_id is not None and c.account_id == account_id]
        availability = db.scalars(select(EveningAvailability).where(EveningAvailability.day == today)).all()
        my_availability = next((row for row in availability if row.account_id == account_id), None)
        availability_names = {a.id: a.battletag.split("#")[0] for a in accounts}
        suggested_instance = INSTANCE_CATALOG.get(suggestion)
    worker = request.app.state.worker
    by_pace = sorted((c for c in chars if c.id in pace_by_char), key=lambda c: -pace_by_char[c.id].per_day)
    weekly_extra = sum(max(0, pace_by_char[gain.character.id].weekly_estimate - gain.levels)
                       for gain in week.gains if gain.character.id in pace_by_char
                       and pace_by_char[gain.character.id].weekly_estimate is not None)
    return render(request, "index.html", chars=chars, week=week, weekly_extra=weekly_extra, race_charts=race_charts, race_rows=race_rows,
                  race_comparison=race_comparison, race_total=race_total, race_total_level=race_total_level, race_replay=race_replay,
                  days=insights.chronicle(events, tz), ideas=insights.group_ideas(chars),
                  goal=insights.squad_goal(chars, goal_cfg.target, goal_cfg.deadline, pace_by_char),
                  goal_cfg=goal_cfg, groups=groups, plans=insights.group_plans(groups, chars),
                  paces=pace_by_char, by_pace=by_pace, firsts=firsts, records=insights.records(events, tz),
                  seen=insights.recently_seen(chars), headlines=insights.headlines(events), accounts=accounts,
                  targets=targets, missions=missions, completed_missions=completed_missions, mission_chars=mission_chars,
                  mission_kinds=squad.MISSION_KINDS, mission_roles=squad.MISSION_ROLES, account_id=account_id,
                  availability=availability, availability_names=availability_names, my_availability=my_availability,
                  availability_labels=expedition.AVAILABILITY, priority_labels=expedition.PREFERENCES,
                  suggestions=expedition.evening_suggestions(availability, chars, priority),
                  priority=priority, suggested_instance=suggested_instance,
                  dungeon_catalog=DUNGEON_CATALOG, raid_catalog=RAID_CATALOG, instance_catalog=INSTANCE_CATALOG,
                  activity=insights.activity_totals(events), last_cycle=worker.last_cycle,
                  blizzard_ready=settings.blizzard_enabled, add_form=request.session.pop("add_form", None) or {})


@router.get("/administration", response_class=HTMLResponse)
async def admin_page(request: Request):
    require_admin(request)
    with SessionLocal() as db:
        admin_missions = db.scalars(select(SquadMission)
                                   .order_by(SquadMission.completed_at.is_not(None), SquadMission.id.desc())).all()
        return render(request, "admin.html", chars=leaderboard(db), groups=squad.get_groups(db),
                      admin_missions=admin_missions, mission_kinds=squad.MISSION_KINDS,
                      goal_cfg=squad.get_goal(db), targets=squad.community_targets(db),
                      metrics=squad.COMMUNITY_METRICS, add_form=request.session.pop("add_form", None) or {})


@router.post("/administration/defis")
async def add_target(request: Request, titre: str = Form(""), indicateur: str = Form(""),
                     cible: int = Form(0), csrf: str = Form("")):
    require_admin(request)
    check_csrf(request, csrf)
    titre = titre.strip()
    if not 1 <= len(titre) <= 80 or indicateur not in squad.COMMUNITY_METRICS or not 1 <= cible <= 100000:
        raise HTTPException(400, "Vérifie le nom, l’indicateur et la cible du défi.")
    with SessionLocal() as db:
        if squad.add_community_target(db, titre, indicateur, cible):
            db.commit()
            flash(request, "Défi publié dans « Ce soir ».")
        else:
            flash(request, "Retire un défi avant d’en ajouter un autre.", "error")
    return back("/administration")


@router.post("/administration/defis/{target_id}/supprimer")
async def remove_target(request: Request, target_id: str, csrf: str = Form("")):
    require_admin(request)
    check_csrf(request, csrf)
    with SessionLocal() as db:
        squad.remove_community_target(db, target_id)
        db.commit()
    return back("/administration")


@router.get("/compte", response_class=HTMLResponse)
async def account_page(request: Request):
    with SessionLocal() as db:
        account_id = request.session.get("account_id")
        account = db.get(Account, account_id) if account_id is not None else None
        chars = db.scalars(select(Character).where(Character.account_id == account.id)
                           .order_by(Character.level.desc(), Character.name, Character.surname)).all() if account else []
    if not account:
        request.session.pop("account_id", None)
    return render(request, "account.html", account=account, chars=chars)


def _race_chart(db, chars: list[Character]):
    charts = []
    series = []
    histories = []
    for c in sorted(chars, key=lambda char: char.id):
        if c.status == "pending":
            continue
        snaps = db.scalars(select(Snapshot).where(Snapshot.character_id == c.id)
                           .order_by(Snapshot.taken_at)).all()
        points, launch_anchor = progression_points(snaps, "level")
        if not points:
            continue
        observed = points[1:] if launch_anchor else points
        first, last = observed[0][1], observed[-1][1]
        gain = f"{last - first:+d}" if len(observed) > 1 else "—"
        desc = f"Évolution du niveau de {c.display_name}. Dernier niveau relevé : {last}."
        if launch_anchor:
            desc += " Le zéro au lancement sert de repère."
        chart = line_chart([Series(c.display_name, class_color(c.class_name), points)],
                           f"Niveau de {c.display_name}", desc, f"race-{c.id}",
                           y_label="Niveau", y_min=0, step=False, tz_name=settings.timezone)
        comparison_color = squad_chart_color(len(series))
        series.append(Series(c.display_name, comparison_color, points))
        histories.append((c.display_name, comparison_color, snaps))
        charts.append({"color": comparison_color, "id": c.id, "name": c.display_name, "last": last, "first": first,
                       "gain": gain, "launch": launch_anchor, "svg": chart})
    charts.sort(key=lambda item: -item["last"])
    rows = [(item["name"], item["first"], item["last"], item["gain"]) for item in charts]
    if not series:
        return charts, rows, None, None, 0, []
    comparison = line_chart(series, "Comparer les personnages", "Une courbe de niveau par personnage.",
                            "race-comparison", y_label="Niveau", y_min=0, step=False, tz_name=settings.timezone)
    cumulative = sum_progression(series)
    total = line_chart([Series("Total de la squad", "#f8cf65", cumulative)], "Niveaux cumulés",
                       "Somme des niveaux des personnages suivis à chaque date.", "race-total",
                       y_label="Total des niveaux", y_min=0, step=False, tz_name=settings.timezone)
    return charts, rows, comparison, total, int(cumulative[-1][1]), progression_replay(histories, utcnow(), settings.timezone)



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
    level_points, level_has_launch = progression_points(snaps, "level")
    if len(level_points) > 1:
        level_chart = line_chart([Series(char.display_name, color, level_points)],
                                 f"Niveau de {char.display_name}",
                                 "Niveaux observés par Wow Squad." + (" Zéro au lancement comme repère." if level_has_launch else ""),
                                 "level", y_label="Niveau", y_min=0, step=False, tz_name=settings.timezone)
    ilvl_points, ilvl_has_launch = progression_points(snaps, "ilvl")
    if len(ilvl_points) > 1:
        ilvl_chart = line_chart([Series(char.display_name, color, ilvl_points)],
                                f"Niveau moyen de l’équipement de {char.display_name}",
                                "Niveaux moyens de l’équipement relevés. Le zéro au lancement sert de repère.",
                                "ilvl", y_label="Équipement", y_min=0, step=False, tz_name=settings.timezone)
    state = char.state or {}
    with SessionLocal() as db:
        goal_cfg = squad.get_goal(db)
        pace = insights.paces(db, [char], goal_cfg.target, settings.timezone).get(char.id)
    return render(request, "character.html", char=char, pace=pace, goal_cfg=goal_cfg, snaps=snaps, events=events, level_chart=level_chart,
                  ilvl_chart=ilvl_chart, professions=state.get("professions") or {},
                  mounts=state.get("mounts"), achievements=state.get("achievements"),
                  honorable_kills=state.get("honorable_kills"), can_manage=can_manage_character(request, char),
                  activity=insights.activity_totals(events), activity_state=state,
                  xp=xp_progress(char.level, state.get("experience")), level_has_launch=level_has_launch, ilvl_has_launch=ilvl_has_launch,
                  boss_totals={key: sum(row["count"] for row in state[key].values()) if state.get(key) is not None else None
                               for key in ("dungeon_encounters", "raid_encounters")})


@router.get("/perso/{char_id}/guilde", response_class=HTMLResponse)
async def guild_page(request: Request, char_id: int):
    with SessionLocal() as db:
        char = db.get(Character, char_id)
        if not char or not char.guild:
            raise HTTPException(404, "Aucune guilde connue pour ce personnage.")
        slug = (char.state or {}).get("guild_slug") or character_slug(char.guild)
        candidates = db.scalars(select(Character).where(Character.region == char.region, Character.guild == char.guild)).all()
        tracked = [member for member in candidates if ((member.state or {}).get("guild_slug") or character_slug(member.guild)) == slug]
    bz = request.app.state.worker.bz
    guild = await guilds.fetch_guild(bz, slug) if bz and slug and bz.region == char.region else guilds.guild_details(None, None, None, None)
    return render(request, "guild.html", char=char, guild=guild, tracked=tracked,
                  tracked_by_slug={member.slug: member for member in tracked})


# --- Actions -------------------------------------------------------------------------------------

@router.post("/persos")
async def add_character(request: Request, prenom: str = Form(""), nom: str = Form(""), joueur: str = Form(""),
                        csrf: str = Form("")):
    require_admin(request)
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
        return back("/administration")
    slug = character_slug(name, surname)
    with SessionLocal() as db:
        existing = db.scalars(select(Character).where(Character.region == settings.region,
                                                      Character.slug == slug)).first()
        if existing:
            flash(request, f"{existing.display_name} fait déjà partie de la squad.", "info")
            return back(f"/perso/{existing.id}")
        char = Character(region=settings.region, slug=slug, name=name[:1].upper() + name[1:], surname=surname,
                         player=joueur.strip()[:40], state={})
        removed = dict((kv_get(db, "removed_characters") or {}).get("items", {}))
        if removed.pop(f"{settings.region}:{slug}", None):
            kv_set(db, "removed_characters", {"items": removed})
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
        char = require_character_access(request, db.get(Character, char_id))
        char.player = joueur.strip()[:40]
        db.commit()
    flash(request, "Joueur mis à jour.")
    return back(f"/perso/{char_id}")


@router.post("/perso/{char_id}/supprimer")
async def delete_character(request: Request, char_id: int, csrf: str = Form("")):
    require_admin(request)
    check_csrf(request, csrf)
    with SessionLocal() as db:
        char = db.get(Character, char_id)
        if char is None:
            raise HTTPException(404, "Personnage introuvable.")
        name = char.display_name
        for signup in db.scalars(select(MissionSignup).where(MissionSignup.character_id == char.id)):
            db.delete(signup)
        removed = dict((kv_get(db, "removed_characters") or {}).get("items", {}))
        removed[f"{char.region}:{char.slug}"] = True
        kv_set(db, "removed_characters", {"items": removed})
        db.delete(char)
        db.commit()
        flash(request, f"{name} a quitté la squad (historique supprimé).")
    return back("/administration")

# --- Réglages de la squad : objectif et groupes ---------------------------------------------------

@router.post("/reglages/objectif")
async def set_goal(request: Request, niveau: str = Form(""), echeance: str = Form(""), csrf: str = Form("")):
    require_admin(request)
    check_csrf(request, csrf)
    try:
        target = int(niveau)
        deadline = date.fromisoformat(echeance) if echeance else None
    except ValueError:
        flash(request, "Objectif invalide : indique un niveau entre 2 et 60 et une date valide.", "error")
        return back("/administration")
    with SessionLocal() as db:
        squad.set_goal(db, target, deadline)
        db.commit()
    flash(request, f"Objectif enregistré : niveau {max(2, min(target, insights.MAX_LEVEL))}"
                   + (f" pour le {deadline.strftime('%d/%m/%Y')}." if deadline else "."))
    return back("/administration")


@router.post("/groupes")
async def save_group(request: Request, csrf: str = Form(""), nom: str = Form(""), groupe: str = Form("")):
    require_admin(request)
    check_csrf(request, csrf)
    form = await request.form()
    members = [int(v) for v in form.getlist("membres") if str(v).isdigit()]
    if not nom.strip() or len(members) < 2:
        flash(request, "Un groupe a besoin d'un nom et d'au moins deux persos.", "error")
        return back("/administration")
    with SessionLocal() as db:
        squad.save_group(db, nom, members, groupe or None)
        db.commit()
    flash(request, f"Groupe « {nom.strip()[:40]} » enregistré. Il apparaît en tête de « Ce soir ».")
    return back("/administration")


@router.post("/groupes/{group_id}/supprimer")
async def delete_group(request: Request, group_id: str, csrf: str = Form("")):
    require_admin(request)
    check_csrf(request, csrf)
    with SessionLocal() as db:
        squad.delete_group(db, group_id)
        db.commit()
    flash(request, "Groupe supprimé.")
    return back("/administration")


# --- Objectifs et inscriptions ------------------------------------------------------------------

def mission_account(db, request: Request) -> int:
    account_id = request.session.get("account_id")
    if account_id is None or db.get(Account, account_id) is None:
        raise HTTPException(403, "Connecte ton compte Battle.net pour participer aux objectifs.")
    return account_id


@router.post("/disponibilite")
async def save_availability(request: Request, statut: str = Form(""), role: str = Form("flex"),
                            persos: list[int] = Form([]), csrf: str = Form("")):
    check_csrf(request, csrf)
    today = utcnow().astimezone(ZoneInfo(settings.timezone)).date()
    with SessionLocal() as db:
        account_id = mission_account(db, request)
        if statut not in expedition.AVAILABILITY or role not in squad.MISSION_ROLES:
            raise HTTPException(400, "Disponibilité invalide.")
        ids = list(dict.fromkeys(persos)) if statut != "no" else []
        if statut != "no" and not ids:
            flash(request, "Choisis au moins un personnage pour ce soir.", "error")
            return back("/#ce-soir")
        if ids and set(db.scalars(select(Character.id).where(Character.id.in_(ids), Character.account_id == account_id))) != set(ids):
            raise HTTPException(403, "Choisis les personnages de ton compte.")
        row = db.get(EveningAvailability, (today, account_id))
        if row is None:
            row = EveningAvailability(day=today, account_id=account_id)
            db.add(row)
        row.status, row.role, row.character_ids = statut, role, ids
        db.commit()
    return back("/#ce-soir")


@router.get("/carte", response_class=HTMLResponse)
async def world_map(request: Request, zone: str = "", repere: str = ""):
    points = worldmap.points()
    zones = sorted({point["zone"] for point in points})
    selected = next((point for point in points if point["key"] == repere), None)
    active_zone = selected["zone"] if selected else zone if zone in zones else "Marche de l’Ouest"
    return render(request, "world_map.html", points=points, zones=zones, active_zone=active_zone,
                  selected=selected, kinds=worldmap.KINDS, backgrounds=worldmap.map_backgrounds())


@router.get("/objectifs/{mission_id}/preparation")
async def preparation_page(request: Request, mission_id: int):
    account_id = request.session.get("account_id")
    with SessionLocal() as db:
        mission = db.get(SquadMission, mission_id)
        if mission is None:
            raise HTTPException(404, "Sortie introuvable.")
        signups = expedition.mission_signups(db, mission_id)
        checks = expedition.preparation_checks(mission)
        saved = {(r.account_id, r.character_id): r.checks for r in db.scalars(select(MissionPreparation)
                 .where(MissionPreparation.mission_id == mission_id))}
        people = [{"signup": row, "checks": [key for key in saved.get((row.account_id, row.character_id), []) if key in dict(checks)],
                   "quest": expedition.quest_status(row.character, mission.instance_key)} for row in signups]
        own = next((person for person in people if person["signup"].account_id == account_id), None)
        return render(request, "preparation.html", mission=mission, people=people, own=own, checks=checks,
                      roles=squad.MISSION_ROLES, instance=INSTANCE_CATALOG.get(mission.instance_key), map_zone=worldmap.ZONES.get(mission.instance_key))


@router.post("/objectifs/{mission_id}/preparation")
async def save_preparation(request: Request, mission_id: int, checks: list[str] = Form([]), csrf: str = Form("")):
    check_csrf(request, csrf)
    with SessionLocal() as db:
        account_id = mission_account(db, request)
        mission = db.get(SquadMission, mission_id, with_for_update=True)
        signup = db.get(MissionSignup, (mission_id, account_id))
        if not mission or mission.completed_at or not signup:
            raise HTTPException(403, "Inscris-toi à une sortie ouverte pour confirmer ta préparation.")
        allowed = dict(expedition.preparation_checks(mission))
        if not set(checks) <= allowed.keys():
            raise HTTPException(400, "Étape de préparation invalide.")
        key = (mission_id, account_id, signup.character_id)
        row = db.get(MissionPreparation, key)
        if row is None:
            row = MissionPreparation(mission_id=mission_id, account_id=account_id, character_id=signup.character_id)
            db.add(row)
        row.checks = list(dict.fromkeys(checks))
        db.commit()
    return back(f"/objectifs/{mission_id}/preparation")


@router.get("/objectifs/{mission_id}/recit")
async def mission_story(request: Request, mission_id: int):
    account_id = request.session.get("account_id")
    with SessionLocal() as db:
        mission = db.get(SquadMission, mission_id)
        if not mission or not mission.completed_at:
            raise HTTPException(404, "Cette sortie n’est pas encore dans le journal.")
        signups = expedition.mission_signups(db, mission_id)
        can_contribute = account_id == mission.creator_id or any(row.account_id == account_id for row in signups)
        memories = db.execute(select(MissionMemory.id, MissionMemory.author, MissionMemory.author_id, MissionMemory.at,
                                     MissionMemory.note, MissionMemory.image.is_not(None).label("has_image"),
                                     Event.text.label("event_text"), Event.at.label("event_at"), MissionMemory.event_id)
                              .outerjoin(Event, Event.id == MissionMemory.event_id)
                              .where(MissionMemory.mission_id == mission_id)
                              .order_by(MissionMemory.at)).all()
        candidates = []
        if can_contribute:
            linked = set(db.scalars(select(MissionMemory.event_id).where(MissionMemory.event_id.is_not(None))))
            candidates = [e for e in expedition.story_candidates(db, mission, signups) if e.id not in linked]
        return render(request, "mission_story.html", mission=mission, signups=signups, memories=memories,
                      candidates=candidates, can_contribute=can_contribute, account_id=account_id)


@router.post("/objectifs/{mission_id}/recit")
async def add_memory(request: Request, mission_id: int, note: str = Form(""), event_id: int = Form(0),
                     capture: UploadFile | None = File(None), csrf: str = Form("")):
    check_csrf(request, csrf)
    account_id = request.session.get("account_id")
    note = note.strip()
    with SessionLocal() as db:
        mission = db.get(SquadMission, mission_id)
        if not mission or not mission.completed_at:
            raise HTTPException(404, "Sortie terminée introuvable.")
        if account_id is None or (account_id != mission.creator_id and db.get(MissionSignup, (mission_id, account_id)) is None):
            raise HTTPException(403, "Seuls l’organisateur et les inscrits peuvent compléter ce récit.")
    try:
        image = photos.process(await capture.read(photos.MAX_UPLOAD_BYTES + 1), screenshot=True) if capture and capture.filename else None
    except ValueError as exc:
        flash(request, str(exc), "error")
        return back(f"/objectifs/{mission_id}/recit")
    if len(note) > 500 or not (note or event_id or image):
        flash(request, "Ajoute une note, une capture ou un événement.", "error")
        return back(f"/objectifs/{mission_id}/recit")
    with SessionLocal() as db:
        account_id = mission_account(db, request)
        mission = db.get(SquadMission, mission_id, with_for_update=True)
        signups = expedition.mission_signups(db, mission_id)
        signup = next((row for row in signups if row.account_id == account_id), None)
        if not mission or not mission.completed_at or (account_id != mission.creator_id and signup is None):
            raise HTTPException(403, "Ce récit ne peut pas être modifié par ce compte.")
        if event_id and event_id not in {e.id for e in expedition.story_candidates(db, mission, signups)}:
            raise HTTPException(400, "Cet événement ne fait pas partie des relevés proposés pour la sortie.")
        if len(db.scalars(select(MissionMemory.id).where(MissionMemory.mission_id == mission_id).limit(50)).all()) >= 50:
            flash(request, "Ce récit contient déjà 50 souvenirs.", "error")
            return back(f"/objectifs/{mission_id}/recit")
        author = signup.character.display_name if signup else db.get(Account, account_id).battletag.split("#")[0]
        db.add(MissionMemory(mission_id=mission_id, author_id=account_id, author=author[:80],
                             note=note, image=image, event_id=event_id or None))
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            flash(request, "Cet événement est déjà rattaché à une sortie.", "error")
    return back(f"/objectifs/{mission_id}/recit")


@router.post("/objectifs/{mission_id}/recit/{memory_id}/retirer")
async def remove_memory(request: Request, mission_id: int, memory_id: int, csrf: str = Form("")):
    check_csrf(request, csrf)
    with SessionLocal() as db:
        account_id = mission_account(db, request)
        memory = db.get(MissionMemory, memory_id)
        mission = db.get(SquadMission, mission_id)
        if not memory or not mission or memory.mission_id != mission_id:
            raise HTTPException(404, "Souvenir introuvable.")
        if account_id not in (memory.author_id, mission.creator_id):
            raise HTTPException(403, "Ce souvenir appartient à une autre personne.")
        db.delete(memory)
        db.commit()
    return back(f"/objectifs/{mission_id}/recit")


@router.get("/objectifs/{mission_id}/recit/{memory_id}/capture")
async def memory_image(mission_id: int, memory_id: int):
    with SessionLocal() as db:
        memory = db.get(MissionMemory, memory_id)
        if not memory or memory.mission_id != mission_id or not memory.image:
            raise HTTPException(404, "Capture introuvable.")
        return Response(memory.image, media_type="image/webp", headers={"Cache-Control": "private, max-age=300"})


@router.post("/objectifs")
async def create_mission(request: Request, titre: str = Form(""), type_objectif: str = Form(""),
                         date_prevue: str = Form(""), effectif: int = Form(5), niveau: int = Form(1),
                         notes: str = Form(""), csrf: str = Form(""), objectif: int = Form(0),
                         instance_key: str = Form("")):
    check_csrf(request, csrf)
    try:
        starts_at = datetime.fromisoformat(date_prevue) if date_prevue else None
        if starts_at is not None:
            if starts_at.tzinfo is not None:
                raise ValueError
            starts_at = starts_at.replace(tzinfo=ZoneInfo(settings.timezone)).astimezone(utcnow().tzinfo)
    except (ValueError, OverflowError):
        flash(request, "Choisis une date et une heure valides.", "error")
        return back("/#ce-soir")
    instance = INSTANCE_CATALOG.get(instance_key)
    if instance_key and instance is None:
        raise HTTPException(400, "Cette instance n’est pas dans le catalogue.")
    if instance:
        titre = titre.strip() or instance.name
        type_objectif = instance.kind
    if (not titre.strip() or len(titre.strip()) > 80 or len(notes.strip()) > 320 or
            type_objectif not in squad.MISSION_KINDS or not 2 <= effectif <= 40 or not 1 <= niveau <= insights.MAX_LEVEL):
        flash(request, "Vérifie le titre, le type, le niveau et l’effectif de l’objectif.", "error")
        return back("/#ce-soir")
    with SessionLocal() as db:
        account_id = mission_account(db, request)
        if objectif:
            mission = db.get(SquadMission, objectif, with_for_update=True)
            if mission is None or mission.completed_at is not None:
                raise HTTPException(404, "Cet objectif n’est plus ouvert.")
            if mission.creator_id != account_id:
                raise HTTPException(403, "Seul le créateur peut modifier cet objectif.")
        else:
            mission = SquadMission(creator_id=account_id)
            db.add(mission)
        mission.instance_key = instance.key if instance else None
        mission.title, mission.kind, mission.notes = titre.strip(), type_objectif, notes.strip()
        mission.starts_at, mission.target_size, mission.min_level = starts_at, effectif, niveau
        db.commit()
    flash(request, "Objectif mis à jour." if objectif else "Objectif publié dans « Ce soir ».")
    return back("/#ce-soir")


@router.post("/objectifs/{mission_id}/participation")
async def mission_participation(request: Request, mission_id: int, personnage: int = Form(0),
                                role: str = Form("flex"), action: str = Form("inscrire"), csrf: str = Form("")):
    check_csrf(request, csrf)
    with SessionLocal() as db:
        account_id = mission_account(db, request)
        mission = db.get(SquadMission, mission_id, with_for_update=True)
        if mission is None or mission.completed_at is not None:
            raise HTTPException(404, "Cet objectif n’est plus ouvert.")
        signup = db.get(MissionSignup, (mission_id, account_id))
        if action == "retirer":
            if signup:
                db.delete(signup)
        elif action == "inscrire" and role in squad.MISSION_ROLES:
            char = db.get(Character, personnage)
            if char is None or char.account_id != account_id:
                raise HTTPException(403, "Choisis un personnage de ton compte.")
            if signup:
                signup.character_id, signup.role = char.id, role
            else:
                db.add(MissionSignup(mission_id=mission_id, account_id=account_id, character_id=char.id, role=role))
        else:
            raise HTTPException(400, "Participation invalide.")
        db.commit()
    flash(request, "Participation retirée." if action == "retirer" else "Participation enregistrée.")
    return back("/#ce-soir")


@router.post("/objectifs/{mission_id}/supprimer")
async def delete_mission(request: Request, mission_id: int, csrf: str = Form("")):
    require_admin(request)
    check_csrf(request, csrf)
    with SessionLocal() as db:
        mission = db.get(SquadMission, mission_id, with_for_update=True)
        if mission is None:
            raise HTTPException(404, "Objectif introuvable.")
        for model in (MissionMemory, MissionPreparation, MissionSignup):
            db.execute(delete(model).where(model.mission_id == mission_id))
        db.delete(mission)
        db.commit()
    flash(request, "Objectif supprimé.")
    return back("/administration#admin-objectifs")


@router.post("/objectifs/{mission_id}/terminer")
async def complete_mission(request: Request, mission_id: int, csrf: str = Form("")):
    check_csrf(request, csrf)
    with SessionLocal() as db:
        account_id = mission_account(db, request)
        mission = db.get(SquadMission, mission_id, with_for_update=True)
        if mission is None:
            raise HTTPException(404, "Objectif introuvable.")
        if mission.creator_id != account_id:
            raise HTTPException(403, "Seul le créateur peut terminer cet objectif.")
        if mission.completed_at is None:
            mission.completed_at = utcnow()
            db.commit()
    flash(request, "Objectif marqué comme terminé.")
    return back("/#ce-soir")


# --- Photos de profil (par joueur, partagées par tous ses persos) --------------------------------

@router.post("/perso/{char_id}/photo")
async def upload_photo(request: Request, char_id: int, photo: UploadFile | None = File(None), csrf: str = Form(""),
                       zoom: float = Form(1.0), pos_x: float = Form(50), pos_y: float = Form(50)):
    check_csrf(request, csrf)
    with SessionLocal() as db:
        char = require_character_access(request, db.get(Character, char_id))
        key = photos.character_key(char)
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
        flash(request, "Photo enregistrée sur les persos du même joueur.")
    return back(f"/perso/{char_id}")


@router.post("/perso/{char_id}/photo/supprimer")
async def delete_photo(request: Request, char_id: int, csrf: str = Form("")):
    check_csrf(request, csrf)
    with SessionLocal() as db:
        char = require_character_access(request, db.get(Character, char_id))
        row = db.get(PlayerPhoto, photos.character_key(char))
        if row:
            db.delete(row)
            db.commit()
            photos.refresh(db)
            flash(request, "Photo retirée.")
    return back(f"/perso/{char_id}")


@router.get("/photo/account/{account_id}")
async def account_photo(account_id: int) -> Response:
    return photo_response(f"account/{account_id}")


@router.get("/photo/{key}")
async def player_photo(key: str) -> Response:
    return photo_response(photos.player_key(key))


def photo_response(key: str) -> Response:
    with SessionLocal() as db:
        row = db.get(PlayerPhoto, key)
    if not row:
        raise HTTPException(404, "Pas de photo pour ce joueur.")
    # L'URL change à chaque nouvelle photo (?v=...) : on peut la mettre en cache longtemps.
    return Response(row.image, media_type="image/webp",
                    headers={"Cache-Control": "private, max-age=31536000, immutable"})


@router.post("/recap/envoyer")
async def send_recap(request: Request, csrf: str = Form("")):
    require_admin(request)
    check_csrf(request, csrf)
    await request.app.state.worker.send_recap_now()
    flash(request, "Récap envoyé sur le canal configuré." if request.app.state.worker.notifier.enabled
           else "Aucun canal configuré : le récap a été affiché dans les logs du serveur.", "info")
    return back("/administration")


# --- Connexion Battle.net (ajoute automatiquement ses persos Forever) ----------------------------

@router.get("/auth/battlenet")
async def battlenet_login(request: Request):
    worker = request.app.state.worker
    if not settings.battlenet_login_enabled or not worker.bz:
        flash(request, "La connexion Battle.net n'est pas configurée (PUBLIC_URL et identifiants API).", "error")
        return back("/compte")
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
        return back("/compte")
    try:
        token = await worker.bz.exchange_code(code, settings.oauth_redirect_uri)
        info = await worker.bz.userinfo(token["access_token"])
    except BlizzardError as exc:
        log.warning("OAuth Battle.net : %s", exc)
        message = (str(exc) if isinstance(exc, BlizzardCredentialsError)
                   else f"Battle.net a refusé la connexion ({exc}).")
        flash(request, message, "error")
        return back("/compte")

    try:
        entries = await worker.bz.account_characters(token["access_token"])
    except BlizzardError as exc:
        log.warning("Liste des persos du compte : %s", exc)
        entries = None

    with SessionLocal() as db:
        account = db.scalars(select(Account).where(Account.battle_id == int(info["id"]))).first()
        if not account:
            account = Account(battle_id=int(info["id"]), battletag=info.get("battletag", "Inconnu"))
            db.add(account)
        account.battletag = info.get("battletag", account.battletag)
        db.flush()
        added = apply_account_characters(db, worker.bz.region, account, entries, True) if entries is not None else -1
        db.commit()
        photos.refresh(db)
    worker.remember_user_token(account.battle_id, token["access_token"], int(token.get("expires_in", 86400)))
    request.session["account_id"] = account.id
    request.session.pop("battletag", None)
    if added >= 0:
        worker.trigger_cycle()
    if added > 0:
        flash(request, f"Compte lié : {added} perso(s) Forever ajouté(s). Ils seront suivis en continu, sans reconnexion.")
    elif added == 0:
        flash(request, "Compte lié. Aucun nouveau perso : ils sont déjà suivis, ou pas encore niveau 10.", "info")
    else:
        flash(request, "Compte lié, mais Battle.net ne renvoie pas encore tes persos Forever. Ajoute-les à la main en attendant.", "error")
    return back("/compte")


@router.get("/healthz")
async def healthz() -> Response:
    return Response("ok", media_type="text/plain")

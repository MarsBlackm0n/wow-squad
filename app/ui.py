"""Aides d'affichage : filtres Jinja, couleurs de classe et graphiques SVG rendus côté serveur.

Les graphiques sont du SVG statique (pas de JS) avec un titre, une description textuelle et un tableau de données
équivalent dans la page : lisibles au lecteur d'écran et sans dépendance externe.
"""

from __future__ import annotations

import re
from bisect import bisect_right
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from html import escape
from zoneinfo import ZoneInfo

from markupsafe import Markup

# Couleurs officielles des classes, utilisées en accent (liseré, point) et jamais comme seul porteur d'information.
CLASS_COLORS = {
    "warrior": "#C69B6D", "paladin": "#F48CBA", "hunter": "#AAD372", "rogue": "#FFF468", "priest": "#F0F0F0",
    "shaman": "#0070DD", "mage": "#3FC7EB", "warlock": "#8788EE", "druid": "#FF7C0A", "deathknight": "#C41E3A",
    "monk": "#00FF98", "demonhunter": "#A330C9", "evoker": "#33937F",
}
CLASS_ALIASES = {
    "guerri": "warrior", "paladin": "paladin", "chass": "hunter", "hunter": "hunter", "voleu": "rogue",
    "rogue": "rogue", "pretr": "priest", "priest": "priest", "chaman": "shaman", "shaman": "shaman", "mage": "mage",
    "demonist": "warlock", "warlock": "warlock", "druid": "druid", "chevalier": "deathknight", "death": "deathknight",
    "moine": "monk", "monk": "monk", "demon hunter": "demonhunter", "chasseur de demons": "demonhunter",
    "evocat": "evoker", "evoker": "evoker",
}


def _fold(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", (text or "").lower()) if unicodedata.category(c) != "Mn")


def ruleset_label(ruleset: str) -> str:
    if not ruleset:
        return "Non renseigné"
    key = re.sub(r"[^a-z0-9]", "", _fold(ruleset))
    labels = {
        "normal": "Normal", "pve": "Normal", "jce": "Normal",
        "pvp": "PvP", "jcj": "PvP", "playerversusplayer": "PvP", "joueurcontrejoueur": "PvP",
        "rp": "RP", "jdr": "RP", "roleplay": "RP", "roleplaying": "RP", "jeuderole": "RP",
        "rppvp": "RP + PvP", "pvprp": "RP + PvP", "jdrjcj": "RP + PvP", "jcjjdr": "RP + PvP",
        "roleplaypvp": "RP + PvP", "roleplayingpvp": "RP + PvP", "jeuderolejcj": "RP + PvP",
    }
    return labels.get(key, ruleset)


def class_key(class_name: str) -> str:
    folded = _fold(class_name)
    # Les alias les plus longs d'abord : « chasseur de démons » avant « chass ».
    for alias in sorted(CLASS_ALIASES, key=len, reverse=True):
        if folded.startswith(alias):
            return CLASS_ALIASES[alias]
    return ""


def class_color(class_name: str) -> str:
    return CLASS_COLORS.get(class_key(class_name), "#8B93A1")


def faction_key(faction: str) -> str:
    f = _fold(faction)
    return "horde" if "horde" in f else "alliance" if "alliance" in f else "neutral"


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def reltime(dt: datetime | None) -> str:
    if not dt:
        return "jamais"
    seconds = int((datetime.now(timezone.utc) - _aware(dt)).total_seconds())
    if seconds < 60:
        return "à l'instant"
    for unit_seconds, singular, plural in ((86400 * 30, "mois", "mois"), (86400, "jour", "jours"),
                                           (3600, "heure", "heures"), (60, "minute", "minutes")):
        if seconds >= unit_seconds:
            n = seconds // unit_seconds
            return f"il y a {n} {singular if n == 1 else plural}"
    return "à l'instant"


def make_formatters(tz_name: str):
    tz = ZoneInfo(tz_name)

    def localtime(dt: datetime | None, fmt: str = "%d/%m/%Y à %H:%M") -> str:
        return _aware(dt).astimezone(tz).strftime(fmt) if dt else ""

    def time_tag(dt: datetime | None) -> Markup:
        """<time> avec date ISO lisible par les machines et date complète au survol."""
        if not dt:
            return Markup("<span>jamais</span>")
        iso = _aware(dt).isoformat()
        return Markup(f'<time datetime="{iso}" title="{escape(localtime(dt))}">{escape(reltime(dt))}</time>')

    return localtime, time_tag


FR_DAYS = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
FR_MONTHS = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre", "octobre",
             "novembre", "décembre"]


def frdate(d, tz_name: str = "Europe/Paris") -> str:
    """« Aujourd'hui », « Hier » ou « mardi 10 novembre »."""
    today = datetime.now(ZoneInfo(tz_name)).date()
    if d == today:
        return "Aujourd'hui"
    if (today - d).days == 1:
        return "Hier"
    return f"{FR_DAYS[d.weekday()].capitalize()} {d.day} {FR_MONTHS[d.month - 1]}"


# Courbe WoW Forever (1–60), XP nécessaire du niveau d’indice n+1 au suivant.
# https://www.wowhead.com/forever/experience-calculator
XP_TO_NEXT = (
    400, 900, 1400, 2100, 2800, 3600, 4500, 5400, 6500, 7600,
    8800, 10100, 11400, 12900, 14400, 16000, 17700, 19400, 21300, 23200,
    25200, 27300, 29400, 31700, 34000, 36400, 38900, 41400, 44300, 47400,
    50800, 54500, 58600, 62800, 67100, 71600, 76100, 80800, 85700, 90700,
    95800, 101000, 106300, 111800, 117500, 123200, 129100, 135100, 141200, 147500,
    153900, 160400, 167100, 173900, 180800, 187900, 195000, 202300, 209800,
)


def xp_progress(level: int, experience: int | None) -> dict | None:
    if experience is None or not 1 <= level <= len(XP_TO_NEXT):
        return None
    required = XP_TO_NEXT[level - 1]
    if not 0 <= experience <= required:
        return None
    return {"current": experience, "required": required, "remaining": required - experience,
            "percent": 100 * experience / required}


# Lancement mondial : 5 novembre 2026, 00 h 00 en France (CET).
FOREVER_LAUNCH = datetime(2026, 11, 4, 23, tzinfo=timezone.utc)


def progression_points(snapshots, field: str) -> tuple[list[tuple[datetime, float]], bool]:
    points = [(_aware(s.taken_at), getattr(s, field)) for s in snapshots
              if getattr(s, field) and _aware(s.taken_at) >= FOREVER_LAUNCH]
    if not points:
        return points, False
    # Si le lancement coïncide avec le premier relevé, ne pas inventer de seconde mesure à la même heure.
    if points[0][0] <= FOREVER_LAUNCH:
        return points, False
    return [(FOREVER_LAUNCH, 0), *points], True


SQUAD_CHART_COLORS = ("#67c9ff", "#ff946c", "#a6dc71", "#d3a4ff", "#f8cf65",
                      "#f98eba", "#64dac8", "#a8aaff", "#f2ae72", "#dce988")


def squad_chart_color(index: int) -> str:
    if index < len(SQUAD_CHART_COLORS):
        return SQUAD_CHART_COLORS[index]
    return f"hsl({index * 137.508 % 360:.2f} 78% 72%)"


def progression_replay(histories, now: datetime, tz_name: str) -> list[dict]:
    """Compare les gains mesurés sur la même fenêtre glissante de sept jours."""
    prepared = [(name, color, [(_aware(s.taken_at), s.level, s.ilvl) for s in snaps if s.level and s.ilvl])
                for name, color, snaps in histories]
    tz = ZoneInfo(tz_name)
    frames = []
    for offset in range(13, -1, -1):
        end = now - timedelta(days=offset)
        start = end - timedelta(days=7)
        points = []
        for name, color, history in prepared:
            window = [(at, level, ilvl) for at, level, ilvl in history if start <= at <= end]
            if len(window) < 2 or window[-1][0] - window[0][0] < timedelta(days=1):
                continue
            first, last = window[0], window[-1]
            points.append({"name": name, "color": color, "level": last[1] - first[1],
                           "gear": round(last[2] - first[2], 1), "days": round((last[0] - first[0]).total_seconds() / 86400, 1)})
        if len(points) >= 2:
            frames.append({"date": end.astimezone(tz).strftime("%d/%m"), "points": points})
    return frames


def sum_progression(series) -> list[tuple[datetime, float]]:
    """Additionne les courbes linéaires sur une chronologie commune."""
    timelines = []
    for item in series:
        if item.points:
            points = dict(sorted(item.points, key=lambda point: point[0]))
            timelines.append((list(points), list(points.values())))
    dates = sorted({at for times, _ in timelines for at in times})
    total_points = []
    for at in dates:
        total = 0.0
        for times, values in timelines:
            index = bisect_right(times, at) - 1
            if index < 0:
                continue
            if index == len(times) - 1:
                total += values[index]
                continue
            fraction = (at - times[index]) / (times[index + 1] - times[index])
            total += values[index] + fraction * (values[index + 1] - values[index])
        total_points.append((at, total))
    return total_points


def number(value, digits: int = 0) -> str:
    if value is None:
        return "—"
    text = f"{value:,.{digits}f}".replace(",", " ")
    return text.replace(".", ",") if digits else text


# --- Graphiques ----------------------------------------------------------------------------------

@dataclass
class Series:
    label: str
    color: str
    points: list[tuple[datetime, float]]
    character_id: int | None = None


def line_chart(series: list[Series], title: str, desc: str, chart_id: str, y_label: str = "",
               y_min: float | None = None, step: bool = True, width: int = 640, height: int = 280,
               tz_name: str = "Europe/Paris") -> Markup:
    series = [s for s in series if s.points]
    if not series:
        return Markup("")
    pad_l, pad_r, pad_t, pad_b = 44, 14, 22, 30
    plot_w, plot_h = width - pad_l - pad_r, height - pad_t - pad_b
    xs = [_aware(p[0]).timestamp() for s in series for p in s.points]
    ys = [p[1] for s in series for p in s.points]
    x0, x1 = min(xs), max(xs)
    if x1 - x0 < 3600:
        x0 -= 1800
        x1 += 1800
    lo = y_min if y_min is not None else min(ys)
    hi = max(ys)
    if hi - lo < 1:
        hi = lo + 1
    ticks = _nice_ticks(lo, hi)
    lo, hi = min(lo, ticks[0]), max(hi, ticks[-1])

    def sx(ts: float) -> float:
        return pad_l + (ts - x0) / (x1 - x0) * plot_w

    def sy(v: float) -> float:
        return pad_t + plot_h - (v - lo) / (hi - lo) * plot_h

    parts = [f'<svg class="chart" viewBox="0 0 {width} {height}" role="img" '
             f'aria-labelledby="{chart_id}-title {chart_id}-desc" preserveAspectRatio="xMidYMid meet">',
             f'<title id="{chart_id}-title">{escape(title)}</title>',
             f'<desc id="{chart_id}-desc">{escape(desc)}</desc>', '<g class="chart-grid" aria-hidden="true">']
    for t in ticks:
        y = sy(t)
        parts.append(f'<line x1="{pad_l}" x2="{pad_l + plot_w}" y1="{y:.1f}" y2="{y:.1f}"/>'
                     f'<text x="{pad_l - 8}" y="{y + 4:.1f}" text-anchor="end">{number(t)}</text>')
    ticks_x = _time_ticks(x0, x1, ZoneInfo(tz_name))
    for i, (ts, label) in enumerate(ticks_x):
        anchor = "start" if i == 0 else "end" if i == len(ticks_x) - 1 else "middle"
        parts.append(f'<text x="{sx(ts):.1f}" y="{height - 8}" text-anchor="{anchor}">{label}</text>')
    if y_label:
        parts.append(f'<text x="{pad_l}" y="{pad_t - 4}" class="chart-axis-label">{escape(y_label)}</text>')
    parts.append("</g>")

    for s in series:
        pts = sorted(((_aware(t).timestamp(), v) for t, v in s.points))
        d, prev_y = [], None
        for i, (ts, v) in enumerate(pts):
            x, y = sx(ts), sy(v)
            if i == 0:
                d.append(f"M{x:.1f},{y:.1f}")
            else:
                d.append(f"H{x:.1f}V{y:.1f}" if step else f"L{x:.1f},{y:.1f}")
            prev_y = y
        end_x = sx(pts[-1][0])
        if s.character_id is not None:
            parts.append(f'<g data-race-series="{s.character_id}">')
        if len(series) == 1 and len(pts) > 1:
            fill_id = f"{chart_id}-area"
            start_x = sx(pts[0][0])
            floor = pad_t + plot_h
            parts.append(f'<defs><linearGradient id="{fill_id}" x1="0%" y1="0%" x2="0%" y2="100%">'
                         f'<stop stop-color="{s.color}" stop-opacity=".24"/>'
                         f'<stop offset="1" stop-color="{s.color}" stop-opacity=".015"/>'
                         '</linearGradient></defs>')
            parts.append(f'<path d="{" ".join(d)}V{floor:.1f}H{start_x:.1f}Z" '
                         f'fill="url(#{fill_id})" aria-hidden="true"/>')
        parts.append(f'<path class="chart-line" d="{" ".join(d)}" stroke="{s.color}" aria-hidden="true"/>')
        parts.append(f'<circle cx="{end_x:.1f}" cy="{prev_y:.1f}" r="4.5" fill="{s.color}" '
                     'stroke="var(--chart-bg)" stroke-width="1.5" aria-hidden="true"/>')

        if s.character_id is not None:
            parts.append("</g>")

    # Pas d'étiquettes dans le SVG : la légende est une liste HTML à côté (lisible, zoomable, traduisible).
    parts.append("</svg>")
    return Markup("".join(parts))


def _nice_ticks(lo: float, hi: float, count: int = 5) -> list[float]:
    span = hi - lo
    raw = span / max(count - 1, 1)
    magnitude = 10 ** len(str(int(raw))) / 10 if raw >= 1 else 1
    step = next(m * magnitude for m in (1, 2, 2.5, 5, 10) if m * magnitude >= raw)
    start = (lo // step) * step
    ticks, t = [], start
    while t <= hi + step * 0.001:
        ticks.append(round(t, 2))
        t += step
    if ticks[-1] < hi:
        ticks.append(round(t, 2))
    return ticks


def _time_ticks(x0: float, x1: float, tz: ZoneInfo, count: int = 5) -> list[tuple[float, str]]:
    span = x1 - x0
    fmt = "%H:%M" if span < 2 * 86400 else "%d/%m"
    return [(x0 + span * i / (count - 1),
             datetime.fromtimestamp(x0 + span * i / (count - 1), tz=tz).strftime(fmt))
            for i in range(count)]

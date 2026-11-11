"""Réglages de la squad modifiables depuis le site : objectif commun et groupes fixes (stockés dans la table kv)."""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import date

from sqlalchemy.orm import Session

from .db import kv_get, kv_set
from .insights import MAX_LEVEL

MAX_GROUPS = 12


@dataclass
class Goal:
    target: int = MAX_LEVEL
    deadline: date | None = None


def get_goal(db: Session) -> Goal:
    raw = kv_get(db, "goal") or {}
    deadline = raw.get("deadline")
    try:
        deadline = date.fromisoformat(deadline) if deadline else None
    except ValueError:
        deadline = None
    return Goal(int(raw.get("target") or MAX_LEVEL), deadline)


def set_goal(db: Session, target: int, deadline: date | None) -> None:
    kv_set(db, "goal", {"target": max(2, min(target, MAX_LEVEL)),
                        "deadline": deadline.isoformat() if deadline else None})


def get_groups(db: Session) -> list[dict]:
    return list((kv_get(db, "groups") or {}).get("items", []))


def save_group(db: Session, name: str, members: list[int], group_id: str | None = None) -> None:
    groups = [g for g in get_groups(db) if g.get("id") != group_id]
    groups.append({"id": group_id or secrets.token_hex(4), "name": name.strip()[:40], "members": members[:40]})
    kv_set(db, "groups", {"items": groups[-MAX_GROUPS:]})


def delete_group(db: Session, group_id: str) -> None:
    kv_set(db, "groups", {"items": [g for g in get_groups(db) if g.get("id") != group_id]})
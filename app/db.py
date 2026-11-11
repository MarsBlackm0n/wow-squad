from __future__ import annotations

import os
from datetime import datetime, timezone

from sqlalchemy import (JSON, BigInteger, Boolean, DateTime, Float, ForeignKey, Integer, String, Text,
                        UniqueConstraint, create_engine, select)
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, relationship, sessionmaker

from .config import settings


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Character(Base):
    __tablename__ = "characters"
    __table_args__ = (UniqueConstraint("region", "slug"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    region: Mapped[str] = mapped_column(String(4))
    slug: Mapped[str] = mapped_column(String(80))  # "nom-surnom" en minuscules
    name: Mapped[str] = mapped_column(String(40))
    surname: Mapped[str] = mapped_column(String(40), default="")
    player: Mapped[str] = mapped_column(String(40), default="")  # le pote derrière le perso
    battletag: Mapped[str | None] = mapped_column(String(64), nullable=True)

    class_name: Mapped[str] = mapped_column(String(40), default="")
    race: Mapped[str] = mapped_column(String(40), default="")
    faction: Mapped[str] = mapped_column(String(20), default="")
    guild: Mapped[str] = mapped_column(String(80), default="")
    ruleset: Mapped[str] = mapped_column(String(60), default="")
    level: Mapped[int] = mapped_column(Integer, default=0)
    ilvl: Mapped[float] = mapped_column(Float, default=0)
    achievement_points: Mapped[int] = mapped_column(Integer, default=0)
    deaths: Mapped[int | None] = mapped_column(Integer, nullable=True)
    avatar_url: Mapped[str] = mapped_column(Text, default="")
    last_login: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # ok | pending (pas encore visible dans l'API) | error
    status: Mapped[str] = mapped_column(String(16), default="pending")
    status_message: Mapped[str] = mapped_column(Text, default="")
    # Dernier état complet (hauts faits, montures, métiers...) pour calculer les nouveautés.
    state: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    refreshed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    snapshots: Mapped[list[Snapshot]] = relationship(back_populates="character", cascade="all, delete-orphan",
                                                     order_by="Snapshot.taken_at")
    events: Mapped[list[Event]] = relationship(back_populates="character", cascade="all, delete-orphan")

    @property
    def display_name(self) -> str:
        return f"{self.name} {self.surname}".strip()


class Snapshot(Base):
    """Une ligne par rafraîchissement où quelque chose a bougé : sert aux courbes de progression."""
    __tablename__ = "snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    character_id: Mapped[int] = mapped_column(ForeignKey("characters.id", ondelete="CASCADE"), index=True)
    taken_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    level: Mapped[int] = mapped_column(Integer, default=0)
    ilvl: Mapped[float] = mapped_column(Float, default=0)
    achievement_points: Mapped[int] = mapped_column(Integer, default=0)
    deaths: Mapped[int | None] = mapped_column(Integer, nullable=True)
    mounts: Mapped[int | None] = mapped_column(Integer, nullable=True)
    honorable_kills: Mapped[int | None] = mapped_column(Integer, nullable=True)

    character: Mapped[Character] = relationship(back_populates="snapshots")


class Event(Base):
    __tablename__ = "events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    character_id: Mapped[int] = mapped_column(ForeignKey("characters.id", ondelete="CASCADE"), index=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    kind: Mapped[str] = mapped_column(String(32))
    icon: Mapped[str] = mapped_column(String(8), default="")
    text: Mapped[str] = mapped_column(Text)
    data: Mapped[dict] = mapped_column(JSON, default=dict)
    notified: Mapped[bool] = mapped_column(Boolean, default=False)

    character: Mapped[Character] = relationship(back_populates="events")


class Account(Base):
    """Compte Battle.net lié. Aucun secret ici : le jeton OAuth (lecture seule, 24 h) reste en mémoire du worker."""
    __tablename__ = "accounts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    battle_id: Mapped[int] = mapped_column(BigInteger, unique=True)
    battletag: Mapped[str] = mapped_column(String(64))
    linked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class KeyValue(Base):
    __tablename__ = "kv"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[dict] = mapped_column(JSON, default=dict)


if settings.database_url.startswith("sqlite"):
    os.makedirs("data", exist_ok=True)
    engine = create_engine(settings.database_url, connect_args={"check_same_thread": False})
else:
    engine = create_engine(settings.database_url, pool_pre_ping=True)

SessionLocal = sessionmaker(engine, expire_on_commit=False)


def init_db() -> None:
    Base.metadata.create_all(engine)


def kv_get(db: Session, key: str) -> dict | None:
    row = db.get(KeyValue, key)
    return row.value if row else None


def kv_set(db: Session, key: str, value: dict) -> None:
    row = db.get(KeyValue, key)
    if row:
        row.value = value
    else:
        db.add(KeyValue(key=key, value=value))


__all__ = ["Account", "Character", "Event", "KeyValue", "Snapshot", "SessionLocal", "init_db", "kv_get", "kv_set",
           "select", "utcnow"]

"""
Datenbankmodelle und Verbindung (MySQL/MariaDB via SQLAlchemy)
"""

from datetime import datetime
from sqlalchemy import (
    create_engine, Column, Integer, String, DateTime,
    ForeignKey, UniqueConstraint, SmallInteger
)
from sqlalchemy.orm import declarative_base, sessionmaker, relationship

import config

DATABASE_URL = (
    f"mysql+pymysql://{config.DB_USER}:{config.DB_PASSWORD}"
    f"@{config.DB_HOST}:{config.DB_PORT}/{config.DB_NAME}?charset=utf8mb4"
)

engine = create_engine(DATABASE_URL, echo=False, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine)
Base = declarative_base()


class Team(Base):
    """Mannschaft des Vereins (z.B. 'Herren 1', 'Damen 1')"""
    __tablename__ = "teams"

    id            = Column(Integer, primary_key=True, autoincrement=True)
    fussball_id   = Column(String(64), unique=True, nullable=False)   # ID auf fußball.de
    name          = Column(String(128), nullable=False)
    gender        = Column(String(10))                                 # 'Herren' | 'Damen'
    updated_at    = Column(DateTime, default=datetime.now, onupdate=datetime.now)

    matches       = relationship("Match", back_populates="team", cascade="all, delete-orphan")
    standings     = relationship("Standing", back_populates="team", cascade="all, delete-orphan")


class Match(Base):
    """Spiel (Spielplan + Ergebnis)"""
    __tablename__ = "matches"
    __table_args__ = (UniqueConstraint("fussball_match_id"),)

    id               = Column(Integer, primary_key=True, autoincrement=True)
    fussball_match_id = Column(String(64), nullable=False)
    team_id          = Column(Integer, ForeignKey("teams.id"), nullable=False)
    competition      = Column(String(128))                             # Staffelname
    match_day        = Column(SmallInteger)                            # Spieltag
    match_date       = Column(DateTime)
    home_team        = Column(String(128))
    away_team        = Column(String(128))
    home_goals       = Column(SmallInteger)                            # NULL = noch nicht gespielt
    away_goals       = Column(SmallInteger)
    status           = Column(String(20), default="scheduled")        # scheduled | finished | cancelled
    venue            = Column(String(128))
    updated_at       = Column(DateTime, default=datetime.now, onupdate=datetime.now)

    team = relationship("Team", back_populates="matches")


class Standing(Base):
    """Tabellenplatz einer Mannschaft in einer Staffel"""
    __tablename__ = "standings"
    __table_args__ = (UniqueConstraint("team_id", "competition", "season"),)

    id            = Column(Integer, primary_key=True, autoincrement=True)
    team_id       = Column(Integer, ForeignKey("teams.id"), nullable=False)
    competition   = Column(String(128))
    season        = Column(String(10))
    rank          = Column(SmallInteger)
    team_name     = Column(String(128))
    played        = Column(SmallInteger, default=0)
    wins          = Column(SmallInteger, default=0)
    draws         = Column(SmallInteger, default=0)
    losses        = Column(SmallInteger, default=0)
    goals_for     = Column(SmallInteger, default=0)
    goals_against = Column(SmallInteger, default=0)
    points        = Column(SmallInteger, default=0)
    updated_at    = Column(DateTime, default=datetime.now, onupdate=datetime.now)

    team = relationship("Team", back_populates="standings")


def init_db():
    """Erstellt alle Tabellen (falls nicht vorhanden)."""
    Base.metadata.create_all(bind=engine)
    print("✓ Datenbank initialisiert")

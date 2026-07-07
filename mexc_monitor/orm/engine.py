from __future__ import annotations

from pathlib import Path

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Engine

from mexc_monitor.orm.base import Base

_engines: dict[str, Engine] = {}


def _migrate_spread_snapshots_columns(engine: Engine) -> None:
    """SQLite: добавить колонки к существующей таблице без Alembic."""
    insp = inspect(engine)
    if "spread_snapshots" not in insp.get_table_names():
        return
    existing = {c["name"] for c in insp.get_columns("spread_snapshots")}
    alters: list[str] = []
    for col, sqltype in (
        ("fee_round_trip_bps", "REAL"),
        ("net_spread_bps", "REAL"),
        ("l1_max_executable_base", "REAL"),
        ("l1_max_notional_quote", "REAL"),
    ):
        if col not in existing:
            alters.append(f"ALTER TABLE spread_snapshots ADD COLUMN {col} {sqltype}")
    if not alters:
        return
    with engine.begin() as conn:
        for stmt in alters:
            conn.execute(text(stmt))


def get_engine(path: Path) -> Engine:
    """Один Engine на путь к файлу (потокобезопасные сессии при check_same_thread=False)."""
    key = str(path.resolve())
    if key not in _engines:
        url = f"sqlite:///{path.resolve().as_posix()}"
        _engines[key] = create_engine(
            url,
            echo=False,
            connect_args={"check_same_thread": False},
        )
    return _engines[key]


def create_schema(path: Path) -> None:
    import mexc_monitor.orm.models  # noqa: F401 — регистрация SpreadSnapshot в metadata

    path.parent.mkdir(parents=True, exist_ok=True)
    engine = get_engine(path)
    Base.metadata.create_all(engine)
    _migrate_spread_snapshots_columns(engine)

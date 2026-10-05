"""User-editable runtime settings (portfolio size, risk %) persisted in SQLite so both the
dashboard and the background jobs (live_scanner) see the same values, unlike the
env-based defaults in core/config.py which only change on restart."""

from core.db import get_db


def get_setting(key: str, default: str = None) -> str:
    conn = get_db()
    row = conn.execute("SELECT value FROM app_settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(key: str, value) -> None:
    conn = get_db()
    conn.execute(
        "INSERT INTO app_settings (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, str(value)),
    )
    conn.commit()


def get_float_setting(key: str, default: float) -> float:
    value = get_setting(key)
    return float(value) if value is not None else default

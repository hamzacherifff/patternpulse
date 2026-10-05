"""Manual trade log: positions the user actually took, kept separate from the tool's own
signals table so real P&L can be tracked and compared against what was recommended."""

from datetime import datetime, timezone

from core.db import get_db


def open_position(symbol: str, direction: str, entry_price: float, size: float,
                   notes: str = "", linked_signal_id: int = None) -> int:
    conn = get_db()
    now = datetime.now(timezone.utc).isoformat()
    cur = conn.execute(
        """
        INSERT INTO positions (symbol, direction, entry_price, size, opened_at, status, notes, linked_signal_id)
        VALUES (?, ?, ?, ?, ?, 'open', ?, ?)
        """,
        (symbol.upper(), direction, entry_price, size, now, notes, linked_signal_id),
    )
    conn.commit()
    return cur.lastrowid


def close_position(position_id: int, exit_price: float) -> float:
    conn = get_db()
    row = conn.execute("SELECT * FROM positions WHERE id = ?", (position_id,)).fetchone()
    if row is None or row["status"] != "open":
        raise ValueError("Position not open or not found")

    direction_mult = 1 if row["direction"] == "long" else -1
    realized_pnl = row["size"] * (exit_price - row["entry_price"]) / row["entry_price"] * direction_mult
    now = datetime.now(timezone.utc).isoformat()

    conn.execute(
        "INSERT INTO position_closes (position_id, exit_price, closed_at, realized_pnl) VALUES (?, ?, ?, ?)",
        (position_id, exit_price, now, realized_pnl),
    )
    conn.execute("UPDATE positions SET status = 'closed' WHERE id = ?", (position_id,))
    conn.commit()
    return realized_pnl


def get_open_positions() -> list[dict]:
    conn = get_db()
    return [dict(r) for r in conn.execute(
        "SELECT * FROM positions WHERE status = 'open' ORDER BY opened_at DESC"
    ).fetchall()]


def get_closed_positions() -> list[dict]:
    conn = get_db()
    return [dict(r) for r in conn.execute(
        """
        SELECT p.*, c.exit_price, c.closed_at, c.realized_pnl
        FROM positions p JOIN position_closes c ON c.position_id = p.id
        WHERE p.status = 'closed' ORDER BY c.closed_at DESC
        """
    ).fetchall()]


def delete_position(position_id: int) -> None:
    conn = get_db()
    conn.execute("DELETE FROM position_closes WHERE position_id = ?", (position_id,))
    conn.execute("DELETE FROM positions WHERE id = ?", (position_id,))
    conn.commit()


def unrealized_pnl(position: dict, current_price: float) -> float:
    direction_mult = 1 if position["direction"] == "long" else -1
    return position["size"] * (current_price - position["entry_price"]) / position["entry_price"] * direction_mult

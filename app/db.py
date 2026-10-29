"""Books for Super Hayat Market.

Money is stored as integer cents. Quantities are decimals.
Stock, sales, and amounts owed change only when a save function is called.
"""

from __future__ import annotations

from contextvars import ContextVar
import calendar
import os
import re
import sqlite3
import threading
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = Path(os.environ.get("SUPERHAYAT_DATA", ROOT / "data"))
DB_PATH = DATA / "super_hayat.db"
INCOMING = DATA / "incoming"
INVOICES = DATA / "invoices"
SALES = DATA / "sales"
BACKUPS = DATA / "backups"

_LOCK = threading.Lock()

MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
MONTHS_LONG = [
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
]

CATEGORIES = [
    ("debt", "Debt payments", 10, 0, 1),
    ("meat", "Meat invoices", 20, 0, 1),
    ("grocery", "Grocery invoices", 30, 0, 1),
    ("produce", "Produce", 40, 0, 1),
    ("rent", "Rent", 50, 0, 1),
    ("utilities", "Utilities", 60, 0, 1),
    ("payroll", "Employee pay", 70, 0, 1),
    ("insurance", "Insurance", 80, 0, 1),
    ("taxes", "Taxes", 90, 0, 1),
    ("other", "Supplies, fees, and repairs", 100, 0, 1),
    ("cushion", "Cash cushion", 1000, 1, 1),
]

BILL_SKIP = {"meat", "grocery", "produce", "debt", "cushion"}
LINE_CATEGORIES = {"meat", "grocery", "produce"}


def now_iso() -> str:
    return datetime.now().replace(microsecond=0).isoformat(sep=" ")


def parse_day(value: str) -> date:
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError as exc:
        raise ValueError("Enter a real date.") from exc


def monday_of(day: date) -> date:
    return day - timedelta(days=day.weekday())


def pretty(day: date) -> str:
    return f"{MONTHS[day.month - 1]} {day.day}, {day.year}"


def pretty_long_month(day: date) -> str:
    return f"{MONTHS_LONG[day.month - 1]} {day.year}"


def add_months(day: date, count: int = 1) -> date:
    month = day.month - 1 + count
    year = day.year + month // 12
    month = month % 12 + 1
    last = calendar.monthrange(year, month)[1]
    return date(year, month, min(day.day, last))


def cents(value, label="Amount") -> int:
    if value is None or value == "":
        return 0
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} needs to be a number.")
    number = int(value)
    if number < 0:
        raise ValueError(f"{label} cannot be negative.")
    return number


def quantity(value, label="Quantity") -> float:
    try:
        number = round(float(value), 3)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} needs to be a number.") from exc
    if number < 0:
        raise ValueError(f"{label} cannot be negative.")
    return number


def norm_name(name: str) -> str:
    return " ".join(str(name or "").strip().lower().split())


def money_text(cents_value: int) -> str:
    sign = "-" if cents_value < 0 else ""
    amount = abs(int(cents_value))
    return f"{sign}${amount // 100:,}.{amount % 100:02d}"


_ACTIVE_CONNECTION = ContextVar("active_connection", default=None)

@contextmanager
def connect():
    active = _ACTIVE_CONNECTION.get()
    if active is not None:
        yield active
        return
    DATA.mkdir(parents=True, exist_ok=True)
    with _LOCK:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()


def init_db() -> None:
    for folder in (DATA, INCOMING, INVOICES, SALES, BACKUPS):
        folder.mkdir(parents=True, exist_ok=True)
    with connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS categories (
                key TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                sort_order INTEGER NOT NULL,
                is_cushion INTEGER NOT NULL DEFAULT 0,
                builtin INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS products (
                id INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                name_key TEXT NOT NULL UNIQUE,
                category TEXT NOT NULL,
                track INTEGER NOT NULL DEFAULT 0,
                last_cost_cents INTEGER,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS product_aliases (
                alias_key TEXT PRIMARY KEY,
                product_id INTEGER NOT NULL REFERENCES products(id),
                alias_name TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS stock_moves (
                id INTEGER PRIMARY KEY,
                product_id INTEGER NOT NULL REFERENCES products(id),
                moved_on TEXT NOT NULL,
                kind TEXT NOT NULL,
                qty REAL NOT NULL,
                unit_cost_cents INTEGER,
                sale_price_cents INTEGER,
                source_key TEXT,
                note TEXT
            );
            CREATE TABLE IF NOT EXISTS invoices (
                id INTEGER PRIMARY KEY,
                supplier TEXT NOT NULL,
                invoice_date TEXT NOT NULL,
                due_date TEXT,
                category TEXT NOT NULL DEFAULT 'grocery',
                tax_other_cents INTEGER NOT NULL DEFAULT 0,
                printed_total_cents INTEGER,
                photo_path TEXT,
                notes TEXT,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS invoice_lines (
                id INTEGER PRIMARY KEY,
                invoice_id INTEGER NOT NULL REFERENCES invoices(id) ON DELETE CASCADE,
                product_id INTEGER REFERENCES products(id),
                item_name TEXT NOT NULL,
                category TEXT NOT NULL,
                quantity REAL NOT NULL,
                unit_cost_cents INTEGER NOT NULL,
                line_total_cents INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS invoice_payments (
                id INTEGER PRIMARY KEY,
                invoice_id INTEGER NOT NULL REFERENCES invoices(id) ON DELETE CASCADE,
                paid_on TEXT NOT NULL,
                amount_cents INTEGER NOT NULL,
                note TEXT
            );
            CREATE TABLE IF NOT EXISTS loans (
                id INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                starting_cents INTEGER NOT NULL,
                regular_cents INTEGER,
                frequency TEXT,
                next_due TEXT,
                notes TEXT,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS loan_payments (
                id INTEGER PRIMARY KEY,
                loan_id INTEGER NOT NULL REFERENCES loans(id) ON DELETE CASCADE,
                paid_on TEXT NOT NULL,
                amount_cents INTEGER NOT NULL,
                note TEXT
            );
            CREATE TABLE IF NOT EXISTS bills (
                id INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                category_key TEXT NOT NULL,
                amount_cents INTEGER NOT NULL,
                next_due TEXT NOT NULL,
                recurrence TEXT NOT NULL,
                paid_this_period_cents INTEGER NOT NULL DEFAULT 0,
                active INTEGER NOT NULL DEFAULT 1,
                notes TEXT
            );
            CREATE TABLE IF NOT EXISTS bill_payments (
                id INTEGER PRIMARY KEY,
                bill_id INTEGER NOT NULL REFERENCES bills(id) ON DELETE CASCADE,
                due_on TEXT NOT NULL,
                paid_on TEXT NOT NULL,
                amount_cents INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS sales (
                sale_date TEXT PRIMARY KEY,
                meat_cents INTEGER NOT NULL DEFAULT 0,
                grocery_cents INTEGER NOT NULL DEFAULT 0,
                not_in_balance_cents INTEGER NOT NULL DEFAULT 0,
                waiting_cents INTEGER NOT NULL DEFAULT 0,
                totals_photo TEXT,
                items_photo TEXT,
                notes TEXT
            );
            CREATE TABLE IF NOT EXISTS sale_items (
                id INTEGER PRIMARY KEY,
                sale_date TEXT NOT NULL,
                product_id INTEGER REFERENCES products(id),
                raw_name TEXT NOT NULL,
                qty REAL NOT NULL,
                price_cents INTEGER
            );
            CREATE TABLE IF NOT EXISTS weeks (
                week_start TEXT PRIMARY KEY,
                opening_cents INTEGER NOT NULL DEFAULT 0,
                purchases_cents INTEGER NOT NULL DEFAULT 0,
                pocket_cents INTEGER NOT NULL DEFAULT 0,
                notes TEXT,
                saved_at TEXT
            );
            CREATE TABLE IF NOT EXISTS week_reserves (
                week_start TEXT NOT NULL REFERENCES weeks(week_start) ON DELETE CASCADE,
                category_key TEXT NOT NULL,
                amount_cents INTEGER NOT NULL,
                PRIMARY KEY (week_start, category_key)
            );
            """
        )
        for key, name, sort_order, cushion, builtin in CATEGORIES:
            conn.execute(
                """
                INSERT INTO categories (key, name, sort_order, is_cushion, builtin)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(key) DO NOTHING
                """,
                (key, name, sort_order, cushion, builtin),
            )
    _purge_old_incoming()


def _purge_old_incoming() -> None:
    if not INCOMING.exists():
        return
    cutoff = datetime.now().timestamp() - 2 * 24 * 3600
    for path in INCOMING.iterdir():
        try:
            if path.is_file() and path.stat().st_mtime < cutoff:
                path.unlink()
        except OSError:
            pass


def _categories(conn) -> list[dict]:
    rows = conn.execute("SELECT * FROM categories ORDER BY sort_order, name").fetchall()
    return [
        {
            "key": row["key"],
            "name": row["name"],
            "is_cushion": bool(row["is_cushion"]),
            "builtin": bool(row["builtin"]),
        }
        for row in rows
    ]


def _invoice_totals(conn, invoice_id: int) -> tuple[int, int, list[sqlite3.Row]]:
    invoice = conn.execute("SELECT * FROM invoices WHERE id = ?", (invoice_id,)).fetchone()
    lines = conn.execute(
        "SELECT * FROM invoice_lines WHERE invoice_id = ? ORDER BY id", (invoice_id,)
    ).fetchall()
    paid = conn.execute(
        "SELECT COALESCE(SUM(amount_cents), 0) AS paid FROM invoice_payments WHERE invoice_id = ?",
        (invoice_id,),
    ).fetchone()["paid"]
    line_sum = sum(row["line_total_cents"] for row in lines)
    computed = line_sum + invoice["tax_other_cents"]
    owed = invoice["printed_total_cents"] if invoice["printed_total_cents"] is not None else computed
    return int(owed), int(paid), lines


def _allocate(amount: int, weights: dict[str, int]) -> dict[str, int]:
    if amount <= 0 or not weights:
        return {}
    total = sum(weights.values()) or 1
    keys = list(weights)
    shares = {key: amount * weights[key] // total for key in keys}
    leftover = amount - sum(shares.values())
    if leftover:
        biggest = max(keys, key=lambda key: weights[key])
        shares[biggest] += leftover
    return {key: value for key, value in shares.items() if value}


def still_owed_cents(conn=None) -> int:
    if conn is None:
        with connect() as owned:
            return still_owed_cents(owned)
    total = 0
    for invoice in conn.execute("SELECT id FROM invoices"):
        owed, paid, _lines = _invoice_totals(conn, invoice["id"])
        total += max(0, owed - paid)
    for loan in conn.execute("SELECT * FROM loans"):
        paid = conn.execute(
            "SELECT COALESCE(SUM(amount_cents), 0) AS paid FROM loan_payments WHERE loan_id = ?",
            (loan["id"],),
        ).fetchone()["paid"]
        total += max(0, loan["starting_cents"] - paid)
    return int(total)


def _levels(conn) -> dict[int, float]:
    levels: dict[int, float] = {}
    rows = conn.execute(
        "SELECT * FROM stock_moves ORDER BY moved_on, id"
    ).fetchall()
    for row in rows:
        product_id = row["product_id"]
        if row["kind"] == "count":
            levels[product_id] = float(row["qty"])
        elif row["kind"] == "receive":
            levels[product_id] = levels.get(product_id, 0.0) + float(row["qty"])
        elif row["kind"] in ("sold", "spoiled"):
            levels[product_id] = levels.get(product_id, 0.0) - float(row["qty"])
    return levels


def stock_value_cents(conn=None) -> int:
    if conn is None:
        with connect() as owned:
            return stock_value_cents(owned)
    levels = _levels(conn)
    total = 0
    for product in conn.execute("SELECT * FROM products WHERE track = 1"):
        on_hand = levels.get(product["id"], 0.0)
        if on_hand > 0 and product["last_cost_cents"]:
            total += int(round(on_hand * product["last_cost_cents"]))
    return total


def _product_public(row, on_hand: float) -> dict:
    value = 0
    if on_hand > 0 and row["last_cost_cents"]:
        value = int(round(on_hand * row["last_cost_cents"]))
    return {
        "id": row["id"],
        "name": row["name"],
        "category": row["category"],
        "track": bool(row["track"]),
        "on_hand": round(on_hand, 3),
        "last_cost_cents": row["last_cost_cents"],
        "value_cents": value,
        "short": on_hand < -0.0001,
    }


def list_stock(category: str | None = None, query: str = "") -> dict:
    with connect() as conn:
        levels = _levels(conn)
        products = []
        sql = "SELECT * FROM products WHERE track = 1"
        args: list = []
        if category in LINE_CATEGORIES:
            sql += " AND category = ?"
            args.append(category)
        if query.strip():
            sql += " AND name_key LIKE ?"
            args.append(f"%{norm_name(query)}%")
        sql += " ORDER BY name"
        for row in conn.execute(sql, args):
            products.append(_product_public(row, levels.get(row["id"], 0.0)))
        return {"products": products, "stock_value_cents": stock_value_cents(conn)}


def product_detail(product_id: int, grain: str, day: str) -> dict:
    start, end = period_bounds(grain, day)
    with connect() as conn:
        row = conn.execute("SELECT * FROM products WHERE id = ?", (product_id,)).fetchone()
        if row is None:
            raise ValueError("That product is not in the list.")
        levels = _levels(conn)
        spent = conn.execute(
            """
            SELECT COALESCE(SUM(l.line_total_cents), 0) AS spent
            FROM invoice_lines l
            JOIN invoices i ON i.id = l.invoice_id
            WHERE l.product_id = ? AND i.invoice_date >= ? AND i.invoice_date <= ?
            """,
            (product_id, start.isoformat(), end.isoformat()),
        ).fetchone()["spent"]
        moves = []
        for move in conn.execute(
            "SELECT * FROM stock_moves WHERE product_id = ? ORDER BY moved_on DESC, id DESC LIMIT 12",
            (product_id,),
        ):
            moves.append(
                {
                    "moved_on": move["moved_on"],
                    "kind": move["kind"],
                    "qty": move["qty"],
                    "note": move["note"] or "",
                }
            )
        public = _product_public(row, levels.get(product_id, 0.0))
        public["spent_cents"] = int(spent)
        public["period_label"] = _period_label(grain, start, end)
        public["moves"] = moves
        return public


def rename_product(product_id: int, name: str) -> dict:
    cleaned = " ".join(str(name or "").split())
    key = norm_name(cleaned)
    if not key:
        raise ValueError("Enter a product name.")
    with connect() as conn:
        row = conn.execute("SELECT * FROM products WHERE id = ?", (product_id,)).fetchone()
        if row is None:
            raise ValueError("That product is not in the list.")
        other = conn.execute(
            "SELECT * FROM products WHERE name_key = ? AND id != ?", (key, product_id)
        ).fetchone()
        if other:
            conn.execute(
                "UPDATE invoice_lines SET product_id = ? WHERE product_id = ?",
                (other["id"], product_id),
            )
            conn.execute(
                "UPDATE sale_items SET product_id = ? WHERE product_id = ?",
                (other["id"], product_id),
            )
            conn.execute(
                "UPDATE stock_moves SET product_id = ? WHERE product_id = ?",
                (other["id"], product_id),
            )
            conn.execute(
                "UPDATE product_aliases SET product_id = ? WHERE product_id = ?",
                (other["id"], product_id),
            )
            conn.execute("DELETE FROM products WHERE id = ?", (product_id,))
            product_id = other["id"]
        else:
            conn.execute(
                "UPDATE products SET name = ?, name_key = ? WHERE id = ?",
                (cleaned, key, product_id),
            )
        fresh = conn.execute("SELECT * FROM products WHERE id = ?", (product_id,)).fetchone()
        return _product_public(fresh, _levels(conn).get(product_id, 0.0))


def update_product(product_id: int, payload: dict) -> dict:
    cleaned = " ".join(str(payload.get("name") or "").split())
    key = norm_name(cleaned)
    if not key:
        raise ValueError("Enter a product name.")
    category = payload.get("category") if payload.get("category") in LINE_CATEGORIES else None
    unit = payload.get("unit_cost_cents")
    unit_cents = None if unit in (None, "") else cents(unit, "Unit cost")
    on_hand = payload.get("on_hand")
    with connect() as conn:
        row = conn.execute("SELECT * FROM products WHERE id = ?", (product_id,)).fetchone()
        if row is None:
            raise ValueError("That product is not in the list.")
        other = conn.execute(
            "SELECT id FROM products WHERE name_key = ? AND id != ?", (key, product_id)
        ).fetchone()
        if other:
            raise ValueError("That name is already in inventory. Use the other item, or pick a different name.")
        conn.execute(
            "UPDATE products SET name = ?, name_key = ?, category = COALESCE(?, category), last_cost_cents = COALESCE(?, last_cost_cents) WHERE id = ?",
            (cleaned, key, category, unit_cents, product_id),
        )
        if on_hand not in (None, ""):
            amount = quantity(on_hand, "On hand")
            conn.execute(
                """
                INSERT INTO stock_moves (product_id, moved_on, kind, qty, unit_cost_cents, source_key, note)
                VALUES (?, ?, 'count', ?, ?, ?, 'Edited on hand')
                """,
                (
                    product_id,
                    date.today().isoformat(),
                    amount,
                    unit_cents if unit_cents is not None else row["last_cost_cents"],
                    f"count:{product_id}:{now_iso()}",
                ),
            )
        fresh = conn.execute("SELECT * FROM products WHERE id = ?", (product_id,)).fetchone()
        return _product_public(fresh, _levels(conn).get(product_id, 0.0))


def remove_product(product_id: int) -> None:
    with connect() as conn:
        row = conn.execute("SELECT id FROM products WHERE id = ?", (product_id,)).fetchone()
        if row is None:
            raise ValueError("That product is not in the list.")
        conn.execute("DELETE FROM stock_moves WHERE product_id = ?", (product_id,))
        conn.execute("UPDATE products SET track = 0 WHERE id = ?", (product_id,))


def set_tracking(product_id: int, track: bool) -> dict:
    with connect() as conn:
        row = conn.execute("SELECT * FROM products WHERE id = ?", (product_id,)).fetchone()
        if row is None:
            raise ValueError("That product is not in the list.")
        conn.execute("UPDATE products SET track = ? WHERE id = ?", (1 if track else 0, product_id))
        fresh = conn.execute("SELECT * FROM products WHERE id = ?", (product_id,)).fetchone()
        return _product_public(fresh, _levels(conn).get(product_id, 0.0))


def spoil_product(product_id: int, qty: float, day: str) -> dict:
    amount = quantity(qty, "Quantity")
    if amount <= 0:
        raise ValueError("Enter how much was spoiled or tossed.")
    moved_on = parse_day(day).isoformat()
    with connect() as conn:
        row = conn.execute("SELECT * FROM products WHERE id = ?", (product_id,)).fetchone()
        if row is None:
            raise ValueError("That product is not in the list.")
        conn.execute(
            """
            INSERT INTO stock_moves (product_id, moved_on, kind, qty, unit_cost_cents, source_key, note)
            VALUES (?, ?, 'spoiled', ?, ?, ?, 'Spoiled or tossed')
            """,
            (product_id, moved_on, amount, row["last_cost_cents"], f"spoil:{product_id}:{now_iso()}"),
        )
        fresh = conn.execute("SELECT * FROM products WHERE id = ?", (product_id,)).fetchone()
        return _product_public(fresh, _levels(conn).get(product_id, 0.0))


def count_product(product_id: int, qty: float, day: str) -> dict:
    amount = quantity(qty, "Count")
    moved_on = parse_day(day).isoformat()
    with connect() as conn:
        row = conn.execute("SELECT * FROM products WHERE id = ?", (product_id,)).fetchone()
        if row is None:
            raise ValueError("That product is not in the list.")
        conn.execute("UPDATE products SET track = 1 WHERE id = ?", (product_id,))
        conn.execute(
            """
            INSERT INTO stock_moves (product_id, moved_on, kind, qty, unit_cost_cents, source_key, note)
            VALUES (?, ?, 'count', ?, ?, ?, 'Shelf count')
            """,
            (product_id, moved_on, amount, row["last_cost_cents"], f"count:{product_id}:{now_iso()}"),
        )
        fresh = conn.execute("SELECT * FROM products WHERE id = ?", (product_id,)).fetchone()
        return _product_public(fresh, _levels(conn).get(product_id, 0.0))


def _find_product(conn, name: str):
    key = norm_name(name)
    if not key:
        return None
    row = conn.execute("SELECT * FROM products WHERE name_key = ?", (key,)).fetchone()
    if row:
        return row
    alias = conn.execute(
        "SELECT product_id FROM product_aliases WHERE alias_key = ?", (key,)
    ).fetchone()
    if not alias:
        return None
    return conn.execute("SELECT * FROM products WHERE id = ?", (alias["product_id"],)).fetchone()


def match_names(names: list[str]) -> list[dict]:
    with connect() as conn:
        found = []
        for name in names:
            row = _find_product(conn, name)
            found.append(
                {
                    "name": name,
                    "product_id": row["id"] if row else None,
                    "product_name": row["name"] if row else None,
                }
            )
        return found


def search_products(query: str = "") -> list[dict]:
    with connect() as conn:
        levels = _levels(conn)
        if query.strip():
            rows = conn.execute(
                "SELECT * FROM products WHERE name_key LIKE ? ORDER BY name LIMIT 30",
                (f"%{norm_name(query)}%",),
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM products ORDER BY name LIMIT 300").fetchall()
        return [_product_public(row, levels.get(row["id"], 0.0)) for row in rows]


def _remember_alias(conn, product_id: int, raw_name: str) -> None:
    key = norm_name(raw_name)
    product = conn.execute("SELECT name_key FROM products WHERE id = ?", (product_id,)).fetchone()
    if not key or product is None or key == product["name_key"]:
        return
    conn.execute(
        """
        INSERT INTO product_aliases (alias_key, product_id, alias_name)
        VALUES (?, ?, ?)
        ON CONFLICT(alias_key) DO UPDATE SET
            product_id = excluded.product_id,
            alias_name = excluded.alias_name
        """,
        (key, product_id, str(raw_name).strip()),
    )


def _ensure_product(conn, name: str, category: str, product_id=None, track=None) -> int:
    if product_id:
        row = conn.execute("SELECT * FROM products WHERE id = ?", (product_id,)).fetchone()
        if row is None:
            raise ValueError("Pick a product from the list.")
        if category in ("meat", "produce"):
            conn.execute(
                "UPDATE products SET category = ?, track = 1 WHERE id = ?",
                (category, product_id),
            )
        elif track:
            conn.execute("UPDATE products SET track = 1 WHERE id = ?", (product_id,))
        _remember_alias(conn, product_id, name)
        return product_id
    existing = _find_product(conn, name)
    if existing:
        if category in ("meat", "produce"):
            conn.execute(
                "UPDATE products SET category = ?, track = 1 WHERE id = ?",
                (category, existing["id"]),
            )
        elif track:
            conn.execute("UPDATE products SET track = 1 WHERE id = ?", (existing["id"],))
        _remember_alias(conn, existing["id"], name)
        return existing["id"]
    cleaned = " ".join(str(name).split())
    key = norm_name(cleaned)
    if not key:
        raise ValueError("Enter an item name.")
    if track is None:
        track = category in ("meat", "produce")
    cur = conn.execute(
        """
        INSERT INTO products (name, name_key, category, track, created_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (cleaned, key, category if category in LINE_CATEGORIES else "grocery", 1 if track else 0, now_iso()),
    )
    return int(cur.lastrowid)


def _replace_invoice_stock(conn, invoice_id: int, invoice_date: str) -> None:
    conn.execute("DELETE FROM stock_moves WHERE source_key = ?", (f"invoice:{invoice_id}",))
    for line in conn.execute(
        "SELECT * FROM invoice_lines WHERE invoice_id = ? ORDER BY id", (invoice_id,)
    ):
        if not line["product_id"] or line["quantity"] <= 0:
            continue
        conn.execute(
            "UPDATE products SET last_cost_cents = ? WHERE id = ?",
            (line["unit_cost_cents"], line["product_id"]),
        )
        conn.execute(
            """
            INSERT INTO stock_moves (
                product_id, moved_on, kind, qty, unit_cost_cents, source_key, note
            ) VALUES (?, ?, 'receive', ?, ?, ?, ?)
            """,
            (
                line["product_id"],
                invoice_date,
                line["quantity"],
                line["unit_cost_cents"],
                f"invoice:{invoice_id}",
                "Received on invoice",
            ),
        )


def _line_payload(raw: dict, fallback: str) -> dict:
    name = " ".join(str(raw.get("name") or "").split())
    if not name:
        raise ValueError("Each line needs an item name.")
    category = raw.get("category") or fallback
    if category not in LINE_CATEGORIES:
        category = "grocery"
    qty = quantity(raw.get("quantity", raw.get("qty", 0)))
    unit = cents(raw.get("unit_cost_cents"), "Unit cost")
    if "line_total_cents" in raw and raw["line_total_cents"] is not None:
        total = cents(raw.get("line_total_cents"), "Line total")
    else:
        total = int(round(qty * unit))
    return {
        "name": name,
        "category": category,
        "quantity": qty,
        "unit_cost_cents": unit,
        "line_total_cents": total,
        "product_id": raw.get("product_id"),
    }


def save_invoice(payload: dict, invoice_id: int | None = None) -> dict:
    supplier = " ".join(str(payload.get("supplier") or "").split())
    if not supplier:
        raise ValueError("Enter who the invoice is from.")
    invoice_date = parse_day(payload.get("invoice_date") or date.today().isoformat()).isoformat()
    due = payload.get("due_date") or None
    if due:
        due = parse_day(due).isoformat()
    category = payload.get("category") if payload.get("category") in LINE_CATEGORIES else "grocery"
    tax = cents(payload.get("tax_other_cents"), "Tax or other charges")
    printed = payload.get("printed_total_cents")
    printed_cents = None if printed in (None, "") else cents(printed, "Invoice total")
    notes = str(payload.get("notes") or "").strip()
    photo = payload.get("photo_path")
    lines = [_line_payload(line, category) for line in payload.get("lines") or []]
    if not lines and not printed_cents:
        raise ValueError("Add a line for each item, or enter the invoice total.")
    with connect() as conn:
        if invoice_id is None:
            cur = conn.execute(
                """
                INSERT INTO invoices (
                    supplier, invoice_date, due_date, category, tax_other_cents,
                    printed_total_cents, photo_path, notes, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (supplier, invoice_date, due, category, tax, printed_cents, photo, notes, now_iso()),
            )
            invoice_id = int(cur.lastrowid)
        else:
            existing = conn.execute("SELECT id FROM invoices WHERE id = ?", (invoice_id,)).fetchone()
            if existing is None:
                raise ValueError("That invoice is not in the list.")
            conn.execute(
                """
                UPDATE invoices
                SET supplier = ?, invoice_date = ?, due_date = ?, category = ?,
                    tax_other_cents = ?, printed_total_cents = ?, notes = ?,
                    photo_path = COALESCE(?, photo_path)
                WHERE id = ?
                """,
                (supplier, invoice_date, due, category, tax, printed_cents, notes, photo, invoice_id),
            )
            conn.execute("DELETE FROM invoice_lines WHERE invoice_id = ?", (invoice_id,))
        for line in lines:
            product_id = _ensure_product(
                conn, line["name"], line["category"], line.get("product_id"), track=True
            )
            conn.execute(
                """
                INSERT INTO invoice_lines (
                    invoice_id, product_id, item_name, category, quantity,
                    unit_cost_cents, line_total_cents
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    invoice_id,
                    product_id,
                    line["name"],
                    line["category"],
                    line["quantity"],
                    line["unit_cost_cents"],
                    line["line_total_cents"],
                ),
            )
        owed, paid, _lines = _invoice_totals(conn, invoice_id)
        if paid > owed:
            raise ValueError("Payments already recorded are more than this invoice total.")
        _replace_invoice_stock(conn, invoice_id, invoice_date)
        return _invoice_public(conn, invoice_id)


def _invoice_public(conn, invoice_id: int) -> dict:
    invoice = conn.execute("SELECT * FROM invoices WHERE id = ?", (invoice_id,)).fetchone()
    owed, paid, lines = _invoice_totals(conn, invoice_id)
    line_sum = sum(row["line_total_cents"] for row in lines)
    computed = line_sum + invoice["tax_other_cents"]
    return {
        "id": invoice["id"],
        "supplier": invoice["supplier"],
        "invoice_date": invoice["invoice_date"],
        "due_date": invoice["due_date"],
        "category": invoice["category"],
        "tax_other_cents": invoice["tax_other_cents"],
        "printed_total_cents": invoice["printed_total_cents"],
        "computed_cents": computed,
        "owed_cents": owed,
        "paid_cents": paid,
        "unpaid_cents": max(0, owed - paid),
        "difference_cents": (owed - computed) if invoice["printed_total_cents"] is not None else 0,
        "photo_path": invoice["photo_path"],
        "notes": invoice["notes"] or "",
        "lines": [
            {
                "id": row["id"],
                "product_id": row["product_id"],
                "name": row["item_name"],
                "category": row["category"],
                "quantity": row["quantity"],
                "unit_cost_cents": row["unit_cost_cents"],
                "line_total_cents": row["line_total_cents"],
            }
            for row in lines
        ],
    }


def list_invoices() -> list[dict]:
    with connect() as conn:
        ids = [row["id"] for row in conn.execute("SELECT id FROM invoices ORDER BY invoice_date DESC, id DESC")]
        return [_invoice_public(conn, invoice_id) for invoice_id in ids]


def get_invoice(invoice_id: int) -> dict:
    with connect() as conn:
        if conn.execute("SELECT 1 FROM invoices WHERE id = ?", (invoice_id,)).fetchone() is None:
            raise ValueError("That invoice is not in the list.")
        return _invoice_public(conn, invoice_id)


def delete_invoice(invoice_id: int) -> None:
    with connect() as conn:
        conn.execute("DELETE FROM stock_moves WHERE source_key = ?", (f"invoice:{invoice_id}",))
        deleted = conn.execute("DELETE FROM invoices WHERE id = ?", (invoice_id,))
        if deleted.rowcount == 0:
            raise ValueError("That invoice is not in the list.")


def pay_invoice(invoice_id: int, payload: dict) -> dict:
    amount = cents(payload.get("amount_cents"), "Payment")
    if amount <= 0:
        raise ValueError("Enter how much was paid.")
    paid_on = parse_day(payload.get("paid_on") or date.today().isoformat()).isoformat()
    note = str(payload.get("note") or "").strip()
    with connect() as conn:
        if conn.execute("SELECT 1 FROM invoices WHERE id = ?", (invoice_id,)).fetchone() is None:
            raise ValueError("That invoice is not in the list.")
        owed, paid, _lines = _invoice_totals(conn, invoice_id)
        remaining = max(0, owed - paid)
        if amount > remaining:
            raise ValueError(f"That payment is more than the {money_text(remaining)} still owed.")
        conn.execute(
            "INSERT INTO invoice_payments (invoice_id, paid_on, amount_cents, note) VALUES (?, ?, ?, ?)",
            (invoice_id, paid_on, amount, note),
        )
        return _invoice_public(conn, invoice_id)


def _loan_public(conn, loan_id: int) -> dict:
    loan = conn.execute("SELECT * FROM loans WHERE id = ?", (loan_id,)).fetchone()
    paid = conn.execute(
        "SELECT COALESCE(SUM(amount_cents), 0) AS paid FROM loan_payments WHERE loan_id = ?",
        (loan_id,),
    ).fetchone()["paid"]
    balance = max(0, loan["starting_cents"] - paid)
    return {
        "id": loan["id"],
        "name": loan["name"],
        "starting_cents": loan["starting_cents"],
        "regular_cents": loan["regular_cents"],
        "frequency": loan["frequency"] or "",
        "next_due": loan["next_due"],
        "notes": loan["notes"] or "",
        "paid_cents": int(paid),
        "balance_cents": int(balance),
    }


def list_loans() -> list[dict]:
    with connect() as conn:
        return [
            _loan_public(conn, row["id"])
            for row in conn.execute("SELECT id FROM loans ORDER BY name")
        ]


def save_loan(payload: dict) -> dict:
    name = " ".join(str(payload.get("name") or "").split())
    if not name:
        raise ValueError("Enter a name for the loan.")
    starting = cents(payload.get("starting_cents"), "Amount still owed")
    if starting <= 0:
        raise ValueError("Enter the amount still owed.")
    regular = payload.get("regular_cents")
    regular_cents = None if regular in (None, "") else cents(regular, "Regular payment")
    frequency = payload.get("frequency") or ""
    if frequency not in ("", "weekly", "monthly"):
        raise ValueError("Choose weekly, monthly, or no regular payment.")
    if regular_cents and not frequency:
        frequency = "monthly"
    next_due = payload.get("next_due") or None
    if next_due:
        next_due = parse_day(next_due).isoformat()
    notes = str(payload.get("notes") or "").strip()
    with connect() as conn:
        cur = conn.execute(
            """
            INSERT INTO loans (name, starting_cents, regular_cents, frequency, next_due, notes, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (name, starting, regular_cents, frequency, next_due, notes, now_iso()),
        )
        return _loan_public(conn, int(cur.lastrowid))


def pay_loan(loan_id: int, payload: dict) -> dict:
    amount = cents(payload.get("amount_cents"), "Payment")
    if amount <= 0:
        raise ValueError("Enter how much was paid.")
    paid_on = parse_day(payload.get("paid_on") or date.today().isoformat()).isoformat()
    with connect() as conn:
        loan = conn.execute("SELECT * FROM loans WHERE id = ?", (loan_id,)).fetchone()
        if loan is None:
            raise ValueError("That loan is not in the list.")
        paid = conn.execute(
            "SELECT COALESCE(SUM(amount_cents), 0) AS paid FROM loan_payments WHERE loan_id = ?",
            (loan_id,),
        ).fetchone()["paid"]
        remaining = max(0, loan["starting_cents"] - paid)
        if amount > remaining:
            raise ValueError(f"That payment is more than the {money_text(remaining)} still owed.")
        conn.execute(
            "INSERT INTO loan_payments (loan_id, paid_on, amount_cents, note) VALUES (?, ?, ?, ?)",
            (loan_id, paid_on, amount, str(payload.get("note") or "").strip()),
        )
        next_due = loan["next_due"]
        if loan["frequency"] and next_due:
            current = parse_day(next_due)
            if loan["frequency"] == "weekly":
                next_due = (current + timedelta(days=7)).isoformat()
            else:
                next_due = add_months(current).isoformat()
            conn.execute("UPDATE loans SET next_due = ? WHERE id = ?", (next_due, loan_id))
        return _loan_public(conn, loan_id)


def delete_loan(loan_id: int) -> None:
    with connect() as conn:
        deleted = conn.execute("DELETE FROM loans WHERE id = ?", (loan_id,))
        if deleted.rowcount == 0:
            raise ValueError("That loan is not in the list.")


def _bill_public(conn, bill_id: int) -> dict:
    bill = conn.execute("SELECT * FROM bills WHERE id = ?", (bill_id,)).fetchone()
    category = conn.execute(
        "SELECT name FROM categories WHERE key = ?", (bill["category_key"],)
    ).fetchone()
    remaining = max(0, bill["amount_cents"] - bill["paid_this_period_cents"]) if bill["active"] else 0
    return {
        "id": bill["id"],
        "name": bill["name"],
        "category_key": bill["category_key"],
        "category_name": category["name"] if category else bill["category_key"],
        "amount_cents": bill["amount_cents"],
        "next_due": bill["next_due"],
        "recurrence": bill["recurrence"],
        "paid_this_period_cents": bill["paid_this_period_cents"],
        "remaining_cents": remaining,
        "active": bool(bill["active"]),
        "notes": bill["notes"] or "",
    }


def list_bills() -> list[dict]:
    with connect() as conn:
        return [
            _bill_public(conn, row["id"])
            for row in conn.execute("SELECT id FROM bills ORDER BY active DESC, next_due, name")
        ]


def save_bill(payload: dict) -> dict:
    name = " ".join(str(payload.get("name") or "").split())
    if not name:
        raise ValueError("Enter a name for the bill.")
    amount = cents(payload.get("amount_cents"), "Amount")
    if amount <= 0:
        raise ValueError("Enter the bill amount.")
    category_key = str(payload.get("category_key") or "other")
    recurrence = payload.get("recurrence") or "monthly"
    if recurrence not in ("once", "weekly", "monthly"):
        raise ValueError("Choose how often this bill comes back.")
    next_due = parse_day(payload.get("next_due") or date.today().isoformat()).isoformat()
    notes = str(payload.get("notes") or "").strip()
    with connect() as conn:
        if conn.execute("SELECT 1 FROM categories WHERE key = ?", (category_key,)).fetchone() is None:
            raise ValueError("Choose a cost category.")
        if category_key in BILL_SKIP:
            raise ValueError("Put vendor invoices and loans on Who we owe. This list is for store bills.")
        cur = conn.execute(
            """
            INSERT INTO bills (
                name, category_key, amount_cents, next_due, recurrence, notes
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (name, category_key, amount, next_due, recurrence, notes),
        )
        return _bill_public(conn, int(cur.lastrowid))


def pay_bill(bill_id: int, payload: dict) -> dict:
    amount = cents(payload.get("amount_cents"), "Payment")
    if amount <= 0:
        raise ValueError("Enter how much was paid.")
    paid_on = parse_day(payload.get("paid_on") or date.today().isoformat()).isoformat()
    with connect() as conn:
        bill = conn.execute("SELECT * FROM bills WHERE id = ?", (bill_id,)).fetchone()
        if bill is None or not bill["active"]:
            raise ValueError("That bill is not waiting to be paid.")
        if bill["amount_cents"] <= bill["paid_this_period_cents"]:
            raise ValueError("This bill is already paid for this due date.")
        remaining_payment = amount
        due = parse_day(bill["next_due"])
        paid_this = bill["paid_this_period_cents"]
        bill_amount = bill["amount_cents"]
        while remaining_payment > 0 and bill["active"]:
            need = bill_amount - paid_this
            applied = min(remaining_payment, need)
            conn.execute(
                "INSERT INTO bill_payments (bill_id, due_on, paid_on, amount_cents) VALUES (?, ?, ?, ?)",
                (bill_id, due.isoformat(), paid_on, applied),
            )
            paid_this += applied
            remaining_payment -= applied
            if paid_this >= bill_amount:
                paid_this = 0
                if bill["recurrence"] == "once":
                    conn.execute(
                        "UPDATE bills SET active = 0, paid_this_period_cents = 0 WHERE id = ?",
                        (bill_id,),
                    )
                    bill = conn.execute("SELECT * FROM bills WHERE id = ?", (bill_id,)).fetchone()
                    break
                due = due + timedelta(days=7) if bill["recurrence"] == "weekly" else add_months(due)
                conn.execute(
                    "UPDATE bills SET next_due = ?, paid_this_period_cents = 0 WHERE id = ?",
                    (due.isoformat(), bill_id),
                )
            else:
                conn.execute(
                    "UPDATE bills SET paid_this_period_cents = ? WHERE id = ?",
                    (paid_this, bill_id),
                )
                break
        return _bill_public(conn, bill_id)


def delete_bill(bill_id: int) -> None:
    with connect() as conn:
        deleted = conn.execute("DELETE FROM bills WHERE id = ?", (bill_id,))
        if deleted.rowcount == 0:
            raise ValueError("That bill is not in the list.")


def add_category(name: str) -> dict:
    cleaned = " ".join(str(name or "").split())
    if not cleaned:
        raise ValueError("Enter a name for the cost.")
    key = re.sub(r"[^a-z0-9]+", "-", norm_name(cleaned)).strip("-") or "cost"
    with connect() as conn:
        base = key
        number = 2
        while conn.execute("SELECT 1 FROM categories WHERE key = ?", (key,)).fetchone():
            key = f"{base}-{number}"
            number += 1
        sort_order = conn.execute(
            "SELECT COALESCE(MAX(sort_order), 100) AS n FROM categories WHERE is_cushion = 0"
        ).fetchone()["n"] + 10
        if sort_order >= 1000:
            sort_order = 990
        conn.execute(
            "INSERT INTO categories (key, name, sort_order, is_cushion, builtin) VALUES (?, ?, ?, 0, 0)",
            (key, cleaned, sort_order),
        )
        row = conn.execute("SELECT * FROM categories WHERE key = ?", (key,)).fetchone()
        return {"key": row["key"], "name": row["name"], "is_cushion": False, "builtin": False}


def upsert_sale(payload: dict) -> dict:
    sale_date = parse_day(payload.get("sale_date") or date.today().isoformat()).isoformat()
    meat = cents(payload.get("meat_cents"), "Meat sales")
    grocery = cents(payload.get("grocery_cents"), "Grocery sales")
    extra = cents(payload.get("not_in_balance_cents"), "Amount not already in the balance")
    waiting = cents(payload.get("waiting_cents"), "Waiting on card or EBT")
    notes = str(payload.get("notes") or "").strip()
    photo = payload.get("totals_photo")
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO sales (
                sale_date, meat_cents, grocery_cents, not_in_balance_cents,
                waiting_cents, totals_photo, notes
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(sale_date) DO UPDATE SET
                meat_cents = excluded.meat_cents,
                grocery_cents = excluded.grocery_cents,
                not_in_balance_cents = excluded.not_in_balance_cents,
                waiting_cents = excluded.waiting_cents,
                totals_photo = COALESCE(excluded.totals_photo, sales.totals_photo),
                notes = excluded.notes
            """,
            (sale_date, meat, grocery, extra, waiting, photo, notes),
        )
        return _sale_public(conn, sale_date)


def clear_waiting(sale_date: str, add_to_balance: bool) -> dict:
    day = parse_day(sale_date).isoformat()
    with connect() as conn:
        row = conn.execute("SELECT * FROM sales WHERE sale_date = ?", (day,)).fetchone()
        if row is None:
            raise ValueError("There is no sales entry for that day.")
        extra = row["not_in_balance_cents"]
        if add_to_balance:
            extra += row["waiting_cents"]
        conn.execute(
            "UPDATE sales SET waiting_cents = 0, not_in_balance_cents = ? WHERE sale_date = ?",
            (extra, day),
        )
        return _sale_public(conn, day)


def _sale_public(conn, sale_date: str) -> dict:
    row = conn.execute("SELECT * FROM sales WHERE sale_date = ?", (sale_date,)).fetchone()
    items = conn.execute(
        """
        SELECT s.*, p.name AS product_name
        FROM sale_items s
        LEFT JOIN products p ON p.id = s.product_id
        WHERE s.sale_date = ?
        ORDER BY s.id
        """,
        (sale_date,),
    ).fetchall()
    if row is None and not items:
        return {
            "sale_date": sale_date,
            "meat_cents": 0,
            "grocery_cents": 0,
            "not_in_balance_cents": 0,
            "waiting_cents": 0,
            "notes": "",
            "totals_photo": None,
            "items_photo": None,
            "items": [],
        }
    return {
        "sale_date": sale_date,
        "meat_cents": row["meat_cents"] if row else 0,
        "grocery_cents": row["grocery_cents"] if row else 0,
        "not_in_balance_cents": row["not_in_balance_cents"] if row else 0,
        "waiting_cents": row["waiting_cents"] if row else 0,
        "notes": (row["notes"] or "") if row else "",
        "totals_photo": row["totals_photo"] if row else None,
        "items_photo": row["items_photo"] if row else None,
        "items": [
            {
                "id": item["id"],
                "product_id": item["product_id"],
                "name": item["raw_name"],
                "product_name": item["product_name"],
                "qty": item["qty"],
                "price_cents": item["price_cents"],
            }
            for item in items
        ],
    }


def get_sale(sale_date: str) -> dict:
    day = parse_day(sale_date).isoformat()
    with connect() as conn:
        return _sale_public(conn, day)


def list_sales(start: str, end: str) -> list[dict]:
    start_day = parse_day(start).isoformat()
    end_day = parse_day(end).isoformat()
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT sale_date FROM sales
            WHERE sale_date >= ? AND sale_date <= ?
            UNION
            SELECT sale_date FROM sale_items
            WHERE sale_date >= ? AND sale_date <= ?
            ORDER BY sale_date
            """,
            (start_day, end_day, start_day, end_day),
        ).fetchall()
        return [_sale_public(conn, row["sale_date"]) for row in rows]


def save_sale_items(payload: dict) -> dict:
    sale_date = parse_day(payload.get("sale_date") or date.today().isoformat()).isoformat()
    photo = payload.get("items_photo")
    raw_items = payload.get("items") or []
    cleaned = []
    for raw in raw_items:
        name = " ".join(str(raw.get("name") or "").split())
        if not name:
            continue
        qty = quantity(raw.get("qty", raw.get("quantity", 0)))
        if qty <= 0:
            raise ValueError(f"Enter a quantity for {name}.")
        price = raw.get("price_cents")
        price_cents = None if price in (None, "") else cents(price, "Price")
        cleaned.append(
            {
                "name": name,
                "qty": qty,
                "price_cents": price_cents,
                "product_id": raw.get("product_id"),
                "category": raw.get("category") if raw.get("category") in LINE_CATEGORIES else "grocery",
            }
        )
    with connect() as conn:
        conn.execute("DELETE FROM stock_moves WHERE source_key = ?", (f"sale:{sale_date}",))
        conn.execute("DELETE FROM sale_items WHERE sale_date = ?", (sale_date,))
        conn.execute(
            """
            INSERT INTO sales (sale_date, meat_cents, grocery_cents, not_in_balance_cents, waiting_cents, items_photo)
            VALUES (?, 0, 0, 0, 0, ?)
            ON CONFLICT(sale_date) DO UPDATE SET
                items_photo = COALESCE(excluded.items_photo, sales.items_photo)
            """,
            (sale_date, photo),
        )
        for item in cleaned:
            product_id = _ensure_product(
                conn,
                item["name"],
                item["category"],
                item.get("product_id"),
                track=True,
            )
            conn.execute(
                """
                INSERT INTO sale_items (sale_date, product_id, raw_name, qty, price_cents)
                VALUES (?, ?, ?, ?, ?)
                """,
                (sale_date, product_id, item["name"], item["qty"], item["price_cents"]),
            )
            cost = conn.execute(
                "SELECT last_cost_cents FROM products WHERE id = ?", (product_id,)
            ).fetchone()["last_cost_cents"]
            conn.execute(
                """
                INSERT INTO stock_moves (
                    product_id, moved_on, kind, qty, unit_cost_cents, sale_price_cents, source_key, note
                ) VALUES (?, ?, 'sold', ?, ?, ?, ?, 'Sold through the register')
                """,
                (product_id, sale_date, item["qty"], cost, item["price_cents"], f"sale:{sale_date}"),
            )
        return _sale_public(conn, sale_date)


def figures(opening: int, extra: int, reserves: list[int], purchases: int, pocket: int) -> dict:
    available = opening + extra
    reserved = sum(reserves)
    left = available - reserved
    unassigned = left - purchases - pocket
    return {
        "available_cents": available,
        "reserved_cents": reserved,
        "left_cents": left,
        "unassigned_cents": unassigned,
    }


def _suggested(conn, week_start: date) -> tuple[dict[str, int], list[dict]]:
    horizon = week_start + timedelta(days=13)
    suggestions = {row["key"]: 0 for row in conn.execute("SELECT key FROM categories")}
    due_soon = []
    for invoice in conn.execute("SELECT * FROM invoices"):
        owed, paid, lines = _invoice_totals(conn, invoice["id"])
        unpaid = max(0, owed - paid)
        if unpaid <= 0:
            continue
        due = invoice["due_date"]
        if due and parse_day(due) > horizon:
            continue
        weights: dict[str, int] = {}
        for line in lines:
            weights[line["category"]] = weights.get(line["category"], 0) + line["line_total_cents"]
        if not weights:
            weights[invoice["category"] or "grocery"] = unpaid
        for key, share in _allocate(unpaid, weights).items():
            if key not in suggestions:
                key = "grocery"
            suggestions[key] = suggestions.get(key, 0) + share
        due_soon.append(
            {
                "kind": "invoice",
                "name": invoice["supplier"],
                "detail": "Invoice",
                "due": due,
                "cents": unpaid,
            }
        )
    for bill in conn.execute("SELECT * FROM bills WHERE active = 1"):
        if parse_day(bill["next_due"]) > horizon:
            continue
        remaining = max(0, bill["amount_cents"] - bill["paid_this_period_cents"])
        if remaining <= 0:
            continue
        key = bill["category_key"] if bill["category_key"] in suggestions else "other"
        suggestions[key] += remaining
        due_soon.append(
            {
                "kind": "bill",
                "name": bill["name"],
                "detail": "Bill",
                "due": bill["next_due"],
                "cents": remaining,
            }
        )
    for loan in conn.execute("SELECT * FROM loans"):
        paid = conn.execute(
            "SELECT COALESCE(SUM(amount_cents), 0) AS paid FROM loan_payments WHERE loan_id = ?",
            (loan["id"],),
        ).fetchone()["paid"]
        balance = max(0, loan["starting_cents"] - paid)
        if balance <= 0 or not loan["regular_cents"]:
            continue
        if loan["next_due"] and parse_day(loan["next_due"]) > horizon:
            continue
        share = min(loan["regular_cents"], balance)
        suggestions["debt"] += share
        due_soon.append(
            {
                "kind": "loan",
                "name": loan["name"],
                "detail": "Loan payment",
                "due": loan["next_due"],
                "cents": share,
            }
        )
    last = conn.execute(
        """
        SELECT r.amount_cents
        FROM week_reserves r
        JOIN weeks w ON w.week_start = r.week_start
        WHERE r.category_key = 'cushion' AND w.saved_at IS NOT NULL
        ORDER BY r.week_start DESC
        LIMIT 1
        """
    ).fetchone()
    if last:
        suggestions["cushion"] = last["amount_cents"]
    due_soon.sort(key=lambda item: (item["due"] or "9999", item["name"]))
    return suggestions, due_soon


def _sales_between(conn, start: date, end: date) -> dict:
    row = conn.execute(
        """
        SELECT
            COALESCE(SUM(meat_cents), 0) AS meat,
            COALESCE(SUM(grocery_cents), 0) AS grocery,
            COALESCE(SUM(not_in_balance_cents), 0) AS extra,
            COALESCE(SUM(waiting_cents), 0) AS waiting,
            COUNT(*) AS days
        FROM sales
        WHERE sale_date >= ? AND sale_date <= ?
        """,
        (start.isoformat(), end.isoformat()),
    ).fetchone()
    return {
        "meat_cents": int(row["meat"]),
        "grocery_cents": int(row["grocery"]),
        "not_in_balance_cents": int(row["extra"]),
        "waiting_cents": int(row["waiting"]),
        "days": int(row["days"]),
    }


def _is_empty(conn) -> bool:
    checks = [
        "SELECT 1 FROM sales LIMIT 1",
        "SELECT 1 FROM invoices LIMIT 1",
        "SELECT 1 FROM loans LIMIT 1",
        "SELECT 1 FROM bills LIMIT 1",
        "SELECT 1 FROM weeks WHERE saved_at IS NOT NULL LIMIT 1",
        "SELECT 1 FROM products LIMIT 1",
    ]
    return all(conn.execute(sql).fetchone() is None for sql in checks)


def get_week(start: str | None = None) -> dict:
    week_start = monday_of(parse_day(start) if start else date.today())
    week_end = week_start + timedelta(days=6)
    with connect() as conn:
        saved = conn.execute("SELECT * FROM weeks WHERE week_start = ?", (week_start.isoformat(),)).fetchone()
        suggestions, due_soon = _suggested(conn, week_start)
        stored = {
            row["category_key"]: row["amount_cents"]
            for row in conn.execute(
                "SELECT * FROM week_reserves WHERE week_start = ?", (week_start.isoformat(),)
            )
        }
        opening = saved["opening_cents"] if saved else 0
        opening_note = ""
        if saved is None or saved["saved_at"] is None:
            previous = week_start - timedelta(days=7)
            prev = conn.execute("SELECT * FROM weeks WHERE week_start = ?", (previous.isoformat(),)).fetchone()
            if prev and prev["saved_at"]:
                prev_reserves = [
                    row["amount_cents"]
                    for row in conn.execute(
                        "SELECT amount_cents FROM week_reserves WHERE week_start = ?",
                        (previous.isoformat(),),
                    )
                ]
                prev_sales = _sales_between(conn, previous, previous + timedelta(days=6))
                prev_math = figures(
                    prev["opening_cents"],
                    prev_sales["not_in_balance_cents"],
                    prev_reserves,
                    prev["purchases_cents"],
                    prev["pocket_cents"],
                )
                opening = max(0, prev_math["unassigned_cents"])
                opening_note = "Suggested from last week. If your cash and bank balance is different, type that instead."
        sales = _sales_between(conn, week_start, week_end)
        reserves = []
        for category in _categories(conn):
            amount = stored.get(category["key"], suggestions.get(category["key"], 0))
            if saved is None or saved["saved_at"] is None:
                amount = suggestions.get(category["key"], 0)
            reserves.append(
                {
                    **category,
                    "suggested_cents": suggestions.get(category["key"], 0),
                    "amount_cents": amount,
                }
            )
        purchases = saved["purchases_cents"] if saved and saved["saved_at"] else 0
        pocket = saved["pocket_cents"] if saved and saved["saved_at"] else 0
        math = figures(opening, sales["not_in_balance_cents"], [item["amount_cents"] for item in reserves], purchases, pocket)
        if saved is None or saved["saved_at"] is None:
            pocket = max(0, math["left_cents"] - purchases)
            math = figures(opening, sales["not_in_balance_cents"], [item["amount_cents"] for item in reserves], purchases, pocket)
        saved_flag = bool(saved and saved["saved_at"])
        overdue = _has_due_unpaid(conn, week_end)
        checklist = [
            {"label": "Record meat sales and grocery sales separately.", "done": sales["days"] > 0},
            {
                "label": "Keep one invoice list with who is owed, the balance, and the payment date.",
                "done": _invoice_list_ready(conn),
            },
            {"label": "Set aside pay, bills, and taxes before new orders.", "done": saved_flag},
            {"label": "Mark payments so the unpaid balance drops.", "done": not overdue},
            {"label": "Look at what is due in the next two weeks.", "done": saved_flag},
            {
                "label": "Use only money that is not set aside for new purchases.",
                "done": saved_flag and math["left_cents"] >= 0 and math["unassigned_cents"] >= 0,
            },
        ]
        return {
            "week_start": week_start.isoformat(),
            "week_end": week_end.isoformat(),
            "label": f"Week of {pretty(week_start)}",
            "saved": saved_flag,
            "opening_cents": opening,
            "opening_note": opening_note,
            "purchases_cents": purchases,
            "pocket_cents": pocket,
            "notes": (saved["notes"] or "") if saved else "",
            "sales": sales,
            "reserves": reserves,
            "due_soon": due_soon,
            "still_owed_cents": still_owed_cents(conn),
            "stock_value_cents": stock_value_cents(conn),
            "checklist": checklist,
            "empty": _is_empty(conn),
            "math": math,
        }


def _invoice_list_ready(conn) -> bool:
    invoices = conn.execute("SELECT due_date FROM invoices").fetchall()
    loans = conn.execute("SELECT id FROM loans").fetchall()
    if not invoices and not loans:
        return False
    return all(row["due_date"] for row in invoices)


def _has_due_unpaid(conn, through: date) -> bool:
    for invoice in conn.execute("SELECT * FROM invoices"):
        owed, paid, _lines = _invoice_totals(conn, invoice["id"])
        if owed - paid <= 0:
            continue
        if not invoice["due_date"] or parse_day(invoice["due_date"]) <= through:
            return True
    for bill in conn.execute("SELECT * FROM bills WHERE active = 1"):
        if parse_day(bill["next_due"]) <= through and bill["amount_cents"] > bill["paid_this_period_cents"]:
            return True
    for loan in conn.execute("SELECT * FROM loans"):
        paid = conn.execute(
            "SELECT COALESCE(SUM(amount_cents), 0) AS paid FROM loan_payments WHERE loan_id = ?",
            (loan["id"],),
        ).fetchone()["paid"]
        if loan["starting_cents"] - paid <= 0 or not loan["regular_cents"]:
            continue
        if not loan["next_due"] or parse_day(loan["next_due"]) <= through:
            return True
    return False


def save_week(payload: dict) -> dict:
    week_start = monday_of(parse_day(payload.get("week_start") or date.today().isoformat()))
    opening = cents(payload.get("opening_cents"), "Cash and bank balance")
    purchases = cents(payload.get("purchases_cents"), "New purchases")
    pocket = cents(payload.get("pocket_cents"), "Left for him")
    notes = str(payload.get("notes") or "").strip()
    incoming = payload.get("reserves") or {}
    with connect() as conn:
        known = {row["key"] for row in conn.execute("SELECT key FROM categories")}
        conn.execute(
            """
            INSERT INTO weeks (week_start, opening_cents, purchases_cents, pocket_cents, notes, saved_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(week_start) DO UPDATE SET
                opening_cents = excluded.opening_cents,
                purchases_cents = excluded.purchases_cents,
                pocket_cents = excluded.pocket_cents,
                notes = excluded.notes,
                saved_at = excluded.saved_at
            """,
            (week_start.isoformat(), opening, purchases, pocket, notes, now_iso()),
        )
        conn.execute("DELETE FROM week_reserves WHERE week_start = ?", (week_start.isoformat(),))
        for key in known:
            amount = cents(incoming.get(key, 0), "Set aside")
            conn.execute(
                "INSERT INTO week_reserves (week_start, category_key, amount_cents) VALUES (?, ?, ?)",
                (week_start.isoformat(), key, amount),
            )
    return get_week(week_start.isoformat())


def period_bounds(grain: str, day_text: str) -> tuple[date, date]:
    day = parse_day(day_text)
    if grain == "day":
        return day, day
    if grain == "week":
        start = monday_of(day)
        return start, start + timedelta(days=6)
    if grain == "month":
        start = day.replace(day=1)
        if day.month == 12:
            end = date(day.year, 12, 31)
        else:
            end = date(day.year, day.month + 1, 1) - timedelta(days=1)
        return start, end
    if grain == "year":
        return date(day.year, 1, 1), date(day.year, 12, 31)
    raise ValueError("Choose a day, week, month, or year.")


def _period_label(grain: str, start: date, end: date) -> str:
    if grain == "day":
        return f"{MONTHS_LONG[start.month - 1]} {start.day}, {start.year}"
    if grain == "week":
        return f"Week of {pretty(start)}"
    if grain == "month":
        return pretty_long_month(start)
    return str(start.year)


def report(grain: str, day_text: str) -> dict:
    start, end = period_bounds(grain, day_text)
    with connect() as conn:
        sales = _sales_between(conn, start, end)
        goods = {"meat": 0, "grocery": 0, "produce": 0}
        products: dict[int, dict] = {}
        for invoice in conn.execute(
            "SELECT * FROM invoices WHERE invoice_date >= ? AND invoice_date <= ?",
            (start.isoformat(), end.isoformat()),
        ):
            _owed, _paid, lines = _invoice_totals(conn, invoice["id"])
            weights = {}
            for line in lines:
                weights[line["category"]] = weights.get(line["category"], 0) + line["line_total_cents"]
                if line["product_id"]:
                    slot = products.setdefault(
                        line["product_id"],
                        {"id": line["product_id"], "name": line["item_name"], "cents": 0},
                    )
                    slot["name"] = line["item_name"]
                    slot["cents"] += line["line_total_cents"]
            if invoice["tax_other_cents"] and weights:
                for key, share in _allocate(invoice["tax_other_cents"], weights).items():
                    if key in goods:
                        goods[key] += share
            elif invoice["tax_other_cents"] and not lines:
                bucket = invoice["category"] if invoice["category"] in goods else "grocery"
                goods[bucket] += invoice["tax_other_cents"]
            for key, amount in weights.items():
                if key in goods:
                    goods[key] += amount
            if not lines and invoice["printed_total_cents"]:
                bucket = invoice["category"] if invoice["category"] in goods else "grocery"
                goods[bucket] += invoice["printed_total_cents"]
        product_rows = sorted(products.values(), key=lambda item: item["cents"], reverse=True)
        bills = []
        for payment in conn.execute(
            """
            SELECT p.amount_cents, p.due_on, b.name, b.category_key
            FROM bill_payments p
            JOIN bills b ON b.id = p.bill_id
            WHERE p.due_on >= ? AND p.due_on <= ?
            """,
            (start.isoformat(), end.isoformat()),
        ):
            bills.append(
                {
                    "name": payment["name"],
                    "category_key": payment["category_key"],
                    "due": payment["due_on"],
                    "cents": payment["amount_cents"],
                    "status": "paid",
                }
            )
        for bill in conn.execute("SELECT * FROM bills WHERE active = 1"):
            if start.isoformat() <= bill["next_due"] <= end.isoformat():
                remaining = max(0, bill["amount_cents"] - bill["paid_this_period_cents"])
                if remaining:
                    bills.append(
                        {
                            "name": bill["name"],
                            "category_key": bill["category_key"],
                            "due": bill["next_due"],
                            "cents": remaining,
                            "status": "still to pay",
                        }
                    )
        debt = conn.execute(
            """
            SELECT COALESCE(SUM(amount_cents), 0) AS paid
            FROM loan_payments
            WHERE paid_on >= ? AND paid_on <= ?
            """,
            (start.isoformat(), end.isoformat()),
        ).fetchone()["paid"]
        spoilage = []
        for move in conn.execute(
            """
            SELECT m.*, p.name
            FROM stock_moves m
            JOIN products p ON p.id = m.product_id
            WHERE m.kind = 'spoiled' AND m.moved_on >= ? AND m.moved_on <= ?
            ORDER BY m.moved_on, p.name
            """,
            (start.isoformat(), end.isoformat()),
        ):
            cost = int(round(move["qty"] * (move["unit_cost_cents"] or 0)))
            spoilage.append(
                {"name": move["name"], "qty": move["qty"], "cents": cost, "moved_on": move["moved_on"]}
            )
        return {
            "grain": grain,
            "start": start.isoformat(),
            "end": end.isoformat(),
            "label": _period_label(grain, start, end),
            "revenue": {
                "meat_cents": sales["meat_cents"],
                "grocery_cents": sales["grocery_cents"],
                "total_cents": sales["meat_cents"] + sales["grocery_cents"],
            },
            "goods": {
                "meat_cents": goods["meat"],
                "grocery_cents": goods["grocery"],
                "produce_cents": goods["produce"],
                "total_cents": goods["meat"] + goods["grocery"] + goods["produce"],
            },
            "products": product_rows,
            "bills": bills,
            "bill_total_cents": sum(item["cents"] for item in bills),
            "debt_payments_cents": int(debt),
            "spoilage": spoilage,
            "spoilage_cents": sum(item["cents"] for item in spoilage),
            "still_owed_cents": still_owed_cents(conn),
            "stock_value_cents": stock_value_cents(conn),
        }


def money_flow(day_text: str | None = None) -> dict:
    """Sales in, then every cost, then what is left. Unpaid invoices sit below. Loans are left out."""
    selected = parse_day(day_text or date.today().isoformat())
    summary = report("month", selected.isoformat())
    start = date.fromisoformat(summary["start"])
    end = date.fromisoformat(summary["end"])
    goods = {
        "meat": summary["goods"]["meat_cents"],
        "grocery": summary["goods"]["grocery_cents"],
        "produce": summary["goods"]["produce_cents"],
    }
    with connect() as conn:
        bill_names = {
            row["key"]: row["name"]
            for row in conn.execute(
                """
                SELECT key, name FROM categories
                WHERE key NOT IN ('meat', 'grocery', 'produce', 'debt', 'cushion')
                ORDER BY sort_order, name
                """
            )
        }
        bill_totals = {key: 0 for key in bill_names}
        for bill in conn.execute(
            "SELECT category_key, amount_cents FROM bills WHERE next_due >= ? AND next_due <= ?",
            (start.isoformat(), end.isoformat()),
        ):
            key = bill["category_key"] if bill["category_key"] in bill_totals else "other"
            if key not in bill_totals:
                bill_totals[key] = 0
                bill_names[key] = key
            bill_totals[key] += int(bill["amount_cents"])
        debts = []
        for row in conn.execute("SELECT id FROM invoices ORDER BY COALESCE(due_date, invoice_date), id"):
            owed, paid, _lines = _invoice_totals(conn, row["id"])
            unpaid = max(0, owed - paid)
            if unpaid <= 0:
                continue
            invoice = conn.execute(
                "SELECT supplier, due_date, invoice_date FROM invoices WHERE id = ?",
                (row["id"],),
            ).fetchone()
            debts.append(
                {
                    "id": row["id"],
                    "name": invoice["supplier"],
                    "due": invoice["due_date"] or invoice["invoice_date"],
                    "cents": unpaid,
                }
            )
    costs = [
        {"name": "Meat bought", "cents": goods["meat"]},
        {"name": "Grocery bought", "cents": goods["grocery"]},
        {"name": "Produce bought", "cents": goods["produce"]},
    ]
    costs.extend({"name": bill_names[key], "cents": bill_totals[key]} for key in bill_names)
    sales_total = summary["revenue"]["total_cents"]
    cost_total = sum(item["cents"] for item in costs)
    return {
        "month_label": pretty_long_month(selected),
        "selected": selected.isoformat(),
        "sales": [
            {"name": "Meat sold", "cents": summary["revenue"]["meat_cents"]},
            {"name": "Grocery sold", "cents": summary["revenue"]["grocery_cents"]},
        ],
        "sales_total": sales_total,
        "costs": costs,
        "cost_total": cost_total,
        "left": sales_total - cost_total,
        "debts": debts,
        "debt_total": sum(item["cents"] for item in debts),
    }


def dashboard(day_text: str | None = None) -> dict:
    selected = parse_day(day_text or date.today().isoformat())
    start = selected.replace(day=1)
    end = selected.replace(day=calendar.monthrange(selected.year, selected.month)[1])
    with connect() as conn:
        sales_rows = {
            row["sale_date"]: row
            for row in conn.execute(
                "SELECT * FROM sales WHERE sale_date >= ? AND sale_date <= ?",
                (start.isoformat(), end.isoformat()),
            )
        }
        invoices_by_day: dict[str, list[dict]] = {}
        for row in conn.execute(
            """
            SELECT id FROM invoices
            WHERE invoice_date >= ? AND invoice_date <= ?
            ORDER BY invoice_date, id
            """,
            (start.isoformat(), end.isoformat()),
        ):
            invoice = _invoice_public(conn, row["id"])
            invoices_by_day.setdefault(invoice["invoice_date"], []).append(invoice)
        days = []
        month_revenue = 0
        month_cost = 0
        cursor = start
        while cursor <= end:
            key = cursor.isoformat()
            sale = sales_rows.get(key)
            meat = int(sale["meat_cents"]) if sale else 0
            grocery = int(sale["grocery_cents"]) if sale else 0
            revenue = meat + grocery
            invoices = invoices_by_day.get(key, [])
            cost = sum(item["owed_cents"] for item in invoices)
            month_revenue += revenue
            month_cost += cost
            days.append(
                {
                    "date": key,
                    "day": cursor.day,
                    "weekday": cursor.weekday(),
                    "revenue_cents": revenue,
                    "cost_cents": cost,
                    "meat_cents": meat,
                    "grocery_cents": grocery,
                    "invoices": invoices,
                }
            )
            cursor += timedelta(days=1)
        levels = _levels(conn)
        stock = [
            _product_public(row, levels.get(row["id"], 0.0))
            for row in conn.execute("SELECT * FROM products WHERE track = 1 ORDER BY name")
        ]
        return {
            "today": date.today().isoformat(),
            "selected": selected.isoformat(),
            "month_label": pretty_long_month(selected),
            "month_start": start.isoformat(),
            "revenue_cents": month_revenue,
            "cost_cents": month_cost,
            "lead": start.weekday(),
            "days": days,
            "stock": stock,
        }


def bootstrap() -> dict:
    with connect() as conn:
        categories = _categories(conn)
        return {
            "store": "Super Hayat Market",
            "today": date.today().isoformat(),
            "week_start": monday_of(date.today()).isoformat(),
            "categories": categories,
            "bill_categories": [item for item in categories if item["key"] not in BILL_SKIP],
            "empty": _is_empty(conn),
            "still_owed_cents": still_owed_cents(conn),
            "stock_value_cents": stock_value_cents(conn),
        }


def backup_database() -> dict:
    BACKUPS.mkdir(parents=True, exist_ok=True)
    stamp = date.today().isoformat()
    target = BACKUPS / f"Super Hayat {stamp}.db"
    number = 2
    while target.exists():
        target = BACKUPS / f"Super Hayat {stamp} {number}.db"
        number += 1
    with _LOCK:
        source = sqlite3.connect(DB_PATH)
        try:
            copy = sqlite3.connect(target)
            try:
                source.backup(copy)
            finally:
                copy.close()
        finally:
            source.close()
    today = date.today()
    return {"message": f"Copy saved, {pretty(today)}.", "file": target.name}

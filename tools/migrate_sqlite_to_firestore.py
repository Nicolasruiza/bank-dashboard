"""
One-time migration: SQLite → Firestore.
Run once after dropping firebase-credentials.json in the project root.

    python3 tools/migrate_sqlite_to_firestore.py
"""
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "bank_statements.db")


def _sqlite_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _set_counter(db, collection, value):
    db.collection("_counters").document(collection).set({"value": value})


def migrate():
    from tools.firestore_client import get_db
    db = get_db()
    conn = _sqlite_conn()

    # ── statements ──────────────────────────────────────────────────────────────
    print("Migrating statements…")
    rows = conn.execute("SELECT * FROM statements").fetchall()
    max_id = 0
    for row in rows:
        d = dict(row)
        doc_id = str(d["id"])
        max_id = max(max_id, d["id"])
        db.collection("statements").document(doc_id).set(d)
    _set_counter(db, "statements", max_id)
    print(f"  {len(rows)} statements migrated")

    # ── transactions ─────────────────────────────────────────────────────────────
    print("Migrating transactions (with denormalized fields)…")
    # Build statement lookup for denormalization
    stmts = {str(r["id"]): dict(r) for r in conn.execute("SELECT * FROM statements").fetchall()}
    rows = conn.execute("SELECT * FROM transactions").fetchall()
    max_id = 0
    batch = db.batch()
    count = 0
    for row in rows:
        d = dict(row)
        max_id = max(max_id, d["id"])
        stmt = stmts.get(str(d["statement_id"]), {})
        # Denormalize: add fields used in queries to avoid JOINs
        d["month"] = d["date"][:7] if d.get("date") else None
        d["account_type"] = stmt.get("account_type", "personal")
        d["account_suffix"] = stmt.get("account_suffix")
        d["account_subtype"] = stmt.get("account_subtype")
        batch.set(db.collection("transactions").document(str(d["id"])), d)
        count += 1
        if count % 400 == 0:
            batch.commit()
            batch = db.batch()
            print(f"  …{count} transactions written")
    batch.commit()
    _set_counter(db, "transactions", max_id)
    print(f"  {count} transactions migrated")

    # ── categories ───────────────────────────────────────────────────────────────
    print("Migrating categories…")
    rows = conn.execute("SELECT * FROM categories").fetchall()
    for row in rows:
        d = dict(row)
        db.collection("categories").document(d["name"]).set({"name": d["name"], "type": d["type"]})
    print(f"  {len(rows)} categories migrated")

    # ── investment_accounts ──────────────────────────────────────────────────────
    print("Migrating investment_accounts…")
    rows = conn.execute("SELECT * FROM investment_accounts").fetchall()
    max_id = 0
    for row in rows:
        d = dict(row)
        max_id = max(max_id, d["id"])
        db.collection("investment_accounts").document(str(d["id"])).set(d)
    _set_counter(db, "investment_accounts", max_id)
    print(f"  {len(rows)} investment_accounts migrated")

    # ── portfolio_holdings ───────────────────────────────────────────────────────
    print("Migrating portfolio_holdings…")
    rows = conn.execute("SELECT * FROM portfolio_holdings").fetchall()
    max_id = 0
    for row in rows:
        d = dict(row)
        max_id = max(max_id, d["id"])
        db.collection("portfolio_holdings").document(str(d["id"])).set(d)
    _set_counter(db, "portfolio_holdings", max_id)
    print(f"  {len(rows)} holdings migrated")

    # ── portfolio_dividends ──────────────────────────────────────────────────────
    print("Migrating portfolio_dividends…")
    rows = conn.execute("SELECT * FROM portfolio_dividends").fetchall()
    max_id = 0
    for row in rows:
        d = dict(row)
        max_id = max(max_id, d["id"])
        db.collection("portfolio_dividends").document(str(d["id"])).set(d)
    _set_counter(db, "portfolio_dividends", max_id)
    print(f"  {len(rows)} dividends migrated")

    # ── budget_targets ───────────────────────────────────────────────────────────
    print("Migrating budget_targets…")
    rows = conn.execute("SELECT * FROM budget_targets").fetchall()
    for row in rows:
        d = dict(row)
        db.collection("budget_targets").document(d["category"]).set(d)
    print(f"  {len(rows)} budget_targets migrated")

    # ── vendor_flags ─────────────────────────────────────────────────────────────
    print("Migrating vendor_flags…")
    rows = conn.execute("SELECT * FROM vendor_flags").fetchall()
    for row in rows:
        d = dict(row)
        doc_id = d["description"].replace("/", "__slash__")
        db.collection("vendor_flags").document(doc_id).set(d)
    print(f"  {len(rows)} vendor_flags migrated")

    conn.close()
    print("\nMigration complete.")


if __name__ == "__main__":
    migrate()

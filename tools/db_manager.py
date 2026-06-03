"""
Database layer — Firestore backend.
All function signatures are identical to the original SQLite version so no callers change.
"""
from datetime import datetime
from typing import Optional, Dict, Any
from google.cloud import firestore as firestore_lib
from tools.firestore_client import get_db

EXPENSE_CATEGORIES = [
    "meals", "groceries", "rent", "utilities", "subscriptions", "transport",
    "healthcare", "education", "entertainment", "fees", "insurance", "shopping",
    "transfers_out", "cc_payment", "office_expenses", "telecom", "taxes", "payroll",
    "professional_services", "bank_charges", "travel", "home_improvement",
    "investments", "other_expense"
]

INCOME_CATEGORIES = [
    "salary", "dividends", "transfers_in", "interest", "refunds",
    "cc_payment_received", "other_income"
]

INTERNAL_CATEGORIES = ["transfers_out", "transfers_in", "cc_payment", "cc_payment_received"]


# ── Auto-increment helpers ─────────────────────────────────────────────────────

def _next_id(collection_name: str) -> int:
    """Atomically increment and return the next integer ID for a collection."""
    db = get_db()
    counter_ref = db.collection("_counters").document(collection_name)
    transaction = db.transaction()

    @firestore_lib.transactional
    def _run(txn, ref):
        snap = ref.get(transaction=txn)
        val = snap.to_dict().get("value", 0) if snap.exists else 0
        new_val = val + 1
        txn.set(ref, {"value": new_val})
        return new_val

    return _run(transaction, counter_ref)


def _doc(collection: str, doc_id) -> Optional[Dict[str, Any]]:
    snap = get_db().collection(collection).document(str(doc_id)).get()
    if not snap.exists:
        return None
    d = snap.to_dict()
    d.setdefault("id", int(snap.id) if snap.id.isdigit() else snap.id)
    return d


def _collection_list(collection: str) -> list:
    docs = get_db().collection(collection).stream()
    result = []
    for snap in docs:
        d = snap.to_dict()
        d.setdefault("id", int(snap.id) if snap.id.isdigit() else snap.id)
        result.append(d)
    return result


# ── Init (seed categories) ─────────────────────────────────────────────────────

def init_db():
    """Seed categories into Firestore. Safe to call repeatedly."""
    db = get_db()
    for name in EXPENSE_CATEGORIES:
        db.collection("categories").document(name).set({"name": name, "type": "expense"}, merge=True)
    for name in INCOME_CATEGORIES:
        db.collection("categories").document(name).set({"name": name, "type": "income"}, merge=True)


# ── Statements ────────────────────────────────────────────────────────────────

def create_statement(filename: str, bank_name: str = None, account_type: str = "personal",
                     account_subtype: str = None) -> int:
    db = get_db()
    new_id = _next_id("statements")
    db.collection("statements").document(str(new_id)).set({
        "id": new_id,
        "filename": filename,
        "bank_name": bank_name,
        "uploaded_at": datetime.utcnow().isoformat(),
        "month": None,
        "status": "pending",
        "account_type": account_type,
        "account_subtype": account_subtype,
        "account_suffix": None,
    })
    return new_id


def update_statement_status(statement_id: int, status: str, month: str = None):
    ref = get_db().collection("statements").document(str(statement_id))
    update = {"status": status}
    if month is not None:
        update["month"] = month
    ref.update(update)


def update_statement_account_type(statement_id: int, account_type: str):
    get_db().collection("statements").document(str(statement_id)).update({"account_type": account_type})
    # Keep denormalized fields on transactions in sync
    _sync_transactions_from_statement(statement_id, {"account_type": account_type})


def update_statement_account_subtype(statement_id: int, subtype: Optional[str]):
    get_db().collection("statements").document(str(statement_id)).update({"account_subtype": subtype})
    _sync_transactions_from_statement(statement_id, {"account_subtype": subtype})


def update_statement_account_suffix(statement_id: int, suffix: Optional[str]):
    get_db().collection("statements").document(str(statement_id)).update({"account_suffix": suffix})
    _sync_transactions_from_statement(statement_id, {"account_suffix": suffix})


def _sync_transactions_from_statement(statement_id: int, fields: dict):
    """Propagate statement field changes to denormalized transaction docs."""
    db = get_db()
    txs = db.collection("transactions").where("statement_id", "==", statement_id).stream()
    batch = db.batch()
    count = 0
    for snap in txs:
        batch.update(snap.reference, fields)
        count += 1
        if count % 400 == 0:
            batch.commit()
            batch = db.batch()
    if count % 400 != 0:
        batch.commit()


def find_statement(account_suffix: str, month: str, account_type: str) -> Optional[dict]:
    if not account_suffix or not month:
        return None
    docs = (get_db().collection("statements")
            .where("account_suffix", "==", account_suffix)
            .where("month", "==", month)
            .where("account_type", "==", account_type)
            .limit(1)
            .stream())
    for snap in docs:
        d = snap.to_dict()
        d.setdefault("id", int(snap.id))
        return d
    return None


def get_all_statements(account_type: str = None) -> list:
    db = get_db()
    query = db.collection("statements")
    if account_type:
        query = query.where("account_type", "==", account_type)
    docs = []
    for snap in query.stream():
        d = snap.to_dict()
        d.setdefault("id", int(snap.id) if snap.id.isdigit() else snap.id)
        docs.append(d)
    return sorted(docs, key=lambda x: x.get("uploaded_at", ""), reverse=True)


def delete_statement(statement_id: int):
    db = get_db()
    # Delete all transactions for this statement
    txs = db.collection("transactions").where("statement_id", "==", statement_id).stream()
    batch = db.batch()
    count = 0
    for snap in txs:
        batch.delete(snap.reference)
        count += 1
        if count % 400 == 0:
            batch.commit()
            batch = db.batch()
    batch.commit()
    db.collection("statements").document(str(statement_id)).delete()


def get_account_suffixes(account_type: str = "personal") -> list:
    docs = (get_db().collection("statements")
            .where("account_type", "==", account_type)
            .stream())
    seen = {}
    for snap in docs:
        d = snap.to_dict()
        suffix = d.get("account_suffix")
        if suffix and suffix not in seen:
            seen[suffix] = d.get("account_subtype")
    return [{"suffix": s, "subtype": t} for s, t in sorted(seen.items())]


# ── Transactions ──────────────────────────────────────────────────────────────

def get_manual_overrides(statement_id: int) -> dict:
    docs = (get_db().collection("transactions")
            .where("statement_id", "==", statement_id)
            .where("category_source", "==", "manual")
            .stream())
    return {snap.to_dict()["description"]: snap.to_dict()["category"] for snap in docs}


def get_global_manual_corrections(limit: int = 30) -> list:
    docs = (get_db().collection("transactions")
            .where("category_source", "==", "manual")
            .stream())
    counts: dict = {}
    for snap in docs:
        d = snap.to_dict()
        key = (d.get("description", ""), d.get("category", ""))
        counts[key] = counts.get(key, 0) + 1
    sorted_items = sorted(counts.items(), key=lambda x: x[1], reverse=True)[:limit]
    return [{"description": k[0], "category": k[1], "count": v} for k, v in sorted_items]


def insert_transactions(statement_id: int, transactions: list, manual_overrides: dict = None):
    manual_overrides = manual_overrides or {}
    db = get_db()

    # Get the statement's denormalized fields
    stmt_snap = db.collection("statements").document(str(statement_id)).get()
    stmt = stmt_snap.to_dict() if stmt_snap.exists else {}

    # Delete existing transactions for this statement
    existing = db.collection("transactions").where("statement_id", "==", statement_id).stream()
    batch = db.batch()
    count = 0
    for snap in existing:
        batch.delete(snap.reference)
        count += 1
        if count % 400 == 0:
            batch.commit()
            batch = db.batch()
    batch.commit()

    # Insert new transactions
    batch = db.batch()
    count = 0
    for tx in transactions:
        new_id = _next_id("transactions")
        desc = tx.get("description", "")
        category = manual_overrides.get(desc, tx.get("category"))
        source = "manual" if desc in manual_overrides else "ai"
        date_str = tx.get("date", "")
        doc = {
            "id": new_id,
            "statement_id": statement_id,
            "date": date_str,
            "description": desc,
            "amount": tx.get("amount", 0.0),
            "category": category,
            "category_source": source,
            "raw_text": tx.get("raw_text", ""),
            # Denormalized fields for query efficiency
            "month": date_str[:7] if date_str else None,
            "account_type": stmt.get("account_type", "personal"),
            "account_suffix": stmt.get("account_suffix"),
            "account_subtype": stmt.get("account_subtype"),
        }
        batch.set(db.collection("transactions").document(str(new_id)), doc)
        count += 1
        if count % 400 == 0:
            batch.commit()
            batch = db.batch()
    if count % 400 != 0:
        batch.commit()


def derive_statement_month(statement_id: int) -> Optional[str]:
    docs = (get_db().collection("transactions")
            .where("statement_id", "==", statement_id)
            .stream())
    month_counts: dict = {}
    for snap in docs:
        m = snap.to_dict().get("month")
        if m:
            month_counts[m] = month_counts.get(m, 0) + 1
    if not month_counts:
        return None
    return max(month_counts, key=month_counts.get)


def update_transaction_category(tx_id: int, category: str):
    get_db().collection("transactions").document(str(tx_id)).update({
        "category": category,
        "category_source": "manual",
    })


# ── Queries ───────────────────────────────────────────────────────────────────

def _fetch_transactions(month: str, account_type: str, account_suffix: Optional[str]) -> list:
    """Fetch all transaction dicts matching the given filters."""
    query = (get_db().collection("transactions")
             .where("month", "==", month)
             .where("account_type", "==", account_type))
    if account_suffix:
        query = query.where("account_suffix", "==", account_suffix)
    return [snap.to_dict() for snap in query.stream()]


def get_available_months(account_type: str = "personal", account_suffix: Optional[str] = None) -> list:
    query = (get_db().collection("statements")
             .where("status", "==", "done")
             .where("account_type", "==", account_type))
    if account_suffix:
        query = query.where("account_suffix", "==", account_suffix)
    months = sorted(
        {snap.to_dict().get("month") for snap in query.stream() if snap.to_dict().get("month")},
        reverse=True
    )
    return months


def get_pnl_by_month(month: str, account_type: str = "personal",
                     exclude_internal: bool = False, account_suffix: Optional[str] = None) -> dict:
    txs = _fetch_transactions(month, account_type, account_suffix)

    # Category type lookup
    cat_type = {snap.id: snap.to_dict().get("type", "expense")
                for snap in get_db().collection("categories").stream()}

    by_cat: dict = {}
    for tx in txs:
        cat = tx.get("category") or "other_expense"
        if cat not in by_cat:
            by_cat[cat] = {"total": 0.0, "count": 0}
        by_cat[cat]["total"] += tx.get("amount", 0.0)
        by_cat[cat]["count"] += 1

    by_category = []
    total_income = 0.0
    total_expense = 0.0

    for cat, agg in by_cat.items():
        total = round(agg["total"], 2)
        cat_t = cat_type.get(cat, "expense" if total < 0 else "income")
        by_category.append({"category": cat, "type": cat_t, "total": total, "count": agg["count"]})
        if total > 0:
            total_income += total
        else:
            total_expense += total

    net = round(sum(tx.get("amount", 0.0) for tx in txs), 2)
    result = {
        "month": month, "account_type": account_type,
        "total_income": round(total_income, 2),
        "total_expenses": round(total_expense, 2),
        "net": net,
        "by_category": sorted(by_category, key=lambda x: abs(x["total"]), reverse=True),
    }

    if exclude_internal:
        adj_income = sum(c["total"] for c in by_category
                        if c["type"] == "income" and c["category"] not in INTERNAL_CATEGORIES)
        adj_expense = sum(c["total"] for c in by_category
                         if c["type"] == "expense" and c["category"] not in INTERNAL_CATEGORIES)
        result["adjusted_income"] = round(adj_income, 2)
        result["adjusted_expenses"] = round(adj_expense, 2)
        result["adjusted_net"] = round(adj_income + adj_expense, 2)

    return result


def get_transactions_by_month(month: str, account_type: str = "personal",
                               account_suffix: Optional[str] = None) -> list:
    txs = _fetch_transactions(month, account_type, account_suffix)
    # Attach statement fields from the denormalized fields already on each tx
    return sorted(
        [{**tx, "id": tx.get("id")} for tx in txs],
        key=lambda x: x.get("date", ""), reverse=True
    )


def get_top_vendors(month: str, category: str, account_type: str = "personal",
                    limit: int = 15, account_suffix: Optional[str] = None) -> list:
    txs = _fetch_transactions(month, account_type, account_suffix)
    filtered = [tx for tx in txs if tx.get("category") == category]
    vendor_agg: dict = {}
    for tx in filtered:
        name = tx.get("description", "")
        if name not in vendor_agg:
            vendor_agg[name] = {"count": 0, "total": 0.0}
        vendor_agg[name]["count"] += 1
        vendor_agg[name]["total"] += tx.get("amount", 0.0)
    sorted_vendors = sorted(vendor_agg.items(), key=lambda x: abs(x[1]["total"]), reverse=True)[:limit]
    return [{"name": k, "count": v["count"], "total": round(v["total"], 2)} for k, v in sorted_vendors]


def get_insights_data(month: str, account_type: str = "personal",
                      account_suffix: Optional[str] = None) -> dict:
    transactions = get_transactions_by_month(month, account_type, account_suffix=account_suffix)
    pnl = get_pnl_by_month(month, account_type, account_suffix=account_suffix)

    year, mon = int(month[:4]), int(month[5:7])
    prior_month = f"{year - 1}-12" if mon == 1 else f"{year}-{str(mon - 1).zfill(2)}"
    available = get_available_months(account_type, account_suffix=account_suffix)
    prior_pnl = get_pnl_by_month(prior_month, account_type, account_suffix=account_suffix) if prior_month in available else None

    return {"transactions": transactions, "pnl": pnl, "prior_pnl": prior_pnl,
            "month": month, "account_type": account_type}


def get_category_trend(category: str, account_type: str = "personal",
                       account_suffix: Optional[str] = None,
                       account_subtype: Optional[str] = None) -> list:
    query = (get_db().collection("transactions")
             .where("category", "==", category)
             .where("account_type", "==", account_type))
    if account_suffix:
        query = query.where("account_suffix", "==", account_suffix)
    if account_subtype:
        query = query.where("account_subtype", "==", account_subtype)

    month_totals: dict = {}
    for snap in query.stream():
        d = snap.to_dict()
        m = d.get("month")
        if m:
            month_totals[m] = round(month_totals.get(m, 0.0) + d.get("amount", 0.0), 2)

    return [{"month": m, "total": t} for m, t in sorted(month_totals.items())]


# ── Categories ────────────────────────────────────────────────────────────────

def get_all_categories() -> list:
    docs = get_db().collection("categories").stream()
    cats = [snap.to_dict() for snap in docs]
    return sorted(cats, key=lambda x: (x.get("type", ""), x.get("name", "")))


def create_category(name: str, cat_type: str) -> dict:
    clean_name = name.lower().strip().replace(" ", "_")
    db = get_db()
    ref = db.collection("categories").document(clean_name)
    if ref.get().exists:
        raise ValueError(f"Category '{clean_name}' already exists")
    doc = {"name": clean_name, "type": cat_type}
    ref.set(doc)
    return doc


# ── Vendor flags ──────────────────────────────────────────────────────────────

def _flag_doc_id(description: str) -> str:
    return description.replace("/", "__slash__")


def upsert_vendor_flag(description: str, flag: str, notes: Optional[str] = None):
    from datetime import timezone
    doc = {
        "description": description,
        "flag": flag,
        "flagged_at": datetime.now(timezone.utc).isoformat(),
        "notes": notes,
    }
    get_db().collection("vendor_flags").document(_flag_doc_id(description)).set(doc)


def delete_vendor_flag(description: str):
    get_db().collection("vendor_flags").document(_flag_doc_id(description)).delete()


def get_all_vendor_flags() -> list:
    return [snap.to_dict() for snap in get_db().collection("vendor_flags").stream()]


if __name__ == "__main__":
    init_db()
    print("Firestore categories seeded.")

"""
Rule-based spending insights engine.
No additional Claude API calls — pure Python logic on transaction data.
"""
import os
from collections import defaultdict
from datetime import datetime
from tools import db_manager


def compute_insights(transactions: list, pnl: dict, prior_pnl: dict) -> dict:
    sections = {}

    # Rule A: Recurring subscriptions
    sections["subscriptions"] = _rule_subscriptions(transactions)

    # Rule B: Top 3 expense categories by % of total spending
    sections["top_categories"] = _rule_top_categories(pnl)

    # Rule C: Month-over-month increases > 20%
    sections["mom_increases"] = _rule_mom_increases(pnl, prior_pnl)

    # Rule D: Subscription creep (high-frequency small charges)
    sections["subscription_creep"] = _rule_subscription_creep(transactions)

    # Rule E: Largest single transactions
    sections["largest_transactions"] = _rule_largest_transactions(transactions)

    return sections


def _rule_subscriptions(transactions: list) -> dict:
    subs = [t for t in transactions if t.get("category") == "subscriptions"]
    grouped = defaultdict(lambda: {"count": 0, "total": 0.0})
    for t in subs:
        key = t["description"].upper().strip()
        grouped[key]["count"] += 1
        grouped[key]["total"] = round(grouped[key]["total"] + t["amount"], 2)

    items = [
        {"vendor": name, "amount": data["total"], "count": data["count"]}
        for name, data in grouped.items()
    ]
    items.sort(key=lambda x: abs(x["amount"]), reverse=True)

    return {
        "title": "Recurring Subscriptions",
        "items": items
    }


def _rule_top_categories(pnl: dict) -> dict:
    expenses = [c for c in pnl.get("by_category", []) if c["type"] == "expense" and c["total"] < 0]
    total_abs = sum(abs(c["total"]) for c in expenses) or 1

    items = []
    for c in sorted(expenses, key=lambda x: abs(x["total"]), reverse=True)[:5]:
        items.append({
            "category": c["category"],
            "total": c["total"],
            "pct": round(abs(c["total"]) / total_abs * 100, 1)
        })

    return {
        "title": "Top Expense Categories",
        "items": items
    }


def _rule_mom_increases(pnl: dict, prior_pnl: dict) -> dict:
    if not prior_pnl:
        return {"title": "Month-over-Month Increases", "available": False, "items": []}

    prior_map = {c["category"]: c["total"] for c in prior_pnl.get("by_category", [])}
    items = []

    for c in pnl.get("by_category", []):
        if c["type"] != "expense" or c["total"] >= 0:
            continue
        prior_val = prior_map.get(c["category"])
        if prior_val is None or prior_val >= 0:
            continue
        pct_change = (abs(c["total"]) - abs(prior_val)) / abs(prior_val) * 100
        if pct_change > 20:
            items.append({
                "category": c["category"],
                "prior": round(prior_val, 2),
                "current": round(c["total"], 2),
                "pct_change": round(pct_change, 1)
            })

    items.sort(key=lambda x: x["pct_change"], reverse=True)
    return {"title": "Month-over-Month Increases (>20%)", "available": True, "items": items}


def _rule_subscription_creep(transactions: list) -> dict:
    """High-frequency small charges: same vendor, >3 times, each < $30."""
    small = [t for t in transactions if -30 < t.get("amount", 0) < 0]
    grouped = defaultdict(lambda: {"count": 0, "total": 0.0, "amounts": []})
    for t in small:
        key = t["description"].upper().strip()
        grouped[key]["count"] += 1
        grouped[key]["total"] = round(grouped[key]["total"] + t["amount"], 2)
        grouped[key]["amounts"].append(t["amount"])

    items = []
    for name, data in grouped.items():
        if data["count"] > 3:
            per_tx = round(data["total"] / data["count"], 2)
            items.append({
                "vendor": name,
                "count": data["count"],
                "per_tx": per_tx,
                "total": round(data["total"], 2)
            })

    items.sort(key=lambda x: x["count"], reverse=True)
    return {"title": "Possible Subscription Creep", "items": items}


def _rule_largest_transactions(transactions: list) -> dict:
    expenses = [t for t in transactions if t.get("amount", 0) < 0]
    top = sorted(expenses, key=lambda x: abs(x["amount"]), reverse=True)[:5]
    items = [
        {
            "date": t["date"],
            "description": t["description"],
            "amount": t["amount"],
            "category": t.get("category", "")
        }
        for t in top
    ]
    return {"title": "Largest Single Expenses", "items": items}


def compute_spending_dna(transactions: list, account_type: str = "personal",
                          account_suffix: str = None) -> dict:
    """
    Analyzes spending patterns across all provided transactions (multi-month).
    Also queries vendor_flags from the DB.
    Returns: {top_vendors, recurring, subscriptions_to_review, spending_spikes, flagged_vendors}
    """
    # ── Load vendor flags ──────────────────────────────────────────────────────
    try:
        flags_by_desc = {r["description"]: r for r in db_manager.get_all_vendor_flags()}
    except Exception:
        flags_by_desc = {}

    # ── Aggregate by vendor across all months ──────────────────────────────────
    vendor_totals = defaultdict(lambda: {"total": 0.0, "count": 0, "months": set()})
    for t in transactions:
        if t.get("amount", 0) >= 0:
            continue
        desc = t.get("description", "").strip()
        if not desc:
            continue
        month = (t.get("date") or "")[:7]  # YYYY-MM
        vendor_totals[desc]["total"] += t["amount"]
        vendor_totals[desc]["count"] += 1
        vendor_totals[desc]["months"].add(month)

    # ── Top vendors ────────────────────────────────────────────────────────────
    top_vendors = sorted(
        [
            {
                "description": desc,
                "total_cad": round(data["total"], 2),
                "count": data["count"],
                "avg_per_visit": round(data["total"] / data["count"], 2),
                "flag": flags_by_desc.get(desc, {}).get("flag"),
            }
            for desc, data in vendor_totals.items()
        ],
        key=lambda x: abs(x["total_cad"]),
        reverse=True
    )[:10]

    # ── Recurring: appear in 3+ distinct months ────────────────────────────────
    months_count = {desc: len(data["months"]) for desc, data in vendor_totals.items()}
    recurring_descs = {desc for desc, cnt in months_count.items() if cnt >= 3}

    recurring = sorted(
        [
            {
                "description": desc,
                "months_seen": months_count[desc],
                "avg_monthly": round(vendor_totals[desc]["total"] / months_count[desc], 2),
            }
            for desc in recurring_descs
        ],
        key=lambda x: abs(x["avg_monthly"]),
        reverse=True
    )

    # ── Subscriptions to review: recurring + avg < $50/mo ─────────────────────
    subscriptions_to_review = [
        r for r in recurring if abs(r["avg_monthly"]) < 50
    ]

    # ── Spending spikes: month-over-month category changes ─────────────────────
    monthly_by_cat: dict = defaultdict(lambda: defaultdict(float))
    for t in transactions:
        if t.get("amount", 0) >= 0:
            continue
        month = (t.get("date") or "")[:7]
        cat = t.get("category") or "uncategorized"
        monthly_by_cat[cat][month] += t["amount"]

    spending_spikes = []
    for cat, month_data in monthly_by_cat.items():
        sorted_months = sorted(month_data.keys())
        for i in range(1, len(sorted_months)):
            prev_m = sorted_months[i - 1]
            curr_m = sorted_months[i]
            prev_val = abs(month_data[prev_m])
            curr_val = abs(month_data[curr_m])
            if prev_val > 0:
                change = curr_val - prev_val
                pct_change = change / prev_val * 100
                if pct_change > 25 and abs(change) > 50:
                    spending_spikes.append({
                        "category": cat,
                        "month": curr_m,
                        "prev_month": prev_m,
                        "change_cad": round(change, 2),
                        "pct_change": round(pct_change, 1),
                    })

    spending_spikes.sort(key=lambda x: abs(x["change_cad"]), reverse=True)
    spending_spikes = spending_spikes[:5]

    # ── Flagged vendors ────────────────────────────────────────────────────────
    flagged_vendors = []
    for desc, flag_info in flags_by_desc.items():
        total = vendor_totals.get(desc, {}).get("total", 0)
        flagged_vendors.append({
            "description": desc,
            "flag": flag_info.get("flag"),
            "notes": flag_info.get("notes"),
            "total_cad": round(total, 2),
        })

    return {
        "top_vendors": top_vendors,
        "recurring": recurring,
        "subscriptions_to_review": subscriptions_to_review,
        "spending_spikes": spending_spikes,
        "flagged_vendors": flagged_vendors,
    }

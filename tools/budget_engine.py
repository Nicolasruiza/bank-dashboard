import json
import os
import time
from statistics import median
from typing import Optional

from anthropic import Anthropic
from dotenv import load_dotenv
from tools.firestore_client import get_db

load_dotenv()

DISCRETIONARY = {"meals", "entertainment", "shopping", "subscriptions", "travel", "education"}
ESSENTIAL = {"groceries", "utilities", "insurance", "rent", "healthcare", "telecom"}

_client = None


def _get_client():
    global _client
    if _client is None:
        _client = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    return _client


def upsert_budget_target(category: str, target_cad: float) -> dict:
    doc = {"category": category, "target_cad": target_cad, "period": "monthly"}
    get_db().collection("budget_targets").document(category).set(doc)
    return doc


def get_all_targets() -> list:
    return sorted(
        [snap.to_dict() for snap in get_db().collection("budget_targets").stream()],
        key=lambda x: x.get("category", "")
    )


def get_budget_analysis(month: str, account_type: str = "personal",
                        account_suffix: Optional[str] = None) -> dict:
    db = get_db()

    # Fetch expense category names
    expense_cats = {
        snap.id for snap in db.collection("categories").stream()
        if snap.to_dict().get("type") == "expense"
    }

    # Fetch actual spending from denormalized transactions
    query = (db.collection("transactions")
               .where("month", "==", month)
               .where("account_type", "==", account_type))
    if account_suffix:
        query = query.where("account_suffix", "==", account_suffix)

    actuals: dict = {}
    for snap in query.stream():
        d = snap.to_dict()
        cat = d.get("category")
        if cat in expense_cats:
            actuals[cat] = actuals.get(cat, 0.0) + d.get("amount", 0.0)

    targets = {snap.id: snap.to_dict().get("target_cad") for snap in db.collection("budget_targets").stream()}

    all_cats = set(actuals.keys()) | set(targets.keys())
    items = []
    on_track = near_limit = over_budget = no_target = 0

    for cat in sorted(all_cats):
        actual = actuals.get(cat, 0.0)
        target = targets.get(cat)
        actual_abs = abs(actual)

        if target is None:
            status = "no_target"
            variance = variance_pct = None
            no_target += 1
        else:
            variance = actual_abs - target
            variance_pct = (variance / target * 100) if target else 0
            if actual_abs <= target * 0.9:
                status = "green"; on_track += 1
            elif actual_abs <= target:
                status = "amber"; near_limit += 1
            else:
                status = "red"; over_budget += 1

        items.append({
            "category": cat,
            "target": target,
            "actual": round(actual, 2),
            "variance": round(variance, 2) if variance is not None else None,
            "variance_pct": round(variance_pct, 1) if variance_pct is not None else None,
            "status": status,
        })

    return {
        "month": month,
        "items": items,
        "summary": {
            "on_track": on_track, "near_limit": near_limit,
            "over_budget": over_budget, "no_target": no_target,
        },
    }


def suggest_budgets(account_type: str = "personal", months: int = 6) -> list:
    db = get_db()

    # Get last N months from statements
    stmt_docs = (db.collection("statements")
                   .where("account_type", "==", account_type)
                   .stream())
    all_months = sorted(
        {d.to_dict().get("month") for d in stmt_docs if d.to_dict().get("month")},
        reverse=True
    )
    available_months = all_months[:months]

    if len(available_months) < 2:
        return []

    expense_cats = {
        snap.id for snap in db.collection("categories").stream()
        if snap.to_dict().get("type") == "expense"
    }

    monthly_by_cat: dict = {}
    for month in available_months:
        txs = (db.collection("transactions")
                 .where("month", "==", month)
                 .where("account_type", "==", account_type)
                 .stream())
        month_cat: dict = {}
        for snap in txs:
            d = snap.to_dict()
            cat = d.get("category")
            if cat in expense_cats:
                month_cat[cat] = month_cat.get(cat, 0.0) + d.get("amount", 0.0)
        for cat, total in month_cat.items():
            if cat not in monthly_by_cat:
                monthly_by_cat[cat] = []
            monthly_by_cat[cat].append(abs(total))

    medians = {
        cat: round(median(vals), 2)
        for cat, vals in monthly_by_cat.items()
        if len(vals) >= 2
    }
    if not medians:
        return []

    prompt = f"""You are a Canadian CPA building budget recommendations.

Here are a client's median monthly spending amounts by expense category (in CAD) over the last {len(available_months)} months:

{json.dumps(medians, indent=2)}

Rules for suggesting target budgets:
- Discretionary categories ({', '.join(sorted(DISCRETIONARY))}): suggest median × 0.90 (10% reduction)
- Essential categories ({', '.join(sorted(ESSENTIAL))}): suggest median × 1.00 (no reduction)
- Other categories: suggest median × 0.95
- Round to nearest dollar
- Write a single concrete rationale sentence per category referencing the median amount

Return ONLY a JSON array of objects with keys: category, suggested_amount (number), rationale (string).
Only include expense categories with meaningful spend (> $10/month). Max 10 suggestions.
JSON array only, no other text:"""

    client = _get_client()
    for attempt in range(3):
        try:
            response = client.messages.create(
                model="claude-sonnet-4-6", max_tokens=1024,
                messages=[{"role": "user", "content": prompt}]
            )
            text = response.content[0].text.strip()
            if text.startswith("```"):
                text = "\n".join(text.split("\n")[1:-1])
            return json.loads(text)
        except Exception as e:
            err = str(e).lower()
            if ("rate_limit" in err or "429" in err) and attempt < 2:
                time.sleep(60)
            elif ("overload" in err or "529" in err) and attempt < 2:
                time.sleep(30)
            else:
                break
    return []

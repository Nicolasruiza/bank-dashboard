import json
import os
import time
from typing import Optional

from anthropic import Anthropic
from dotenv import load_dotenv
from tools import db_manager

load_dotenv()

_client = None


def _get_client():
    global _client
    if _client is None:
        _client = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    return _client


def get_financial_snapshot(account_type: str = "personal", months: int = 3) -> dict:
    """
    Pulls the last N months of P&L summaries from Firestore.
    Returns a compact dict suitable for injecting into the Claude prompt.
    """
    available_months = db_manager.get_available_months(account_type)[:months]

    monthly_snapshots = []
    for month in available_months:
        pnl = db_manager.get_pnl_by_month(month, account_type)
        by_cat = pnl.get("by_category", [])
        income = pnl["total_income"]
        expenses = pnl["total_expenses"]
        top_cats = [
            {"category": c["category"], "amount": c["total"]}
            for c in by_cat if c["total"] < 0
        ][:8]
        monthly_snapshots.append({
            "month": month,
            "total_income": income,
            "total_expenses": expenses,
            "net": pnl["net"],
            "top_expense_categories": top_cats,
        })

    if not monthly_snapshots:
        return {"months": [], "error": "No data available"}

    avg_income = sum(s["total_income"] for s in monthly_snapshots) / len(monthly_snapshots)
    avg_expenses = sum(abs(s["total_expenses"]) for s in monthly_snapshots) / len(monthly_snapshots)
    avg_net = sum(s["net"] for s in monthly_snapshots) / len(monthly_snapshots)
    savings_rate = (avg_net / avg_income * 100) if avg_income > 0 else 0

    return {
        "account_type": account_type,
        "months": monthly_snapshots,
        "averages": {
            "monthly_income": round(avg_income, 2),
            "monthly_expenses": round(avg_expenses, 2),
            "monthly_net": round(avg_net, 2),
            "savings_rate_pct": round(savings_rate, 1),
        }
    }


def run_cpa_analysis(snapshot: dict, portfolio_summary: Optional[dict] = None) -> list:
    """
    Calls Claude with a structured financial snapshot.
    Returns a list of advisory sections: [{title, insight, action, priority}]
    """
    portfolio_ctx = ""
    if portfolio_summary and portfolio_summary.get("totals", {}).get("total_market_value_cad", 0) > 0:
        t = portfolio_summary["totals"]
        portfolio_ctx = f"""
Portfolio summary:
- Total invested (cost basis): ${t.get('total_cost_cad', 0):,.2f} CAD
- Current market value: ${t.get('total_market_value_cad', 0):,.2f} CAD
- Unrealized gain/loss: ${t.get('total_gain_loss_cad', 0):,.2f} CAD ({t.get('total_gain_loss_pct', 0):.1f}%)
- Total dividends received: ${t.get('total_dividends_cad', 0):,.2f} CAD
"""

    prompt = f"""You are a senior CPA and financial advisor operating under Canadian CRA rules.
You speak directly and give specific, actionable recommendations — not generic advice.
You identify money leaks, tax optimization opportunities, and wealth-building gaps.
You reference specific numbers from the data provided.

Financial data for the last {len(snapshot.get('months', []))} months:

{json.dumps(snapshot, indent=2)}
{portfolio_ctx}

Return ONLY a JSON array with up to 6 advisory sections, ordered by priority (high first).
Each section must have exactly these keys: title, insight, action, priority (high|medium|low).

Consider flagging when relevant:
- RRSP contribution room opportunities (when income > $80k annualized)
- TFSA headroom if savings rate is below 15%
- Business expense deductions if account_type is business
- Capital gains exposure if portfolio has large unrealized gains

JSON array only, no other text:"""

    client = _get_client()
    max_attempts = 3
    for attempt in range(max_attempts):
        try:
            response = client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=2048,
                messages=[{"role": "user", "content": prompt}]
            )
            text = response.content[0].text.strip()
            # Strip markdown fences if present
            if text.startswith("```"):
                lines = text.split("\n")
                text = "\n".join(lines[1:-1])
            return json.loads(text)
        except Exception as e:
            err_str = str(e).lower()
            if "rate_limit" in err_str or "429" in err_str:
                if attempt < max_attempts - 1:
                    time.sleep(60)
            elif "overload" in err_str or "529" in err_str or "500" in err_str:
                if attempt < max_attempts - 1:
                    time.sleep(30)
            else:
                break
    return []

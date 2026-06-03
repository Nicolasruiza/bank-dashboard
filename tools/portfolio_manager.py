import time
from datetime import datetime, timezone
from typing import Optional

from google.cloud import firestore as firestore_lib
from tools.firestore_client import get_db
from tools.db_manager import _next_id

# In-memory price cache: {ticker: (price, timestamp)}
_price_cache: dict = {}
_CACHE_TTL = 300  # 5 minutes

VALID_ACCOUNT_TYPES = ["TFSA", "RRSP", "RRIF", "RESP", "FHSA", "Non-Registered"]


def _snap_to_dict(snap) -> Optional[dict]:
    if snap is None or not snap.exists:
        return None
    d = snap.to_dict()
    d.setdefault("id", int(snap.id) if snap.id.isdigit() else snap.id)
    return d


# ── FX & Price Fetching ────────────────────────────────────────────────────────

def get_usdcad_rate() -> float:
    """Fetch live USD/CAD rate via yfinance. Returns last cached or 1.38 as fallback."""
    cached = _price_cache.get("USDCAD=X")
    if cached and (time.time() - cached[1]) < _CACHE_TTL:
        return cached[0]
    try:
        import yfinance as yf
        rate = yf.Ticker("USDCAD=X").fast_info.last_price
        if rate and rate > 0:
            _price_cache["USDCAD=X"] = (float(rate), time.time())
            return float(rate)
    except Exception:
        pass
    # Return last cached value if any, else hard fallback
    cached = _price_cache.get("USDCAD=X")
    return cached[0] if cached else 1.38


def get_live_prices(tickers: list) -> dict:
    """
    Fetch last prices for a list of tickers in their native currency.
    Returns {ticker: price_native} — None for failed/stale tickers.
    Uses 5-minute in-memory cache.
    """
    result = {}
    to_fetch = []
    now = time.time()

    for t in tickers:
        cached = _price_cache.get(t)
        if cached and (now - cached[1]) < _CACHE_TTL:
            result[t] = cached[0]
        else:
            to_fetch.append(t)

    if to_fetch:
        try:
            import yfinance as yf
            tickers_obj = yf.Tickers(" ".join(to_fetch))
            for t in to_fetch:
                try:
                    price = tickers_obj.tickers[t].fast_info.last_price
                    if price and price > 0:
                        _price_cache[t] = (float(price), now)
                        result[t] = float(price)
                    else:
                        result[t] = None
                except Exception:
                    result[t] = None
        except Exception:
            for t in to_fetch:
                result[t] = None

    return result


# ── Holdings CRUD ──────────────────────────────────────────────────────────────

def get_ticker_info(ticker: str) -> dict:
    """
    Returns {"currency": "CAD"|"USD"|..., "name": "..."} for a valid ticker.
    Raises ValueError on invalid/unknown ticker.
    """
    import yfinance as yf
    ticker = ticker.upper().strip()
    try:
        info = yf.Ticker(ticker).fast_info
        currency = getattr(info, "currency", None)
        if not currency:
            raise ValueError(f"No currency found for ticker '{ticker}'")
        name = getattr(info, "company_name", ticker)
        return {"currency": currency.upper(), "name": name or ticker}
    except Exception as e:
        raise ValueError(f"Could not fetch info for ticker '{ticker}': {e}")


# ── Investment Accounts CRUD ───────────────────────────────────────────────────

def get_all_accounts() -> list:
    db = get_db()
    accounts = []
    for snap in db.collection("investment_accounts").stream():
        d = snap.to_dict()
        d.setdefault("id", int(snap.id))
        count = len(list(db.collection("portfolio_holdings").where("account_id", "==", d["id"]).stream()))
        d["holding_count"] = count
        accounts.append(d)
    return sorted(accounts, key=lambda a: a.get("created_at", ""))


def create_account(account_type: str, nickname: str) -> dict:
    if account_type not in VALID_ACCOUNT_TYPES:
        raise ValueError(f"Invalid account type '{account_type}'")
    nickname = nickname.strip()
    if not nickname:
        raise ValueError("Nickname is required")
    db = get_db()
    new_id = _next_id("investment_accounts")
    doc = {
        "id": new_id,
        "type": account_type,
        "nickname": nickname,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    db.collection("investment_accounts").document(str(new_id)).set(doc)
    return {**doc, "holding_count": 0}


def update_account(account_id: int, nickname: str) -> dict:
    nickname = nickname.strip()
    if not nickname:
        raise ValueError("Nickname is required")
    db = get_db()
    ref = db.collection("investment_accounts").document(str(account_id))
    snap = ref.get()
    if not snap.exists:
        raise ValueError(f"Account {account_id} not found")
    ref.update({"nickname": nickname})
    count = len(list(db.collection("portfolio_holdings").where("account_id", "==", account_id).stream()))
    d = snap.to_dict()
    d["nickname"] = nickname
    d["id"] = account_id
    d["holding_count"] = count
    return d


def delete_account(account_id: int) -> None:
    db = get_db()
    count = len(list(db.collection("portfolio_holdings").where("account_id", "==", account_id).stream()))
    if count > 0:
        raise ValueError(f"Cannot delete account with {count} holding(s) assigned — reassign them first")
    db.collection("investment_accounts").document(str(account_id)).delete()


def assign_holding_account(holding_id: int, account_id: Optional[int]) -> None:
    get_db().collection("portfolio_holdings").document(str(holding_id)).update({"account_id": account_id})


# ── Holdings CRUD ──────────────────────────────────────────────────────────────

def add_holding(ticker: str, shares: float, avg_buy_price_local: float, account_id: Optional[int] = None) -> dict:
    """
    Stores avg_buy_price exactly as entered. Currency is auto-detected via yfinance.
    Raises ValueError if ticker is invalid.
    """
    ticker = ticker.upper().strip()
    info = get_ticker_info(ticker)
    local_currency = info["currency"].upper()
    db = get_db()
    new_id = _next_id("portfolio_holdings")
    doc = {
        "id": new_id,
        "ticker": ticker,
        "shares": shares,
        "avg_buy_price_local": avg_buy_price_local,
        "currency": local_currency,
        "local_currency": local_currency,
        "account_id": account_id,
        "added_at": datetime.now(timezone.utc).isoformat(),
    }
    db.collection("portfolio_holdings").document(str(new_id)).set(doc)
    return doc


def delete_holding(holding_id: int) -> None:
    db = get_db()
    divs = db.collection("portfolio_dividends").where("holding_id", "==", holding_id).stream()
    for snap in divs:
        snap.reference.delete()
    db.collection("portfolio_holdings").document(str(holding_id)).delete()


def get_all_holdings() -> list:
    db = get_db()
    holdings = []
    for snap in db.collection("portfolio_holdings").stream():
        d = snap.to_dict()
        d.setdefault("id", int(snap.id))
        # Attach account label if assigned
        acct_id = d.get("account_id")
        if acct_id:
            acct_snap = db.collection("investment_accounts").document(str(acct_id)).get()
            if acct_snap.exists:
                acct = acct_snap.to_dict()
                d["account_type_label"] = acct.get("type")
                d["account_nickname"] = acct.get("nickname")
        holdings.append(d)
    # Sort: assigned accounts first (by account_id), then unassigned, within each by added_at
    return sorted(holdings, key=lambda h: (
        0 if h.get("account_id") is None else 1,  # None last — flip: unassigned=1, assigned=0
        h.get("account_id") or 0,
        h.get("added_at", "")
    ))


# ── Dividends ──────────────────────────────────────────────────────────────────

def log_dividend(holding_id: int, ticker: str, amount_cad: float, paid_date: str,
                 notes: Optional[str] = None) -> dict:
    db = get_db()
    new_id = _next_id("portfolio_dividends")
    doc = {
        "id": new_id,
        "holding_id": holding_id,
        "ticker": ticker.upper().strip(),
        "amount_cad": amount_cad,
        "paid_date": paid_date,
        "notes": notes,
    }
    db.collection("portfolio_dividends").document(str(new_id)).set(doc)
    return doc


def get_dividends(holding_id: Optional[int] = None) -> list:
    db = get_db()
    if holding_id is not None:
        query = db.collection("portfolio_dividends").where("holding_id", "==", holding_id)
    else:
        query = db.collection("portfolio_dividends")
    divs = [snap.to_dict() for snap in query.stream()]
    return sorted(divs, key=lambda d: d.get("paid_date", ""), reverse=True)


# ── Portfolio Summary ──────────────────────────────────────────────────────────

def get_portfolio_summary() -> dict:
    """
    Fetches all holdings with live prices, computing both local-currency and CAD values.

    Per holding:
      current_price_local  — live price in the holding's native currency
      current_value_local  — shares × current_price_local
      current_price_cad    — current_price_local × fx_rate (if USD), else same
      current_value_cad    — shares × current_price_cad
      cost_basis_local     — shares × avg_buy_price_local (as entered)
      cost_basis_cad       — cost_basis_local × fx_rate (if USD), else same
      unrealized_gl_local  — current_value_local − cost_basis_local
      unrealized_gl_cad    — current_value_cad − cost_basis_cad
      unrealized_gl_pct    — unrealized_gl_local / cost_basis_local × 100
      weight_pct           — holding's CAD market value / total CAD market value

    Never raises — stale prices flagged with price_stale=True.
    """
    holdings = get_all_holdings()
    dividends = get_dividends()
    total_dividends = sum(d["amount_cad"] for d in dividends)

    fx_rate = get_usdcad_rate()
    fx_timestamp = datetime.now(timezone.utc).isoformat()

    if not holdings:
        return {
            "holdings": [],
            "totals": {
                "total_cost_cad": 0,
                "total_cost_local_breakdown": {},
                "total_market_value_cad": 0,
                "total_market_value_local_breakdown": {},
                "total_unrealized_gl_cad": 0,
                "total_unrealized_gl_pct": 0,
                "total_dividends_cad": 0,
            },
            "fx_rate_usd_cad": fx_rate,
            "fx_timestamp": fx_timestamp,
        }

    tickers = [h["ticker"] for h in holdings]
    prices = get_live_prices(tickers)

    enriched = []
    total_cost_cad = 0.0
    total_value_cad = 0.0
    # Track local-currency breakdowns (for display)
    local_cost_breakdown: dict = {}
    local_value_breakdown: dict = {}

    for h in holdings:
        ticker = h["ticker"]
        # Resolve local_currency — fall back to `currency` column for rows migrated before this fix
        local_currency = (h.get("local_currency") or h.get("currency") or "USD").upper()
        is_usd = local_currency == "USD"

        live_price_local = prices.get(ticker)
        stale = live_price_local is None

        # ── Local values (native currency) ──────────────────────────────────────
        avg_price_local = h["avg_buy_price_local"]
        cost_basis_local = round(avg_price_local * h["shares"], 2)

        if not stale:
            current_value_local = round(live_price_local * h["shares"], 2)
            unrealized_gl_local = round(current_value_local - cost_basis_local, 2)
        else:
            current_value_local = cost_basis_local  # best-effort: use cost as placeholder
            unrealized_gl_local = 0.0

        unrealized_gl_pct = round(
            unrealized_gl_local / cost_basis_local * 100, 2
        ) if cost_basis_local else 0.0

        # ── CAD values (converted) ───────────────────────────────────────────────
        if is_usd:
            current_price_cad = round(live_price_local * fx_rate, 4) if not stale else None
            cost_basis_cad = round(cost_basis_local * fx_rate, 2)
            current_value_cad = round(current_value_local * fx_rate, 2)
        else:
            current_price_cad = live_price_local if not stale else None
            cost_basis_cad = cost_basis_local
            current_value_cad = current_value_local

        unrealized_gl_cad = round(current_value_cad - cost_basis_cad, 2)

        total_cost_cad += cost_basis_cad
        total_value_cad += current_value_cad

        # Accumulate local breakdowns for summary
        lc = local_currency
        local_cost_breakdown[lc] = round(local_cost_breakdown.get(lc, 0) + cost_basis_local, 2)
        local_value_breakdown[lc] = round(local_value_breakdown.get(lc, 0) + current_value_local, 2)

        enriched.append({
            **h,
            "local_currency": local_currency,
            # Local (native) figures
            "avg_buy_price_local": avg_price_local,
            "current_price_local": round(live_price_local, 4) if not stale else None,
            "current_value_local": current_value_local,
            "cost_basis_local": cost_basis_local,
            "unrealized_gl_local": unrealized_gl_local,
            # CAD figures
            "current_price_cad": current_price_cad,
            "current_value_cad": current_value_cad,
            "cost_basis_cad": cost_basis_cad,
            "unrealized_gl_cad": unrealized_gl_cad,
            # Shared
            "unrealized_gl_pct": unrealized_gl_pct,
            "price_stale": stale,
        })

    # Compute CAD-based weights after all totals are known
    for h in enriched:
        h["weight_pct"] = round(h["current_value_cad"] / total_value_cad * 100, 1) if total_value_cad else 0

    total_gl_cad = total_value_cad - total_cost_cad
    total_gl_pct = round(total_gl_cad / total_cost_cad * 100, 2) if total_cost_cad else 0

    return {
        "holdings": enriched,
        "totals": {
            "total_cost_cad": round(total_cost_cad, 2),
            "total_cost_local_breakdown": local_cost_breakdown,
            "total_market_value_cad": round(total_value_cad, 2),
            "total_market_value_local_breakdown": local_value_breakdown,
            "total_unrealized_gl_cad": round(total_gl_cad, 2),
            "total_unrealized_gl_pct": total_gl_pct,
            "total_dividends_cad": round(total_dividends, 2),
        },
        "fx_rate_usd_cad": fx_rate,
        "fx_timestamp": fx_timestamp,
    }

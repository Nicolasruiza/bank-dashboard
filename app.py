import os
os.environ.setdefault("GRPC_DNS_RESOLVER", "native")  # fix macOS gRPC DNS bug
import sys

from flask import Flask, request, jsonify, render_template
from dotenv import load_dotenv

load_dotenv()

sys.path.insert(0, os.path.dirname(__file__))

from tools import db_manager
from tools.statement_processor import process_statement
from tools.insights_engine import compute_insights, compute_spending_dna
from tools import portfolio_manager
from tools import cpa_advisor
from tools import budget_engine

UPLOAD_DIR = os.path.join(os.path.dirname(__file__), ".tmp")
os.makedirs(UPLOAD_DIR, exist_ok=True)

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 50 * 1024 * 1024

db_manager.init_db()


# ── Pages ──────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("dashboard.html")


# ── API: Months ────────────────────────────────────────────────────────────────

@app.route("/api/months")
def api_months():
    account_type = request.args.get("account_type", "personal")
    account_suffix = request.args.get("account_suffix") or None
    return jsonify(db_manager.get_available_months(account_type, account_suffix=account_suffix))


# ── API: P&L ──────────────────────────────────────────────────────────────────

@app.route("/api/pnl")
def api_pnl():
    month = request.args.get("month")
    account_type = request.args.get("account_type", "personal")
    account_suffix = request.args.get("account_suffix") or None
    exclude_internal = request.args.get("exclude_internal", "false").lower() == "true"
    if not month:
        return jsonify({"error": "month parameter required (YYYY-MM)"}), 400
    return jsonify(db_manager.get_pnl_by_month(
        month, account_type, exclude_internal=exclude_internal, account_suffix=account_suffix
    ))


# ── API: Transactions ──────────────────────────────────────────────────────────

@app.route("/api/transactions")
def api_transactions():
    month = request.args.get("month")
    account_type = request.args.get("account_type", "personal")
    account_suffix = request.args.get("account_suffix") or None
    if not month:
        return jsonify({"error": "month parameter required (YYYY-MM)"}), 400
    return jsonify(db_manager.get_transactions_by_month(month, account_type, account_suffix=account_suffix))


@app.route("/api/transactions/<int:tx_id>/category", methods=["POST"])
def api_update_category(tx_id):
    data = request.get_json()
    category = data.get("category") if data else None
    if not category:
        return jsonify({"error": "category required"}), 400
    db_manager.update_transaction_category(tx_id, category)
    return jsonify({"ok": True})


# ── API: Vendors ───────────────────────────────────────────────────────────────

@app.route("/api/vendors")
def api_vendors():
    month = request.args.get("month")
    category = request.args.get("category")
    account_type = request.args.get("account_type", "personal")
    account_suffix = request.args.get("account_suffix") or None
    if not month or not category:
        return jsonify({"error": "month and category parameters required"}), 400
    vendors = db_manager.get_top_vendors(month, category, account_type, account_suffix=account_suffix)
    return jsonify({"category": category, "month": month, "vendors": vendors})


# ── API: Insights ──────────────────────────────────────────────────────────────

@app.route("/api/insights")
def api_insights():
    month = request.args.get("month")
    account_type = request.args.get("account_type", "personal")
    account_suffix = request.args.get("account_suffix") or None
    if not month:
        return jsonify({"error": "month parameter required"}), 400
    data = db_manager.get_insights_data(month, account_type, account_suffix=account_suffix)
    sections = compute_insights(data["transactions"], data["pnl"], data["prior_pnl"])
    return jsonify({"month": month, "account_type": account_type, "sections": sections})


# ── API: Statements ────────────────────────────────────────────────────────────

@app.route("/api/statements")
def api_statements():
    account_type = request.args.get("account_type") or None
    return jsonify(db_manager.get_all_statements(account_type))


@app.route("/api/statements/<int:stmt_id>", methods=["DELETE"])
def api_delete_statement(stmt_id):
    db_manager.delete_statement(stmt_id)
    return jsonify({"ok": True})


@app.route("/api/statements/<int:stmt_id>", methods=["PATCH"])
def api_update_statement(stmt_id):
    data = request.get_json()
    if not data:
        return jsonify({"error": "JSON body required"}), 400

    if "account_type" in data:
        account_type = (data.get("account_type") or "").strip()
        if account_type not in ("personal", "business"):
            return jsonify({"error": "account_type must be 'personal' or 'business'"}), 400
        db_manager.update_statement_account_type(stmt_id, account_type)

    if "account_suffix" in data:
        suffix = data.get("account_suffix")
        if suffix is not None:
            suffix = str(suffix).strip()[-4:] or None
        db_manager.update_statement_account_suffix(stmt_id, suffix)

    if "account_subtype" in data:
        subtype = data.get("account_subtype") or None
        valid_subtypes = ("chequing", "savings", "credit_card", None)
        if subtype not in valid_subtypes:
            return jsonify({"error": "account_subtype must be chequing, savings, or credit_card"}), 400
        db_manager.update_statement_account_subtype(stmt_id, subtype)

    return jsonify({"ok": True})


# ── API: Account Suffixes ──────────────────────────────────────────────────────

@app.route("/api/account-suffixes")
def api_account_suffixes():
    account_type = request.args.get("account_type", "personal")
    return jsonify(db_manager.get_account_suffixes(account_type))


# ── API: Category Trend ───────────────────────────────────────────────────────

@app.route("/api/category-trend")
def api_category_trend():
    category = request.args.get("category")
    account_type = request.args.get("account_type", "personal")
    account_suffix = request.args.get("account_suffix") or None
    account_subtype = request.args.get("account_subtype") or None
    if not category:
        return jsonify({"error": "category parameter required"}), 400
    return jsonify(db_manager.get_category_trend(category, account_type, account_suffix, account_subtype))


# ── API: Upload ────────────────────────────────────────────────────────────────

@app.route("/api/upload", methods=["POST"])
def api_upload():
    if "file" not in request.files:
        return jsonify({"error": "No file provided"}), 400

    f = request.files["file"]
    if not f.filename:
        return jsonify({"error": "Empty filename"}), 400
    if not f.filename.lower().endswith(".pdf"):
        return jsonify({"error": "Only PDF files are supported"}), 400

    bank_hint = request.form.get("bank_name", "").strip() or None
    account_type = request.form.get("account_type", "personal")
    if account_type not in ("personal", "business"):
        account_type = "personal"
    account_subtype = request.form.get("account_subtype", "").strip() or None
    if account_subtype not in ("chequing", "savings", "credit_card", None):
        account_subtype = None
    # Manual account suffix override from upload form (takes precedence over auto-detection)
    manual_suffix = request.form.get("account_suffix_override", "").strip()
    manual_suffix = manual_suffix[-4:] if manual_suffix.isdigit() and len(manual_suffix) >= 4 else None

    safe_name = os.path.basename(f.filename)
    save_path = os.path.join(UPLOAD_DIR, safe_name)
    f.save(save_path)

    statement_id = db_manager.create_statement(safe_name, bank_name=bank_hint, account_type=account_type,
                                               account_subtype=account_subtype)
    result = process_statement(save_path, statement_id, bank_hint=bank_hint)
    # Override auto-detected suffix if user provided one manually
    if manual_suffix and result.get("status") == "done":
        db_manager.update_statement_account_suffix(statement_id, manual_suffix)
        result["account_suffix"] = manual_suffix

    if result["status"] == "error":
        return jsonify({"error": result["message"]}), 422

    return jsonify({
        "ok": True,
        "statement_id": statement_id,
        "transaction_count": result["transaction_count"],
        "month": result.get("month"),
        "account_type": result.get("account_type") or account_type,
        "account_suffix": result.get("account_suffix")
    })


# ── API: Categories ────────────────────────────────────────────────────────────

@app.route("/api/categories", methods=["GET"])
def api_categories():
    return jsonify(db_manager.get_all_categories())


@app.route("/api/categories", methods=["POST"])
def api_create_category():
    data = request.get_json()
    name = (data.get("name") or "").strip() if data else ""
    cat_type = (data.get("type") or "").strip() if data else ""
    if not name or not cat_type:
        return jsonify({"error": "name and type required"}), 400
    if cat_type not in ("expense", "income"):
        return jsonify({"error": "type must be 'expense' or 'income'"}), 400
    try:
        return jsonify(db_manager.create_category(name, cat_type)), 201
    except ValueError as e:
        return jsonify({"error": str(e)}), 409


# ── API: Portfolio ─────────────────────────────────────────────────────────────

@app.route("/api/portfolio/ticker-info", methods=["GET"])
def api_portfolio_ticker_info():
    ticker = (request.args.get("ticker") or "").strip().upper()
    if not ticker:
        return jsonify({"error": "ticker param required"}), 400
    try:
        info = portfolio_manager.get_ticker_info(ticker)
        return jsonify(info)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400


@app.route("/api/portfolio/accounts", methods=["GET"])
def api_portfolio_accounts_get():
    return jsonify(portfolio_manager.get_all_accounts())


@app.route("/api/portfolio/accounts", methods=["POST"])
def api_portfolio_accounts_post():
    data = request.get_json()
    if not data:
        return jsonify({"error": "JSON body required"}), 400
    account_type = (data.get("type") or "").strip()
    nickname = (data.get("nickname") or "").strip()
    try:
        account = portfolio_manager.create_account(account_type, nickname)
        return jsonify({"ok": True, "account": account}), 201
    except ValueError as e:
        return jsonify({"error": str(e)}), 400


@app.route("/api/portfolio/accounts/<int:account_id>", methods=["PATCH"])
def api_portfolio_accounts_patch(account_id):
    data = request.get_json()
    if not data:
        return jsonify({"error": "JSON body required"}), 400
    nickname = (data.get("nickname") or "").strip()
    try:
        account = portfolio_manager.update_account(account_id, nickname)
        return jsonify({"ok": True, "account": account})
    except ValueError as e:
        return jsonify({"error": str(e)}), 400


@app.route("/api/portfolio/accounts/<int:account_id>", methods=["DELETE"])
def api_portfolio_accounts_delete(account_id):
    try:
        portfolio_manager.delete_account(account_id)
        return jsonify({"ok": True})
    except ValueError as e:
        return jsonify({"error": str(e)}), 400


@app.route("/api/portfolio/holdings", methods=["GET"])
def api_portfolio_holdings_get():
    return jsonify(portfolio_manager.get_all_holdings())


@app.route("/api/portfolio/holdings", methods=["POST"])
def api_portfolio_holdings_post():
    data = request.get_json()
    if not data:
        return jsonify({"error": "JSON body required"}), 400
    ticker = (data.get("ticker") or "").strip()
    shares = data.get("shares")
    avg_price = data.get("avg_buy_price_local") or data.get("avg_cost_cad")
    account_id = data.get("account_id")  # optional
    if not ticker or shares is None or avg_price is None:
        return jsonify({"error": "ticker, shares, and avg_buy_price_local are required"}), 400
    try:
        holding = portfolio_manager.add_holding(
            ticker, float(shares), float(avg_price),
            int(account_id) if account_id else None
        )
        return jsonify({"ok": True, "holding": holding}), 201
    except ValueError as e:
        return jsonify({"error": str(e)}), 400


@app.route("/api/portfolio/holdings/<int:holding_id>", methods=["PATCH"])
def api_portfolio_holdings_patch(holding_id):
    data = request.get_json()
    if not data:
        return jsonify({"error": "JSON body required"}), 400
    account_id = data.get("account_id")  # None = unassign
    portfolio_manager.assign_holding_account(holding_id, int(account_id) if account_id else None)
    return jsonify({"ok": True})


@app.route("/api/portfolio/holdings/<int:holding_id>", methods=["DELETE"])
def api_portfolio_holdings_delete(holding_id):
    portfolio_manager.delete_holding(holding_id)
    return jsonify({"ok": True})


@app.route("/api/portfolio/dividends", methods=["POST"])
def api_portfolio_dividends_post():
    data = request.get_json()
    if not data:
        return jsonify({"error": "JSON body required"}), 400
    holding_id = data.get("holding_id")
    ticker = (data.get("ticker") or "").strip()
    amount_cad = data.get("amount_cad")
    paid_date = (data.get("paid_date") or "").strip()
    notes = data.get("notes")
    if not holding_id or not ticker or amount_cad is None or not paid_date:
        return jsonify({"error": "holding_id, ticker, amount_cad, and paid_date are required"}), 400
    div = portfolio_manager.log_dividend(int(holding_id), ticker, float(amount_cad), paid_date, notes)
    return jsonify({"ok": True, "dividend": div}), 201


@app.route("/api/portfolio/dividends", methods=["GET"])
def api_portfolio_dividends_get():
    holding_id = request.args.get("holding_id")
    return jsonify(portfolio_manager.get_dividends(int(holding_id) if holding_id else None))


@app.route("/api/portfolio/summary", methods=["GET"])
def api_portfolio_summary():
    return jsonify(portfolio_manager.get_portfolio_summary())


# ── API: CPA Advisor ───────────────────────────────────────────────────────────

@app.route("/api/advisor/analysis", methods=["POST"])
def api_advisor_analysis():
    data = request.get_json() or {}
    account_type = data.get("account_type", "personal")
    months = int(data.get("months", 3))
    try:
        snapshot = cpa_advisor.get_financial_snapshot(account_type, months)
        portfolio = portfolio_manager.get_portfolio_summary()
        sections = cpa_advisor.run_cpa_analysis(snapshot, portfolio)
        return jsonify({
            "sections": sections,
            "snapshot_months": [m["month"] for m in snapshot.get("months", [])],
            "generated_at": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(),
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 503


# ── API: Budget ────────────────────────────────────────────────────────────────

@app.route("/api/budget/targets", methods=["GET"])
def api_budget_targets_get():
    return jsonify(budget_engine.get_all_targets())


@app.route("/api/budget/targets", methods=["POST"])
def api_budget_targets_post():
    data = request.get_json()
    if not data:
        return jsonify({"error": "JSON body required"}), 400
    category = (data.get("category") or "").strip()
    target_cad = data.get("target_cad")
    if not category or target_cad is None:
        return jsonify({"error": "category and target_cad required"}), 400
    target = budget_engine.upsert_budget_target(category, float(target_cad))
    return jsonify({"ok": True, "target": target})


@app.route("/api/budget/analysis", methods=["GET"])
def api_budget_analysis():
    month = request.args.get("month")
    account_type = request.args.get("account_type", "personal")
    account_suffix = request.args.get("account_suffix") or None
    if not month:
        return jsonify({"error": "month required"}), 400
    return jsonify(budget_engine.get_budget_analysis(month, account_type, account_suffix))


@app.route("/api/budget/suggest", methods=["POST"])
def api_budget_suggest():
    data = request.get_json() or {}
    account_type = data.get("account_type", "personal")
    suggestions = budget_engine.suggest_budgets(account_type)
    return jsonify({"suggestions": suggestions})


# ── API: Spending DNA ──────────────────────────────────────────────────────────

@app.route("/api/spending-dna", methods=["GET"])
def api_spending_dna():
    account_type = request.args.get("account_type", "personal")
    account_suffix = request.args.get("account_suffix") or None

    # Fetch last 3 months of transactions
    months_res = db_manager.get_available_months(account_type, account_suffix=account_suffix)
    recent_months = months_res[:3]

    all_transactions = []
    for month in recent_months:
        txs = db_manager.get_transactions_by_month(month, account_type, account_suffix=account_suffix)
        all_transactions.extend(txs)

    return jsonify(compute_spending_dna(all_transactions, account_type, account_suffix))


@app.route("/api/spending-dna/flag", methods=["POST"])
def api_spending_dna_flag():
    data = request.get_json()
    if not data:
        return jsonify({"error": "JSON body required"}), 400
    description = (data.get("description") or "").strip()
    flag = (data.get("flag") or "cancel").strip()
    notes = data.get("notes")
    if not description:
        return jsonify({"error": "description required"}), 400
    if flag not in ("cancel", "review", "keep"):
        return jsonify({"error": "flag must be cancel, review, or keep"}), 400

    db_manager.upsert_vendor_flag(description, flag, notes)
    return jsonify({"ok": True})


@app.route("/api/spending-dna/flag", methods=["DELETE"])
def api_spending_dna_unflag():
    data = request.get_json()
    description = (data.get("description") or "").strip() if data else ""
    if not description:
        return jsonify({"error": "description required"}), 400

    db_manager.delete_vendor_flag(description)
    return jsonify({"ok": True})


# ── API: Net Worth ─────────────────────────────────────────────────────────────

@app.route("/api/net-worth", methods=["GET"])
def api_net_worth():
    # Cash = most recent personal month net
    months = db_manager.get_available_months("personal")
    cash = 0.0
    cash_label = "No data"
    if months:
        latest = months[0]
        pnl = db_manager.get_pnl_by_month(latest, "personal", exclude_internal=True)
        cash = pnl.get("adjusted_net") or pnl.get("net") or 0.0
        cash_label = latest

    # Investments = portfolio market value
    portfolio = portfolio_manager.get_portfolio_summary()
    invest = portfolio.get("totals", {}).get("total_market_value_cad", 0.0)
    fx = portfolio.get("fx_rate", 1.38)
    stale = any(h.get("price_stale") for h in portfolio.get("holdings", []))

    return jsonify({
        "cash_balance_cad": round(cash, 2),
        "cash_label": cash_label,
        "investment_value_cad": round(invest, 2),
        "total_net_worth_cad": round(cash + invest, 2),
        "fx_rate": fx,
        "portfolio_stale": stale,
    })


if __name__ == "__main__":
    print("Starting Bank Statement P&L Dashboard...")
    print("Open http://localhost:8080 in your browser")
    app.run(debug=True, port=8080)

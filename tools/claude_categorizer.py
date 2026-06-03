"""
Uses Claude AI to extract and categorize transactions from raw bank statement text.
One API call per PDF — all transactions batched together.
Returns (transactions_list, account_suffix_or_None).
"""
import json
import os
import time
from datetime import datetime
from typing import Optional

from anthropic import Anthropic
from dotenv import load_dotenv

load_dotenv()

# Keep in sync with EXPENSE_CATEGORIES + INCOME_CATEGORIES in db_manager.py
VALID_CATEGORIES = [
    # Expenses
    "meals", "groceries", "rent", "utilities", "subscriptions", "transport",
    "healthcare", "education", "entertainment", "fees", "insurance", "shopping",
    "transfers_out", "cc_payment", "office_expenses", "telecom", "taxes", "payroll",
    "professional_services", "bank_charges", "travel", "home_improvement",
    "investments", "other_expense",
    # Income
    "salary", "dividends", "transfers_in", "interest", "refunds",
    "cc_payment_received", "other_income"
]

PROMPT_TEMPLATE = """You are a financial data parser. Extract every transaction from the bank statement text below and return a JSON object.

For each transaction return an object with exactly these fields:
{{
  "date": "YYYY-MM-DD",
  "description": "original description text from the statement",
  "amount": <float: negative for expenses/debits/withdrawals/payments, positive for income/credits/deposits>,
  "category": "<one of the categories listed below>"
}}

Valid categories:
- Expenses: meals, groceries, rent, utilities, subscriptions, transport, healthcare, education, entertainment, fees, insurance, shopping, transfers_out, cc_payment, office_expenses, telecom, taxes, payroll, professional_services, bank_charges, travel, home_improvement, investments, other_expense
- Income: salary, dividends, transfers_in, interest, refunds, cc_payment_received, other_income

Category guidance:
- investments: money sent to brokerage/investment accounts, retirement accounts (Fidelity, Vanguard, Schwab, Robinhood, ETRADE, etc.) — always negative
- transfers_out: Zelle/Venmo sent to people, wire transfers, ACH pushes to external personal accounts — NOT to investment or credit card accounts
- transfers_in: incoming wire transfers, ACH pulls from external accounts, Zelle/Venmo received — NOT used for credit card payment receipts
- cc_payment: payments FROM a bank account TO a credit card company — AUTOPAY, "PAYMENT - THANK YOU", "AMEX PAYMENT", "CHASE PAYMENT", "CITI AUTOPAY", "DISCOVER PAYMENT", etc. Use cc_payment, NOT meals/shopping/other_expense
- cc_payment_received: payment RECEIVED by a credit card account from a bank (the other side of cc_payment). On a credit card statement, these appear as credits that reduce the balance — "PAYMENT THANK YOU", "AUTOPAY PAYMENT", "ONLINE PAYMENT", etc. Always positive. Use cc_payment_received, NOT transfers_in or other_income.
- bank_charges: overdraft fees, monthly maintenance fees, ATM fees, wire transfer fees, NSF fees
- telecom: phone bills, internet service, cable/satellite TV
- office_expenses: office supplies, printer ink, software licenses, coworking space
- professional_services: lawyers, accountants, consultants, freelancers you paid
- payroll: employee wages, contractor payments from a business account
- taxes: tax payments to IRS, state, estimated quarterly taxes
- subscriptions: streaming services, SaaS software, recurring membership fees{bank_hint}
{few_shot_block}
Rules:
- Parse ALL transactions, do not skip any
- Normalize dates to ISO 8601 format (YYYY-MM-DD). If only month/day shown, infer the year from context
- Amount sign: debits/withdrawals/payments/expenses are NEGATIVE; credits/deposits/income are POSITIVE
- Do NOT include running balances, opening/closing balances, or subtotals — only individual transactions
- If this is a CREDIT CARD statement (shows a credit limit, minimum payment due, available credit, or individual merchant charges), set account_type to "credit_card". Otherwise set it to null.

Return ONLY a JSON object with exactly three keys — no markdown fences, no explanation:
{{
  "account_suffix": "<last 4 digits of account number shown on the statement, or null if not found>",
  "account_type": "<credit_card if this is a credit card statement, otherwise null>",
  "transactions": [ ... array of transaction objects ... ]
}}

Bank statement text:
{raw_text}"""


def categorize_transactions(raw_text: str, bank_hint: Optional[str] = None,
                             few_shot_examples: Optional[list] = None) -> tuple:
    """
    Returns (transactions_list, account_suffix_or_None, account_type_or_None).
    account_type will be "credit_card" if Claude detected a CC statement, else None.
    """
    client = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    hint_str = f"\n- Bank: {bank_hint}" if bank_hint else ""
    few_shot_block = _build_few_shot_block(few_shot_examples or [])

    prompt = PROMPT_TEMPLATE.format(
        raw_text=raw_text,
        bank_hint=hint_str,
        few_shot_block=few_shot_block
    )

    response = _call_with_retry(client, prompt)
    raw_response = response.content[0].text.strip()
    return _parse_response(raw_response)


def _build_few_shot_block(examples: list) -> str:
    if not examples:
        return ""
    lines = []
    for ex in examples[:25]:
        lines.append(f'  - "{ex["description"]}" -> {ex["category"]}')
    return "\nExamples from your correction history (use these as learned patterns):\n" + "\n".join(lines) + "\n"


def _call_with_retry(client: Anthropic, prompt: str, max_retries: int = 2):
    for attempt in range(max_retries + 1):
        try:
            return client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=8192,
                messages=[{"role": "user", "content": prompt}]
            )
        except Exception as e:
            err_str = str(e)
            if "429" in err_str or "rate_limit" in err_str.lower():
                wait = 60
            elif "529" in err_str or "overload" in err_str.lower() or "500" in err_str:
                wait = 30
            else:
                raise
            if attempt < max_retries:
                print(f"[claude_categorizer] API error, retrying in {wait}s...")
                time.sleep(wait)
            else:
                raise


def _parse_response(raw_response: str) -> tuple:
    """
    Returns (transactions_list, account_suffix_or_None, account_type_or_None).
    account_type will only be "credit_card" (auto-detected) or None.
    Handles both the new object format and a bare list (backwards compat).
    """
    text = raw_response
    if text.startswith("```"):
        lines = text.split("\n")
        text = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
    text = text.strip()

    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        debug_path = os.path.join(
            os.path.dirname(os.path.dirname(__file__)), ".tmp",
            f"debug_claude_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.txt"
        )
        os.makedirs(os.path.dirname(debug_path), exist_ok=True)
        with open(debug_path, "w") as f:
            f.write(raw_response)
        raise ValueError(f"Claude returned invalid JSON. Saved to {debug_path}. Error: {e}")

    # Support both formats
    if isinstance(data, list):
        raw_transactions = data
        account_suffix = None
        account_type = None
    elif isinstance(data, dict):
        raw_transactions = data.get("transactions", [])
        account_suffix = data.get("account_suffix")
        if account_suffix is not None:
            account_suffix = str(account_suffix).strip()[-4:] or None
        # Only trust "credit_card" from Claude — personal/business are user-chosen
        raw_account_type = data.get("account_type")
        account_type = "credit_card" if raw_account_type == "credit_card" else None
    else:
        raise ValueError(f"Unexpected Claude response type: {type(data)}")

    validated = []
    for i, tx in enumerate(raw_transactions):
        if not isinstance(tx, dict):
            continue
        category = tx.get("category", "other_expense")
        if category not in VALID_CATEGORIES:
            category = "other_expense" if float(tx.get("amount", -1)) < 0 else "other_income"
        validated.append({
            "date": str(tx.get("date", "")),
            "description": str(tx.get("description", f"Transaction {i + 1}")),
            "amount": float(tx.get("amount", 0.0)),
            "category": category,
            "raw_text": str(tx.get("description", ""))
        })

    return (validated, account_suffix, account_type)

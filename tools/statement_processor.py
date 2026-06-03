"""
Orchestrator: PDF file → extract text → Claude categorization → SQLite
"""
import os
import sys

from tools.pdf_extractor import extract_text, extract_account_suffix
from tools.claude_categorizer import categorize_transactions
from tools import db_manager


def _get_account_type(statement_id: int) -> str:
    """Look up the account_type for a statement row."""
    with db_manager.get_conn() as conn:
        row = conn.execute("SELECT account_type FROM statements WHERE id = ?", (statement_id,)).fetchone()
    return row["account_type"] if row else "personal"


def process_statement(pdf_path: str, statement_id: int, bank_hint: str = None) -> dict:
    """
    Full pipeline for a single PDF statement.
    Returns {'status': 'done', 'transaction_count': N, 'month': ..., 'account_suffix': ...}
    or {'status': 'error', 'message': str}
    """
    try:
        db_manager.update_statement_status(statement_id, "processing")

        # Step 1: Extract raw text from PDF
        print(f"[processor] Extracting text from {os.path.basename(pdf_path)}...")
        raw_text = extract_text(pdf_path)
        print(f"[processor] Extracted {len(raw_text)} characters")

        # Step 2: Preserve manual overrides from any previous processing run
        manual_overrides = db_manager.get_manual_overrides(statement_id)
        if manual_overrides:
            print(f"[processor] Preserving {len(manual_overrides)} manual overrides")

        # Step 2.5: Fetch global manual corrections for few-shot learning
        few_shot_examples = db_manager.get_global_manual_corrections(limit=30)
        if few_shot_examples:
            print(f"[processor] Injecting {len(few_shot_examples)} few-shot examples into Claude prompt")

        # Step 2.8: Try regex extraction of account suffix before calling Claude
        regex_suffix = extract_account_suffix(pdf_path)

        # Step 3: Send to Claude — returns (transactions, account_suffix, account_type)
        print(f"[processor] Sending to Claude...")
        transactions, claude_suffix, claude_account_type = categorize_transactions(
            raw_text, bank_hint=bank_hint, few_shot_examples=few_shot_examples
        )
        # Prefer regex (more reliable), fall back to Claude's extraction
        account_suffix = regex_suffix or claude_suffix
        print(f"[processor] Claude returned {len(transactions)} transactions. suffix: regex={regex_suffix}, claude={claude_suffix}, using={account_suffix}, detected_type={claude_account_type}")

        if not transactions:
            db_manager.update_statement_status(statement_id, "error")
            return {"status": "error", "message": "No transactions found in the PDF."}

        # Log CC detection for informational purposes (account_type stays as user chose)
        if claude_account_type == "credit_card":
            print(f"[processor] Detected credit card statement — cc_payment_received will be used for payment entries")

        # Step 4: Persist to database — derive month first to enable deduplication check
        # Insert transactions into the placeholder statement so we can derive the month
        db_manager.insert_transactions(statement_id, transactions, manual_overrides)
        month = db_manager.derive_statement_month(statement_id)

        # Deduplication: if another statement with the same suffix+month+account_type already
        # exists, merge into it and delete this placeholder to avoid duplicate account rows.
        if account_suffix and month:
            existing = db_manager.find_statement(account_suffix, month, _get_account_type(statement_id))
            if existing and existing["id"] != statement_id:
                print(f"[processor] Duplicate detected — merging into existing statement {existing['id']} (suffix={account_suffix}, month={month})")
                db_manager.insert_transactions(existing["id"], transactions, manual_overrides)
                db_manager.update_statement_status(existing["id"], "done", month=month)
                db_manager.update_statement_account_suffix(existing["id"], account_suffix)
                # Remove the redundant placeholder row created for this upload
                db_manager.delete_statement(statement_id)
                statement_id = existing["id"]
                print(f"[processor] Done (merged). {len(transactions)} transactions, month={month}, suffix={account_suffix}")
                return {
                    "status": "done",
                    "transaction_count": len(transactions),
                    "month": month,
                    "account_suffix": account_suffix,
                    "account_type": claude_account_type or None,
                    "merged_into": existing["id"],
                }

        # Step 5: Save metadata for new (non-duplicate) statement
        db_manager.update_statement_status(statement_id, "done", month=month)

        if account_suffix:
            db_manager.update_statement_account_suffix(statement_id, account_suffix)

        print(f"[processor] Done. {len(transactions)} transactions, month={month}, suffix={account_suffix}")
        return {
            "status": "done",
            "transaction_count": len(transactions),
            "month": month,
            "account_suffix": account_suffix,
            "account_type": claude_account_type or None
        }

    except Exception as e:
        error_msg = str(e)
        print(f"[processor] Error: {error_msg}", file=sys.stderr)
        db_manager.update_statement_status(statement_id, "error")
        return {"status": "error", "message": error_msg}

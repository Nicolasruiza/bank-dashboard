# Bank Statement P&L Dashboard — Claude Code Guide

## Project Overview
A Flask web app that ingests bank statement PDFs, uses Claude AI to extract and categorize transactions, stores them in SQLite, and renders a P&L dashboard with charts and insights.

**Run:** `python3 app.py` → `http://localhost:8080`

## Architecture

```
app.py                          Flask routes (API + page serving)
tools/
  pdf_extractor.py              PDF text extraction (pdfplumber → PyMuPDF fallback)
  statement_processor.py        Upload pipeline orchestrator
  claude_categorizer.py         Claude API call + JSON parsing
  db_manager.py                 All SQLite queries
  insights_engine.py            Computed insights (subscriptions, MoM changes, etc.)
static/app.js                   All frontend logic (vanilla JS, Chart.js)
templates/dashboard.html        Single-page UI
bank_statements.db              SQLite database (gitignored)
.tmp/                           Uploaded PDFs + Claude debug dumps
```

## Pipeline: Upload → DB
1. `pdf_extractor.extract_text()` — pdfplumber first, PyMuPDF fallback
2. `pdf_extractor.extract_account_suffix()` — regex against raw text (preferred)
3. `claude_categorizer.categorize_transactions()` — one API call, batches all transactions; also returns `account_suffix` as fallback
4. `db_manager.insert_transactions()` — preserves any existing manual category overrides
5. `db_manager.derive_statement_month()` — inferred from most common transaction date

Regex suffix takes priority over Claude's suffix extraction (`regex_suffix or claude_suffix`).

## Key Data Model
- `statements`: `id, filename, bank_name, uploaded_at, month, status, account_type (personal|business), account_suffix`
- `transactions`: `id, statement_id, date, description, amount, category, category_source (ai|manual), raw_text`
- `categories`: `id, name, type (expense|income)` — seeded from hardcoded lists in `db_manager.py`

Internal categories (excluded when "Exclude Internal" toggled): `transfers_out`, `transfers_in`, `cc_payment`

## Account Suffix Extraction — Known Formats
Scotiabank statements use: `Account# 4538 XXXX XXXX 9016` (spaced masked format)
Patterns added to handle this:
- `r'[Aa]ccount\s*#\s+\d{4}(?:\s+[X*•\-]+)+\s+(\d{4})\b'`
- `r'(?:[X*•]+\s+){2,}(\d{4})\b'`

If a new bank's suffix isn't detected, run `python3 tools/pdf_extractor.py <file.pdf>` to see raw extracted text, then add a matching pattern to `extract_account_suffix()` in `pdf_extractor.py`.

## Claude API Usage
- Model: `claude-sonnet-4-6`
- One call per PDF, `max_tokens=8192`
- Prompt injects: bank hint, few-shot examples from user's past manual corrections (top 30)
- Returns JSON: `{ "account_suffix": "...", "transactions": [...] }`
- Debug dumps on JSON parse failure saved to `.tmp/debug_claude_*.txt`
- Retry logic: 429/rate-limit → 60s wait; 500/529/overload → 30s wait; max 2 retries

## Frontend
- No framework — vanilla JS in `static/app.js`
- State: `currentAccountType`, `currentMonth`, `currentAccountSuffix`, `excludeInternal`
- All API calls go through `apiParams()` which injects current filter state
- Charts: Chart.js (donut for expense breakdown, bar for 6-month trend)

## Adding Categories
Categories are seeded in `db_manager.py` (`EXPENSE_CATEGORIES` / `INCOME_CATEGORIES`). New categories can be added via the UI or by adding to those lists. The `VALID_CATEGORIES` list in `claude_categorizer.py` must be kept in sync.

## Common Tasks
- **Re-process a statement:** Delete it via Manage Statements modal, re-upload
- **Fix wrong suffix:** "Edit #" button in Manage Statements modal
- **Fix wrong account type:** "→ Business / → Personal" toggle in Manage Statements modal
- **Debug Claude output:** Check `.tmp/debug_claude_*.txt` if upload fails with JSON error

# CLAUDE.md - Token Efficient Rules

1. Think before acting. Read existing files before writing code.
2. Be concise in output but thorough in reasoning.
3. Prefer editing over rewriting whole files.
4. Do not re-read files you have already read unless the file may have changed.
5. Test your code before declaring done.
6. No sycophantic openers or closing fluff.
7. Keep solutions simple and direct.
8. User instructions always override this file.
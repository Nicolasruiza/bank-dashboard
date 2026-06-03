# Bank Statement Categorizer — Workflow SOP

## Objective
Process bank statement PDFs to extract transactions, categorize them using Claude AI,
persist to SQLite, and display as monthly P&L in a local web dashboard.

## Inputs Required
- PDF bank statement file (any bank)
- `ANTHROPIC_API_KEY` set in `.env`

## Tools Used (in order)
1. `tools/pdf_extractor.py` — converts PDF pages to raw text
2. `tools/claude_categorizer.py` — sends text to Claude, returns structured transactions
3. `tools/db_manager.py` — persists to SQLite
4. `tools/statement_processor.py` — orchestrates steps 1–3

## Running the App
```bash
cd /Users/nicolasruiz/Documents/New\ Agentic
pip install -r requirements.txt
python app.py
# Open http://localhost:5000
```

## Standard Execution Flow
1. User opens `http://localhost:5000` and clicks **Upload Statement**
2. User selects a PDF file and optionally types the bank name
3. `app.py` saves the file to `.tmp/`, creates a statement DB record (`status=pending`)
4. `statement_processor.py` runs:
   - `pdf_extractor.py` extracts raw text (pdfplumber → PyMuPDF fallback)
   - `claude_categorizer.py` sends text to Claude in a single API call
   - `db_manager.py` inserts all transactions, updates statement `status=done`
5. Browser auto-refreshes to show the new month's P&L

## Categories

### Expenses
`meals`, `groceries`, `rent`, `utilities`, `subscriptions`, `transport`,
`healthcare`, `education`, `entertainment`, `fees`, `insurance`, `shopping`, `other_expense`

### Income
`salary`, `dividends`, `transfers_in`, `interest`, `refunds`, `other_income`

## Manual Category Override
- In the transaction table, change the category dropdown for any row
- This fires an immediate API call and marks `category_source = 'manual'`
- Manual overrides survive re-uploads of the same statement

## Error Handling

### PDF extraction returns empty text
- **Cause:** Scanned/image-based PDF — pdfplumber can't extract text
- **Action:** App tries PyMuPDF as fallback; if still empty, statement is marked `error`
- **Note:** True image PDFs require OCR (not currently supported). Use your bank's text-based export instead.

### Claude API errors
- `429 rate_limit` → waits 60 seconds, retries once
- `529 overload / 500` → waits 30 seconds, retries up to 2×
- JSON parse failure → raw Claude response saved to `.tmp/debug_claude_<timestamp>.txt`; statement marked `error`

### No transactions found
- Claude returned an empty array from the extracted text
- Usually means the PDF is a summary page, not a transaction list
- Try a different page range or a full statement export

### Wrong amounts or dates
- Claude infers the year from context; if the PDF shows only month/day, provide the bank name hint
- Amount sign errors (expense showing as income) → manually re-categorize in the transaction table

## Cost Notes
- One PDF upload = **one Claude API call** regardless of transaction count
- Typical 1-month statement (~80 transactions): ~2,000 input + ~1,500 output tokens
- Estimated cost: **$0.01–0.02 per upload** at claude-sonnet-4-6 pricing
- To reduce cost: change `model="claude-sonnet-4-6"` to `model="claude-haiku-4-5-20251001"` in `tools/claude_categorizer.py`

## Adding New Categories
1. Insert into `categories` table: `INSERT INTO categories (name, type) VALUES ('new_cat', 'expense');`
2. Add `'new_cat'` to `VALID_CATEGORIES` in `tools/claude_categorizer.py`
3. Add `'new_cat'` to the prompt's category list in `tools/claude_categorizer.py`
4. Restart `app.py`

## Known Bank Quirks
- **Chase:** dates show as `MM/DD` with no year — Claude infers from statement context; provide bank hint "Chase"
- **Bank of America:** running balance column can confuse amount parsing — provide hint "Bank of America"
- *(Add new quirks here as discovered)*

## Self-Improvement Log
| Date | What broke | How fixed | What changed |
|------|-----------|-----------|--------------|
| *(add entries here)* | | | |

# Portfolio & P&L Feature Additions — Design Spec

**Date:** 2026-04-11

---

## Context

Seven feature additions to the existing Flask bank statement P&L dashboard:

**Portfolio:**
1. Auto-detect ticker currency (no manual currency picker)
2. Investment accounts (TFSA/RRSP/etc.) with nicknames
3. Assign holdings to accounts
4. Holdings table grouped by account with subtotals

**P&L:**
5. Sortable columns on all tables (client-side)
6. Full-view income/expense filter (category table + donut + transaction list)
7. Category row click → transaction drill-down modal with inline editing (replaces vendor modal)

---

## Critical Files

| File | Changes |
|------|---------|
| `tools/portfolio_manager.py` | add_holding currency auto-detect, account CRUD functions, grouped summary |
| `tools/db_manager.py` | no changes (portfolio tables managed in portfolio_manager.py) |
| `app.py` | new account endpoints, updated holdings POST/PATCH, updated pnl/transactions endpoints for type filter |
| `templates/dashboard.html` | Manage Accounts modal, updated Add Holding form, income/expense toggle, category modal |
| `static/app.js` | all frontend logic for 7 features |
| `static/style.css` | account group headers, subtotal rows, sort indicators, filter toggle |

---

## Portfolio Design

### 1. Auto-detect Ticker Currency

**Backend (`tools/portfolio_manager.py`):**
- New function `get_ticker_info(ticker: str) -> dict`:
  ```python
  info = yf.Ticker(ticker).fast_info
  return {"currency": info.currency, "name": getattr(info, "company_name", ticker)}
  ```
  Returns `{"currency": "USD"|"CAD"|..., "name": "..."}` or raises on invalid ticker.
- New Flask endpoint `GET /api/portfolio/ticker-info?ticker=AAPL`
  Returns `{currency, name}` or `{error}` on failure.

**Frontend Add Holding form:**
- Remove the "Price Currency" radio group entirely
- When the user finishes typing a ticker and tabs/blurs away, call `/api/portfolio/ticker-info`
- Show a subtle status line: `Detected: CAD · VGRO.TO — iShares Core...`
- Store detected currency in a hidden field; submit with the form
- If detection fails (invalid ticker), show error and block submission

### 2. Investment Accounts

**New DB table** (created in `portfolio_manager.py` `init_portfolio_db()`):
```sql
CREATE TABLE IF NOT EXISTS investment_accounts (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    type       TEXT NOT NULL,
    nickname   TEXT NOT NULL,
    created_at TEXT NOT NULL
);
```
Valid types: `TFSA`, `RRSP`, `RRIF`, `RESP`, `FHSA`, `Non-Registered`

**Schema migration** on `portfolio_holdings`:
```sql
ALTER TABLE portfolio_holdings ADD COLUMN account_id INTEGER REFERENCES investment_accounts(id);
```
Existing holdings: `account_id = NULL` (shown as "Unassigned").

**New functions in `portfolio_manager.py`:**
- `get_all_accounts() -> list`
- `create_account(type: str, nickname: str) -> dict`
- `update_account(account_id: int, nickname: str) -> dict`
- `delete_account(account_id: int)` — raises if holdings are assigned
- `assign_holding_account(holding_id: int, account_id: Optional[int])`

**New Flask endpoints (`app.py`):**
- `GET /api/portfolio/accounts`
- `POST /api/portfolio/accounts` — body: `{type, nickname}`
- `PATCH /api/portfolio/accounts/<id>` — body: `{nickname}`
- `DELETE /api/portfolio/accounts/<id>`

**Updated Flask endpoints:**
- `POST /api/portfolio/holdings` — accepts optional `account_id`
- `PATCH /api/portfolio/holdings/<id>` — accepts `account_id` to reassign

### 3. Holdings Grouped by Account

**`get_portfolio_summary()`** returns holdings sorted by `account_id` (NULLs last).

**Frontend holdings table** groups rows by account:
- Account header row: dark surface, spans all columns, shows `[Type Badge] Nickname` + account subtotals (cost, value, GL) on the right
- Holding rows for that account
- "Unassigned" group at the bottom for `account_id = NULL` holdings
- Inline account dropdown on each holding row for quick reassignment

**Account dropdown in Add Holding form:** populated from `/api/portfolio/accounts`. Default: `— Unassigned —`.

**"Manage Accounts" button** in portfolio header → modal:
- Lists accounts: type badge, nickname, holding count
- "+ New Account" inline form: type `<select>` + nickname `<input>`
- Rename button (inline edit of nickname)
- Delete button (disabled if holdings assigned, shows tooltip)

---

## P&L Design

### 4. Sortable Columns

Client-side sort — no API changes needed.

**State per table:**
```javascript
const _sort = {
  category: { col: null, dir: 1 },  // dir: 1=asc, -1=desc
  transactions: { col: null, dir: 1 }
};
```

**Implementation:**
- `<th>` elements get `data-sort="fieldName"` and `onclick="sortTable('category', 'amount')"` 
- Active column gets `▲` or `▼` indicator via CSS class
- `sortTable(table, col)` toggles direction if same col, resets to asc if new col, then re-renders

**Tables affected:** category breakdown, transaction list, manage statements table.

**Sort fields:**
- Category table: category name (A-Z), amount (abs), count, pct
- Transaction table: date, description, amount
- Manage statements: filename, month, account type

### 5. Income/Expense Filter

**State:** `let txTypeFilter = "all"` — `"all"` | `"income"` | `"expense"`

**UI:** Three pill buttons above the category table:
`[All]  [Income ▲]  [Expenses ▼]`

**Effect on:**
- Category table: hides rows whose `type` doesn't match filter
- Donut chart: re-renders with only matching categories (income chart would show income categories)
- Transaction list: filters `allTransactions` by sign — income = `amount > 0`, expense = `amount < 0`

**Donut chart for income:** shows income categories in the existing color palette (currently only expenses shown). When filter = "income", donut shows income breakdown instead.

**API:** No changes — filtering is entirely client-side on already-loaded data.

### 6. Category Transaction Drill-Down Modal (replaces vendor modal)

**What changes:** clicking a category row no longer calls `openVendorModal()` — instead calls `openCategoryModal(category, type)`.

**New modal `#categoryModal`:**
- Title: `[Category Label] — [Month]`
- Subtitle: `N transactions · Total: $X`
- Table columns: Date | Description | Amount | Category (dropdown) | Source (AI/Manual badge)
- Category dropdown is the same `<select class="cat-select">` with `overrideCategory()` — **reuses existing inline editing logic entirely**
- Search box to filter within the modal
- Scroll within modal (max-height: 70vh)

**Data source:** calls existing `/api/transactions` and filters client-side by category — no new endpoint needed. The full transaction list is already loaded into `allTransactions`.

**No vendor "top vendors" list** — removed entirely. The drill-down is now the transaction list itself.

---

## Verification

1. Add a ticker (e.g., `AAPL`) — confirm no currency picker, currency auto-detected as USD
2. Create a TFSA account with nickname "My TFSA" — appears in accounts list and Add Holding dropdown
3. Add a holding to "My TFSA" — table shows it under TFSA group with correct subtotals
4. Reassign a holding to a different account via the inline dropdown — table regroups
5. Click column headers in category table — rows sort correctly, indicator flips
6. Click "Expenses" filter — donut chart shows only expenses, transaction list shows only negative amounts
7. Click a category row — modal opens with that category's transactions, inline category dropdown works and saves

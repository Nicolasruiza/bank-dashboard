/* ── Theme ───────────────────────────────────────────────────────────────────── */
function initTheme() {
  const saved = localStorage.getItem("theme");
  if (saved) {
    document.documentElement.setAttribute("data-theme", saved);
  }
  updateThemeIcon();
}

function toggleTheme() {
  const current = document.documentElement.getAttribute("data-theme");
  const systemDark = window.matchMedia("(prefers-color-scheme: dark)").matches;
  const isDark = current === "dark" || (!current && systemDark);
  const next = isDark ? "light" : "dark";
  document.documentElement.setAttribute("data-theme", next);
  localStorage.setItem("theme", next);
  updateThemeIcon();
}

function updateThemeIcon() {
  const el = document.getElementById("themeIcon");
  if (!el) return;
  const current = document.documentElement.getAttribute("data-theme");
  const systemDark = window.matchMedia("(prefers-color-scheme: dark)").matches;
  const isDark = current === "dark" || (!current && systemDark);
  el.textContent = isDark ? "☀️" : "🌙";
}

initTheme();

/* ── State ───────────────────────────────────────────────────────────────────── */
let allTransactions = [];
let filteredTransactions = [];
let currentPage = 1;
const PAGE_SIZE = 50;
let allCategories = [];
let donutChart = null;
let barChart = null;
let budgetChart = null;
let currentAccountType = "personal";
let currentMonth = "";
let currentAccountSuffix = null;
let excludeInternal = true;
let advisorCache = null;
let portfolioHoldings = [];

// Category trend expand state
const _expandedTrend = { category: null, type: null, chart: null, cache: {} };

// Sort state
const _sort = {
  category:     { col: null, dir: 1 },
  transactions: { col: null, dir: 1 },
  manage:       { col: null, dir: 1 },
};

// Income/expense filter — purely client-side, no re-fetch
let txTypeFilter = "all"; // "all" | "income" | "expense"

// Last loaded PNL data — used for client-side re-renders without re-fetching
let _lastPnlData = null;

const INTERNAL_CATEGORIES = ["transfers_out", "transfers_in", "cc_payment", "cc_payment_received"];

/* ── URL param helper ────────────────────────────────────────────────────────── */
function apiParams(extra = {}) {
  const p = new URLSearchParams({ account_type: currentAccountType });
  if (currentAccountSuffix) p.set("account_suffix", currentAccountSuffix);
  if (excludeInternal) p.set("exclude_internal", "true");
  Object.entries(extra).forEach(([k, v]) => { if (v != null) p.set(k, v); });
  return p.toString();
}

/* ── Init ────────────────────────────────────────────────────────────────────── */
document.addEventListener("DOMContentLoaded", async () => {
  // Restore exclude-internal preference (default: true — show True P&L)
  excludeInternal = localStorage.getItem("excludeInternal") !== "false";
  document.getElementById("excludeInternalToggle").checked = excludeInternal;

  await loadCategories();
  await loadMonths();
  await loadAccountSuffixes();
  loadTrendChart();
  loadNetWorth();
  // Show budget toggle for default personal tab
  document.getElementById("pnlBudgetToggle").style.display = "flex";
});

/* ── Account Type Tabs ───────────────────────────────────────────────────────── */
async function switchAccountType(type) {
  currentAccountType = type;
  currentAccountSuffix = null;
  currentMonth = "";
  _clearTrendCache();
  document.querySelectorAll(".tab-btn").forEach(btn => btn.classList.toggle("active", btn.dataset.type === type));
  // Hide portfolio view, show P&L view
  document.getElementById("portfolioView").style.display = "none";
  document.getElementById("pnlView").style.display = "block";
  clearDashboard();
  await loadMonths();
  await loadAccountSuffixes();
  loadTrendChart();
  // Show budget toggle only for personal
  document.getElementById("pnlBudgetToggle").style.display = (type === "personal") ? "flex" : "none";
}

function clearDashboard() {
  document.getElementById("cardIncome").textContent = "—";
  document.getElementById("cardExpenses").textContent = "—";
  document.getElementById("cardNet").textContent = "—";
  document.getElementById("cardNetWrap").classList.remove("positive", "negative");
  document.getElementById("adjustedSummary").style.display = "none";
  document.getElementById("categoryBody").innerHTML = "";
  document.getElementById("txBody").innerHTML = "";
  document.getElementById("txCount").textContent = "";
  document.getElementById("pagination").innerHTML = "";
  document.getElementById("insightsSection").style.display = "none";
  if (donutChart) { donutChart.destroy(); donutChart = null; }
}

/* ── Categories ──────────────────────────────────────────────────────────────── */
async function loadCategories() {
  const res = await fetch("/api/categories");
  allCategories = await res.json();
}

const SUBTYPE_LABELS = { chequing: "Chequing", savings: "Savings", credit_card: "Credit Card" };

/* ── Account Suffixes ────────────────────────────────────────────────────────── */
async function loadAccountSuffixes() {
  const res = await fetch(`/api/account-suffixes?account_type=${currentAccountType}`);
  const accounts = await res.json(); // [{suffix, subtype}, ...]
  const sel = document.getElementById("accountSuffixSelect");
  sel.innerHTML = '<option value="">All Accounts</option>';
  const suffixList = accounts.map(a => a.suffix);
  accounts.forEach(a => {
    const opt = document.createElement("option");
    opt.value = a.suffix;
    const label = SUBTYPE_LABELS[a.subtype] ? ` (${SUBTYPE_LABELS[a.subtype]})` : "";
    opt.textContent = `••${a.suffix}${label}`;
    sel.appendChild(opt);
  });
  if (currentAccountSuffix && suffixList.includes(currentAccountSuffix)) {
    sel.value = currentAccountSuffix;
  } else {
    currentAccountSuffix = null;
    sel.value = "";
  }
}

function onAccountSuffixChange() {
  currentAccountSuffix = document.getElementById("accountSuffixSelect").value || null;
  _clearTrendCache();
  loadMonths();
}

/* ── Months ──────────────────────────────────────────────────────────────────── */
async function loadMonths() {
  const res = await fetch("/api/months?" + apiParams());
  const months = await res.json();
  const sel = document.getElementById("monthSelect");
  sel.innerHTML = '<option value="">— select a month —</option>';
  months.forEach(m => {
    const opt = document.createElement("option");
    opt.value = m;
    opt.textContent = formatMonth(m);
    sel.appendChild(opt);
  });
  if (months.length > 0) {
    sel.value = months[0];
    currentMonth = months[0];
    await onMonthChange();
  }
}

/* ── Month Change ────────────────────────────────────────────────────────────── */
async function onMonthChange() {
  const month = document.getElementById("monthSelect").value;
  if (!month) return;
  currentMonth = month;
  document.getElementById("monthStatus").textContent = "Loading...";
  const tasks = [loadPnl(month), loadTransactions(month), loadInsights(month)];
  if (currentAccountType === "personal") tasks.push(loadSpendingDna());
  await Promise.all(tasks);
  document.getElementById("monthStatus").textContent = "";
  // Refresh budget view if it's visible
  if (document.getElementById("budgetView").style.display !== "none") {
    loadBudgetAnalysis(month);
  }
}

/* ── Exclude Internal Toggle ─────────────────────────────────────────────────── */
function onExcludeInternalChange() {
  excludeInternal = document.getElementById("excludeInternalToggle").checked;
  localStorage.setItem("excludeInternal", excludeInternal);
  if (currentMonth) {
    loadPnl(currentMonth);
    loadTrendChart();
  }
}

/* ── P&L Summary ─────────────────────────────────────────────────────────────── */
async function loadPnl(month) {
  const res = await fetch("/api/pnl?" + apiParams({ month }));
  const data = await res.json();

  const netCard = document.getElementById("cardNetWrap");
  netCard.classList.remove("positive", "negative");

  const adjDiv = document.getElementById("adjustedSummary");

  if (excludeInternal && data.adjusted_income != null) {
    // Primary cards show True P&L (adjusted, excluding internal transfers)
    document.getElementById("cardIncome").textContent = fmt(data.adjusted_income);
    document.getElementById("cardExpenses").textContent = fmt(Math.abs(data.adjusted_expenses));
    document.getElementById("cardNet").textContent = fmt(data.adjusted_net);
    netCard.classList.add(data.adjusted_net >= 0 ? "positive" : "negative");

    // Secondary banner shows raw totals for reference
    document.getElementById("adjIncome").textContent = fmt(data.total_income);
    document.getElementById("adjExpenses").textContent = fmt(Math.abs(data.total_expenses));
    const adjNetEl = document.getElementById("adjNet");
    adjNetEl.textContent = fmt(data.net);
    adjNetEl.className = data.net >= 0 ? "amount-pos" : "amount-neg";
    adjDiv.style.display = "block";
  } else {
    // Primary cards show raw totals
    document.getElementById("cardIncome").textContent = fmt(data.total_income);
    document.getElementById("cardExpenses").textContent = fmt(Math.abs(data.total_expenses));
    document.getElementById("cardNet").textContent = fmt(data.net);
    netCard.classList.add(data.net >= 0 ? "positive" : "negative");
    adjDiv.style.display = "none";
  }

  _lastPnlData = data;
  renderCategoryTable(data.by_category, data.total_income, data.total_expenses);
  renderDonutChart(data.by_category);
}

/* ── Category Table ──────────────────────────────────────────────────────────── */
function renderCategoryTable(byCategory, totalIncome, totalExpenses) {
  const tbody = document.getElementById("categoryBody");
  tbody.innerHTML = "";
  const totalAbs = Math.abs(totalIncome) + Math.abs(totalExpenses);

  if (!byCategory || byCategory.length === 0) {
    tbody.innerHTML = `<tr><td colspan="5" class="empty-state">No data for this month</td></tr>`;
    return;
  }

  // Apply income/expense filter
  let rows = byCategory;
  if (txTypeFilter === "income") rows = rows.filter(r => r.type === "income");
  else if (txTypeFilter === "expense") rows = rows.filter(r => r.type === "expense");

  // Apply sort
  const s = _sort.category;
  if (s.col) {
    rows = [...rows].sort((a, b) => {
      let av, bv;
      if (s.col === "category") { av = labelFor(a.category); bv = labelFor(b.category); return s.dir * av.localeCompare(bv); }
      if (s.col === "amount")   { av = Math.abs(a.total);    bv = Math.abs(b.total); }
      if (s.col === "count")    { av = a.count;               bv = b.count; }
      if (s.col === "pct")      { av = Math.abs(a.total);    bv = Math.abs(b.total); } // same as amount
      return s.dir * (av - bv);
    });
  }

  rows.forEach(row => {
    const pct = totalAbs > 0 ? Math.abs(row.total / totalAbs * 100).toFixed(1) : "0.0";
    const amtClass = row.total >= 0 ? "amount-pos" : "amount-neg";
    const typeBadge = row.type === "income"
      ? '<span class="badge badge-income">Income</span>'
      : '<span class="badge badge-expense">Expense</span>';
    const internalBadge = INTERNAL_CATEGORIES.includes(row.category)
      ? ' <span class="badge badge-internal">Internal</span>' : "";

    // Main category row
    const tr = document.createElement("tr");
    tr.className = "clickable-row";
    tr.dataset.category = row.category;
    tr.innerHTML = `
      <td><button class="expand-btn" title="Show monthly trend" onclick="event.stopPropagation();toggleCategoryTrend('${row.category}','${row.type}',this.closest('tr'))">▶</button></td>
      <td>${labelFor(row.category)}${internalBadge}</td>
      <td>${typeBadge}</td>
      <td class="num ${amtClass}">${fmt(row.total)}</td>
      <td class="num">${row.count}</td>
      <td class="num">${pct}%</td>
    `;
    tr.onclick = () => openCategoryModal(row.category, row.type);
    tbody.appendChild(tr);

    // Hidden trend row
    const trendTr = document.createElement("tr");
    trendTr.className = "trend-row";
    trendTr.dataset.category = row.category;
    trendTr.style.display = "none";
    trendTr.innerHTML = `<td colspan="6" class="trend-cell"><canvas class="trend-canvas"></canvas></td>`;
    tbody.appendChild(trendTr);
  });
}

/* ── Sort & Filter ───────────────────────────────────────────────────────────── */
function sortTable(table, col) {
  const state = _sort[table];
  if (state.col === col) {
    state.dir *= -1;
  } else {
    state.col = col;
    state.dir = 1;
  }
  _updateSortIndicators(table);
  if (table === "category") {
    // Re-render from cached data — no fetch needed
    if (_lastPnlData) {
      renderCategoryTable(_lastPnlData.by_category, _lastPnlData.total_income, _lastPnlData.total_expenses);
    }
  } else if (table === "transactions") {
    currentPage = 1;
    renderTransactionTable();
  }
}

function _updateSortIndicators(table) {
  const state = _sort[table];
  // Clear all indicators for this table
  document.querySelectorAll(`[id^="si-${table === "category" ? "cat" : "tx"}-"]`).forEach(el => {
    el.textContent = "";
    el.closest("th")?.classList.remove("sort-active");
  });
  if (!state.col) return;
  const suffix = table === "category" ? "cat" : "tx";
  const el = document.getElementById(`si-${suffix}-${state.col}`);
  if (el) {
    el.textContent = state.dir === 1 ? " ▲" : " ▼";
    el.closest("th")?.classList.add("sort-active");
  }
}

const _typeFilterCycle = ["all", "income", "expense"];
const _typeFilterLabels = { all: "", income: " ▲ Income", expense: " ▼ Expenses" };

function cycleTypeFilter() {
  const idx = _typeFilterCycle.indexOf(txTypeFilter);
  txTypeFilter = _typeFilterCycle[(idx + 1) % _typeFilterCycle.length];
  _updateTypeFilterHeader();
  // Re-render from cached data — no fetch needed
  if (_lastPnlData) {
    renderCategoryTable(_lastPnlData.by_category, _lastPnlData.total_income, _lastPnlData.total_expenses);
    renderDonutChart(_lastPnlData.by_category);
  }
  currentPage = 1;
  filterTransactions();
}

function _updateTypeFilterHeader() {
  const th = document.getElementById("typeFilterTh");
  const indicator = document.getElementById("typeFilterIndicator");
  if (!indicator) return;
  if (txTypeFilter === "all") {
    indicator.textContent = "";
    th?.classList.remove("type-filter-active");
  } else {
    indicator.textContent = _typeFilterLabels[txTypeFilter];
    th?.classList.add("type-filter-active");
  }
}

let _lastStatements = [];  // cached for manage-table sort

function sortManage(col) {
  const s = _sort.manage;
  s.dir = s.col === col ? s.dir * -1 : 1;
  s.col = col;
  // Update indicators
  document.querySelectorAll('[id^="si-mg-"]').forEach(el => {
    el.textContent = "";
    el.closest("th")?.classList.remove("sort-active");
  });
  const el = document.getElementById(`si-mg-${col}`);
  if (el) { el.textContent = s.dir === 1 ? " ▲" : " ▼"; el.closest("th")?.classList.add("sort-active"); }
  // Re-render from cache
  _renderManageRows(_lastStatements);
}

function _renderManageRows(statements) {
  const s = _sort.manage;
  let rows = [...statements];
  if (s.col) {
    rows.sort((a, b) => {
      const av = (a[s.col] || "").toLowerCase();
      const bv = (b[s.col] || "").toLowerCase();
      return s.dir * av.localeCompare(bv);
    });
  }
  const tbody = document.getElementById("manageBody");
  if (!rows.length) {
    tbody.innerHTML = `<tr><td colspan="6" class="empty-state">No statements uploaded yet</td></tr>`; return;
  }
  tbody.innerHTML = rows.map(s => {
    const typeMap = {
      personal: { badge: '<span class="badge badge-personal">Personal</span>', next: "business", label: "→ Business" },
      business: { badge: '<span class="badge badge-business">Business</span>', next: "personal", label: "→ Personal" },
    };
    const typeInfo = typeMap[s.account_type] || typeMap["personal"];
    const subtypeLabel = SUBTYPE_LABELS[s.account_subtype] || "";
    const suffix = s.account_suffix
      ? `<span style="color:var(--text-muted)">••${s.account_suffix}</span>${subtypeLabel ? ` <span class="badge badge-cc" style="font-size:9px">${subtypeLabel}</span>` : ""}`
      : "—";
    const shortName = s.filename.length > 28 ? s.filename.slice(0, 26) + "…" : s.filename;
    const subtypeOptions = ["", "chequing", "savings", "credit_card"].map(v =>
      `<option value="${v}" ${s.account_subtype === v ? "selected" : ""}>${SUBTYPE_LABELS[v] || "— Not specified —"}</option>`
    ).join("");
    return `<tr>
      <td title="${escHtml(s.filename)}">${escHtml(shortName)}</td>
      <td>${escHtml(s.bank_name || "—")}</td>
      <td>${suffix}</td>
      <td>${s.month ? formatMonth(s.month) : "—"}</td>
      <td>${typeInfo.badge}</td>
      <td class="num" style="white-space:nowrap">
        <select class="cat-select" onchange="setStatementSubtype(${s.id},this.value)" style="margin-bottom:0">${subtypeOptions}</select>
        <button class="btn-secondary btn-sm" onclick="toggleStatementType(${s.id},'${typeInfo.next}')" style="margin-left:4px">${typeInfo.label}</button>
        <button class="btn-secondary btn-sm" onclick="editSuffix(${s.id},'${escHtml(s.account_suffix||'')}')" style="margin-left:4px">Edit #</button>
        <button class="btn-secondary btn-sm btn-danger" onclick="deleteStatementFromManage(${s.id})" style="margin-left:4px">Delete</button>
      </td>
    </tr>`;
  }).join("");
}

/* ── Category Trend Charts ───────────────────────────────────────────────────── */
async function toggleCategoryTrend(category, categoryType, rowEl) {
  const trendRow = rowEl.nextElementSibling;
  const btn = rowEl.querySelector(".expand-btn");

  // Collapse if already open
  if (_expandedTrend.category === category) {
    trendRow.style.display = "none";
    if (_expandedTrend.chart) { _expandedTrend.chart.destroy(); _expandedTrend.chart = null; }
    _expandedTrend.category = null;
    btn.textContent = "▶";
    btn.classList.remove("expand-btn-open");
    return;
  }

  // Collapse any previously open row
  if (_expandedTrend.category) {
    const prevTrend = document.querySelector(`.trend-row[data-category="${_expandedTrend.category}"]`);
    const prevBtn = document.querySelector(`tr[data-category="${_expandedTrend.category}"] .expand-btn`);
    if (prevTrend) prevTrend.style.display = "none";
    if (prevBtn) { prevBtn.textContent = "▶"; prevBtn.classList.remove("expand-btn-open"); }
    if (_expandedTrend.chart) { _expandedTrend.chart.destroy(); _expandedTrend.chart = null; }
  }

  _expandedTrend.category = category;
  _expandedTrend.type = categoryType;
  btn.textContent = "▼";
  btn.classList.add("expand-btn-open");
  trendRow.style.display = "";

  if (_expandedTrend.cache[category]) {
    _expandedTrend.chart = _renderTrendChart(category, categoryType, _expandedTrend.cache[category], trendRow);
  } else {
    const canvas = trendRow.querySelector("canvas");
    const ctx = canvas.getContext("2d");
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    canvas.parentElement.textContent = "Loading…";

    const p = new URLSearchParams({ category, account_type: currentAccountType });
    if (currentAccountSuffix) p.set("account_suffix", currentAccountSuffix);
    const res = await fetch("/api/category-trend?" + p);
    const data = await res.json();
    _expandedTrend.cache[category] = data;

    // Restore canvas (textContent cleared it)
    trendRow.querySelector("td").innerHTML = `<canvas class="trend-canvas"></canvas>`;
    _expandedTrend.chart = _renderTrendChart(category, categoryType, data, trendRow);
  }
}

function _renderTrendChart(category, categoryType, data, trendRow) {
  const canvas = trendRow.querySelector("canvas");
  const color = categoryType === "income" ? "#22c55e" : "#ef4444";
  const isDark = document.documentElement.getAttribute("data-theme") !== "light" &&
    (document.documentElement.getAttribute("data-theme") === "dark" ||
     window.matchMedia("(prefers-color-scheme: dark)").matches);
  const tickColor = isDark ? "#7c8aab" : "#6b7280";
  const gridColor = isDark ? "#2e3250" : "#e5e7eb";

  return new Chart(canvas.getContext("2d"), {
    type: "line",
    data: {
      labels: data.map(d => formatMonth(d.month)),
      datasets: [{
        label: labelFor(category),
        data: data.map(d => Math.abs(d.total)),
        borderColor: color,
        backgroundColor: color + "22",
        tension: 0.3,
        fill: true,
        pointRadius: 4,
        pointBackgroundColor: color,
      }]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: { display: false },
        tooltip: { callbacks: { label: ctx => ` ${fmt(ctx.raw)}` } }
      },
      scales: {
        x: { ticks: { color: tickColor, font: { size: 11 } }, grid: { color: gridColor } },
        y: { beginAtZero: true, ticks: { color: tickColor, callback: v => "$" + v.toLocaleString() }, grid: { color: gridColor } }
      }
    }
  });
}

// Invalidate trend cache when filters change (month/account change would show stale data)
function _clearTrendCache() {
  _expandedTrend.cache = {};
  if (_expandedTrend.chart) { _expandedTrend.chart.destroy(); _expandedTrend.chart = null; }
  _expandedTrend.category = null;
}

/* ── Donut Chart ─────────────────────────────────────────────────────────────── */
function renderDonutChart(byCategory) {
  // Respect txTypeFilter: income filter shows income breakdown, else show expenses
  const showIncome = txTypeFilter === "income";
  const filtered = showIncome
    ? (byCategory || []).filter(c => c.type === "income" && c.total > 0)
    : (byCategory || []).filter(c => c.type === "expense" && c.total < 0);
  const labels = filtered.map(c => labelFor(c.category));
  const values = filtered.map(c => Math.abs(c.total));
  const colors = generateColors(labels.length);

  const ctx = document.getElementById("donutChart").getContext("2d");
  if (donutChart) donutChart.destroy();
  if (!values.length) { ctx.clearRect(0, 0, ctx.canvas.width, ctx.canvas.height); return; }

  const isDarkDonut = document.documentElement.getAttribute("data-theme") !== "light" &&
    (document.documentElement.getAttribute("data-theme") === "dark" ||
     window.matchMedia("(prefers-color-scheme: dark)").matches);
  const donutLegendColor = isDarkDonut ? "#e2e6f0" : "#111827";
  const donutBorderColor = isDarkDonut ? "#1a1d27" : "#ffffff";

  donutChart = new Chart(ctx, {
    type: "doughnut",
    data: { labels, datasets: [{ data: values, backgroundColor: colors, borderWidth: 2, borderColor: donutBorderColor }] },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: {
        legend: { position: "right", labels: { color: donutLegendColor, font: { size: 11 }, padding: 10, boxWidth: 12 } },
        tooltip: { callbacks: { label: ctx => ` ${ctx.label}: ${fmt(ctx.raw)}` } }
      }
    }
  });
}

/* ── Bar Chart: 6-month trend (independent of month dropdown) ────────────────── */
async function loadTrendChart() {
  const res = await fetch("/api/months?" + apiParams());
  const allMonths = await res.json();
  const months = allMonths.slice(0, 6).reverse();

  const ctx = document.getElementById("barChart").getContext("2d");
  if (barChart) barChart.destroy();
  if (!months.length) { ctx.clearRect(0, 0, ctx.canvas.width, ctx.canvas.height); return; }

  const results = await Promise.all(
    months.map(m => fetch("/api/pnl?" + apiParams({ month: m })).then(r => r.json()))
  );

  const useAdj = excludeInternal && results[0].adjusted_income != null;
  const incomeData = results.map(r => useAdj ? (r.adjusted_income || 0) : (r.total_income || 0));
  const expenseData = results.map(r => useAdj ? Math.abs(r.adjusted_expenses || 0) : Math.abs(r.total_expenses || 0));
  const labels = months.map(formatMonth);

  const isDarkBar = document.documentElement.getAttribute("data-theme") !== "light" &&
    (document.documentElement.getAttribute("data-theme") === "dark" ||
     window.matchMedia("(prefers-color-scheme: dark)").matches);
  const barTickColor = isDarkBar ? "#7c8aab" : "#6b7280";
  const barGridColor = isDarkBar ? "#2e3250" : "#e5e7eb";
  const barLegendColor = isDarkBar ? "#e2e6f0" : "#111827";

  barChart = new Chart(ctx, {
    type: "bar",
    data: {
      labels,
      datasets: [
        { label: "Income", data: incomeData, backgroundColor: "rgba(34,197,94,0.7)", borderRadius: 4 },
        { label: "Expenses", data: expenseData, backgroundColor: "rgba(239,68,68,0.7)", borderRadius: 4 }
      ]
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: {
        legend: { labels: { color: barLegendColor, font: { size: 11 } } },
        tooltip: { callbacks: { label: ctx => ` ${ctx.dataset.label}: ${fmt(ctx.raw)}` } }
      },
      scales: {
        x: { ticks: { color: barTickColor }, grid: { color: barGridColor } },
        y: { ticks: { color: barTickColor, callback: v => "$" + v.toLocaleString() }, grid: { color: barGridColor } }
      }
    }
  });
}

/* ── Insights ────────────────────────────────────────────────────────────────── */
async function loadInsights(month) {
  const res = await fetch("/api/insights?" + apiParams({ month }));
  const data = await res.json();
  const section = document.getElementById("insightsSection");
  const content = document.getElementById("insightsContent");
  document.getElementById("insightsMonth").textContent = formatMonth(month);

  const s = data.sections || {};
  const cards = [];

  if (s.subscriptions?.items?.length > 0) {
    cards.push(renderInsightCard("insight-info", "💳", "Recurring Subscriptions",
      s.subscriptions.items.map(i => `<div class="insight-item">
        <span class="insight-item-name">${escHtml(i.vendor)}</span>
        <span class="insight-item-value amount-neg">${fmt(i.amount)}</span>
      </div>`).join("")
    ));
  }
  if (s.top_categories?.items?.length > 0) {
    cards.push(renderInsightCard("insight-info", "📊", "Top Expense Categories",
      s.top_categories.items.map(i => `<div class="insight-item">
        <span class="insight-item-name">${labelFor(i.category)}</span>
        <span class="insight-item-meta">${i.pct}%</span>
        <span class="insight-item-value amount-neg">${fmt(i.total)}</span>
      </div>`).join("")
    ));
  }
  if (s.mom_increases?.available && s.mom_increases?.items?.length > 0) {
    cards.push(renderInsightCard("insight-warning", "⚠️", "Month-over-Month Increases (>20%)",
      s.mom_increases.items.map(i => `<div class="insight-item">
        <span class="insight-item-name">${labelFor(i.category)}</span>
        <span class="insight-item-meta">+${i.pct_change}%</span>
        <span class="insight-item-value amount-neg">${fmt(i.current)}</span>
      </div>`).join("")
    ));
  }
  if (s.subscription_creep?.items?.length > 0) {
    cards.push(renderInsightCard("insight-warning", "🔁", "Possible Subscription Creep",
      s.subscription_creep.items.map(i => `<div class="insight-item">
        <span class="insight-item-name">${escHtml(i.vendor)}</span>
        <span class="insight-item-meta">${i.count}× @ ${fmt(i.per_tx)}</span>
        <span class="insight-item-value amount-neg">${fmt(i.total)}</span>
      </div>`).join("")
    ));
  }
  if (s.largest_transactions?.items?.length > 0) {
    cards.push(renderInsightCard("insight-alert", "🔴", "Largest Single Expenses",
      s.largest_transactions.items.map(i => `<div class="insight-item">
        <span class="insight-item-name">${escHtml(i.description)}</span>
        <span class="insight-item-meta">${i.date}</span>
        <span class="insight-item-value amount-neg">${fmt(i.amount)}</span>
      </div>`).join("")
    ));
  }

  if (cards.length === 0) { section.style.display = "none"; return; }
  content.innerHTML = `<div class="insights-grid">${cards.join("")}</div>`;
  section.style.display = "block";
}

function renderInsightCard(cls, icon, title, bodyHtml) {
  return `<div class="insight-card ${cls}">
    <div class="insight-card-title"><span>${icon}</span>${title}</div>
    ${bodyHtml}
  </div>`;
}

/* ── Transactions ────────────────────────────────────────────────────────────── */
async function loadTransactions(month) {
  const res = await fetch("/api/transactions?" + apiParams({ month }));
  allTransactions = await res.json();
  filterTransactions();
}

function filterTransactions() {
  const query = document.getElementById("txSearch").value.toLowerCase();
  let base = [...allTransactions];
  // Apply income/expense filter
  if (txTypeFilter === "income") base = base.filter(t => t.amount > 0);
  else if (txTypeFilter === "expense") base = base.filter(t => t.amount < 0);
  filteredTransactions = query
    ? base.filter(t =>
        t.description.toLowerCase().includes(query) || (t.category || "").toLowerCase().includes(query))
    : base;
  // Apply sort
  const s = _sort.transactions;
  if (s.col) {
    filteredTransactions.sort((a, b) => {
      let av, bv;
      if (s.col === "date")        { return s.dir * a.date.localeCompare(b.date); }
      if (s.col === "description") { return s.dir * a.description.localeCompare(b.description); }
      if (s.col === "amount")      { av = a.amount; bv = b.amount; return s.dir * (av - bv); }
      return 0;
    });
  }
  currentPage = 1;
  renderTransactionTable();
}

function renderTransactionTable() {
  const tbody = document.getElementById("txBody");
  tbody.innerHTML = "";
  document.getElementById("txCount").textContent = `${filteredTransactions.length} transactions`;

  if (!filteredTransactions.length) {
    tbody.innerHTML = `<tr><td colspan="5" class="empty-state">No transactions found</td></tr>`;
    renderPagination(0); return;
  }

  const page = filteredTransactions.slice((currentPage - 1) * PAGE_SIZE, currentPage * PAGE_SIZE);
  page.forEach(tx => {
    const amtClass = tx.amount >= 0 ? "amount-pos" : "amount-neg";
    const amtStr = (tx.amount >= 0 ? "+" : "") + fmt(tx.amount);
    const badge = tx.category_source === "manual"
      ? '<span class="badge badge-manual">Manual</span>'
      : '<span class="badge badge-ai">AI</span>';
    const catOptions = allCategories.map(c =>
      `<option value="${c.name}" ${c.name === tx.category ? "selected" : ""}>${labelFor(c.name)}</option>`
    ).join("");

    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td>${tx.date}</td>
      <td>${escHtml(tx.description)}</td>
      <td class="num ${amtClass}">${amtStr}</td>
      <td><select class="cat-select" data-id="${tx.id}" onchange="overrideCategory(this)">${catOptions}</select></td>
      <td>${badge}</td>
    `;
    tbody.appendChild(tr);
  });
  renderPagination(filteredTransactions.length);
}

async function overrideCategory(selectEl) {
  const txId = selectEl.dataset.id;
  const category = selectEl.value;
  const tx = allTransactions.find(t => t.id == txId);
  if (tx) { tx.category = category; tx.category_source = "manual"; }
  const ftx = filteredTransactions.find(t => t.id == txId);
  if (ftx) { ftx.category = category; ftx.category_source = "manual"; }
  selectEl.closest("tr").cells[4].innerHTML = '<span class="badge badge-manual">Manual</span>';

  await fetch(`/api/transactions/${txId}/category`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ category })
  });
}

/* ── Pagination ──────────────────────────────────────────────────────────────── */
function renderPagination(total) {
  const container = document.getElementById("pagination");
  container.innerHTML = "";
  const pages = Math.ceil(total / PAGE_SIZE);
  if (pages <= 1) return;
  for (let i = 1; i <= pages; i++) {
    const btn = document.createElement("button");
    btn.className = "page-btn" + (i === currentPage ? " active" : "");
    btn.textContent = i;
    btn.onclick = () => { currentPage = i; renderTransactionTable(); };
    container.appendChild(btn);
  }
}

/* ── Category Drill-Down Modal ───────────────────────────────────────────────── */
let _categoryModalTxs = [];

function openCategoryModal(category, type) {
  const label = labelFor(category);
  document.getElementById("categoryModalTitle").textContent = label;
  document.getElementById("categoryModalSearch").value = "";
  document.getElementById("categoryModal").classList.add("open");

  // Filter from already-loaded transactions
  _categoryModalTxs = allTransactions.filter(t => t.category === category);
  const total = _categoryModalTxs.reduce((s, t) => s + t.amount, 0);
  document.getElementById("categoryModalSubtitle").textContent =
    `${_categoryModalTxs.length} transaction${_categoryModalTxs.length !== 1 ? "s" : ""} · Total: ${fmt(total)}`;
  _renderCategoryModalRows(_categoryModalTxs);
}

function _renderCategoryModalRows(txs) {
  const tbody = document.getElementById("categoryModalBody");
  if (!txs.length) {
    tbody.innerHTML = `<tr><td colspan="5" class="empty-state">No transactions found</td></tr>`; return;
  }
  tbody.innerHTML = txs.map(t => {
    const amtClass = t.amount >= 0 ? "amount-pos" : "amount-neg";
    const srcBadge = t.category_source === "manual"
      ? '<span class="badge badge-manual">Manual</span>'
      : '<span class="badge badge-ai">AI</span>';
    const catOptions = allCategories.map(c =>
      `<option value="${c.name}" ${c.name === t.category ? "selected" : ""}>${labelFor(c.name)}</option>`
    ).join("");
    return `<tr>
      <td>${t.date}</td>
      <td>${escHtml(t.description)}</td>
      <td class="num ${amtClass}">${fmt(t.amount)}</td>
      <td><select class="cat-select" data-id="${t.id}" onchange="overrideCategoryFromModal(this)">${catOptions}</select></td>
      <td>${srcBadge}</td>
    </tr>`;
  }).join("");
}

function filterCategoryModal() {
  const q = document.getElementById("categoryModalSearch").value.toLowerCase();
  const filtered = q ? _categoryModalTxs.filter(t =>
    t.description.toLowerCase().includes(q) || t.category.toLowerCase().includes(q)
  ) : _categoryModalTxs;
  _renderCategoryModalRows(filtered);
}

async function overrideCategoryFromModal(selectEl) {
  const txId = parseInt(selectEl.dataset.id);
  const newCat = selectEl.value;
  await overrideCategory(selectEl);
  // Keep modal list in sync
  _categoryModalTxs = _categoryModalTxs.map(t =>
    t.id === txId ? { ...t, category: newCat, category_source: "manual" } : t
  );
}

function closeCategoryModal(event) {
  if (!event || event.target === document.getElementById("categoryModal"))
    document.getElementById("categoryModal").classList.remove("open");
}

/* ── Manage Statements Modal ─────────────────────────────────────────────────── */
async function openManageModal() {
  document.getElementById("manageModal").classList.add("open");
  await renderManageTable();
}
function closeManageModal(event) {
  if (!event || event.target === document.getElementById("manageModal"))
    document.getElementById("manageModal").classList.remove("open");
}

async function renderManageTable() {
  const res = await fetch("/api/statements");
  _lastStatements = await res.json();
  _renderManageRows(_lastStatements);
}

async function toggleStatementType(stmtId, newType) {
  const res = await fetch(`/api/statements/${stmtId}`, {
    method: "PATCH", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ account_type: newType })
  });
  if (res.ok) {
    await renderManageTable();
    await loadMonths();
    await loadAccountSuffixes();
    loadTrendChart();
    if (currentMonth) await onMonthChange();
  }
}

async function editSuffix(stmtId, current) {
  const val = prompt("Enter last 4 digits of account number (leave blank to clear):", current);
  if (val === null) return; // cancelled
  const suffix = val.trim().replace(/\D/g, "").slice(-4) || null;
  const res = await fetch(`/api/statements/${stmtId}`, {
    method: "PATCH", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ account_suffix: suffix })
  });
  if (res.ok) { await renderManageTable(); await loadAccountSuffixes(); }
}

async function setStatementSubtype(stmtId, subtype) {
  await fetch(`/api/statements/${stmtId}`, {
    method: "PATCH", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ account_subtype: subtype || null })
  });
  await loadAccountSuffixes(); // refresh dropdown labels
}

async function deleteStatementFromManage(stmtId) {
  if (!confirm("Delete this statement and all its transactions?")) return;
  const res = await fetch(`/api/statements/${stmtId}`, { method: "DELETE" });
  if (res.ok) {
    await renderManageTable();
    await loadMonths();
    await loadAccountSuffixes();
    loadTrendChart();
    if (currentMonth) await onMonthChange();
  }
}

/* ── Upload Modal ────────────────────────────────────────────────────────────── */
function openUploadModal() {
  document.getElementById("uploadModal").classList.add("open");
  document.getElementById("uploadStatus").style.display = "none";
  document.getElementById("uploadForm").reset();
  document.querySelectorAll('input[name="account_type"]').forEach(r => { r.checked = r.value === currentAccountType; });
}
function closeUploadModal(event) {
  if (!event || event.target === document.getElementById("uploadModal"))
    document.getElementById("uploadModal").classList.remove("open");
}

async function handleUpload(event) {
  event.preventDefault();
  const files = document.getElementById("pdfFile").files;
  const bankName = document.getElementById("bankName").value;
  const accountType = document.querySelector('input[name="account_type"]:checked').value;
  const statusEl = document.getElementById("uploadStatus");
  const btn = document.getElementById("uploadBtn");

  btn.disabled = true;
  let lastMonth = null;
  let successCount = 0;
  const errors = [];

  for (let i = 0; i < files.length; i++) {
    statusEl.className = "upload-status loading";
    statusEl.textContent = `Processing ${i + 1} of ${files.length}: ${files[i].name}…`;
    statusEl.style.display = "block";

    const fd = new FormData();
    fd.append("file", files[i]);
    fd.append("bank_name", bankName);
    fd.append("account_type", accountType);

    try {
      const res = await fetch("/api/upload", { method: "POST", body: fd });
      const data = await res.json();
      if (!res.ok || data.error) { errors.push(`${files[i].name}: ${data.error}`); }
      else { successCount++; if (data.month) lastMonth = data.month; }
    } catch (err) { errors.push(`${files[i].name}: ${err.message}`); }
  }

  if (errors.length && !successCount) {
    statusEl.className = "upload-status error";
    statusEl.textContent = errors[0];
    btn.disabled = false; return;
  }

  statusEl.className = "upload-status success";
  statusEl.textContent = `Done! ${successCount}/${files.length} files processed.${errors.length ? ` ${errors.length} failed.` : ""}`;

  if (accountType !== currentAccountType) { await switchAccountType(accountType); }
  else { await loadCategories(); await loadMonths(); await loadAccountSuffixes(); loadTrendChart(); }

  if (lastMonth) {
    document.getElementById("monthSelect").value = lastMonth;
    currentMonth = lastMonth;
    await onMonthChange();
  }

  setTimeout(() => closeUploadModal(), 2000);
  btn.disabled = false;
}

/* ── Add Category Modal ──────────────────────────────────────────────────────── */
function openAddCategoryModal() {
  document.getElementById("addCategoryModal").classList.add("open");
  document.getElementById("newCatName").value = "";
  document.getElementById("addCatStatus").style.display = "none";
}
function closeAddCategoryModal(event) {
  if (!event || event.target === document.getElementById("addCategoryModal"))
    document.getElementById("addCategoryModal").classList.remove("open");
}

async function submitAddCategory() {
  const name = document.getElementById("newCatName").value.trim();
  const type = document.querySelector('input[name="newCatType"]:checked').value;
  const statusEl = document.getElementById("addCatStatus");

  if (!name) {
    statusEl.className = "upload-status error";
    statusEl.textContent = "Please enter a category name.";
    statusEl.style.display = "block"; return;
  }

  const res = await fetch("/api/categories", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name, type })
  });
  const data = await res.json();

  if (!res.ok) {
    statusEl.className = "upload-status error";
    statusEl.textContent = data.error || "Failed to create category.";
    statusEl.style.display = "block"; return;
  }

  statusEl.className = "upload-status success";
  statusEl.textContent = `Category "${data.name}" added!`;
  statusEl.style.display = "block";
  await loadCategories();
  setTimeout(() => closeAddCategoryModal(), 1500);
}

/* ── Helpers ─────────────────────────────────────────────────────────────────── */
function fmt(value) {
  if (value == null) return "—";
  const abs = Math.abs(value);
  return (value < 0 ? "-" : "") + "$" + abs.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function formatMonth(m) {
  if (!m) return "";
  const [year, month] = m.split("-");
  return new Date(parseInt(year), parseInt(month) - 1, 1).toLocaleString("en-US", { month: "long", year: "numeric" });
}

function labelFor(cat) {
  if (!cat) return "—";
  return cat.replace(/_/g, " ").replace(/\b\w/g, c => c.toUpperCase());
}

function escHtml(str) {
  if (!str) return "";
  return str.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

function generateColors(n) {
  const palette = ["#6366f1","#3b82f6","#06b6d4","#10b981","#f59e0b","#f97316","#ef4444","#ec4899","#8b5cf6","#14b8a6","#84cc16","#eab308","#a855f7","#0ea5e9","#22c55e"];
  return Array.from({ length: n }, (_, i) => palette[i % palette.length]);
}

/* ══════════════════════════════════════════════════════════════════════════════
   FEATURE 1 — PORTFOLIO TAB
══════════════════════════════════════════════════════════════════════════════ */

let _portCurrencyMode = "cad";   // "cad" | "local"
let _portData = null;            // last fetched summary response

function switchToPortfolio() {
  document.querySelectorAll(".tab-btn").forEach(btn => btn.classList.remove("active"));
  document.querySelector('.tab-btn[data-type="portfolio"]').classList.add("active");
  document.getElementById("pnlView").style.display = "none";
  document.getElementById("portfolioView").style.display = "block";
  loadPortfolio();
}

function setPortCurrency(mode) {
  _portCurrencyMode = mode;
  document.getElementById("portToggleCAD").classList.toggle("active", mode === "cad");
  document.getElementById("portToggleLocal").classList.toggle("active", mode === "local");
  document.getElementById("holdingsViewLabel").textContent =
    mode === "cad" ? "All values in CAD" : "Values in each holding's native currency";
  if (_portData) renderHoldingsTable(_portData.holdings || []);
}


async function loadPortfolio() {
  document.getElementById("holdingsTableWrap").innerHTML = '<div class="empty-state">Fetching live prices…</div>';
  try {
    const res = await fetch("/api/portfolio/summary");
    const data = await res.json();
    _portData = data;
    portfolioHoldings = data.holdings || [];
    renderPortfolioSummary(data);
    renderHoldingsTable(data.holdings || []);
    renderDividendsSection();
    // FX note
    const fx = data.fx_rate_usd_cad || 0;
    const ts = data.fx_timestamp ? new Date(data.fx_timestamp).toLocaleTimeString() : "";
    document.getElementById("portFxNote").textContent =
      `1 USD = C$${fx.toFixed(4)} · updated ${ts}`;
    loadNetWorth();
  } catch (e) {
    document.getElementById("holdingsTableWrap").innerHTML = '<div class="empty-state">Failed to load portfolio data.</div>';
  }
}

function renderPortfolioSummary(data) {
  const t = data.totals || {};
  // Summary bar always in CAD (consolidated view)
  document.getElementById("portTotalCost").textContent = fmtCAD(t.total_cost_cad);

  const valEl = document.getElementById("portTotalValue");
  valEl.textContent = fmtCAD(t.total_market_value_cad);
  valEl.style.color = (t.total_unrealized_gl_cad || 0) >= 0 ? "var(--green)" : "var(--red)";

  const gainSub = document.getElementById("portTotalGainSub");
  const gl = t.total_unrealized_gl_cad || 0;
  gainSub.textContent = (gl >= 0 ? "▲ +" : "▼ ") + fmtCAD(gl) + " total return";
  gainSub.style.color = gl >= 0 ? "var(--green)" : "var(--red)";

  const retPctEl = document.getElementById("portTotalReturnPct");
  const pct = t.total_unrealized_gl_pct || 0;
  retPctEl.textContent = (pct >= 0 ? "+" : "") + pct.toFixed(2) + "%";
  retPctEl.style.color = pct >= 0 ? "var(--green)" : "var(--red)";

  document.getElementById("portTotalDivs").textContent = fmtCAD(t.total_dividends_cad);
}

function fmtCAD(value) {
  if (value == null) return "—";
  const abs = Math.abs(value);
  return (value < 0 ? "-" : "") + "C$" + abs.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function fmtLocal(value, currency) {
  if (value == null) return "—";
  const abs = Math.abs(value);
  const prefix = currency === "USD" ? "$" : "C$";
  return (value < 0 ? "-" : "") + prefix + abs.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function _holdingRowHtml(h, useLocal, accounts) {
  const lc = h.local_currency || h.currency || "USD";
  const gl = useLocal ? h.unrealized_gl_local : h.unrealized_gl_cad;
  const glPct = h.unrealized_gl_pct || 0;
  const glClass = (gl || 0) >= 0 ? "amount-pos" : "amount-neg";
  const glSign  = (gl || 0) >= 0 ? "+" : "";

  const avgCost = useLocal
    ? fmtLocal(h.avg_buy_price_local, lc)
    : fmtCAD(h.cost_basis_cad != null && h.shares ? h.cost_basis_cad / h.shares : null);

  const livePrice = useLocal
    ? (h.current_price_local != null ? fmtLocal(h.current_price_local, lc) : "—")
    : (h.current_price_cad  != null ? fmtCAD(h.current_price_cad)          : "—");

  const marketVal = useLocal
    ? fmtLocal(h.current_value_local, lc)
    : fmtCAD(h.current_value_cad);

  const glStr = useLocal
    ? fmtLocal(gl, lc)
    : fmtCAD(gl);

  const priceBadge = h.price_stale
    ? '<span class="badge badge-stale" style="font-size:9px;margin-left:4px;">STALE</span>'
    : '<span class="badge badge-live" style="font-size:9px;margin-left:4px;">●</span>';

  const acctOptions = `<option value="">— Unassigned —</option>` +
    (accounts || []).map(a =>
      `<option value="${a.id}" ${h.account_id === a.id ? "selected" : ""}>[${a.type}] ${escHtml(a.nickname)}</option>`
    ).join("");

  return `<tr>
    <td><strong>${escHtml(h.ticker)}</strong><br>
      <span style="font-size:10px;color:var(--text-muted);">${escHtml(lc)}</span></td>
    <td>${h.shares.toLocaleString()}</td>
    <td class="num">${avgCost}</td>
    <td class="num">${livePrice}${priceBadge}</td>
    <td class="num">${marketVal}</td>
    <td class="num ${glClass}">${glSign}${glStr}</td>
    <td class="num ${glClass}">${glSign}${glPct.toFixed(2)}%</td>
    <td class="num">${(h.weight_pct || 0).toFixed(1)}%</td>
    <td class="num">
      <select class="cat-select acct-assign" style="font-size:11px;padding:2px 4px;margin-bottom:0"
        onchange="setHoldingAccount(${h.id},this.value)">${acctOptions}</select>
    </td>
    <td class="num">
      <button class="btn-secondary btn-sm" style="border-color:var(--red);color:var(--red);"
        onclick="deleteHolding(${h.id})">✕</button>
    </td>
  </tr>`;
}

async function renderHoldingsTable(holdings) {
  const wrap = document.getElementById("holdingsTableWrap");
  if (!holdings.length) {
    wrap.innerHTML = '<div class="empty-state">No holdings yet. Click "+ Add Holding" to get started.</div>';
    return;
  }

  const useLocal = _portCurrencyMode === "local";
  const priceHeader = useLocal ? "Price (local)" : "Price (CAD)";
  const valueHeader = useLocal ? "Value (local)" : "Value (CAD)";
  const costHeader  = useLocal ? "Avg Cost (local)" : "Avg Cost (CAD)";

  // Fetch accounts for the inline dropdown
  let accounts = [];
  try {
    const r = await fetch("/api/portfolio/accounts");
    accounts = await r.json();
  } catch (_) {}

  // Group by account_id
  const groups = {};
  holdings.forEach(h => {
    const key = h.account_id != null ? h.account_id : "__unassigned__";
    if (!groups[key]) groups[key] = [];
    groups[key].push(h);
  });

  let html = `<table>
    <thead><tr>
      <th>Ticker</th><th>Shares</th>
      <th class="num">${costHeader}</th>
      <th class="num">${priceHeader}</th>
      <th class="num">${valueHeader}</th>
      <th class="num">Gain / Loss</th>
      <th class="num">Return %</th>
      <th class="num">Weight</th>
      <th>Account</th>
      <th></th>
    </tr></thead>
    <tbody>`;

  // Render account groups (sorted: named accounts first, unassigned last)
  const sortedKeys = [...Object.keys(groups)].sort((a, b) => {
    if (a === "__unassigned__") return 1;
    if (b === "__unassigned__") return -1;
    return parseInt(a) - parseInt(b);
  });

  sortedKeys.forEach(key => {
    const groupHoldings = groups[key];
    const acct = key === "__unassigned__" ? null : accounts.find(a => a.id === parseInt(key));
    const groupLabel = acct ? `[${acct.type}] ${escHtml(acct.nickname)}` : "Unassigned";

    // Subtotals for this group
    const groupCost = groupHoldings.reduce((s, h) => s + (useLocal ? (h.cost_basis_local || 0) : (h.cost_basis_cad || 0)), 0);
    const groupVal  = groupHoldings.reduce((s, h) => s + (useLocal ? (h.current_value_local || 0) : (h.current_value_cad || 0)), 0);
    const groupGL   = groupVal - groupCost;
    const glClass   = groupGL >= 0 ? "amount-pos" : "amount-neg";

    html += `<tr class="account-group-header">
      <td colspan="4"><span class="account-group-label">${groupLabel}</span></td>
      <td class="num">${useLocal ? "—" : fmtCAD(groupVal)}</td>
      <td class="num ${glClass}">${useLocal ? "—" : (groupGL >= 0 ? "+" : "") + fmtCAD(groupGL)}</td>
      <td colspan="4" style="color:var(--text-muted);font-size:11px;text-align:right">
        Cost: ${useLocal ? "—" : fmtCAD(groupCost)}
      </td>
    </tr>`;

    groupHoldings.forEach(h => {
      html += _holdingRowHtml(h, useLocal, accounts);
    });
  });

  html += `</tbody></table>`;
  wrap.innerHTML = html;
}

async function setHoldingAccount(holdingId, accountId) {
  await fetch(`/api/portfolio/holdings/${holdingId}`, {
    method: "PATCH", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ account_id: accountId ? parseInt(accountId) : null })
  });
  loadPortfolio();
}

async function renderDividendsSection() {
  const res = await fetch("/api/portfolio/dividends");
  const divs = await res.json();
  const section = document.getElementById("dividendsSection");
  const wrap = document.getElementById("dividendsTableWrap");
  if (!divs.length) { section.style.display = "none"; return; }
  section.style.display = "block";
  wrap.innerHTML = `<table>
    <thead><tr><th>Date</th><th>Ticker</th><th class="num">Amount (CAD)</th><th>Notes</th></tr></thead>
    <tbody>${divs.map(d => `<tr>
      <td>${d.paid_date}</td>
      <td><strong>${escHtml(d.ticker)}</strong></td>
      <td class="num amount-pos">+${fmt(d.amount_cad)}</td>
      <td style="color:var(--text-muted);font-size:12px;">${escHtml(d.notes || "—")}</td>
    </tr>`).join("")}</tbody>
  </table>`;
}

/* ── Add Holding Modal ── */
async function openAddHoldingModal() {
  document.getElementById("addHoldingModal").classList.add("open");
  document.getElementById("holdingTicker").value = "";
  document.getElementById("holdingShares").value = "";
  document.getElementById("holdingCost").value = "";
  document.getElementById("holdingCurrencyDetected").value = "";
  document.getElementById("holdingCostCurrencyHint").textContent = "";
  document.getElementById("tickerDetectStatus").style.display = "none";
  document.getElementById("addHoldingStatus").style.display = "none";
  document.getElementById("addHoldingSubmitBtn").disabled = false;
  // Populate account dropdown
  const acctSel = document.getElementById("holdingAccount");
  acctSel.innerHTML = '<option value="">— Unassigned —</option>';
  try {
    const res = await fetch("/api/portfolio/accounts");
    const accounts = await res.json();
    accounts.forEach(a => {
      const opt = document.createElement("option");
      opt.value = a.id;
      opt.textContent = `[${a.type}] ${a.nickname}`;
      acctSel.appendChild(opt);
    });
  } catch (_) {}
}
function closeAddHoldingModal(event) {
  if (!event || event.target === document.getElementById("addHoldingModal"))
    document.getElementById("addHoldingModal").classList.remove("open");
}

async function detectTickerCurrency() {
  const ticker = document.getElementById("holdingTicker").value.trim().toUpperCase();
  if (!ticker) return;
  const statusEl = document.getElementById("tickerDetectStatus");
  const hintEl = document.getElementById("holdingCostCurrencyHint");
  const hiddenEl = document.getElementById("holdingCurrencyDetected");
  const submitBtn = document.getElementById("addHoldingSubmitBtn");
  statusEl.className = "field-hint";
  statusEl.textContent = "Detecting currency…";
  statusEl.style.display = "block";
  submitBtn.disabled = true;
  try {
    const res = await fetch(`/api/portfolio/ticker-info?ticker=${encodeURIComponent(ticker)}`);
    const data = await res.json();
    if (!res.ok || data.error) {
      statusEl.className = "field-hint field-hint-error";
      statusEl.textContent = data.error || "Invalid ticker";
      hiddenEl.value = "";
    } else {
      hiddenEl.value = data.currency;
      hintEl.textContent = `(in ${data.currency})`;
      statusEl.className = "field-hint field-hint-ok";
      statusEl.textContent = `Detected: ${data.currency} · ${data.name || ticker}`;
      submitBtn.disabled = false;
    }
  } catch (e) {
    statusEl.className = "field-hint field-hint-error";
    statusEl.textContent = "Could not reach server";
  }
}

async function submitAddHolding() {
  const ticker = document.getElementById("holdingTicker").value.trim().toUpperCase();
  const shares = parseFloat(document.getElementById("holdingShares").value);
  const cost = parseFloat(document.getElementById("holdingCost").value);
  const currency = document.getElementById("holdingCurrencyDetected").value;
  const account_id = document.getElementById("holdingAccount").value || null;
  const statusEl = document.getElementById("addHoldingStatus");

  if (!ticker || isNaN(shares) || isNaN(cost)) {
    statusEl.className = "upload-status error";
    statusEl.textContent = "Ticker, shares, and price are required.";
    statusEl.style.display = "block"; return;
  }
  if (!currency) {
    statusEl.className = "upload-status error";
    statusEl.textContent = "Please tab out of the Ticker field to detect the currency first.";
    statusEl.style.display = "block"; return;
  }

  const res = await fetch("/api/portfolio/holdings", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ ticker, shares, avg_buy_price_local: cost, account_id: account_id ? parseInt(account_id) : null })
  });
  const data = await res.json();
  if (!res.ok) {
    statusEl.className = "upload-status error";
    statusEl.textContent = data.error || "Failed to add holding.";
    statusEl.style.display = "block"; return;
  }
  closeAddHoldingModal();
  loadPortfolio();
}

async function deleteHolding(holdingId) {
  if (!confirm("Remove this holding and all its dividends?")) return;
  await fetch(`/api/portfolio/holdings/${holdingId}`, { method: "DELETE" });
  loadPortfolio();
}

/* ── Manage Accounts Modal ── */
async function openAccountsModal() {
  document.getElementById("accountsModal").classList.add("open");
  await renderAccountsList();
}
function closeAccountsModal(event) {
  if (!event || event.target === document.getElementById("accountsModal"))
    document.getElementById("accountsModal").classList.remove("open");
}

async function renderAccountsList() {
  const listEl = document.getElementById("accountsList");
  listEl.innerHTML = '<div class="empty-state">Loading…</div>';
  const res = await fetch("/api/portfolio/accounts");
  const accounts = await res.json();
  if (!accounts.length) {
    listEl.innerHTML = '<div class="empty-state">No accounts yet. Create one below.</div>';
    return;
  }
  listEl.innerHTML = `<table>
    <thead><tr>
      <th>Type</th><th>Nickname</th><th class="num">Holdings</th><th></th>
    </tr></thead>
    <tbody>${accounts.map(a => `<tr>
      <td><span class="badge badge-acct">${escHtml(a.type)}</span></td>
      <td>
        <span class="acct-name-display" id="acct-name-${a.id}">${escHtml(a.nickname)}</span>
        <input class="acct-name-input" id="acct-input-${a.id}" value="${escHtml(a.nickname)}"
          style="display:none" onkeydown="if(event.key==='Enter')saveAccountName(${a.id})" />
      </td>
      <td class="num">${a.holding_count}</td>
      <td class="num" style="white-space:nowrap">
        <button class="btn-secondary btn-sm" onclick="startEditAccount(${a.id})">Rename</button>
        <button class="btn-secondary btn-sm btn-danger" style="margin-left:4px"
          ${a.holding_count > 0 ? `disabled title="Reassign ${a.holding_count} holding(s) first"` : ""}
          onclick="deleteAccount(${a.id})">Delete</button>
      </td>
    </tr>`).join("")}</tbody>
  </table>`;
}

function startEditAccount(id) {
  document.getElementById(`acct-name-display-${id}`)?.setAttribute("style", "display:none");
  const span = document.querySelector(`#acct-name-${id}`);
  const input = document.querySelector(`#acct-input-${id}`);
  if (span) span.style.display = "none";
  if (input) { input.style.display = "inline-block"; input.focus(); }
}

async function saveAccountName(id) {
  const input = document.getElementById(`acct-input-${id}`);
  const nickname = input?.value.trim();
  if (!nickname) return;
  const statusEl = document.getElementById("accountsStatus");
  const res = await fetch(`/api/portfolio/accounts/${id}`, {
    method: "PATCH", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ nickname })
  });
  const data = await res.json();
  if (!res.ok) {
    statusEl.className = "upload-status error";
    statusEl.textContent = data.error || "Failed to rename";
    statusEl.style.display = "block"; return;
  }
  statusEl.style.display = "none";
  await renderAccountsList();
}

async function deleteAccount(id) {
  if (!confirm("Delete this account?")) return;
  const statusEl = document.getElementById("accountsStatus");
  const res = await fetch(`/api/portfolio/accounts/${id}`, { method: "DELETE" });
  const data = await res.json();
  if (!res.ok) {
    statusEl.className = "upload-status error";
    statusEl.textContent = data.error;
    statusEl.style.display = "block"; return;
  }
  statusEl.style.display = "none";
  await renderAccountsList();
  loadPortfolio();
}

async function submitCreateAccount() {
  const type = document.getElementById("newAcctType").value;
  const nickname = document.getElementById("newAcctNickname").value.trim();
  const statusEl = document.getElementById("accountsStatus");
  if (!nickname) {
    statusEl.className = "upload-status error";
    statusEl.textContent = "Nickname is required";
    statusEl.style.display = "block"; return;
  }
  const res = await fetch("/api/portfolio/accounts", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ type, nickname })
  });
  const data = await res.json();
  if (!res.ok) {
    statusEl.className = "upload-status error";
    statusEl.textContent = data.error || "Failed to create account";
    statusEl.style.display = "block"; return;
  }
  document.getElementById("newAcctNickname").value = "";
  statusEl.style.display = "none";
  await renderAccountsList();
}

/* ── Log Dividend Modal ── */
function openLogDivModal() {
  const sel = document.getElementById("divHolding");
  sel.innerHTML = portfolioHoldings.length
    ? portfolioHoldings.map(h => `<option value="${h.id}" data-ticker="${escHtml(h.ticker)}">${escHtml(h.ticker)}</option>`).join("")
    : '<option value="">— add a holding first —</option>';
  document.getElementById("divAmount").value = "";
  document.getElementById("divDate").value = new Date().toISOString().slice(0, 10);
  document.getElementById("divNotes").value = "";
  document.getElementById("logDivStatus").style.display = "none";
  document.getElementById("logDivModal").classList.add("open");
}
function closeLogDivModal(event) {
  if (!event || event.target === document.getElementById("logDivModal"))
    document.getElementById("logDivModal").classList.remove("open");
}

async function submitLogDividend() {
  const sel = document.getElementById("divHolding");
  const holding_id = parseInt(sel.value);
  const ticker = sel.selectedOptions[0]?.dataset.ticker || "";
  const amount_cad = parseFloat(document.getElementById("divAmount").value);
  const paid_date = document.getElementById("divDate").value.trim();
  const notes = document.getElementById("divNotes").value.trim() || null;
  const statusEl = document.getElementById("logDivStatus");

  if (!holding_id || isNaN(amount_cad) || !paid_date) {
    statusEl.className = "upload-status error";
    statusEl.textContent = "Holding, amount, and date are required.";
    statusEl.style.display = "block"; return;
  }
  const res = await fetch("/api/portfolio/dividends", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ holding_id, ticker, amount_cad, paid_date, notes })
  });
  if (!res.ok) {
    const d = await res.json();
    statusEl.className = "upload-status error";
    statusEl.textContent = d.error || "Failed.";
    statusEl.style.display = "block"; return;
  }
  closeLogDivModal();
  loadPortfolio();
}

/* ══════════════════════════════════════════════════════════════════════════════
   FEATURE 2 — CPA ADVISOR PANEL
══════════════════════════════════════════════════════════════════════════════ */

function toggleAdvisorPanel() {
  document.getElementById("advisorPanel").classList.toggle("open");
}

async function runAdvisorAnalysis() {
  const body = document.getElementById("advisorBody");
  body.innerHTML = '<div class="advisor-refresh"><button class="btn-primary" style="flex:1;opacity:0.5;" disabled>Analyzing…</button></div><div class="advisor-spinner">Claude is reviewing your finances…<br><span style="font-size:11px;color:var(--text-muted);">This takes 10–20 seconds</span></div>';
  document.getElementById("advisorFooter").style.display = "none";

  try {
    const res = await fetch("/api/advisor/analysis", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ account_type: currentAccountType, months: 3 })
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || "Analysis failed");
    advisorCache = data;
    renderAdvisorSections(data);
  } catch (e) {
    body.innerHTML = `<div class="advisor-refresh"><button class="btn-primary" style="flex:1" onclick="runAdvisorAnalysis()">↻ Retry Analysis</button></div><div style="color:var(--red);font-size:13px;padding:12px 0;">${escHtml(e.message)}</div>`;
  }
}

function renderAdvisorSections(data) {
  const body = document.getElementById("advisorBody");
  const sections = data.sections || [];
  const months = (data.snapshot_months || []).map(formatMonth).join(", ");
  const ts = data.generated_at ? new Date(data.generated_at).toLocaleTimeString() : "";

  const cards = sections.map(s => {
    const priorityClass = `priority-${s.priority || "low"}`;
    const badge = s.priority === "high"
      ? '<span class="badge badge-high">🔴 High</span>'
      : s.priority === "medium"
        ? '<span class="badge badge-medium">🟡 Medium</span>'
        : '<span class="badge badge-low">🟢 Low</span>';
    return `<div class="advisor-card ${priorityClass}">
      <div class="advisor-card-title">${escHtml(s.title)}${badge}</div>
      <div class="advisor-card-insight">${escHtml(s.insight)}</div>
      <div class="advisor-card-action">${escHtml(s.action)}</div>
    </div>`;
  }).join("");

  body.innerHTML = `
    <div class="advisor-refresh">
      <button class="btn-primary" style="flex:1" onclick="runAdvisorAnalysis()">↻ Refresh Analysis</button>
      <span class="advisor-refresh-ts">Updated ${ts}</span>
    </div>
    <div style="font-size:11px;color:var(--text-muted);margin-bottom:16px;">Based on: ${months}</div>
    ${cards || '<div class="empty-state">No insights generated.</div>'}
  `;
  document.getElementById("advisorFooter").style.display = "block";
}

/* ══════════════════════════════════════════════════════════════════════════════
   FEATURE 3 — BUDGET INTELLIGENCE
══════════════════════════════════════════════════════════════════════════════ */

function showPnlView() {
  document.getElementById("pnlTableView").style.display = "block";
  document.getElementById("budgetView").style.display = "none";
  document.getElementById("togglePnlBtn").classList.add("active");
  document.getElementById("toggleBudgetBtn").classList.remove("active");
  document.getElementById("categoryHeading").innerHTML = 'Category Breakdown <span class="hint">Click a row to see top vendors</span>';
}

function showBudgetView() {
  document.getElementById("pnlTableView").style.display = "none";
  document.getElementById("budgetView").style.display = "block";
  document.getElementById("togglePnlBtn").classList.remove("active");
  document.getElementById("toggleBudgetBtn").classList.add("active");
  document.getElementById("categoryHeading").innerHTML = 'Budget vs Actual';
  if (currentMonth) loadBudgetAnalysis(currentMonth);
}

async function loadBudgetAnalysis(month) {
  const params = new URLSearchParams({ month, account_type: currentAccountType });
  if (currentAccountSuffix) params.set("account_suffix", currentAccountSuffix);
  const res = await fetch("/api/budget/analysis?" + params.toString());
  if (!res.ok) return;
  const data = await res.json();
  renderBudgetTrafficLight(data.summary || {});
  renderBudgetChart(data.items || []);
  renderBudgetTable(data.items || []);
}

function renderBudgetTrafficLight(summary) {
  const el = document.getElementById("budgetTrafficLight");
  el.innerHTML = `
    <span class="tl-badge green">✅ ${summary.on_track || 0} On Track</span>
    <span class="tl-badge amber">⚠️ ${summary.near_limit || 0} Near Limit</span>
    <span class="tl-badge red">🔴 ${summary.over_budget || 0} Over Budget</span>
    <span class="tl-badge grey">○ ${summary.no_target || 0} No Target</span>
  `;
}

function renderBudgetChart(items) {
  const ctx = document.getElementById("budgetChart").getContext("2d");
  if (budgetChart) { budgetChart.destroy(); budgetChart = null; }
  const withTarget = items.filter(i => i.target != null);
  if (!withTarget.length) return;

  const labels = withTarget.map(i => labelFor(i.category));
  const targets = withTarget.map(i => i.target || 0);
  const actuals = withTarget.map(i => Math.abs(i.actual || 0));
  const colors = withTarget.map(i => {
    if (i.status === "red") return "#ef4444";
    if (i.status === "amber") return "#f59e0b";
    return "#22c55e";
  });

  const isDarkBudget = document.documentElement.getAttribute("data-theme") !== "light" &&
    (document.documentElement.getAttribute("data-theme") === "dark" ||
     window.matchMedia("(prefers-color-scheme: dark)").matches);
  const chartTickColor = isDarkBudget ? "#7c8aab" : "#6b7280";
  const chartGridColor = isDarkBudget ? "#2e3250" : "#e5e7eb";
  const targetBg = isDarkBudget ? "#2e3250" : "#e5e7eb";

  budgetChart = new Chart(ctx, {
    type: "bar",
    data: {
      labels,
      datasets: [
        { label: "Target", data: targets, backgroundColor: targetBg, borderRadius: 3 },
        { label: "Actual", data: actuals, backgroundColor: colors, borderRadius: 3 }
      ]
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: { legend: { labels: { color: chartTickColor, font: { size: 11 } } } },
      scales: {
        x: { ticks: { color: chartTickColor }, grid: { color: chartGridColor } },
        y: { ticks: { color: chartTickColor, callback: v => "$" + v.toLocaleString() }, grid: { color: chartGridColor } }
      }
    }
  });
}

function renderBudgetTable(items) {
  const tbody = document.getElementById("budgetTableBody");
  if (!items.length) {
    tbody.innerHTML = '<tr><td colspan="5" class="empty-state">No budget data for this month</td></tr>';
    return;
  }
  tbody.innerHTML = items.map(i => {
    const statusBadge = {
      green:     '<span class="badge badge-low">✓ On Track</span>',
      amber:     '<span class="badge badge-medium">⚠ Near Limit</span>',
      red:       '<span class="badge badge-high">✗ Over</span>',
      no_target: '<span class="badge" style="background:var(--surface2);color:var(--text-muted);">No Target</span>'
    }[i.status] || "";

    const varClass = (i.variance || 0) <= 0 ? "amount-pos" : "amount-neg";
    const varSign  = (i.variance || 0) <= 0 ? "" : "+";
    const varStr   = i.target != null
      ? `${varSign}${fmt(i.variance)} (${varSign}${(i.variance_pct || 0).toFixed(1)}%)`
      : "—";

    const targetCell = i.target != null
      ? `<input class="budget-target-input" value="${i.target.toFixed(2)}"
           onchange="saveBudgetTarget('${i.category}', this.value)" />`
      : `<input class="budget-target-input" placeholder="Set target"
           onchange="saveBudgetTarget('${i.category}', this.value)" />`;

    return `<tr>
      <td>${labelFor(i.category)}</td>
      <td class="num">${targetCell}</td>
      <td class="num">${fmt(Math.abs(i.actual || 0))}</td>
      <td class="num ${varClass}">${varStr}</td>
      <td class="num">${statusBadge}</td>
    </tr>`;
  }).join("");
}

async function saveBudgetTarget(category, value) {
  const target_cad = parseFloat(value);
  if (isNaN(target_cad) || target_cad < 0) return;
  await fetch("/api/budget/targets", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ category, target_cad })
  });
  if (currentMonth) loadBudgetAnalysis(currentMonth);
}

async function openBudgetSuggestModal() {
  document.getElementById("budgetSuggestModal").classList.add("open");
  document.getElementById("budgetSuggestContent").innerHTML = '<div class="advisor-spinner">Generating AI suggestions…</div>';

  const res = await fetch("/api/budget/suggest", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ account_type: currentAccountType })
  });
  const data = await res.json();
  const suggestions = data.suggestions || [];

  if (!suggestions.length) {
    document.getElementById("budgetSuggestContent").innerHTML = '<div class="empty-state">Not enough data to generate suggestions (need 2+ months).</div>';
    return;
  }

  document.getElementById("budgetSuggestContent").innerHTML = suggestions.map((s, idx) => `
    <div class="suggest-card" id="suggestCard${idx}">
      <div class="suggest-card-left">
        <div class="suggest-cat">${labelFor(s.category)}</div>
        <div class="suggest-rationale">${escHtml(s.rationale)}</div>
      </div>
      <span class="suggest-amount">${fmt(s.suggested_amount)}/mo</span>
      <div class="suggest-actions">
        <button class="btn-accept" onclick="acceptBudgetSuggestion('${s.category}', ${s.suggested_amount}, ${idx})">✓ Accept</button>
        <button class="btn-reject" onclick="rejectBudgetSuggestion(${idx})">✗ Skip</button>
      </div>
    </div>
  `).join("");
}

async function acceptBudgetSuggestion(category, amount, idx) {
  await saveBudgetTarget(category, amount);
  const card = document.getElementById(`suggestCard${idx}`);
  if (card) card.style.opacity = "0.4";
}

function rejectBudgetSuggestion(idx) {
  const card = document.getElementById(`suggestCard${idx}`);
  if (card) card.style.opacity = "0.4";
}

function closeBudgetSuggestModal(event) {
  if (!event || event.target === document.getElementById("budgetSuggestModal"))
    document.getElementById("budgetSuggestModal").classList.remove("open");
}

/* ══════════════════════════════════════════════════════════════════════════════
   FEATURE 4 — SPENDING DNA
══════════════════════════════════════════════════════════════════════════════ */

let dnaExpanded = false;

function toggleSpendingDna() {
  dnaExpanded = !dnaExpanded;
  document.getElementById("dnaContent").style.display = dnaExpanded ? "block" : "none";
  const chevron = document.getElementById("dnaChevron");
  chevron.textContent = dnaExpanded ? "▲" : "▼";
}

async function loadSpendingDna() {
  const section = document.getElementById("spendingDnaSection");
  if (currentAccountType !== "personal") { section.style.display = "none"; return; }
  section.style.display = "block";

  const params = new URLSearchParams({ account_type: currentAccountType });
  if (currentAccountSuffix) params.set("account_suffix", currentAccountSuffix);
  try {
    const res = await fetch("/api/spending-dna?" + params.toString());
    if (!res.ok) return;
    const data = await res.json();
    renderDnaContent(data);
  } catch (e) { /* silent fail */ }
}

function renderDnaContent(data) {
  const content = document.getElementById("dnaContent");

  const topVendors = (data.top_vendors || []).map((v, i) => `
    <div class="dna-row">
      <span class="dna-rank">${i + 1}</span>
      <span class="dna-vendor">${escHtml(v.description)}</span>
      <span class="dna-meta">${v.count}×</span>
      <span class="dna-amount">${fmt(v.total_cad)}</span>
    </div>`).join("") || '<div style="padding:8px 0;font-size:12px;color:var(--text-muted);">No data</div>';

  const recurring = (data.recurring || []).map(v => `
    <div class="dna-row">
      <span class="dna-vendor">${escHtml(v.description)}</span>
      <span class="dna-meta">${v.months_seen}mo</span>
      <span class="dna-amount">${fmt(v.avg_monthly)}/mo</span>
    </div>`).join("") || '<div style="padding:8px 0;font-size:12px;color:var(--text-muted);">None detected</div>';

  const toReview = (data.subscriptions_to_review || []).map(v => {
    const flagged = (data.flagged_vendors || []).some(f => f.description === v.description);
    return `<div class="dna-row">
      <span class="dna-vendor">${escHtml(v.description)}<span class="dna-review-badge">REVIEW</span></span>
      <span class="dna-amount">${fmt(v.avg_monthly)}/mo</span>
      <button class="dna-flag-btn ${flagged ? "flagged" : ""}"
        onclick="flagVendor('${escHtml(v.description).replace(/'/g, "\\'")}', this)">
        ${flagged ? "🚩 Flagged" : "Flag"}
      </button>
    </div>`;
  }).join("") || '<div style="padding:8px 0;font-size:12px;color:var(--text-muted);">None</div>';

  const spikes = (data.spending_spikes || []).map(s => `
    <div class="dna-spike">
      <div class="dna-spike-cat">${labelFor(s.category)}</div>
      <div class="dna-spike-desc ${Math.abs(s.change_cad) > 200 ? "" : "amber"}">
        jumped ${s.change_cad >= 0 ? "+" : ""}${fmt(s.change_cad)} in ${s.month} vs prior month
      </div>
    </div>`).join("") || '<div style="padding:8px 0;font-size:12px;color:var(--text-muted);">No significant spikes</div>';

  content.innerHTML = `<div class="dna-grid">
    <div class="dna-sub">
      <div class="dna-sub-title">🏆 Top Vendors (3mo)</div>
      ${topVendors}
    </div>
    <div class="dna-sub">
      <div class="dna-sub-title">🔁 Recurring Charges</div>
      ${recurring}
    </div>
    <div class="dna-sub">
      <div class="dna-sub-title">⚠️ Subscriptions to Review</div>
      ${toReview}
    </div>
    <div class="dna-sub">
      <div class="dna-sub-title">📈 Spending Spikes (MoM)</div>
      ${spikes}
    </div>
  </div>`;
}

async function flagVendor(description, btn) {
  const alreadyFlagged = btn.classList.contains("flagged");
  if (alreadyFlagged) {
    await fetch("/api/spending-dna/flag", {
      method: "DELETE", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ description })
    });
    btn.classList.remove("flagged");
    btn.textContent = "Flag";
  } else {
    await fetch("/api/spending-dna/flag", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ description, flag: "cancel" })
    });
    btn.classList.add("flagged");
    btn.textContent = "🚩 Flagged";
  }
}

/* ══════════════════════════════════════════════════════════════════════════════
   FEATURE 5 — NET WORTH BANNER
══════════════════════════════════════════════════════════════════════════════ */

async function loadNetWorth() {
  try {
    const res = await fetch("/api/net-worth");
    if (!res.ok) return;
    const data = await res.json();

    const total = (data.cash_balance_cad || 0) + (data.investment_value_cad || 0);
    document.getElementById("nwTotal").textContent = fmt(total);
    document.getElementById("nwCash").textContent = fmt(data.cash_balance_cad || 0);
    document.getElementById("nwInvest").textContent = fmt(data.investment_value_cad || 0);

    const fxEl = document.getElementById("nwFx");
    if (data.fx_rate) {
      fxEl.textContent = `FX: 1 USD = ${data.fx_rate.toFixed(4)} CAD`;
    }
  } catch (e) { /* silent fail */ }
}

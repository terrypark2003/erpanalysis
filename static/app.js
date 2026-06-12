/* 재고 분석 대시보드 프론트엔드 */
"use strict";

let DATA = null;
const charts = {};

/* ---------- 포맷 유틸 ---------- */
const nf = new Intl.NumberFormat("ko-KR");
const fmtNum = (v, digits = 0) =>
  v === null || v === undefined ? "-" : nf.format(Number(v.toFixed ? v.toFixed(digits) : v));
const fmtMoney = (v) => (v === null || v === undefined ? "-" : nf.format(Math.round(v)) + "원");
const fmtMoneyShort = (v) => {
  if (v === null || v === undefined) return "-";
  const abs = Math.abs(v);
  if (abs >= 1e8) return (v / 1e8).toFixed(1) + "억원";
  if (abs >= 1e4) return nf.format(Math.round(v / 1e4)) + "만원";
  return nf.format(Math.round(v)) + "원";
};
const fmtPct = (v, digits = 1) =>
  v === null || v === undefined ? "-" : (v * 100).toFixed(digits) + "%";
const fmtDays = (v) => (v === null || v === undefined ? "-" : fmtNum(v, 1) + "일");

const gradeChip = (g) => {
  const map = { "정상": "chip-normal", "주의": "chip-caution", "체화": "chip-stagnant", "악성": "chip-dead" };
  return `<span class="chip ${map[g] || "chip-flat"}">${g}</span>`;
};
const trendChip = (t) => {
  const map = { "상승": "chip-up", "하락": "chip-down", "유지": "chip-flat", "판매없음": "chip-none" };
  return `<span class="chip ${map[t] || "chip-flat"}">${t}</span>`;
};
const abcChip = (g) => `<span class="chip chip-${g.toLowerCase()}">${g}</span>`;
const actionChip = (a) => {
  if (!a) return "";
  const cls = a.includes("긴급") || a.includes("처분") ? "chip-action" : "chip-action2";
  return `<span class="chip ${cls}">${a}</span>`;
};
const nameCell = (r) =>
  `<div class="item-name">${r.name}</div><div class="item-code">${r.code} · ${r.category}</div>`;

/* ---------- 정렬 가능한 테이블 렌더러 ---------- */
function renderTable(el, columns, rows, opts = {}) {
  const state = el._sortState || { key: opts.defaultSort || null, dir: -1 };
  el._sortState = state;

  const sorted = [...rows];
  if (state.key) {
    const col = columns.find((c) => c.key === state.key);
    sorted.sort((a, b) => {
      let va = col && col.sortValue ? col.sortValue(a) : a[state.key];
      let vb = col && col.sortValue ? col.sortValue(b) : b[state.key];
      if (va === null || va === undefined) va = -Infinity;
      if (vb === null || vb === undefined) vb = -Infinity;
      if (typeof va === "string") return va.localeCompare(vb, "ko") * state.dir;
      return (va - vb) * state.dir;
    });
  }

  const limit = opts.limit || Infinity;
  const shown = sorted.slice(0, limit);

  let html = "<thead><tr>";
  for (const c of columns) {
    const arrow = state.key === c.key ? `<span class="arrow">${state.dir > 0 ? "▲" : "▼"}</span>` : "";
    html += `<th class="${c.left ? "left" : ""}" data-key="${c.key}">${c.label} ${arrow}</th>`;
  }
  html += "</tr></thead><tbody>";
  for (const r of shown) {
    html += "<tr>";
    for (const c of columns) {
      html += `<td class="${c.left ? "left" : ""}">${c.render(r)}</td>`;
    }
    html += "</tr>";
  }
  if (!shown.length) {
    html += `<tr><td class="left" colspan="${columns.length}" style="color:#9ca3af">해당 품목이 없습니다.</td></tr>`;
  }
  html += "</tbody>";
  el.innerHTML = html;

  el.querySelectorAll("th").forEach((th) => {
    th.onclick = () => {
      const key = th.dataset.key;
      if (state.key === key) state.dir *= -1;
      else { state.key = key; state.dir = -1; }
      renderTable(el, columns, rows, opts);
    };
  });
}

/* ---------- CSV 다운로드 ---------- */
function downloadCsv(filename, headers, rows) {
  const esc = (v) => {
    if (v === null || v === undefined) v = "";
    v = String(v);
    return /[",\n]/.test(v) ? `"${v.replace(/"/g, '""')}"` : v;
  };
  const lines = [headers.map(esc).join(",")];
  for (const r of rows) lines.push(r.map(esc).join(","));
  // BOM을 붙여 엑셀에서 한글이 깨지지 않게 한다
  const blob = new Blob(["﻿" + lines.join("\n")], { type: "text/csv;charset=utf-8" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = filename;
  a.click();
  URL.revokeObjectURL(a.href);
}

/* ---------- 차트 ---------- */
function makeChart(id, config) {
  if (charts[id]) charts[id].destroy();
  const ctx = document.getElementById(id);
  if (!ctx || typeof Chart === "undefined") return;
  charts[id] = new Chart(ctx, config);
}

/* 진행 중인 달은 라벨에 표시(데이터가 일부만 쌓여 낮게 보이므로) */
function monthLabels() {
  const labels = [...DATA.month_keys];
  const asOf = new Date(DATA.as_of + "T00:00:00");
  const lastDay = new Date(asOf.getFullYear(), asOf.getMonth() + 1, 0).getDate();
  if (asOf.getDate() < lastDay && labels.length) {
    labels[labels.length - 1] += " (진행중)";
  }
  return labels;
}

function renderCharts() {
  const s = DATA.summary;
  const labels = monthLabels();

  makeChart("chart-monthly", {
    type: "line",
    data: {
      labels,
      datasets: [{
        label: "월별 출고금액",
        data: s.monthly_out_amt,
        borderColor: "#2563eb",
        backgroundColor: "rgba(37,99,235,.12)",
        fill: true,
        tension: 0.3,
      }],
    },
    options: {
      plugins: { legend: { display: false } },
      scales: { y: { ticks: { callback: (v) => fmtMoneyShort(v) } } },
    },
  });

  makeChart("chart-abc-donut", {
    type: "doughnut",
    data: {
      labels: ["A등급", "B등급", "C등급"],
      datasets: [{
        data: [s.abc_amounts.A, s.abc_amounts.B, s.abc_amounts.C],
        backgroundColor: ["#2563eb", "#94a3b8", "#c4b5fd"],
      }],
    },
    options: {
      plugins: {
        legend: { position: "bottom" },
        tooltip: { callbacks: { label: (c) =>
          ` ${c.label}: ${s.abc_basis === "qty" ? fmtNum(c.raw) + "개" : fmtMoneyShort(c.raw)}` } },
      },
    },
  });

  const withTurnover = DATA.items.filter((r) => r.turnover !== null);
  const top = [...withTurnover].sort((a, b) => b.turnover - a.turnover).slice(0, 15);
  const bottomPool = withTurnover.filter((r) => r.cur_qty > 0);
  const bottom = [...bottomPool].sort((a, b) => a.turnover - b.turnover).slice(0, 15);

  const barOpts = {
    indexAxis: "y",
    plugins: { legend: { display: false } },
    scales: { x: { title: { display: true, text: "회전율(회/년)" } } },
  };
  makeChart("chart-top-turnover", {
    type: "bar",
    data: {
      labels: top.map((r) => r.name),
      datasets: [{ data: top.map((r) => r.turnover), backgroundColor: "#16a34a" }],
    },
    options: barOpts,
  });
  makeChart("chart-bottom-turnover", {
    type: "bar",
    data: {
      labels: bottom.map((r) => r.name),
      datasets: [{ data: bottom.map((r) => r.turnover), backgroundColor: "#dc2626" }],
    },
    options: barOpts,
  });

  // 파레토: 출고금액 상위 30 + 누적 점유율
  const byAmt = [...DATA.items].sort((a, b) => b.out_amount - a.out_amount).slice(0, 30);
  const total = DATA.items.reduce((acc, r) => acc + r.out_amount, 0) || 1;
  let cum = 0;
  const cumPct = byAmt.map((r) => { cum += r.out_amount; return +(cum / total * 100).toFixed(1); });
  makeChart("chart-pareto", {
    data: {
      labels: byAmt.map((r) => r.name),
      datasets: [
        { type: "bar", label: "출고금액", data: byAmt.map((r) => r.out_amount),
          backgroundColor: byAmt.map((r) => r.abc === "A" ? "#2563eb" : r.abc === "B" ? "#94a3b8" : "#c4b5fd"),
          yAxisID: "y" },
        { type: "line", label: "누적 점유율(%)", data: cumPct, borderColor: "#dc2626",
          yAxisID: "y2", tension: 0.2, pointRadius: 2 },
      ],
    },
    options: {
      scales: {
        x: { ticks: { maxRotation: 60, minRotation: 45, font: { size: 10 } } },
        y: { ticks: { callback: (v) => fmtMoneyShort(v) } },
        y2: { position: "right", min: 0, max: 100, ticks: { callback: (v) => v + "%" }, grid: { drawOnChartArea: false } },
      },
    },
  });

  renderTrendChart("amt");
}

function renderTrendChart(mode) {
  const s = DATA.summary;
  makeChart("chart-trend", {
    type: "line",
    data: {
      labels: monthLabels(),
      datasets: [{
        label: mode === "amt" ? "출고금액" : "출고수량",
        data: mode === "amt" ? s.monthly_out_amt : s.monthly_out_qty,
        borderColor: "#2563eb",
        backgroundColor: "rgba(37,99,235,.12)",
        fill: true,
        tension: 0.3,
      }],
    },
    options: {
      plugins: { legend: { display: false } },
      scales: { y: { ticks: { callback: (v) => mode === "amt" ? fmtMoneyShort(v) : fmtNum(v) } } },
    },
  });
}

/* ---------- KPI ---------- */
function renderKpis() {
  const s = DATA.summary;
  if (s.total_stock_amount > 0) {
    document.getElementById("kpi-stock-amount").textContent = fmtMoneyShort(s.total_stock_amount);
    document.getElementById("kpi-item-count").textContent = `분석 품목 ${fmtNum(s.item_count)}개`;
  } else {
    document.getElementById("kpi-stock-amount").textContent = "—";
    document.getElementById("kpi-item-count").textContent =
      `분석 품목 ${fmtNum(s.item_count)}개 · 단가 미등록(품목등록에 단가 입력 시 금액 표시)`;
  }
  document.getElementById("kpi-turnover").textContent =
    s.overall_turnover === null ? "-" : s.overall_turnover.toFixed(1) + "회/년";
  document.getElementById("kpi-doi").textContent =
    s.overall_turnover ? `평균 재고일수 약 ${Math.round(365 / s.overall_turnover)}일` : "";
  document.getElementById("kpi-stagnant-amount").textContent = fmtMoneyShort(s.stagnant_amount);
  document.getElementById("kpi-stagnant-ratio").textContent =
    `재고자산의 ${fmtPct(s.stagnant_ratio)} · ${s.stagnant_count}개 품목 (악성 ${s.dead_count}개)`;
  document.getElementById("kpi-reorder").textContent = `${s.reorder_count}개`;
  document.getElementById("kpi-imminent").textContent =
    `리드타임 내 품절 임박 ${s.imminent_stockout_count}개`;
  document.getElementById("kpi-trend").textContent = `↑${s.trend_up_count} ↓${s.trend_down_count}`;
  document.getElementById("kpi-nosales").textContent = `판매 없는 품목 ${s.no_sales_count}개`;
}

/* ---------- 테이블들 ---------- */
const COL = {
  name: { key: "name", label: "품목", left: true, render: nameCell },
  curQty: { key: "cur_qty", label: "현재고", render: (r) => fmtNum(r.cur_qty) },
  stockAmt: { key: "stock_amount", label: "재고금액", render: (r) => fmtMoney(r.stock_amount) },
  outQty: { key: "out_qty", label: "기간 출고량", render: (r) => fmtNum(r.out_qty) },
  outAmt: { key: "out_amount", label: "기간 출고금액", render: (r) => fmtMoney(r.out_amount) },
  turnover: { key: "turnover", label: "회전율(회/년)", render: (r) => r.turnover === null ? "-" : fmtNum(r.turnover, 1) },
  doi: { key: "doi_days", label: "재고일수", render: (r) => fmtDays(r.doi_days) },
  stockout: { key: "days_to_stockout", label: "소진예상", render: (r) => fmtDays(r.days_to_stockout) },
  abc: { key: "abc", label: "ABC", render: (r) => abcChip(r.abc) },
  grade: { key: "stagnant_grade", label: "체화등급", render: (r) => gradeChip(r.stagnant_grade),
           sortValue: (r) => ({ "정상": 0, "주의": 1, "체화": 2, "악성": 3 }[r.stagnant_grade]) },
  trend: { key: "trend", label: "추세", render: (r) => trendChip(r.trend),
           sortValue: (r) => ({ "상승": 3, "유지": 2, "하락": 1, "판매없음": 0 }[r.trend]) },
  growth: { key: "growth_3m", label: "3개월 성장률", render: (r) => fmtPct(r.growth_3m) },
  lastOut: { key: "last_out_date", label: "마지막 출고", render: (r) => r.last_out_date || "출고 없음",
             sortValue: (r) => r.last_out_date || "" },
  sinceOut: { key: "days_since_out", label: "경과일", render: (r) => fmtNum(r.days_since_out) + "일" },
  cross: { key: "cross_ratio", label: "교차비율", render: (r) => r.cross_ratio === null ? "-" : fmtNum(r.cross_ratio, 1) },
  margin: { key: "margin_rate", label: "마진율", render: (r) => fmtPct(r.margin_rate) },
  action: { key: "action", label: "추천 액션", render: (r) => actionChip(r.action) },
  safety: { key: "safety_stock", label: "안전재고", render: (r) => fmtNum(r.safety_stock, 1) },
  rop: { key: "reorder_point", label: "재주문점", render: (r) => fmtNum(r.reorder_point, 1) },
  avgDaily: { key: "avg_daily_out", label: "일평균출고", render: (r) => fmtNum(r.avg_daily_out, 2) },
  suggested: { key: "suggested_order", label: "권장발주량", render: (r) => fmtNum(r.suggested_order) },
};

function renderTables() {
  const items = DATA.items;

  const actions = items.filter((r) => r.action);
  renderTable(document.getElementById("table-actions"),
    [COL.name, COL.action, COL.abc, COL.grade, COL.trend, COL.curQty, COL.stockAmt, COL.stockout, COL.turnover],
    actions, { defaultSort: "stock_amount" });

  renderTable(document.getElementById("table-turnover"),
    [COL.name, COL.turnover, COL.doi, COL.outQty, COL.outAmt, COL.curQty, COL.avgDaily, COL.stockout, COL.abc],
    items, { defaultSort: "turnover" });

  const abcItems = items.filter((r) => r.out_amount > 0);
  renderTable(document.getElementById("table-abc"),
    [COL.name, COL.abc,
     { key: "out_amount", label: "기간 출고금액", render: (r) => fmtMoney(r.out_amount) },
     { key: "cum_share", label: "누적 점유율", render: (r) => fmtPct(r.cum_share) },
     COL.turnover, COL.cross, COL.margin, COL.trend],
    abcItems, { defaultSort: "out_amount" });

  const abcS = DATA.summary;
  const abcVal = (v) => abcS.abc_basis === "qty" ? `출고 ${fmtNum(v)}개` : `매출 ${fmtMoneyShort(v)}`;
  document.getElementById("abc-summary").innerHTML = ["A", "B", "C"].map((g) =>
    `<div class="abc-pill">${abcChip(g)} <b>${abcS.abc_counts[g]}</b>개 품목 · ${abcVal(abcS.abc_amounts[g])}</div>`
  ).join("");

  renderStagnantTable();
  document.getElementById("stagnant-caption").textContent =
    `— 기준: 주의 ${DATA.params.caution_days}일↑ · 체화 ${DATA.params.stagnant_days}일↑ · 악성 ${DATA.params.dead_days}일↑ 무출고(재고 보유 품목)`;

  const ups = items.filter((r) => r.trend === "상승" && r.growth_3m !== null)
    .sort((a, b) => b.growth_3m - a.growth_3m).slice(0, 20);
  const downs = items.filter((r) => r.trend === "하락" && r.growth_3m !== null)
    .sort((a, b) => a.growth_3m - b.growth_3m).slice(0, 20);
  const trendCols = [COL.name, COL.growth, COL.outQty, COL.curQty, COL.stockout, COL.abc];
  renderTable(document.getElementById("table-trend-up"), trendCols, ups, { defaultSort: "growth_3m" });
  const downTable = document.getElementById("table-trend-down");
  downTable._sortState = { key: "growth_3m", dir: 1 };
  renderTable(downTable, trendCols, downs);

  renderTable(document.getElementById("table-reorder"),
    [COL.name, COL.curQty, COL.avgDaily, COL.safety, COL.rop, COL.suggested, COL.stockout, COL.abc, COL.trend],
    items.filter((r) => r.need_reorder), { defaultSort: "days_to_stockout" });
  document.getElementById("reorder-caption").textContent =
    `— 현재고 ≤ 재주문점 · 리드타임 ${DATA.params.lead_time_days}일 · 서비스수준 ${Math.round(DATA.params.service_level * 100)}%`;

  renderAllTable();
  renderPricesTable();
}

function renderStagnantTable() {
  const want = new Set();
  if (document.getElementById("chk-dead").checked) want.add("악성");
  if (document.getElementById("chk-stagnant").checked) want.add("체화");
  if (document.getElementById("chk-caution").checked) want.add("주의");
  const rows = DATA.items.filter((r) => want.has(r.stagnant_grade));
  renderTable(document.getElementById("table-stagnant"),
    [COL.name, COL.grade, COL.lastOut, COL.sinceOut, COL.curQty, COL.stockAmt, COL.abc, COL.action],
    rows, { defaultSort: "stock_amount" });
}

function renderAllTable() {
  const q = (document.getElementById("search-all").value || "").trim().toLowerCase();
  const rows = !q ? DATA.items : DATA.items.filter((r) =>
    (r.name + r.code + r.category).toLowerCase().includes(q));
  renderTable(document.getElementById("table-all"),
    [COL.name, COL.curQty, COL.stockAmt, COL.turnover, COL.doi, COL.stockout,
     COL.abc, COL.grade, COL.trend, COL.cross, COL.action],
    rows, { defaultSort: "stock_amount" });
}

/* ---------- 단가 관리(브라우저 저장 오버라이드) ---------- */
const PRICE_KEY = "price_overrides_v1";
let priceDraft = null; // 입력 중 값(저장 전). null이면 아직 미초기화

function loadPriceOverrides() {
  try { return JSON.parse(localStorage.getItem(PRICE_KEY) || "{}"); }
  catch { return {}; }
}

function updatePriceCount() {
  const saved = Object.keys(loadPriceOverrides()).length;
  const draft = Object.keys(priceDraft || {}).length;
  const el = document.getElementById("price-count");
  el.textContent = `저장된 단가 ${saved}개` +
    (draft !== saved ? ` · 입력 중 ${draft}개 (저장 버튼을 누르세요)` : "");
}

function renderPricesTable() {
  if (priceDraft === null) priceDraft = loadPriceOverrides();
  const q = (document.getElementById("search-prices").value || "").trim().toLowerCase();
  const rows = !q ? DATA.items : DATA.items.filter((r) =>
    (r.name + r.code + r.category).toLowerCase().includes(q));
  const el = document.getElementById("table-prices");
  let html = "<thead><tr><th class='left'>품목</th><th>현재 구매단가</th><th>현재 판매단가</th>" +
    "<th>내 구매단가(원가)</th><th>내 판매단가</th></tr></thead><tbody>";
  for (const r of rows) {
    const d = priceDraft[r.code] || {};
    html += `<tr><td class="left">${nameCell(r)}</td>` +
      `<td>${r.in_price > 0 ? fmtMoney(r.in_price) : '<span class="muted">미등록</span>'}</td>` +
      `<td>${r.out_price > 0 ? fmtMoney(r.out_price) : '<span class="muted">미등록</span>'}</td>` +
      `<td><input type="number" min="0" step="any" class="price-input" data-code="${r.code}" data-field="in_price" value="${d.in_price ?? ""}"></td>` +
      `<td><input type="number" min="0" step="any" class="price-input" data-code="${r.code}" data-field="out_price" value="${d.out_price ?? ""}"></td></tr>`;
  }
  if (!rows.length) html += `<tr><td class="left" colspan="5" style="color:#9ca3af">해당 품목이 없습니다.</td></tr>`;
  html += "</tbody>";
  el.innerHTML = html;
  el.querySelectorAll(".price-input").forEach((inp) => {
    inp.addEventListener("change", () => {
      const code = inp.dataset.code, field = inp.dataset.field;
      const v = parseFloat(inp.value);
      if (!priceDraft[code]) priceDraft[code] = {};
      if (v > 0) priceDraft[code][field] = v;
      else delete priceDraft[code][field];
      if (!Object.keys(priceDraft[code]).length) delete priceDraft[code];
      updatePriceCount();
    });
  });
  updatePriceCount();
}

/* 따옴표·쉼표가 든 품목명도 안전하게 읽는 단순 CSV 파서 */
function parseCsv(text) {
  const rows = [];
  let row = [], field = "", inQ = false;
  const s = text.replace(/^﻿/, "");
  for (let i = 0; i < s.length; i++) {
    const c = s[i];
    if (inQ) {
      if (c === '"') { if (s[i + 1] === '"') { field += '"'; i++; } else inQ = false; }
      else field += c;
    } else if (c === '"') inQ = true;
    else if (c === ",") { row.push(field); field = ""; }
    else if (c === "\n" || c === "\r") {
      if (c === "\r" && s[i + 1] === "\n") i++;
      row.push(field); field = "";
      if (row.length > 1 || row[0] !== "") rows.push(row);
      row = [];
    } else field += c;
  }
  if (field !== "" || row.length) { row.push(field); rows.push(row); }
  return rows;
}

function bindPriceEvents() {
  document.getElementById("search-prices").addEventListener("input", renderPricesTable);

  document.getElementById("btn-price-save").onclick = async () => {
    localStorage.setItem(PRICE_KEY, JSON.stringify(priceDraft || {}));
    await loadDashboard(); // 저장된 단가로 서버 재계산 → 전체 화면 갱신
  };

  document.getElementById("btn-price-clear").onclick = async () => {
    if (!confirm("입력한 단가를 모두 삭제할까요? (ECOUNT 단가로 되돌아갑니다)")) return;
    priceDraft = {};
    localStorage.removeItem(PRICE_KEY);
    await loadDashboard();
  };

  document.getElementById("btn-price-csv").onclick = () => {
    const ov = priceDraft || loadPriceOverrides();
    downloadCsv("단가입력_양식.csv", ["품목코드", "품목명", "구매단가(원가)", "판매단가"],
      DATA.items.map((r) => {
        const d = ov[r.code] || {};
        return [r.code, r.name,
          d.in_price ?? (r.in_price > 0 ? r.in_price : ""),
          d.out_price ?? (r.out_price > 0 ? r.out_price : "")];
      }));
  };

  document.getElementById("btn-price-import").onclick = () =>
    document.getElementById("file-price-csv").click();
  document.getElementById("file-price-csv").addEventListener("change", async (e) => {
    const file = e.target.files[0];
    if (!file) return;
    const text = await file.text();
    e.target.value = "";
    const codes = new Set(DATA.items.map((r) => r.code));
    priceDraft = priceDraft || loadPriceOverrides();
    let applied = 0;
    for (const cols of parseCsv(text)) {
      const code = (cols[0] || "").trim();
      if (!code || !codes.has(code)) continue; // 헤더·알 수 없는 코드 제외
      const ip = parseFloat(String(cols[2] ?? "").replace(/,/g, ""));
      const op = parseFloat(String(cols[3] ?? "").replace(/,/g, ""));
      const entry = {};
      if (ip > 0) entry.in_price = ip;
      if (op > 0) entry.out_price = op;
      if (Object.keys(entry).length) { priceDraft[code] = entry; applied++; }
    }
    localStorage.setItem(PRICE_KEY, JSON.stringify(priceDraft));
    alert(`${applied}개 품목의 단가를 불러와 저장했습니다.`);
    await loadDashboard();
  });
}

/* ---------- CSV 핸들러 ---------- */
const CSV_HEADERS = ["품목코드", "품목명", "카테고리", "현재고", "재고금액", "기간출고량", "기간출고금액",
  "회전율(연)", "재고일수", "소진예상일", "ABC", "체화등급", "마지막출고일", "무출고경과일",
  "추세", "3개월성장률(%)", "안전재고", "재주문점", "권장발주량", "마진율(%)", "교차비율", "추천액션"];
const csvRow = (r) => [r.code, r.name, r.category, r.cur_qty, r.stock_amount, r.out_qty, r.out_amount,
  r.turnover, r.doi_days, r.days_to_stockout, r.abc, r.stagnant_grade, r.last_out_date, r.days_since_out,
  r.trend, r.growth_3m === null ? "" : (r.growth_3m * 100).toFixed(1), r.safety_stock, r.reorder_point,
  r.suggested_order, r.margin_rate === null ? "" : (r.margin_rate * 100).toFixed(1), r.cross_ratio, r.action];

function bindEvents() {
  document.querySelectorAll(".tab").forEach((tab) => {
    tab.onclick = () => {
      document.querySelectorAll(".tab").forEach((t) => t.classList.remove("active"));
      document.querySelectorAll(".panel").forEach((p) => p.classList.remove("active"));
      tab.classList.add("active");
      document.getElementById("panel-" + tab.dataset.tab).classList.add("active");
    };
  });

  ["chk-dead", "chk-stagnant", "chk-caution"].forEach((id) =>
    document.getElementById(id).addEventListener("change", renderStagnantTable));
  document.getElementById("search-all").addEventListener("input", renderAllTable);
  document.querySelectorAll('input[name="trend-mode"]').forEach((radio) =>
    radio.addEventListener("change", (e) => renderTrendChart(e.target.value)));

  document.getElementById("btn-csv-all").onclick = () =>
    downloadCsv("재고분석_전체품목.csv", CSV_HEADERS, DATA.items.map(csvRow));
  document.getElementById("btn-csv-stagnant").onclick = () =>
    downloadCsv("체화악성재고.csv", CSV_HEADERS,
      DATA.items.filter((r) => ["주의", "체화", "악성"].includes(r.stagnant_grade)).map(csvRow));
  document.getElementById("btn-csv-reorder").onclick = () =>
    downloadCsv("발주제안.csv", CSV_HEADERS, DATA.items.filter((r) => r.need_reorder).map(csvRow));

  document.getElementById("btn-refresh").onclick = async () => {
    const btn = document.getElementById("btn-refresh");
    btn.disabled = true;
    btn.textContent = "수집 중…";
    try {
      const res = await fetch("/api/refresh", { method: "POST" });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || `HTTP ${res.status}`);
      }
      await loadDashboard();
    } catch (e) {
      showError("데이터 새로고침 실패:\n" + e.message);
    } finally {
      btn.disabled = false;
      btn.textContent = "⟳ 데이터 새로고침";
    }
  };
}

/* ---------- 로딩 ---------- */
function showError(msg) {
  const box = document.getElementById("error-box");
  box.textContent = msg;
  box.classList.remove("hidden");
}

async function loadDashboard() {
  document.getElementById("error-box").classList.add("hidden");
  const ov = loadPriceOverrides();
  const res = Object.keys(ov).length
    ? await fetch("/api/dashboard", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ price_overrides: ov }),
      })
    : await fetch("/api/dashboard");
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    document.getElementById("loading").classList.add("hidden");
    showError("데이터를 불러오지 못했습니다:\n" + (err.detail || `HTTP ${res.status}`) +
      "\n\nECOUNT 연동 설정은 README를 참고하세요. (.env 없이 실행하면 데모 데이터로 동작합니다)");
    return;
  }
  DATA = await res.json();

  const badge = document.getElementById("source-badge");
  if (DATA.meta.source === "demo") {
    badge.textContent = "데모 데이터";
    badge.className = "badge badge-demo";
  } else {
    badge.textContent = "ECOUNT 실데이터";
    badge.className = "badge badge-live";
  }
  document.getElementById("fetched-at").textContent =
    DATA.meta.fetched_at ? "수집: " + DATA.meta.fetched_at.replace("T", " ") : "";
  document.getElementById("period-label").textContent =
    `${DATA.period_start} ~ ${DATA.as_of} (${DATA.period_months}개월)`;
  document.getElementById("source-label").textContent =
    DATA.meta.source === "demo" ? "데모 샘플 데이터" : "ECOUNT Open API";

  renderKpis();
  renderTables();
  if (typeof Chart !== "undefined") {
    renderCharts();
  } else {
    showError("차트 라이브러리(Chart.js CDN) 로딩에 실패해 표만 표시합니다. 인터넷 연결을 확인하세요.");
  }

  document.getElementById("loading").classList.add("hidden");
  document.getElementById("dashboard").classList.remove("hidden");
}

bindEvents();
bindPriceEvents();
loadDashboard().catch((e) => {
  document.getElementById("loading").classList.add("hidden");
  showError("대시보드 초기화 실패: " + e.message);
});

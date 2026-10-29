const main = document.querySelector("#main");
const toast = document.querySelector("#toast");
const WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

const state = {
  renderId: 0,
  tab: "month",
  selected: "",
  dash: null,
  flow: null,
  editingInvoice: null,
  payDraft: null,
  productDraft: null,
  invoiceOpen: false,
  itemOpen: false,
  supplier: "",
  invoiceDate: "",
  lines: [],
  photoInvoice: null,
  photoTotals: null,
  saleDraft: null,
  suggestion: "",
  pasteTarget: "totals",
};

function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, (ch) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[ch]));
}

function money(cents) {
  const amount = Math.abs(Number(cents) || 0) / 100;
  const text = amount.toLocaleString("en-US", { style: "currency", currency: "USD" });
  return Number(cents) < 0 ? `-${text}` : text;
}

function shortMoney(cents) {
  if (!cents) return "";
  const dollars = (Number(cents) || 0) / 100;
  return dollars.toLocaleString("en-US", { maximumFractionDigits: 0 });
}

function dollarsToCents(value) {
  const text = String(value ?? "").trim().replace(/[$,]/g, "");
  if (!text) return 0;
  const number = Number(text);
  if (!Number.isFinite(number) || number < 0) return null;
  return Math.round(number * 100);
}

function centsToInput(cents) {
  return ((Number(cents) || 0) / 100).toFixed(2);
}

function qtyText(value) {
  const number = Number(value);
  if (!Number.isFinite(number)) return "";
  return String(Math.round(number * 1000) / 1000);
}

function showToast(message) {
  toast.hidden = false;
  toast.textContent = message;
  clearTimeout(showToast.timer);
  showToast.timer = setTimeout(() => { toast.hidden = true; }, 4200);
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    method: options.method || "GET",
    headers: options.body ? { "Content-Type": "application/json" } : undefined,
    body: options.body ? JSON.stringify(options.body) : undefined,
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error || "Something went wrong.");
  return data;
}

function filePayload(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve({ image_base64: reader.result, filename: file.name });
    reader.onerror = () => reject(new Error("Could not read that picture."));
    reader.readAsDataURL(file);
  });
}

function selectedDay() {
  return state.dash.days.find((day) => day.date === state.dash.selected);
}

function longDate(iso) {
  const [year, month, day] = iso.split("-").map(Number);
  return new Date(year, month - 1, day).toLocaleDateString("en-US", {
    weekday: "long", month: "long", day: "numeric",
  });
}

function shiftMonth(iso, delta) {
  const [year, month, day] = iso.split("-").map(Number);
  const date = new Date(year, month - 1 + delta, 1);
  const last = new Date(date.getFullYear(), date.getMonth() + 1, 0).getDate();
  date.setDate(Math.min(day, last));
  const y = date.getFullYear();
  const m = String(date.getMonth() + 1).padStart(2, "0");
  const d = String(date.getDate()).padStart(2, "0");
  return `${y}-${m}-${d}`;
}

function payBox() {
  const draft = state.payDraft;
  if (!draft) return "";
  return `<div class="pay-box">
    <label for="pay-amount">Amount paid</label>
    <input id="pay-amount" inputmode="decimal" value="${esc(draft.amount)}" aria-label="Amount paid">
    <label for="pay-date">Date paid</label>
    <input id="pay-date" type="date" value="${esc(draft.date)}" aria-label="Date paid">
    <button type="button" class="primary" id="save-payment">Save payment</button>
    <button type="button" class="quiet" id="cancel-payment">Cancel</button>
  </div>
  <p class="muted">This lowers what is still owed. The cost stays on the day the invoice was bought.</p>`;
}

function blankLine() {
  return { name: "", quantity: "", unit: "", category: "grocery" };
}

function tabs() {
  const current = (name) => state.tab === name ? ` aria-current="page"` : "";
  return `<nav class="tabs">
    <button type="button" data-tab="month"${current("month")}>Month</button>
    <button type="button" data-tab="money"${current("money")}>Money</button>
  </nav>`;
}

async function load(date) {
  const mine = ++state.renderId;
  if (date && date !== state.selected) state.saleDraft = null;
  if (date) state.selected = date;
  const query = state.selected ? `?date=${encodeURIComponent(state.selected)}` : "";
  const data = await api(`/api/dashboard${query}`);
  if (mine !== state.renderId) return;
  state.dash = data;
  state.selected = data.selected;
  if (!state.invoiceDate) state.invoiceDate = data.selected;
  if (state.tab === "money") {
    state.flow = await api(`/api/flow?date=${encodeURIComponent(state.selected)}`);
    if (mine !== state.renderId) return;
  }
  render();
}

function render() {
  if (state.tab === "money" && state.flow) {
    renderMoney();
    return;
  }
  const dash = state.dash;
  const day = selectedDay();
  const blanks = Array.from({ length: dash.lead }, () => `<div></div>`).join("");
  const cells = dash.days.map((item) => {
    const pressed = item.date === dash.selected ? ` aria-pressed="true"` : "";
    const today = item.date === dash.today ? " today" : "";
    const rev = item.revenue_cents ? `<span class="rev">${esc(shortMoney(item.revenue_cents))}</span>` : "";
    const cost = item.cost_cents ? `<span class="cost">${esc(shortMoney(item.cost_cents))}</span>` : "";
    return `<button type="button" class="day${today}" data-date="${item.date}"${pressed}>
      <span class="num">${item.day}</span>${rev}${cost}
    </button>`;
  }).join("");
  main.innerHTML = `
    ${tabs()}
    <div class="top">
      <h1>${esc(dash.month_label)}</h1>
      <div class="month-nav">
        <button type="button" id="prev-month" aria-label="Previous month">←</button>
        <input id="jump-date" type="date" value="${esc(dash.selected)}" aria-label="Choose a day">
        <button type="button" id="next-month" aria-label="Next month">→</button>
      </div>
    </div>
    <section class="headlines">
      <article class="card">
        <p class="kicker">Revenue this month</p>
        <p class="figure revenue">${money(dash.revenue_cents)}</p>
        <p class="muted">Each day's sales, added together.</p>
      </article>
      <article class="card">
        <p class="kicker">Costs this month</p>
        <p class="figure cost">${money(dash.cost_cents)}</p>
        <p class="muted">Each invoice total, added on its date.</p>
      </article>
    </section>
    <p class="legend"><span class="rev">Sales</span><span class="cost">Costs</span></p>
    <div class="weekdays">${WEEKDAYS.map((name) => `<span>${name}</span>`).join("")}</div>
    <div class="month">${blanks}${cells}</div>
    ${dayPanel(day)}
    ${inventoryPanel()}
  `;
  wire();
}

function saleValues(day) {
  if (state.saleDraft && state.saleDraft.date === day.date) return state.saleDraft;
  return { meat: centsToInput(day.meat_cents), grocery: centsToInput(day.grocery_cents) };
}

function renderMoney() {
  const flow = state.flow;
  const row = (item) => `<div class="flow-row"><span>${esc(item.name)}</span><span class="num">${money(item.cents)}</span></div>`;
  const debts = flow.debts.length
    ? flow.debts.map((item) => `
      <div class="flow-row">
        <span>${esc(item.name)} <span class="due">${esc(item.due || "")}</span> <button type="button" data-pay-invoice="${item.id}" data-pay-amount="${item.cents}">Pay</button></span>
        <span class="num">${money(item.cents)}</span>
      </div>
      ${state.payDraft && state.payDraft.id === item.id ? payBox() : ""}`).join("")
    : `<p class="muted">No unpaid invoices.</p>`;
  main.innerHTML = `
    ${tabs()}
    <div class="top">
      <h1>${esc(flow.month_label)}</h1>
      <div class="month-nav">
        <button type="button" id="prev-month" aria-label="Previous month">←</button>
        <input id="jump-date" type="date" value="${esc(flow.selected)}" aria-label="Choose a month">
        <button type="button" id="next-month" aria-label="Next month">→</button>
      </div>
    </div>
    <section class="card flow">
      <h2>Money in</h2>
      ${flow.sales.map(row).join("")}
      <div class="flow-total"><span>Sales</span><span class="num">${money(flow.sales_total)}</span></div>
      <h2>Costs</h2>
      ${flow.costs.map(row).join("")}
      <div class="flow-total"><span>Costs</span><span class="num">${money(flow.cost_total)}</span></div>
      <div class="flow-total left"><span>Left after costs</span><span class="num">${money(flow.left)}</span></div>
      <h2>Debts</h2>
      <p class="muted">Unpaid invoices. Loans are not listed here, and this balance is not taken out of sales a second time.</p>
      ${debts}
      <div class="flow-total"><span>Still unpaid</span><span class="num">${money(flow.debt_total)}</span></div>
    </section>`;
  wire();
}

function dayPanel(day) {
  const values = saleValues(day);
  const invoices = day.invoices.map((invoice) => {
    const title = invoice.supplier === "By hand" && invoice.lines.length === 1
      ? invoice.lines[0].name
      : invoice.supplier;
    const detail = invoice.lines.map((line) => `${esc(line.name)} ${qtyText(line.quantity)} × ${money(line.unit_cost_cents)}`).join(" · ");
    const balance = invoice.unpaid_cents > 0
      ? `Paid ${money(invoice.paid_cents)} · Still owed ${money(invoice.unpaid_cents)}`
      : "Paid";
    const paying = state.payDraft && state.payDraft.id === invoice.id;
    return `<tr>
      <td>${esc(title)}${detail ? `<div class="item-sub">${detail}</div>` : ""}<div class="item-sub">${balance}</div></td>
      <td class="num">${money(invoice.owed_cents)}</td>
      <td>
        ${invoice.unpaid_cents > 0 ? `<button type="button" data-pay-invoice="${invoice.id}" data-pay-amount="${invoice.unpaid_cents}">Pay</button>` : ""}
        <button type="button" data-edit-invoice="${invoice.id}">Edit</button>
        <button type="button" class="quiet" data-remove-invoice="${invoice.id}">Remove</button>
        ${paying ? payBox() : ""}
      </td>
    </tr>`;
  }).join("");
  return `
    <section class="card" style="margin-top:14px" data-paste="totals">
      <p class="kicker">Accounting</p>
      <div class="day-head">
        <h2>${esc(longDate(day.date))}</h2>
        <p class="muted">${day.date === state.dash.today ? "Today" : "Saved here even if you enter it later."}</p>
      </div>
      <div class="split">
        <div>
          <h3>Sales</h3>
          <p class="muted">What came in this day. A screenshot only fills the boxes.</p>
          <div class="fields">
            <div><label for="meat">Meat</label><input id="meat" inputmode="decimal" value="${esc(values.meat)}"></div>
            <div><label for="grocery">Grocery</label><input id="grocery" inputmode="decimal" value="${esc(values.grocery)}"></div>
          </div>
          <div class="actions">
            <button type="button" class="primary" id="save-sales">Save sales</button>
            <label class="file-button">Use a screenshot<input id="sales-file" type="file" accept="image/*"></label>
          </div>
          ${state.suggestion && state.pasteTarget === "totals" ? `<p class="suggest">${esc(state.suggestion)}</p>` : ""}
        </div>
        <div data-paste="invoice">
          <div class="section-title">
            <h3>Invoices</h3>
            <button type="button" id="toggle-invoice">${state.invoiceOpen ? "Close" : "Add an invoice"}</button>
          </div>
          <p class="muted">The invoice total is this day's cost. Change the date if you missed a day.</p>
          ${invoices ? `<table><tbody>${invoices}</tbody></table>` : `<p class="muted">No invoices on this day.</p>`}
          ${state.invoiceOpen ? invoiceForm() : ""}
        </div>
      </div>
    </section>`;
}

function invoiceForm() {
  const rows = state.lines.map((line, index) => `
    <tr data-line="${index}">
      <td><input data-field="name" aria-label="Item" value="${esc(line.name)}" placeholder="Item"></td>
      <td><input data-field="quantity" aria-label="Quantity" value="${esc(line.quantity)}" placeholder="Qty"></td>
      <td><input data-field="unit" aria-label="Unit cost" value="${esc(line.unit)}" placeholder="Unit cost"></td>
      <td>
        <select data-field="category" aria-label="Kind">
          ${["meat", "grocery", "produce"].map((key) => `<option value="${key}"${line.category === key ? " selected" : ""}>${key[0].toUpperCase()}${key.slice(1)}</option>`).join("")}
        </select>
      </td>
      <td><button type="button" class="quiet" data-remove-line="${index}">Remove</button></td>
    </tr>`).join("");
  return `
    <div class="fields" style="margin-top:10px">
      <div><label for="supplier">Who it is from</label><input id="supplier" value="${esc(state.supplier)}"></div>
      <div><label for="invoice-date">Date of the invoice</label><input id="invoice-date" type="date" value="${esc(state.invoiceDate || state.dash.selected)}"></div>
    </div>
    <table>
      <thead><tr><th>Item</th><th>Qty</th><th>Unit cost</th><th>Kind</th><th></th></tr></thead>
      <tbody>${rows}</tbody>
    </table>
    <div class="actions">
      <button type="button" id="add-line">Add a line</button>
      <label class="file-button">Scan an invoice<input id="invoice-file" type="file" accept="image/*"></label>
      <button type="button" class="primary" id="save-invoice">${state.editingInvoice ? "Save changes" : "Save invoice"}</button>
    </div>
    <p class="muted">Change any box. Add a line if the reader missed one.</p>
    ${state.suggestion && state.pasteTarget === "invoice" ? `<p class="suggest">${esc(state.suggestion)}</p>` : ""}
  `;
}

function inventoryPanel() {
  const rows = state.dash.stock.map((item) => {
    if (state.productDraft && state.productDraft.id === item.id) {
      const draft = state.productDraft;
      return `<tr>
        <td><input id="edit-name" value="${esc(draft.name)}" aria-label="Item name"></td>
        <td><input id="edit-qty" value="${esc(draft.on_hand)}" aria-label="On hand"></td>
        <td><input id="edit-unit" value="${esc(draft.unit)}" aria-label="Unit cost"></td>
        <td class="num"></td>
        <td>
          <select id="edit-kind" aria-label="Kind">
            ${["meat", "grocery", "produce"].map((key) => `<option value="${key}"${draft.category === key ? " selected" : ""}>${key[0].toUpperCase()}${key.slice(1)}</option>`).join("")}
          </select>
          <button type="button" class="primary" id="save-product">Save</button>
          <button type="button" class="quiet" id="cancel-product">Cancel</button>
        </td>
      </tr>`;
    }
    return `<tr>
      <td>${esc(item.name)}<div class="item-sub">${esc(item.category)}</div></td>
      <td class="num">${qtyText(item.on_hand)}${item.short ? " short" : ""}</td>
      <td class="num">${item.last_cost_cents ? money(item.last_cost_cents) : "—"}</td>
      <td class="num">${money(item.value_cents)}</td>
      <td>
        <button type="button" data-edit-product="${item.id}">Edit</button>
        <button type="button" class="quiet" data-remove-product="${item.id}">Remove</button>
      </td>
    </tr>`;
  }).join("");
  return `
    <section class="card" style="margin-top:14px">
      <div class="section-title">
        <h2>Inventory</h2>
        <button type="button" id="toggle-item">${state.itemOpen ? "Close" : "Add an item"}</button>
      </div>
      <p class="muted">Add it here, or scan an invoice above. Either way the cost lands on the day you choose, and the quantity goes on hand.</p>
      ${state.itemOpen ? itemForm() : ""}
      ${rows ? `<table><thead><tr><th>Item</th><th class="num">On hand</th><th class="num">Unit cost</th><th class="num">Value</th><th></th></tr></thead><tbody>${rows}</tbody></table>` : `<p class="muted">Nothing on hand yet.</p>`}
    </section>`;
}

function itemForm() {
  return `
    <div class="fields">
      <div><label for="item-name">Item</label><input id="item-name"></div>
      <div><label for="item-qty">Quantity</label><input id="item-qty" inputmode="decimal"></div>
      <div><label for="item-unit">Unit cost</label><input id="item-unit" inputmode="decimal"></div>
      <div><label for="item-kind">Kind</label>
        <select id="item-kind"><option value="meat">Meat</option><option value="grocery" selected>Grocery</option><option value="produce">Produce</option></select>
      </div>
      <div><label for="item-date">Add the cost to</label><input id="item-date" type="date" value="${esc(state.dash.selected)}"></div>
    </div>
    <div class="actions"><button type="button" class="primary" id="save-item">Add to inventory and to that day</button></div>`;
}

function readLinesFromDom() {
  state.lines = [...document.querySelectorAll("tr[data-line]")].map((row) => ({
    name: row.querySelector("[data-field=name]").value,
    quantity: row.querySelector("[data-field=quantity]").value,
    unit: row.querySelector("[data-field=unit]").value,
    category: row.querySelector("[data-field=category]").value,
  }));
  const supplier = document.querySelector("#supplier");
  const invoiceDate = document.querySelector("#invoice-date");
  if (supplier) state.supplier = supplier.value;
  if (invoiceDate) state.invoiceDate = invoiceDate.value;
}

function wire() {
  document.querySelectorAll("[data-tab]").forEach((button) => {
    button.onclick = () => {
      state.tab = button.dataset.tab;
      state.suggestion = "";
      load(state.selected);
    };
  });
  const anchor = (state.flow && state.tab === "money") ? state.flow.selected : state.dash.selected;
  document.querySelector("#prev-month").onclick = () => load(shiftMonth(anchor, -1));
  document.querySelector("#next-month").onclick = () => load(shiftMonth(anchor, 1));
  document.querySelector("#jump-date").onchange = (event) => load(event.target.value);
  document.querySelectorAll(".day").forEach((button) => {
    button.onclick = () => {
      state.invoiceDate = button.dataset.date;
      state.suggestion = "";
      load(button.dataset.date);
    };
  });
  const saveSalesButton = document.querySelector("#save-sales");
  if (saveSalesButton) saveSalesButton.onclick = saveSales;
  const salesFile = document.querySelector("#sales-file");
  if (salesFile) salesFile.onchange = (event) => {
    const file = event.target.files[0];
    event.target.value = "";
    if (file) readPicture(file, "totals");
  };
  const toggleInvoice = document.querySelector("#toggle-invoice");
  if (toggleInvoice) toggleInvoice.onclick = () => {
    if (state.invoiceOpen) {
      state.invoiceOpen = false;
      state.editingInvoice = null;
      state.lines = [];
      state.supplier = "";
    } else {
      if (state.lines.length === 0) state.lines = [blankLine()];
      state.invoiceDate = state.invoiceDate || state.dash.selected;
      state.invoiceOpen = true;
    }
    state.pasteTarget = state.invoiceOpen ? "invoice" : "totals";
    state.suggestion = "";
    render();
  };
  document.querySelectorAll("[data-pay-invoice]").forEach((button) => {
    button.onclick = () => {
      state.payDraft = {
        id: Number(button.dataset.payInvoice),
        amount: centsToInput(button.dataset.payAmount),
        date: (state.dash && state.dash.today) || state.selected,
      };
      state.invoiceOpen = false;
      render();
    };
  });
  const savePayment = document.querySelector("#save-payment");
  if (savePayment) savePayment.onclick = saveInvoicePayment;
  const cancelPayment = document.querySelector("#cancel-payment");
  if (cancelPayment) cancelPayment.onclick = () => { state.payDraft = null; render(); };
  document.querySelectorAll("[data-edit-invoice]").forEach((button) => {
    button.onclick = () => {
      const invoice = selectedDay().invoices.find((item) => String(item.id) === button.dataset.editInvoice);
      if (!invoice) return;
      state.payDraft = null;
      state.editingInvoice = invoice.id;
      state.supplier = invoice.supplier;
      state.invoiceDate = invoice.invoice_date;
      state.lines = invoice.lines.map((line) => ({
        name: line.name,
        quantity: qtyText(line.quantity),
        unit: centsToInput(line.unit_cost_cents),
        category: line.category,
      }));
      if (!state.lines.length) state.lines = [blankLine()];
      state.invoiceOpen = true;
      state.pasteTarget = "invoice";
      state.suggestion = "Change any line, or add one that was missed, then save.";
      render();
    };
  });
  document.querySelectorAll("[data-remove-invoice]").forEach((button) => {
    button.onclick = () => removeInvoice(button.dataset.removeInvoice);
  });
  if (state.invoiceOpen) {
    document.querySelector("#add-line").onclick = () => {
      readLinesFromDom();
      state.lines.push(blankLine());
      render();
    };
    document.querySelectorAll("[data-remove-line]").forEach((button) => {
      button.onclick = () => {
        readLinesFromDom();
        state.lines.splice(Number(button.dataset.removeLine), 1);
        if (state.lines.length === 0) state.lines = [blankLine()];
        render();
      };
    });
    document.querySelector("#invoice-file").onchange = (event) => {
      const file = event.target.files[0];
      event.target.value = "";
      if (file) readPicture(file, "invoice");
    };
    document.querySelector("#save-invoice").onclick = saveInvoice;
  }
  const toggleItem = document.querySelector("#toggle-item");
  if (toggleItem) toggleItem.onclick = () => {
    state.itemOpen = !state.itemOpen;
    render();
  };
  const saveItem = document.querySelector("#save-item");
  if (saveItem) saveItem.onclick = saveManualItem;
  document.querySelectorAll("[data-edit-product]").forEach((button) => {
    button.onclick = () => {
      const item = state.dash.stock.find((row) => String(row.id) === button.dataset.editProduct);
      if (!item) return;
      state.productDraft = {
        id: item.id,
        name: item.name,
        on_hand: qtyText(item.on_hand),
        unit: item.last_cost_cents ? centsToInput(item.last_cost_cents) : "",
        category: item.category,
      };
      render();
    };
  });
  document.querySelectorAll("[data-remove-product]").forEach((button) => {
    button.onclick = () => removeProduct(button.dataset.removeProduct);
  });
  const saveProduct = document.querySelector("#save-product");
  if (saveProduct) saveProduct.onclick = saveProductEdit;
  const cancelProduct = document.querySelector("#cancel-product");
  if (cancelProduct) cancelProduct.onclick = () => { state.productDraft = null; render(); };
  main.querySelectorAll("[data-paste]").forEach((zone) => {
    zone.addEventListener("focusin", (event) => {
      if (event.target.closest("[data-paste]") === zone) state.pasteTarget = zone.dataset.paste;
    });
  });
}

async function saveSales() {
  const meat = dollarsToCents(document.querySelector("#meat").value);
  const grocery = dollarsToCents(document.querySelector("#grocery").value);
  if (meat === null || grocery === null) { showToast("Enter dollar amounts."); return; }
  try {
    await api("/api/sales", {
      method: "POST",
      body: {
        sale_date: state.dash.selected,
        meat_cents: meat,
        grocery_cents: grocery,
        not_in_balance_cents: 0,
        waiting_cents: 0,
        photo_token: state.photoTotals,
      },
    });
    state.photoTotals = null;
    state.saleDraft = null;
    state.suggestion = "";
    showToast("Sales saved for this day.");
    await load(state.dash.selected);
  } catch (error) { showToast(error.message); }
}

async function saveInvoice() {
  readLinesFromDom();
  const lines = [];
  for (const line of state.lines) {
    if (!line.name.trim()) continue;
    const unit = dollarsToCents(line.unit);
    if (unit === null || !line.quantity) { showToast("Each line needs a quantity and a unit cost."); return; }
    const quantity = Number(line.quantity);
    lines.push({
      name: line.name.trim(),
      quantity,
      unit_cost_cents: unit,
      line_total_cents: Math.round(quantity * unit),
      category: line.category,
    });
  }
  if (!state.supplier.trim()) { showToast("Enter who the invoice is from."); return; }
  if (!lines.length) { showToast("Add at least one item."); return; }
  const invoiceDate = state.invoiceDate || state.dash.selected;
  const body = {
    supplier: state.supplier.trim(),
    invoice_date: invoiceDate,
    category: lines[0].category,
    lines,
    photo_token: state.photoInvoice,
  };
  try {
    if (state.editingInvoice) {
      await api(`/api/invoices/${state.editingInvoice}`, { method: "PUT", body });
    } else {
      await api("/api/invoices", { method: "POST", body });
    }
    const updated = Boolean(state.editingInvoice);
    state.supplier = "";
    state.lines = [];
    state.editingInvoice = null;
    state.invoiceOpen = false;
    state.photoInvoice = null;
    state.suggestion = "";
    showToast(updated ? "Invoice updated." : "Invoice added to that day.");
    await load(invoiceDate);
  } catch (error) { showToast(error.message); }
}

async function saveManualItem() {
  const name = document.querySelector("#item-name").value.trim();
  const quantity = Number(document.querySelector("#item-qty").value);
  const unit = dollarsToCents(document.querySelector("#item-unit").value);
  const category = document.querySelector("#item-kind").value;
  const invoiceDate = document.querySelector("#item-date").value || state.dash.selected;
  if (!name || !quantity || unit === null) { showToast("Enter the item, quantity, and unit cost."); return; }
  try {
    await api("/api/invoices", {
      method: "POST",
      body: {
        supplier: "By hand",
        invoice_date: invoiceDate,
        category,
        lines: [{
          name,
          quantity,
          unit_cost_cents: unit,
          line_total_cents: Math.round(quantity * unit),
          category,
        }],
      },
    });
    state.itemOpen = false;
    showToast("Item added. Its cost is on that day.");
    await load(invoiceDate);
  } catch (error) { showToast(error.message); }
}

async function saveProductEdit() {
  const name = document.querySelector("#edit-name").value.trim();
  const onHand = document.querySelector("#edit-qty").value.trim();
  const unitText = document.querySelector("#edit-unit").value.trim();
  const category = document.querySelector("#edit-kind").value;
  if (!name) { showToast("Enter the item name."); return; }
  if (onHand === "" || Number.isNaN(Number(onHand)) || Number(onHand) < 0) {
    showToast("On hand needs to be a number.");
    return;
  }
  const body = { name, on_hand: onHand, category };
  if (unitText) {
    const unit = dollarsToCents(unitText);
    if (unit === null) { showToast("Unit cost needs to be a number."); return; }
    body.unit_cost_cents = unit;
  }
  try {
    await api(`/api/products/${state.productDraft.id}`, { method: "PUT", body });
    state.productDraft = null;
    showToast("Item updated.");
    await load(state.dash.selected);
  } catch (error) { showToast(error.message); }
}

async function removeProduct(id) {
  const item = state.dash.stock.find((row) => String(row.id) === String(id));
  const label = item ? item.name : "this item";
  if (!window.confirm(`Remove ${label} from inventory? The invoice it came from stays on that day.`)) return;
  try {
    await api(`/api/products/${id}`, { method: "DELETE" });
    if (state.productDraft && String(state.productDraft.id) === String(id)) state.productDraft = null;
    showToast("Item removed from inventory.");
    await load(state.dash.selected);
  } catch (error) { showToast(error.message); }
}

async function saveInvoicePayment() {
  const draft = state.payDraft;
  if (!draft) return;
  const amount = dollarsToCents(document.querySelector("#pay-amount").value);
  const paidOn = document.querySelector("#pay-date").value;
  if (!amount) { showToast("Enter how much was paid."); return; }
  try {
    await api(`/api/invoices/${draft.id}/payments`, {
      method: "POST",
      body: { amount_cents: amount, paid_on: paidOn },
    });
    state.payDraft = null;
    showToast("Payment recorded. The cost stays on the invoice date.");
    await load(state.dash.selected);
  } catch (error) { showToast(error.message); }
}

async function removeInvoice(id) {
  try {
    await api(`/api/invoices/${id}`, { method: "DELETE" });
    showToast("Invoice removed.");
    await load(state.dash.selected);
  } catch (error) { showToast(error.message); }
}

async function readPicture(file, target) {
  state.pasteTarget = target;
  try {
    showToast(target === "invoice"
      ? "Reading the invoice on this PC. Nothing is saved yet."
      : "Reading the picture. Nothing is saved yet.");
    const body = await filePayload(file);
    const path = target === "totals" ? "/api/ocr/totals" : "/api/ocr/invoice";
    const result = await api(path, { method: "POST", body });
    if (!result.ok) {
      state.suggestion = result.message || "Could not read that picture.";
      render();
      return;
    }
    if (target === "totals") {
      state.photoTotals = result.photo_token || null;
      const current = saleValues(selectedDay());
      state.saleDraft = {
        date: state.dash.selected,
        meat: result.meat_cents != null ? centsToInput(result.meat_cents) : current.meat,
        grocery: result.grocery_cents != null ? centsToInput(result.grocery_cents) : current.grocery,
      };
      if (result.meat_cents == null && result.grocery_cents == null) {
        state.suggestion = "No meat total or grocery total was in that picture. Type them, or use Scan an invoice for a vendor bill.";
      } else {
        state.suggestion = "Those amounts are filled in. Check them, then tap Save. Nothing is stored until you do.";
      }
    } else {
      state.photoInvoice = result.photo_token || null;
      if (result.supplier) state.supplier = result.supplier;
      if (result.invoice_date) state.invoiceDate = result.invoice_date;
      if (result.lines && result.lines.length) {
        state.lines = result.lines.map((line) => ({
          name: line.product_name || line.name || "",
          quantity: qtyText(line.quantity),
          unit: line.unit_cost_cents ? centsToInput(line.unit_cost_cents) : "",
          category: line.category || "grocery",
        }));
        state.suggestion = result.reader === "pp-ocrv6-medium"
          ? "Read with the local invoice model. Check the supplier, date, and lines, then tap Save."
          : "Check the supplier, date, and lines, then tap Save. Nothing is stored until you do.";
      } else {
        state.suggestion = "No item rows were read. Type the lines. The picture is only a suggestion.";
      }
      state.invoiceOpen = true;
      state.pasteTarget = "invoice";
    }
    render();
  } catch (error) { showToast(error.message); }
}

window.addEventListener("paste", (event) => {
  const item = [...event.clipboardData.items].find((entry) => entry.type.startsWith("image/"));
  if (!item) return;
  event.preventDefault();
  readPicture(item.getAsFile(), state.pasteTarget || "totals");
});

load().catch((error) => {
  main.innerHTML = `<p>${esc(error.message)}</p>`;
});

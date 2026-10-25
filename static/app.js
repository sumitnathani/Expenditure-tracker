/* Frontend for the local expense tracker. Vanilla JS, no build step.
 *
 * The backend owns every calculation: this file only renders what it is
 * handed and re-renders the whole month after each add/edit/delete, so the
 * row positions it sends back are never stale.
 */

const state = {
  month: null,
  label: '',
  file: '',
  rows: [],
  summary: null,
  genres: [],
  months: [],
  today: document.body.dataset.today,
};

let editingIndex = null;
let bannerTimer = null;

/* ------------------------------------------------------------- utilities */

const h = (tag, props = {}, ...children) => {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(props || {})) {
    if (value === null || value === undefined || value === false) continue;
    if (key === 'class') node.className = value;
    else if (key === 'text') node.textContent = value;
    else if (key.startsWith('on')) node.addEventListener(key.slice(2), value);
    else node.setAttribute(key, value);
  }
  for (const child of children.flat(Infinity)) {
    if (child === null || child === undefined || child === false) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
};

const rupees = new Intl.NumberFormat('en-IN', { maximumFractionDigits: 2 });

const money = (n) => `₹${rupees.format(Math.abs(Number(n) || 0))}`;
const signedMoney = (n) => {
  const value = Number(n) || 0;
  if (value === 0) return money(0);
  return `${value > 0 ? '+' : '−'}${money(value)}`;
};

const prettyDate = (iso) => {
  const [y, m, d] = iso.split('-').map(Number);
  const date = new Date(y, m - 1, d);
  return date.toLocaleDateString('en-GB', { weekday: 'short', day: 'numeric', month: 'short' });
};

const monthAfter = (key, delta) => {
  const [y, m] = key.split('-').map(Number);
  const date = new Date(y, m - 1 + delta, 1);
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}`;
};

const $ = (id) => document.getElementById(id);

function banner(message, kind = 'error') {
  const node = $('banner');
  node.textContent = message;
  node.classList.toggle('ok', kind === 'ok');
  node.hidden = false;
  clearTimeout(bannerTimer);
  bannerTimer = setTimeout(() => { node.hidden = true; }, kind === 'ok' ? 2500 : 6000);
}

async function api(method, url, body) {
  const response = await fetch(url, {
    method,
    headers: body ? { 'Content-Type': 'application/json' } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(payload.error || `Request failed (${response.status})`);
  return payload;
}

/* --------------------------------------------------------------- loading */

async function load(month = state.month) {
  try {
    const url = month ? `/api/months/${month}` : '/api/state';
    const payload = await api('GET', url);
    if (!month) {
      state.months = payload.months || [];
      state.today = payload.today || state.today;
    }
    apply(payload);
  } catch (error) {
    banner(error.message);
  }
}

function apply(payload) {
  state.month = payload.month;
  state.label = payload.label || '';
  state.file = payload.file || '';
  state.rows = payload.rows || [];
  state.summary = payload.summary;
  state.genres = payload.genres || [];
  editingIndex = null;

  // A new month file may have just been created by an entry logged elsewhere.
  if (!state.months.some((entry) => entry.month === state.month)) {
    state.months = [...state.months, { month: state.month, label: state.label }]
      .sort((a, b) => b.month.localeCompare(a.month));
  }
  render();
}

/* -------------------------------------------------------------- rendering */

function render() {
  const picker = $('month-picker');
  if (picker.value !== state.month) picker.value = state.month;
  $('file-tag').textContent = state.file || '';

  const dateInput = $('add-date');
  if (!dateInput.value || !dateInput.value.startsWith(state.month)) {
    dateInput.value = state.month === state.today.slice(0, 7) ? state.today : `${state.month}-01`;
  }

  renderChips();
  renderGenres();
  renderStats();
  renderTable();
  renderBreakdown();
}

function renderChips() {
  const container = $('month-chips');
  container.replaceChildren(...state.months.map((entry) =>
    h('button', {
      class: 'chip',
      type: 'button',
      text: entry.label,
      'aria-current': String(entry.month === state.month),
      onclick: () => load(entry.month),
    })
  ));
}

function renderGenres() {
  $('genre-list').replaceChildren(...state.genres.map((g) => h('option', { value: g })));
}

function renderStats() {
  const s = state.summary;
  const over = s.deviation < 0;
  const devClass = s.entries === 0 ? '' : (over ? 'bad' : 'good');
  const verdict = s.entries === 0 ? 'nothing logged yet'
    : over ? `${money(s.deviation)} over where you should be`
    : s.deviation === 0 ? 'exactly on budget'
    : `${money(s.deviation)} in hand`;

  // The file stops at the last logged entry, so days since then are shown
  // separately rather than being written in as rows.
  const projection = s.projection
    ? h('div', {
        class: 'sub projection',
        text: `Counting through ${prettyDate(s.projection.date)} too: ${signedMoney(s.projection.deviation)}`,
      })
    : null;

  const cards = [
    h('div', { class: 'stat' },
      h('div', { class: 'label', text: 'Allowance accrued' }),
      h('div', { class: 'value', text: money(s.allowance) }),
      h('div', { class: 'sub', text: `${money(s.per_day)}/day × ${s.days_tracked} ${s.days_tracked === 1 ? 'day' : 'days'}` })),
    h('div', { class: 'stat' },
      h('div', { class: 'label', text: 'Spent' }),
      h('div', { class: 'value', text: money(s.spent) }),
      h('div', { class: 'sub', text: s.entries === 0 ? 'no entries' : `${s.entries} ${s.entries === 1 ? 'entry' : 'entries'} · avg ${money(s.avg_per_day)}/day` })),
    h('div', { class: `stat ${devClass}` },
      h('div', { class: 'label', text: 'Deviation' }),
      h('div', { class: 'value', text: s.entries === 0 ? '—' : signedMoney(s.deviation) }),
      h('div', { class: 'sub', text: verdict }),
      projection),
    h('div', { class: 'stat' },
      h('div', { class: 'label', text: 'Tracked' }),
      h('div', { class: 'value', text: s.first_date ? `${s.days_tracked}` : '—' }),
      h('div', { class: 'sub', text: s.first_date ? `${prettyDate(s.first_date)} → ${prettyDate(s.last_date)}` : 'no days logged' })),
  ];
  $('stats').replaceChildren(...cards);
}

function renderTable() {
  const body = $('entries-body');
  const rows = state.rows;

  $('empty-state').hidden = rows.length > 0;
  $('row-count').textContent = rows.length
    ? `${rows.length} ${rows.length === 1 ? 'row' : 'rows'} in ${state.file}`
    : '';

  body.replaceChildren(...rows.map((row) =>
    row.index === editingIndex ? editRow(row) : displayRow(row)
  ));
}

function displayRow(row) {
  const devClass = row.deviation < 0 ? 'bad' : 'good';
  const rest = row.placeholder;

  return h('tr', { class: rest ? 'rest-day' : '' },
    h('td', { class: 'num', text: String(row.index + 1) }),
    h('td', { class: 'date', text: prettyDate(row.date) }),
    h('td', {}, rest ? h('span', { class: 'rest-tag', text: 'rest day' }) : row.item),
    h('td', { text: rest ? '—' : (row.genre || '—') }),
    h('td', { class: 'num', text: rest ? '—' : money(row.amount) }),
    h('td', { class: `num deviation ${devClass}`, text: signedMoney(row.deviation) }),
    h('td', { class: 'num total', text: money(row.total) }),
    h('td', { class: 'actions-col' },
      h('div', { class: 'row-actions' },
        h('button', { class: 'ghost', type: 'button', text: 'Edit', onclick: () => startEdit(row.index) }),
        h('button', { class: 'ghost danger', type: 'button', text: 'Delete', onclick: () => removeRow(row.index) }))));
}

function editRow(row) {
  const date = h('input', { type: 'date', value: row.date });
  const item = h('input', { type: 'text', value: row.item, placeholder: 'Item' });
  const genre = h('input', { type: 'text', value: row.genre, list: 'genre-list', placeholder: 'Genre' });
  const amount = h('input', { type: 'number', value: row.amount, min: '0', step: '0.01' });

  const save = () => commitEdit(row.index, date, item, genre, amount);

  for (const input of [date, item, genre, amount]) {
    input.addEventListener('keydown', (event) => {
      if (event.key === 'Enter') { event.preventDefault(); save(); }
      if (event.key === 'Escape') { editingIndex = null; renderTable(); }
    });
  }

  return h('tr', { class: 'editing' },
    h('td', { class: 'num', text: String(row.index + 1) }),
    h('td', {}, date),
    h('td', {}, item),
    h('td', {}, genre),
    h('td', { class: 'num' }, amount),
    h('td', { class: 'num', text: '—' }),
    h('td', { class: 'num', text: '—' }),
    h('td', { class: 'actions-col' },
      h('div', { class: 'row-actions' },
        h('button', { class: 'primary', type: 'button', text: 'Save', onclick: save }),
        h('button', {
          class: 'ghost', type: 'button', text: 'Cancel',
          onclick: () => { editingIndex = null; renderTable(); },
        }))));
}

function renderBreakdown() {
  const items = state.summary.by_genre || [];
  $('breakdown-card').hidden = items.length === 0;
  if (!items.length) return;

  const peak = Math.max(...items.map((i) => i.amount)) || 1;
  $('breakdown').replaceChildren(...items.map((item) =>
    h('div', { class: 'bar-row' },
      h('div', { class: 'bar-name', text: item.genre }),
      h('div', { class: 'bar-track' },
        h('div', { class: 'bar-fill', style: `width:${Math.max(2, (item.amount / peak) * 100)}%` })),
      h('div', { class: 'bar-amount', text: money(item.amount) }))
  ));
}

/* -------------------------------------------------------------- mutations */

function startEdit(index) {
  editingIndex = index;
  renderTable();
  const first = $('entries-body').querySelector('tr.editing input[type="text"]');
  if (first) { first.focus(); first.select(); }
}

async function commitEdit(index, date, item, genre, amount) {
  try {
    apply(await api('PUT', `/api/rows/${index}`, {
      month: state.month,
      date: date.value,
      item: item.value,
      genre: genre.value,
      amount: amount.value,
    }));
    banner('Row updated. Deviation recalculated for the whole month.', 'ok');
  } catch (error) {
    banner(error.message);
  }
}

async function removeRow(index) {
  const row = state.rows[index];
  const what = row.placeholder ? `the rest day ${prettyDate(row.date)}` : `"${row.item}" on ${prettyDate(row.date)}`;
  if (!confirm(`Delete ${what}? The whole month will be recalculated.`)) return;
  try {
    apply(await api('DELETE', `/api/rows/${index}?month=${state.month}`));
    banner('Row deleted. Deviation recalculated for the whole month.', 'ok');
  } catch (error) {
    banner(error.message);
  }
}

async function submitAdd(event) {
  event.preventDefault();
  const date = $('add-date');
  const item = $('add-item');
  const genre = $('add-genre');
  const amount = $('add-amount');
  const button = event.target.querySelector('button[type="submit"]');

  button.disabled = true;
  try {
    const payload = await api('POST', '/api/rows', {
      date: date.value, item: item.value, genre: genre.value, amount: amount.value,
    });
    const moved = payload.month !== state.month;
    apply(payload);
    banner(moved
      ? `Logged in ${payload.label} — switched you there.`
      : `Added. ${payload.rows.length} rows recalculated.`, 'ok');
    item.value = '';
    amount.value = '';
    item.focus();
  } catch (error) {
    banner(error.message);
  } finally {
    button.disabled = false;
  }
}

/* ------------------------------------------------------------------- wiring */

$('add-form').addEventListener('submit', submitAdd);
$('month-picker').addEventListener('change', (event) => {
  if (event.target.value) load(event.target.value);
});
$('prev-month').addEventListener('click', () => load(monthAfter(state.month, -1)));
$('next-month').addEventListener('click', () => load(monthAfter(state.month, 1)));

document.addEventListener('keydown', (event) => {
  if (event.key === 'Escape' && editingIndex !== null) {
    editingIndex = null;
    renderTable();
  }
});

load();

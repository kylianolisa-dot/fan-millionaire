// The browser's replaceChildren() would print the text "null" for null/false and "[object ...]" for arrays.
// Make it skip empty values and flatten arrays so views can pass conditional children freely.
(() => {
  const native = Element.prototype.replaceChildren;
  Element.prototype.replaceChildren = function (...kids) {
    native.apply(this, kids.flat(Infinity).filter((k) => k != null && k !== false));
  };
})();

// Shared helpers. Everything is rendered with textContent (via h) so user text can never inject HTML.
function h(tag, attrs, ...kids) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === false || v == null) continue;
    if (k === 'class') e.className = v;
    else if (k.startsWith('on')) e.addEventListener(k.slice(2), v);
    else if (k === 'value' || k === 'checked' || k === 'disabled' || k === 'selected') e[k] = v;
    else e.setAttribute(k, v);
  }
  for (const kid of kids.flat(Infinity)) {
    if (kid == null || kid === false) continue;
    e.append(kid.nodeType ? kid : document.createTextNode(String(kid)));
  }
  return e;
}

async function api(path, method = 'GET', data) {
  const opt = { method, headers: {}, credentials: 'same-origin' };
  if (method !== 'GET') { opt.headers['Content-Type'] = 'application/json'; opt.body = JSON.stringify(data || {}); }
  const r = await fetch(path, opt);
  let j = {};
  try { j = await r.json(); } catch (_) {}
  if (!r.ok) { const e = new Error(j.error || 'Request failed'); e.status = r.status; throw e; }
  return j;
}

const naira = (k) => '₦' + (k / 100).toLocaleString('en-NG', { minimumFractionDigits: k % 100 ? 2 : 0, maximumFractionDigits: 2 });
const fmtDate = (s) => s ? new Date(s * 1000).toLocaleString('en-NG', { weekday: 'short', day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' }) : '';
const paragraphs = (t) => String(t || '').split('\n').filter(Boolean).map((l) => h('p', {}, l));

function toast(msg, type = 'ok') {
  const t = h('div', { class: 'toast ' + type }, msg);
  document.body.append(t);
  setTimeout(() => t.remove(), 3800);
}

function deviceId() {
  try {
    let d = localStorage.getItem('fm_device');
    if (!d) { d = crypto.randomUUID(); localStorage.setItem('fm_device', d); }
    return d;
  } catch (_) { return null; }
}

function table(headers, rows) {
  return h('div', { class: 'tablewrap' }, h('table', {},
    h('thead', {}, h('tr', {}, headers.map((x) => h('th', {}, x)))),
    h('tbody', {}, rows.length ? rows.map((r) => h('tr', {}, r.map((c) => h('td', {}, c)))) :
      h('tr', {}, h('td', { colspan: headers.length, class: 'muted' }, 'Nothing here yet')))));
}

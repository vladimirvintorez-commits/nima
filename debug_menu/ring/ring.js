// ring.js — рендерер кольцевого дебаг-меню: рисует ТОЛЬКО два кольца
// (внешнее — категории, внутреннее по клику — пункты), весь остальной
// холст прозрачен и пропускает клики мимо окна. Геометрия и цвета —
// порт радиального вида из debug_menu/ui.py.
'use strict';

const BASE = `http://127.0.0.1:${new URLSearchParams(location.search).get('port') || '8791'}`;

const COLORS = {
  segIdle: '#2a2f34', segHover: '#3a4149', seg2Idle: '#33383d',
  bg: '#0a0a0a', fg: '#33ff33', amber: '#ffb000', dim: '#1a4a1a',
};
const RAD_HOLE = 0.13, R1_OUT = 0.30, R2_IN = 0.315, R2_OUT = 0.42;

let menu = null;           // {version, groups:[{name, items:[{label, action, icon}]}]}
let openCat = null;        // индекс раскрытой категории
let hover = null;          // {kind:'cat'|'item', idx}
let segments = [];         // пересчитывается в draw()

const c = document.getElementById('c');
const ctx = c.getContext('2d');
let W = 0, H = 0;

function resize() {
  W = c.width = window.innerWidth;
  H = c.height = window.innerHeight;
  draw();
}
window.addEventListener('resize', resize);

function geometry() {
  const cx = W / 2, cy = H / 2 + 20;
  const s = Math.min(W, H);
  return { cx, cy, hole: s * RAD_HOLE, r1: s * R1_OUT, r2i: s * R2_IN, r2o: s * R2_OUT };
}

function segPath(cx, cy, rIn, rOut, a0, a1) {
  ctx.beginPath();
  ctx.arc(cx, cy, rOut, a0, a1);
  ctx.arc(cx, cy, rIn, a1, a0, true);
  ctx.closePath();
}

function polar(cx, cy, r, ang) {
  return [cx + r * Math.cos(ang), cy + r * Math.sin(ang)];
}

function shortCaption(label) {
  let cap = label.replace(/^\[|\]$/g, '').trim();
  cap = cap.split('·')[0].trim() || cap;
  const toks = cap.split(/\s+/);
  if (toks.length && toks[0].length === 1 && !/[\wа-яё]/i.test(toks[0])) {
    cap = toks.slice(1).join(' ') || cap;
  }
  return cap.length > 16 ? cap.slice(0, 15) + '…' : cap;
}

function draw() {
  if (!menu) return;
  ctx.clearRect(0, 0, W, H);
  const { cx, cy, hole, r1, r2i, r2o } = geometry();
  const groups = menu.groups;
  segments = [];
  const seg = (2 * Math.PI) / groups.length;

  // ── Кольцо-2: пункты раскрытой категории (рисуем первым) ──
  if (openCat !== null && openCat < groups.length) {
    const g = groups[openCat];
    const m = g.items.length;
    if (m > 0) {
      const cStart = -Math.PI / 2 + openCat * seg;
      const cExt = seg;
      const mid = cStart + cExt / 2;
      const ext = Math.max(cExt, Math.min(Math.PI, cExt * 3));
      const start = mid - ext / 2;
      const sub = ext / m;
      for (let j = 0; j < m; j++) {
        const a0 = start + j * sub, a1 = a0 + sub;
        const flat = g.flat + j;
        const isHover = hover && hover.kind === 'item' && hover.idx === flat;
        segPath(cx, cy, r2i, r2o, a0, a1);
        ctx.fillStyle = isHover ? COLORS.segHover : COLORS.seg2Idle;
        ctx.fill();
        ctx.lineWidth = isHover ? 2 : 1;
        ctx.strokeStyle = isHover ? COLORS.amber : COLORS.bg;
        ctx.stroke();
        segments.push({ kind: 'item', idx: flat, a0, a1, rIn: r2i, rOut: r2o });
        // иконка + подпись
        const rMid = (r2i + r2o) / 2;
        const [ix, iy] = polar(cx, cy, rMid, (a0 + a1) / 2);
        ctx.textAlign = 'center';
        ctx.fillStyle = COLORS.amber;
        ctx.font = 'bold 15px "Courier New"';
        ctx.fillText(g.items[j].icon, ix, iy - 3);
        ctx.fillStyle = COLORS.fg;
        ctx.font = 'bold 8px "Courier New"';
        ctx.fillText(shortCaption(g.items[j].label), ix, iy + 12);
      }
    }
  }

  // ── Кольцо-1: категории ──
  for (let i = 0; i < groups.length; i++) {
    const a0 = -Math.PI / 2 + i * seg, a1 = a0 + seg;
    const isOpen = i === openCat;
    const isHover = hover && hover.kind === 'cat' && hover.idx === i;
    segPath(cx, cy, hole, r1, a0, a1);
    ctx.fillStyle = (isHover || isOpen) ? COLORS.segHover : COLORS.segIdle;
    ctx.fill();
    ctx.lineWidth = isOpen ? 2 : 1;
    ctx.strokeStyle = isOpen ? COLORS.amber : COLORS.bg;
    ctx.stroke();
    segments.push({ kind: 'cat', idx: i, a0, a1, rIn: hole, rOut: r1 });
    const rMid = (hole + r1) / 2;
    const [tx, ty] = polar(cx, cy, rMid, (a0 + a1) / 2);
    ctx.textAlign = 'center';
    ctx.fillStyle = isOpen ? COLORS.amber : COLORS.fg;
    ctx.font = 'bold 8px "Courier New"';
    const lines = wrapWords(groups[i].name, 10);
    const y0 = ty - (lines.length - 1) * 5;
    lines.forEach((l, k) => ctx.fillText(l, tx, y0 + k * 10));
  }

  // ── Центр: тонкая граница дырки + имя открытой категории ──
  ctx.beginPath();
  ctx.arc(cx, cy, hole, 0, 2 * Math.PI);
  ctx.strokeStyle = COLORS.dim;
  ctx.lineWidth = 1;
  ctx.stroke();
  if (openCat !== null && openCat < groups.length) {
    ctx.fillStyle = COLORS.amber;
    ctx.font = 'bold 9px "Courier New"';
    ctx.textAlign = 'center';
    const lines = wrapWords(groups[openCat].name, 12);
    lines.forEach((l, k) => ctx.fillText(l, cx, cy - (lines.length - 1) * 6 + k * 12));
  }
}

function wrapWords(text, maxLen) {
  const words = text.split(' ');
  const lines = [];
  let line = '';
  for (const w of words) {
    if (line && (line + ' ' + w).length > maxLen) { lines.push(line); line = w; }
    else line = line ? line + ' ' + w : w;
  }
  if (line) lines.push(line);
  return lines;
}

// ── Хит-тест: угол+радиус ──
function pick(x, y) {
  const { cx, cy, hole } = geometry();
  const dx = x - cx, dy = y - cy;
  const dist = Math.hypot(dx, dy);
  if (dist < hole) return { kind: 'hole' };
  const ang = Math.atan2(dy, dx);          // canvas-угол, совпадает с углами сегментов
  const norm = (a) => ((a % (2 * Math.PI)) + 2 * Math.PI) % (2 * Math.PI);
  const am = norm(ang);
  for (const kind of ['item', 'cat']) {
    for (const s of segments) {
      if (s.kind !== kind) continue;
      if (dist < s.rIn || dist > s.rOut) continue;
      const a0 = norm(s.a0), a1 = norm(s.a1);
      if (a0 <= a1 ? (am >= a0 && am <= a1) : (am >= a0 || am <= a1)) return s;
    }
  }
  return null;
}

// ── Мышь: hover, хит-тест окна, drag, клик ──
let drag = null;
let lastHit = null;

function onMouseMove(e) {
  if (drag) {
    const dx = e.screenX - drag.sx, dy = e.screenY - drag.sy;
    if (Math.abs(dx) + Math.abs(dy) > 4) drag.moved = true;
    if (drag.moved) window.ring.moveBy(e.screenX - drag.lx, e.screenY - drag.ly);
    drag.lx = e.screenX; drag.ly = e.screenY;
    return;
  }
  const rect = c.getBoundingClientRect();
  const x = e.clientX - rect.left, y = e.clientY - rect.top;
  const g = geometry();
  const inside = Math.hypot(x - g.cx, y - g.cy) <= (openCat !== null ? g.r2o : g.r1);
  if (inside !== lastHit) { lastHit = inside; window.ring.setHit(inside); }
  const p = inside ? pick(x, y) : null;
  const prev = hover;
  hover = p && (p.kind === 'item' || p.kind === 'cat') ? { kind: p.kind, idx: p.idx } : null;
  c.style.cursor = hover ? 'pointer' : 'grab';
  if (JSON.stringify(prev) !== JSON.stringify(hover)) draw();
}

function onMouseDown(e) {
  drag = { sx: e.screenX, sy: e.screenY, lx: e.screenX, ly: e.screenY, moved: false };
}

function onMouseUp(e) {
  if (!drag) return;
  const moved = drag.moved;
  drag = null;
  if (moved) return;
  const rect = c.getBoundingClientRect();
  const p = pick(e.clientX - rect.left, e.clientY - rect.top);
  if (!p) return;
  if (p.kind === 'hole') { openCat = null; draw(); return; }
  if (p.kind === 'cat') { openCat = (openCat === p.idx) ? null : p.idx; draw(); return; }
  if (p.kind === 'item') activate(p.idx);
}

function activate(flatIdx) {
  for (const g of menu.groups) {
    if (flatIdx >= g.flat && flatIdx < g.flat + g.items.length) {
      const item = g.items[flatIdx - g.flat];
      // text/plain без кастомных заголовков — simple-request без CORS-preflight
      // (страница грузится с file://, preflight сервер бы не прошёл)
      fetch(`${BASE}/action`, {
        method: 'POST',
        headers: { 'Content-Type': 'text/plain' },
        body: JSON.stringify({ action: item.action }),
      }).catch((err) => console.error('action failed', err));
      return;
    }
  }
}

window.addEventListener('mousemove', onMouseMove);
window.addEventListener('mousedown', onMouseDown);
window.addEventListener('mouseup', onMouseUp);
window.addEventListener('contextmenu', (e) => e.preventDefault());

resize();
fetch(`${BASE}/menu`)
  .then((r) => r.json())
  .then((data) => {
    menu = data;
    resize();
  })
  .catch((err) => console.error('menu load failed', err));

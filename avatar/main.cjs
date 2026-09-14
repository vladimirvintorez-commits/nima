// main.cjs — Electron-процесс окна Нимфеи.
//
// Окно: прозрачное, без рамки, поверх всех. Управление — две точки:
//   • верхняя ЦЕНТРАЛЬНАЯ — перетаскивание окна; двойной щелчок — блокировка/
//     разблокировка изменений окна (перетаскивание, ресайз и перенос персонажа);
//   • верхняя ПРАВАЯ — изменение размера, СИММЕТРИЧНОЕ (окно растёт от центра).
//
// Состояние (mood/action/outfit/mouth) приходит из Python через файл
// avatar_state.json (мост avatar/bridge.py) — main читает его через fs.watch
// и рассылает в рендерер.
const { app, BrowserWindow, ipcMain, screen } = require('electron');
const fs = require('fs');
const path = require('path');

const STATE_PATH = process.env.NIMA_STATE_PATH || path.join(__dirname, 'avatar_state.json');
const VRM_DIR = process.env.NIMA_VRM_DIR || path.join(__dirname, '..', 'vita_avatar_app');
const ERR_LOG = path.join(__dirname, 'renderer_error.log');
const WIN_STATE = path.join(__dirname, 'window_state.json');
const ALWAYS_ON_TOP = process.env.NIMA_ALWAYS_ON_TOP !== '0';

const MIN_W = 240, MIN_H = 320, MAX_W = 1200, MAX_H = 1600;

let win = null;
let lastStateMtime = 0;
let stateReadTimer = null;

function logLine(msg) {
  try { fs.appendFileSync(ERR_LOG, `${new Date().toISOString()} ${msg}\n`, 'utf-8'); } catch (_) {}
}

// --- память окна: позиция/размер переживают перезапуск ---
function loadWinState() {
  try { return JSON.parse(fs.readFileSync(WIN_STATE, 'utf-8')); } catch (_) { return null; }
}

function saveWinState() {
  if (!win || win.isDestroyed()) return;
  try { fs.writeFileSync(WIN_STATE, JSON.stringify(win.getBounds()), 'utf-8'); } catch (_) {}
}

function restoreWinBounds() {
  const ws = loadWinState();
  if (!ws) return null;
  // сброшенное на второй план/за экран окно вернём в дефолт
  const b = screen.getPrimaryDisplay().workArea;
  const x = Number.isFinite(ws.x) ? Math.round(ws.x) : null;
  const y = Number.isFinite(ws.y) ? Math.round(ws.y) : null;
  const w = Number.isFinite(ws.width) ? Math.min(MAX_W, Math.max(MIN_W, ws.width)) : 420;
  const h = Number.isFinite(ws.height) ? Math.min(MAX_H, Math.max(MIN_H, ws.height)) : 640;
  const visible = x !== null && y !== null && x > -w + 80 && x < b.width - 80 && y > -20 && y < b.height - 80;
  return visible ? { x, y, width: w, height: h } : { width: w, height: h };
}

// Файлы моделей/анимаций читаются ЗДЕСЬ (fs) и уходят в рендерер ArrayBuffer'ом:
// fetch/file:// в Chromium ненадёжен, а так загрузка работает всегда.
// .vrm — из VRM_DIR, .vrma — из VRM_DIR/animations (в обоих случаях по имени).
ipcMain.handle('vrm:read', (_e, fileName) => {
  if (/^[\w\-. ]+\.vrm$/i.test(fileName || '')) {
    try {
      const buf = fs.readFileSync(path.join(VRM_DIR, fileName));
      return buf.buffer.slice(buf.byteOffset, buf.byteOffset + buf.byteLength);
    } catch (err) {
      logLine(`vrm:read ${fileName}: ${err}`);
      return null;
    }
  }
  if (/^[\w\-. ]+\.vrma$/i.test(fileName || '')) {
    try {
      const buf = fs.readFileSync(path.join(VRM_DIR, 'animations', fileName));
      return buf.buffer.slice(buf.byteOffset, buf.byteOffset + buf.byteLength);
    } catch (err) {
      logLine(`vrm:read ${fileName}: ${err}`);
      return null;
    }
  }
  return null;  // только простые имена файлов
});

function readState() {
  try {
    const stat = fs.statSync(STATE_PATH);
    if (stat.mtimeMs === lastStateMtime) return;
    lastStateMtime = stat.mtimeMs;
    const state = JSON.parse(fs.readFileSync(STATE_PATH, 'utf-8'));
    if (win && !win.isDestroyed()) win.webContents.send('state', state);
  } catch (_) { /* файла может ещё не быть — не страшно */ }
}

function createWindow() {
  const restored = restoreWinBounds();
  win = new BrowserWindow({
    width: restored?.width || 420,
    height: restored?.height || 640,
    x: restored?.x,
    y: restored?.y,
    transparent: true,
    frame: false,
    resizable: false,          // ресайз свой, симметричный (точка справа)
    hasShadow: false,
    alwaysOnTop: ALWAYS_ON_TOP,
    skipTaskbar: true,
    backgroundColor: '#00000000',
    webPreferences: {
      preload: path.join(__dirname, 'preload.cjs'),
      contextIsolation: true,
      nodeIntegration: false,
      webSecurity: false,      // загрузка локальных VRM по file://
    },
  });
  win.setAlwaysOnTop(ALWAYS_ON_TOP, 'screen-saver');
  // NIMA_DEBUG_OPAQUE=1 — непрозрачный фон: диагностика «пустого окна»
  // (если в этом режиме Нима видна — проблема в композитинге прозрачности)
  // NIMA_DEBUG_QUERY="jellylog&jelly=1" — произвольные query-параметры рендереру
  {
    const query = {
      ...(process.env.NIMA_DEBUG_OPAQUE === '1' ? { opaque: '1' } : {}),
      ...Object.fromEntries(new URLSearchParams(process.env.NIMA_DEBUG_QUERY || '')),
    };
    win.loadFile(path.join(__dirname, 'renderer', 'index.html'),
                 Object.keys(query).length ? { query } : undefined);
  }

  // позиция окна для тестов/настройки: NIMA_X / NIMA_Y
  const px = parseInt(process.env.NIMA_X, 10), py = parseInt(process.env.NIMA_Y, 10);
  if (Number.isFinite(px) && Number.isFinite(py)) win.setPosition(px, py);
  // все ошибки/логи консоли рендерера — в renderer_error.log (диагностика «пустого окна»)
  win.webContents.on('console-message', (_e, level, message, line, sourceId) => {
    const short = String(message).replace(/\s+/g, ' ').slice(0, 300);
    logLine(`console[${level}] ${path.basename(String(sourceId))}:${line} ${short}`);
  });
  win.webContents.on('preload-error', (_e, p, err) => logLine(`preload-error ${p}: ${err}`));
  win.webContents.on('did-fail-load', (_e, code, desc) => logLine(`did-fail-load ${code} ${desc}`));
  win.webContents.once('did-finish-load', () => {
    win.webContents.send('init', { vrmDir: VRM_DIR, statePath: STATE_PATH });
  });
  // Авто-перезагрузка рендерера при пересборке viewer.bundle.js (v14.8.38).
  // Раньше окно жило на коде momenta первого старта: правки физики
  // «не доходили», пока не убьёшь процесс целиком (bridge.start() при
  // живом Electron ничего не перезапускает). Mtime-поллинг — надёжнее
  // fs.watch на Windows.
  const BUNDLE = path.join(__dirname, 'renderer', 'viewer.bundle.js');
  let bundleMtime = 0;
  setInterval(() => {
    try {
      const m = fs.statSync(BUNDLE).mtimeMs;
      if (bundleMtime && Math.abs(m - bundleMtime) > 50 && win && !win.isDestroyed()) {
        logLine('viewer.bundle.js changed — reload renderer');
        win.webContents.reload();
      }
      bundleMtime = m;
    } catch (_) {}
  }, 2000);

  // Ресайз: рендерер присылёт АБСОЛЮТНЫЙ целевой размер (стартовый размер +
  // 2×дельта курсора). Инкрементальные дельты нельзя: setBounds применяется
  // асинхронно, чтение getSize() между move-событиями даёт устаревший размер —
  // окно «съезжает» и сжимается посреди перетаскивания.
  // Привязка: по горизонтали — СИММЕТРИЧНО от центра (правая кромка следует
  // за курсором), по вертикали — от НЕПОДВИЖНОГО верхнего края. Если растить
  // от центра и по вертикали, верхняя кромка (где точка) едет НАВСТРЕЧУ
  // курсору — пользователь тянет вниз, а окно уходит вверх.
  // Окно frameless стоит resizable:false (иначе у него появляются системные
  // границы), поэтому на время setBounds включаем resizable и возвращаем
  // обратно — иначе Windows не даст изменить размер и вылетит ошибка.
  ipcMain.on('win:resize-to', (_e, { width, height }) => {
    if (!win || win.isDestroyed()) return;
    if (!Number.isFinite(width) || !Number.isFinite(height)) return;
    const w = Math.min(MAX_W, Math.max(MIN_W, Math.round(width)));
    const h = Math.min(MAX_H, Math.max(MIN_H, Math.round(height)));
    const b = win.getBounds();
    if (w === b.width && h === b.height) return;
    const cx = b.x + b.width / 2;          // getCenter у окна нет
    win.setResizable(true);
    try {
      win.setBounds({ x: Math.round(cx - w / 2), y: b.y, width: w, height: h });
    } finally {
      win.setResizable(false);
    }
  });
  ipcMain.on('win:move-by', (_e, { dx, dy }) => {
    if (!win || win.isDestroyed()) return;
    const [x, y] = win.getPosition();
    win.setPosition(Math.round(x + dx), Math.round(y + dy));
  });
  ipcMain.on('win:lock', (_e, locked) => {
    if (win && !win.isDestroyed()) win.setMovable(!locked);
  });

  // запоминаем геометрию окна (движение/ресайз/закрытие); дебаунс — setBounds
  // сыплет событиями посреди перетаскивания
  let saveTimer = null;
  const scheduleSave = () => {
    if (saveTimer) clearTimeout(saveTimer);
    saveTimer = setTimeout(saveWinState, 400);
  };
  win.on('move', scheduleSave);
  win.on('resize', scheduleSave);
  win.on('close', () => {
    if (saveTimer) clearTimeout(saveTimer);
    saveWinState();
  });

  // следим за состоянием: fs.watch + ручной таймер как страховка
  try {
    fs.watch(STATE_PATH, readState);
  } catch (_) { /* файла может не быть при первом старте */ }
  stateReadTimer = setInterval(readState, 300);
}

app.whenReady().then(createWindow);

// NIMA_SHOT=/путь/кадр.png — отладочный снимок: через NIMA_SHOT_DELAY сек
// (по умолчанию 12 — ждём загрузку модели) сохранить кадр окна в PNG и
// закрыться. Для тестов поз/анимаций; с NIMA_DEBUG_OPAQUE=1 фон непрозрачный.
app.whenReady().then(() => {
  if (!process.env.NIMA_SHOT) return;
  const delay = (parseInt(process.env.NIMA_SHOT_DELAY, 10) || 12) * 1000;
  setTimeout(async () => {
    try {
      const img = await win.webContents.capturePage();
      fs.writeFileSync(process.env.NIMA_SHOT, img.toPNG());
      console.log('[nima-shot] saved:', process.env.NIMA_SHOT);
    } catch (err) {
      console.error('[nima-shot] failed:', err);
    }
    app.quit();
  }, delay);
});

app.on('window-all-closed', () => {
  if (stateReadTimer) clearInterval(stateReadTimer);
  app.quit();
});

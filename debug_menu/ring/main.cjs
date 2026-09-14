// main.cjs — Electron-процесс кольцевого дебаг-меню.
//
// Окно: прозрачное, без рамки, без тени, поверх всех. На экране существует
// ТОЛЬКО содержимое колец — всё остальное прозрачно и пропускает клики
// (setIgnoreMouseEvents по хит-тесту из рендерера).
//
// «Мозг» меню остаётся в Python (debug_menu/): этот процесс общается с ним
// по локальному HTTP (порт приходит в переменной RING_PORT):
//   GET  /menu   — список категорий/пунктов/иконок + версия
//   GET  /state  — {visible, quit}: показывать ли кольцо / закрыть приложение
//   POST /action — {"action": "launch_bot", ...} — выбор пункта
const { app, BrowserWindow, ipcMain } = require('electron');
const path = require('path');

const RING_PORT = process.env.RING_PORT || '8791';
const BASE = `http://127.0.0.1:${RING_PORT}`;
const ERR_LOG = path.join(__dirname, 'ring_error.log');

let win = null;
let quitting = false;

function logLine(msg) {
  try { require('fs').appendFileSync(ERR_LOG, `${new Date().toISOString()} ${msg}\n`, 'utf-8'); } catch (_) {}
}

function createWindow() {
  win = new BrowserWindow({
    width: 760,
    height: 760,
    transparent: true,
    frame: false,
    resizable: false,
    hasShadow: false,          // никакой тени по контуру — только кольца
    thickFrame: false,
    enableLargerThanScreen: false,
    backgroundColor: '#00000000',
    webPreferences: {
      preload: path.join(__dirname, 'preload.cjs'),
      contextIsolation: true,
      nodeIntegration: false,
    },
  });
  win.loadFile(path.join(__dirname, 'index.html'), { query: { port: RING_PORT } });

  win.webContents.on('console-message', (_e, level, message, line, sourceId) => {
    logLine(`console[${level}] ${path.basename(String(sourceId))}:${line} ${String(message).slice(0, 300)}`);
  });
  win.webContents.on('preload-error', (_e, p, err) => logLine(`preload-error ${p}: ${err}`));
  win.webContents.on('did-fail-load', (_e, code, desc) => logLine(`did-fail-load ${code} ${desc}`));

  // перетаскивание окна за кольца (рендерер шлёт дельты курсора)
  ipcMain.on('ring:move-by', (_e, { dx, dy }) => {
    if (!win || win.isDestroyed()) return;
    const [x, y] = win.getPosition();
    win.setPosition(Math.round(x + dx), Math.round(y + dy));
  });

  // хит-тест: курсор над кольцами — окно принимает клики; вне — пропускает их
  ipcMain.on('ring:hit', (_e, hit) => {
    if (!win || win.isDestroyed()) return;
    win.setIgnoreMouseEvents(!hit, { forward: true });
  });

  // опрос Python: показывать ли кольцо / пора ли закрываться
  let visible = true;
  setInterval(async () => {
    if (quitting) return;
    try {
      const res = await fetch(`${BASE}/state`, { signal: AbortSignal.timeout(1500) });
      const st = await res.json();
      if (st.quit) { quitting = true; app.quit(); return; }
      if (win && !win.isDestroyed() && st.visible !== visible) {
        visible = st.visible;
        if (visible) win.show();
        else win.hide();
      }
    } catch (_) { /* Python ещё не поднялся / уже упал — попробуем позже */ }
  }, 300);
}

app.whenReady().then(createWindow);
app.on('window-all-closed', () => app.quit());

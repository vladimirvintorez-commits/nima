const { app, BrowserWindow, ipcMain } = require('electron');
const fs = require('fs');
const path = require('path');

const ROOT = path.resolve(__dirname, '..', '..');
const AVATAR_DIR = path.join(ROOT, 'avatar');
const MODEL_DIR = path.join(ROOT, 'vita_avatar_app');
const MODEL = 'Nima_Sexual.vrm';
const ALL_GESTURES = ['hips', 'stretch', 'gesture', 'show', 'butt', 'jiggle', 'ears', 'fix', 'bosom'];
const gestureArg = process.argv.find((arg) => arg.startsWith('--gesture='));
const GESTURES = gestureArg ? [gestureArg.slice('--gesture='.length)] : ALL_GESTURES;
const CYCLE_MS = 6500;
const SAMPLE_MS = 50;
const STEP_TIMEOUT_MS = 20000;
const RUN_DIR = path.join(ROOT, '.electron-invariant-runtime');
const LOG = path.join(ROOT, 'electron-invariant-harness.log');
const OUTPUT = path.join(ROOT, `electron-invariant-${GESTURES[0] || 'unknown'}.json`);

fs.mkdirSync(RUN_DIR, { recursive: true });
app.setPath('userData', path.join(RUN_DIR, 'userData'));
app.setPath('cache', path.join(RUN_DIR, 'cache'));
app.commandLine.appendSwitch('disable-gpu-shader-disk-cache');
// CPU-only: сбой GPU-процесса вешает executeJavaScript (sample timeout).
app.commandLine.appendSwitch('disable-gpu');

function log(stage, detail = '') {
  fs.appendFileSync(LOG, `${new Date().toISOString()} ${stage}${detail ? ` ${detail}` : ''}\n`, 'utf8');
}
function finite(values) { return Array.isArray(values) && values.every(Number.isFinite); }
function delay(ms) { return new Promise((resolve) => setTimeout(resolve, ms)); }
function timeout(promise, label, ms = STEP_TIMEOUT_MS) {
  let timer;
  return Promise.race([
    promise.finally(() => clearTimeout(timer)),
    new Promise((_, reject) => { timer = setTimeout(() => reject(new Error(`${label} timeout after ${ms}ms`)), ms); }),
  ]);
}
function fatal(kind, error) {
  const message = error?.stack || error?.message || String(error);
  log(`fatal:${kind}`, message.replace(/\s+/g, ' '));
  try { fs.writeFileSync(OUTPUT, JSON.stringify({ model: MODEL, fatal: kind, error: message }, null, 2)); } catch (_) {}
  app.exit(2);
}

log('process:start', process.argv.join(' '));
process.on('uncaughtException', (error) => fatal('uncaughtException', error));
process.on('unhandledRejection', (error) => fatal('unhandledRejection', error));
app.on('before-quit', () => log('app:before-quit'));
app.on('will-quit', () => log('app:will-quit'));
app.on('quit', (_event, code) => log('app:quit', String(code)));

log('app:ready:before');
timeout(app.whenReady(), 'app ready').then(async () => {
  log('app:ready:after');
  log('window:create:before');
  const win = new BrowserWindow({
    width: 520,
    height: 780,
    show: false,
    webPreferences: {
      preload: path.join(AVATAR_DIR, 'preload.cjs'),
      contextIsolation: true,
      nodeIntegration: false,
      webSecurity: false,
      backgroundThrottling: false,
    },
  });
  log('window:create:after');
  win.webContents.on('did-fail-load', (_e, code, desc, url) => fatal('did-fail-load', new Error(`${code} ${desc} ${url}`)));
  win.webContents.on('render-process-gone', (_e, details) => fatal('render-process-gone', new Error(JSON.stringify(details))));
  win.webContents.on('console-message', (_e, level, message) => log(`renderer:console:${level}`, String(message).replace(/\s+/g, ' ')));

  ipcMain.handle('vrm:read', (_event, fileName) => {
    const filePath = /\.vrm$/i.test(fileName)
      ? path.join(MODEL_DIR, fileName)
      : path.join(MODEL_DIR, 'animations', fileName);
    if (!fs.existsSync(filePath)) return null;
    const buffer = fs.readFileSync(filePath);
    return buffer.buffer.slice(buffer.byteOffset, buffer.byteOffset + buffer.byteLength);
  });

  const results = [];
  for (const gesture of GESTURES) {
    log('gesture:before', gesture);
    const page = path.join(AVATAR_DIR, 'renderer', 'index.html');
    const url = `file://${page.replace(/\\/g, '/')}?invariant=1&event=${encodeURIComponent(gesture)}`;
    log('load:before', url);
    await timeout(win.loadURL(url), `load ${gesture}`);
    log('load:after', gesture);
    win.webContents.send('init', { vrmDir: MODEL_DIR, statePath: '' });
    win.webContents.send('state', { current_outfit: MODEL, action: '', speaking: false, mouth: 0 });

    log('model-ready:before', gesture);
    await timeout((async () => {
      while (true) {
        const ready = await win.webContents.executeJavaScript('Boolean(window.__nimaInvariantProbe)');
        if (ready) return;
        await delay(100);
      }
    })(), `model ready ${gesture}`);
    log('model-ready:after', gesture);

    const samples = [];
    const started = Date.now();
    while (Date.now() - started <= CYCLE_MS) {
      samples.push(await timeout(win.webContents.executeJavaScript(`window.__nimaInvariantProbe.sample()`), `sample ${gesture}`, 3000));
      await delay(SAMPLE_MS);
    }
    const restored = await timeout(win.webContents.executeJavaScript('window.__nimaInvariantProbe.restored()'), `restore ${gesture}`, 3000);
    const firstFailure = samples.find((sample) => !sample.ready || !sample.bonesFinite) || null;
    const ok = samples.length > 0 && !firstFailure && restored;
    const result = { gesture, ok, restored, samples: samples.length, firstFailure };
    results.push(result);
    log('gesture:after', JSON.stringify(result));
  }

  const report = { model: MODEL, cycleMs: CYCLE_MS, results };
  fs.writeFileSync(OUTPUT, JSON.stringify(report, null, 2));
  process.stdout.write(`${JSON.stringify(report)}\n`);
  log('quit:before', results.every((result) => result.ok) ? '0' : '1');
  win.destroy();
  app.exit(results.every((result) => result.ok) ? 0 : 1);
}).catch((error) => fatal('top-level', error));

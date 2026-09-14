// preload.cjs — безопасный мост рендерера с main-процессом.
const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('nima', {
  onInit: (cb) => ipcRenderer.on('init', (_e, data) => cb(data)),
  onState: (cb) => ipcRenderer.on('state', (_e, data) => cb(data)),
  readVrm: (fileName) => ipcRenderer.invoke('vrm:read', fileName),
  resizeTo: (width, height) => ipcRenderer.send('win:resize-to', { width, height }),
  moveBy: (dx, dy) => ipcRenderer.send('win:move-by', { dx, dy }),
  setLock: (locked) => ipcRenderer.send('win:lock', locked),
});

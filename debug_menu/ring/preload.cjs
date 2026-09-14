// preload.cjs — мост рендерера кольцевого меню с main-процессом.
const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('ring', {
  moveBy: (dx, dy) => ipcRenderer.send('ring:move-by', { dx, dy }),
  setHit: (hit) => ipcRenderer.send('ring:hit', !!hit),
});

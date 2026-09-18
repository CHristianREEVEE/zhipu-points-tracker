// v2：暴露穿透三件套给页面
const { contextBridge, ipcRenderer, shell } = require('electron');
contextBridge.exposeInMainWorld('__electron', {
  // 打开完整面板（菜单+页面双通道）
  openFull: () => ipcRenderer.send('open-full'),
  // 进入穿透（页面按钮触发）
  ptSet: (on) => ipcRenderer.send('pt-set', on),
  // 穿透中临时武装/解除鼠标事件（右键恢复用）
  ptArm: (on) => ipcRenderer.send('pt-arm', on),
  // 主进程通知穿透状态变化（右键菜单恢复时）
  onPtState: (cb) => ipcRenderer.on('pt-state', (_e, on) => cb(on)),
});

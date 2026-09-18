'use strict';
// 智谱积分悬浮窗 - Electron 主进程 v2
// 穿透模型（按南浊 09-19 00:08 定的规矩）：
//   点「穿透」→ setIgnoreMouseEvents(true, forward) → 鼠标完全穿过窗口，不影响任何底层使用
//   穿透中页面靠 forward 的 mousemove 自感知光标 → ptArm(true) 临时恢复鼠标事件
//   → 右键菜单「恢复交互」退出；左键全程锁定，绝不误触
const { app, BrowserWindow, Menu, shell, screen, ipcMain } = require('electron');
const path = require('path');
const fs = require('fs');

// ==== 配置（打包时占位，构建脚本替换真实值）====
const WIDGET_URL = '__WIDGET_URL__';
const PAGE_KEY = '__PAGE_KEY__';
const CFG_PATH = path.join(path.dirname(app.getPath('exe')), 'widget.config.json');
const WIN_W = 300, WIN_H = 252;   // v2 加高：任务列表+时钟

let win = null;
let clickThrough = false;   // 穿透模式（用户意图）
let armed = false;          // 穿透中临时恢复鼠标事件（右键恢复用）

function loadCfg() {
  try { return JSON.parse(fs.readFileSync(CFG_PATH, 'utf-8')); } catch { return {}; }
}
function saveCfg(k, v) {
  const cfg = loadCfg(); cfg[k] = v;
  try { fs.writeFileSync(CFG_PATH, JSON.stringify(cfg, null, 2)); } catch {}
}

function applyMouseEvents() {
  if (!win) return;
  // 只有「穿透中且未武装」才真正忽略鼠标；其余一律正常接收
  win.setIgnoreMouseEvents(clickThrough && !armed, { forward: true });
}

function setClickThrough(on) {
  clickThrough = on;
  armed = false;
  applyMouseEvents();
  if (win) win.webContents.send('pt-state', on);
}

function createWindow() {
  const cfg = loadCfg();
  const opts = {
    width: WIN_W, height: WIN_H,
    x: cfg.x, y: cfg.y,
    transparent: true, frame: false, resizable: false,
    alwaysOnTop: true, skipTaskbar: true,
    hasShadow: false,
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
    },
  };
  if (cfg.x === undefined) {
    const wa = screen.getPrimaryDisplay().workArea;
    opts.x = wa.x + wa.width - WIN_W - 24;
    opts.y = wa.y + 24;
  }
  win = new BrowserWindow(opts);
  win.setAlwaysOnTop(true, 'screen-saver');

  win.loadURL(WIDGET_URL).then(async () => {
    try {
      await win.webContents.executeJavaScript(
        `localStorage.setItem('pk', ${JSON.stringify(PAGE_KEY)}); undefined`);
      win.webContents.reloadIgnoringCache();
    } catch {}
  });

  // 位置记忆
  const savePos = () => {
    if (!win) return;
    const [x, y] = win.getPosition();
    if (x >= -50 && y >= -50) saveCfg('x', x), saveCfg('y', y);
  };
  win.on('moved', savePos);
  win.on('close', savePos);

  // ---- IPC ----
  ipcMain.on('open-full', () => shell.openExternal(WIDGET_URL.replace(/widget\.html.*$/, '')));
  ipcMain.on('pt-set', (_e, on) => setClickThrough(on));
  ipcMain.on('pt-arm', (_e, on) => { armed = !!on; applyMouseEvents(); });

  // ---- 右键菜单（穿透恢复的唯一入口）----
  win.webContents.on('context-menu', () => {
    Menu.buildFromTemplate([
      { label: '恢复交互（退出穿透）', enabled: clickThrough, click: () => setClickThrough(false) },
      { label: '打开完整面板', click: () => shell.openExternal(WIDGET_URL.replace(/widget\.html.*$/, '')) },
      { type: 'separator' },
      { label: '退出悬浮窗', click: () => app.quit() },
    ]).popup();
  });

  // 双保险：穿透中如果用户右键点到了窗口（armed 状态下），确保菜单能弹出
  win.on('blur', () => { /* 保持穿透，不自动退出 */ });

  win.on('closed', () => { win = null; });
}

app.whenReady().then(() => {
  const got = app.requestSingleInstanceLock();
  if (!got) { app.quit(); return; }
  createWindow();
});
app.on('window-all-closed', () => app.quit());
app.on('second-instance', () => {
  if (win) { win.show(); win.focus(); setClickThrough(false); }
});

'use strict';
// 智谱积分悬浮窗 - Electron 主进程 v2.1
// 穿透模型 v2.1（09-19 修右键恢复失效 bug）：
//   v2 的「页面 mousemove 武装 + 3 秒无移动自动解除」有致命竞态：
//   用户瞄准悬浮窗停顿 >3 秒准备右键 → 定时器误判"光标已离开"→ 解除武装 →
//   右键漏穿到桌面，弹出的是 Windows 桌面菜单而非本窗菜单。
//   v2.1 改为「主进程轮询光标位置」：光标在窗口内 → 接收鼠标（右键/选择可用）；
//   离开窗口 → 恢复穿透。不依赖 DOM 事件，悬停多久右键都有效。
//   另加 Ctrl+Alt+P 全局快捷键切换穿透（终极逃生口，右键万一再失效也有救）。
const { app, BrowserWindow, Menu, shell, screen, ipcMain, globalShortcut } = require('electron');
const path = require('path');
const fs = require('fs');

// ==== 配置（打包时占位，构建脚本替换真实值）====
const WIDGET_URL = 'https://reeve10001.top/points/widget.html';
const PAGE_KEY = '470cb3cb69a6d0a0e9876c6f';
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

function cursorInWindow() {
  if (!win) return false;
  try {
    const c = screen.getCursorScreenPoint();
    const b = win.getBounds();
    return c.x >= b.x && c.x < b.x + b.width && c.y >= b.y && c.y < b.y + b.height;
  } catch { return false; }
}

function setClickThrough(on) {
  clickThrough = on;
  armed = false;
  applyMouseEvents();
  if (win) win.webContents.send('pt-state', on);
}

// ==== 核心修复：主进程光标轮询（120ms）====
// 光标进入窗口 → 武装（右键/文本选择可落到本窗）；离开 → 回到穿透。
setInterval(() => {
  if (!win || !clickThrough) return;
  const inside = cursorInWindow();
  if (inside !== armed) { armed = inside; applyMouseEvents(); }
}, 120);

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
  // 页面武装/解除请求：主进程用光标实际位置把关，
  // 旧版页面的「3 秒无移动解除」在光标仍在窗口内时会被直接忽略
  ipcMain.on('pt-arm', (_e, on) => {
    if (!win) return;
    if (on) {
      if (clickThrough && !armed && cursorInWindow()) { armed = true; applyMouseEvents(); }
    } else {
      if (armed && !cursorInWindow()) { armed = false; applyMouseEvents(); }
    }
  });

  // ---- 右键菜单（穿透恢复入口之一）----
  win.webContents.on('context-menu', () => {
    Menu.buildFromTemplate([
      { label: clickThrough ? '恢复交互（退出穿透）' : '进入穿透', click: () => setClickThrough(!clickThrough) },
      { label: '打开完整面板', click: () => shell.openExternal(WIDGET_URL.replace(/widget\.html.*$/, '')) },
      { type: 'separator' },
      { label: '退出悬浮窗', click: () => app.quit() },
    ]).popup();
  });

  win.on('closed', () => { win = null; });
}

app.whenReady().then(() => {
  const got = app.requestSingleInstanceLock();
  if (!got) { app.quit(); return; }
  // 全局快捷键兜底：Ctrl+Alt+P 切换穿透（就算窗口鼠标全失效也能救回来）
  try { globalShortcut.register('Ctrl+Alt+P', () => {
    if (win) { win.show(); setClickThrough(!clickThrough); }
  }); } catch {}
  createWindow();
});
app.on('will-quit', () => { try { globalShortcut.unregisterAll(); } catch {} });
app.on('window-all-closed', () => app.quit());
app.on('second-instance', () => {
  if (win) { win.show(); win.focus(); setClickThrough(false); }
});

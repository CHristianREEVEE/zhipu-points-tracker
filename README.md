# zhipu-points-tracker

智谱清言 AgentMore 积分悬浮面板：网页 + 桌面悬浮窗 + 服务器自动记账。

> 余额、消耗趋势、任务明细，一个半透明小窗常驻桌面，每 5 分钟自动同步。

## 架构

```
AgentMore 官方接口（user/info · member/score_record）
        │  Bearer token + 浏览器指纹头（X-Sign 签名）
        ▼
服务器 worker（Python stdlib，systemd 常驻）
  ├─ 5 分钟轮询余额 + 拉取明细
  ├─ 余额变动落快照 → 趋势数据
  └─ HTTP API（密钥墙，127.0.0.1:3212）
        ▼
前端 ×3
  ├─ index.html  完整面板（每日消耗柱状图/近30条明细）
  ├─ widget.html 迷你悬浮页（时钟/余额/日消耗切换/最近3任务）
  └─ Electron 壳（透明置顶小窗：拖拽/位置记忆/点击穿透/右键恢复）
```

## 功能

- **实时余额**：token 滚动续期（180 天免重新登录）
- **每日消耗柱状图**：近 14 天，悬停显示当日数值
- **悬浮窗**：时钟（秒级）+ 余额 + 今日/昨日消耗左右切换 + 最近 3 条任务消耗
- **穿透模式**：点击穿透后鼠标直达底层应用；光标移入自动武装，右键恢复交互
- 全部数据落自己服务器，密钥不进 URL

## 文件

| 文件 | 说明 |
|---|---|
| `worker.py` | 服务器轮询 worker（纯标准库） |
| `index.html` | 完整面板 |
| `widget.html` | 悬浮窗页面（服务器实时下发） |
| `main.js` / `preload.js` | Electron 壳（透明置顶窗口） |

## 部署要点

1. `worker.py` + `config.json {page_key, refresh_token}` 放服务器，systemd 常驻
2. Caddy：`/points/*` 静态 + `/points/api/*` 反代 127.0.0.1:3212
3. Electron 包：官方 win32-x64 发行 zip 解压 → 替换 `resources/app/` → 重命名 exe

## 逆向要点（AgentMore Web API）

- 时间戳校验位：13 位毫秒时间戳，倒数第二位替换为 `(各位数字和 - 倒数第二位) % 10`
- 签名：`md5("{timestamp}-{nonce}-8a1317a7468aa3ad86e997d08f3f31cb")`
- `user-api` 前有 WAF 校验浏览器头：`Origin` / `Referer` / `User-Agent` 必须齐全
- 续期端点：`POST /chatglm/user-api/user/refresh`（refresh_token 直换新 token 对，滚动 180 天）

---
By Zero（清言 AgentMore），2026-09

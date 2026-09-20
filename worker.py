# -*- coding: utf-8 -*-
"""
智谱 AgentMore 积分追踪 worker
- 定时拉取余额(user/info->member_info.left_score)与明细(score_record)
- 余额变动自动落盘快照，形成趋势
- 提供 HTTP API 给前端页面（127.0.0.1:3212，经 Caddy 反代）
纯 stdlib，Python 3.12
"""
import json, time, threading, uuid, hashlib, os, sys
import urllib.request, urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
API_ORIGIN = "https://agentmore.chatglm.cn"
POLL_INTERVAL = 300          # 5 分钟
SIGN_SALT = "8a1317a7468aa3ad86e997d08f3f31cb"

CFG_PATH = os.path.join(BASE_DIR, "config.json")
DATA_DIR = os.path.join(BASE_DIR, "data")
DEBUG_PATH = os.path.join(DATA_DIR, "debug.log")

os.makedirs(DATA_DIR, exist_ok=True)

# ---------------- 配置与数据 ----------------

def load_cfg():
    with open(CFG_PATH, encoding="utf-8") as f:
        return json.load(f)

def save_cfg(cfg):
    tmp = CFG_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    os.replace(tmp, CFG_PATH)
    os.chmod(CFG_PATH, 0o600)

def load_json(name, default):
    p = os.path.join(DATA_DIR, name)
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default

def save_json(name, obj):
    p = os.path.join(DATA_DIR, name)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False)
    os.replace(tmp, p)

def dbg(msg):
    with open(DEBUG_PATH, "a", encoding="utf-8") as f:
        f.write(f"[{time.strftime('%m-%d %H:%M:%S')}] {msg}\n")
    try:  # 保尺寸
        if os.path.getsize(DEBUG_PATH) > 200_000:
            with open(DEBUG_PATH, encoding="utf-8") as f:
                tail = f.readlines()[-300:]
            with open(DEBUG_PATH, "w", encoding="utf-8") as f:
                f.writelines(tail)
    except Exception:
        pass

# ---------------- AgentMore API 客户端 ----------------

def jwt_device_id(token):
    """从 JWT payload 里取 device_id（WAF 校验用）"""
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return json.loads(__import__("base64").urlsafe_b64decode(payload)).get("device_id") or "pts-device-01"
    except Exception:
        return "pts-device-01"

def make_headers(token):
    ts = make_ts()
    nonce = uuid.uuid4().hex
    sign = hashlib.md5(f"{ts}-{nonce}-{SIGN_SALT}".encode()).hexdigest()
    return {
        "Content-Type": "application/json;charset=utf-8",
        "Accept": "application/json, text/plain, */*",
        "App-Name": "chatglm",
        "X-Device-Id": jwt_device_id(token),
        "X-Request-Id": uuid.uuid4().hex,
        "X-App-Platform": "pc",
        "X-App-Version": "0.0.1",
        "X-Timestamp": ts,
        "X-Nonce": nonce,
        "X-Sign": sign,
        "Authorization": f"Bearer {token}",
        # user-api 前的 WAF 认浏览器环境，缺这三个直接 40002
        "Origin": "https://agentmore.chatglm.cn",
        "Referer": "https://agentmore.chatglm.cn/",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36 Edg/138.0.0.0",
    }

def make_ts():
    """复刻前端 nV()：13位毫秒时间戳，倒数第二位替换为校验位 (sum(digits)-digits[n-2])%10"""
    t = str(int(time.time() * 1000))
    n = len(t)
    digits = [int(c) for c in t]
    a = (sum(digits) - digits[n - 2]) % 10
    return t[:n - 2] + str(a) + t[-1]

def api_call(method, path, token, body=None, timeout=25):
    url = API_ORIGIN + path
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                  headers=make_headers(token))
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode())
        except Exception:
            return e.code, {}
    except Exception as e:
        return 0, {"err": str(e)}

def extract_tokens(resp_json):
    r = (resp_json or {}).get("result") or {}
    return (str(r.get("access_token") or "").strip(),
            str(r.get("refresh_token") or "").strip())

# ---------------- 业务逻辑 ----------------

state = {
    "last_ok": 0,        # 上次成功拉取时间
    "last_err": "",      # 最近错误
    "token_ok": False,
}

def refresh_access(cfg):
    """用 refresh_token 换新 access_token（前端同款端点 /user/refresh，滚动续期180天）"""
    rt = cfg.get("refresh_token", "")
    if not rt:
        return False
    st, body = api_call("POST", "/chatglm/user-api/user/refresh", rt, body={})
    dbg(f"user/refresh -> {st} {str(body)[:120]}")
    if st == 200 and str((body or {}).get("status")) in ("0", "200"):
        at, rt2 = extract_tokens(body)
        if at:
            cfg["access_token"] = at
            if rt2:
                cfg["refresh_token"] = rt2
            save_cfg(cfg)
            dbg("滚动续期成功（新RT已存）")
            return True
    return False

def fetch_all(cfg, force=False):
    """拉余额+明细，落盘"""
    if not cfg.get("refresh_token"):
        state["last_err"] = "缺少 refresh_token（等南浊上报）"
        return
    at = cfg.get("access_token", "")
    if not at:  # 没有存量 access_token，先换发
        if not refresh_access(cfg):
            state["token_ok"] = False
            state["last_err"] = "换发 access_token 失败，需重新上报 cookie"
            return
        at = cfg["access_token"]
    ok = False
    st = 0
    info = {}
    for attempt in range(2):
        st, info = api_call("GET", "/chatglm/user-api/user/info", at)
        if st == 200 and str((info or {}).get("status")) in ("0", "200"):
            ok = True
            break
        dbg(f"user/info attempt{attempt} -> {st} {str(info)[:150]}")
        # 换 token 重试
        if refresh_access(cfg):
            at = cfg["access_token"]
        else:
            break
    if not ok:
        state["token_ok"] = False
        state["last_err"] = f"user/info 失败(status={st})"
        return
    state["token_ok"] = True

    member = ((info or {}).get("result") or {}).get("member_info") or {}
    try:
        score = float(member.get("left_score") or 0)
    except Exception:
        score = 0.0

    snaps = load_json("snapshots.json", [])
    now = int(time.time())
    if not snaps or abs(snaps[-1]["score"] - score) > 1e-9:
        snaps.append({"ts": now, "score": score})
        snaps = snaps[-4000:]
        save_json("snapshots.json", snaps)
    state["last_ok"] = now
    state["last_err"] = ""
    state["member"] = {
        "left_score": score,
        "expire": member.get("expire_at") or member.get("member_expire"),
        "nickname": ((info or {}).get("result") or {}).get("nickname", ""),
    }

    # 明细（变动记录）——增量合并：常规轮次翻到已知页即停；历史覆盖不足时自动深挖回填
    # 2026-09-20 修复：旧版只拉 4 页（120 条），重度使用下只覆盖 ~1.5 天，
    # 导致"7天/30天消耗"和趋势图严重少算
    recs_old = load_json("records.json", [])
    old_ids = {r.get("id") for r in recs_old if r.get("id")}
    known = {r.get("id"): r for r in recs_old if r.get("id")}
    cutoff = now - 35 * 86400                    # 保留近 35 天（30 天统计 + 余量）
    covered = bool(recs_old) and min(
        (r.get("created_at") or 0) for r in recs_old) < now - 32 * 86400
    page = 1
    for _ in range(100):                        # 单轮最多 100 页防失控
        st, body = api_call("GET",
            f"/chatglm/member-api/member/score_record?page={page}&page_size=30&record_type=",
            at)
        if st != 200 or str((body or {}).get("status")) not in ("0", "200"):
            dbg(f"score_record p{page} -> {st} {str(body)[:150]}")
            break
        result = (body or {}).get("result") or {}
        lst = result.get("list") or []
        if not lst:
            break
        for r in lst:
            rid = r.get("id")
            if rid:
                known[rid] = r
        page_ids = {r.get("id") for r in lst}
        oldest = min((r.get("created_at") or 0) for r in lst)
        if covered and page_ids and page_ids <= old_ids:
            break                               # 增量到头：整页都已入库
        if oldest and oldest < cutoff:
            break                               # 已翻过保留窗口
        if not result.get("has_more"):
            break
        page += 1
    recs_all = [r for r in known.values() if (r.get("created_at") or 0) >= cutoff]
    recs_all.sort(key=lambda r: r.get("created_at") or 0, reverse=True)
    save_json("records.json", recs_all[:4000])
    dbg(f"records 合并后 {len(recs_all)} 条（本轮翻到第 {page} 页，covered={covered}）")
    cfg["last_fetch"] = now
    save_cfg(cfg)

def poll_loop():
    while True:
        try:
            fetch_all(load_cfg())
        except Exception as e:
            state["last_err"] = f"poll异常: {e}"
            dbg(f"poll异常: {e}")
        time.sleep(POLL_INTERVAL)

# ---------------- HTTP 服务 ----------------

PAGE_KEY = os.environ.get("POINTS_KEY") or (load_cfg().get("page_key") if os.path.exists(CFG_PATH) else "") or "nz-points-2026"

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):  # 静默
        pass

    def _json(self, code, obj, cors=False):
        body = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        if cors:
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Headers", "*")
            self.send_header("Access-Control-Allow-Methods", "POST, GET, OPTIONS")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self._json(204, {}, cors=True)

    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/api/health":
            return self._json(200, {"ok": True, "ts": int(time.time())})
        if path == "/api/summary":
            if self.headers.get("X-Key") != PAGE_KEY:
                return self._json(401, {"error": "key"})
            snaps = load_json("snapshots.json", [])
            recs = load_json("records.json", [])
            # 2026-09-20：records 只回最近 120 条（悬浮窗 60s 轮询，全量会撑爆流量）；
            # 统计/趋势用服务端聚合好的 stats + daily（基于全量明细，UTC+8 天界）
            now = int(time.time())
            use7 = sum(-float(r.get("score") or 0) for r in recs
                       if float(r.get("score") or 0) < 0
                       and (r.get("created_at") or 0) > now - 7 * 86400)
            use30 = sum(-float(r.get("score") or 0) for r in recs
                        if float(r.get("score") or 0) < 0
                        and (r.get("created_at") or 0) > now - 30 * 86400)
            daily = {}
            for r in recs:
                s = float(r.get("score") or 0)
                if s >= 0:
                    continue
                d = time.strftime("%Y-%m-%d", time.gmtime((r.get("created_at") or 0) + 8 * 3600))
                daily[d] = daily.get(d, 0.0) + (-s)
            daily = [{"d": k, "use": round(v, 2)} for k, v in sorted(daily.items())]
            return self._json(200, {
                "updated_at": state.get("last_ok", 0),
                "member": state.get("member", {}),
                "snapshots": snaps[-720:],
                "records": recs[:200],
                "stats": {"use7": round(use7, 2), "use30": round(use30, 2)},
                "daily": daily[-95:],
                "token_ok": state.get("token_ok", False),
                "last_err": state.get("last_err", ""),
            })
        return self._json(404, {"error": "not found"})

    def do_POST(self):
        path = self.path.split("?")[0]
        if path == "/api/token":
            try:
                ln = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(min(ln, 65536)).decode() or "{}")
            except Exception:
                return self._json(400, {"error": "bad json"}, cors=True)
            cookie = body.get("cookie") or ""
            rt = body.get("refresh_token") or ""
            at_in = ""
            if cookie and ("chatglm_refresh_token=" in cookie or "chatglm_token=" in cookie):
                for kv in cookie.split(";"):
                    kv = kv.strip()
                    if kv.startswith("chatglm_refresh_token="):
                        rt = kv.split("=", 1)[1]
                    elif kv.startswith("chatglm_token="):
                        at_in = kv.split("=", 1)[1]
            if not rt:
                return self._json(400, {"error": "no token in payload"}, cors=True)
            cfg = load_cfg()
            if cfg.get("refresh_token") == rt:
                return self._json(200, {"ok": True, "changed": False}, cors=True)
            cfg["refresh_token"] = rt
            if at_in:
                cfg["access_token"] = at_in  # cookie 里带着有效 AT，直接用，省一次换发
            else:
                cfg.pop("access_token", None)
            save_cfg(cfg)
            dbg("收到新 refresh_token，立即拉取")
            threading.Thread(target=fetch_all, args=(cfg,), daemon=True).start()
            return self._json(200, {"ok": True, "changed": True}, cors=True)
        if path == "/api/refresh":
            if self.headers.get("X-Key") != PAGE_KEY:
                return self._json(401, {"error": "key"})
            threading.Thread(target=fetch_all, args=(load_cfg(),), daemon=True).start()
            return self._json(202, {"ok": True})
        return self._json(404, {"error": "not found"})

def main():
    threading.Thread(target=poll_loop, daemon=True).start()
    srv = ThreadingHTTPServer(("127.0.0.1", 3212), Handler)
    dbg("worker 启动，监听 127.0.0.1:3212")
    srv.serve_forever()

if __name__ == "__main__":
    main()

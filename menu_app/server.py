#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
点菜单后端服务 (MySQL 版)
- 静态页面 /    (index.html)
- GET  /api/menu     从数据库读取菜单（分类+菜品）
- POST /api/order    提交订单，写入 MySQL
- GET  /api/orders   查看历史订单
- POST /api/orders/<id>/status  更新订单状态
- GET  /api/categories  分类列表
"""
import json
import os
import re as _re_multipart
import ssl
import smtplib
import secrets
import threading
from email.mime.text import MIMEText
from email.header import Header
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs
import pymysql

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
INDEX = os.path.join(BASE_DIR, "index.html")
ADMIN = os.path.join(BASE_DIR, "admin.html")
CONFIG = os.path.join(BASE_DIR, "db_config.json")
ADMIN_CFG = os.path.join(BASE_DIR, "admin_config.json")
PORT = int(os.environ.get("MENU_PORT", "8090"))

with open(CONFIG, "r", encoding="utf-8") as f:
    DB = json.load(f)
with open(ADMIN_CFG, "r", encoding="utf-8") as f:
    CONF = json.load(f)

# 简易会话 token 管理
_tokens = set()
_lock = threading.Lock()


def is_admin(headers, query=""):
    """校验管理 token(Authorization Bearer 或 admin_token 参数)"""
    auth = headers.get("Authorization", "") or ""
    tok = ""
    if auth.startswith("Bearer "):
        tok = auth[7:]
    else:
        q = parse_qs(query)
        tok = (q.get("admin_token") or [""])[0]
    return bool(tok and tok in _tokens)


def require_admin(headers, query=""):
    if not is_admin(headers, query):
        return False
    return True


def send_email(subject, body):
    """通过 QQ 邮箱 SMTP 发送订单通知邮件。未启用时返回 False。"""
    cfg = CONF.get("email", {})
    if not cfg.get("enabled") or not cfg.get("auth_code"):
        return False
    try:
        msg = MIMEText(body, "plain", "utf-8")
        msg["Subject"] = Header(subject, "utf-8")
        msg["From"] = cfg["sender"]
        msg["To"] = cfg["recipient"]
        ctx = ssl.create_default_context()
        with smtplib.SMTP_SSL(cfg["smtp_host"], cfg["smtp_port"], timeout=15, context=ctx) as s:
            s.login(cfg["sender"], cfg["auth_code"])
            s.send_message(msg)
        return True
    except Exception as e:
        print(f"[邮件发送失败] {e}")
        return False


def gen_single_image(did, emoji="🍳"):
    """为单个菜品生成占位图 SVG（新增菜时调用）"""
    import re
    img_dir = os.path.join(BASE_DIR, "img")
    os.makedirs(img_dir, exist_ok=True)
    palettes = [
        ("#ff9a8b", "#ff6a88"), ("#ffc371", "#ff5f6d"), ("#f6d365", "#fda085"),
        ("#a8e063", "#56ab2f"), ("#f2994a", "#f2c94c"), ("#89f7fe", "#66a6ff"),
        ("#f093fb", "#f5576c"), ("#e0c3fc", "#8ec5fc"), ("#fbc2eb", "#a6c1ee"),
        ("#ffecd2", "#fcb69f"), ("#d4fc79", "#96e6a1"), ("#a1c4fd", "#c2e9fb"),
        ("#ffafbd", "#ffc3a0"), ("#fdfcfb", "#e2d1c3"),
    ]
    c1, c2 = palettes[did % len(palettes)]
    emoji = re.sub(r'[^\u0000-\uFFFF]', '', emoji or "🍳")
    svg = ("<svg xmlns=\"http://www.w3.org/2000/svg\" width=\"400\" height=\"320\" "
           "viewBox=\"0 0 400 320\">"
           f"<defs><linearGradient id=\"g\" x1=\"0%\" y1=\"0%\" x2=\"100%\" y2=\"100%\">"
           f"<stop offset=\"0%\" stop-color=\"{c1}\"/><stop offset=\"100%\" stop-color=\"{c2}\"/></linearGradient></defs>"
           f"<rect width=\"400\" height=\"320\" fill=\"url(#g)\"/>"
           f"<text x=\"200\" y=\"180\" font-size=\"150\" text-anchor=\"middle\" dominant-baseline=\"middle\">{emoji}</text>"
           "<text x=\"200\" y=\"285\" font-size=\"20\" text-anchor=\"middle\" fill=\"rgba(255,255,255,0.85)\" font-family=\"sans-serif\">待上传真实图片</text>"
           "</svg>")
    with open(os.path.join(img_dir, f"dish_{did}.svg"), "w", encoding="utf-8") as f:
        f.write(svg)


def notify_order(order_id, order_items, note, ordered_by=""):
    """订单落库后异步发送邮件通知；ordered_by: 下单人(空=匿名)"""
    who = (ordered_by or "").strip()
    who_txt = (who + " 点了一单") if who else "有人下了一单（未署名·匿名）"
    subj = ("🍳 新订单 #" + str(order_id) + ((" · " + who) if who else ""))
    lines = [f"{who_txt}（#{order_id}），请准备开做！", ""]
    total_n = 0
    for did, nm, q, p in order_items:
        lines.append(f"  {nm} × {q}")
        total_n += q
    lines.append("")
    lines.append(f"共 {total_n} 份")
    if note:
        lines.append(f"备注：{note}")
    body = "\n".join(lines)
    threading.Thread(target=send_email, args=(subj, body), daemon=True).start()


def db_conn():
    return pymysql.connect(
        host=DB["host"], user=DB["user"], password=DB["password"],
        database=DB["database"], port=DB["port"],
        charset="utf8mb4", cursorclass=pymysql.cursors.DictCursor,
        autocommit=True,
    )


def get_menu():
    conn = db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id, name, icon FROM categories ORDER BY sort_order, id")
            cats = cur.fetchall()
            cur.execute("""SELECT id, category_id, name, emoji, image, description, tags, price
                           FROM dishes WHERE is_available=1 ORDER BY sort_order, id""")
            dishes = cur.fetchall()
        by_cat = {}
        for d in dishes:
            by_cat.setdefault(d["category_id"], []).append({
                "id": d["id"], "name": d["name"], "emoji": d["emoji"],
                "image": d.get("image") or "",
                "desc": d["description"], "tags": [t for t in d["tags"].split(",") if t],
                "price": float(d["price"] or 0),
            })
        return [{
            "id": c["id"], "name": c["name"], "icon": c["icon"],
            "items": by_cat.get(c["id"], []),
        } for c in cats]
    finally:
        conn.close()


def place_order(items, note="", ordered_by=""):
    """items: {dish_id: qty}; ordered_by: 下单人(家人门户用户名/空=匿名)"""
    conn = db_conn()
    try:
        # JSON 数字 key 会变成字符串，统一转 int
        items = {int(k): int(v) for k, v in items.items() if int(v) > 0}
        with conn.cursor() as cur:
            ids = list(items.keys())
            fmt = ",".join(["%s"] * len(ids))
            cur.execute(f"SELECT id, name, price FROM dishes WHERE id IN ({fmt})", ids)
            dish_map = {d["id"]: d for d in cur.fetchall()}
            total = 0
            order_items = []
            for did, qty in items.items():
                if did not in dish_map or qty <= 0:
                    continue
                d = dish_map[did]
                order_items.append((did, d["name"], qty, float(d["price"])))
                total += float(d["price"]) * qty
            cur.execute("INSERT INTO orders (status, ordered_by, note, total_price) VALUES ('pending', %s, %s, %s)",
                        (ordered_by or None, note, total))
            oid = cur.lastrowid
            cur.executemany(
                "INSERT INTO order_items (order_id, dish_id, dish_name, quantity, price) VALUES (%s,%s,%s,%s,%s)",
                [(oid, did, nm, q, p) for did, nm, q, p in order_items],
            )
            return oid, order_items, total
    finally:
        conn.close()


def get_orders():
    conn = db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""SELECT o.id, o.status, o.ordered_by, o.note, o.total_price, o.created_at,
                                  (SELECT JSON_ARRAYAGG(
                                     JSON_OBJECT('dish_name', i.dish_name, 'quantity', i.quantity, 'price', i.price))
                                   FROM order_items i WHERE i.order_id = o.id) AS items
                           FROM orders o ORDER BY o.id DESC LIMIT 50""")
            rows = cur.fetchall()
        for r in rows:
            if r.get("items"):
                r["items"] = json.loads(r["items"])
            else:
                r["items"] = []
        return rows
    finally:
        conn.close()


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False, default=str).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        path = urlparse(self.path).path
        query = urlparse(self.path).query
        try:
            if path in ("/", "/index.html"):
                with open(INDEX, "rb") as f:
                    self._send(200, f.read(), "text/html; charset=utf-8")
            elif path in ("/admin", "/admin.html"):
                if os.path.exists(ADMIN):
                    with open(ADMIN, "rb") as f:
                        self._send(200, f.read(), "text/html; charset=utf-8")
                else:
                    self._send(404, {"error": "admin page missing"})
            elif path.startswith("/img/"):
                rel = path[len("/img/"):]
                img_root = os.path.realpath(os.path.join(BASE_DIR, "img"))
                fp = os.path.realpath(os.path.join(img_root, rel))
                base = os.path.basename(fp)
                ext = base.rsplit(".", 1)[-1].lower() if "." in base else ""
                ct_map = {"svg": "image/svg+xml", "jpg": "image/jpeg", "jpeg": "image/jpeg",
                          "png": "image/png", "webp": "image/webp", "gif": "image/gif"}
                if fp.startswith(img_root + os.sep) and ext in ct_map and os.path.isfile(fp):
                    with open(fp, "rb") as f:
                        self._send(200, f.read(), ct_map[ext])
                else:
                    self._send(404, {"error": "img not found"})
            elif path == "/api/menu":
                self._send(200, get_menu())
            elif path == "/api/orders":
                self._send(200, get_orders())
            elif path == "/api/categories":
                conn = db_conn()
                try:
                    with conn.cursor() as cur:
                        cur.execute("SELECT id, name, icon FROM categories ORDER BY sort_order, id")
                        self._send(200, cur.fetchall())
                finally:
                    conn.close()
            elif path == "/api/admin/menu":   # 管理端：含 image 字段供编辑
                if not require_admin(self.headers, query):
                    self._send(401, {"error": "auth required"}); return
                conn = db_conn()
                try:
                    with conn.cursor() as cur:
                        cur.execute("SELECT id, category_id, name, emoji, image, description, tags, is_available, sort_order FROM dishes ORDER BY category_id, sort_order")
                        dishes = cur.fetchall()
                        cur.execute("SELECT id, name, icon FROM categories ORDER BY sort_order, id")
                        cats = cur.fetchall()
                    self._send(200, {"categories": cats, "dishes": dishes})
                finally:
                    conn.close()
            else:
                self._send(404, {"error": "not found"})
        except Exception as e:
            self._send(500, {"error": str(e)})

    def _read_body(self):
        """读取请求体：支持 Content-Length 与 Transfer-Encoding: chunked（浏览器传 File 对象时常用后者）。"""
        te = (self.headers.get("Transfer-Encoding") or "").lower()
        if "chunked" in te:
            data = b""
            while True:
                line = self.rfile.readline(65536)
                if not line:
                    break
                try:
                    n = int(line.strip().split(b";")[0], 16)
                except ValueError:
                    break
                if n == 0:
                    while True:  # 吞掉 trailer 直至空行
                        t = self.rfile.readline(65536)
                        if t in (b"\r\n", b"\n", b""):
                            break
                    break
                data += self.rfile.read(n)
                self.rfile.read(2)
                if len(data) > 21 * 1024 * 1024:
                    break
            return data
        length = int(self.headers.get("Content-Length", 0) or 0)
        return self.rfile.read(length) if length else b""

    def do_POST(self):
        path = urlparse(self.path).path
        query = urlparse(self.path).query
        raw = self._read_body() or b"{}"
        try:
            data = json.loads(raw)
        except Exception:
            data = {}
        try:
            # —— 生单（无需鉴权）——
            if path == "/api/order":
                who = str(data.get("who") or "").strip()
                if len(who) > 64:
                    who = who[:64]
                oid, order_items, total = place_order(data.get("items", {}), data.get("note", ""), who)
                notify_order(oid, order_items, data.get("note", ""), who)
                self._send(200, {"ok": True, "order_id": oid, "total": total,
                                 "items_count": sum(q for _, _, q, _ in order_items)})

            # —— 管理端（需 token）——
            elif path == "/api/admin/login":
                # 免密模式：厨房后台不再设登录门槛，任何人调用即签发 token（family 自用）
                tok = secrets.token_hex(16)
                with _lock:
                    _tokens.add(tok)
                self._send(200, {"ok": True, "token": tok})

            elif path == "/api/admin/logout":
                auth = self.headers.get("Authorization", "") or ""
                if auth.startswith("Bearer "):
                    with _lock:
                        _tokens.discard(auth[7:])
                self._send(200, {"ok": True})

            elif path == "/api/admin/dish/upload":
                if not require_admin(self.headers, query):
                    self._send(401, {"error": "auth required"}); return
                ctype0 = self.headers.get("Content-Type", "")
                if "multipart/form-data" not in ctype0:
                    self._send(400, {"error": "需要 multipart 表单"}); return
                if len(raw) > 20 * 1024 * 1024:
                    self._send(413, {"error": "图片过大(>20MB)"}); return
                import io as _io

                def _extract_file(ct, body):
                    """字节层拆 multipart/form-data，返回首个带 filename 部分的原始 bytes（二进制安全，不用 email 库避免字节篡改）。"""
                    m = _re_multipart.search(r'boundary=("?)([^";]+)\1', ct or "")
                    if not m:
                        return None
                    delim = b"--" + m.group(2).encode("latin-1")
                    for seg in body.split(delim):
                        if seg.startswith(b"\r\n"):
                            seg = seg[2:]
                        if seg.endswith(b"\r\n"):
                            seg = seg[:-2]
                        if not seg or seg.startswith(b"--"):
                            continue
                        head, _, data = seg.partition(b"\r\n\r\n")
                        h = head.decode("latin-1", "replace")
                        if "filename=" in h:
                            return data
                    return None

                fdata = _extract_file(ctype0, raw) or b""
                if not fdata:
                    self._send(400, {"error": "未找到文件"}); return
                if len(fdata) < 32:
                    self._send(400, {"error": "空文件"}); return
                try:
                    from PIL import Image, ImageOps
                    im = Image.open(_io.BytesIO(fdata))
                    im.load()
                    fmt = (im.format or "").upper()
                    if fmt not in ("JPEG", "PNG", "WEBP", "GIF", "BMP"):
                        self._send(400, {"error": "不支持的图片格式: " + fmt}); return
                    im = ImageOps.exif_transpose(im)
                    if im.mode not in ("L", "RGB"):
                        bg = Image.new("RGB", im.size, (255, 255, 255))
                        im2 = im.convert("RGBA")
                        bg.paste(im2, mask=im2.split()[-1])
                        im = bg
                    im.thumbnail((1600, 1600))
                    up_dir = os.path.join(BASE_DIR, "img", "uploads")
                    os.makedirs(up_dir, exist_ok=True)
                    fname = secrets.token_hex(8) + ".jpg"
                    im.save(os.path.join(up_dir, fname), "JPEG", quality=82, optimize=True)
                    self._send(200, {"ok": True, "url": "/img/uploads/" + fname})
                except Exception as ue:
                    self._send(400, {"error": "图片处理失败: " + str(ue)})

            elif path == "/api/admin/dish/add":
                if not require_admin(self.headers, query):
                    self._send(401, {"error": "auth required"}); return
                conn = db_conn()
                try:
                    with conn.cursor() as cur:
                        cur.execute(
                            """INSERT INTO dishes (category_id, name, emoji, image, description, tags, price, sort_order)
                               VALUES (%s,%s,%s,%s,%s,%s,0, COALESCE((SELECT MAX(sort_order)+1 FROM (SELECT sort_order FROM dishes WHERE category_id=%s) t),1))""",
                            (data.get("category_id", 1), data.get("name", "").strip(),
                             data.get("emoji", "🍳"), data.get("image", ""),
                             data.get("description", ""), data.get("tags", ""), data.get("category_id", 1)))
                        nid = cur.lastrowid
                    # 若无图片，自动生成占位图
                    if not data.get("image"):
                        emoji = data.get("emoji", "🍳") or "🍳"
                        gen_single_image(nid, emoji)
                        conn.ping(reconnect=True)
                        with conn.cursor() as cur:
                            cur.execute("UPDATE dishes SET image=%s WHERE id=%s", (f"/img/dish_{nid}.svg", nid))
                    self._send(200, {"ok": True, "id": nid})
                finally:
                    conn.close()

            elif path == "/api/admin/dish/update":
                if not require_admin(self.headers, query):
                    self._send(401, {"error": "auth required"}); return
                did = data.get("id")
                if not did:
                    self._send(400, {"error": "缺少 id"}); return
                conn = db_conn()
                try:
                    with conn.cursor() as cur:
                        cur.execute("SELECT image FROM dishes WHERE id=%s", (did,))
                        old_img = (cur.fetchone() or {}).get("image") or ""
                        cur.execute(
                            """UPDATE dishes SET name=%s, description=%s, tags=%s, category_id=%s, emoji=%s, image=%s
                               WHERE id=%s""",
                            (data.get("name"), data.get("description", ""), data.get("tags", ""),
                             data.get("category_id"), data.get("emoji", "🍳"), data.get("image", ""), did))

                        new_img = data.get("image", "")
                        if old_img and old_img.startswith("/img/uploads/") and old_img != new_img:
                            _op = os.path.realpath(os.path.join(BASE_DIR, old_img.lstrip("/")))
                            _or = os.path.realpath(os.path.join(BASE_DIR, "img"))
                            if _op.startswith(_or + os.sep) and os.path.isfile(_op):
                                try:
                                    os.remove(_op)
                                except OSError:
                                    pass
                        self._send(200, {"ok": True})
                finally:
                    conn.close()

            elif path == "/api/admin/dish/delete":
                if not require_admin(self.headers, query):
                    self._send(401, {"error": "auth required"}); return
                did = data.get("id")
                if not did:
                    self._send(400, {"error": "缺少 id"}); return
                conn = db_conn()
                try:
                    with conn.cursor() as cur:
                        cur.execute("SELECT image FROM dishes WHERE id=%s", (did,))
                        _row = cur.fetchone() or {}
                        cur.execute("DELETE FROM dishes WHERE id=%s", (did,))
                    img = os.path.join(BASE_DIR, "img", f"dish_{did}.svg")
                    if os.path.exists(img):
                        os.remove(img)
                    _u = _row.get("image") or ""
                    if _u.startswith("/img/uploads/"):
                        _up = os.path.realpath(os.path.join(BASE_DIR, _u.lstrip("/")))
                        _ud = os.path.realpath(os.path.join(BASE_DIR, "img", "uploads"))
                        if _up.startswith(_ud + os.sep) and os.path.isfile(_up):
                            try:
                                os.remove(_up)
                            except OSError:
                                pass
                    self._send(200, {"ok": True})
                finally:
                    conn.close()

            elif path == "/api/admin/category/add":
                if not require_admin(self.headers, query):
                    self._send(401, {"error": "auth required"}); return
                conn = db_conn()
                try:
                    with conn.cursor() as cur:
                        cur.execute("INSERT INTO categories (name, icon, sort_order) VALUES (%s,%s,%s)",
                                    (data.get("name", "").strip(), data.get("icon", "🍽️"), 99))
                    self._send(200, {"ok": True, "id": cur.lastrowid})
                finally:
                    conn.close()

            elif path == "/api/admin/email/config":
                if not require_admin(self.headers, query):
                    self._send(401, {"error": "auth required"}); return
                auth_code = (data.get("auth_code") or "").strip()
                recipient = (data.get("recipient") or "").strip()
                if not auth_code or not recipient:
                    self._send(400, {"error": "缺少授权码或收件邮箱"}); return
                CONF["email"]["auth_code"] = auth_code
                CONF["email"]["recipient"] = recipient
                CONF["email"]["sender"] = recipient  # 用收件邮箱作发件人
                CONF["email"]["enabled"] = True
                with open(ADMIN_CFG, "w", encoding="utf-8") as f:
                    json.dump(CONF, f, ensure_ascii=False, indent=2)
                self._send(200, {"ok": True})

            elif path == "/api/admin/email/test":
                if not require_admin(self.headers, query):
                    self._send(401, {"error": "auth required"}); return
                ok = send_email("✅ 点菜单邮件通知测试", "这是一封测试邮件。如果收到，说明订单通知配置成功！\n\n—— 你的点菜单")
                self._send(200, {"ok": ok, "enabled": CONF.get("email", {}).get("enabled", False)})

            elif path.startswith("/api/orders/") and path.endswith("/status"):
                if not require_admin(self.headers, query):
                    self._send(401, {"error": "auth required"}); return
                oid = path.split("/")[3]
                new_status = data.get("status")
                conn = db_conn()
                try:
                    with conn.cursor() as cur:
                        cur.execute("UPDATE orders SET status=%s WHERE id=%s", (new_status, oid))
                    self._send(200, {"ok": True})
                finally:
                    conn.close()
            else:
                self._send(404, {"error": "not found"})
        except Exception as e:
            self._send(500, {"error": str(e)})

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print(f"✅ 菜单服务已启动 (MySQL)")
    print(f"   本机:   http://localhost:{PORT}/")
    print(f"   局域网: http://<服务器IP>:{PORT}/  (需放行 {PORT} 端口)")
    print(f"   API:   GET /api/menu | POST /api/order | GET /api/orders")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass

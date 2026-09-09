#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Family Portal —— 家人/访客安全门户(与管理员 svcctrl 控制台完全隔离)
功能:
  - 注册: 访客提交用户名/密码 → 发含一次性token的确认邮件到管理员QQ邮箱
  - 确认: 管理员点邮件链接 → 打开确认页 → 点"激活该账号"后账号才可登录
  - 登录: 已激活账号登录(每账号个人会话)
  - 云盘: 每账号只能访问 /data/family_root/<username>/ 下的个人文件(上传/下载/列表/删除)
  - 点餐: 提供 /menu/ 入口跳转 menu_app
安全: 访客无任何服务启停/SSH/全盘权限; 密码bcrypt哈希; token一次性。
"""
import os, json, ssl, smtplib, secrets, hashlib, hmac
from functools import wraps
from email.mime.text import MIMEText
from email.header import Header
from datetime import datetime, timedelta
import urllib.parse
import pymysql
from flask import (Flask, request, session, redirect, url_for, render_template,
                   jsonify, send_from_directory, abort, flash)

BASE = os.path.dirname(os.path.abspath(__file__))
CONF = json.load(open("/root/menu_app/admin_config.json", encoding="utf-8")).get("email", {})
DB = dict(host="localhost", user="menu_user", password="YOUR_FAMILY_DB_PASSWORD",
          database="family_portal", port=3306, charset="utf8mb4")
FAMILY_ROOT = "/data/family_root"
ADMIN_USERNAME = "admin"          # 预留: 管理员用户名
PUBLIC_CONFIRM_HOST = "https://YOUR_SERVER_IP"   # 确认链接的公开地址(经nginx)

app = Flask(__name__)

# 会话密钥持久化：重启不再踢人；密钥文件 root600
_KEY_FILE = os.path.join(BASE, "session.key")

def _load_or_make_key():
    try:
        if os.path.exists(_KEY_FILE):
            k = open(_KEY_FILE).read().strip()
            if len(k) == 64:
                return k
    except Exception:
        pass
    k = secrets.token_hex(32)
    with open(_KEY_FILE, "w") as f:
        f.write(k)
    os.chmod(_KEY_FILE, 0o600)
    return k

app.secret_key = _load_or_make_key()
# cookie 独立命名 + 根路径：与 svcctrl 的 session cookie 在同一浏览器共存互不顶掉
app.config.update(SESSION_COOKIE_NAME="fam_session",
                  SESSION_COOKIE_PATH="/",
                  SESSION_COOKIE_SAMESITE="Lax")

# 统一登录入口用的 JSON 接口限流（仅记失败，内存级）
_api_fails = {}

def _api_locked(ip):
    import time as _t
    cnt, first = _api_fails.get(ip, (0, 0))
    if _t.time() - first > 300:
        _api_fails.pop(ip, None)
        return 0
    return cnt

def _api_fail(ip):
    import time as _t
    cnt, first = _api_fails.get(ip, (0, _t.time()))
    _api_fails[ip] = (cnt + 1, first)

def _api_ok_reset(ip):
    _api_fails.pop(ip, None)


def db():
    return pymysql.connect(**DB)


def send_email(subject, body):
    try:
        msg = MIMEText(body, "plain", "utf-8")
        msg["Subject"] = Header(subject, "utf-8")
        msg["From"] = CONF["sender"]
        msg["To"] = CONF["recipient"]
        ctx = ssl.create_default_context()
        with smtplib.SMTP_SSL(CONF["smtp_host"], int(CONF["smtp_port"]), timeout=20, context=ctx) as s:
            s.login(CONF["sender"], CONF["auth_code"])
            s.send_message(msg)
        print(f"[email] sent '{subject}'")
        return True
    except Exception as e:
        print(f"[email] FAILED: {e}")
        return False


def hash_pw(pw, salt=None):
    salt = salt or secrets.token_hex(8)
    d = hashlib.pbkdf2_hmac("sha256", pw.encode(), salt.encode(), 120000).hex()
    return f"{salt}${d}"


def verify_pw(pw, stored):
    try:
        salt, d = stored.split("$", 1)
        return hmac.compare_digest(hash_pw(pw, salt).split("$", 1)[1], d)
    except Exception:
        return False


def get_user(username):
    with db() as c:
        cur = c.cursor()
        cur.execute("SELECT username,password_hash,role,status,created_at FROM users WHERE username=%s", (username,))
        r = cur.fetchone()
        if not r:
            return None
        return dict(username=r[0], password_hash=r[1], role=r[2], status=r[3], created_at=r[4])


def login_required(f):
    @wraps(f)
    def w(*a, **k):
        u = session.get("user")
        if not u:
            return redirect(url_for("login", next=request.path))
        return f(*a, **k)
    return w


def safe_name(name):
    return "".join(ch for ch in (name or "").strip() if (ch.isalnum() or ch in "_-."))[:64]


@app.route("/family/")
def home():
    if session.get("user"):
        return redirect(url_for("dashboard"))
    return render_template("index.html")


@app.route("/family/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = safe_name(request.form.get("username", ""))
        pw = request.form.get("password", "")
        user = get_user(username)
        if not user:
            flash("账号不存在或未激活", "error"); return redirect(url_for("login"))
        if user["status"] != "active":
            flash("该账号尚未激活(需管理员邮箱确认)", "error"); return redirect(url_for("login"))
        if not verify_pw(pw, user["password_hash"]):
            flash("密码错误", "error"); return redirect(url_for("login"))
        session["user"] = user["username"]; session["role"] = user["role"]
        nxt = request.args.get("next") or url_for("dashboard")
        if not nxt.startswith("/"): nxt = url_for("dashboard")
        return redirect(nxt)
    return render_template("login.html")


@app.route("/family/api_login", methods=["POST"])
def api_login():
    """统一登录入口(svcctrl 登录页)调用的 JSON 通道；浏览器仍在 443 根路径，cookie 同域共享。"""
    data = request.get_json(silent=True) or {}
    username = safe_name(data.get("username", ""))
    pw = data.get("password", "")
    ip = request.headers.get("X-Real-IP") or request.remote_addr or "?"
    if _api_locked(ip) >= 8:
        return jsonify({"ok": False, "code": "locked", "error": "失败次数过多，5 分钟后再试"}), 429
    if not username or not pw:
        return jsonify({"ok": False, "code": "no_user", "error": "请输入用户名和密码"})
    user = get_user(username)
    if not user or user["status"] != "active":
        _api_fail(ip)
        return jsonify({"ok": False, "code": "no_user", "error": "账号或密码错误"})
    if not verify_pw(pw, user["password_hash"]):
        _api_fail(ip)
        return jsonify({"ok": False, "code": "bad_pw", "error": "账号或密码错误"})
    _api_ok_reset(ip)
    session["user"] = user["username"]
    session["role"] = user["role"]
    return jsonify({"ok": True, "redirect": url_for("dashboard")})


@app.route("/family/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        username = safe_name(request.form.get("username", ""))
        pw = request.form.get("password", "")
        pw2 = request.form.get("password2", "")
        email_notify = request.form.get("email", "")
        if len(username) < 3 or len(pw) < 6:
            flash("用户名至少3字符、密码至少6位", "error"); return redirect(url_for("register"))
        if pw != pw2:
            flash("两次密码不一致", "error"); return redirect(url_for("register"))
        if get_user(username):
            flash("用户名已存在", "error"); return redirect(url_for("register"))
        token = secrets.token_urlsafe(24)
        pwh = hash_pw(pw)
        with db() as c:
            cur = c.cursor()
            cur.execute("INSERT INTO users(username,password_hash,role,email,status,confirm_token) VALUES(%s,%s,'member',%s,'pending',%s)",
                        (username, pwh, email_notify, token))
            c.commit()
        os.makedirs(os.path.join(FAMILY_ROOT, username), exist_ok=True)
        link = f"{PUBLIC_CONFIRM_HOST}/family/confirm/{token}"
        body = (f"有一个新账号申请注册：\n\n用户名: {username}\n邮箱: {email_notify or '(未填)'}\n\n"
                f"请点击以下链接确认是否开通：\n{link}\n\n若不是你本人操作请忽略。")
        send_email(f"👤 访客注册待确认: {username}", body)
        flash("注册申请已提交，请等待管理员邮箱确认后即可登录", "ok")
        return redirect(url_for("login"))
    return render_template("register.html")


@app.route("/family/confirm/<token>")
def confirm(token):
    """管理员点邮件链接打开的确认页(显示待激活账号, 需再次点激活)"""
    with db() as c:
        cur = c.cursor()
        cur.execute("SELECT username,email,created_at FROM users WHERE confirm_token=%s AND status='pending'", (token,))
        r = cur.fetchone()
    if not r:
        code = "INVALID"
        info = None
    else:
        code = "VALID"
        info = dict(username=r[0], email=r[1], created_at=r[2])
    return render_template("confirm.html", code=code, info=info, token=token)


@app.route("/family/confirm/<token>/activate", methods=["POST"])
def activate(token):
    with db() as c:
        cur = c.cursor()
        cur.execute("SELECT username FROM users WHERE confirm_token=%s AND status='pending'", (token,))
        row = cur.fetchone()
        if not row:
            flash("无效或已处理的确认链接", "error")
            return redirect(url_for("home"))
        uname = row[0]
        cur.execute("UPDATE users SET status='active', confirm_token=NULL WHERE confirm_token=%s", (token,))
        c.commit()
        # 激活成功: 直接显示“已批准”确认页(不再跳转登录)
        return render_template("approved.html", username=uname)


@app.route("/family/logout")
def logout():
    session.clear()
    return redirect(url_for("home"))


@app.route("/family/dashboard")
@login_required
def dashboard():
    return render_template("dashboard.html", user=session["user"], role=session["role"])


@app.route("/family/drive/")
@login_required
def drive():
    root = os.path.join(FAMILY_ROOT, safe_name(session["user"]))
    os.makedirs(root, exist_ok=True)
    return render_template("drive.html", user=session["user"])


@app.route("/family/drive/list")
@login_required
def drive_list():
    root = os.path.join(FAMILY_ROOT, safe_name(session["user"]))
    os.makedirs(root, exist_ok=True)
    items = []
    for name in sorted(os.listdir(root)):
        p = os.path.join(root, name)
        if os.path.isfile(p):
            st = os.stat(p)
            items.append(dict(name=name, kind="file", size=st.st_size,
                              mtime=datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M")))
        elif os.path.isdir(p):
            items.append(dict(name=name, kind="dir", size="-", mtime="-"))
    return jsonify(items)


@app.route("/family/drive/upload", methods=["POST"])
@login_required
def drive_upload():
    root = os.path.join(FAMILY_ROOT, safe_name(session["user"]))
    f = request.files.get("file")
    if not f or f.filename == "":
        return jsonify({"ok": False, "error": "未选择文件"})
    name = os.path.basename(f.filename)
    dest = os.path.join(root, name)
    f.save(dest)
    return jsonify({"ok": True})


@app.route("/family/drive/download/<path:name>")
@login_required
def drive_download(name):
    root = os.path.join(FAMILY_ROOT, safe_name(session["user"]))
    # 防目录穿越
    safe = os.path.basename(name)
    return send_from_directory(root, safe, as_attachment=True)


@app.route("/family/drive/delete/<path:name>", methods=["POST"])
@login_required
def drive_delete(name):
    root = os.path.join(FAMILY_ROOT, safe_name(session["user"]))
    safe = os.path.basename(name)
    p = os.path.join(root, safe)
    if os.path.isfile(p):
        os.remove(p)
        return jsonify({"ok": True})
    return jsonify({"ok": False, "error": "仅支持删除文件"})


@app.route("/family/menu")
@login_required
# 带登录用户名跳去点餐，供下单署名(?diner=)；menu 侧会记住并用于“谁点的”
def menu():
    who = session.get("user") or ""
    return redirect("https://YOUR_SERVER_IP/menu/?diner=" + urllib.parse.quote(who))


# 账号 -> 独立远端桌面(noVNC 经 nginx /family/desktop 反代到本机端口)。新增家人在此登记即可
# 每个值的 wk 为 websockify 监听端口(仅 127.0.0.1)。
DESKTOPS = {
    "dyh": {"name": "dyh", "wk": 6081},
}


@app.route("/family/_authcheck")
# nginx auth_request 用：family 会话有效才 200
# 供 /family/desktop 下 noVNC 静态与 ws 的鉴权
def _authcheck():
    if session.get("user"):
        return ("ok", 200)
    return ("no", 401)


@app.route("/family/desktop")
@login_required
def desktop():
    """当前登录用户的桌面入口：跳转到同域 noVNC 客户端(/family/desktop/vnc.html?autoconnect=1&resize=remote&scale=true&path=websockify)"""
    # noVNC 客户端与 ws 均由 nginx /family/desk/ 反代到本机 websockify, 并受 auth_request 保护
    return redirect("/family/desk/vnc.html?autoconnect=1&resize=remote&scale=true&path=/family/desk/websockify")




if __name__ == "__main__":
    app.run(host="127.0.0.1", port=8092, debug=False)

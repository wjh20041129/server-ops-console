#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
svcctrl — 轻量服务控制台（独立进程，不依赖宝塔）
监控 + 启停服务器的自建服务 / MySQL / 开发工具。

独立运行(端口8750)，与 exam-system(5000) 完全分离，
这样在控制台里停掉 exam 或 MySQL 都不会让控制台自身掉线。
"""
import os
import json
import time
import hashlib
import hmac
import secrets
import subprocess
import platform
import psutil
from datetime import datetime

from flask import (Flask, request, session, jsonify, render_template,
                   abort, redirect, url_for)

BASE = os.path.dirname(os.path.abspath(__file__))
AUTH_FILE = os.path.join(BASE, 'auth.json')
# 允许 API 免登录上报自身状态的服务白名单(见下)，控制台核心进程只读不控
PORT = int(os.environ.get('SVCCTRL_PORT', '8750'))

app = Flask(__name__)
# atop 历史负载查询（历史时刻表盘）
from atop_api import atop_bp
app.register_blueprint(atop_bp)

# IP 历史落库 + 高风险判定（独立模块，含 SQLite/采集/规则）
import ip_history
# 会话密钥：持久化到 session.key(root 600)，使登录态跨服务重启存活
# （以前随机密钥导致每次发版重启，所有在线用户被踢回登录页，表现为"莫名断连"）
_KEY_FILE = os.path.join(BASE, 'session.key')


def _load_or_make_key():
    try:
        if os.path.exists(_KEY_FILE):
            k = open(_KEY_FILE).read().strip()
            if len(k) == 64:
                return k
    except Exception:
        pass
    k = secrets.token_hex(32)
    with open(_KEY_FILE, 'w') as f:
        f.write(k)
    os.chmod(_KEY_FILE, 0o600)
    return k


app.secret_key = _load_or_make_key()
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'


# ---------------------------------------------------------------------------
# 认证：首次启动自动生成随机密码并写 auth.json；之后按密码登录
# ---------------------------------------------------------------------------
def _hash_password(pwd: str) -> str:
    return hashlib.sha256(pwd.encode('utf-8')).hexdigest()


def ensure_auth():
    if not os.path.exists(AUTH_FILE):
        new_pass = secrets.token_urlsafe(9)  # 随机密码
        data = {'password_hash': _hash_password(new_pass),
                'created': datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
        with open(AUTH_FILE, 'w') as f:
            json.dump(data, f)
        os.chmod(AUTH_FILE, 0o600)
        # 打印初始密码到日志/控制台
        print('\n' + '=' * 54)
        print('  svcctrl 首次启动，已生成随机登录密码：')
        print('  >>> ' + new_pass + ' <<<')
        print('  (已加密存入 ' + AUTH_FILE + '，可用 svcctrl changepass 修改)')
        print('=' * 54 + '\n', flush=True)
        return True
    return False


def _atomic_save_auth(data):
    tmp = AUTH_FILE + '.tmp'
    with open(tmp, 'w') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, AUTH_FILE)
    os.chmod(AUTH_FILE, 0o600)


def _migrate_auth(data):
    """旧单账号格式 {username,password_hash} → 新 {users:{name:{password_hash,role,created}}}。
    返回 (data, changed)。"""
    if 'users' in data:
        return data, False
    users = {}
    name = data.get('username') or 'admin'
    users[name] = {'password_hash': data.get('password_hash', ''),
                   'role': 'owner',
                   'created': data.get('created') or data.get('changed') or ''}
    out = {'users': users,
           'created': data.get('created', ''),
           'changed': data.get('changed', '')}
    return out, True


def check_password(pwd: str, username: str = '') -> bool:
    """校验密码。给了 username 则校验该用户；否则遍历(兼容旧调用)。"""
    data = get_auth_data()
    users = data.get('users', {})
    h = _hash_password(pwd)
    if username:
        u = users.get(username)
        return bool(u) and hmac.compare_digest(h, u.get('password_hash', ''))
    for u in users.values():
        if hmac.compare_digest(h, u.get('password_hash', '')):
            return True
    return False


def find_user_by_password(pwd: str):
    """兼容无用户名时代的调用：按密码找用户，返回 (username, u) 或 (None, None)。"""
    data = get_auth_data()
    h = _hash_password(pwd)
    for name, u in data.get('users', {}).items():
        if hmac.compare_digest(h, u.get('password_hash', '')):
            return name, u
    return None, None


def get_auth_data():
    try:
        with open(AUTH_FILE) as f:
            data = json.load(f)
        data, changed = _migrate_auth(data)
        if changed:
            _atomic_save_auth(data)
        return data
    except Exception:
        return {}


# ---- 登录试错锁定：同一 IP 连错 3 次 → 锁定 1 小时 ----
LOCK_FILE = os.path.join(BASE, 'lock.json')
MAX_ATTEMPTS = 3
LOCK_SECONDS = 3600  # 1 小时
_lock = None  # 内存态：{ip: {'fails':n, 'locked_until':ts}}


def _load_lock():
    global _lock
    try:
        if os.path.exists(LOCK_FILE):
            _lock = json.load(open(LOCK_FILE))
        else:
            _lock = {}
    except Exception:
        _lock = {}


def _save_lock():
    try:
        # 只保留仍在锁定期/近期失败的数据
        prune = {}
        now = time.time()
        for ip, s in _lock.items():
            if s.get('locked_until', 0) > now or now - s.get('last_fail', 0) < 86400:
                prune[ip] = s
        with open(LOCK_FILE, 'w') as f:
            json.dump(prune, f)
    except Exception:
        pass


_load_lock()


def lock_remaining(ip) -> int:
    """返回当前剩余锁定秒数；0=未锁定。"""
    s = (_lock or {}).get(ip, {})
    locked_until = s.get('locked_until', 0)
    remain = locked_until - time.time()
    return int(remain) if remain > 0 else 0


def record_fail(ip):
    """记录一次失败；若达到阈值则设置锁定并从0计。"""
    s = _lock.setdefault(ip, {'fails': 0, 'locked_until': 0, 'last_fail': 0})
    # 若正在锁定期内则不动
    if lock_remaining(ip) > 0:
        return
    s['fails'] = s.get('fails', 0) + 1
    s['last_fail'] = time.time()
    if s['fails'] >= MAX_ATTEMPTS:
        s['locked_until'] = time.time() + LOCK_SECONDS
        s['fails'] = 0
    _save_lock()


def attempts_left(ip) -> int:
    return max(0, MAX_ATTEMPTS - (_lock or {}).get(ip, {}).get('fails', 0))


def reset_fails(ip):
    if ip in (_lock or {}):
        _lock.pop(ip, None)
        _save_lock()


def require_login():
    if not session.get('authed'):
        return False
    return True


# ---------------------------------------------------------------------------
# 服务清单 —— 你在面板里看到并能控制的“自建服务”
#   kind: systemd          -> 用 systemctl 启停 / enable
#   kind: proc_monitor     -> 非 systemd 独立进程，仅监控(禁止面板停止以免自断会话)
# ---------------------------------------------------------------------------
SERVICES = [
    {"id": "exam",     "name": "随身笔记系统", "desc": "公考+四级+仓库笔记 (5000)",
     "unit": "exam-system.service", "kind": "systemd"},
    {"id": "family",   "name": "家人门户", "desc": "注册审批/云盘/家庭入口 (8092)",
     "unit": "family-portal.service", "kind": "systemd", "warn": "全站 /family/ 入口依赖它"},
    {"id": "dyhdesk",  "name": "dyh 专属桌面", "desc": "vnc-dyh + novnc-dyh (6081)",
     "unit": "novnc-dyh.service", "extra_units": ["vnc-dyh.service"], "kind": "systemd"},
    {"id": "mysql",    "name": "MySQL 数据库", "desc": "数据存储 · 本控制台会展示详细状态",
     "unit": "mysql.service", "kind": "systemd", "special": "mysql"},
    {"id": "nginx",    "name": "Nginx 反代/HTTPS", "desc": "对外入口 80/443",
     "unit": "nginx.service", "kind": "systemd"},
    {"id": "fail2ban", "name": "Fail2Ban 防护", "desc": "面板登录防爆破 · 拉黑联动",
     "unit": "fail2ban.service", "kind": "systemd", "readonly": True},
    {"id": "cet4",     "name": "CET4 词义预加载", "desc": "四级单词例句/助记后台生成（空闲时停用属正常）",
     "unit": "cet4-preload.service", "kind": "systemd", "enable_with_start": True},
    {"id": "codeserver", "name": "code-server", "desc": "VS Code 网页版(8080) · 约170MB",
     "unit": "code-server.service", "kind": "systemd"},
    {"id": "vnc",      "name": "VNC 图形桌面", "desc": "XFCE 远程桌面(5901)",
     "unit": "vncserver@1.service", "kind": "systemd"},
    {"id": "novnc",    "name": "noVNC (网页桌面)", "desc": "6080 → VNC",
     "unit": "novnc.service", "kind": "systemd"},
    {"id": "filebrowser", "name": "filebrowser", "desc": "网页文件管理(8088)",
     "unit": "filebrowser.service", "kind": "systemd"},
    {"id": "docker",   "name": "Docker 引擎", "desc": "容器运行时 · 停止前请确认无在用容器",
     "unit": "docker.service", "kind": "systemd"},
    {"id": "ttyd",     "name": "Web SSH 终端", "desc": "浏览器命令行 · https://host/ssh/",
     "unit": "ttyd.service", "kind": "systemd"},
    {"id": "menu",     "name": "点餐系统", "desc": "family 点餐 · https://host/menu/",
     "unit": "menu-app.service", "kind": "systemd"},
    # ---- 以下独立进程：仅监控，不开“停止”以安全起见 ----
    {"id": "openclaw", "name": "OpenClaw 网关", "desc": "Control UI(18789) · WebChat 入口 · 重启请在 SSH 终端执行 openclaw gateway restart",
     "unit": None, "kind": "proc_monitor", "procs": ["openclaw/dist/index.js gateway"]},
]

# 面板里点击“停止/重启”前需要二次确认的危险操作仍走系统确认，这里标记不可控的 kind
CONTROLLABLE = {'systemd': True, 'proc_monitor': False}


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------
def run(cmd, timeout=10):
    """执行命令，返回 (code, stdout, stderr)"""
    try:
        p = subprocess.run(cmd, shell=True, capture_output=True,
                           text=True, timeout=timeout)
        return p.returncode, p.stdout.strip(), p.stderr.strip()
    except subprocess.TimeoutExpired:
        return -1, '', 'timeout'
    except Exception as e:
        return -1, '', str(e)


def now_client_ip():
    return request.headers.get('X-Real-IP') or request.remote_addr


# ---------------------------------------------------------------------------
# 页面路由
# ---------------------------------------------------------------------------
@app.after_request
def _no_store_html(resp):
    """控制台 HTML 一律禁缓存：Safari 对无 Cache-Control 的页面做启发式缓存，
    导致发版后用户看到旧页面（2026-09-07 实锤）。静态资源不受影响。"""
    if resp.mimetype == 'text/html':
        resp.headers['Cache-Control'] = 'no-store'
    return resp


@app.route('/')
def index():
    return render_template('index.html')


@app.route('/login')
def login_page():
    if session.get('authed'):
        return redirect('/')
    return render_template('login.html')


@app.route('/api/login', methods=['POST'])
def api_login():
    data = request.get_json(silent=True) or {}
    username = (data.get('username') or '').strip()
    password = data.get('password', '')
    ip = now_client_ip()

    # 1) 锁定检查
    remain = lock_remaining(ip)
    if remain > 0:
        return jsonify({'success': False, 'locked': True,
                        'remain': remain,
                        'error': '尝试次数过多，已锁定 ' + str(int(remain // 60)) +
                                 ' 分钟 ' + str(int(remain % 60)) + ' 秒。请稍后再试。'})

    # 2) 校验用户名+密码 (用户名用普通比较——支持中文; 密码用恒定时间比较)
    ad = get_auth_data()
    u = ad.get('users', {}).get(username)
    pass_ok = bool(u) and hmac.compare_digest(_hash_password(password),
                                              u.get('password_hash', ''))
    if username and pass_ok:
        reset_fails(ip)
        session.clear()
        session['authed'] = True
        session['user'] = username
        session['role'] = u.get('role', 'member')
        return jsonify({'success': True, 'role': u.get('role', 'member')})

    # 3) 记录失败
    record_fail(ip)
    # 若本次达到阈值已触发锁定
    remain2 = lock_remaining(ip)
    if remain2 > 0:
        return jsonify({'success': False, 'locked': True, 'remain': remain2,
                        'error': ('用户名或密码错误。已达 ' + str(MAX_ATTEMPTS) +
                                  ' 次上限，已锁定 1 小时。')})
    left = attempts_left(ip)
    msg = '用户名或密码错误'
    return jsonify({'success': False, 'locked': False, 'left': left,
                    'error': msg + '，还可尝试 ' + str(left) + ' 次'})


# ---------------------------------------------------------------------------
# 统一登录入口：一个账号框，管理员进控制台，家人进门户
# ---------------------------------------------------------------------------
FAM_LOGIN_URL = 'http://127.0.0.1:8092/family/api_login'


def _fam_api_login(username, password, ip):
    """代理登录家人门户；成功返回 (redirect, cookie_value)，失败返回 (None, code)。"""
    import urllib.request, urllib.error
    import http.cookiejar
    payload = json.dumps({'username': username, 'password': password}).encode()
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(jar))
    req = urllib.request.Request(FAM_LOGIN_URL, data=payload, headers={
        'Content-Type': 'application/json',
        'X-Real-IP': ip or '127.0.0.1',
    })
    try:
        with opener.open(req, timeout=8) as r:
            body = json.loads(r.read().decode('utf-8', 'ignore'))
    except urllib.error.HTTPError as e:
        try:
            body = json.loads(e.read().decode('utf-8', 'ignore'))
        except Exception:
            return None, 'unreachable'
    except Exception:
        return None, 'unreachable'
    if not body.get('ok'):
        return None, body.get('code') or 'bad_pw'
    cookie_val = None
    for c in jar:
        if c.name == 'fam_session':
            cookie_val = c.value
    return (body.get('redirect') or '/family/dashboard'), cookie_val


@app.route('/api/login_unified', methods=['POST'])
def api_login_unified():
    data = request.get_json(silent=True) or {}
    username = (data.get('username') or '').strip()
    password = data.get('password', '')
    ip = now_client_ip()

    # 1) 控制台账号判定（不先报错，避免向探测者暴露账号类型）
    ad = get_auth_data()
    u = ad.get('users', {}).get(username)
    console_ok = bool(u) and hmac.compare_digest(_hash_password(password),
                                                 u.get('password_hash', ''))
    if username and console_ok:
        reset_fails(ip)
        session.clear()
        session['authed'] = True
        session['user'] = username
        session['role'] = u.get('role', 'member')
        return jsonify({'ok': True, 'redirect': '/', 'portal': 'admin'})

    # 2) 控制台不对 → 试家人门户（family 侧自有限流 8次/5分钟）
    fam_redirect, fam_cookie = _fam_api_login(username, password, ip)
    if fam_redirect and fam_cookie:
        resp = jsonify({'ok': True, 'redirect': fam_redirect, 'portal': 'family'})
        # 透传 fam_session 到浏览器（当前域/根路径，与 /family/* 同域直达）
        resp.headers['Set-Cookie'] = (
            'fam_session=' + fam_cookie +
            '; Path=/; HttpOnly; SameSite=Lax')
        return resp

    # 3) 两边都不对
    if fam_cookie == 'unreachable':
        return jsonify({'ok': False, 'error': '家人门户服务不可达，请联系管理员'})
    # 家人账号存在但密码错（bad_pw）：不计控制台失败次数，避免家人误锁管理员；
    # 两边都查无此人（no_user）：计入控制台失败（防探测）
    if fam_cookie == 'no_user':
        record_fail(ip)
        remain2 = lock_remaining(ip)
        if remain2 > 0:
            return jsonify({'ok': False, 'locked': True, 'remain': remain2,
                            'error': '用户名或密码错误。已达上限，已锁定 1 小时。'})
        return jsonify({'ok': False, 'left': attempts_left(ip),
                        'error': '用户名或密码错误'})
    return jsonify({'ok': False, 'error': '用户名或密码错误'})


# nginx auth_request 用：校验当前请求携带的 svcctrl 会话 cookie 是否已登录
@app.route('/svc_auth_check')
def svc_auth_check():
    if session.get('authed'):
        return '', 200
    return '', 401


@app.route('/api/lockstate')
def api_lockstate():
    """暴露当前是否被锁(供登录页提示)。不要求登录。"""
    ip = now_client_ip()
    remain = lock_remaining(ip)
    return jsonify({'locked': remain > 0, 'remain': remain,
                    'left': max(0, MAX_ATTEMPTS - (_lock or {}).get(ip, {}).get('fails', 0))})


@app.route('/api/logout', methods=['POST'])
def api_logout():
    session.clear()
    return jsonify({'success': True})


# 连接心跳：免登录、极轻量，供前端实时显示"链接状态"
@app.route('/api/ping')
def api_ping():
    return jsonify({'ok': True, 'authed': bool(session.get('authed')),
                    'srv': datetime.now().strftime('%H:%M:%S')})


@app.route('/api/session')
def api_session():
    return jsonify({'authed': bool(session.get('authed')),
                    'user': session.get('user', ''),
                    'role': session.get('role', '')})


# ---------------------------------------------------------------------------
# API：用户管理（仅 owner 可操作；owner 账号本身不可删除）
# ---------------------------------------------------------------------------
def _users_guard():
    """返回 (None) 放行，或 (json响应, 码) 拒绝。"""
    if not session.get('authed'):
        return jsonify({'error': '未登录'}), 401
    if session.get('role') != 'owner':
        return jsonify({'error': '仅管理员(owner)可管理用户'}), 403
    return None


@app.route('/api/users')
def api_users():
    deny = _users_guard()
    if deny:
        return deny
    data = get_auth_data()
    out = [{'username': n, 'role': u.get('role', 'member'),
            'created': u.get('created', '')}
           for n, u in data.get('users', {}).items()]
    return jsonify({'users': out})


@app.route('/api/users/add', methods=['POST'])
def api_users_add():
    deny = _users_guard()
    if deny:
        return deny
    body = request.get_json(silent=True) or {}
    name = (body.get('username') or '').strip()
    pwd = body.get('password') or ''
    if not name or len(name) > 32:
        return jsonify({'error': '用户名不能为空且不超过32字符'}), 400
    if any(c in name for c in '\r\n"\\/'):
        return jsonify({'error': '用户名含非法字符'}), 400
    if len(pwd) < 6:
        return jsonify({'error': '密码至少 6 位'}), 400
    data = get_auth_data()
    users = data.setdefault('users', {})
    if name in users:
        return jsonify({'error': '用户已存在'}), 409
    users[name] = {'password_hash': _hash_password(pwd), 'role': 'member',
                   'created': datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
    data['changed'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    _atomic_save_auth(data)
    return jsonify({'success': True, 'username': name})


@app.route('/api/users/remove', methods=['POST'])
def api_users_remove():
    deny = _users_guard()
    if deny:
        return deny
    name = ((request.get_json(silent=True) or {}).get('username') or '').strip()
    data = get_auth_data()
    users = data.get('users', {})
    if name not in users:
        return jsonify({'error': '用户不存在'}), 404
    if users[name].get('role') == 'owner':
        return jsonify({'error': 'owner 管理员账号不可删除'}), 400
    del users[name]
    data['changed'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    _atomic_save_auth(data)
    return jsonify({'success': True, 'removed': name})


@app.route('/api/users/setpassword', methods=['POST'])
def api_users_setpassword():
    deny = _users_guard()
    if deny:
        return deny
    body = request.get_json(silent=True) or {}
    name = (body.get('username') or '').strip()
    pwd = body.get('password') or ''
    if len(pwd) < 6:
        return jsonify({'error': '密码至少 6 位'}), 400
    data = get_auth_data()
    users = data.get('users', {})
    if name not in users:
        return jsonify({'error': '用户不存在'}), 404
    users[name]['password_hash'] = _hash_password(pwd)
    data['changed'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    _atomic_save_auth(data)
    return jsonify({'success': True, 'username': name})


# ---------------------------------------------------------------------------
# API：家庭成员账号管理（family_portal MySQL users 表；仅 owner）
#   密码格式与 family_portal.app.hash_pw 严格一致：salt$pbkdf2_sha256_120000
# ---------------------------------------------------------------------------
FAMILY_DB = dict(host='localhost', user='menu_user', password=os.environ.get('FAMILY_DB_PASSWORD', ''),
                 database='family_portal', charset='utf8mb4')


FAMILY_ROOT = '/data/family_root'
FAMILY_TRASH = '/data/.family_trash'


def _fam_dir_stats(username):
    """返回 (文件数, 字节数)；非法用户名/无目录返回 (0,0)。"""
    import re as _r
    if not username or not _r.fullmatch(r"[\w.\-\u4e00-\u9fff]{1,64}", username):
        return 0, 0
    d = os.path.join(FAMILY_ROOT, username)
    rp = os.path.realpath(d)
    if not rp.startswith(os.path.realpath(FAMILY_ROOT) + os.sep):
        return 0, 0
    if not os.path.isdir(rp):
        return 0, 0
    n = 0; size = 0
    try:
        for root, dirs, files in os.walk(rp):
            for fn in files:
                try:
                    size += os.path.getsize(os.path.join(root, fn))
                    n += 1
                except OSError:
                    pass
            if n > 10000:
                break
    except OSError:
        pass
    return n, size


def _fam_dir_trash(username):
    """云盘目录移入回收站(可恢复)；返回 (是否移动, 路径/原因)。"""
    import shutil
    real_root = os.path.realpath(FAMILY_ROOT)
    if not username or '/' in username or '..' in username:
        return False, '用户名非法，已跳过'
    d = os.path.realpath(os.path.join(real_root, username))
    if not d.startswith(real_root + os.sep) or d == real_root:
        return False, '路径非法，已跳过'
    if not os.path.isdir(d):
        return False, '无云盘目录'
    os.makedirs(FAMILY_TRASH, exist_ok=True)
    dest = os.path.join(FAMILY_TRASH, username + '.' +
                        datetime.now().strftime('%Y%m%d_%H%M%S'))
    shutil.move(d, dest)
    return True, dest


def _fam_conn():
    import pymysql
    import pymysql.cursors
    return pymysql.connect(cursorclass=pymysql.cursors.DictCursor, **FAMILY_DB)


def _fam_hash(pw, salt=None):
    salt = salt or secrets.token_hex(8)
    d = hashlib.pbkdf2_hmac('sha256', pw.encode(), salt.encode(), 120000).hex()
    return salt + '$' + d


def _fam_safe_name(name):
    return ''.join(ch for ch in (name or '').strip()
                   if (ch.isalnum() or ch in '_-.'))[:64]


@app.route('/api/fam/users')
def api_fam_users():
    deny = _users_guard()
    if deny:
        return deny
    try:
        with _fam_conn() as c:
            cur = c.cursor()
            cur.execute('SELECT username,role,status,created_at FROM users ORDER BY id')
            rows = cur.fetchall()
        out = [{'username': r['username'], 'role': r.get('role') or 'member',
                'status': r.get('status') or '',
                'created': str(r.get('created_at') or ''),
                'files': _fam_dir_stats(r['username'])[0]} for r in (rows or [])]
        return jsonify({'users': out})
    except Exception as e:
        return jsonify({'error': '数据库查询失败: ' + str(e)}), 500


@app.route('/api/fam/users/add', methods=['POST'])
def api_fam_users_add():
    deny = _users_guard()
    if deny:
        return deny
    body = request.get_json(silent=True) or {}
    raw = (body.get('username') or '')
    name = _fam_safe_name(raw)
    pwd = body.get('password') or ''
    if not name:
        return jsonify({'error': '用户名不能为空'}), 400
    if name != raw.strip():
        return jsonify({'error': '用户名含非法字符(可用字母/数字/中文/_-.，≤64字)'}), 400
    if len(pwd) < 6:
        return jsonify({'error': '密码至少 6 位'}), 400
    try:
        with _fam_conn() as c:
            cur = c.cursor()
            cur.execute('SELECT id FROM users WHERE username=%s', (name,))
            if cur.fetchone():
                return jsonify({'error': '该用户名已存在'}), 409
            cur.execute("INSERT INTO users(username,password_hash,role,email,status) "
                        "VALUES(%s,%s,'member',NULL,'active')", (name, _fam_hash(pwd)))
            c.commit()
        return jsonify({'success': True, 'username': name})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/fam/users/remove', methods=['POST'])
def api_fam_users_remove():
    deny = _users_guard()
    if deny:
        return deny
    name = ((request.get_json(silent=True) or {}).get('username') or '').strip()
    if not name:
        return jsonify({'error': '缺少用户名'}), 400
    try:
        nfiles, nbytes = _fam_dir_stats(name)
        with _fam_conn() as c:
            cur = c.cursor()
            cur.execute('DELETE FROM users WHERE username=%s', (name,))
            n = cur.rowcount
            cur.execute('DELETE FROM session_tokens WHERE username=%s', (name,))
            c.commit()
        if not n:
            return jsonify({'error': '用户不存在'}), 404
        moved = False; dest = ''
        if nfiles:
            try:
                moved, dest = _fam_dir_trash(name)
            except Exception as e:
                dest = '文件清理失败: ' + str(e)
        return jsonify({'success': True, 'removed': name,
                        'files_removed': (nfiles if moved else 0),
                        'files_total': nfiles, 'trash': dest if moved else ''})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/fam/users/activate', methods=['POST'])
def api_fam_users_activate():
    deny = _users_guard()
    if deny:
        return deny
    name = ((request.get_json(silent=True) or {}).get('username') or '').strip()
    try:
        with _fam_conn() as c:
            cur = c.cursor()
            cur.execute("UPDATE users SET status='active', confirm_token=NULL "
                        "WHERE username=%s AND status='pending'", (name,))
            n = cur.rowcount
            c.commit()
        if not n:
            return jsonify({'error': '该用户不在待审批状态'}), 400
        return jsonify({'success': True, 'activated': name})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/fam/users/setpassword', methods=['POST'])
def api_fam_users_setpassword():
    deny = _users_guard()
    if deny:
        return deny
    body = request.get_json(silent=True) or {}
    name = (body.get('username') or '').strip()
    pwd = body.get('password') or ''
    if len(pwd) < 6:
        return jsonify({'error': '密码至少 6 位'}), 400
    try:
        with _fam_conn() as c:
            cur = c.cursor()
            cur.execute('UPDATE users SET password_hash=%s WHERE username=%s',
                        (_fam_hash(pwd), name))
            if cur.rowcount == 0:
                # rowcount=0 可能密码未变；确认存在性
                cur.execute('SELECT id FROM users WHERE username=%s', (name,))
                if not cur.fetchone():
                    return jsonify({'error': '用户不存在'}), 404
            c.commit()
        return jsonify({'success': True, 'username': name})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ---------------------------------------------------------------------------
# API：系统实时表盘
# ---------------------------------------------------------------------------
@app.route('/api/system')
def api_system():
    """复用 exam 的采集逻辑 + 网络 + 进程数"""
    if not session.get('authed'):
        return jsonify({'error': '未登录'}), 401
    try:
        cpu_percent = psutil.cpu_percent(interval=0.4)
        per_cpu = psutil.cpu_percent(interval=0.0, percpu=True)
        load_avg = os.getloadavg()
        mem = psutil.virtual_memory()
        disk = psutil.disk_usage('/')
        net = psutil.net_io_counters()
        boot = psutil.boot_time()
        up = time.time() - boot
        procs = len(psutil.pids())
        ip = psutil.net_if_addrs()
        return jsonify({
            'cpu': {'percent': cpu_percent, 'per_cpu': per_cpu, 'count': psutil.cpu_count(),
                    'load_avg': list(load_avg)},
            'memory': {'total_gb': round(mem.total / 1e9, 1),
                       'used_gb': round(mem.used / 1e9, 1),
                       'percent': mem.percent},
            'disk': {'total_gb': round(disk.total / 1e9, 1),
                     'used_gb': round(disk.used / 1e9, 1),
                     'percent': disk.percent},
            'net': {'sent_mb': round(net.bytes_sent / 1e6, 1),
                    'recv_mb': round(net.bytes_recv / 1e6, 1)},
            'uptime': {'days': int(up // 86400), 'hours': int((up % 86400) // 3600),
                       'minutes': int((up % 3600) // 60)},
            'processes': procs,
            'platform': platform.platform(),
            'time': datetime.now().strftime('%H:%M:%S'),
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ---------------------------------------------------------------------------
# API：MySQL 详细状态
# ---------------------------------------------------------------------------
DB_CFG_FILE = '/root/svcctrl/db.json'  # 可选：若与 exam 密码不同，可在此覆盖


def _mysql_creds():
    """MySQL 连接凭据。默认 root/12345678（与 exam 项目一致，密码明文本地保存）。
    可通过 /root/svcctrl/db.json 覆盖：{"host":..,"user":..,"password":..}。"""
    cfg = {'host': '127.0.0.1', 'user': 'root', 'password': os.environ.get('MYSQL_PASSWORD', ''), 'port': 3306}
    try:
        if os.path.exists(DB_CFG_FILE):
            with open(DB_CFG_FILE) as f:
                cfg.update(json.load(f))
    except Exception:
        pass
    return cfg


def mysql_info():
    import pymysql
    dc = _mysql_creds()
    info = {'ok': False, 'error': ''}
    try:
        conn = pymysql.connect(host=dc.get('host', '127.0.0.1'),
                               user=dc.get('user', 'root'),
                               password=dc.get('password', '') or '',
                               port=dc.get('port', 3306),
                               charset='utf8mb4',
                               connect_timeout=4)
        cur = conn.cursor()
        cur.execute('SELECT VERSION()')
        info['version'] = cur.fetchone()[0]
        cur.execute('SHOW GLOBAL STATUS WHERE Variable_name IN '
                    "('Threads_connected','Questions','Slow_queries','Uptime')")
        st = {k: v for k, v in cur.fetchall()}
        info['ok'] = True
        if 'Uptime' in st:
            u = int(st['Uptime'])
            info['uptime_sec'] = u
            info['uptime'] = u
            if info.get('version','').startswith('8'):
                info['uptime'] = u  # seconds; UI renders days/hr
        info['threads_connected'] = int(st.get('Threads_connected', 0))
        info['connections'] = int(st.get('Threads_connected', 0))
        info['threads'] = int(st.get('Threads_connected', 0))
        info['questions'] = int(st.get('Questions', 0))
        info['slow_queries'] = int(st.get('Slow_queries', 0))
        # 进程列表数(实际连接)
        cur.execute('SELECT COUNT(*) FROM information_schema.processlist')
        info['processlist'] = cur.fetchone()[0]
        # 各库体积
        cur.execute("""SELECT table_schema,
            ROUND(SUM(data_length+index_length)/1024/1024,1),
            COUNT(*) FROM information_schema.tables
            WHERE table_schema NOT IN ('information_schema','performance_schema','sys','mysql')
            GROUP BY table_schema ORDER BY 2 DESC LIMIT 10""")
        dbs = [{'db': r[0], 'mb': float(r[1] or 0), 'tables': int(r[2] or 0)}
               for r in cur.fetchall()]
        info['databases'] = dbs
        conn.close()
    except Exception as e:
        info['ok'] = False
        info['error'] = '连接失败: ' + str(e)
    return info


@app.route('/api/mysql')
def api_mysql():
    if not session.get('authed'):
        return jsonify({'error': '未登录'}), 401
    return jsonify(mysql_info())


# ---------------------------------------------------------------------------
# API：服务列表（状态 + 控制）
# ---------------------------------------------------------------------------
def _svc_mem_pct(name_or_pid_expr, unit=None):
    """拿到某服务的近似内存(取主进程 RSS)，MB"""
    if unit:
        rc, out, _ = run("systemctl show " + unit +
                         " -p MainPID --value 2>/dev/null")
        pid = out.strip()
        if pid and pid.isdigit():
            try:
                p = psutil.Process(int(pid))
                return round(p.memory_info().rss / 1e6)
            except Exception:
                return 0
    return 0


def _ports_of_unit(unit):
    rc, out, _ = run("systemctl show " + unit +
                     " -p MainPID --value 2>/dev/null")
    pid = out.strip()
    if pid and pid.isdigit():
        try:
            conns = psutil.Process(int(pid)).connections()
            return sorted({c.laddr.port for c in conns if c.laddr})
        except Exception:
            return []
    return []


def _unit_exists(unit):
    """systemd 单元是否真实存在。若被卸载(删unit文件/purge)则不存在。
    兼容模板实例(vncserver@1.service → 模板 vncserver@.service)。"""
    rc, out, _ = run("systemctl list-unit-files " + unit + " 2>/dev/null")
    if unit in out:
        return True
    # 实例化单元：匹配模板名
    template = None
    if '@' in unit:
        template = unit.split('@')[0] + '@.service'
    if template:
        rc2, out2, _ = run("systemctl list-unit-files " + template + " 2>/dev/null")
        if template in out2:
            return True
    # 兜底：该单元当前 active / 曾注册过
    if run("systemctl is-active " + unit)[1] in ('active', 'activating'):
        return True
    if run("systemctl is-enabled " + unit)[1] in ('enabled', 'static', 'indirect', 'disabled'):
        return True
    return False


def collect_systemd(svc):
    unit = svc['unit']
    if not _unit_exists(unit):
        # 单元已被卸载（服务文件不再存在）→ 标记不存在
        return {'uninstalled': True}
    active = run("systemctl is-active " + unit)[1] == 'active'
    enabled = run("systemctl is-enabled " + unit)[1]  # enabled/disabled
    mem = _svc_mem_pct(None, unit)
    ports = _ports_of_unit(unit)
    return {
        'active': active,
        'enabled': enabled in ('enabled', 'static', 'indirect'),
        'mem_mb': mem,
        'ports': ports,
    }


def collect_proc(svc):
    """纯 psutil 匹配，不走 shell：避免 pgrep -f 自匹配外壳命令字符串产生的幽灵 PID。"""
    procs = []
    total_mem = 0
    pats = svc.get('procs', [])
    me_pid = os.getpid()
    for p in psutil.process_iter(['pid', 'cmdline']):
        try:
            pid = p.info['pid']
            if pid == me_pid:
                continue
            cmd = ' '.join(p.info['cmdline'] or [])
            if not cmd:
                continue
            if any(pat in cmd for pat in pats):
                rss = p.memory_info().rss / 1e6
                total_mem += rss
                procs.append({'pid': str(pid), 'cpu': p.cpu_percent(0.1),
                              'mem_mb': round(rss)})
        except Exception:
            continue
    return {'active': len(procs) > 0, 'enabled': None,
            'mem_mb': round(total_mem), 'ports': [], 'procs': procs}


@app.route('/api/services')
def api_services():
    if not session.get('authed'):
        return jsonify({'error': '未登录'}), 401
    out = []
    for svc in SERVICES:
        base = {'id': svc['id'], 'name': svc['name'], 'desc': svc['desc'],
                'kind': svc['kind'], 'controllable': CONTROLLABLE[svc['kind']],
                'readonly': bool(svc.get('readonly')),
                'warn': svc.get('warn') or None,
                'uninstallable': (svc['kind'] == 'systemd'
                                  and svc['id'] not in PROTECTED_CLI),
                'unit': svc.get('unit') or None}
        try:
            if svc['kind'] == 'systemd':
                st = collect_systemd(svc)
                if st.get('uninstalled'):
                    # 服务已被卸载（如外部删除了 mysql）→ 从面板消失
                    continue
                base.update(st)
            else:
                base.update(collect_proc(svc))
        except Exception:
            base['active'] = False
        out.append(base)
    # 汇总
    running = sum(1 for s in out if s.get('active'))
    return jsonify({'services': out, 'summary': {'running': running,
                                                 'total': len(out)}})


@app.route('/api/service/control', methods=['POST'])
def api_service_control():
    if not session.get('authed'):
        return jsonify({'error': '未登录'}), 401
    data = request.get_json(silent=True) or {}
    sid = data.get('id')
    action = data.get('action')  # start|stop|restart|enable|disable
    svc = next((s for s in SERVICES if s['id'] == sid), None)
    if not svc:
        return jsonify({'error': '未知服务'}), 400
    if not CONTROLLABLE[svc['kind']]:
        return jsonify({'error': '该服务仅只读监控，不允许在面板操作'}), 403
    if svc.get('readonly') and action in ('stop', 'disable'):
        return jsonify({'error': '“' + svc['name'] + '”为安全基线服务，面板只允许启动/重启，不允许停止或关自启'}), 403
    unit = svc['unit']
    cmds = {
        'start': 'systemctl start ' + unit,
        'stop': 'systemctl stop ' + unit,
        'restart': 'systemctl restart ' + unit,
        'enable': 'systemctl enable ' + unit,
        'disable': 'systemctl disable ' + unit,
    }
    cmd = cmds.get(action)
    if not cmd:
        return jsonify({'error': '未知操作'}), 400
    # 面板上点“启动”时顺带 enable，避免重启服务器后静默消失（如 cet4 预加载）
    extra_cmds = []
    if action == 'start' and svc.get('enable_with_start'):
        extra_cmds.append('systemctl enable ' + unit)
    # 多单元卡片（如 dyh 桌面）：按依赖顺序连带控制附属单元
    extra = svc.get('extra_units') or []
    if extra:
        cmds2 = {
            'start': ['systemctl start ' + u for u in extra] + ['systemctl start ' + unit],
            'stop': ['systemctl stop ' + unit] + ['systemctl stop ' + u for u in extra],
            'restart': ['systemctl restart ' + u for u in extra] + ['systemctl restart ' + unit],
            'enable': ['systemctl enable ' + u for u in extra] + ['systemctl enable ' + unit],
            'disable': ['systemctl disable ' + u for u in extra] + ['systemctl disable ' + unit],
        }
        for c in cmds2.get(action, [cmd]):
            rc, out, err = run(c, timeout=30)
        for c in extra_cmds:
            run(c, timeout=15)
    else:
        rc, out, err = run(cmd, timeout=30)
        for c in extra_cmds:
            run(c, timeout=15)
    time.sleep(1.2)
    # 重新读取状态
    state = collect_systemd(svc) if svc['kind'] == 'systemd' else {}
    ok = rc == 0
    msg = err or out
    if ok and extra_cmds:
        msg = (msg + '（已同时设为开机自启）') if msg else '启动成功（已同时设为开机自启）'
    return jsonify({'success': ok, 'message': msg,
                    'state': state})


# 保护列表：这些是系统/项目根基，禁止通过面板“卸载”按钮删除（只能启停）
PROTECTED_CLI = {'mysql', 'exam', 'nginx', 'svcctrl', 'cet4', 'fail2ban', 'family'}

@app.route('/api/service/logs', methods=['GET'])
def api_service_logs():
    """读取某服务日志。
    两种模式：
      lines=N        -> 直接取最近 N 行（首次/手动刷新）
      since=EPOCH秒  -> 增量拉取该时间点之后的新日志（轮询实现近似实时）
    统一用 journalctl -o short-unix，每行首字段为 unix 时间戳(秒.小数)，
    可被 @epoch 精确解析，不会出现 "Failed to parse timestamp"。
    """
    if not session.get('authed'):
        return jsonify({'error': '未登录'}), 401
    sid = request.args.get('id')
    lines = request.args.get('lines', '200')
    lines = min(int(lines) if lines.isdigit() else 200, 1000)
    since_raw = request.args.get('since', '').strip()
    svc = next((s for s in SERVICES if s['id'] == sid), None)
    if not svc:
        return jsonify({'error': '未知服务'}), 400
    unit = svc.get('unit')
    if not unit:
        return jsonify({'error': '该服务无 systemd 单元(' + svc['kind'] + ')'}), 400
    unit = unit.replace(';', '').replace('|', '').strip()
    # since 以 unix 秒(整数)传给 journalctl @epoch
    if since_raw and since_raw.isdigit():
        sec = int(since_raw)
        if sec < 1000000000:  # 2001年以前 = 无效游标(如0/初始) → 无增量
            return jsonify({'unit': unit, 'log': '', 'count': 0, 'since_raw': since_raw})
        cmd = 'journalctl -u %s --no-pager -o short-unix --since="@%d"' % (unit, sec - 1)
    else:
        cmd = 'journalctl -u %s --no-pager -o short-unix -n %d' % (unit, lines)
    rc, out, err = run(cmd, timeout=10)
    text = out if rc == 0 else (err or out)
    if rc != 0:
        text = text or '该服务暂无日志或日志已被清除'
    lines_list = text.splitlines() if text else []
    return jsonify({'unit': unit, 'log': text,
                    'count': len(lines_list),
                    # 供前端做增量游标：取最后一行首字段(unix秒)
                    'cursor': _last_cursor(lines_list)})


def _last_cursor(lines):
    """取日志最后一行的 unix 时间戳(秒)作为下次增量起点；无则0。"""
    for ln in reversed(lines):
        head = ln.split(None, 1)[0]
        try:
            return int(float(head))
        except Exception:
            continue
    return 0


@app.route('/api/service/uninstall', methods=['POST'])
def api_service_uninstall():
    """卸载某服务：disable + stop + 移除其 unit 文件 + daemon-reload。
    保护核心服务(mysql/exam/nginx/svcctrl)不可卸载。"""
    if not session.get('authed'):
        return jsonify({'error': '未登录'}), 401
    data = request.get_json(silent=True) or {}
    sid = data.get('id')
    svc = next((s for s in SERVICES if s['id'] == sid), None)
    if not svc:
        return jsonify({'error': '未知服务'}), 400
    if not CONTROLLABLE[svc['kind']]:
        return jsonify({'error': '该服务仅只读监控，不能卸载'}), 403
    if sid in PROTECTED_CLI:
        return jsonify({'error': '“' + svc['name'] + '”为系统/项目核心，禁用卸载（可停止/禁用）'}), 403
    unit = (svc.get('unit') or '').strip()
    if not unit or ';' in unit or '|' in unit:
        return jsonify({'error': '无效单元'}), 400

    # 查找 unit 文件真实路径
    rc, out, _ = run('systemctl show ' + unit + ' -p FragmentPath --value')
    fpath = out.strip()
    steps = []
    # 1) 停止
    rc, o, e = run('systemctl stop ' + unit + ' 2>&1', timeout=15)
    steps.append(('stop', rc == 0 or 'not loaded' in e.lower() or 'no such' in e.lower() or 'failed' in e.lower() and 'inactive' in (run('systemctl is-active '+unit)[1] or ''), '停止' + (e or o)))
    # 2) 禁用
    rc, o, e = run('systemctl disable ' + unit + ' 2>&1', timeout=15)
    steps.append(('disable', True, e or o))
    # 3) 若 unit 文件由我们管理(非系统包文件)则尝试删除；位于 /etc/systemd/system/ 根目录的才删，避免破坏发行版包
    removed = False
    if fpath and fpath.startswith('/etc/systemd/system/') and os.path.basename(fpath).endswith('.service'):
        if os.path.exists(fpath):
            os.remove(fpath)
            removed = True
    # 若 FragmentPath 为空但服务是手写的(带 @ 实例型用模板)，尝试从 /etc/systemd/system 找
    if not removed and unit.endswith('.service'):
        cand = '/etc/systemd/system/' + unit
        if os.path.exists(cand):
            os.remove(cand)
            removed = True
    run('systemctl daemon-reload')
    return jsonify({'success': True,
                    'removed_unit_file': removed,
                    'message': '已停止并禁用' + (('，已删除单元文件 ' + fpath) if removed else '，单元文件未删除(系统包，需用包管理器卸载)') ,
                    'steps': steps})


# ---------------------------------------------------------------------------
# 改密码 CLI (svcctrl changepass)
# ---------------------------------------------------------------------------
def cli_changepass(new_pass=None, new_user=None):
    ensure_auth()
    data = get_auth_data()  # 自动迁移为 users 结构
    users = data.setdefault('users', {})
    # 目标账号：显式指定，否则唯一 owner，否则第一个
    target = new_user or next((n for n, u in users.items()
                               if u.get('role') == 'owner'), None) \
        or (next(iter(users)) if users else None)
    if not new_pass:
        import getpass
        new_pass = getpass.getpass('新密码: ')
    if len(new_pass) < 6 and not new_user:
        print('\n提示：密码长度建议不小于 6 位')
    if new_user and new_user not in users:
        users[new_user] = {'role': 'owner',
                           'created': datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
    users[new_user or target]['password_hash'] = _hash_password(new_pass)
    data['changed'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    _atomic_save_auth(data)
    print('凭据已更新: 用户名=' + str(new_user or target))


# ---------------------------------------------------------------------------
# IP 行为检视：nginx 访问日志 + SSH 登录事件，按外网IP聚合
#   仅回显非私人/回环外网 IP。自己访问也是外网IP，未设白名单前一律展示。
# ---------------------------------------------------------------------------
import re as _re
import ipaddress as _ipaddr
ACCESS_LOG = '/var/log/nginx/access.log'


def _is_external(ip):
    ip = (ip or '').strip().lower()
    if not ip:
        return False
    if ip.startswith('::ffff:'):
        ip = ip[7:]
    if ip in ('127.0.0.1', '::1', 'localhost'):
        return False
    try:
        a = _ipaddr.ip_address(ip)
        if a.is_private or a.is_loopback or a.is_link_local or a.is_multicast or a.is_reserved:
            return False
    except ValueError:
        return False
    return True


_nginx_line = _re.compile(r'^(\S+) - - \[([^\]]+)\] "(\S+) (\S+)[^"]*" (\d{3}) (\d+) "([^"]*)" "([^"]*)"')


def _tail_nginx(n=25000, logfile=None):
    """读取 nginx access 日志末 n 行 (从文件尾往前读，控制开销).
    logfile 可选：指定端口独立日志(/var/log/nginx/access_<port>.log)，否则读主日志."""
    path = logfile or ACCESS_LOG
    try:
        with open(path, 'rb') as f:
            f.seek(0, 2)
            size = f.tell()
            block = 256 * 1024
            start = max(0, size - block)
            f.seek(start)
            data = f.read(size - start).decode('utf-8', 'ignore')
        lines = data.splitlines()
        return lines[-n:]
    except Exception:
        return []


def _tail_all_nginx(n=25000):
    """聚合读取所有分端口 nginx 日志(主日志+各独立端口日志)，返回一条条日志文本行。
    真实流量都在分端口日志(access_443/80/8443/8444.log)，主 access.log 常为空。
    """
    paths = [ACCESS_LOG] + list(PORT_LOG_FILE.values())
    lines = []
    for p in paths:
        lines.extend(_tail_nginx(n // max(1, len(paths)), p))
    return lines


def _ssh_events(hours=24):
    out = []
    try:
        since = '--since=-%dh' % hours
        r = subprocess.run(['journalctl', '-u', 'ssh', '--no-pager', since, '-o', 'short-iso'],
                           capture_output=True, text=True, timeout=8)
        for ln in r.stdout.splitlines():
            m = _re.search(r'\b(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})\b', ln)
            if not m:
                continue
            ip = m.group(1)
            if not _is_external(ip):
                continue
            kind = 'unknown'
            if 'Accepted' in ln:
                kind = 'accepted'
            elif 'Failed password' in ln or 'Failed' in ln:
                kind = 'failed'
            elif 'Connection reset' in ln:
                kind = 'reset'
            elif 'Disconnected' in ln:
                kind = 'disc'
            out.append({'ip': ip, 'kind': kind, 'text': ln.strip()})
    except Exception:
        pass
    return out


@app.route('/api/access/ip')
def api_access_ip():
    if not session.get('authed'):
        return jsonify({'error': '未登录'}), 401
    try:
        hours = min(int(request.args.get('hours', 24)), 168)
    except Exception:
        hours = 24
    agg = {}   # ip -> dict
    for ln in _tail_all_nginx():
        m = _nginx_line.match(ln)
        if not m:
            continue
        ip = m.group(1)
        if not _is_external(ip):
            continue
        t = m.group(2)
        method = m.group(3)
        path = m.group(4)
        status = m.group(5)
        ua = m.group(7)
        d = agg.setdefault(ip, {'ip': ip, 'web': 0, 'last': None, 'paths': {},
                                'methods': {},
                                'statuses': {},
                                'ua': ua or ''})
        d['web'] += 1
        d['last'] = t if d['last'] is None else (t if t > d['last'] else d['last'])
        key = method + ' ' + path
        p = d['paths'].setdefault(key, {'n': 0, 'last': None})
        p['n'] += 1
        p['last'] = t if p['last'] is None else (t if t > p['last'] else p['last'])
        d['methods'].setdefault(method, 0)
        d['methods'][method] += 1
        d['statuses'].setdefault(status, 0)
        d['statuses'][status] += 1
        if d['ua'] and len(d['ua']) < 64:
            pass

    for e in _ssh_events(hours):
        d = agg.setdefault(e['ip'], {'ip': e['ip'], 'web': 0, 'last': None, 'paths': {},
                                     'methods': {},
                                     'statuses': {},
                                     'ua': ''})
        d.setdefault('ssh', []).append(e['kind'])

    summary = []
    for ip, d in agg.items():
        top = sorted(d['paths'].items(), key=lambda kv: kv[1]['last'], reverse=True)
        _summary = {
            'ip': ip,
            'web': d['web'],
            'last': d['last'],
            'ua': d['ua'],
            'ssh': len(d.get('ssh', [])),
            'ssh_kinds': d.get('ssh', []),
            'paths_top': [{'path': k, 'n': v['n'], 'last': v['last']}
                          for k, v in top[:3]],
            'methods': d['methods'],
            'statuses': d['statuses'],
        }
        summary.append(_summary)
    summary.sort(key=lambda x: x['last'] or '', reverse=True)
    return jsonify({'ips': summary})


@app.route('/api/access/ip/<ip>')
def api_access_ip_detail(ip):
    if not session.get('authed'):
        return jsonify({'error': '未登录'}), 401
    if not _is_external(ip):
        return jsonify({'error': '仅支持外网IP'}), 400
    day = (request.args.get('date') or '').strip()
    # 历史日期：读该日存档(nginx 归档日志 + journald 当日 SSH)，而非仅当前日志
    if day and day != datetime.now().strftime('%Y-%m-%d'):
        try:
            web = _detail_web_from_archive(ip, day)
            ssh = _detail_ssh_from_journal(ip, day)
            return jsonify({'ip': ip, 'date': day, 'web': web, 'ssh': ssh})
        except Exception as e:
            return jsonify({'ip': ip, 'date': day, 'web': [], 'ssh': [], 'error': str(e)})
    web = []
    for ln in _tail_all_nginx():
        m = _nginx_line.match(ln)
        if not m or m.group(1) != ip:
            continue
        web.append({'t': m.group(2), 'method': m.group(3), 'path': m.group(4),
                    'status': m.group(5), 'ua': m.group(7)})
    web = web[-200:]
    web.reverse()
    ssh = [e for e in _ssh_events() if e['ip'] == ip][-100:]
    return jsonify({'ip': ip, 'web': web, 'ssh': ssh})


def _detail_web_from_archive(ip, day):
    """从指定日期的 nginx 日志(当前或归档)取该 IP 的请求明细。"""
    import ip_history
    out = []
    for lp in ip_history.ACCESS_LOGS:
        path = ip_history._resolve_log_path(lp, day)
        if not path:
            continue
        txt = ip_history._read_nginx_file(path)
        for ln in txt.splitlines():
            m = _nginx_line.match(ln)
            if not m or m.group(1) != ip:
                continue
            out.append({'t': m.group(2), 'method': m.group(3), 'path': m.group(4),
                        'status': m.group(5), 'ua': m.group(7)})
    out = out[-200:]
    out.reverse()
    return out


def _detail_ssh_from_journal(ip, day):
    """从 journald 取指定日期该 IP 的 SSH 事件。"""
    out = []
    try:
        r = subprocess.run(['journalctl', '-u', 'ssh', '--no-pager', '-o', 'short-iso',
                            '--since', day + ' 00:00:00', '--until', day + ' 23:59:59'],
                           capture_output=True, text=True, timeout=20)
        for ln in r.stdout.splitlines():
            if ip in ln:
                kind = 'unknown'
                if 'Accepted' in ln:
                    kind = 'accepted'
                elif 'Failed' in ln:
                    kind = 'failed'
                elif 'Connection reset' in ln:
                    kind = 'reset'
                elif 'Disconnected' in ln:
                    kind = 'disc'
                out.append({'ip': ip, 'kind': kind, 'text': ln.strip()})
    except Exception:
        pass
    return out[-100:]


# 已知对外端口 → nginx 独立日志文件映射。22=SSH(不走nginx日志,走journalctl)。
PORT_LOG_FILE = {
    '443': '/var/log/nginx/access_443.log',
    '8443': '/var/log/nginx/access_8443.log',
    '80': '/var/log/nginx/access_80.log',
    '8444': '/var/log/nginx/access_8444.log',
}


@app.route('/api/access/port/<port>')
def api_access_port(port):
    """按端口聚合攻击/访问情况。22 走 SSH 事件; 其余 web 端口走对应 nginx 独立日志。"""
    if not session.get('authed'):
        return jsonify({'error': '未登录'}), 401
    port = (port or '').strip()
    if port == '22':
        # SSH：按 IP 聚合同样结构
        agg = {}
        for e in _ssh_events(24):
            d = agg.setdefault(e['ip'], {'ip': e['ip'], 'kind': e['kind'],
                                          'n': 0, 'last': e['text'][:40],
                                          'kinds': set()})
            d['n'] += 1
            d['kinds'].add(e['kind'])
            if e['text'][:40] > d['last']:
                d['last'] = e['text'][:40]
        out = []
        for ip, d in agg.items():
            out.append({'ip': ip, 'web': 0, 'ssh': d['n'],
                        'ssh_kinds': sorted(d['kinds']), 'last': d['last'],
                        'methods': {}, 'statuses': {}, 'paths_top': [], 'ua': ''})
        out.sort(key=lambda x: x['ssh'], reverse=True)
        return jsonify({'port': '22', 'type': 'ssh', 'ips': out})

    logfile = PORT_LOG_FILE.get(port)
    if not logfile:
        return jsonify({'error': '未知端口'}), 400
    agg = {}
    for ln in _tail_nginx(25000, logfile):
        m = _nginx_line.match(ln)
        if not m:
            continue
        ip = m.group(1)
        if not _is_external(ip):
            continue
        t = m.group(2)
        method = m.group(3)
        path = m.group(4)
        status = m.group(5)
        d = agg.setdefault(ip, {'ip': ip, 'web': 0, 'last': None,
                                'methods': {}, 'statuses': {},
                                'paths': {}, 'ua': m.group(7) or ''})
        d['web'] += 1
        d['last'] = t if d['last'] is None else (t if t > d['last'] else d['last'])
        d['methods'].setdefault(method, 0); d['methods'][method] += 1
        d['statuses'].setdefault(status, 0); d['statuses'][status] += 1
        key = method + ' ' + path
        p = d['paths'].setdefault(key, {'n': 0, 'last': None})
        p['n'] += 1
        p['last'] = t if p['last'] is None else (t if t > p['last'] else p['last'])
    summary = []
    for ip, d in agg.items():
        top = sorted(d['paths'].items(), key=lambda kv: kv[1]['last'], reverse=True)
        summary.append({'ip': ip, 'web': d['web'], 'last': d['last'], 'ua': d['ua'],
                        'ssh': 0, 'ssh_kinds': [], 'paths_top':
                        [{'path': k, 'n': v['n'], 'last': v['last']} for k, v in top[:3]],
                        'methods': d['methods'], 'statuses': d['statuses']})
    summary.sort(key=lambda x: x['last'] or '', reverse=True)
    return jsonify({'port': port, 'type': 'web', 'ips': summary})


# 可切换模型 → (provider, 通道)。DeepSeek 官方走官方 API；Volcengine 走火山引擎方舟；其余走百炼(兼容端点)。
DEEPSEEK_OFFICIAL = {"deepseek-v4-flash"}   # 官方通道(有额度)
VOLCENGINE_MODELS = ["ark-code-latest"]      # 火山引擎方舟
BAILIAN_MODELS = ["qwen3.8-flash", "deepseek-v4-flash-0731", "deepseek-v3", "qwen-plus", "qwen-turbo", "deepseek-r1"]
MODEL_CHOICES = [
    {"id": "deepseek-v4-flash", "provider": "DeepSeek 官方"},
] + [{"id": m, "provider": "火山引擎 Volcengine"} for m in VOLCENGINE_MODELS] \
    + [{"id": m, "provider": "百炼 Bailian"} for m in BAILIAN_MODELS]
DEFAULT_MODEL = "ark-code-latest"   # 用户指定的统一默认(火山引擎 Volcengine 方舟, 已验证可用)


def _provider_for(model):
    """返回 (provider, key_env, base_env, fallback_url)."""
    if model in DEEPSEEK_OFFICIAL:
        return ("deepseek-official", "DEEPSEEK_API_KEY", "DEEPSEEK_BASE_URL",
                "https://api.deepseek.com/v1")
    if model in VOLCENGINE_MODELS:
        return ("volcengine", "VOLCENGINE_API_KEY", "VOLCENGINE_BASE_URL",
                "https://ark.cn-beijing.volces.com/api/plan/v3")
    return ("bailian", "BAILIAN_API_KEY", "BAILIAN_BASE_URL",
            "https://token-plan.cn-beijing.maas.aliyuncs.com/compatible-mode/v1")


def _llm_chat(messages, model=None, max_tokens=600):
    """按模型走对应通道调用 chat/completions (不打印密钥)."""
    import urllib.request, urllib.error
    model = model or DEFAULT_MODEL
    prov, key_env, base_env, fallback = _provider_for(model)
    key = os.environ.get(key_env, "")
    if not key:
        return {"ok": False, "error": "%s 未配置密钥，AI 不可用" % prov}
    base = os.environ.get(base_env, fallback)
    url = base.rstrip("/") + "/chat/completions"
    body = json.dumps({
        "model": model,
        "messages": messages,
        "temperature": 0.3,
        "max_tokens": max_tokens,
    }).encode()
    req = urllib.request.Request(url, data=body, headers={
        "Content-Type": "application/json",
        "Authorization": "Bearer " + key,
    })
    try:
        with urllib.request.urlopen(req, timeout=90) as r:
            data = json.loads(r.read().decode())
        return {"ok": True, "text": data["choices"][0]["message"]["content"], "model": model, "provider": prov}
    except urllib.error.HTTPError as e:
        return {"ok": False, "error": "HTTP %s %s" % (e.code, e.read()[:200].decode('utf-8', 'ignore'))}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@app.route('/api/access/models')
def api_access_models():
    if not session.get('authed'):
        return jsonify({'error': '未登录'}), 401
    return jsonify({'models': MODEL_CHOICES, 'default': DEFAULT_MODEL})


@app.route('/api/access/ip/<ip>/ai')
def api_access_ip_ai(ip):
    if not session.get('authed'):
        return jsonify({'error': '未登录'}), 401
    if not _is_external(ip):
        return jsonify({'error': '仅支持外网IP'}), 400
    model = request.args.get('model') or ''
    # 收集该 IP 行为文本(同 detail 路径, 自洽不重复落库)。真实流量在分端口日志。
    web = []
    for ln in _tail_all_nginx():
        m = _nginx_line.match(ln)
        if not m or m.group(1) != ip:
            continue
        web.append((m.group(2), m.group(3), m.group(4), m.group(5)))
    web = web[-80:]
    web.reverse()
    ssh = [e for e in _ssh_events() if e['ip'] == ip]
    lines = ["IP %s 最近行为摘要：" % ip]
    lines.append("HTTP/HTTPS 请求 %d 条（最新的在前）：" % len(web))
    for t, mm, pth, st in web[:60]:
        lines.append("  %s | %s %s -> %s" % (t, mm, pth, st))
    if not web:
        lines.append("  （无 web 请求记录）")
    if ssh:
        lines.append("SSH 事件 %d 条：" % len(ssh))
        for e in ssh[-25:]:
            lines.append("  %s" % e['text'][:160])
    else:
        lines.append("无 SSH 事件。")
    sys_prompt = ("你是网络安全分析助手。根据给出的访问日志，判断这个IP的行为："
                  "是正常家庭/办公用户，还是扫描器/爬虫/暴力破解尝试；指出主要访问了哪些"
                  "服务与接口、有无风险、是否建议关注。用中文、简洁分点回答。"
                  "注意：本服务器控制台自身的接口(/api/system /api/services /api/mysql /api/ping "
                  "/api/access/* 等)会被页面高频轮询（每秒多次），若某IP只是在规律访问这些接口、"
                  "且无扫描/爆破特征，应判断为“控制台正常使用”，不要误判为扫描器。")
    msg = _llm_chat([
        {"role": "system", "content": sys_prompt},
        {"role": "user", "content": "\n".join(lines)},
    ], model=model or None)
    return jsonify(msg)






# ---------------------------------------------------------------------------
# IP 黑名单：持久化 + ufw deny/allow（防火墙拦全部）
# ---------------------------------------------------------------------------
BLOCK_FILE = os.path.join(BASE, 'blocklist.json')


def _load_block():
    try:
        return set(json.load(open(BLOCK_FILE)))
    except Exception:
        return set()


def _save_block(s):
    try:
        with open(BLOCK_FILE, 'w') as f:
            json.dump(sorted(s), f)
    except Exception:
        pass


def _client_ip():
    """取当前请求真正的外网来源IP（svcctrl 仅经 nginx 反代可达，可信 X-Real-IP/XFF）"""
    rid = request.headers.get('X-Real-IP') or ''
    ff = request.headers.get('X-Forwarded-For') or ''
    for cand in (rid, (ff.split(',')[0] if ff else '').strip()):
        if cand and _is_external(cand):
            return cand
    ra = request.remote_addr or ''
    return ra if _is_external(ra) else ''


def _ufw_deny(ip, add):
    cmd = ['/sbin/ufw', 'deny', 'from', ip] if add else ['/sbin/ufw', 'delete', 'deny', 'from', ip]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        return r.returncode == 0, (r.stdout + r.stderr)[-400:]
    except Exception as e:
        return False, str(e)


@app.route('/api/access/blocklist')
def api_blocklist():
    if not session.get('authed'):
        return jsonify({'error': '未登录'}), 401
    return jsonify({'blocked': sorted(_load_block())})


@app.route('/api/access/block', methods=['POST'])
def api_access_block():
    if not session.get('authed'):
        return jsonify({'error': '未登录'}), 401
    ip = ((request.get_json(silent=True) or {}).get('ip') or '').strip()
    if not _is_external(ip):
        return jsonify({'error': '非法外网IP'}), 400
    me = _client_ip()
    if me and ip == me:
        return jsonify({'error': '不能拉黑当前正在使用的IP（%s）以免自锁' % ip}), 400
    ok, msg = _ufw_deny(ip, True)
    if ok:
        b = _load_block(); b.add(ip); _save_block(b)
        return jsonify({'ok': True, 'blocked': ip, 'msg': msg.strip()})
    return jsonify({'error': 'ufw: ' + msg.strip()}), 500


@app.route('/api/access/unblock', methods=['POST'])
def api_access_unblock():
    if not session.get('authed'):
        return jsonify({'error': '未登录'}), 401
    ip = ((request.get_json(silent=True) or {}).get('ip') or '').strip()
    ok, msg = _ufw_deny(ip, False)
    if ok:
        b = _load_block(); b.discard(ip); _save_block(b)
        return jsonify({'ok': True, 'unblocked': ip})
    return jsonify({'error': 'ufw: ' + msg.strip()}), 500


# ============ IP 历史存档（按日期查看 / 高风险筛选 / 批量封禁）============

@app.route('/api/access/history')
def api_access_history():
    """按日期返回 IP 聚合列表。?date=YYYY-MM-DD（默认今天）&highrisk=1 只返回高风险。"""
    if not session.get('authed'):
        return jsonify({'error': '未登录'}), 401
    day = (request.args.get('date') or '').strip()
    if not day:
        day = datetime.now().strftime('%Y-%m-%d')
    highrisk = request.args.get('highrisk') in ('1', 'true', 'yes')
    try:
        rows = ip_history.query_day(day, highrisk_only=highrisk)
    except Exception as e:
        return jsonify({'error': '查询失败: %s' % e}), 500
    return jsonify({'date': day, 'highrisk': highrisk, 'ips': rows})


@app.route('/api/access/dates')
def api_access_dates():
    """返回有数据的日期列表（降序），供日期下拉使用。"""
    if not session.get('authed'):
        return jsonify({'error': '未登录'}), 401
    try:
        dates = ip_history.list_dates()
    except Exception as e:
        dates = []
    return jsonify({'dates': dates})


@app.route('/api/access/current-ip')
def api_access_current_ip():
    """返回当前登录/连接来源的外网 IP，供前端实时显示并用于批量封禁自锁保护。"""
    if not session.get('authed'):
        return jsonify({'error': '未登录'}), 401
    return jsonify({'ip': _client_ip()})


@app.route('/api/access/block-bulk', methods=['POST'])
def api_access_block_bulk():
    """批量封禁。body: {ips:[...]}。自动排除当前连接 IP（防自锁）。逐条调用 ufw，返回每条结果。"""
    if not session.get('authed'):
        return jsonify({'error': '未登录'}), 401
    data = request.get_json(silent=True) or {}
    ips = data.get('ips') or []
    if not isinstance(ips, list) or not ips:
        return jsonify({'error': '未提供要封禁的 IP 列表'}), 400
    me = _client_ip()
    ok_list, skip_list, fail_list = [], [], []
    for raw in ips:
        ip = (str(raw) or '').strip()
        if not _is_external(ip):
            fail_list.append({'ip': ip, 'error': '非法外网IP'})
            continue
        if me and ip == me:
            skip_list.append({'ip': ip, 'error': '当前连接IP，已自动跳过'})
            continue
        ok, msg = _ufw_deny(ip, True)
        if ok:
            b = _load_block(); b.add(ip); _save_block(b)
            ok_list.append({'ip': ip})
        else:
            fail_list.append({'ip': ip, 'error': 'ufw: ' + msg.strip()})
    return jsonify({'ok': True, 'blocked': ok_list, 'skipped': skip_list,
                    'failed': fail_list})


if __name__ == '__main__':
    import sys
    # svcctrl changepass <pass> [username]
    if len(sys.argv) > 1 and sys.argv[1] == 'changepass':
        pwd = sys.argv[2] if len(sys.argv) > 2 else None
        user = sys.argv[3] if len(sys.argv) > 3 else None
        cli_changepass(pwd, user)
        sys.exit(0)
    ensure_auth()
    print('svcctrl listening on 127.0.0.1:' + str(PORT), flush=True)
    # 只绑定本机回环；对外由 nginx 反代(带登录)，不直接公网暴露
    app.run(host='127.0.0.1', port=PORT, debug=False, threaded=True)

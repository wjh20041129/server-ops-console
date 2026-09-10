#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ip_history — svcctrl IP 访问历史落库 + 高风险判定。
按天把外网 IP 对服务器的访问(nginx 日志)与 SSH 事件(journald)聚合存入 SQLite，
供控制台按日期查看 / 高风险筛选 / 批量封禁。

数据表：
  ip_daily (date, ip, web, n404, scan_hits, ssh_fail, ssh_ok, ssh_other,
            web_ports, ssh_kinds, ports, last_ts, ua)
  PRIMARY KEY(date, ip)

高风险判定：5 个维度 OR，任一命中即高风险。阈值在 risk_rules.json 可配。
"""
import os
import re
import gzip
import json
import sqlite3
import subprocess
from datetime import datetime, date, timedelta

BASE = os.path.dirname(os.path.abspath(__file__))
DB_FILE = os.path.join(BASE, 'ip_history.db')
RULES_FILE = os.path.join(BASE, 'risk_rules.json')
RETENTION_DAYS = 90

ACCESS_LOGS = [
    '/var/log/nginx/access.log',
    '/var/log/nginx/access_443.log',
    '/var/log/nginx/access_80.log',
    '/var/log/nginx/access_8443.log',
    '/var/log/nginx/access_8444.log',
]

# 常见扫描/漏洞探测路径特征（命中任一即计 scan_hits）。子串匹配，宽松。
SCAN_PATHS = [
    '/wp-admin', '/wp-login', '/wp-content', '/xmlrpc.php',
    '/.env', '/.git/', '/.git/HEAD', '/.svn/', '/.hg/',
    '/phpinfo.php', '/phpmyadmin', '/pma', '/admin.php', '/administrator',
    '/config.php', '/config', '/backup', '/db_backup', '/sql', '/dump',
    '/shell', '/webshell', '/c99', '/r57', '/mutillidae', '/dvwa',
    '/actuator', '/jenkins', '/cve-', '/log4j', '/eval', '/bash',
    '/web.config', '/crossdomain.xml', '/actuator/env', '/manager/html',
    '/cgi-bin', '/vendor', '/laravel', '/thinkphp', '/struts',
    '/.aws/', '/.ssh/', '/src/', '/.npmrc', '/.htaccess',
]

_NGINX_LINE = re.compile(
    r'^(\S+) - - \[([^\]]+)\] "(\S+) (\S+)[^"]*" (\d{3}) (\d+) "([^"]*)" "([^"]*)"')


def _is_external(ip):
    import ipaddress
    ip = (ip or '').strip().lower()
    if not ip:
        return False
    if ip.startswith('::ffff:'):
        ip = ip[7:]
    if ip in ('127.0.0.1', '::1', 'localhost'):
        return False
    try:
        a = ipaddress.ip_address(ip)
        if a.is_private or a.is_loopback or a.is_link_local or a.is_multicast or a.is_reserved:
            return False
    except ValueError:
        return False
    return True


def load_rules():
    """阈值配置。文件不存在/损坏时用默认值。"""
    defaults = {
        'ssh_fail': 5,        # SSH 失败 ≥ N 次
        'scan_any': 1,        # 命中任意扫描路径 ≥ N 次
        'n404': 20,           # 404 状态码 ≥ N 次
        'ports': 3,           # 触及 ≥ N 个不同端口
        'ever_blocked': 1,    # 曾被拉黑 ≥ N 次
    }
    try:
        with open(RULES_FILE) as f:
            d = json.load(f)
        for k in defaults:
            if k not in d or not isinstance(d[k], (int, float)):
                d[k] = defaults[k]
        return d
    except Exception:
        return defaults


def _connect():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def init_db():
    conn = _connect()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS ip_daily (
            date TEXT NOT NULL,
            ip TEXT NOT NULL,
            web INTEGER NOT NULL DEFAULT 0,
            n404 INTEGER NOT NULL DEFAULT 0,
            scan_hits INTEGER NOT NULL DEFAULT 0,
            ssh_fail INTEGER NOT NULL DEFAULT 0,
            ssh_ok INTEGER NOT NULL DEFAULT 0,
            ssh_other INTEGER NOT NULL DEFAULT 0,
            web_ports TEXT NOT NULL DEFAULT '[]',
            ssh_kinds TEXT NOT NULL DEFAULT '[]',
            ports TEXT NOT NULL DEFAULT '[]',
            last_ts TEXT,
            ua TEXT,
            PRIMARY KEY (date, ip)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_ipd_date ON ip_daily(date)")
    conn.commit()
    conn.close()


def ingest_nginx_into(agg, text, day, ports, blk):
    """解析一段 nginx 日志，聚合进 agg[(day,ip)] dict。ports: 该文件对应端口。"""
    for ln in text.splitlines():
        m = _NGINX_LINE.match(ln)
        if not m:
            continue
        ip = m.group(1)
        if not _is_external(ip):
            continue
        ts = m.group(2)
        method, path = m.group(3), m.group(4)
        status = m.group(5)
        ua = m.group(7)
        # 日志时间 11/Sep/2026:00:25:16 +0800 → 我们按"该行属于哪天"聚合。
        # 但 day 由调用方按文件/时间段指定；这里仅防跨天混合，用 ts 的日期作为兜底。
        key = (day, ip)
        d = agg.setdefault(key, {
            'web': 0, 'n404': 0, 'scan_hits': 0, 'web_ports': set(),
            'last_ts': ts, 'ua': ua,
        })
        d['web'] += 1
        if status == '404':
            d['n404'] += 1
        pl = path.lower()
        if any(p in pl for p in SCAN_PATHS):
            d['scan_hits'] += 1
        if ports and ports != 0:  # 0=主access.log(聚合)，不计入独立端口维度
            d['web_ports'].add(ports)


def _read_nginx_file(path):
    """读取 nginx 日志文件（支持 .gz）。返回文本。"""
    try:
        if path.endswith('.gz'):
            with gzip.open(path, 'rt', errors='ignore') as f:
                return f.read()
        with open(path, 'rb') as f:
            return f.read().decode('utf-8', 'ignore')
    except Exception:
        return ''


def collect_day(day):
    """采集单个日期的数据（nginx 归档/当前 + journald SSH）。返回 {(day,ip):row}。"""
    day = str(day)
    agg = {}
    for lp in ACCESS_LOGS:
        # 抽端口：access_443.log → 443；access.log → 主(未归类，端口标 0/主)
        port = None
        mm = re.search(r'access(?:_(\d+))?\.log', lp)
        if mm and mm.group(1):
            port = int(mm.group(1))
        else:
            port = 0
        # 找该日期对应的文件：当天→当前 .log；否则按轮转序号
        path = _resolve_log_path(lp, day)
        if not path:
            continue
        txt = _read_nginx_file(path)
        ingest_nginx_into(agg, txt, day, port, None)
    # journald SSH：该日 00:00:00+0800 → 23:59:59+0800
    _ingest_ssh(agg, day)
    # 归一化
    out = {}
    for (d, ip), v in agg.items():
        out[(d, ip)] = {
            'date': d, 'ip': ip, 'web': v['web'], 'n404': v['n404'],
            'scan_hits': v['scan_hits'],
            'ssh_fail': v.get('ssh_fail', 0), 'ssh_ok': v.get('ssh_ok', 0),
            'ssh_other': v.get('ssh_other', 0),
            'web_ports': sorted(v['web_ports']),
            'ssh_kinds': sorted(set(v.get('ssh_kinds', []))),
            'ports': sorted(v['web_ports']),
            'last_ts': v['last_ts'], 'ua': v['ua'],
        }
    return out


def _resolve_log_path(base, day):
    """给定基础日志路径(如 access_443.log)和目标日期，返回对应文件路径或 None。"""
    today = date.today().isoformat()
    if str(day) == today:
        return base if os.path.exists(base) else None
    # 轮转：access.log.1=昨天, .2=前天... 归档可能有 .gz
    # access_443.log.1 / access_443.log.1.gz / access_443.log-20260910
    # 常见 Debian logrotate: access.log.1, .2.gz, ...
    diff = (date.fromisoformat(str(day)) - date.today()).days  # 负数
    for i in (1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14):
        if diff == -i:
            # .i 或 .i.gz 或 .i-<date>
            for ext_cand in [f"{base}.{i}", f"{base}.{i}.gz",
                             f"{base}.{i}-{day}",
                             f"{base}.{i}-{day}.gz"]:
                if os.path.exists(ext_cand):
                    return ext_cand
    return None


def _ingest_ssh(agg, day):
    """把某日的 journald SSH 事件聚合进 agg。kind: failed/ok/other。"""
    try:
        # --since 该日 00:00 至次日 00:00（本地时区）
        since = f"{day} 00:00:00"
        until = f"{day} 23:59:59"
        r = subprocess.run(
            ['journalctl', '-u', 'ssh', '--no-pager', '-o', 'short-iso',
             '--since', since, '--until', until],
            capture_output=True, text=True, timeout=30)
        for ln in r.stdout.splitlines():
            m = re.search(r'\b(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})\b', ln)
            if not m:
                continue
            ip = m.group(1)
            if not _is_external(ip):
                continue
            key = (str(day), ip)
            d = agg.setdefault(key, {
                'web': 0, 'n404': 0, 'scan_hits': 0, 'web_ports': set(),
                'last_ts': None, 'ua': ''})
            d['web_ports'].add(22)  # SSH 视为一个被触及的服务端口
            if 'Failed password' in ln or 'Failed' in ln:
                d['ssh_fail'] = d.get('ssh_fail', 0) + 1
                d.setdefault('ssh_kinds', []).append('failed')
            elif 'Accepted' in ln:
                d['ssh_ok'] = d.get('ssh_ok', 0) + 1
                d.setdefault('ssh_kinds', []).append('accepted')
            else:
                d['ssh_other'] = d.get('ssh_other', 0) + 1
                d.setdefault('ssh_kinds', []).append('other')
    except Exception:
        pass


def save_rows(rows):
    """upsert 一批 {(day,ip):dict} 进 sqlite。"""
    conn = _connect()
    for (d, ip), v in rows.items():
        conn.execute("""
            INSERT INTO ip_daily
              (date,ip,web,n404,scan_hits,ssh_fail,ssh_ok,ssh_other,
               web_ports,ssh_kinds,ports,last_ts,ua)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(date,ip) DO UPDATE SET
              web=excluded.web, n404=excluded.n404, scan_hits=excluded.scan_hits,
              ssh_fail=excluded.ssh_fail, ssh_ok=excluded.ssh_ok,
              ssh_other=excluded.ssh_other,
              web_ports=excluded.web_ports, ssh_kinds=excluded.ssh_kinds,
              ports=excluded.ports, last_ts=excluded.last_ts, ua=excluded.ua
        """, (
            d, ip, v['web'], v['n404'], v['scan_hits'],
            v['ssh_fail'], v['ssh_ok'], v['ssh_other'],
            json.dumps(v['web_ports']), json.dumps(v['ssh_kinds']),
            json.dumps(v['ports']), v['last_ts'], (v.get('ua') or '')[:200],
        ))
    conn.commit()
    conn.close()


def ingest_today():
    """采集今天数据入库（幂等覆盖当天聚合）。"""
    init_db()
    rows = collect_day(date.today().isoformat())
    save_rows(rows)
    return len(rows)


def backfill(days=RETENTION_DAYS):
    """回填近 N 天历史（含今天），幂等。返回 (成功天数, 累计IP行数)。"""
    init_db()
    total = 0
    today = date.today()
    for i in range(days):
        d = today - timedelta(days=i)
        rows = collect_day(d.isoformat())
        save_rows(rows)
        total += len(rows)
    return (days, total)


def prune_old(days=RETENTION_DAYS):
    """删除超过保留期的旧行。"""
    conn = _connect()
    cutoff = (date.today() - timedelta(days=days)).isoformat()
    cur = conn.execute("DELETE FROM ip_daily WHERE date < ?", (cutoff,))
    conn.commit()
    n = cur.rowcount
    conn.close()
    return n


def load_blocked_history():
    """从 blocklist 历史文件估算某 IP 是否曾被拉黑（简易版：读当前 blocklist）。"""
    try:
        with open(os.path.join(BASE, 'blocklist.json')) as f:
            return set(json.load(f))
    except Exception:
        return set()


def risk_rows(rows, rules=None, blocked=None):
    """对 rows(list of dict) 标注 high_risk 布尔。返回加字段后的列表。"""
    rules = rules or load_rules()
    blocked = blocked if blocked is not None else load_blocked_history()
    for r in rows:
        fail = r.get('ssh_fail', 0)
        n404 = r.get('n404', 0)
        scan = r.get('scan_hits', 0)
        ports = r.get('ports', [])
        blked = 1 if r.get('ip') in blocked else 0
        high = (
            fail >= rules['ssh_fail'] or
            n404 >= rules['n404'] or
            scan >= rules['scan_any'] or
            len(ports) >= rules['ports'] or
            blked >= rules['ever_blocked']
        )
        r['high_risk'] = bool(high)
        r['risk_flags'] = {
            'ssh_fail': fail >= rules['ssh_fail'],
            'n404': n404 >= rules['n404'],
            'scan': scan >= rules['scan_any'],
            'ports': len(ports) >= rules['ports'],
            'blocked_hist': blked >= rules['ever_blocked'],
        }
    return rows


def query_day(day, highrisk_only=False):
    """查询某日 IP 聚合，返回 list[dict]（含风险标注）。"""
    init_db()
    conn = _connect()
    cur = conn.execute(
        "SELECT * FROM ip_daily WHERE date=? ORDER BY (web+ssh_fail) DESC", (str(day),))
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    for r in rows:
        r['web_ports'] = json.loads(r.get('web_ports') or '[]')
        r['ssh_kinds'] = json.loads(r.get('ssh_kinds') or '[]')
        r['ports'] = json.loads(r.get('ports') or '[]')
        r['ssh'] = (r.get('ssh_fail') or 0) + (r.get('ssh_ok') or 0) + (r.get('ssh_other') or 0)
    rows = risk_rows(rows)
    if highrisk_only:
        rows = [r for r in rows if r['high_risk']]
    return rows


def list_dates():
    """返回有数据的日期列表（降序）。"""
    init_db()
    conn = _connect()
    cur = conn.execute("SELECT DISTINCT date FROM ip_daily ORDER BY date DESC")
    out = [r[0] for r in cur.fetchall()]
    conn.close()
    return out


if __name__ == '__main__':
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == 'backfill':
        n = backfill()
        print("backfill done: %d days, %d ip-rows" % n)
    else:
        n = ingest_today()
        print("ingest today: %d ip rows" % n)
    prune_old()
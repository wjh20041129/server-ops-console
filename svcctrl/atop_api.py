# -*- coding: utf-8 -*-
"""
atop 历史负载查询接口（服务控制台「历史时刻表盘」专用）
- /api/atop/dates    → 可用日志日期列表
- /api/atop/summary  → 某天 全天 CPU/内存/磁盘/网络 时间序列
- /api/atop/processes→ 某时刻进程快照

数据源 /var/log/atop/atop_YYYYMMDD（atop 二进制日志）。
解析用 atopsar / atop 子进程输出（列均实测验证）：
  CPU   atopsar -c -x    all 行: %usr %nice %sys %irq %softirq %steal %guest %wait %idle(各核累加)
  内存  atopsar -m -x    memtotal memfree buffers cached ... swptotal swpfree
  磁盘  atopsar -d -x    vda 行: busy read/s KB/read writ/s KB/writ
  网络  atopsar -i -x    eth0/lo 行: busy ipack/s opack/s iKbyte/s oKbyte/s imbps ombps
  进程  atop -r f -b HHMM  PID SYSCPU USRCPU RDELAY VGROW RGROW RDDSK WRDSK CPU CMD
"""
import os
import re
import subprocess
from datetime import datetime
from flask import Blueprint, request, session, jsonify

atop_bp = Blueprint('atop_api', __name__)
ATOP_LOG_DIR = '/var/log/atop'


def _run(args, timeout=90):
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
        return r.stdout or ''
    except Exception:
        return ''


def _lognum(date):
    p = os.path.join(ATOP_LOG_DIR, 'atop_%s' % date)
    return p if os.path.exists(p) else None


def _f(s):
    try:
        return float(str(s).replace('%', '').replace('M', '').replace('G', '').replace('K', '').strip())
    except Exception:
        return 0.0


def _rows(text):
    """atopsar 输出 → [(hh:mm, spaced_tokens), ...]
    时间戳为 HH:MM:SS；sub行(eth0)无时间戳则沿用上一时间。"""
    out = []
    last_t = None
    for line in text.splitlines():
        m = re.match(r'^(\d{2}):(\d{2}):\d{2}\s+', line)
        if m:
            last_t = m.group(1) + ':' + m.group(2)
            rest = line[m.end():].strip()
            out.append((last_t, rest))
        else:
            s = line.strip()
            if last_t and s:
                out.append((last_t, s))
    return out


@atop_bp.route('/api/atop/dates')
def api_atop_dates():
    if not session.get('authed'):
        return jsonify({'error': '未登录'}), 401
    dates = []
    if os.path.isdir(ATOP_LOG_DIR):
        for fn in os.listdir(ATOP_LOG_DIR):
            m = re.match(r'atop_(\d{8})$', fn)
            if m:
                dates.append(m.group(1))
    dates.sort(reverse=True)
    return jsonify({'ok': True, 'dates': dates})


@atop_bp.route('/api/atop/summary')
def api_atop_summary():
    if not session.get('authed'):
        return jsonify({'error': '未登录'}), 401
    date = (request.args.get('date') or '').strip()
    path = _lognum(date)
    if not path:
        return jsonify({'error': '该日期无 atop 日志'}), 404

    ncpu = os.cpu_count() or 1
    cpu_out = _run(['atopsar', '-r', path, '-c', '-x'])
    mem_out = _run(['atopsar', '-r', path, '-m', '-x'])
    dsk_out = _run(['atopsar', '-r', path, '-d', '-x'])
    net_out = _run(['atopsar', '-r', path, '-i', '-x'])

    cpu = {}
    for t, rest in _rows(cpu_out):
        p = rest.split()
        # all 行: all %usr %nice %sys %irq %softirq %steal %guest %wait %idle
        # rest 去掉时间戳后: p[0]=all p[1]=usr p[3]=sys p[8]=wait p[9]=idle
        if p and p[0] == 'all' and len(p) >= 10:
            idle = _f(p[9])
            cpu[t] = round(max(0.0, 100.0 - idle / float(ncpu)), 1)

    mem = {}
    memtotal = None
    for t, rest in _rows(mem_out):
        m = re.match(r'^([\d.]+)M\s+([\d.]+)M', rest)
        if m:
            tot = float(m.group(1))
            free = float(m.group(2))
            if memtotal is None:
                memtotal = tot
            used = tot - free
            pct = round(100.0 * used / tot, 1)
            mem[t] = pct

    disk = {}
    for t, rest in _rows(dsk_out):
        p = rest.split()
        if p and p[0] in ('vda', 'sda', 'nvme0n1') and len(p) >= 5:
            disk[t] = round(_f(p[1]), 1)  # busy%

    neti, neto = {}, {}
    for t, rest in _rows(net_out):
        p = rest.split()
        if p and p[0] == 'eth0' and len(p) >= 6:
            # iKbyte/s=col4? 实测: interf busy ipack/s opack/s iKbyte/s oKbyte/s imbps ombps
            # eth0 行: eth0 ? 1.3 1.1 0 0 0 0  => imbps/ombps 在末尾, iKbyte/okbyte 在 col4/5
            neti[t] = _f(p[4])
            neto[t] = _f(p[5])

    times = sorted(set(cpu) | set(mem) | set(disk) | set(neti) | set(neto))
    points = [{
        'time': t,
        'cpu': cpu.get(t),
        'mem': mem.get(t),
        'disk': disk.get(t),
        'netIn': neti.get(t),
        'netOut': neto.get(t),
    } for t in times]

    # 采样间隔秒（取前两点差）
    step = 600
    if len(times) >= 2:
        try:
            def _m(hm):
                h, mi = map(int, hm.split(':')); return h * 60 + mi
            d = (_m(times[1]) - _m(times[0]) + 1440) % 1440
            if d > 0:
                step = d * 60
        except Exception:
            pass

    return jsonify({'ok': True, 'date': date, 'ncpu': ncpu,
                    'step_seconds': step, 'points': points})


@atop_bp.route('/api/atop/processes')
def api_atop_processes():
    if not session.get('authed'):
        return jsonify({'error': '未登录'}), 401
    date = (request.args.get('date') or '').strip()
    hhmm = (request.args.get('time') or '').strip()
    path = _lognum(date)
    if not path:
        return jsonify({'error': '该日期无 atop 日志'}), 404
    if not re.match(r'^\d{4}$', hhmm):
        return jsonify({'error': '时间格式应为 HHMM'}), 400
    hm = hhmm[:2] + ':' + hhmm[2:]
    out = _run(['atop', '-r', path, '-b', hm])
    procs = []
    for line in out.splitlines():
        s = line.rstrip('\n')
        m = re.match(r'^\s*(\d{3,6})\s+(\S+)\s+(\S+)\s+', s)
        if not m:
            continue
        pid = int(m.group(1))
        cols = s.split()
        # 表头判断
        if cols[0] == 'PID':
            continue
        # 有效进程行需至少：PID SYSCPU USRCPU RDELAY VGROW RGROW RDDSK WRDSK CPU CMD(≥10列)
        if len(cols) < 10:
            continue
        try:
            cpu = _f(cols[8])
        except Exception:
            cpu = 0.0
        cmd = ' '.join(cols[9:]).strip()
        if not cmd or cmd.startswith('<'):
            continue
        procs.append({'pid': pid, 'cpu': cpu, 'cmd': cmd[:120]})
    procs.sort(key=lambda p: -p['cpu'])
    return jsonify({'ok': True, 'time': hhmm, 'procs': procs[:150]})
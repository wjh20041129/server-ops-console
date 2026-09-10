#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""svcctrl IP 历史采集器 —— 由 systemd timer 每 10 分钟触发。
把当天 nginx + journald SSH 数据聚合入库，并清理过期行。"""
import sys
import ip_history

if __name__ == '__main__':
    # 每日首次(00:05 定时)做一次 90 天回填 + 清理；常规 10min 触发只采今天
    if '--backfill' in sys.argv:
        ip_history.backfill()
    n = ip_history.ingest_today()
    pruned = ip_history.prune_old()
    print("ingest today: %d ip rows; pruned %d old rows" % (n, pruned))
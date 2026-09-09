#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
定时抓取 + 更新 heat
每次执行：抓取新闻 → 更新所有 heat 值
"""

import sys
import os
from datetime import datetime

# 添加项目路径
sys.path.insert(0, '/root/exam_system')

def log_message(msg):
    """带时间戳的日志"""
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    print(f"[{timestamp}] {msg}")

def main():
    log_message("="*50)
    log_message("🔄 开始定时任务：抓取新闻 + 更新 heat")
    log_message("="*50)
    
    # 1. 抓取新闻
    log_message("📰 步骤1: 抓取新闻...")
    try:
        from spider import main as crawl_main
        crawl_main()
        log_message("✅ 新闻抓取完成")
    except Exception as e:
        log_message(f"❌ 新闻抓取失败: {e}")
        # 即使抓取失败，也继续更新 heat（让已有新闻分数更新）
    
    # 2. 更新 heat
    log_message("📊 步骤2: 更新 heat 值...")
    try:
        from update_heat import main as heat_main
        heat_main()
        log_message("✅ heat 更新完成")
    except Exception as e:
        log_message(f"❌ heat 更新失败: {e}")
    
    log_message("="*50)
    log_message("✅ 定时任务执行完毕")
    log_message("="*50)

if __name__ == "__main__":
    main()

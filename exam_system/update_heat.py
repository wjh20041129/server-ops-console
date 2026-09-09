#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
每日/开机更新所有新闻的 heat 值
基于当前日期重新计算，无需 AI
"""

import pymysql
from datetime import datetime
import sys
import os

# 添加项目路径到系统路径，以便导入 spider 模块
sys.path.insert(0, '/root/exam_system')

# 从 spider.py 导入 calc_heat 函数
try:
    from spider import calc_heat
except ImportError:
    print("❌ 无法导入 spider.py，请检查路径")
    sys.exit(1)

DB_CONFIG = {
    'host': 'localhost',
    'user': 'root',
    'password': 'YOUR_DB_PASSWORD',
    'database': 'exam_system',
    'charset': 'utf8mb4',
    'cursorclass': pymysql.cursors.DictCursor
}

def update_all_heat():
    """更新所有新闻的 heat 值"""
    try:
        conn = pymysql.connect(**DB_CONFIG)
        cur = conn.cursor()
        
        # 获取所有新闻的 id, source, date
        cur.execute("SELECT id, source, date FROM current_affairs WHERE date IS NOT NULL")
        news_list = cur.fetchall()
        
        if not news_list:
            print("📭 数据库中没有新闻，跳过更新")
            conn.close()
            return 0
        
        updated = 0
        for item in news_list:
            # 获取发布日期
            pub_date = item.get('date')
            if not pub_date:
                continue
            
            # 如果 pub_date 是 date 对象，转为字符串
            if hasattr(pub_date, 'strftime'):
                pub_date_str = pub_date.strftime('%Y-%m-%d')
            else:
                pub_date_str = str(pub_date)
            
            # 重新计算 heat
            new_heat = calc_heat(item['source'], pub_date_str)
            
            # 更新数据库
            cur.execute(
                "UPDATE current_affairs SET heat = %s WHERE id = %s",
                (new_heat, item['id'])
            )
            updated += 1
        
        conn.commit()
        conn.close()
        
        print(f"✅ 已更新 {updated} 条新闻的 heat 值")
        return updated
        
    except pymysql.Error as e:
        print(f"❌ 数据库错误: {e}")
        return 0
    except Exception as e:
        print(f"❌ 更新失败: {e}")
        return 0

def main():
    print("="*50)
    print(f"📊 新闻 Heat 值更新脚本")
    print(f"   执行时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("="*50)
    
    count = update_all_heat()
    
    print("="*50)
    print(f"✅ 完成！共更新 {count} 条新闻")
    print("="*50)
    return count

if __name__ == "__main__":
    main()

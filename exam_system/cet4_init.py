#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
四级刷词模块 - 数据库初始化
创建 cet4_words 表并导入词库
"""
import pymysql
import json
import os

DB_CONFIG = {
    'host': 'localhost',
    'user': 'root',
    'password': 'YOUR_DB_PASSWORD',
    'database': 'exam_system',
    'charset': 'utf8mb4',
    'cursorclass': pymysql.cursors.DictCursor
}

def init_cet4_db():
    conn = pymysql.connect(**DB_CONFIG)
    cursor = conn.cursor()
    
    # 创建单词学习记录表
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS cet4_words (
            id INT AUTO_INCREMENT PRIMARY KEY,
            word VARCHAR(255) NOT NULL UNIQUE,
            phonetic VARCHAR(100) DEFAULT '',
            meaning TEXT,
            status ENUM('pending','review','familiar','ebbinghaus') DEFAULT 'pending',
            group_id INT DEFAULT NULL,
            review_count INT DEFAULT 0,
            ebbinghaus_stage INT DEFAULT 0,
            next_review_time DATETIME DEFAULT NULL,
            last_study_time DATETIME DEFAULT NULL,
            is_ebbinghaus_done TINYINT(1) DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            INDEX idx_status (status),
            INDEX idx_group (group_id),
            INDEX idx_ebbinghaus (ebbinghaus_stage, next_review_time)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """)
    
    # 检查词库是否已导入
    cursor.execute("SELECT COUNT(*) as cnt FROM cet4_words")
    cnt = cursor.fetchone()['cnt']
    
    if cnt == 0:
        # 导入词库
        json_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'cet4.json')
        if os.path.exists(json_path):
            with open(json_path, 'r', encoding='utf-8') as f:
                words = json.load(f)
            for w in words:
                cursor.execute(
                    "INSERT INTO cet4_words (word, phonetic, meaning, status) VALUES (%s, %s, %s, 'pending')",
                    (w['word'], w.get('phonetic', ''), w.get('meaning', ''))
                )
            conn.commit()
            print(f"✅ 已导入 {len(words)} 个四级单词")
        else:
            print("❌ 词库文件 cet4.json 不存在")
    else:
        print(f"ℹ️ 词库已存在，共 {cnt} 个单词")
    
    conn.close()

if __name__ == '__main__':
    init_cet4_db()
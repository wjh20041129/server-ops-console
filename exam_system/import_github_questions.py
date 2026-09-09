#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
导入GitHub开源题库数据到知识题库
"""
import pymysql
import json
import requests

DB_CONFIG = {
    'host': 'localhost',
    'user': 'root',
    'password': 'YOUR_DB_PASSWORD',
    'database': 'exam_system',
    'charset': 'utf8mb4',
    'cursorclass': pymysql.cursors.DictCursor
}

def fetch_questions():
    """从GitHub获取题目数据"""
    url = 'https://raw.githubusercontent.com/opt-sys/gongkao-ai-system/main/question_bank/seed_questions.jsonl'
    resp = requests.get(url, timeout=30)
    resp.raise_for_status()
    
    questions = []
    for line in resp.text.strip().split('\n'):
        if line.strip():
            q = json.loads(line)
            # 只导入选择题
            if q.get('type') in ['single', 'multiple']:
                questions.append(q)
    
    return questions

def map_category(chapter):
    """映射章节到分类"""
    if '常识判断' in chapter:
        return '常识判断'
    elif '言语理解' in chapter:
        return '言语理解'
    elif '数量关系' in chapter:
        return '数量关系'
    elif '判断推理' in chapter:
        return '判断推理'
    elif '资料分析' in chapter:
        return '资料分析'
    else:
        return '常识判断'

def import_questions():
    """导入题目到数据库"""
    print('📥 从GitHub获取题库数据...')
    questions = fetch_questions()
    print(f'✅ 获取到 {len(questions)} 道选择题')
    
    conn = pymysql.connect(**DB_CONFIG)
    cursor = conn.cursor()
    
    saved = 0
    skipped = 0
    for q in questions:
        stem = q.get('stem', '')
        options = q.get('options', [])
        answer = q.get('answer', '')
        analysis = q.get('analysis', '')
        chapter = q.get('chapter', '')
        category = map_category(chapter)
        
        # 检查是否已存在
        cursor.execute("SELECT id FROM knowledge_questions WHERE question = %s", (stem,))
        if cursor.fetchone():
            skipped += 1
            continue
        
        # 清理选项格式
        cleaned_options = []
        for opt in options:
            # 去除 "A. " 这样的前缀
            if len(opt) > 2 and opt[1] == '.':
                cleaned_options.append(opt[3:])
            else:
                cleaned_options.append(opt)
        
        # 插入题目
        cursor.execute("""
            INSERT INTO knowledge_questions 
            (category, subcategory, question, options, answer, explanation, source, difficulty)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        """, (
            category,
            chapter,
            stem,
            json.dumps(cleaned_options, ensure_ascii=False),
            answer,
            analysis,
            'gongkao-ai-system',
            'medium'
        ))
        saved += 1
    
    conn.commit()
    conn.close()
    
    print(f'✅ 成功导入 {saved} 道题目到数据库（跳过 {skipped} 道重复题）')
    
    # 显示统计
    conn = pymysql.connect(**DB_CONFIG)
    cursor = conn.cursor()
    cursor.execute("SELECT category, COUNT(*) as cnt FROM knowledge_questions GROUP BY category")
    stats = cursor.fetchall()
    conn.close()
    
    print('\n📊 题库统计：')
    for s in stats:
        print(f"  - {s['category']}: {s['cnt']} 题")

if __name__ == '__main__':
    import_questions()

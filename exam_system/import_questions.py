#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
导入GitHub题库数据到系统数据库
"""

import json
import pymysql
from datetime import datetime
from batch_generate import get_db_conn

def map_category(chapter):
    """将章节映射到分类"""
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

def import_questions_from_jsonl(jsonl_path):
    """从JSONL文件导入题目"""
    questions = []
    
    with open(jsonl_path, 'r', encoding='utf-8') as f:
        for line in f:
            if line.strip():
                q = json.loads(line)
                questions.append(q)
    
    print(f"读取到 {len(questions)} 道题目")
    
    # 过滤掉申论题（暂时只导入选择题）
    choice_questions = [q for q in questions if q.get('type') in ['single', 'multiple']]
    print(f"其中选择题 {len(choice_questions)} 道")
    
    # 导入数据库
    conn = get_db_conn()
    cursor = conn.cursor()
    
    inserted_count = 0
    for q in choice_questions:
        try:
            category = map_category(q.get('chapter', '常识判断'))
            question_text = q.get('stem', '')
            options = q.get('options', [])
            answer = q.get('answer', '')
            explanation = q.get('analysis', '')
            source = 'github:opt-sys/gongkao-ai-system'
            subcategory = q.get('chapter', '')
            difficulty = 'medium'
            if q.get('difficulty') == 1:
                difficulty = 'easy'
            elif q.get('difficulty') == 3:
                difficulty = 'hard'
            
            # 处理选项格式
            if isinstance(options, list):
                options_text = json.dumps(options, ensure_ascii=False)
            else:
                options_text = json.dumps([], ensure_ascii=False)
            
            # 检查是否已存在
            cursor.execute("""
                SELECT id FROM knowledge_questions 
                WHERE question = %s
            """, (question_text,))
            
            if cursor.fetchone():
                print(f"跳过重复题目: {question_text[:50]}...")
                continue
            
            # 插入题目
            cursor.execute("""
                INSERT INTO knowledge_questions 
                (category, subcategory, question, options, answer, explanation, source, difficulty)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """, (category, subcategory, question_text, options_text, answer, explanation, source, difficulty))
            
            inserted_count += 1
            print(f"导入题目 {inserted_count}: [{category}] {question_text[:50]}...")
            
        except Exception as e:
            print(f"导入题目失败: {e}")
            continue
    
    conn.commit()
    conn.close()
    
    print(f"\n✅ 成功导入 {inserted_count} 道题目")
    
    # 显示统计
    conn = get_db_conn()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT category, COUNT(*) as count 
        FROM knowledge_questions 
        GROUP BY category
    """)
    stats = cursor.fetchall()
    conn.close()
    
    print("\n📊 题库统计:")
    for stat in stats:
        print(f"   {stat['category']}: {stat['count']} 题")

if __name__ == '__main__':
    import sys
    if len(sys.argv) < 2:
        print("用法: python3 import_questions.py <jsonl文件路径>")
        sys.exit(1)
    
    jsonl_path = sys.argv[1]
    import_questions_from_jsonl(jsonl_path)

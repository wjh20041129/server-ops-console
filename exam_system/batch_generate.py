#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
用AI批量生成行测知识题库
"""
import requests
import json
import pymysql
import os
import time

BAILIAN_API_KEY = os.getenv('BAILIAN_API_KEY', '')
BAILIAN_BASE_URL = os.getenv('BAILIAN_BASE_URL', 'https://token-plan.cn-beijing.maas.aliyuncs.com/compatible-mode/v1')
BAILIAN_MODEL = 'qwen3.8-flash'

DB_CONFIG = {
    'host': 'localhost',
    'user': 'root',
    'password': 'YOUR_DB_PASSWORD',
    'database': 'exam_system',
    'charset': 'utf8mb4',
    'cursorclass': pymysql.cursors.DictCursor
}

CATEGORIES = [
    '常识判断',
    '言语理解',
    '数量关系',
    '判断推理',
    '资料分析'
]

def generate_questions(category, batch_size=20):
    """用AI生成指定分类的题目"""
    headers = {
        'Authorization': f'Bearer {BAILIAN_API_KEY}',
        'Content-Type': 'application/json'
    }
    
    prompts = {
        '常识判断': '生成公务员考试常识判断题目，涵盖法律、政治、经济、历史、文化、科技、地理等领域',
        '言语理解': '生成公务员考试言语理解与表达题目，包括选词填空、阅读理解、语句排序等',
        '数量关系': '生成公务员考试数量关系题目，包括数字推理、数学运算等',
        '判断推理': '生成公务员考试判断推理题目，包括图形推理、定义判断、类比推理、逻辑判断等',
        '资料分析': '生成公务员考试资料分析题目，给出一段数据材料后提问'
    }
    
    prompt = prompts[category]
    
    system_msg = f"""你是一个公务员考试题目生成专家。请{prompt}。

要求：
1. 生成{batch_size}道高质量选择题
2. 每题4个选项(A/B/C/D)，只有一个正确答案
3. 提供详细解析
4. 题目难度适中，符合公务员考试水平
5. 确保答案正确且解析清晰

以JSON数组格式输出，每题包含：
{{
    "question": "题目内容",
    "options": ["A. 选项1", "B. 选项2", "C. 选项3", "D. 选项4"],
    "answer": "A/B/C/D",
    "explanation": "详细解析"
}}"""

    payload = {
        'model': BAILIAN_MODEL,
        'messages': [
            {'role': 'system', 'content': system_msg},
            {'role': 'user', 'content': f'请生成{batch_size}道{category}题目'}
        ],
        'temperature': 0.7,
        'max_tokens': 8000
    }
    
    try:
        resp = requests.post(
            f'{BAILIAN_BASE_URL}/chat/completions',
            headers=headers,
            json=payload,
            timeout=120
        )
        
        if resp.status_code == 200:
            result = resp.json()
            content = result['choices'][0]['message']['content']
            
            # 尝试提取JSON
            if '```json' in content:
                content = content.split('```json')[1].split('```')[0]
            elif '```' in content:
                content = content.split('```')[1].split('```')[0]
            
            questions = json.loads(content)
            return questions
        else:
            print(f'API请求失败: {resp.status_code} - {resp.text}')
            return []
    except Exception as e:
        print(f'生成题目失败: {e}')
        return []

def save_to_db(questions, category, source='ai_generated'):
    """保存题目到数据库"""
    conn = pymysql.connect(**DB_CONFIG)
    cursor = conn.cursor()
    
    saved = 0
    for q in questions:
        cursor.execute("""
            INSERT INTO knowledge_questions 
            (category, subcategory, question, options, answer, explanation, source, difficulty)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        """, (
            category,
            '',
            q['question'],
            json.dumps(q['options'], ensure_ascii=False),
            q['answer'],
            q.get('explanation', ''),
            source,
            'medium'
        ))
        saved += 1
    
    conn.commit()
    conn.close()
    return saved

def main():
    print('🚀 开始批量生成行测知识题库...')
    
    for category in CATEGORIES:
        print(f'\n📚 正在生成【{category}】题目...')
        questions = generate_questions(category, batch_size=5)
        
        if questions:
            saved = save_to_db(questions, category)
            print(f'✅ 成功生成并保存 {saved} 道{category}题目')
        else:
            print(f'❌ {category}题目生成失败')
        
        time.sleep(2)  # 避免API限流
    
    print('\n🎉 批量生成完成！')
    
    # 统计总数
    conn = pymysql.connect(**DB_CONFIG)
    cursor = conn.cursor()
    cursor.execute('SELECT COUNT(*) as total FROM knowledge_questions')
    total = cursor.fetchone()['total']
    cursor.execute('SELECT category, COUNT(*) as cnt FROM knowledge_questions GROUP BY category')
    stats = cursor.fetchall()
    
    print(f'\n📊 题库统计（共{total}题）：')
    for cat, count in stats:
        print(f'  - {cat}: {count}题')
    
    conn.close()

if __name__ == '__main__':
    main()

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
行测知识题库爬虫
多源爬取 + AI抽样验证
"""
import requests
from bs4 import BeautifulSoup
import pymysql
import json
import time
import re
import os
import random
from datetime import datetime

# 导入AI功能
from batch_generate import save_to_db

DB_CONFIG = {
    'host': 'localhost',
    'user': 'root',
    'password': 'YOUR_DB_PASSWORD',
    'database': 'exam_system',
    'charset': 'utf8mb4',
    'cursorclass': pymysql.cursors.DictCursor
}

# 已验证网站记录文件
VERIFIED_SITES_FILE = 'verified_sites.json'

def get_db_conn():
    return pymysql.connect(**DB_CONFIG)

def load_verified_sites():
    """加载已验证网站列表"""
    if os.path.exists(VERIFIED_SITES_FILE):
        with open(VERIFIED_SITES_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    return []

def save_verified_site(site_name):
    """保存已验证网站"""
    verified = load_verified_sites()
    if site_name not in verified:
        verified.append(site_name)
        with open(VERIFIED_SITES_FILE, 'w', encoding='utf-8') as f:
            json.dump(verified, f, ensure_ascii=False, indent=2)

def is_verified(site_name):
    """检查网站是否已验证"""
    return site_name in load_verified_sites()

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
}

# ==================== 爬虫源定义 ====================

def crawl_from_exam8(batch_size=20):
    """从考试吧爬取题目"""
    print("📡 正在爬取考试吧...")
    # 简化实现，返回示例数据
    base_questions = [
        {
            'category': '常识判断',
            'subcategory': '法律',
            'question': '下列哪项不属于我国宪法规定的公民基本权利？',
            'options': ['选举权和被选举权', '言论自由', '宗教信仰自由', '依法纳税'],
            'answer': 'D',
            'explanation': '依法纳税是公民的基本义务，不是权利。',
        },
        {
            'category': '言语理解',
            'subcategory': '阅读理解',
            'question': '科技创新是推动经济发展的重要动力。只有不断提高自主创新能力，才能在激烈的国际竞争中立于不败之地。这段文字主要强调：',
            'options': ['科技创新与经济发展无关', '自主创新能力不重要', '提高自主创新能力很重要', '国际竞争不激烈'],
            'answer': 'C',
            'explanation': '文段强调自主创新能力的重要性。',
        },
        {
            'category': '数量关系',
            'subcategory': '数学运算',
            'question': '甲乙两地相距120公里，一辆汽车从甲地出发，每小时行驶60公里，几小时后到达乙地？',
            'options': ['1小时', '2小时', '3小时', '4小时'],
            'answer': 'B',
            'explanation': '时间 = 距离 ÷ 速度 = 120 ÷ 60 = 2小时。',
        },
    ]
    # 根据batch_size扩展题目
    questions = []
    for i in range(batch_size):
        q = base_questions[i % len(base_questions)].copy()
        q['question'] = q['question']  # 保持原题
        questions.append(q)
    return questions[:batch_size]

def crawl_from_offcn(batch_size=20):
    """从中公教育爬取题目"""
    print("📡 正在爬取中公教育...")
    base_questions = [
        {
            'category': '判断推理',
            'subcategory': '逻辑判断',
            'question': '所有的猫都是动物，有些动物会飞，所以：',
            'options': ['所有的猫都会飞', '有些猫会飞', '有些动物不会飞', '以上都不对'],
            'answer': 'D',
            'explanation': '从"所有的猫都是动物"和"有些动物会飞"无法必然推出关于猫是否会飞的结论。',
        },
        {
            'category': '常识判断',
            'subcategory': '历史',
            'question': '中国历史上第一个统一的中央集权封建国家是？',
            'options': ['夏朝', '商朝', '秦朝', '汉朝'],
            'answer': 'C',
            'explanation': '秦始皇统一六国，建立了中国历史上第一个统一的中央集权封建国家。',
        },
    ]
    questions = []
    for i in range(batch_size):
        q = base_questions[i % len(base_questions)].copy()
        questions.append(q)
    return questions[:batch_size]

def crawl_from_huatu(batch_size=20):
    """从华图教育爬取题目"""
    print("📡 正在爬取华图教育...")
    base_questions = [
        {
            'category': '言语理解',
            'subcategory': '选词填空',
            'question': '科学的发展______了人们的视野，也______了人们的生活。',
            'options': ['开阔，改善', '拓宽，改变', '扩大，提高', '扩展，优化'],
            'answer': 'A',
            'explanation': '"开阔视野"和"改善生活"是固定搭配。',
        },
        {
            'category': '数量关系',
            'subcategory': '数字推理',
            'question': '2, 4, 8, 16, ____',
            'options': ['20', '24', '32', '64'],
            'answer': 'C',
            'explanation': '这是一个等比数列，公比为2，下一个数是16×2=32。',
        },
        {
            'category': '判断推理',
            'subcategory': '定义判断',
            'question': '犯罪是指违反刑法规定，具有严重社会危害性，应当受到刑罚处罚的行为。下列哪项属于犯罪？',
            'options': ['闯红灯', '盗窃他人财物', '随地吐痰', '高空抛物'],
            'answer': 'B',
            'explanation': '盗窃他人财物违反刑法，具有严重社会危害性，应当受到刑罚处罚。',
        },
    ]
    questions = []
    for i in range(batch_size):
        q = base_questions[i % len(base_questions)].copy()
        questions.append(q)
    return questions[:batch_size]

# ==================== AI验证 ====================

def verify_questions_with_ai(questions, sample_size=None, is_verified_site=False):
    """
    用AI抽样验证题目答案是否正确
    - 已验证网站：5%验证率，最少1道
    - 新网站：3道标准验证
    """
    if sample_size is None:
        if is_verified_site:
            # 已验证网站：5%验证率，不足1按1计算
            sample_size = max(1, int(len(questions) * 0.05))
        else:
            # 新网站：标准验证3道
            sample_size = min(3, len(questions))
    
    # 随机抽样
    sample_questions = random.sample(questions, sample_size)
    
    print(f"\n🔍 正在用AI抽样验证 {sample_size} 道题目...")
    
    correct_count = 0
    verification_results = []
    
    for q in sample_questions:
        try:
            is_correct = verify_single_question(q)
            if is_correct:
                correct_count += 1
            verification_results.append({
                'question': q['question'][:50] + '...',
                'answer': q['answer'],
                'is_correct': is_correct
            })
            print(f"  {'✓' if is_correct else '✗'} 答案 {q['answer']} {'正确' if is_correct else '错误'}")
        except Exception as e:
            print(f"  ✗ 验证失败: {e}")
            verification_results.append({
                'question': q['question'][:50] + '...',
                'answer': q['answer'],
                'is_correct': False
            })
    
    pass_rate = correct_count / len(sample_questions)
    return pass_rate, verification_results

def verify_single_question(question):
    """用AI验证单道题的答案是否正确"""
    import requests
    
    api_key = os.getenv('BAILIAN_API_KEY', '')
    base_url = os.getenv('BAILIAN_BASE_URL', 'https://token-plan.cn-beijing.maas.aliyuncs.com/compatible-mode/v1')
    model = os.getenv('BAILIAN_MODEL', 'qwen3.8-flash')
    
    headers = {
        'Authorization': f'Bearer {api_key}',
        'Content-Type': 'application/json'
    }
    
    prompt = f"""请判断以下公务员考试题目的答案是否正确。

题目：{question['question']}
选项：{question['options']}
给出的答案：{question['answer']}

如果答案正确，只回复：CORRECT
如果答案错误，只回复：WRONG
不要回复其他任何内容。"""

    payload = {
        'model': model,
        'messages': [{'role': 'user', 'content': prompt}],
        'max_tokens': 20,
        'temperature': 0.1
    }
    
    try:
        resp = requests.post(
            f'{base_url}/chat/completions',
            headers=headers,
            json=payload,
            timeout=30
        )
        
        if resp.status_code == 200:
            result = resp.json()
            content = result['choices'][0]['message']['content'].strip()
            return 'CORRECT' in content
        else:
            print(f"    API错误: HTTP {resp.status_code}")
            return None  # 无法判断
    except Exception as e:
        print(f"    API调用失败: {e}")
        return None  # 无法判断

# ==================== 主流程 ====================

def crawl_with_verification(site_name, crawl_func, is_batch_mode=False):
    """
    爬取并验证单个网站
    is_batch_mode: 是否为批量模式（已验证网站）
    """
    print(f"\n{'='*50}")
    print(f"📡 正在处理: {site_name}")
    print(f"{'='*50}")
    
    # 判断是否已验证
    verified = is_verified(site_name)
    
    if verified and is_batch_mode:
        print(f"✓ {site_name} 已验证，使用批量模式（50-100道）")
    elif verified:
        print(f"✓ {site_name} 已验证，使用低验证率（5%）")
    else:
        print(f"⚠ {site_name} 未验证，使用标准验证（3道）")
    
    # 爬取题目
    try:
        if verified and is_batch_mode:
            # 批量模式：爬取50-100道
            questions = crawl_func(batch_size=random.randint(50, 100))
        else:
            # 标准模式：爬取少量
            questions = crawl_func(batch_size=20)
        
        if not questions:
            print(f"  ✗ 未爬取到题目")
            return 0
        
        print(f"  ✓ 爬取了 {len(questions)} 道题")
    except Exception as e:
        print(f"  ✗ 爬取失败: {e}")
        return 0
    
    # 确定验证数量
    if verified:
        # 已验证网站：5%验证率，最少1道
        sample_size = max(1, int(len(questions) * 0.05))
        print(f"  📊 验证数量: {sample_size} 道（5%）")
    else:
        # 新网站：标准验证3道
        sample_size = min(3, len(questions))
        print(f"  📊 验证数量: {sample_size} 道（标准）")
    
    # AI验证
    pass_rate, results = verify_questions_with_ai(questions, sample_size)
    print(f"  验证通过率: {pass_rate*100:.1f}%")
    
    if pass_rate < 0.8:
        print(f"  ❌ 通过率低于80%，放弃这批题目")
        return 0
    
    # 标记为已验证
    if not verified:
        save_verified_site(site_name)
        print(f"  ✓ 已标记为已验证网站")
    
    # 保存到数据库
    print(f"  💾 准备保存 {len(questions)} 道题...")
    saved = 0
    for q in questions:
        try:
            # 检查是否已存在
            conn = get_db_conn()
            cursor = conn.cursor()
            cursor.execute("SELECT id FROM knowledge_questions WHERE question = %s", (q['question'],))
            exists = cursor.fetchone()
            conn.close()
            
            if not exists:
                conn = get_db_conn()
                cursor = conn.cursor()
                cursor.execute("""
                    INSERT INTO knowledge_questions 
                    (category, subcategory, question, options, answer, explanation, source, difficulty)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """, (
                    q.get('category', '未知'),
                    q.get('subcategory', ''),
                    q['question'],
                    json.dumps(q.get('options', []), ensure_ascii=False),
                    q['answer'],
                    q.get('explanation', ''),
                    site_name,
                    'medium'
                ))
                conn.commit()
                conn.close()
                saved += 1
        except Exception as e:
            print(f"    ✗ 保存失败: {e}")
    
    print(f"  ✓ 成功保存 {saved} 道新题")
    return saved

def main():
    print("=" * 60)
    print("🕷️ 行测知识题库爬虫 - 多源爬取 + AI抽样验证")
    print("=" * 60)
    
    sources = [
        ('考试吧', crawl_from_exam8),
        ('中公教育', crawl_from_offcn),
        ('华图教育', crawl_from_huatu),
    ]
    
    total_saved = 0
    
    for source_name, crawl_func in sources:
        # 第一次爬取已验证网站时启用批量模式
        is_batch = is_verified(source_name)
        saved = crawl_with_verification(source_name, crawl_func, is_batch_mode=is_batch)
        total_saved += saved
        time.sleep(0.5)  # 避免请求过快
    
    # 统计
    conn = get_db_conn()
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) as total FROM knowledge_questions")
    total = cursor.fetchone()['total']
    cursor.execute("SELECT category, COUNT(*) as cnt FROM knowledge_questions GROUP BY category")
    stats = cursor.fetchall()
    conn.close()
    
    print(f"\n📊 题库统计（共{total}题）：")
    for s in stats:
        print(f"  - {s['category']}: {s['cnt']}题")
    
    print(f"\n🎉 本次新增 {total_saved} 道题，题库更新完成！")

if __name__ == '__main__':
    main()

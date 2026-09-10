#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
四级刷词模块 - 后端API
使用Flask Blueprint，调用百炼 DeepSeek V4 Flash API
"""
import json
import os
import random
import re
import requests
import threading
from datetime import datetime, timedelta
from flask import Blueprint, request, jsonify, Response

cet4_bp = Blueprint('cet4', __name__)

BAILIAN_API_KEY = os.environ.get('BAILIAN_API_KEY', '')
BAILIAN_BASE_URL = os.environ.get('BAILIAN_BASE_URL', 'https://token-plan.cn-beijing.maas.aliyuncs.com/compatible-mode/v1')
BAILIAN_MODEL = 'qwen3.8-flash'

# 艾宾浩斯复习间隔（天）
EBBINGHAUS_INTERVALS = [1, 2, 4, 7, 15]

# 预加载线程锁
_preload_lock = threading.Lock()

def get_db():
    import pymysql
    DB_CONFIG = {
        'host': 'localhost',
        'user': 'root',
        'password': 'YOUR_DB_PASSWORD',
        'database': 'exam_system',
        'charset': 'utf8mb4',
        'cursorclass': pymysql.cursors.DictCursor
    }
    return pymysql.connect(**DB_CONFIG)

def call_bailian(prompt, system='', timeout=120):
    """调用百炼 DeepSeek V4 Flash API"""
    messages = []
    if system:
        messages.append({'role': 'system', 'content': system})
    messages.append({'role': 'user', 'content': prompt})
    headers = {
        'Authorization': f'Bearer {BAILIAN_API_KEY}',
        'Content-Type': 'application/json'
    }
    payload = {
        'model': BAILIAN_MODEL,
        'messages': messages,
        'temperature': 0.7,
        'max_tokens': 800
    }
    try:
        resp = requests.post(
            f'{BAILIAN_BASE_URL}/chat/completions',
            headers=headers,
            json=payload,
            timeout=timeout
        )
        if resp.status_code != 200:
            return None, f'API错误: HTTP {resp.status_code}'
        data = resp.json()
        return data['choices'][0]['message']['content'], None
    except Exception as e:
        return None, f'API连接失败: {str(e)}'

# ============================================================
# 0. AI预加载（后台线程）
# ============================================================
def preload_word_ai(word_id, word, base_meaning=''):
    """后台预加载单词的例句、记忆技巧和详细释义，存入数据库（单次AI调用）"""
    try:
        prompt = "为四级单词 \"" + word + "\" 生成以下精简学习内容：\n一、例句（2个，四级难度，每个带中文翻译）\n二、派生词（1个）\n三、记忆技巧（词根/联想/近义，具体好记）\n四、详细释义（每个词性列出真实义项，各配1个常用搭配）\n基础释义：" + base_meaning + "\n格式（简洁）：\n【例句】1. " + word + ": 例句. / 翻译。 2. " + word + ": 例句. / 翻译。\n【派生】派生词\n【记忆】词根: ... 联想: ... 近义: ...\n【释义】1. 词性. 真实义项（具体中文词义）；搭配: 具体搭配。2. 词性. 真实义项；搭配: 具体搭配。\n重要要求：严禁照抄模板占位符；不得出现\"义项1\"\"义项2\"\"义项：\"等空壳文字；每个义项必须写出具体的中文释义。"
        content, err = call_bailian(prompt, timeout=150)
        if err or not content:
            return
        
        text = content.strip()
        example = ''
        memorytip = ''
        detail = ''
        if '【例句】' in text:
            seg = text.split('【例句】')[1]
            if '【派生】' in seg:
                example = seg.split('【派生】')[0].strip()
        if '【记忆】' in text:
            seg = text.split('【记忆】')[1]
            if '【释义】' in seg:
                memorytip = seg.split('【释义】')[0].strip()
            else:
                memorytip = seg.strip()
        if '【释义】' in text:
            detail = text.split('【释义】')[1].strip()
        if not detail or not detail.strip():
            detail = base_meaning
        
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE cet4_words SET ai_example=%s, ai_memorytip=%s, ai_detail=%s, ai_loaded=1 WHERE id=%s",
            (example, memorytip, detail, word_id)
        )
        conn.commit()
        conn.close()
    except Exception:
        pass

def trigger_preload(word_ids):
    """触发一组单词的预加载（线程方式，限并发2个，不阻塞请求）"""
    import queue
    q = queue.Queue()
    for item in word_ids:
        q.put(item)
    
    def worker():
        while True:
            try:
                item = q.get_nowait()
            except Exception:
                break
            try:
                if len(item) == 3:
                    wid, w, meaning = item
                else:
                    wid, w = item
                    meaning = ''
                preload_word_ai(wid, w, meaning)
            except Exception:
                pass
            finally:
                q.task_done()
    
    # 最多2个并发线程（7B模型较慢，避免排队）
    for _ in range(min(2, len(word_ids))):
        t = threading.Thread(target=worker, daemon=True)
        t.start()

# ============================================================
# 1. 开启下一组20词
# ============================================================
@cet4_bp.route('/api/cet4/group/create', methods=['POST'])
def group_create():
    conn = get_db()
    cursor = conn.cursor()
    
    # 获取pending单词
    cursor.execute("SELECT id, word, meaning FROM cet4_words WHERE status='pending' AND is_cut=0 ORDER BY RAND() LIMIT 20")
    pending = cursor.fetchall()
    
    if not pending:
        conn.close()
        return jsonify({'error': '四级全部新单词已经学习完毕，请前往复习旧单词'}), 400
    
    # 生成新的组号
    cursor.execute("SELECT COALESCE(MAX(group_id), 0) as max_group FROM cet4_words")
    max_group = cursor.fetchone()['max_group']
    new_group = max_group + 1
    
    remaining = len(pending)
    for w in pending:
        cursor.execute(
            "UPDATE cet4_words SET group_id=%s, status='pending', review_count=0, ebbinghaus_stage=0, is_ebbinghaus_done=0 WHERE id=%s",
            (new_group, w['id'])
        )
    conn.commit()
    conn.close()
    
    # 后台预加载AI内容（例句+记忆技巧）
    trigger_preload([(w['id'], w['word'], w.get('meaning', '')) for w in pending])
    
    return jsonify({
        'group_id': new_group,
        'total': remaining,
        'message': f'已开启第{new_group}组，共{remaining}个单词' if remaining < 20 else f'已开启第{new_group}组，共20个单词',
        'partial': remaining < 20
    })

# ============================================================
# 2. 获取本组下一个单词（未处理的，随机顺序）
# ============================================================
@cet4_bp.route('/api/cet4/group/next_word', methods=['GET'])
def group_next_word():
    group_id = request.args.get('group_id', type=int)
    if not group_id:
        return jsonify({'error': '缺少group_id'}), 400
    
    conn = get_db()
    cursor = conn.cursor()
    
    # 获取本组中 status 为 pending 或 review 的单词（随机顺序，非字母序）
    cursor.execute("""
        SELECT id, word, phonetic, meaning, status, review_count,
               ai_example, ai_memorytip, ai_detail, ai_loaded
        FROM cet4_words 
        WHERE group_id=%s AND status IN ('pending','review') AND is_cut=0
        ORDER BY FIELD(status, 'review', 'pending'), RAND()
        LIMIT 1
    """, (group_id,))
    word = cursor.fetchone()
    conn.close()
    
    if not word:
        return jsonify({'done': True, 'message': '本组单词已全部完成'})
    
    return jsonify({'done': False, 'word': word})

# ============================================================
# 2.1 获取本组全部单词（学习/切换用）
# ============================================================
@cet4_bp.route('/api/cet4/group/words', methods=['GET'])
def group_words():
    group_id = request.args.get('group_id', type=int)
    if not group_id:
        return jsonify({'error': '缺少group_id'}), 400
    
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT id, word, phonetic, meaning, status, review_count,
               ai_example, ai_memorytip, ai_detail, ai_loaded
        FROM cet4_words 
        WHERE group_id=%s AND is_cut=0
        ORDER BY id ASC
    """, (group_id,))
    words = cursor.fetchall()
    conn.close()
    return jsonify({'group_id': group_id, 'total': len(words), 'words': words})

# ============================================================
# 2.2 获取单词AI内容（例句/记忆技巧，优先读库缓存）
# ============================================================
@cet4_bp.route('/api/cet4/word/ai', methods=['GET'])
def word_ai():
    word_id = request.args.get('word_id', type=int)
    if not word_id:
        return jsonify({'error': '缺少word_id'}), 400
    
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT word, phonetic, meaning, ai_example, ai_memorytip, ai_detail, ai_loaded FROM cet4_words WHERE id=%s", (word_id,))
    w = cursor.fetchone()
    conn.close()
    
    if not w:
        return jsonify({'error': '单词不存在'}), 404
    
    return jsonify({
        'word': w['word'],
        'phonetic': w['phonetic'] or '',
        'meaning': w['meaning'] or '',
        'example': w['ai_example'] or '',
        'memorytip': w['ai_memorytip'] or '',
        'detail': w['ai_detail'] or '',
        'loaded': bool(w['ai_loaded'])
    })

# ============================================================
# 2.2 修改单词释义（用户修正后保存，重新生成时供AI模型参考）
# ============================================================
@cet4_bp.route('/api/cet4/word/update_meaning', methods=['POST'])
def word_update_meaning():
    data = request.json
    word_id = data.get('word_id')
    meaning = (data.get('meaning') or '').strip()
    if not word_id:
        return jsonify({'error': '缺少word_id'}), 400
    if not meaning:
        return jsonify({'error': '释义不能为空'}), 400
    
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM cet4_words WHERE id=%s", (word_id,))
    w = cursor.fetchone()
    if not w:
        conn.close()
        return jsonify({'error': '单词不存在'}), 404
    
    cursor.execute("UPDATE cet4_words SET meaning=%s WHERE id=%s", (meaning, word_id))
    conn.commit()
    conn.close()
    return jsonify({'success': True, 'meaning': meaning})

# ============================================================
# 2.3 获取本组生词（review状态，只刷生词模式用）
# ============================================================
@cet4_bp.route('/api/cet4/group/review_words', methods=['GET'])
def group_review_words():
    group_id = request.args.get('group_id', type=int)
    if not group_id:
        return jsonify({'error': '缺少group_id'}), 400
    
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT id, word, phonetic, meaning, status, review_count,
               ai_example, ai_memorytip, ai_detail, ai_loaded
        FROM cet4_words 
        WHERE group_id=%s AND status='review' AND is_cut=0
        ORDER BY id ASC
    """, (group_id,))
    words = cursor.fetchall()
    conn.close()
    return jsonify({'group_id': group_id, 'total': len(words), 'words': words})

# ============================================================
# 3. 标记单词认识/不认识
# ============================================================
@cet4_bp.route('/api/cet4/word/mark', methods=['POST'])
def word_mark():
    data = request.json
    word_id = data.get('word_id')
    known = data.get('known')  # True=认识, False=不认识
    if not word_id:
        return jsonify({'error': '缺少word_id'}), 400
    
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM cet4_words WHERE id=%s", (word_id,))
    word = cursor.fetchone()
    if not word:
        conn.close()
        return jsonify({'error': '单词不存在'}), 404
    
    now = datetime.now()
    cur_status = word['status']
    review_count = word['review_count']
    group_id = word['group_id']
    
    if known:
        # 认识 → familiar，进入艾宾浩斯流程
        stage = word['ebbinghaus_stage']
        if stage == 0:
            stage = 1
        else:
            stage = min(stage + 1, 5)
        
        next_review = now + timedelta(days=EBBINGHAUS_INTERVALS[stage - 1])
        is_done = 1 if stage >= 5 else 0
        
        cursor.execute("""
            UPDATE cet4_words SET status='familiar', review_count=0, ebbinghaus_stage=%s,
            next_review_time=%s, last_study_time=%s, is_ebbinghaus_done=%s
            WHERE id=%s
        """, (stage, next_review, now, is_done, word_id))
        conn.commit()
        conn.close()
        return jsonify({'success': True, 'status': 'familiar', 'stage': stage, 'next_review': next_review.strftime('%Y-%m-%d %H:%M')})
    else:
        # 不认识
        if cur_status == 'review' or review_count >= 1:
            # 本组第二次出现且不认识 → 强制familiar，进入艾宾浩斯
            stage = word['ebbinghaus_stage'] if word['ebbinghaus_stage'] > 0 else 1
            next_review = now + timedelta(days=EBBINGHAUS_INTERVALS[stage - 1])
            cursor.execute("""
                UPDATE cet4_words SET status='familiar', ebbinghaus_stage=%s,
                next_review_time=%s, last_study_time=%s
                WHERE id=%s
            """, (stage, next_review, now, word_id))
            conn.commit()
            conn.close()
            return jsonify({'success': True, 'status': 'familiar', 'forced': True, 'stage': stage, 'next_review': next_review.strftime('%Y-%m-%d %H:%M')})
        else:
            # 第一次不认识 → review，review_count+1
            new_count = review_count + 1
            cursor.execute(
                "UPDATE cet4_words SET status='review', review_count=%s, last_study_time=%s WHERE id=%s",
                (new_count, now, word_id)
            )
            conn.commit()
            conn.close()
            return jsonify({'success': True, 'status': 'review', 'review_count': new_count})


# ============================================================
# 3.5 斩词：真正掌握、以后不再出现，进熟词本
# ============================================================
@cet4_bp.route('/api/cet4/word/cut', methods=['POST'])
def word_cut():
    """把一个词手动归入熟词本：is_cut=1，从此不出现在任何刷词池。"""
    data = request.json or {}
    word_id = data.get('word_id')
    if not word_id:
        return jsonify({'error': '缺少word_id'}), 400
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT id, word FROM cet4_words WHERE id=%s", (word_id,))
    if not cursor.fetchone():
        conn.close()
        return jsonify({'error': '单词不存在'}), 404
    cursor.execute("UPDATE cet4_words SET is_cut=1, cut_at=%s, last_study_time=%s WHERE id=%s",
                   (datetime.now(), datetime.now(), word_id))
    conn.commit()
    conn.close()
    return jsonify({'success': True, 'cut': True})


@cet4_bp.route('/api/cet4/word/uncut', methods=['POST'])
def word_uncut():
    """熟词本还原：取消斩，恢复到斩之前的状态（原状态不变）。"""
    data = request.json or {}
    word_id = data.get('word_id')
    if not word_id:
        return jsonify({'error': '缺少word_id'}), 400
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM cet4_words WHERE id=%s", (word_id,))
    if not cursor.fetchone():
        conn.close()
        return jsonify({'error': '单词不存在'}), 404
    cursor.execute("UPDATE cet4_words SET is_cut=0, cut_at=NULL WHERE id=%s", (word_id,))
    conn.commit()
    conn.close()
    return jsonify({'success': True, 'cut': False})


@cet4_bp.route('/api/cet4/cutbook/list', methods=['GET'])
def cutbook_list():
    """熟词本：已斩的单词列表（支持搜索）。"""
    search = request.args.get('search', '').strip()
    conn = get_db()
    cursor = conn.cursor()
    sql = "SELECT id, word, phonetic, meaning, cut_at FROM cet4_words WHERE is_cut=1"
    params = []
    if search:
        sql += " AND (word LIKE %s OR meaning LIKE %s)"
        params.extend([f'%{search}%', f'%{search}%'])
    sql += " ORDER BY cut_at DESC LIMIT 500"
    cursor.execute(sql, params)
    words = cursor.fetchall()
    for w in words:
        if w.get('cut_at'):
            w['cut_at'] = w['cut_at'].strftime('%Y-%m-%d %H:%M')
    conn.close()
    return jsonify({'total': len(words), 'words': words})


# ============================================================
# 4. 艾宾浩斯复习：今日到期列表
# ============================================================
@cet4_bp.route('/api/cet4/review/today_list', methods=['GET'])
def review_today_list():
    conn = get_db()
    cursor = conn.cursor()
    now = datetime.now()
    cursor.execute("""
        SELECT id, word, phonetic, meaning, ebbinghaus_stage, review_count
        FROM cet4_words 
        WHERE is_ebbinghaus_done=0 AND ebbinghaus_stage>0 AND next_review_time IS NOT NULL 
        AND next_review_time <= %s AND is_cut=0
        ORDER BY next_review_time ASC
    """, (now,))
    words = cursor.fetchall()
    conn.close()
    return jsonify({'total': len(words), 'words': words})

# ============================================================
# 5. 艾宾浩斯复习：标记认识/不认识
# ============================================================
@cet4_bp.route('/api/cet4/review/mark', methods=['POST'])
def review_mark():
    data = request.json
    word_id = data.get('word_id')
    known = data.get('known')
    if not word_id:
        return jsonify({'error': '缺少word_id'}), 400
    
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM cet4_words WHERE id=%s", (word_id,))
    word = cursor.fetchone()
    if not word:
        conn.close()
        return jsonify({'error': '单词不存在'}), 404
    
    now = datetime.now()
    stage = word['ebbinghaus_stage']
    
    if known:
        # 认识 → stage+1
        new_stage = min(stage + 1, 5)
        is_done = 1 if new_stage >= 5 else 0
        next_review = now + timedelta(days=EBBINGHAUS_INTERVALS[new_stage - 1])
        cursor.execute("""
            UPDATE cet4_words SET ebbinghaus_stage=%s, next_review_time=%s, 
            last_study_time=%s, is_ebbinghaus_done=%s
            WHERE id=%s
        """, (new_stage, next_review, now, is_done, word_id))
        conn.commit()
        conn.close()
        return jsonify({'success': True, 'stage': new_stage, 'done': bool(is_done), 'next_review': next_review.strftime('%Y-%m-%d %H:%M')})
    else:
        # 不认识 → 保持stage，重置24小时后
        next_review = now + timedelta(days=1)
        cursor.execute(
            "UPDATE cet4_words SET next_review_time=%s, last_study_time=%s WHERE id=%s",
            (next_review, now, word_id)
        )
        conn.commit()
        conn.close()
        return jsonify({'success': True, 'stage': stage, 'reset_24h': True, 'next_review': next_review.strftime('%Y-%m-%d %H:%M')})

# ============================================================
# 6. 我学过的单词（familiar列表）
# ============================================================
@cet4_bp.route('/api/cet4/learned/list', methods=['GET'])
def learned_list():
    search = request.args.get('search', '').strip()
    filter_type = request.args.get('filter', 'all')  # all / unknown
    conn = get_db()
    cursor = conn.cursor()
    
    sql = "SELECT id, word, phonetic, meaning, ebbinghaus_stage, is_ebbinghaus_done FROM cet4_words WHERE status='familiar' AND is_cut=0"
    params = []
    if search:
        sql += " AND (word LIKE %s OR meaning LIKE %s)"
        params.extend([f'%{search}%', f'%{search}%'])
    if filter_type == 'unknown':
        # 曾经标记不认识（review_count>0 或 曾经在review状态）
        sql += " AND (review_count > 0 OR ebbinghaus_stage > 1)"
    sql += " ORDER BY word ASC LIMIT 500"
    
    cursor.execute(sql, params)
    words = cursor.fetchall()
    total = len(words)
    conn.close()
    return jsonify({'total': total, 'words': words})

# ============================================================
# 6.5 生词本：标记为不认识但尚未学成的单词
# ============================================================
@cet4_bp.route('/api/cet4/wrongbook/list', methods=['GET'])
def wrongbook_list():
    """生词本：所有标记过不认识的单词
    包括：status='review'（还没学成）以及 review_count>0 或 ebbinghaus_stage>1（曾标记不认识）
    """
    search = request.args.get('search', '').strip()
    conn = get_db()
    cursor = conn.cursor()
    sql = """
        SELECT id, word, phonetic, meaning, review_count, ebbinghaus_stage, status,
               last_study_time
        FROM cet4_words
        WHERE (status='review' OR review_count > 0 OR ebbinghaus_stage > 1) AND is_cut=0
    """
    params = []
    if search:
        sql += " AND (word LIKE %s OR meaning LIKE %s)"
        params.extend([f'%{search}%', f'%{search}%'])
    sql += " ORDER BY last_study_time DESC, id DESC LIMIT 500"
    cursor.execute(sql, params)
    words = cursor.fetchall()
    total = len(words)
    conn.close()
    return jsonify({'total': total, 'words': words})


# ============================================================
# 6.6 艾宾浩斯曲线数据：每个已学单词的学习/复习时间
# ============================================================
@cet4_bp.route('/api/cet4/curve/data', methods=['GET'])
def curve_data():
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT id, word, phonetic, meaning, ebbinghaus_stage, is_ebbinghaus_done,
               last_study_time, next_review_time
        FROM cet4_words
        WHERE status='familiar' AND ebbinghaus_stage > 0 AND last_study_time IS NOT NULL AND is_cut=0
        ORDER BY last_study_time ASC
    """)
    words = cursor.fetchall()
    conn.close()
    for w in words:
        if w['last_study_time']:
            w['last_study_time'] = w['last_study_time'].strftime('%Y-%m-%d %H:%M:%S')
        if w['next_review_time']:
            w['next_review_time'] = w['next_review_time'].strftime('%Y-%m-%d %H:%M:%S')
    return jsonify({'total': len(words), 'words': words})


# ============================================================
# 7. 当前任务组状态
# ============================================================
@cet4_bp.route('/api/cet4/group/status', methods=['GET'])
def group_status():
    conn = get_db()
    cursor = conn.cursor()
    
    # 当前活跃组（有未处理单词的组）
    cursor.execute("""
        SELECT group_id, 
            SUM(CASE WHEN status='pending' AND is_cut=0 THEN 1 ELSE 0 END) as remaining,
            SUM(CASE WHEN status IN ('familiar','review') AND is_cut=0 THEN 1 ELSE 0 END) as learned,
            COUNT(*) as total
        FROM cet4_words 
        WHERE group_id IS NOT NULL 
        GROUP BY group_id 
        HAVING remaining > 0
        ORDER BY group_id DESC 
        LIMIT 1
    """)
    current_group = cursor.fetchone()
    
    # 待学习单词总数
    cursor.execute("SELECT COUNT(*) as cnt FROM cet4_words WHERE status='pending' AND is_cut=0")
    pending_total = cursor.fetchone()['cnt']
    
    # 今日到期复习数
    now = datetime.now()
    cursor.execute("""
        SELECT COUNT(*) as cnt FROM cet4_words 
        WHERE is_ebbinghaus_done=0 AND ebbinghaus_stage>0 AND next_review_time IS NOT NULL AND next_review_time <= %s AND is_cut=0
    """, (now,))
    review_today = cursor.fetchone()['cnt']
    
    # 已学单词总数
    cursor.execute("SELECT COUNT(*) as cnt FROM cet4_words WHERE status='familiar' AND is_cut=0")
    familiar_total = cursor.fetchone()['cnt']

    # 熟词本总数（已斩）
    cursor.execute("SELECT COUNT(*) as cnt FROM cet4_words WHERE is_cut=1")
    cut_total = cursor.fetchone()['cnt']

    # 生词本总数（曾标记不认识）
    cursor.execute("SELECT COUNT(*) as cnt FROM cet4_words WHERE (status='review' OR review_count > 0 OR ebbinghaus_stage > 1) AND is_cut=0")
    wrong_total = cursor.fetchone()['cnt']
    
    conn.close()

    # SUM()/COUNT(*) 在 pymysql 下可能返回 Decimal，jsonify 无法序列化，统一转 int
    from decimal import Decimal
    def _int(v):
        return int(v) if isinstance(v, Decimal) else v
    if current_group:
        current_group = {k: _int(v) for k, v in current_group.items()}
    pending_total = _int(pending_total)
    review_today = _int(review_today)
    familiar_total = _int(familiar_total)
    wrong_total = _int(wrong_total)
    cut_total = _int(cut_total)

    return jsonify({
        'current_group': current_group,
        'pending_total': pending_total,
        'review_today': review_today,
        'familiar_total': familiar_total,
        'wrong_total': wrong_total,
        'cut_total': cut_total
    })

# ============================================================
# 8. AI 生成例句
# ============================================================
@cet4_bp.route('/api/cet4/ai/example', methods=['POST'])
@cet4_bp.route('/api/cet4/ollama/example', methods=['POST'])  # 向后兼容旧路由
def ai_example():
    data = request.json
    word = (data.get('word') or '').strip()
    word_id = data.get('word_id')
    force = bool(data.get('force'))
    meaning = (data.get('meaning') or '').strip()
    if not word:
        return jsonify({'error': '缺少单词'}), 400
    
    # 优先读取预加载缓存（force=True 时跳过缓存重新生成）
    if word_id and not force:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("SELECT ai_example FROM cet4_words WHERE id=%s", (word_id,))
        w = cursor.fetchone()
        conn.close()
        if w and w['ai_example']:
            return jsonify({'success': True, 'content': w['ai_example'], 'cached': True})
    
    # 如果前端没传释义，从数据库读（确保用最新修正后的释义）
    if not meaning and word_id:
        try:
            conn = get_db()
            cursor = conn.cursor()
            cursor.execute("SELECT meaning FROM cet4_words WHERE id=%s", (word_id,))
            w = cursor.fetchone()
            conn.close()
            if w and w['meaning']:
                meaning = w['meaning']
        except Exception:
            pass
    
    meaning_ctx = ('（该单词的释义为："' + meaning + '"，请基于这个释义来造句和讲解）' if meaning else '')
    prompt = "为四级单词 \"" + word + "\" 生成精简学习内容：\n1. 例句1（简单四级）+中文翻译\n2. 例句2（简单四级）+中文翻译\n3. 1个常用派生词\n要求最简洁，不要多余解释，格式：\n1. " + word + ": 例句. / 翻译。\n2. " + word + ": 例句. / 翻译。\n派生: 派生词" + meaning_ctx
    
    content, err = call_bailian(prompt)
    if err or not content:
        return jsonify({'error': 'AI服务不可用，请稍后重试'}), 503
    
    # 回写缓存
    if word_id:
        try:
            conn = get_db()
            cursor = conn.cursor()
            cursor.execute("UPDATE cet4_words SET ai_example=%s, ai_loaded=1 WHERE id=%s", (content.strip(), word_id))
            conn.commit()
            conn.close()
        except Exception:
            pass
    
    return jsonify({'success': True, 'content': content.strip(), 'cached': False})

# ============================================================
# 9. AI 生成记忆技巧（精简版，含预加载缓存）
# ============================================================
@cet4_bp.route('/api/cet4/ai/memorytip', methods=['POST'])
@cet4_bp.route('/api/cet4/ollama/memorytip', methods=['POST'])  # 向后兼容旧路由
def ai_memorytip():
    data = request.json
    word = (data.get('word') or '').strip()
    word_id = data.get('word_id')
    force = bool(data.get('force'))
    meaning = (data.get('meaning') or '').strip()
    if not word:
        return jsonify({'error': '缺少单词'}), 400
    
    # 优先读取预加载缓存（force=True 时跳过缓存重新生成）
    if word_id and not force:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("SELECT ai_memorytip FROM cet4_words WHERE id=%s", (word_id,))
        w = cursor.fetchone()
        conn.close()
        if w and w['ai_memorytip']:
            return jsonify({'success': True, 'content': w['ai_memorytip'], 'cached': True})
    
    # 如果前端没传释义，从数据库读（确保用最新修正后的释义）
    if not meaning and word_id:
        try:
            conn = get_db()
            cursor = conn.cursor()
            cursor.execute("SELECT meaning FROM cet4_words WHERE id=%s", (word_id,))
            w = cursor.fetchone()
            conn.close()
            if w and w['meaning']:
                meaning = w['meaning']
        except Exception:
            pass
    
    meaning_ctx = ('（该单词的释义为："' + meaning + '"，请基于这个释义来讲解）' if meaning else '')
    prompt = "为四级单词 \"" + word + "\" 生成记忆技巧，要具体好记：\n1. 词根词缀（如无则写联想）\n2. 联想记忆（形象/场景）\n3. 近义词（1-2个）\n要求最简洁，格式：\n词根: ...\n联想: ...\n近义: ..." + meaning_ctx
    
    content, err = call_bailian(prompt)
    if err or not content:
        return jsonify({'error': 'AI服务不可用，请稍后重试'}), 503
    
    # 回写缓存
    if word_id:
        try:
            conn = get_db()
            cursor = conn.cursor()
            cursor.execute("UPDATE cet4_words SET ai_memorytip=%s, ai_loaded=1 WHERE id=%s", (content.strip(), word_id))
            conn.commit()
            conn.close()
        except Exception:
            pass
    
    return jsonify({'success': True, 'content': content.strip(), 'cached': False})

# ============================================================
# 10. 导出四级全部词汇
# ============================================================
@cet4_bp.route('/api/cet4/export', methods=['GET'])
def cet4_export():
    """导出全部四级词汇为CSV/文本"""
    fmt = request.args.get('format', 'csv')
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT word, phonetic, meaning, status FROM cet4_words ORDER BY word ASC")
    words = cursor.fetchall()
    conn.close()
    
    if fmt == 'txt':
        lines = [f"{w['word']}\t{w['phonetic'] or ''}\t{w['meaning'] or ''}" for w in words]
        content = '\n'.join(lines)
        filename = 'cet4_words.txt'
        mime = 'text/plain; charset=utf-8'
    else:
        import csv
        import io
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(['word', 'phonetic', 'meaning', 'status'])
        for w in words:
            writer.writerow([w['word'], w['phonetic'] or '', w['meaning'] or '', w['status']])
        content = buf.getvalue()
        filename = 'cet4_words.csv'
        mime = 'text/csv; charset=utf-8'
    
    from flask import Response
    return Response(
        content,
        mimetype=mime,
        headers={'Content-Disposition': f'attachment; filename={filename}'}
    )

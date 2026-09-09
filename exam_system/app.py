#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
import pymysql
import json
from flask import Flask, request, jsonify, send_file, render_template
from flask_cors import CORS
from cet4_api import cet4_bp
from werkzeug.utils import secure_filename
import requests
from datetime import datetime, timedelta
import time
import re
import base64
import glob
import uuid
import random
import psutil
import platform
import traceback

# ==================== 环境变量 ====================
BAILIAN_API_KEY = os.environ.get('BAILIAN_API_KEY', '')
BAILIAN_BASE_URL = os.environ.get('BAILIAN_BASE_URL', 'https://token-plan.cn-beijing.maas.aliyuncs.com/compatible-mode/v1')
BAILIAN_MODEL = os.environ.get('BAILIAN_MODEL', 'qwen3.8-flash')

# ==================== 全局配置 ====================
DEFAULT_THINKING_BUDGET = 800

# ==================== 对话历史存储 ====================
chat_sessions = {}

# ==================== Flask 应用 ====================
app = Flask(__name__)
CORS(app)
app.register_blueprint(cet4_bp)

app.config['UPLOAD_FOLDER'] = 'uploads'
app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024
app.config['SECRET_KEY'] = 'exam-system-secret-key-2026'

DB_CONFIG = {
    'host': 'localhost',
    'user': 'root',
    'password': 'YOUR_DB_PASSWORD',
    'database': 'exam_system',
    'charset': 'utf8mb4',
    'cursorclass': pymysql.cursors.DictCursor
}

def get_db_connection():
    return pymysql.connect(**DB_CONFIG)

def init_db():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""CREATE TABLE IF NOT EXISTS notes (
        id INT AUTO_INCREMENT PRIMARY KEY,
        title VARCHAR(255) NOT NULL,
        content TEXT,
        type VARCHAR(20) DEFAULT 'xingce',
        category VARCHAR(50) DEFAULT '通用',
        status VARCHAR(20) DEFAULT '待批改',
        score INT DEFAULT NULL,
        feedback TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        INDEX idx_type (type)
    )""")
    cursor.execute("""CREATE TABLE IF NOT EXISTS xingce_submissions (
        id INT AUTO_INCREMENT PRIMARY KEY,
        title VARCHAR(255) NOT NULL,
        content TEXT,
        category VARCHAR(50) DEFAULT '言语理解',
        status VARCHAR(20) DEFAULT '待批改',
        score INT DEFAULT NULL,
        feedback TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
    )""")
    cursor.execute("""CREATE TABLE IF NOT EXISTS shenlun_submissions (
        id INT AUTO_INCREMENT PRIMARY KEY,
        title VARCHAR(255) NOT NULL,
        material TEXT,
        answer TEXT,
        category VARCHAR(50) DEFAULT '归纳概括',
        status VARCHAR(20) DEFAULT '待批改',
        score INT DEFAULT NULL,
        feedback TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
    )""")
    cursor.execute("""CREATE TABLE IF NOT EXISTS current_affairs (
        id INT AUTO_INCREMENT PRIMARY KEY,
        title VARCHAR(500) NOT NULL,
        content TEXT,
        source VARCHAR(100),
        date DATE,
        tags VARCHAR(200),
        importance VARCHAR(20) DEFAULT 'low',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        UNIQUE KEY unique_title (title(255))
    )""")
    cursor.execute("""CREATE TABLE IF NOT EXISTS repository (
        id INT AUTO_INCREMENT PRIMARY KEY,
        file_name VARCHAR(255) NOT NULL,
        file_path VARCHAR(500) NOT NULL,
        file_size INT DEFAULT 0,
        category VARCHAR(50) DEFAULT '其他',
        description TEXT,
        answer TEXT,
        ai_explanation TEXT,
        download_count INT DEFAULT 0,
        group_id VARCHAR(50) DEFAULT NULL,
        uploaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        INDEX idx_group_id (group_id)
    )""")
    # 检查并添加 ai_explanation 字段
    cursor.execute("SHOW COLUMNS FROM repository LIKE 'ai_explanation'")
    if not cursor.fetchone():
        cursor.execute("ALTER TABLE repository ADD COLUMN ai_explanation TEXT")
    cursor.execute("SHOW COLUMNS FROM repository LIKE 'answer'")
    if not cursor.fetchone():
        cursor.execute("ALTER TABLE repository ADD COLUMN answer TEXT")
    cursor.execute("SHOW COLUMNS FROM repository LIKE 'group_id'")
    if not cursor.fetchone():
        cursor.execute("ALTER TABLE repository ADD COLUMN group_id VARCHAR(50) DEFAULT NULL")
        cursor.execute("ALTER TABLE repository ADD INDEX idx_group_id (group_id)")
    cursor.execute("SHOW COLUMNS FROM repository LIKE 'uploaded_at'")
    if not cursor.fetchone():
        cursor.execute("ALTER TABLE repository ADD COLUMN uploaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP")

    cursor.execute("""CREATE TABLE IF NOT EXISTS ai_conversations (
        id INT AUTO_INCREMENT PRIMARY KEY,
        session_id VARCHAR(100) NOT NULL,
        user_message TEXT,
        ai_response TEXT,
        model VARCHAR(50) DEFAULT 'qwen3.8-flash',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        INDEX idx_session_id (session_id)
    )""")
    cursor.execute("""CREATE TABLE IF NOT EXISTS favorites (
        id INT AUTO_INCREMENT PRIMARY KEY,
        session_id VARCHAR(100) NOT NULL,
        item_type ENUM('affair', 'repository') NOT NULL,
        item_id INT NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        UNIQUE KEY unique_favorite (session_id, item_type, item_id),
        INDEX idx_session_id (session_id)
    )""")
    cursor.execute("""CREATE TABLE IF NOT EXISTS daily_questions (
        id INT AUTO_INCREMENT PRIMARY KEY,
        news_id INT,
        question TEXT NOT NULL,
        options TEXT NOT NULL,
        answer VARCHAR(10) NOT NULL,
        explanation TEXT,
        source_url VARCHAR(500),
        confidence INT DEFAULT 0,
        question_date DATE NOT NULL,
        category VARCHAR(50) DEFAULT '时政',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        INDEX idx_date (question_date)
    )""")
    cursor.execute("""CREATE TABLE IF NOT EXISTS user_answers (
        id INT AUTO_INCREMENT PRIMARY KEY,
        session_id VARCHAR(100) NOT NULL,
        question_id INT NOT NULL,
        user_answer VARCHAR(10),
        is_correct BOOLEAN DEFAULT FALSE,
        answered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        UNIQUE KEY unique_answer (session_id, question_id),
        INDEX idx_session (session_id)
    )""")
    cursor.execute("""CREATE TABLE IF NOT EXISTS wrong_answers (
        id INT AUTO_INCREMENT PRIMARY KEY,
        session_id VARCHAR(100) NOT NULL,
        question_id INT NOT NULL,
        user_answer VARCHAR(10),
        correct_answer VARCHAR(10),
        reviewed BOOLEAN DEFAULT FALSE,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        INDEX idx_session (session_id)
    )""")
    cursor.execute("""CREATE TABLE IF NOT EXISTS knowledge_questions (
        id INT AUTO_INCREMENT PRIMARY KEY,
        category VARCHAR(50) NOT NULL,
        subcategory VARCHAR(50) DEFAULT '',
        question TEXT NOT NULL,
        options TEXT NOT NULL,
        answer VARCHAR(10) NOT NULL,
        explanation TEXT,
        source VARCHAR(100) DEFAULT 'unknown',
        source_url VARCHAR(500),
        difficulty VARCHAR(20) DEFAULT 'medium',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        INDEX idx_category (category),
        INDEX idx_source (source)
    )""")
    cursor.execute("""CREATE TABLE IF NOT EXISTS knowledge_answers (
        id INT AUTO_INCREMENT PRIMARY KEY,
        question_id INT NOT NULL,
        user_answer VARCHAR(10),
        is_correct BOOLEAN DEFAULT FALSE,
        answered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        INDEX idx_question (question_id)
    )""")
    cursor.execute("""CREATE TABLE IF NOT EXISTS knowledge_wrong (
        id INT AUTO_INCREMENT PRIMARY KEY,
        question_id INT NOT NULL,
        user_answer VARCHAR(10),
        correct_answer VARCHAR(10),
        reviewed BOOLEAN DEFAULT FALSE,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        INDEX idx_question (question_id)
    )""")
    conn.commit()
    conn.close()
    print("✅ 数据库表初始化完成")

def get_session_id():
    session_id = request.headers.get('X-Session-ID')
    if not session_id:
        session_id = 'default'
    return session_id

def compute_affair_score(title, source, date, importance):
    score = 0
    if importance == 'high':
        score += 30
    elif importance == 'medium':
        score += 20
    else:
        score += 10
    keywords_high = ['习近平', '总书记', '国家主席', '全国两会', '二十大', '全会', '中央政治局', '国务院']
    keywords_medium = ['经济', '民生', '改革', '法治', '科技', '教育', '医疗', '乡村振兴', '生态', '粮食']
    keywords_low = ['国际', '外交', '文化', '体育', '旅游', '娱乐']
    for kw in keywords_high:
        if kw in title:
            score += 15
            break
    for kw in keywords_medium:
        if kw in title:
            score += 8
            break
    for kw in keywords_low:
        if kw in title:
            score += 3
            break
    source_high = ['中国政府网', '求是网', '人民网']
    source_medium = ['新华网', '央视网']
    if source in source_high:
        score += 12
    elif source in source_medium:
        score += 7
    else:
        score += 3
    if date:
        try:
            days_ago = (datetime.now().date() - date).days
            if days_ago <= 7:
                score += 8
            elif days_ago <= 30:
                score += 5
        except:
            pass
    if len(title) < 5:
        score -= 5
    elif len(title) > 80:
        score -= 3
    score = max(0, min(100, score))
    return score

def get_affair_score_color(score):
    if score < 40:
        return '#9ca3af'
    elif score < 60:
        return '#3b82f6'
    elif score < 80:
        return '#f59e0b'
    else:
        return '#dc2626'

quiz_state = {}

def get_quiz_question(session_id, index):
    state = quiz_state.get(session_id)
    if not state or index >= len(state['group_ids']):
        return None
    group_id = state['group_ids'][index]
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM repository WHERE group_id = %s", (group_id,))
    files = cursor.fetchall()
    conn.close()
    if not files:
        return None
    first = files[0]
    title = first.get('description', '') or first['file_name'].replace('_'+first['file_name'].split('_')[-1], '')
    images = []
    text_content = ''
    answer = ''  # 标准答案，用于后台，不暴露给前端
    ai_explanation = ''
    for f in files:
        ext = f['file_name'].split('.')[-1].lower()
        if ext in ['jpg', 'jpeg', 'png', 'gif', 'webp', 'bmp']:
            images.append({
                'id': f['id'],
                'file_name': f['file_name'],
                'url': f'/api/repository/download/{f["id"]}?inline=true'
            })
        elif ext == 'txt':
            file_path = os.path.join(app.config['UPLOAD_FOLDER'], f['file_path'])
            if os.path.exists(file_path):
                try:
                    with open(file_path, 'r', encoding='utf-8') as fp:
                        text_content = fp.read()
                except:
                    pass
        if f.get('answer'):
            answer = f['answer']  # 读取标准答案，但不发送到前端
        if f.get('ai_explanation'):
            ai_explanation = f['ai_explanation']
    return {
        'group_id': group_id,
        'title': title,
        'text': text_content,
        'images': images,
        # 不返回 answer，避免前端显示
        'ai_explanation': ai_explanation,
        'total': len(state['group_ids']),
        'index': index + 1
    }

@app.route('/')
def index():
    return render_template('index.html')

# ============================================================
# 仓库文件 API
# ============================================================
@app.route('/api/repository', methods=['GET'])
def get_repository():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM repository ORDER BY group_id DESC, uploaded_at DESC")
    result = cursor.fetchall()
    conn.close()
    return jsonify(result)

def ensure_repo_role_column():
    """幂等: repository.role 列（'answer'=答案/笔记附件）。"""
    try:
        conn = get_db_connection(); cur = conn.cursor()
        cur.execute("SELECT COUNT(*) c FROM information_schema.columns WHERE table_schema=DATABASE() AND table_name='repository' AND column_name='role'")
        row = cur.fetchone()
        has = (row['c'] if isinstance(row, dict) else row[0]) or 0
        if not has:
            cur.execute("ALTER TABLE repository ADD COLUMN role VARCHAR(20) NULL DEFAULT NULL")
            conn.commit()
            print('[schema] repository.role added')
        conn.close()
    except Exception as e:
        print('[schema] ensure role column failed:', e)

ensure_repo_role_column()

@app.route('/api/repository', methods=['POST'])
def upload_file():
    print(f"📥 收到上传请求")
    if 'file' in request.files:
        files = [request.files['file']]
    else:
        files = request.files.getlist('files')
    if not files or len(files) == 0 or files[0].filename == '':
        return jsonify({'error': '请选择文件'}), 400

    group_id = request.form.get('group_id', None)
    if not group_id:
        group_id = f"g_{datetime.now().strftime('%Y%m%d%H%M%S')}_{uuid.uuid4().hex[:6]}"
    category = request.form.get('category', '其他')
    description = request.form.get('description', '')
    answer = request.form.get('answer', '')
    role = request.form.get('role', '') or None
    uploaded = []

    conn = get_db_connection()
    cursor = conn.cursor()

    for file in files:
        if file.filename == '':
            continue
        filename = secure_filename(file.filename)
        name_parts = filename.rsplit('.', 1)
        if len(name_parts) == 2:
            filename = f"{name_parts[0]}_{int(time.time())}.{name_parts[1]}"
        else:
            filename = f"{filename}_{int(time.time())}"
        file_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        file.save(file_path)
        file_size = os.path.getsize(file_path)
        cursor.execute("""INSERT INTO repository
            (file_name, file_path, file_size, category, description, answer, group_id, role)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
            (file.filename, filename, file_size, category, description, answer, group_id, role))
        uploaded.append(file.filename)

    conn.commit()
    conn.close()
    return jsonify({'message': f'上传成功 {len(uploaded)} 个文件', 'files': uploaded, 'group_id': group_id})

@app.route('/api/repository/group/<path:group_id>', methods=['GET'])
def get_repo_group(group_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM repository WHERE group_id=%s ORDER BY uploaded_at ASC", (group_id,))
    rows = cursor.fetchall()
    conn.close()
    if not rows:
        return jsonify({'error': '组不存在'}), 404
    return jsonify(rows)

@app.route('/api/repository/<int:file_id>', methods=['PUT'])
def update_file(file_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM repository WHERE id=%s", (file_id,))
    record = cursor.fetchone()
    if not record:
        conn.close()
        return jsonify({'error': '文件不存在'}), 404
    file_path_old = os.path.join(app.config['UPLOAD_FOLDER'], record['file_path'])
    if 'file' in request.files:
        file = request.files['file']
        if file.filename != '':
            if os.path.exists(file_path_old):
                os.remove(file_path_old)
            filename = secure_filename(file.filename)
            name_parts = filename.rsplit('.', 1)
            if len(name_parts) == 2:
                filename = f"{name_parts[0]}_{int(time.time())}.{name_parts[1]}"
            else:
                filename = f"{filename}_{int(time.time())}"
            new_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
            file.save(new_path)
            file_size = os.path.getsize(new_path)
            cursor.execute("""UPDATE repository SET file_name=%s, file_path=%s, file_size=%s, uploaded_at=CURRENT_TIMESTAMP
                WHERE id=%s""", (file.filename, filename, file_size, file_id))
            conn.commit()
            conn.close()
            return jsonify({'message': '文件替换成功'})
    else:
        data = request.get_json()
        if data and 'content' in data:
            if not record['file_name'].endswith('.txt'):
                conn.close()
                return jsonify({'error': '只有文本文件可以编辑内容'}), 400
            try:
                with open(file_path_old, 'w', encoding='utf-8') as f:
                    f.write(data['content'])
                new_size = os.path.getsize(file_path_old)
                cursor.execute("UPDATE repository SET file_size=%s, uploaded_at=CURRENT_TIMESTAMP WHERE id=%s",
                               (new_size, file_id))
                conn.commit()
                conn.close()
                return jsonify({'message': '内容更新成功'})
            except Exception as e:
                conn.close()
                return jsonify({'error': str(e)}), 500
        else:
            description = data.get('description') if data else None
            answer = data.get('answer') if data else None
            if description is not None or answer is not None:
                updates = []
                params = []
                if description is not None:
                    updates.append("description=%s")
                    params.append(description)
                if answer is not None:
                    updates.append("answer=%s")
                    params.append(answer)
                if updates:
                    sql = f"UPDATE repository SET {', '.join(updates)} WHERE id=%s"
                    params.append(file_id)
                    cursor.execute(sql, tuple(params))
                    conn.commit()
                    conn.close()
                    return jsonify({'message': '更新成功'})
    conn.close()
    return jsonify({'error': '无有效更新数据'}), 400

@app.route('/api/repository/<int:file_id>', methods=['DELETE'])
def delete_file(file_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT file_path FROM repository WHERE id=%s", (file_id,))
    result = cursor.fetchone()
    if result:
        file_path = os.path.join(app.config['UPLOAD_FOLDER'], result['file_path'])
        if os.path.exists(file_path):
            os.remove(file_path)
        cursor.execute("DELETE FROM repository WHERE id=%s", (file_id,))
        conn.commit()
    conn.close()
    return jsonify({'message': '删除成功'})

@app.route('/api/repository/download/<int:file_id>')
def download_file(file_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM repository WHERE id=%s", (file_id,))
    result = cursor.fetchone()
    conn.close()
    if not result:
        return jsonify({'error': '文件不存在'}), 404
    file_path = os.path.join(app.config['UPLOAD_FOLDER'], result['file_path'])
    if not os.path.exists(file_path):
        return jsonify({'error': '文件不存在'}), 404
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("UPDATE repository SET download_count = download_count + 1 WHERE id=%s", (file_id,))
    conn.commit()
    conn.close()
    inline = request.args.get('inline', 'false').lower() == 'true'
    return send_file(file_path, as_attachment=not inline, download_name=result['file_name'])

@app.route('/api/repository/preview/<int:file_id>')
def preview_file(file_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM repository WHERE id=%s", (file_id,))
    main_file = cursor.fetchone()
    if not main_file:
        conn.close()
        return jsonify({'error': '文件不存在'}), 404
    file_path = os.path.join(app.config['UPLOAD_FOLDER'], main_file['file_path'])
    if not os.path.exists(file_path):
        conn.close()
        return jsonify({'error': '文件不存在'}), 404
    group_id = main_file.get('group_id')
    related_files = []
    if group_id:
        cursor.execute("SELECT * FROM repository WHERE group_id = %s AND id != %s", (group_id, file_id))
        related_files = list(cursor.fetchall())
    conn.close()

    # 组名跟随"主文件"：组内最早上传、且不是答案/备注附件的文件（即题目本身）
    group_title = ''
    if group_id:
        def _title_of(rec):
            return (rec.get('description') or '').strip()
        all_rows = [main_file] + related_files
        non_answer = [r for r in all_rows if (r.get('role') or '') != 'answer']
        def _sort_key(r):
            t = r.get('uploaded_at')
            t = t if t else datetime.min
            is_txt = 0 if str(r.get('file_name') or '').lower().endswith('.txt') else 1
            is_ans = 1 if (r.get('role') or '') == 'answer' else 0
            return (t, is_txt, is_ans, r.get('id') or 0)
        ordered = sorted(non_answer or all_rows, key=_sort_key)
        for rec in ordered:
            t = _title_of(rec)
            if t:
                group_title = t
                break
    if not group_title:
        group_title = (main_file.get('description') or '').strip()
    if not group_title:
        name_clean = re.sub(r'_\d+$', '', main_file['file_name'])
        name_clean = re.sub(r'\.[^.]+$', '', name_clean)
        group_title = name_clean

    result = {
        'group_title': group_title,
        'main': {'file_name': main_file['file_name'], 'type': 'unknown', 'answer': main_file.get('answer', '')},
        'related': []
    }

    file_ext = main_file['file_name'].split('.')[-1].lower() if '.' in main_file['file_name'] else ''
    if file_ext in ['jpg', 'jpeg', 'png', 'gif', 'webp', 'bmp', 'svg', 'heic']:
        with open(file_path, 'rb') as f:
            image_data = f.read()
            base64_data = base64.b64encode(image_data).decode('utf-8')
            result['main']['type'] = 'image'
            result['main']['mime'] = f'image/{file_ext}' if file_ext != 'jpg' else 'image/jpeg'
            result['main']['data'] = base64_data
    elif file_ext in ['txt', 'md', 'json', 'xml', 'csv', 'log', 'py', 'js', 'html', 'css']:
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()
            result['main']['type'] = 'text'
            result['main']['content'] = content
        except UnicodeDecodeError:
            try:
                with open(file_path, 'r', encoding='gbk') as f:
                    content = f.read()
                result['main']['type'] = 'text'
                result['main']['content'] = content
            except:
                result['main']['type'] = 'file'
                result['main']['message'] = '编码无法识别'
    else:
        result['main']['type'] = 'file'
        result['main']['message'] = '此格式暂不支持预览'

    if result['main']['type'] != 'text':
        for rf in [main_file] + related_files:
            if rf['id'] == main_file['id']:
                continue
            if (rf.get('role') or '') == 'answer':
                continue  # 追加的答案/笔记不参与题目正文提取
            rf_ext = rf['file_name'].split('.')[-1].lower() if '.' in rf['file_name'] else ''
            if rf_ext == 'txt':
                rf_path = os.path.join(app.config['UPLOAD_FOLDER'], rf['file_path'])
                if os.path.exists(rf_path):
                    try:
                        with open(rf_path, 'r', encoding='utf-8') as f:
                            txt_content = f.read()
                        result['main']['content'] = txt_content
                        result['main']['type'] = 'text'
                        break
                    except:
                        pass

    for rf in related_files:
        result['related'].append({
            'id': rf['id'],
            'file_name': rf['file_name'],
            'answer': rf.get('answer', '')
        })

    return jsonify(result)

# ============================================================
# 时政热点 API
# ============================================================
@app.route('/api/current_affairs', methods=['GET'])
def get_affairs():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM current_affairs ORDER BY id DESC")
    result = cursor.fetchall()
    conn.close()
    for item in result:
        item['score'] = compute_affair_score(
            item.get('title', ''),
            item.get('source', ''),
            item.get('date'),
            item.get('importance', 'low')
        )
        item['score_color'] = get_affair_score_color(item['score'])
    return jsonify(result)

@app.route('/api/current_affairs', methods=['DELETE'])
def delete_affairs():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM current_affairs WHERE id=%s", (request.args.get('id'),))
    conn.commit()
    conn.close()
    return jsonify({'message': '删除成功'})

@app.route('/api/current_affairs/batch', methods=['DELETE'])
def batch_delete_affairs():
    data = request.json
    ids = data.get('ids', [])
    if not ids:
        return jsonify({'error': '缺少IDs'}), 400
    conn = get_db_connection()
    cursor = conn.cursor()
    placeholders = ','.join(['%s'] * len(ids))
    cursor.execute(f"DELETE FROM current_affairs WHERE id IN ({placeholders})", ids)
    conn.commit()
    conn.close()
    return jsonify({'message': f'成功删除 {cursor.rowcount} 条'})

@app.route('/api/current_affairs/batch/importance', methods=['PUT'])
def batch_update_importance():
    data = request.json
    ids = data.get('ids', [])
    importance = data.get('importance', 'medium')
    if not ids:
        return jsonify({'error': '缺少IDs'}), 400
    conn = get_db_connection()
    cursor = conn.cursor()
    placeholders = ','.join(['%s'] * len(ids))
    cursor.execute(f"UPDATE current_affairs SET importance=%s WHERE id IN ({placeholders})", (importance, *ids))
    conn.commit()
    conn.close()
    return jsonify({'message': f'成功更新 {cursor.rowcount} 条'})

# ============================================================
# 收藏 API
# ============================================================
@app.route('/api/favorites', methods=['GET'])
def get_favorites():
    session_id = get_session_id()
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT item_type, item_id FROM favorites WHERE session_id = %s", (session_id,))
    favs = cursor.fetchall()
    result = {'affairs': [], 'repositories': []}
    affair_ids = []
    repo_ids = []
    for f in favs:
        if f['item_type'] == 'affair':
            affair_ids.append(f['item_id'])
        else:
            repo_ids.append(f['item_id'])
    if affair_ids:
        placeholders = ','.join(['%s'] * len(affair_ids))
        cursor.execute(f"SELECT * FROM current_affairs WHERE id IN ({placeholders})", affair_ids)
        affairs = cursor.fetchall()
        for item in affairs:
            item['score'] = compute_affair_score(
                item.get('title', ''),
                item.get('source', ''),
                item.get('date'),
                item.get('importance', 'low')
            )
            item['score_color'] = get_affair_score_color(item['score'])
        result['affairs'] = affairs
    if repo_ids:
        placeholders = ','.join(['%s'] * len(repo_ids))
        cursor.execute(f"SELECT * FROM repository WHERE id IN ({placeholders})", repo_ids)
        repos = cursor.fetchall()
        result['repositories'] = repos
    conn.close()
    return jsonify(result)

@app.route('/api/favorites/toggle', methods=['POST'])
def toggle_favorite():
    session_id = get_session_id()
    data = request.json
    item_type = data.get('item_type')
    item_id = data.get('item_id')
    if not item_type or not item_id:
        return jsonify({'error': '缺少参数'}), 400
    if item_type not in ('affair', 'repository'):
        return jsonify({'error': '无效类型'}), 400
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM favorites WHERE session_id = %s AND item_type = %s AND item_id = %s",
                   (session_id, item_type, item_id))
    existing = cursor.fetchone()
    if existing:
        cursor.execute("DELETE FROM favorites WHERE session_id = %s AND item_type = %s AND item_id = %s",
                       (session_id, item_type, item_id))
        conn.commit()
        conn.close()
        return jsonify({'message': '取消收藏', 'favorited': False})
    else:
        cursor.execute("INSERT INTO favorites (session_id, item_type, item_id) VALUES (%s, %s, %s)",
                       (session_id, item_type, item_id))
        conn.commit()
        conn.close()
        return jsonify({'message': '收藏成功', 'favorited': True})

# ============================================================
# AI 助手（纯文本，带上下文管理）
# ============================================================
@app.route('/api/ai/chat', methods=['POST'])
def ai_chat():
    data = request.json
    user_message = data.get('message', '').strip()
    session_id = data.get('session_id', 'default')


    if not user_message:
        return jsonify({'error': '请输入问题'}), 400

    if session_id not in chat_sessions:
        chat_sessions[session_id] = []

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) as total FROM current_affairs")
    total_affairs = cursor.fetchone()['total'] or 0
    today = datetime.now().strftime('%Y-%m-%d')
    cursor.execute("SELECT COUNT(*) as today_count FROM current_affairs WHERE date = %s", (today,))
    today_affairs = cursor.fetchone()['today_count'] or 0
    cursor.execute("SELECT title, source, date FROM current_affairs ORDER BY date DESC, id DESC LIMIT 10")
    recent_affairs = cursor.fetchall()
    cursor.execute("SELECT COUNT(*) as total FROM repository")
    total_repo = cursor.fetchone()['total'] or 0
    cursor.execute("SELECT file_name, category FROM repository ORDER BY uploaded_at DESC LIMIT 10")
    recent_repo = cursor.fetchall()
    cursor.execute("SELECT COUNT(*) as total FROM notes")
    total_notes = cursor.fetchone()['total'] or 0
    conn.close()

    affairs_text = "\n".join([f"- {a['title']}（{a['source']}，{a['date']}）" for a in recent_affairs]) if recent_affairs else "暂无"
    repo_text = "\n".join([f"- {r['file_name']}（{r['category']}）" for r in recent_repo]) if recent_repo else "暂无"

    system_prompt = f"""你是一个智能助手，名叫"公考小助手"。

=== 当前系统数据 ===
- 时政热点总数：{total_affairs} 条
- 今天的时政热点：{today_affairs} 条
- 仓库文件总数：{total_repo} 个
- 笔记总数：{total_notes} 条

=== 最近 10 条时政热点 ===
{affairs_text}

=== 最近 10 个仓库文件 ===
{repo_text}

=== 重要说明 ===
1. 当用户询问"今天有时政热点吗"、"有多少时政热点"、"仓库里有笔记吗"这类问题时，请根据上面的数据回答。
2. 如果用户问的问题与这些数据无关，正常闲聊即可。
3. 回答要自然、友好，像朋友一样交流。"""

    messages = [{"role": "system", "content": system_prompt}]
    history = chat_sessions[session_id][-80:] if len(chat_sessions[session_id]) > 80 else chat_sessions[session_id]
    messages.extend(history)
    messages.append({"role": "user", "content": user_message})

    def call_bailian():
        headers = {
            'Authorization': f'Bearer {BAILIAN_API_KEY}',
            'Content-Type': 'application/json'
        }
        payload = {
            'model': BAILIAN_MODEL,
            'messages': messages,
            'temperature': 0.7,
            'max_tokens': 2000,
            'enable_thinking': True,
            'thinking_budget': DEFAULT_THINKING_BUDGET,
            'stream': False
        }
        try:
            resp = requests.post(
                f'{BAILIAN_BASE_URL}/chat/completions',
                headers=headers,
                json=payload,
                timeout=60
            )
            if resp.status_code == 200:
                result = resp.json()
                return result['choices'][0]['message']['content'], 'cloud', None
            else:
                return None, None, f"HTTP {resp.status_code}: {resp.text[:200]}"
        except Exception as e:
            return None, None, str(e)
    
    # 统一使用百炼 DeepSeek V4 Flash
    if not BAILIAN_API_KEY:
        print("❌ 未配置百炼 API Key")
        return jsonify({'error': '未配置百炼 API Key，无法调用 AI 服务'}), 500
    ai_response, mode, err = call_bailian()
    
    if ai_response is None:
        print(f"❌ AI 调用失败：{err}")
        return jsonify({
            'error': f'AI 服务不可用。{err}'
        }), 500

    chat_sessions[session_id].append({"role": "user", "content": user_message})
    chat_sessions[session_id].append({"role": "assistant", "content": ai_response})
    if len(chat_sessions[session_id]) > 80:
        chat_sessions[session_id] = chat_sessions[session_id][-80:]

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO ai_conversations (session_id, user_message, ai_response, model) VALUES (%s, %s, %s, %s)",
        (session_id, user_message, ai_response, mode if mode else 'unknown')
    )
    conn.commit()
    conn.close()

    response_data = {'response': ai_response, 'mode': mode}
    return jsonify(response_data)

# ============================================================
# AI 识图专用接口（支持标准答案校对）
# ============================================================
def call_bailian_vision_with_retry(payload):
    headers = {
        'Authorization': f'Bearer {BAILIAN_API_KEY}',
        'Content-Type': 'application/json'
    }
    timeout = 60
    max_retry = 2
    for i in range(max_retry):
        try:
            resp = requests.post(
                f'{BAILIAN_BASE_URL}/chat/completions',
                headers=headers,
                json=payload,
                timeout=timeout
            )
            if resp.status_code == 200:
                return resp.json(), None
            return None, f"HTTP {resp.status_code}: {resp.text[:200]}"
        except requests.exceptions.Timeout:
            if i < max_retry - 1:
                continue
            else:
                return None, "模型响应超时，图片可能过大，请重试"
        except Exception as e:
            return None, str(e)
    return None, "多次请求均超时"

@app.route('/api/ai/vision', methods=['POST'])
def ai_vision():
    import time as timer
    start = timer.time()
    
    data = request.json
    image_base64 = data.get('image_base64')
    user_question = data.get('question', '')
    user_answer = data.get('user_answer', '')  # 用户自己填写的作答（可选）
    group_id = data.get('group_id', None)      # 用于查询标准答案

    if not image_base64:
        return jsonify({'error': '缺少图片'}), 400

    image_base64 = image_base64.strip().replace('\n', '').replace(' ', '')
    if 'base64,' in image_base64:
        image_base64 = image_base64.split('base64,')[-1]

    # 从数据库获取标准答案
    standard_answer = ''
    if group_id:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT answer FROM repository WHERE group_id = %s LIMIT 1", (group_id,))
        result = cursor.fetchone()
        conn.close()
        if result and result.get('answer'):
            standard_answer = result['answer']
            print(f"📤 [Vision] 获取到标准答案: {standard_answer}")

    # 构建 prompt
    if standard_answer:
        if user_answer:
            text_prompt = f'''你是公考行测老师，请根据以下标准答案和用户作答进行校对和讲解：

标准答案：{standard_answer}
用户作答：{user_answer}
题目信息：{user_question}

请按以下格式输出：
1. 判断用户作答是否正确（如果正确，说"正确"；如果错误，指出错误之处）
2. 标准答案的解题思路（解析本题的核心逻辑）
3. 考点名称
4. 对用户答案的针对性建议
要求：简洁、专业，不要冗余。'''
        else:
            text_prompt = f'''你是公考行测老师，请根据以下标准答案讲解这道题：

标准答案：{standard_answer}
题目信息：{user_question}

请按以下格式输出：
1. 标准答案的正确性验证
2. 解题思路（解析本题的核心逻辑）
3. 考点名称
4. 易错点提醒
要求：简洁、专业，不要冗余。'''
    else:
        # 没有标准答案，正常讲解
        text_prompt = '你是公考行测老师，识别这道题，给出：1.正确答案 2.核心解题思路 3.考点名称。简洁作答，不要冗余。'
        if user_question:
            text_prompt += '\n题目信息：' + user_question

    messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": text_prompt},
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_base64}"}}
            ]
        }
    ]

    payload = {
        'model': BAILIAN_MODEL,
        'messages': messages,
        'temperature': 0.7,
        'max_tokens': 800,
        'enable_thinking': True,
        'thinking_budget': DEFAULT_THINKING_BUDGET,
        'stream': False
    }

    print(f"📤 [Vision] 模型: {BAILIAN_MODEL}")
    print(f"📤 [Vision] Base64 长度: {len(image_base64)} 字符")
    if standard_answer:
        print(f"📤 [Vision] 已使用标准答案: {standard_answer}")

    result, err = call_bailian_vision_with_retry(payload)
    
    elapsed = timer.time() - start
    print(f"⏱️ [Vision] 耗时: {elapsed:.2f} 秒")

    if err:
        print(f"❌ [Vision] 错误: {err}")
        return jsonify({'success': False, 'error': err}), 500

    try:
        response_text = result['choices'][0]['message']['content']
        print(f"✅ [Vision] 响应成功，内容长度: {len(response_text)}")
        
        if group_id:
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute("UPDATE repository SET ai_explanation = %s WHERE group_id = %s", (response_text, group_id))
            conn.commit()
            conn.close()
            print(f"💾 讲解结果已保存到 group_id: {group_id}")
        
        return jsonify({'success': True, 'response': response_text})
    except (KeyError, IndexError) as e:
        print(f"❌ [Vision] 响应格式异常: {e}")
        return jsonify({'success': False, 'error': f'响应格式异常: {str(e)}'}), 500

# ============================================================
# 刷题模块 API
# ============================================================
@app.route('/api/quiz/start', methods=['POST'])
def quiz_start():
    data = request.json
    category = data.get('category')
    cat_map = {'行测': '行测提交', '申论': '申论提交'}
    db_cat = cat_map.get(category)
    if not db_cat:
        return jsonify({'error': '无效分类，请选择“行测”或“申论”'}), 400

    session_id = get_session_id()
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT DISTINCT group_id FROM repository 
        WHERE category = %s AND group_id IS NOT NULL
    """, (db_cat,))
    groups = cursor.fetchall()
    conn.close()
    group_ids = [g['group_id'] for g in groups]
    if not group_ids:
        return jsonify({'error': '该分类下暂无题目'}), 404

    random.shuffle(group_ids)
    quiz_state[session_id] = {
        'group_ids': group_ids,
        'current_index': 0,
        'category': db_cat
    }
    question = get_quiz_question(session_id, 0)
    if not question:
        return jsonify({'error': '获取题目失败'}), 500
    return jsonify(question)

@app.route('/api/quiz/next', methods=['POST'])
def quiz_next():
    data = request.json
    direction = data.get('direction', 'next')
    session_id = get_session_id()
    state = quiz_state.get(session_id)
    if not state:
        return jsonify({'error': '请先开始刷题'}), 400

    if direction == 'next':
        state['current_index'] += 1
    else:
        state['current_index'] -= 1

    if state['current_index'] < 0:
        state['current_index'] = 0
        return jsonify({'error': '已是第一题'}), 400
    if state['current_index'] >= len(state['group_ids']):
        state['current_index'] = len(state['group_ids']) - 1
        return jsonify({'error': '已到最后一题'}), 400

    question = get_quiz_question(session_id, state['current_index'])
    if not question:
        return jsonify({'error': '获取题目失败'}), 500
    return jsonify(question)

@app.route('/api/quiz/reset', methods=['POST'])
def quiz_reset():
    session_id = get_session_id()
    if session_id in quiz_state:
        del quiz_state[session_id]
    return jsonify({'message': '已重置'})

@app.route('/api/quiz/question', methods=['GET'])
def quiz_question():
    session_id = get_session_id()
    state = quiz_state.get(session_id)
    if not state:
        return jsonify({'error': '请先开始刷题'}), 400
    question = get_quiz_question(session_id, state['current_index'])
    if not question:
        return jsonify({'error': '题目不存在'}), 404
    return jsonify(question)

# ============================================================
# 爬虫、搜索、统计、系统状态等（省略，与之前相同）
# ============================================================
@app.route('/api/crawl_affairs', methods=['POST'])
def crawl_affairs():
    return jsonify({'error': '爬虫功能已移除，时政热点请通过其他方式获取'}), 410

@app.route('/api/search', methods=['GET'])
def global_search():
    query = request.args.get('q', '').strip()
    if not query:
        return jsonify({'error': '请输入搜索关键词'}), 400
    results = []
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT id, title, content, source, date, 'affair' as type 
        FROM current_affairs 
        WHERE title LIKE %s OR content LIKE %s
        ORDER BY date DESC
    """, ('%'+query+'%', '%'+query+'%'))
    affairs = cursor.fetchall()
    for row in affairs:
        results.append({
            'type': '时政热点',
            'title': row['title'],
            'content': (row['content'] or '')[:200],
            'source': row['source'],
            'date': row['date'],
            'id': row['id'],
            'link': row['content']
        })
    cursor.execute("""
        SELECT id, file_name, file_path, description, category 
        FROM repository 
        WHERE file_name LIKE %s OR description LIKE %s
    """, ('%'+query+'%', '%'+query+'%'))
    files = cursor.fetchall()
    for row in files:
        matched = False
        content_preview = ''
        if query in row['file_name'] or (row['description'] and query in row['description']):
            matched = True
            content_preview = row['description'] or ''
        else:
            if row['file_name'].endswith('.txt'):
                file_path = os.path.join(app.config['UPLOAD_FOLDER'], row['file_path'])
                if os.path.exists(file_path):
                    try:
                        with open(file_path, 'r', encoding='utf-8') as f:
                            file_content = f.read()
                            if query in file_content:
                                matched = True
                                idx = file_content.lower().find(query.lower())
                                start = max(0, idx-30)
                                end = min(len(file_content), idx+len(query)+30)
                                content_preview = '...' + file_content[start:end] + '...'
                    except:
                        pass
        if matched:
            results.append({
                'type': '仓库文件',
                'title': row['file_name'],
                'content': content_preview,
                'id': row['id'],
                'file_id': row['id']
            })
    cursor.execute("""
        SELECT id, word, phonetic, meaning 
        FROM cet4_words 
        WHERE word LIKE %s OR meaning LIKE %s OR phonetic LIKE %s
        ORDER BY word ASC
        LIMIT 5
    """, ('%'+query+'%', '%'+query+'%', '%'+query+'%'))
    words = cursor.fetchall()
    for row in words:
        results.append({
            'type': '四级单词',
            'title': row['word'],
            'content': (row['meaning'] or '')[:100],
            'phonetic': row['phonetic'] or '',
            'word_id': row['id'],
            'word': row['word'],
            'meaning': row['meaning'] or ''
        })
    conn.close()
    return jsonify(results)

@app.route('/api/stats', methods=['GET'])
def get_stats():
    conn = get_db_connection()
    cursor = conn.cursor()
    stats = {}
    tables = [
        ('affairs_count', 'current_affairs'),
        ('repository_count', 'repository'),
        ('ai_count', 'ai_conversations')
    ]
    for key, table in tables:
        try:
            cursor.execute(f"SELECT COUNT(*) FROM {table}")
            result = cursor.fetchone()
            stats[key] = result['COUNT(*)'] if result else 0
        except Exception:
            stats[key] = 0
    conn.close()
    return jsonify(stats)

@app.route('/api/system_stats', methods=['GET'])
def get_system_stats():
    try:
        cpu_percent = psutil.cpu_percent(interval=0.5)
        cpu_count = psutil.cpu_count()
        load_avg = os.getloadavg() if hasattr(os, 'getloadavg') else None

        mem = psutil.virtual_memory()
        mem_total = mem.total / (1024**3)
        mem_used = mem.used / (1024**3)
        mem_percent = mem.percent

        disk = psutil.disk_usage('/')
        disk_total = disk.total / (1024**3)
        disk_used = disk.used / (1024**3)
        disk_percent = disk.percent

        uptime_seconds = time.time() - psutil.boot_time()
        uptime_days = int(uptime_seconds // 86400)
        uptime_hours = int((uptime_seconds % 86400) // 3600)
        uptime_minutes = int((uptime_seconds % 3600) // 60)

        stats = {
            'cpu': {
                'percent': cpu_percent,
                'count': cpu_count,
                'load_avg': load_avg
            },
            'memory': {
                'total_gb': round(mem_total, 1),
                'used_gb': round(mem_used, 1),
                'percent': mem_percent
            },
            'disk': {
                'total_gb': round(disk_total, 1),
                'used_gb': round(disk_used, 1),
                'percent': disk_percent
            },
            'uptime': {
                'days': uptime_days,
                'hours': uptime_hours,
                'minutes': uptime_minutes
            },
            'platform': platform.platform()
        }
        return jsonify(stats)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# ============================================================

# ============================================================
# 以下模块已移除：每日练习 / 知识题库 / 生成器 / 爬虫
# 全部 AI 功能统一使用百炼 DeepSeek V4 Flash API
# ============================================================

if __name__ == '__main__':
    import logging
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
    logger = logging.getLogger(__name__)
    os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
    init_db()
    try:
        app.run(host='127.0.0.1', port=5000, debug=True, use_reloader=False)
    except KeyboardInterrupt:
        logger.info("🛑 服务器已停止")

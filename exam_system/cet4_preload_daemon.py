#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
四级刷词 - 持续AI预加载守护进程
开机自动运行，扫描所有 ai_loaded=0 的单词，用百炼 API (DeepSeek V4 Flash) 逐个生成例句+记忆+释义并落库。
并发2线程，低优先级，不阻塞主服务。
"""
import pymysql
import json
import urllib.request
import time
import threading
import logging
import os
import sys

try:
    import psutil
except ImportError:
    psutil = None

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler('/tmp/cet4_preload.log'),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger('cet4_preload')

DB_CONFIG = {
    'host': 'localhost',
    'user': 'root',
    'password': 'YOUR_DB_PASSWORD',
    'database': 'exam_system',
    'charset': 'utf8mb4',
    'cursorclass': pymysql.cursors.DictCursor
}
BAILIAN_KEY = os.environ.get('BAILIAN_API_KEY', '')
BAILIAN_URL = 'https://token-plan.cn-beijing.maas.aliyuncs.com/compatible-mode/v1/chat/completions'
BAILIAN_MODEL = 'qwen3.8-flash'
CONCURRENCY = 2
BATCH_SIZE = 20
# —— 自适应节流调度 ——
# 平时(无待生成词): 每隔 IDLE_INTERVAL 秒低频巡检一次(默认4小时)
IDLE_INTERVAL = 4 * 3600
# 判定“确认没新词”的试探扫描次数与间隔(默认连续 3 次、每次间隔 5 秒)
IDLE_PROBE_TIMES = 3
IDLE_PROBE_GAP = 5
# 高频批量生成时，每批之间的短暂间隔(秒)
ACTIVE_BATCH_GAP = 1
SLEEP_EMPTY = 30   # (保留兼容：单次空命中后的短暂间隔，实际按新调度走)
SLEEP_ERROR = 60   # 生成失败时休眠
STATUS_FILE = '/tmp/cet4_preload_status.json'
MEMORY_LIMIT = 90  # 内存使用率超过此值时暂停生成
MEMORY_CHECK_INTERVAL = 10  # 内存检查间隔（秒）

def get_db():
    return pymysql.connect(**DB_CONFIG)

def memory_pressure():
    """返回当前内存使用率百分比（0-100），无法获取时返回0（不限制）"""
    if psutil is None:
        return 0
    try:
        return psutil.virtual_memory().percent
    except Exception:
        return 0

def wait_if_memory_high():
    """内存超过阈值时休眠等待，直到低于阈值才继续"""
    while True:
        pct = memory_pressure()
        if pct < MEMORY_LIMIT:
            return
        logger.info(f"⏸️ 内存使用率 {pct:.0f}% ≥ {MEMORY_LIMIT}%，暂停生成等待内存释放...")
        write_status(running=True, paused_memory=True, mem_percent=pct, current_word='等待内存', last_update=time.strftime('%Y-%m-%d %H:%M:%S'))
        time.sleep(MEMORY_CHECK_INTERVAL)

def call_bailian(prompt, timeout=180):
    messages = [{'role': 'user', 'content': prompt}]
    data = json.dumps({
        'model': BAILIAN_MODEL,
        'messages': messages,
        'temperature': 0.7,
        'max_tokens': 800,
        'stream': False
    }).encode()
    req = urllib.request.Request(BAILIAN_URL, data=data, headers={'Authorization': f'Bearer {BAILIAN_KEY}', 'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            r = json.loads(resp.read().decode())
            return r['choices'][0]['message']['content'].strip(), None
    except Exception as e:
        return None, str(e)

# 向后兼容别名

def preload_one(word_id, word, base_meaning):
    """为单个单词生成例句+记忆+释义并落库"""
    prompt = "为四级单词 \"" + word + "\" 生成以下精简学习内容：\n一、例句（2个，四级难度，每个带中文翻译）\n二、派生词（1个）\n三、记忆技巧（词根/联想/近义，具体好记）\n四、详细释义（每个词性列出真实义项，各配1个常用搭配）\n基础释义：" + base_meaning + "\n格式（简洁）：\n【例句】1. " + word + ": 例句. / 翻译。 2. " + word + ": 例句. / 翻译。\n【派生】派生词\n【记忆】词根: ... 联想: ... 近义: ...\n【释义】1. 词性. 真实义项（具体中文词义）；搭配: 具体搭配。2. 词性. 真实义项；搭配: 具体搭配。\n重要要求：严禁照抄模板占位符；不得出现\"义项1\"\"义项2\"\"义项：\"等空壳文字；每个义项必须写出具体的中文释义。"
    content, err = call_bailian(prompt)
    if err or not content:
        return False, err or '空内容'
    
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
    if not example:
        example = text[:200]
    if not memorytip:
        memorytip = text[:200]
    
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE cet4_words SET ai_example=%s, ai_memorytip=%s, ai_detail=%s, ai_loaded=1 WHERE id=%s",
        (example, memorytip, detail, word_id)
    )
    conn.commit()
    conn.close()
    return True, None

def write_status(**kw):
    try:
        with open(STATUS_FILE, 'w', encoding='utf-8') as f:
            json.dump(kw, f, ensure_ascii=False)
    except Exception:
        pass

def scan_pending():
    """返回当前待生成的单词列表(ai_loaded=0)，失败返回空。"""
    try:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id, word, meaning FROM cet4_words WHERE ai_loaded=0 "
            "ORDER BY id ASC LIMIT %s", (BATCH_SIZE * 2,))
        rows = cursor.fetchall()
        conn.close()
        return rows or []
    except Exception as e:
        logger.error(f"扫描待生成词失败: {e}")
        return []


def count_done():
    """已生成(ai_loaded=1)的词数。"""
    try:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) AS cnt FROM cet4_words WHERE ai_loaded=1")
        n = cursor.fetchone()['cnt']
        conn.close()
        return n
    except Exception:
        return 0


def probe_idle(pending):
    """空闲确认：当前一次扫描无词后，再连续试探几次(各隔几秒)确认是否真的没新词。
    若任一试探发现新词则返回带词列表(应立即转高频处理)；全部为空返回空列表。"""
    if pending:
        return pending
    for i in range(IDLE_PROBE_TIMES):
        time.sleep(IDLE_PROBE_GAP)
        again = scan_pending()
        if again:
            logger.info(f"离线巡检补到新词(第{i+1}次试探)，恢复高频生成")
            return again
    return []


def process_batch(pending):
    """并发生成一批，返回 (成功数, 失败数)。"""
    q = list(pending)
    results = {'done': 0, 'fail': 0}

    def worker():
        while q:
            wait_if_memory_high()
            item = q.pop(0)
            ok, err = preload_one(item['id'], item['word'], item.get('meaning', ''))
            if ok:
                results['done'] += 1
                logger.info(f"✅ {item['word']} {item.get('meaning', '')} (剩余待生成约 {len(q)})")
            else:
                results['fail'] += 1
                logger.warning(f"❌ {item['word']}: {err}")
            write_status(running=True, total_done=0, current_word=item['word'],
                         last_update=time.strftime('%Y-%m-%d %H:%M:%S'))

    threads = []
    for _ in range(min(CONCURRENCY, len(pending))):
        t = threading.Thread(target=worker, daemon=True)
        t.start()
        threads.append(t)
    for t in threads:
        t.join()
    return results['done'], results['fail']


def main():
    logger.info("四级刷词AI预加载守护进程启动(自适应节流)")
    write_status(running=True, total_done=0, current_word='', last_update=time.strftime('%Y-%m-%d %H:%M:%S'))

    # 完成状态缓存：避免“全空→彻底睡4h”期间反复打扰日志
    idle_logged = False
    while True:
        try:
            pending = scan_pending()
            if not pending:
                # 无词：探测确认（间隔几秒反复看几次）；仍无才进入长期空闲
                confirmed_empty = probe_idle(pending)
                if confirmed_empty:
                    # 探测期间冒出新词 → 立即高频处理
                    d, f = process_batch(confirmed_empty)
                    logger.info(f"本批完成(探测补货): 成功{d} 失败{f}")
                    idle_logged = False
                    time.sleep(ACTIVE_BATCH_GAP)
                    continue

                # 真的空闲：低频巡检
                total = count_done()
                if not idle_logged:
                    logger.info(f"✅ 无待生成词(当前已生成 {total} 词)，转入低频巡检，每 {IDLE_INTERVAL//3600} 小时扫描一次")
                    write_status(running=False, total_done=total, current_word='空闲(低频巡检)',
                                 last_update=time.strftime('%Y-%m-%d %H:%M:%S'))
                    idle_logged = True
                time.sleep(IDLE_INTERVAL)
                idle_logged = False  # 一个低频周期结束，下轮重新播报
                continue

            # 有待生成词：高频批量生成
            d, f = process_batch(pending)
            logger.info(f"本批完成: 成功{d} 失败{f}")
            write_status(running=True, total_done=0, current_word='',
                         last_update=time.strftime('%Y-%m-%d %H:%M:%S'))
            idle_logged = False
            time.sleep(ACTIVE_BATCH_GAP)

        except Exception as e:
            logger.error(f"主循环异常: {e}")
            time.sleep(SLEEP_ERROR)

if __name__ == '__main__':
    main()
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
多源时政新闻爬虫 - 适配公考学习系统
修复日期提取问题
"""

import requests
from bs4 import BeautifulSoup
from datetime import datetime
import re
import hashlib
import pymysql

DB_CONFIG = {
    'host': 'localhost',
    'user': 'root',
    'password': 'YOUR_DB_PASSWORD',
    'database': 'exam_system',
    'charset': 'utf8mb4',
    'cursorclass': pymysql.cursors.DictCursor
}

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}

# ===================== heat评分 =====================
SOURCE_BASE_SCORE = {
    "中国政府网": 100,
    "求是网": 92,
    "人民网-时政": 90,
    "人民网-经济": 84,
    "人民网-社会": 82,
    "新华网": 94,
    "央视网": 86,
    "半月谈": 78
}

def calc_heat(source_name: str, pub_date_str: str) -> int:
    """动态计算热度分：来源基础分 + 时间衰减"""
    base = SOURCE_BASE_SCORE.get(source_name, 70)
    try:
        pub_dt = datetime.strptime(pub_date_str, "%Y-%m-%d")
        now = datetime.now()
        delta_days = (now.date() - pub_dt.date()).days
        if delta_days <= 0:
            time_add = 10
        elif delta_days <= 7:
            time_add = max(0, 10 - delta_days)
        elif delta_days > 90:
            time_add = -25
        else:
            time_add = 0
    except Exception:
        time_add = 0
    total = base + time_add
    return max(40, min(100, total))

def get_db_conn():
    return pymysql.connect(**DB_CONFIG)

def clean_title(text):
    if not text:
        return ''
    text = re.sub(r'\s+', ' ', text).strip()
    return text

def save_news_to_mysql(title, url, source, date=None, heat=50):
    if not title or not url:
        return False
    title = clean_title(title)
    if len(title) < 4:
        return False
    if not date:
        date = datetime.now().strftime('%Y-%m-%d')
    
    conn = get_db_conn()
    cur = conn.cursor()
    try:
        cur.execute("SELECT id FROM current_affairs WHERE title = %s", (title,))
        if cur.fetchone():
            return False
        cur.execute("""
            INSERT INTO current_affairs (title, content, source, date, heat)
            VALUES (%s, %s, %s, %s, %s)
        """, (title, url, source, date, heat))
        conn.commit()
        return True
    except Exception as e:
        print(f"  [DB异常] {e}")
        return False
    finally:
        conn.close()

# ============ 中国政府网 ============
def fetch_gov_cn():
    news = []
    try:
        url = "https://www.gov.cn/yaowen/liebiao/202608/"
        resp = requests.get(url, headers=HEADERS, timeout=15)
        resp.encoding = 'utf-8'
        soup = BeautifulSoup(resp.text, 'html.parser')
        items = soup.select('a[href*="content_"]')
        if not items:
            items = soup.select('.list li a') or soup.select('.news-list a')
        
        for a in items[:12]:
            title = a.get_text(strip=True)
            link = a.get('href')
            if not title or not link or len(title) < 5:
                continue
            if not link.startswith('http'):
                link = 'https://www.gov.cn' + link if link.startswith('/') else 'https://www.gov.cn/' + link
            
            # 从链接提取日期: /202608/ 或 /2026/08/
            date_match = re.search(r'/(\d{4})(\d{2})?(\d{2})?/', link)
            if date_match:
                year = date_match.group(1)
                month = date_match.group(2) if date_match.group(2) else '01'
                day = date_match.group(3) if date_match.group(3) else '01'
                date = f"{year}-{month}-{day}"
            else:
                # 如果链接中没有日期，从页面中找
                date_tag = a.find_next('span', class_='date') or a.find_next('time')
                if date_tag:
                    date_text = date_tag.get_text(strip=True)
                    date_match2 = re.search(r'(\d{4})[-年](\d{1,2})[-月](\d{1,2})', date_text)
                    if date_match2:
                        date = f"{date_match2.group(1)}-{date_match2.group(2).zfill(2)}-{date_match2.group(3).zfill(2)}"
                    else:
                        date = datetime.now().strftime('%Y-%m-%d')
                else:
                    date = datetime.now().strftime('%Y-%m-%d')
            
            news.append({'title': title, 'source': '中国政府网', 'url': link, 'date': date})
    except Exception as e:
        print(f"  ⚠️ 中国政府网抓取失败: {e}")
    return news

# ============ 求是网 ============
def fetch_qstheory():
    news = []
    try:
        url = "https://www.qstheory.cn/"
        resp = requests.get(url, headers=HEADERS, timeout=10)
        resp.encoding = 'utf-8'
        soup = BeautifulSoup(resp.text, 'html.parser')
        items = soup.select('.hot-list li a') or soup.select('.list li a') or soup.select('a[href*="/article/"]')
        for a in items[:8]:
            title = a.get_text(strip=True)
            link = a.get('href')
            if not title or not link or len(title) < 5:
                continue
            if not link.startswith('http'):
                link = 'https://www.qstheory.cn' + link if link.startswith('/') else 'https://www.qstheory.cn/' + link
            
            # 求是网日期在链接中: /2026/08/04/ 或 /20260804/
            date_match = re.search(r'/(\d{4})(\d{2})?(\d{2})?/', link) or re.search(r'/(\d{4})/(\d{2})/(\d{2})/', link)
            if date_match:
                groups = date_match.groups()
                if len(groups) >= 3 and groups[1] and groups[2]:
                    date = f"{groups[0]}-{groups[1].zfill(2)}-{groups[2].zfill(2)}"
                else:
                    date = datetime.now().strftime('%Y-%m-%d')
            else:
                date = datetime.now().strftime('%Y-%m-%d')
            
            news.append({'title': title, 'source': '求是网', 'url': link, 'date': date})
    except Exception as e:
        print(f"  ⚠️ 求是网抓取失败: {e}")
    return news

# ============ 人民网 ============
def fetch_people():
    news = []
    channels = [
        ("人民网-时政", "http://cpc.people.com.cn/"),
        ("人民网-经济", "http://finance.people.com.cn/"),
        ("人民网-社会", "http://society.people.com.cn/"),
    ]
    
    for source_name, url in channels:
        try:
            resp = requests.get(url, headers=HEADERS, timeout=10)
            resp.encoding = 'gbk'
            soup = BeautifulSoup(resp.content, 'html.parser', from_encoding='gbk')
            items = soup.find_all('a', href=True)
            
            for a in items:
                title = a.get_text(strip=True)
                link = a.get('href')
                if not title or not link or len(title) < 5:
                    continue
                if '/n1/' not in link and '/n/' not in link:
                    continue
                if any(k in link for k in ['video', 'photo', 'index', 'special']):
                    continue
                if link.startswith('//'):
                    link = 'http:' + link
                elif link.startswith('/'):
                    link = url.rstrip('/') + link
                
                # 人民网日期在链接中: /2026/0803/
                date_match = re.search(r'/(\d{4})/(\d{4})/', link)
                if date_match:
                    date = f"{date_match.group(1)}-{date_match.group(2)[:2]}-{date_match.group(2)[2:]}"
                else:
                    # 尝试从标签中找日期
                    date_tag = a.find_next('span', class_='date') or a.find_next('time')
                    if date_tag:
                        date_text = date_tag.get_text(strip=True)
                        date_match2 = re.search(r'(\d{4})[-年](\d{1,2})[-月](\d{1,2})', date_text)
                        if date_match2:
                            date = f"{date_match2.group(1)}-{date_match2.group(2).zfill(2)}-{date_match2.group(3).zfill(2)}"
                        else:
                            date = datetime.now().strftime('%Y-%m-%d')
                    else:
                        date = datetime.now().strftime('%Y-%m-%d')
                
                news.append({'title': title, 'source': source_name, 'url': link, 'date': date})
                if len(news) >= 30:
                    break
        except Exception as e:
            print(f"  ⚠️ {source_name} 抓取失败: {e}")
    return news

# ============ 新华网 ============
def fetch_xinhua():
    news = []
    try:
        url = "http://www.xinhuanet.com/politics/"
        resp = requests.get(url, headers=HEADERS, timeout=10)
        resp.encoding = 'utf-8'
        soup = BeautifulSoup(resp.text, 'html.parser')
        items = soup.select('.clearfix .h2 a') or soup.select('.news-title a') or soup.select('a[href*="/politics/"]')
        for a in items[:10]:
            title = a.get_text(strip=True)
            link = a.get('href')
            if not title or not link or len(title) < 5:
                continue
            if not link.startswith('http'):
                link = 'http://www.xinhuanet.com' + link if link.startswith('/') else 'http://www.xinhuanet.com/' + link
            
            # 新华网日期在链接中: /2026-08-04/ 或 /202608/
            date_match = re.search(r'/(\d{4})-(\d{2})-(\d{2})/', link) or re.search(r'/(\d{4})(\d{2})?(\d{2})?/', link)
            if date_match:
                groups = date_match.groups()
                if len(groups) >= 3:
                    if '-' in link:
                        date = f"{groups[0]}-{groups[1].zfill(2)}-{groups[2].zfill(2)}"
                    else:
                        month = groups[1] if groups[1] else '01'
                        day = groups[2] if groups[2] else '01'
                        date = f"{groups[0]}-{month.zfill(2)}-{day.zfill(2)}"
                else:
                    date = datetime.now().strftime('%Y-%m-%d')
            else:
                # 尝试从标签中提取
                date_tag = a.find_next('span', class_='time') or a.find_next('time')
                if date_tag:
                    date_text = date_tag.get_text(strip=True)
                    date_match2 = re.search(r'(\d{4})[-年](\d{1,2})[-月](\d{1,2})', date_text)
                    if date_match2:
                        date = f"{date_match2.group(1)}-{date_match2.group(2).zfill(2)}-{date_match2.group(3).zfill(2)}"
                    else:
                        date = datetime.now().strftime('%Y-%m-%d')
                else:
                    date = datetime.now().strftime('%Y-%m-%d')
            
            news.append({'title': title, 'source': '新华网', 'url': link, 'date': date})
    except Exception as e:
        print(f"  ⚠️ 新华网抓取失败: {e}")
    return news

# ============ 央视网 ============
def fetch_cctv():
    news = []
    try:
        url = "https://news.cctv.com/"
        resp = requests.get(url, headers=HEADERS, timeout=10)
        resp.encoding = 'utf-8'
        soup = BeautifulSoup(resp.text, 'html.parser')
        all_links = soup.find_all('a', href=True)
        
        for a in all_links:
            title = a.get_text(strip=True)
            link = a.get('href')
            if not title or not link or len(title) < 5:
                continue
            if not re.search(r'/\d{4}/\d{2}/\d{2}/', link):
                continue
            if any(k in link for k in ['video', 'photo', 'special', 'live']):
                continue
            if link.startswith('//'):
                link = 'https:' + link
            elif link.startswith('/'):
                link = 'https://news.cctv.com' + link
            
            # 央视网日期在链接中: /2026/08/04/
            date_match = re.search(r'/(\d{4})/(\d{2})/(\d{2})/', link)
            if date_match:
                date = f"{date_match.group(1)}-{date_match.group(2)}-{date_match.group(3)}"
            else:
                date = datetime.now().strftime('%Y-%m-%d')
            
            news.append({'title': title, 'source': '央视网', 'url': link, 'date': date})
            if len(news) >= 12:
                break
    except Exception as e:
        print(f"  ⚠️ 央视网抓取失败: {e}")
    return news

# ============ 半月谈 ============
def fetch_ban_yue_tan():
    news = []
    try:
        url = "http://www.banyuetan.org/"
        resp = requests.get(url, headers=HEADERS, timeout=10)
        resp.encoding = 'utf-8'
        soup = BeautifulSoup(resp.text, 'html.parser')
        items = soup.select('.title a') or soup.select('.list a') or soup.select('a[href*="/yw/detail/"]')
        
        for a in items[:8]:
            title = a.get_text(strip=True)
            link = a.get('href')
            if not title or not link or len(title) < 5:
                continue
            if not link.startswith('http'):
                link = 'http://www.banyuetan.org' + link if link.startswith('/') else 'http://www.banyuetan.org/' + link
            
            # 半月谈日期在链接中: /20260805/
            date_match = re.search(r'/(\d{8})/', link)
            if date_match:
                d = date_match.group(1)
                date = f"{d[:4]}-{d[4:6]}-{d[6:8]}"
            else:
                # 尝试从页面中提取
                date_tag = a.find_next('span', class_='date') or a.find_next('time')
                if date_tag:
                    date_text = date_tag.get_text(strip=True)
                    date_match2 = re.search(r'(\d{4})[-年](\d{1,2})[-月](\d{1,2})', date_text)
                    if date_match2:
                        date = f"{date_match2.group(1)}-{date_match2.group(2).zfill(2)}-{date_match2.group(3).zfill(2)}"
                    else:
                        date = datetime.now().strftime('%Y-%m-%d')
                else:
                    date = datetime.now().strftime('%Y-%m-%d')
            
            news.append({'title': title, 'source': '半月谈', 'url': link, 'date': date})
    except Exception as e:
        print(f"  ⚠️ 半月谈抓取失败: {e}")
    return news

# ============ 合并所有来源 ============
def fetch_all_sources():
    all_news = []
    all_news.extend(fetch_gov_cn())
    all_news.extend(fetch_qstheory())
    all_news.extend(fetch_people())
    all_news.extend(fetch_xinhua())
    all_news.extend(fetch_cctv())
    all_news.extend(fetch_ban_yue_tan())

    seen = set()
    unique = []
    for item in all_news:
        key = hashlib.md5(item['title'].encode('utf-8')).hexdigest()
        if key not in seen:
            seen.add(key)
            unique.append(item)
    unique.sort(key=lambda x: calc_heat(x["source"], x["date"]), reverse=True)
    return unique

def main():
    print("="*70)
    print("📰 多源时政新闻爬虫 v3.2 (修复日期提取)")
    print("  来源: 中国政府网/人民网/新华网/央视网/半月谈/求是网")
    print("="*70)
    
    print("\n🔄 正在抓取多源时政新闻...")
    news_list = fetch_all_sources()
    
    if not news_list:
        print("⚠️ 未获取到任何新闻")
        return 0
    
    print(f"\n📝 共获取 {len(news_list)} 条新闻（去重后）")
    
    added = 0
    for item in news_list[:30]:
        heat_val = calc_heat(item["source"], item["date"])
        if save_news_to_mysql(item['title'], item['url'], item['source'], item['date'], heat_val):
            added += 1
            print(f"  ✅ {item['title'][:50]}... (日期:{item['date']})")
    
    print("\n" + "="*70)
    print(f"📊 新增 {added} 条时政新闻")
    print("="*70)
    return added

if __name__ == "__main__":
    main()

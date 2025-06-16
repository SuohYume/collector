import os
import csv
import yaml
import random
import asyncio
import aiohttp
import ssl
import base64
import re
import json
from urllib.parse import urlparse, unquote, parse_qs
from hashlib import md5
from datetime import datetime, timezone, timedelta

# --- 配置区 ---
CONFIG = {
    "DB_FILE": "link_database.csv",
    "ARCHIVE_FILE": "archive.csv",
    "REPORT_FILE": "quality_report.csv",
    "CACHE_DIR": "cached_subs",
    "OUTPUT_TASK_LIST": "sub_list_for_testing.txt",
    "OUTPUT_FULL_PACKAGE": "clash.yaml",
    "OUTPUT_LITE_PACKAGE": "clash_lite.yaml",
    "RAW_NODE_ESTIMATE_TARGET": 50000,
    "LITE_NODE_COUNT": 1000,
    "MAX_CONCURRENT_REQUESTS": 50,
    "REQUEST_TIMEOUT": 8,
    "MAX_FAILURE_STREAK": 10,
    "ARCHIVE_DAYS": 60,
}

# --- 全局SSL上下文 ---
SSL_CONTEXT = ssl.create_default_context()
SSL_CONTEXT.check_hostname = False
SSL_CONTEXT.verify_mode = ssl.CERT_NONE

# --- 辅助函数 ---
def ensure_id(row):
    if 'id' not in row or not row['id']:
        row['id'] = f"sub_{md5(row['url'].encode()).hexdigest()[:10]}"
        return True
    return False

def get_node_count_from_content(text):
    if not text: return 0
    try:
        content = yaml.safe_load(text)
        if isinstance(content, dict) and 'proxies' in content and isinstance(content['proxies'], list):
            return len(content['proxies'])
    except Exception: pass
    try:
        decoded_text = base64.b64decode(''.join(text.split())).decode('utf-8')
        return len(re.findall(r"(vmess|vless|ss|trojan)://", decoded_text, re.IGNORECASE))
    except Exception: pass
    return len(re.findall(r"(vmess|vless|ss|trojan)://", text, re.IGNORECASE))

async def fetch_content(session, url, url_type):
    headers = {'User-Agent': 'Clash'}
    async def get(target_url):
        try:
            async with session.get(target_url, headers=headers, timeout=CONFIG['REQUEST_TIMEOUT'], ssl=SSL_CONTEXT) as response:
                response.raise_for_status()
                return await response.text()
        except Exception: return None

    if url_type == 'api': return await get(url.rstrip('/') + '/clash/proxies')
    if url_type == 'raw': return await get(url)

    # 自动探测
    content = await get(url.rstrip('/') + '/clash/proxies')
    if content and get_node_count_from_content(content) > 0: return content
    content = await get(url)
    if content and get_node_count_from_content(content) > 0: return content
    print(f"❌ 自动探测失败: {url}")
    return None

# --- 主逻辑 ---
async def main():
    if not os.path.exists(CONFIG['CACHE_DIR']): os.makedirs(CONFIG['CACHE_DIR'])
    for f in os.listdir(CONFIG['CACHE_DIR']): os.remove(os.path.join(CONFIG['CACHE_DIR'], f))

    try:
        with open(CONFIG['DB_FILE'], 'r', newline='', encoding='utf-8') as f:
            links_db = list(csv.DictReader(f))
    except FileNotFoundError:
        print(f"主数据库 {CONFIG['DB_FILE']} 未找到。")
        return

    db_changed = any(ensure_id(row) for row in links_db)

    try:
        with open(CONFIG['REPORT_FILE'], 'r', newline='', encoding='utf-8') as f:
            naughty_list = {row['failed_id'] for row in csv.DictReader(f)}
    except FileNotFoundError: naughty_list = set()

    now = datetime.now(timezone.utc)
    repo = os.environ.get('GITHUB_REPOSITORY', 'user/repo')

    for row in links_db:
        row['failure_streak'] = int(row.get('failure_streak', 0))
        cache_file_url = f"https://raw.githubusercontent.com/{repo}/main/{CONFIG['CACHE_DIR']}/{row['id']}.txt"
        if row['id'] in naughty_list:
            row['failure_streak'] += 1
            row['status'] = 'unstable'
        else:
            row['failure_streak'] = 0
            if row.get('status') != 'dead': row['status'] = 'active'
        if row['failure_streak'] >= CONFIG['MAX_FAILURE_STREAK']:
            row['status'] = 'dead'
        row['last_report_time'] = now.isoformat()

    def get_priority(row):
        status_map = {'active': 0, 'new': 1, 'unstable': 2, 'dead': 3}
        return (status_map.get(row.get('status', 'new'), 9), row['failure_streak'])
    links_db.sort(key=get_priority)

    total_estimated_nodes = 0
    links_for_debian = []

    async with aiohttp.ClientSession() as session:
        tasks_to_run = []
        for link_data in links_db:
            if link_data.get('status') == 'dead': continue
            if total_estimated_nodes >= CONFIG['RAW_NODE_ESTIMATE_TARGET'] and int(link_data.get('estimated_raw_node_count', 0)) > 0:
                continue
            tasks_to_run.append(link_data)

        async def process_link(link_data):
            content = await fetch_content(session, link_data['url'], link_data.get('type', 'auto'))
            if content:
                node_count = get_node_count_from_content(content)
                if node_count > 0:
                    link_data['estimated_raw_node_count'] = node_count
                    cache_path = os.path.join(CONFIG['CACHE_DIR'], f"{link_data['id']}.txt")
                    with open(cache_path, 'w', encoding='utf-8') as f: f.write(content)
                    return link_data
            return None

        results = await asyncio.gather(*(process_link(ld) for ld in tasks_to_run))

    for result in results:
        if result:
            total_estimated_nodes += int(result['estimated_raw_node_count'])
            links_for_debian.append(result)
            if total_estimated_nodes >= CONFIG['RAW_NODE_ESTIMATE_TARGET']:
                break

    task_urls = [f"https://raw.githubusercontent.com/{repo}/main/{CONFIG['CACHE_DIR']}/{link['id']}.txt" for link in links_for_debian]
    with open(CONFIG['OUTPUT_TASK_LIST'], 'w', encoding='utf-8') as f: f.write("\n".join(task_urls))
    print(f"\n✅ 任务链接集 {CONFIG['OUTPUT_TASK_LIST']} 已生成，包含 {len(task_urls)} 个独立的源镜像。")

    # 回写数据库
    header = links_db[0].keys() if links_db else []
    if 'estimated_raw_node_count' not in header and links_db: header.append('estimated_raw_node_count')
    if header:
        with open(CONFIG['DB_FILE'], 'w', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=header, extrasaction='ignore'); writer.writeheader(); writer.writerows(links_db)

if __name__ == "__main__":
    asyncio.run(main())

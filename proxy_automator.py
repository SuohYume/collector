# proxy_automator.py
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
from datetime import datetime, timezone

# --- 配置区 ---
CONFIG = {
    "DB_FILE": "link_database.csv",
    "REPORT_FILE": "quality_report.csv",
    "CACHE_DIR": "cached_subs",
    "OUTPUT_TASK_LIST": "sub_list_for_testing.txt",
    "RAW_NODE_ESTIMATE_TARGET": 50000,
    "REQUEST_TIMEOUT": 8,
    "MAX_FAILURE_STREAK": 10,
}

# --- 全局SSL上下文 ---
SSL_CONTEXT = ssl.create_default_context()
SSL_CONTEXT.check_hostname = False
SSL_CONTEXT.verify_mode = ssl.CERT_NONE

# --- 辅助函数 ---
def ensure_id(row):
    """为没有ID的行生成一个唯一的、持久的ID"""
    if 'id' not in row or not row['id']:
        if 'url' in row and row['url']:
            row['id'] = f"sub_{md5(row['url'].encode()).hexdigest()[:10]}"
            return True
    return False

def get_node_count_from_content(text):
    """轻量级解析，只为快速估算节点数量"""
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
    """智能探针获取内容"""
    headers = {'User-Agent': 'Clash'}
    async def get(target_url):
        try:
            async with session.get(target_url, headers=headers, timeout=CONFIG['REQUEST_TIMEOUT'], ssl=SSL_CONTEXT) as response:
                response.raise_for_status()
                return await response.text()
        except Exception: return None

    if url_type == 'api': return await get(url.rstrip('/') + '/clash/proxies')
    if url_type == 'raw': return await get(url)
    
    content = await get(url.rstrip('/') + '/clash/proxies')
    if content and get_node_count_from_content(content) > 0: return content
    
    content = await get(url)
    if content and get_node_count_from_content(content) > 0: return content
    
    print(f"❌ 自动探测失败: {url}")
    return None

async def process_link(session, link_data):
    """处理单个链接：获取内容、估算数量、写入缓存"""
    content = await fetch_content(session, link_data['url'], link_data.get('type', 'auto'))
    if not content:
        return None
    
    node_count = get_node_count_from_content(content)
    if node_count > 0:
        link_data['estimated_raw_node_count'] = node_count
        cache_path = os.path.join(CONFIG['CACHE_DIR'], f"{link_data['id']}.txt")
        with open(cache_path, 'w', encoding='utf-8') as f:
            f.write(content)
        return link_data
    return None

async def main():
    # 1. 初始化和数据库ID管理
    if not os.path.exists(CONFIG['CACHE_DIR']): os.makedirs(CONFIG['CACHE_DIR'])
    for f in os.listdir(CONFIG['CACHE_DIR']): os.remove(os.path.join(CONFIG['CACHE_DIR'], f))

    try:
        with open(CONFIG['DB_FILE'], 'r', newline='', encoding='utf-8') as f:
            links_db = list(csv.DictReader(f))
    except FileNotFoundError:
        print(f"主数据库 {CONFIG['DB_FILE']} 未找到。请创建它并至少包含'url'列。")
        return

    db_changed = False
    # 【重大修正】使用正确的循环来确保为所有新链接生成ID
    for row in links_db:
        if ensure_id(row):
            db_changed = True
    
    # 2. 读取“差生报告”并更新健康度
    try:
        with open(CONFIG['REPORT_FILE'], 'r', newline='', encoding='utf-8') as f:
            naughty_list = {row['failed_id'] for row in csv.DictReader(f)}
    except FileNotFoundError:
        naughty_list = set()

    repo = os.environ.get('GITHUB_REPOSITORY', 'user/repo')
    now = datetime.now(timezone.utc).isoformat()
    for row in links_db:
        row['failure_streak'] = int(row.get('failure_streak', 0))
        # 【重大修正】确保所有row都有'id'键后再进行后续操作
        if row.get('id') in naughty_list:
            row['failure_streak'] += 1
        else:
            row['failure_streak'] = 0
        
        if row['failure_streak'] >= CONFIG['MAX_FAILURE_STREAK']:
            row['status'] = 'dead'
        else:
            row['status'] = 'active'
        row['last_report_time'] = now

    # 3. 按优先级排序链接
    def get_priority(row):
        status_map = {'active': 0, 'new': 1, 'unstable': 2, 'dead': 3}
        return (status_map.get(row.get('status', 'new'), 9), row['failure_streak'])
    links_db.sort(key=get_priority)

    # 4. 【真并发】估算凑量与全量缓存
    total_estimated_nodes = 0
    links_for_debian = []
    
    tasks_to_run = [ld for ld in links_db if ld.get('status') != 'dead']

    async with aiohttp.ClientSession() as session:
        results = await asyncio.gather(*(process_link(session, ld) for ld in tasks_to_run))

    for result in results:
        if result:
            if total_estimated_nodes < CONFIG['RAW_NODE_ESTIMATE_TARGET']:
                total_estimated_nodes += int(result.get('estimated_raw_node_count', 0))
                links_for_debian.append(result)
            # 更新主数据库中对应链接的估算值
            for ld in links_db:
                if ld['id'] == result['id']:
                    ld['estimated_raw_node_count'] = result['estimated_raw_node_count']
                    break

    print(f"凑量完成，共选中 {len(links_for_debian)} 个链接，估算节点总数: {total_estimated_nodes}")
    
    # 5. 生成最终的“任务链接集”
    task_urls = [f"https://raw.githubusercontent.com/{repo}/main/{CONFIG['CACHE_DIR']}/{link['id']}.txt" for link in links_for_debian]
    with open(CONFIG['OUTPUT_TASK_LIST'], 'w', encoding='utf-8') as f:
        f.write("\n".join(task_urls))
    print(f"✅ 任务链接集 {CONFIG['OUTPUT_TASK_LIST']} 已生成。")

    # 6. 回写更新后的主数据库
    header = ['id', 'url', 'type', 'status', 'last_report_time', 'failure_streak', 'estimated_raw_node_count']
    with open(CONFIG['DB_FILE'], 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=header, extrasaction='ignore')
        writer.writeheader()
        writer.writerows(links_db)

if __name__ == "__main__":
    asyncio.run(main())

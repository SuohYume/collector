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
from datetime import datetime, timezone, timedelta
from config import CONFIG

# --- 全局SSL上下文，避免重复创建 ---
SSL_CONTEXT = ssl.create_default_context()
SSL_CONTEXT.check_hostname = False
SSL_CONTEXT.verify_mode = ssl.CERT_NONE

# --- 核心功能函数 ---

def ensure_id(row):
    """为新链接自动生成并分配一个唯一的、持久的ID"""
    if 'id' not in row or not row['id']:
        row['id'] = f"sub_{md5(row['url'].encode()).hexdigest()[:10]}"
        return True
    return False

def get_node_count_from_content(text):
    """轻量级解析，只为快速估算节点数量"""
    if not text: return 0
    try:
        content = yaml.safe_load(text)
        if isinstance(content, dict) and 'proxies' in content: return len(content['proxies'])
    except Exception: pass
    try:
        decoded_text = base64.b64decode(''.join(text.split())).decode('utf-8')
        return len(re.findall(r"(vmess|vless|ss|trojan)://", decoded_text, re.IGNORECASE))
    except Exception: pass
    return len(re.findall(r"(vmess|vless|ss|trojan)://", text, re.IGNORECASE))

async def fetch_raw_content(session, url, url_type):
    """【智能探针】获取原始内容"""
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

def load_and_update_database():
    """读取数据库和报告，更新健康度，并自动管理ID"""
    try:
        with open(CONFIG['DB_FILE'], 'r', newline='', encoding='utf-8') as f:
            links_db = list(csv.DictReader(f))
    except FileNotFoundError: return []

    db_changed = any(ensure_id(row) for row in links_db)

    try:
        with open(CONFIG['REPORT_FILE'], 'r', newline='', encoding='utf-8') as f:
            naughty_list = {row['failed_id'] for row in csv.DictReader(f)}
    except FileNotFoundError: naughty_list = set()

    now_iso = datetime.now(timezone.utc).isoformat()
    for row in links_db:
        row['failure_streak'] = int(row.get('failure_streak', 0))
        if row['id'] in naughty_list:
            row['failure_streak'] += 1
            row['status'] = 'unstable'
        else:
            row['failure_streak'] = 0
            row['status'] = 'active'
        
        if row['failure_streak'] >= CONFIG['MAX_FAILURE_STREAK']:
            row['status'] = 'dead'
        row['last_report_time'] = now_iso
    
    if db_changed: # 如果有新ID生成，立即回写
        header = links_db[0].keys() if links_db else []
        if header:
            with open(CONFIG['DB_FILE'], 'w', newline='', encoding='utf-8') as f:
                writer = csv.DictWriter(f, fieldnames=header); writer.writeheader(); writer.writerows(links_db)

    return links_db

async def run_collection_pipeline(links_db):
    """【真并发】执行链接获取、估算和缓存的核心流水线"""
    
    def get_priority(row):
        status_map = {'active': 0, 'new': 1, 'unstable': 2, 'dead': 3}
        return (status_map.get(row.get('status', 'new'), 9), row['failure_streak'])
    
    sorted_links = sorted(links_db, key=get_priority)

    total_estimated_nodes = 0
    links_to_cache = []

    # 筛选出需要处理的链接
    for link_data in sorted_links:
        if link_data.get('status') == 'dead': continue
        if total_estimated_nodes >= CONFIG['RAW_NODE_ESTIMATE_TARGET']: break
        
        # 使用之前估算的值（如果有），避免重复获取
        estimated_count = int(link_data.get('estimated_raw_node_count', 0))
        if estimated_count > 0:
            total_estimated_nodes += estimated_count
            links_to_cache.append(link_data)
        else: # 对于没有估算值的新链接，加入待办
            links_to_cache.append(link_data)

    # 并发获取所有待办链接的内容
    async with aiohttp.ClientSession() as session:
        tasks = [fetch_raw_content(session, link['url'], link.get('type', 'auto')) for link in links_to_cache]
        contents = await asyncio.gather(*tasks)

    # 处理结果并写入缓存
    final_task_list = []
    repo = os.environ.get('GITHUB_REPOSITORY', 'user/repo')
    
    for i, content in enumerate(contents):
        link_data = links_to_cache[i]
        if not content: continue
        
        node_count = get_node_count_from_content(content)
        if node_count > 0:
            link_data['estimated_raw_node_count'] = node_count
            cache_path = os.path.join(CONFIG['CACHE_DIR'], f"{link_data['id']}.txt")
            with open(cache_path, 'w', encoding='utf-8') as f:
                f.write(content)
            
            final_task_list.append(f"https://raw.githubusercontent.com/{repo}/main/{cache_path}")
            
    return final_task_list

def write_output_files(task_list, db):
    """写入所有最终产物"""
    with open(CONFIG['TASK_LIST_FILE'], 'w', encoding='utf-8') as f:
        f.write("\n".join(task_list))
    print(f"✅ 任务链接集 {CONFIG['TASK_LIST_FILE']} 已生成，包含 {len(task_list)} 个独立的源镜像。")

    # 回写最终更新的数据库
    header = db[0].keys() if db else []
    if header:
        with open(CONFIG['DB_FILE'], 'w', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=header, extrasaction='ignore')
            writer.writeheader()
            writer.writerows(db)

async def main():
    if not os.path.exists(CONFIG['CACHE_DIR']): os.makedirs(CONFIG['CACHE_DIR'])
    for f in os.listdir(CONFIG['CACHE_DIR']): os.remove(os.path.join(CONFIG['CACHE_DIR'], f))
    
    # 1. 更新健康度
    links_database = load_and_update_database()
    if not links_database: return

    # 2. 并发执行收集和缓存
    final_task_urls = await run_collection_pipeline(links_database)

    # 3. 写入最终文件
    write_output_files(final_task_urls, links_database)


if __name__ == "__main__":
    asyncio.run(main())

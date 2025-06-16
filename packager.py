import os
import csv
import asyncio
import aiohttp
import ssl
from hashlib import md5
from datetime import datetime, timezone

# --- 全局配置 ---
DB_FILE = "link_database.csv"
REPORT_FILE = "quality_report.csv"
CACHE_DIR = "cached_subs"
OUTPUT_TASK_LIST = "sub_list_for_testing.txt"
REQUEST_TIMEOUT = 10
MAX_FAILURE_STREAK = 10

# --- 全局SSL上下文 ---
SSL_CONTEXT = ssl.create_default_context()
SSL_CONTEXT.check_hostname = False
SSL_CONTEXT.verify_mode = ssl.CERT_NONE

def ensure_id(row: dict) -> bool:
    """为没有ID的行生成一个唯一的、持久的ID"""
    if 'id' not in row or not row['id']:
        if 'url' in row and row['url']:
            row['id'] = f"sub_{md5(row['url'].encode()).hexdigest()[:10]}"
            return True
    return False

async def fetch_content(session: aiohttp.ClientSession, url: str) -> str | None:
    """获取URL原始内容，忽略SSL错误"""
    headers = {'User-Agent': 'Clash'}
    try:
        # 统一添加/clash/proxies尝试，如果失败则尝试原始链接
        try:
            api_url = url.rstrip('/') + '/clash/proxies'
            async with session.get(api_url, headers=headers, timeout=REQUEST_TIMEOUT, ssl=SSL_CONTEXT) as response:
                response.raise_for_status()
                print(f"✅ [API] 成功获取: {url}")
                return await response.text()
        except aiohttp.ClientError:
            # API模式失败，尝试原始链接
            async with session.get(url, headers=headers, timeout=REQUEST_TIMEOUT, ssl=SSL_CONTEXT) as response:
                response.raise_for_status()
                print(f"✅ [Raw] 成功获取: {url}")
                return await response.text()
    except Exception as e:
        print(f"❌ 获取失败: {url}, 原因: {e}")
        return None

async def process_link(session: aiohttp.ClientSession, link_data: dict) -> dict | None:
    """处理单个链接：获取内容并写入缓存"""
    content = await fetch_content(session, link_data['url'])
    if content:
        cache_path = os.path.join(CACHE_DIR, f"{link_data['id']}.txt")
        with open(cache_path, 'w', encoding='utf-8') as f:
            f.write(content)
        link_data['cache_path'] = cache_path
        return link_data
    return None

async def main():
    # 1. 初始化
    if not os.path.exists(CACHE_DIR):
        os.makedirs(CACHE_DIR)
    for f in os.listdir(CACHE_DIR):
        os.remove(os.path.join(CACHE_DIR, f))

    # 2. 读取数据库并自动管理ID
    try:
        with open(DB_FILE, 'r', newline='', encoding='utf-8') as f:
            links_db = list(csv.DictReader(f))
    except FileNotFoundError:
        print(f"未找到主数据库 {DB_FILE}。")
        return

    db_changed = any(ensure_id(row) for row in links_db)

    # 3. 读取“差生报告”并更新健康度
    try:
        with open(REPORT_FILE, 'r', newline='', encoding='utf-8') as f:
            naughty_list = {row['failed_id'] for row in csv.DictReader(f)}
    except FileNotFoundError:
        naughty_list = set()

    repo = os.environ.get('GITHUB_REPOSITORY', 'user/repo')
    now = datetime.now(timezone.utc).isoformat()
    
    for row in links_db:
        row['failure_streak'] = int(row.get('failure_streak', 0))
        if row.get('id') in naughty_list:
            row['failure_streak'] += 1
        else:
            row['failure_streak'] = 0
        
        row['status'] = 'dead' if row['failure_streak'] >= MAX_FAILURE_STREAK else 'active'
        row['last_report_time'] = now

    # 4. 按优先级排序并并发抓取和缓存
    def get_priority(row):
        status_map = {'active': 0, 'new': 1, 'unstable': 2, 'dead': 3}
        return (status_map.get(row.get('status', 'new'), 9), row['failure_streak'])
    links_db.sort(key=get_priority)

    tasks_to_run = [ld for ld in links_db if ld.get('status') != 'dead']
    
    print(f"--- 准备处理 {len(tasks_to_run)} 个健康链接 ---")
    
    async with aiohttp.ClientSession() as session:
        results = await asyncio.gather(*(process_link(session, ld) for ld in tasks_to_run))

    successful_links = [res for res in results if res]
    
    # 5. 生成最终的“任务链接集”
    task_urls = [f"https://raw.githubusercontent.com/{repo}/main/{link['cache_path']}" for link in successful_links]
    with open(OUTPUT_TASK_LIST, 'w', encoding='utf-8') as f:
        f.write("\n".join(task_urls))
    
    print(f"\n✅ 最终任务链接集 {OUTPUT_TASK_LIST} 已生成，包含 {len(task_urls)} 个可供测试的镜像链接。")

    # 6. 回写更新后的主数据库
    final_header = ['id', 'url', 'status', 'last_report_time', 'failure_streak']
    with open(DB_FILE, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=final_header, extrasaction='ignore')
        writer.writeheader()
        writer.writerows(links_db)

if __name__ == "__main__":
    asyncio.run(main())

import os
import csv
import asyncio
import aiohttp
import ssl
import re
from hashlib import md5
from datetime import datetime, timezone
from config import CONFIG # 导入配置

# --- 全局SSL上下文 ---
SSL_CONTEXT = ssl.create_default_context()
SSL_CONTEXT.check_hostname = False
SSL_CONTEXT.verify_mode = ssl.CERT_NONE

# --- 辅助函数 ---
def ensure_id(row: dict) -> bool:
    if not row.get('id'):
        if row.get('url'):
            row['id'] = f"sub_{md5(row['url'].encode()).hexdigest()[:10]}"
            return True
    return False

def get_node_count_from_content(text: str) -> int:
    if not text: return 0
    import re
    if "proxies:" in text or re.search(r"(vmess|vless|ss|trojan)://", text, re.IGNORECASE): return 1
    try:
        import base64
        if base64.b64decode(''.join(text.split())): return 1
    except Exception: return 0
    return 0

async def fetch_content(session: aiohttp.ClientSession, url: str) -> str | None:
    headers = {'User-Agent': 'Clash'}
    # 【关键】从配置中读取代理地址
    proxy = CONFIG.get("FETCHER_PROXY") or None
    try:
        # 简化逻辑：所有链接都直接访问，不再尝试拼接后缀
        async with session.get(url, headers=headers, timeout=CONFIG['REQUEST_TIMEOUT'], ssl=SSL_CONTEXT, proxy=proxy) as response:
            response.raise_for_status()
            print(f"✅ 成功获取: {url}")
            return await response.text()
    except Exception as e:
        print(f"❌ 获取失败: {url}, 原因: {e}")
        return None

async def process_link(session: aiohttp.ClientSession, link_data: dict) -> dict | None:
    content = await fetch_content(session, link_data['url'])
    if content:
        node_count = get_node_count_from_content(content)
        link_data['estimated_raw_node_count'] = node_count
        cache_path = os.path.join(CONFIG['CACHE_DIR'], f"{link_data['id']}.txt")
        with open(cache_path, 'w', encoding='utf-8') as f:
            f.write(content)
        return link_data
    return None

# --- 主执行逻辑 ---
async def main():
    # 1. 初始化和数据库ID管理
    try:
        with open(CONFIG['DB_FILE'], 'r', newline='', encoding='utf-8') as f:
            links_db = list(csv.DictReader(f))
    except FileNotFoundError:
        print(f"主数据库 {CONFIG['DB_FILE']} 未找到。")
        return

    db_header = list(links_db[0].keys()) if links_db else ['url']
    if 'id' not in db_header: db_header.insert(0, 'id')

    db_changed = False
    for row in links_db:
        if ensure_id(row):
            db_changed = True

    if db_changed:
        with open(CONFIG['DB_FILE'], 'w', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=db_header, extrasaction='ignore'); writer.writeheader(); writer.writerows(links_db)

    # 2. 读取报告并更新健康度
    try:
        with open(CONFIG['REPORT_FILE'], 'r', newline='', encoding='utf-8') as f:
            naughty_list = {row['failed_id'] for row in csv.DictReader(f)}
    except FileNotFoundError: naughty_list = set()

    now_iso = datetime.now(timezone.utc).isoformat()
    for row in links_db:
        row['failure_streak'] = int(row.get('failure_streak') or 0)
        if row.get('id') in naughty_list: row['failure_streak'] += 1
        else: row['failure_streak'] = 0
        row['status'] = 'dead' if row['failure_streak'] >= CONFIG['MAX_FAILURE_STREAK'] else 'active'
        row['last_report_time'] = now_iso

    # 3. 排序并并发获取内容
    def get_priority(row):
        return (0 if row.get('status') == 'active' else 1, row['failure_streak'])
    links_db.sort(key=get_priority)

    if not os.path.exists(CONFIG['CACHE_DIR']): os.makedirs(CONFIG['CACHE_DIR'])
    for f in os.listdir(CONFIG['CACHE_DIR']): os.remove(os.path.join(CONFIG['CACHE_DIR'], f))

    tasks_to_run = [ld for ld in links_db if ld.get('status') != 'dead']
    print(f"--- 准备处理 {len(tasks_to_run)} 个健康链接 ---")
    if not tasks_to_run: return

    total_estimated_nodes = 0
    links_for_debian = []

    connector = aiohttp.TCPConnector(limit=CONFIG['MAX_CONCURRENT_REQUESTS'])
    async with aiohttp.ClientSession(connector=connector) as session:
        results = await asyncio.gather(*(process_link(session, ld) for ld in tasks_to_run))

    for result in results:
        if result:
            current_count = int(result.get('estimated_raw_node_count', 0))
            if total_estimated_nodes < CONFIG['RAW_NODE_ESTIMATE_TARGET']:
                if current_count > 0:
                    total_estimated_nodes += current_count
                    links_for_debian.append(result)
            for ld in links_db:
                if ld['id'] == result['id']:
                    ld['estimated_raw_node_count'] = current_count
                    break

    print(f"凑量完成，共选中 {len(links_for_debian)} 个链接，估算节点总数: {total_estimated_nodes}")

    # 4. 生成产物并回写数据库
    repo = os.environ.get('GITHUB_REPOSITORY', 'user/repo')
    task_urls = [f"https://raw.githubusercontent.com/{repo}/main/{CONFIG['CACHE_DIR']}/{link['id']}.txt" for link in links_for_debian]
    with open(CONFIG['OUTPUT_TASK_LIST'], 'w', encoding='utf-8') as f:
        f.write("\n".join(task_urls))
    print(f"✅ 任务链接集 {CONFIG['OUTPUT_TASK_LIST']} 已生成。")

    final_header = ['id', 'url', 'type', 'status', 'last_report_time', 'failure_streak', 'estimated_raw_node_count']
    with open(CONFIG['DB_FILE'], 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=final_header, extrasaction='ignore'); writer.writeheader(); writer.writerows(links_db)

if __name__ == "__main__":
    asyncio.run(main())

import os
import csv
import asyncio
import aiohttp
import ssl
import re
from hashlib import md5
from datetime import datetime, timezone
from config import CONFIG

# --- 全局SSL上下文 ---
SSL_CONTEXT = ssl.create_default_context()
SSL_CONTEXT.check_hostname = False
SSL_CONTEXT.verify_mode = ssl.CERT_NONE

# --- 辅助函数 ---
def ensure_id(row: dict) -> bool:
    """为没有ID的行生成一个唯一的、持久的ID"""
    if not row.get('id'):
        if row.get('url'):
            row['id'] = f"sub_{md5(row['url'].encode()).hexdigest()[:10]}"
            return True
    return False

def get_node_count_from_content(text: str) -> int:
    """轻量级解析，只为快速估算节点数量"""
    if not text: return 0
    # 这是一个简化的估算，实际节点数以您本地工具为准
    return len(re.findall(r"(ss|trojan|vless|vmess)://", text, re.IGNORECASE))

async def fetch_content(session: aiohttp.ClientSession, url: str) -> str | None:
    """获取URL原始内容"""
    headers = {'User-Agent': 'Clash'}
    proxy = CONFIG.get("FETCHER_PROXY") or None # 从配置读取代理
    try:
        async with session.get(url, headers=headers, timeout=CONFIG['REQUEST_TIMEOUT'], ssl=SSL_CONTEXT, proxy=proxy) as response:
            response.raise_for_status()
            print(f"✅ 成功获取: {url}")
            return await response.text()
    except Exception as e:
        print(f"❌ 获取失败: {url}, 原因: {e}")
        return None

async def process_link(session: aiohttp.ClientSession, link_data: dict) -> dict | None:
    """处理单个链接：获取内容、估算数量、写入缓存"""
    content = await fetch_content(session, link_data['url'])
    if content:
        node_count = get_node_count_from_content(content)
        # 即使内容为空，也认为获取成功，交由本地判断
        link_data['estimated_raw_node_count'] = node_count
        cache_path = os.path.join(CONFIG['CACHE_DIR'], f"{link_data['id']}.txt")
        with open(cache_path, 'w', encoding='utf-8') as f:
            f.write(content)
        return link_data
    return None

# --- 主执行逻辑 ---
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

    db_header = list(links_db[0].keys()) if links_db else ['id', 'url', 'type', 'status', 'last_report_time', 'failure_streak', 'estimated_raw_node_count']
    if 'id' not in db_header: db_header.insert(0, 'id')

    db_changed = any(ensure_id(row) for row in links_db)

    if db_changed:
        print("检测到新链接，正在自动分配ID并回写数据库...")
        with open(CONFIG['DB_FILE'], 'w', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=db_header, extrasaction='ignore')
            writer.writeheader()
            writer.writerows(links_db)
        print("ID分配完成。")

    # 2. 读取“差生报告”并更新健康度
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

    # 3. 按优先级排序并并发获取内容
    def get_priority(row):
        return (0 if row.get('status') == 'active' else 1, row['failure_streak'])
    links_db.sort(key=get_priority)

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

    with open(CONFIG['DB_FILE'], 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=db_header, extrasaction='ignore')
        writer.writeheader()
        writer.writerows(links_db)

if __name__ == "__main__":
    asyncio.run(main())

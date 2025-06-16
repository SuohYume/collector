import os
import csv
import asyncio
import aiohttp
import ssl
from hashlib import md5

# --- 配置区 ---
# 您可以按需修改这里的参数
DB_FILE = "link_database.csv"
CACHE_DIR = "cached_subs"
MANIFEST_FILE = "cache_manifest.txt"
REQUEST_TIMEOUT = 15  # 请求超时（秒）
MAX_CONCURRENT_REQUESTS = 100 # 最大并发请求数

# 【重要】请在这里填入您本地使用的、可靠的GitHub代理，以确保云端也能成功访问
# 留空则不使用代理
FETCHER_PROXY = "https://github.serein.cc/"

# --- 全局SSL上下文 ---
SSL_CONTEXT = ssl.create_default_context()
SSL_CONTEXT.check_hostname = False
SSL_CONTEXT.verify_mode = ssl.CERT_NONE

def ensure_id(row: dict) -> bool:
    """为没有ID的行生成一个唯一的、持久的ID"""
    if not row.get('id'):
        if row.get('url'):
            # 使用URL的md5哈希值的前10位作为稳定ID
            row['id'] = f"sub_{md5(row['url'].encode()).hexdigest()[:10]}"
            return True
    return False

async def fetch_and_save(session: aiohttp.ClientSession, link_data: dict):
    """获取原始内容并直接保存"""
    url = link_data.get('url')
    file_id = link_data.get('id')
    if not url or not file_id:
        return None

    headers = {'User-Agent': 'Mozilla/5.0'}
    proxy = FETCHER_PROXY or None

    try:
        async with session.get(url, headers=headers, timeout=REQUEST_TIMEOUT, ssl=SSL_CONTEXT, proxy=proxy) as response:
            # 即使是4xx或5xx错误，也读取内容，因为有些错误页面可能也包含信息
            content = await response.text()
            # 只有在请求成功(2xx)且内容非空时，才认为是成功
            if response.ok and content:
                cache_path = os.path.join(CACHE_DIR, f"{file_id}.txt")
                with open(cache_path, 'w', encoding='utf-8') as f:
                    f.write(content)
                print(f"✅ 缓存成功: {url}")
                return cache_path
            else:
                print(f"⚠️ 内容为空或状态码非200: {url} (Status: {response.status})")
                return None
    except Exception as e:
        print(f"❌ 获取失败: {url}, 原因: {e}")
        return None

async def main():
    # 1. 确保缓存目录存在并清空
    if not os.path.exists(CACHE_DIR):
        os.makedirs(CACHE_DIR)
    for f in os.listdir(CACHE_DIR):
        os.remove(os.path.join(CACHE_DIR, f))

    # 2. 读取数据库并自动管理ID
    try:
        with open(DB_FILE, 'r', newline='', encoding='utf-8') as f:
            links_db = list(csv.DictReader(f))
    except FileNotFoundError:
        print(f"主数据库 {DB_FILE} 未找到，请创建它并至少包含'url'列。")
        return

    db_header = list(links_db[0].keys()) if links_db else ['url']
    if 'id' not in db_header:
        db_header.insert(0, 'id')

    db_changed = False
    for row in links_db:
        if ensure_id(row):
            db_changed = True

    if db_changed:
        print("检测到新链接，正在自动分配ID并回写数据库...")
        with open(DB_FILE, 'w', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=db_header, extrasaction='ignore')
            writer.writeheader()
            writer.writerows(links_db)
        print("ID分配完成。")

    # 3. 并发执行所有下载和保存任务
    print(f"--- 准备缓存 {len(links_db)} 个链接 ---")

    connector = aiohttp.TCPConnector(limit=MAX_CONCURRENT_REQUESTS)
    async with aiohttp.ClientSession(connector=connector) as session:
        tasks = [fetch_and_save(session, ld) for ld in links_db]
        results = await asyncio.gather(*tasks)

    # 4. 生成最终的缓存清单文件
    successful_caches = [res for res in results if res]
    repo = os.environ.get('GITHUB_REPOSITORY', 'user/repo')

    manifest_urls = [f"https://raw.githubusercontent.com/{repo}/main/{cache_path}" for cache_path in successful_caches]

    with open(MANIFEST_FILE, 'w', encoding='utf-8') as f:
        f.write("\n".join(manifest_urls))

    print(f"\n--- ✨ 操作完成 ---")
    print(f"✅ 成功缓存了 {len(successful_caches)} 个订阅链接。")
    print(f"✅ 缓存清单文件 '{MANIFEST_FILE}' 已生成，可供您的本地工具使用。")

if __name__ == "__main__":
    asyncio.run(main())

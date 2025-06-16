import os
import csv
import asyncio
import aiohttp
import ssl
from hashlib import md5

# --- 文件定义 ---
DB_FILE = "link_database.csv"
CACHE_DIR = "cached_subs"
MANIFEST_FILE = "cache_manifest.txt"

# --- 网络配置 ---
REQUEST_TIMEOUT = 15  # 请求超时（秒）
MAX_CONCURRENT_REQUESTS = 100 # 最大并发请求数

# --- 全局SSL上下文 (忽略证书验证错误，以最大可能获取内容) ---
SSL_CONTEXT = ssl.create_default_context()
SSL_CONTEXT.check_hostname = False
SSL_CONTEXT.verify_mode = ssl.CERT_NONE

async def fetch_and_save(session: aiohttp.ClientSession, url: str):
    """
    获取原始内容并直接保存。
    它只做一件事：尝试下载，成功就保存，失败就报告。
    """
    if not url:
        return None

    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'}

    try:
        async with session.get(url, headers=headers, timeout=REQUEST_TIMEOUT, ssl=SSL_CONTEXT) as response:
            # 即使是4xx或5xx错误，也读取内容，因为有些错误页面可能也包含信息
            content = await response.text()
            # 只有在请求成功(2xx)且内容非空时，才认为是成功
            if response.ok and content:
                # 使用URL的md5哈希值作为稳定、唯一的文件名
                file_hash = md5(url.encode()).hexdigest()
                cache_path = os.path.join(CACHE_DIR, f"{file_hash}.txt")

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

    # 2. 读取数据库中的URL
    try:
        with open(DB_FILE, 'r', newline='', encoding='utf-8') as f:
            # 直接读取第一列作为URL，忽略表头
            urls_to_fetch = [row[0] for row in csv.reader(f) if row and row[0].strip() and not row[0].strip().startswith('#')]
            # 移除可能存在的表头
            if urls_to_fetch and urls_to_fetch[0].lower() == 'url':
                urls_to_fetch.pop(0)
    except FileNotFoundError:
        print(f"主数据库 {DB_FILE} 未找到。")
        return

    # 3. 并发执行所有下载和保存任务
    print(f"--- 准备缓存 {len(urls_to_fetch)} 个链接 ---")

    connector = aiohttp.TCPConnector(limit=MAX_CONCURRENT_REQUESTS)
    async with aiohttp.ClientSession(connector=connector) as session:
        tasks = [fetch_and_save(session, url) for url in urls_to_fetch]
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

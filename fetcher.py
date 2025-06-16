import os
import csv
import asyncio
import aiohttp
import ssl
import re
import yaml
import base64
from config import CONFIG

# --- 全局SSL上下文，避免重复创建，提高效率 ---
SSL_CONTEXT = ssl.create_default_context()
SSL_CONTEXT.check_hostname = False
SSL_CONTEXT.verify_mode = ssl.CERT_NONE

def get_node_count_from_content(text: str) -> int:
    """
    一个轻量级的函数，仅用于“智能探针”判断返回内容是否有效，
    而不进行完整的节点解析。
    """
    if not text:
        return 0
    # 快速检查，只要包含任何一个可能的关键字，就认为内容有效
    if "proxies:" in text or "vmess://" in text or "ss://" in text or "trojan://" in text or "vless://" in text:
        return 1 # 返回一个大于0的数即可
    try:
        # 尝试base64解码后再次检查
        decoded_text = base64.b64decode(''.join(text.split())).decode('utf-8')
        if "vmess://" in decoded_text or "ss://" in decoded_text:
            return 1
    except Exception:
        pass
    return 0

async def fetch_content(session: aiohttp.ClientSession, url: str, url_type: str) -> str | None:
    """
    【智能探针】获取单个URL的原始内容。
    - 支持自动探测（api优先）和手动强制类型。
    - 忽略SSL证书错误。
    - 返回获取到的文本内容或None。
    """
    headers = {'User-Agent': 'Clash'}

    async def get(target_url: str) -> str | None:
        try:
            async with session.get(target_url, headers=headers, timeout=CONFIG['REQUEST_TIMEOUT'], ssl=SSL_CONTEXT) as response:
                response.raise_for_status()
                return await response.text()
        except Exception as e:
            # print(f"Debug: Fetch failed for {target_url} -> {e}")
            return None

    # 如果手动指定了类型，则只尝试该类型
    if url_type == 'api':
        return await get(url.rstrip('/') + '/clash/proxies')
    if url_type == 'raw':
        return await get(url)
    
    # --- 自动探测模式 ---
    # 1. 优先尝试API模式，因为其意图最明确
    api_url = url.rstrip('/') + '/clash/proxies'
    content = await get(api_url)
    if content and get_node_count_from_content(content) > 0:
        print(f"    - Probe successful (API): {url}")
        return content
    
    # 2. API模式失败或内容无效，后备尝试Raw直链模式
    content = await get(url)
    if content and get_node_count_from_content(content) > 0:
        print(f"    - Probe successful (Raw): {url}")
        return content
    
    print(f"    - Probe failed for: {url}")
    return None

async def main():
    """
    主执行函数：读取任务清单，并发抓取，写入缓存和结果。
    """
    # 确保缓存目录存在且清空
    if not os.path.exists(CONFIG['CACHE_DIR']):
        os.makedirs(CONFIG['CACHE_DIR'])
    for f in os.listdir(CONFIG['CACHE_DIR']):
        os.remove(os.path.join(CONFIG['CACHE_DIR'], f))

    try:
        with open(CONFIG['TASK_LIST_FILE'], 'r', newline='', encoding='utf-8') as f:
            tasks = list(csv.DictReader(f))
    except FileNotFoundError:
        print("Fetcher: Task list not found. Is curator.py running correctly? Exiting.")
        return

    print(f"Fetcher: Starting to fetch content for {len(tasks)} links...")
    
    fetch_results = []
    # 创建一个TCP连接器，并设置并发连接数限制
    connector = aiohttp.TCPConnector(limit=CONFIG['MAX_CONCURRENT_REQUESTS'])
    async with aiohttp.ClientSession(connector=connector) as session:
        # 创建所有并发任务
        aio_tasks = [fetch_content(session, t['url'], t['type']) for t in tasks]
        # 等待所有任务完成
        contents = await asyncio.gather(*aio_tasks)

    successful_fetches = 0
    # 处理所有任务的结果
    for i, content in enumerate(contents):
        task = tasks[i]
        if content:
            successful_fetches += 1
            cache_path = os.path.join(CONFIG['CACHE_DIR'], f"{task['id']}.txt")
            with open(cache_path, 'w', encoding='utf-8') as f:
                f.write(content)
            fetch_results.append({'id': task['id'], 'cache_path': cache_path, 'status': 'success'})
        else:
            fetch_results.append({'id': task['id'], 'cache_path': '', 'status': 'failure'})

    # 将获取结果写入中间文件，供下一个模块使用
    with open(CONFIG['FETCH_RESULTS_FILE'], 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=['id', 'cache_path', 'status'])
        writer.writeheader()
        writer.writerows(fetch_results)
    
    print(f"Fetcher: Finished. Successfully cached content for {successful_fetches}/{len(tasks)} links.")

if __name__ == "__main__":
    asyncio.run(main())

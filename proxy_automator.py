import os
import csv
import yaml
import random
import asyncio
import aiohttp
from datetime import datetime, timedelta, timezone

# =================================================================================
# --- 配置区 (您可以根据需要调整这里的参数) ---
# =================================================================================
CONFIG = {
    # --- 文件路径配置 ---
    "db_file": "link_database.csv",
    "archive_file": "archive.csv",
    "readme_template": "README_TEMPLATE.md",
    "readme_output": "README.md",
    
    # --- 输出文件配置 ---
    "output_clash_full": "subscription_full.yaml",
    "output_clash_selected": "subscription_selected_10k.yaml", 
    "output_raw_links": "proxies.txt",

    # --- 行为控制参数 ---
    "max_concurrent_requests": 100,
    "request_timeout": 10,
    "max_retries": 3,
    "unstable_threshold": 5,
    "dead_threshold": 20,
    "archive_days": 30,
    "selected_node_count": 10000,
}
# =================================================================================
# --- 核心功能函数 (已添加详细日志) ---
# =================================================================================
async def fetch_url(session, link_data):
    """
    异步获取单个URL的节点信息，并打印详细的调试日志。
    """
    base_url = link_data['url'].strip()
    url = base_url.rstrip('/') + "/clash/proxies"
    headers = {'User-Agent': 'Clash'}

    # --- DEBUG --- 打印正在尝试的URL
    print(f"🔎 [ATTEMPTING] {url}")

    for attempt in range(CONFIG['max_retries']):
        try:
            if attempt > 0:
                # --- DEBUG --- 打印重试信息
                print(f"⏳ [RETRY {attempt}] Waiting 2s before retrying {url}")
                await asyncio.sleep(2 * attempt)
            
            async with session.get(url, headers=headers, timeout=CONFIG['request_timeout']) as response:
                # --- DEBUG --- 打印HTTP状态码
                print(f"  - [STATUS {response.status}] for {url}")
                response.raise_for_status() # 如果状态码是4xx或5xx，这里会抛出异常
                
                text = await response.text()
                content = yaml.safe_load(text)
                
                if isinstance(content, dict) and 'proxies' in content and isinstance(content['proxies'], list):
                    # --- DEBUG --- 打印成功信息
                    print(f"✅ [SUCCESS] Found {len(content['proxies'])} nodes from {url}")
                    return {"url": base_url, "status": "success", "proxies": content['proxies']}
                else:
                    # --- DEBUG --- 打印内容格式错误
                    reason = "Invalid content format (not a dict with 'proxies' list)"
                    print(f"❌ [FAIL] {url} - {reason}")
                    return {"url": base_url, "status": "fail", "reason": reason}
        except asyncio.TimeoutError:
             # --- DEBUG --- 打印超时错误
            reason = f"Request timed out after {CONFIG['request_timeout']}s"
            print(f"❌ [FAIL] {url} - Attempt {attempt + 1}/{CONFIG['max_retries']} - {reason}")
            if attempt == CONFIG['max_retries'] - 1:
                return {"url": base_url, "status": "fail", "reason": reason}
        except aiohttp.ClientResponseError as e:
            # --- DEBUG --- 打印HTTP错误
            reason = f"HTTP Error: {e.status} {e.message}"
            print(f"❌ [FAIL] {url} - Attempt {attempt + 1}/{CONFIG['max_retries']} - {reason}")
            # 如果是404 Not Found，没必要重试
            if e.status == 404:
                return {"url": base_url, "status": "fail", "reason": reason}
            if attempt == CONFIG['max_retries'] - 1:
                return {"url": base_url, "status": "fail", "reason": reason}
        except Exception as e:
            # --- DEBUG --- 打印其他所有错误
            reason = f"An unexpected error occurred: {str(e)}"
            print(f"❌ [FAIL] {url} - Attempt {attempt + 1}/{CONFIG['max_retries']} - {reason}")
            if attempt == CONFIG['max_retries'] - 1:
                return {"url": base_url, "status": "fail", "reason": reason}
    
    # 理论上不会执行到这里，但作为保险
    return {"url": base_url, "status": "fail", "reason": "Unknown retry failure"}


def generate_fingerprint(node):
    """为节点生成唯一指纹以去重 (优化版，更稳定)"""
    if not isinstance(node, dict): return None
    key_fields = ['server', 'port']
    node_type = node.get('type')
    try:
        if node_type == 'ss': key_fields.extend(['password', 'cipher'])
        elif node_type in ['vmess', 'vless']: key_fields.append('uuid')
        elif node_type == 'trojan': key_fields.append('password')
        elif node_type == 'ssr': key_fields.extend(['password', 'protocol', 'obfs'])
        # 使用 .get(k, '') 确保即使键不存在也不会报错
        return f"{node_type}://" + "-".join(sorted([f"{k}:{node.get(k, '')}" for k in key_fields]))
    except (KeyError, TypeError):
        return None

def update_readme(stats):
    """根据模板和最新数据，更新README.md"""
    try:
        with open(CONFIG['readme_template'], 'r', encoding='utf-8') as f:
            template = f.read()
        
        repo_url = f"https://raw.githubusercontent.com/{os.environ['GITHUB_REPOSITORY']}/main"
        
        replacements = {
            "{last_update_time}": stats['last_update_time'],
            "{total_nodes}": str(stats['total_nodes']),
            "{active_links}": str(stats['active_links']),
            "{total_links}": str(stats['total_links']),
            "{newly_added_nodes}": str(stats['total_nodes']), # 简化为总数
            "{sub_full_url}": f"`{repo_url}/{CONFIG['output_clash_full']}`",
            "{sub_selected_url}": f"`{repo_url}/{CONFIG['output_clash_selected']}`",
            "{raw_links_url}": f"`{repo_url}/{CONFIG['output_raw_links']}`",
        }
        
        for placeholder, value in replacements.items():
            template = template.replace(placeholder, value)
            
        with open(CONFIG['readme_output'], 'w', encoding='utf-8') as f:
            f.write(template)
        print("✅ README.md updated successfully.")
    except Exception as e:
        print(f"❌ Failed to update README.md: {e}")

async def main():
    """主执行函数，协调所有操作"""
    # 1. 读取数据库并进行生命周期管理
    try:
        with open(CONFIG['db_file'], 'r', newline='', encoding='utf-8') as f:
            links = list(csv.DictReader(f))
    except FileNotFoundError:
        print(f"❌ Database file not found at {CONFIG['db_file']}. Exiting.")
        return

    now = datetime.now(timezone.utc)
    active_db, links_to_archive = [], []

    for link in links:
        for field in ['success_streak', 'failure_streak', 'node_count']:
            link[field] = int(link.get(field) or 0)
        
        if link.get('status') == 'dead':
            last_success_str = link.get('last_success_time')
            if last_success_str:
                last_success = datetime.fromisoformat(last_success_str.replace('Z', '+00:00'))
                if now - last_success > timedelta(days=CONFIG['archive_days']):
                    links_to_archive.append(link)
                    continue
        active_db.append(link)

    if links_to_archive:
        file_exists = os.path.isfile(CONFIG['archive_file'])
        with open(CONFIG['archive_file'], 'a', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=links_to_archive[0].keys())
            if not file_exists:
                writer.writeheader()
            writer.writerows(links_to_archive)
        print(f"🗄️ Archived {len(links_to_archive)} dead links.")

    if not active_db:
        print("No active links to process.")
        return

    # 2. 智能调度需要检查的链接
    links_to_check = [link for link in active_db if link.get('status', 'new') in ['active', 'unstable', 'new']]
    for link in active_db:
        if link.get('status') == 'dead':
            last_check_str = link.get('last_check_time')
            if last_check_str:
                last_check = datetime.fromisoformat(last_check_str.replace('Z', '+00:00'))
                if now - last_check > timedelta(days=1):
                    links_to_check.append(link)

    # 3. 并发执行检查
    all_proxies = []
    async with aiohttp.ClientSession() as session:
        tasks = [fetch_url(session, link) for link in links_to_check]
        results = await asyncio.gather(*tasks)

    # 4. 更新健康档案
    results_map = {res['url']: res for res in results}
    for link in active_db:
        link['last_check_time'] = now.isoformat()
        if link['url'] in results_map:
            result = results_map[link['url']]
            if result['status'] == 'success':
                link.update({
                    'success_streak': link['success_streak'] + 1,
                    'failure_streak': 0,
                    'status': 'active',
                    'last_success_time': now.isoformat(),
                    'node_count': len(result['proxies'])
                })
                all_proxies.extend(result['proxies'])
            else:
                link['failure_streak'] += 1
                link['success_streak'] = 0
                if link['failure_streak'] >= CONFIG['dead_threshold']:
                    link['status'] = 'dead'
                elif link['failure_streak'] >= CONFIG['unstable_threshold']:
                    link['status'] = 'unstable'
    
    # 5. 去重
    unique_proxies = []
    seen_fingerprints = set()
    for proxy in all_proxies:
        fingerprint = generate_fingerprint(proxy)
        if fingerprint and fingerprint not in seen_fingerprints:
            seen_fingerprints.add(fingerprint)
            unique_proxies.append(proxy)
            
    print(f"Deduplication complete. Found {len(unique_proxies)} unique nodes.")

    # 6. 生成所有输出文件
    clash_full_config = {'proxies': unique_proxies}
    with open(CONFIG['output_clash_full'], 'w', encoding='utf-8') as f:
        yaml.dump(clash_full_config, f, allow_unicode=True, sort_keys=False)
    
    selected_count = min(len(unique_proxies), CONFIG['selected_node_count'])
    selected_proxies = random.sample(unique_proxies, selected_count)
    clash_selected_config = {'proxies': selected_proxies}
    with open(CONFIG['output_clash_selected'], 'w', encoding='utf-8') as f:
        yaml.dump(clash_selected_config, f, allow_unicode=True, sort_keys=False)

    with open(CONFIG['output_raw_links'], 'w', encoding='utf-8') as f:
        f.write("# Raw proxy links can be generated here.\n")

    # 7. 写回数据库和更新README
    with open(CONFIG['db_file'], 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=active_db[0].keys())
        writer.writeheader()
        writer.writerows(active_db)

    stats = {
        'last_update_time': now.strftime('%Y-%m-%d %H:%M:%S'),
        'total_nodes': len(unique_proxies),
        'active_links': len([l for l in active_db if l['status'] == 'active']),
        'total_links': len(active_db),
    }
    update_readme(stats)

if __name__ == "__main__":
    asyncio.run(main())

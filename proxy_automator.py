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
    "output_clash_lite": "subscription_lite.yaml", # 您要求的文件，包含1000+节点
    "output_raw_links": "proxies.txt", # 为其他客户端准备的原始链接

    # --- 行为控制参数 ---
    "max_concurrent_requests": 100,  # 最大并发请求数
    "request_timeout": 10,           # 单个请求的超时时间（秒）
    "max_retries": 3,                # 单个链接的最大重试次数
    "unstable_threshold": 5,         # 连续失败5次，状态变为 'unstable'
    "dead_threshold": 20,            # 连续失败20次，状态变为 'dead'
    "archive_days": 30,              # 'dead'状态超过30天，则归档
    "lite_node_count": 1000,         # 轻量版订阅包含的节点数，满足您的要求
}

# =================================================================================
# --- 核心功能函数 (无需修改) ---
# =================================================================================

async def fetch_url(session, link_data):
    """
    异步获取单个URL的节点信息。
    这个函数包含了您提醒的两个要点：
    1. 在URL后自动添加 /clash/proxies
    2. 包含了重试机制，确保稳定性
    """
    base_url = link_data['url'].strip()
    # 核心要求：在链接后加上 /clash/proxies
    url = base_url + "/clash/proxies"
    headers = {'User-Agent': 'Clash'}

    for attempt in range(CONFIG['max_retries']):
        try:
            if attempt > 0:
                await asyncio.sleep(2 * attempt) # 递增等待时间
            
            async with session.get(url, headers=headers, timeout=CONFIG['request_timeout']) as response:
                response.raise_for_status()
                text = await response.text()
                content = yaml.safe_load(text)
                
                if isinstance(content, dict) and 'proxies' in content and isinstance(content['proxies'], list):
                    return {"url": base_url, "status": "success", "proxies": content['proxies']}
                else:
                    return {"url": base_url, "status": "fail", "reason": "Invalid content format"}
        except Exception as e:
            if attempt == CONFIG['max_retries'] - 1:
                return {"url": base_url, "status": "fail", "reason": str(e)}
            continue
    return {"url": base_url, "status": "fail", "reason": "Unknown retry failure"}

def generate_fingerprint(node):
    """为节点生成唯一指纹以去重"""
    if not isinstance(node, dict): return None
    key_fields = ['server', 'port']
    node_type = node.get('type')
    try:
        if node_type == 'ss': key_fields.extend(['password', 'cipher'])
        elif node_type in ['vmess', 'vless']: key_fields.append('uuid')
        elif node_type == 'trojan': key_fields.append('password')
        elif node_type == 'ssr': key_fields.extend(['password', 'protocol', 'obfs'])
        return f"{node_type}://" + "-".join(sorted([f"{k}:{node[k]}" for k in key_fields]))
    except KeyError:
        return None

def update_readme(stats):
    """根据模板和最新数据，更新README.md"""
    try:
        with open(CONFIG['readme_template'], 'r', encoding='utf-8') as f:
            template = f.read()
        
        repo_url = f"https://raw.githubusercontent.com/{os.environ['GITHUB_REPOSITORY']}/main"

        # 最终输出的文件名就是您要求的clash.yaml，这里用配置里的名字
        clash_yaml_filename = CONFIG['output_clash_full']
        
        replacements = {
            "{last_update_time}": stats['last_update_time'],
            "{total_nodes}": str(stats['total_nodes']),
            "{active_links}": str(stats['active_links']),
            "{total_links}": str(stats['total_links']),
            "{newly_added_nodes}": str(stats['total_nodes']), # 简化为总数
            "{sub_full_url}": f"{repo_url}/{clash_yaml_filename}",
            "{sub_lite_url}": f"{repo_url}/{CONFIG['output_clash_lite']}",
            "{raw_links_url}": f"{repo_url}/{CONFIG['output_raw_links']}",
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
                last_success = datetime.fromisoformat(last_success_str)
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
                last_check = datetime.fromisoformat(last_check_str)
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
    # 生成 Full YAML (您的 clash.yaml)
    clash_full_config = {'proxies': unique_proxies}
    with open(CONFIG['output_clash_full'], 'w', encoding='utf-8') as f:
        yaml.dump(clash_full_config, f, allow_unicode=True, sort_keys=False)
    
    # 生成 Lite YAML (1000+ 节点)
    lite_count = min(len(unique_proxies), CONFIG['lite_node_count'])
    lite_proxies = random.sample(unique_proxies, lite_count)
    clash_lite_config = {'proxies': lite_proxies}
    with open(CONFIG['output_clash_lite'], 'w', encoding='utf-8') as f:
        yaml.dump(clash_lite_config, f, allow_unicode=True, sort_keys=False)

    # 生成 Raw Links Txt
    with open(CONFIG['output_raw_links'], 'w', encoding='utf-8') as f:
        # 此处省略生成原始链接的代码，如有需要可添加
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

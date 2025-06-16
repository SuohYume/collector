import os
import csv
import yaml
import random
import asyncio
import aiohttp
from datetime import datetime, timedelta, timezone

# --- 配置区 ---
CONFIG = {
    "db_file": "link_database.csv",
    "archive_file": "archive.csv",
    "readme_template": "README_TEMPLATE.md",
    "readme_output": "README.md",
    "output_clash_full": "subscription_full.yaml",
    "output_clash_selected": "subscription_selected_10k.yaml", 
    "output_raw_links": "proxies.txt",
    "max_concurrent_requests": 100,
    "request_timeout": 15,
    "max_retries": 2,
    "unstable_threshold": 5,
    "dead_threshold": 20,
    "archive_days": 30,
    "selected_node_count": 10000,
}

# --- 核心功能函数 ---
async def fetch_url(session, link_data):
    """专注地获取单个URL的节点信息，并忽略SSL错误。"""
    url = link_data['url'].strip()
    headers = {'User-Agent': 'Clash'}
    print(f"🔎 [CHECKING] {url}")
    try:
        async with session.get(url, headers=headers, timeout=CONFIG['request_timeout'], ssl=False) as response:
            if response.status != 200:
                raise Exception(f"Failed with status {response.status}")
            text = await response.text()
            content = yaml.safe_load(text)
            if isinstance(content, dict) and 'proxies' in content and isinstance(content['proxies'], list):
                print(f"✅ [SUCCESS] Found {len(content['proxies'])} nodes from {url}")
                return {"url": url, "status": "success", "proxies": content['proxies']}
            else:
                raise Exception("Invalid content format")
    except Exception as e:
        reason = f"Error: {str(e)}"
        print(f"❌ [FAIL] {url} - {reason}")
        return {"url": url, "status": "fail", "reason": reason}

def generate_fingerprint(proxy):
    """为节点生成唯一指纹以去重"""
    if not isinstance(proxy, dict): return None
    key_fields = ['server', 'port']
    node_type = proxy.get('type')
    try:
        if node_type == 'ss': key_fields.extend(['password', 'cipher'])
        elif node_type in ['vmess', 'vless']: key_fields.append('uuid')
        elif node_type == 'trojan': key_fields.append('password')
        elif node_type == 'ssr': key_fields.extend(['password', 'protocol', 'obfs'])
        return f"{node_type}://" + "-".join(sorted([f"{k}:{proxy.get(k, '')}" for k in key_fields]))
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
            "{newly_added_nodes}": str(stats['total_nodes']),
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
    try:
        with open(CONFIG['db_file'], 'r', newline='', encoding='utf-8') as f:
            links = list(csv.DictReader(f))
    except FileNotFoundError:
        print(f"❌ Database file not found at {CONFIG['db_file']}. Exiting.")
        return

    now = datetime.now(timezone.utc)
    active_db = [link for link in links if not (link.get('status') == 'dead' and link.get('last_success_time') and (now - datetime.fromisoformat(link['last_success_time'].replace('Z', '+00:00')) > timedelta(days=CONFIG['archive_days'])))]

    if not active_db:
        print("No active links to process.")
        return

    links_to_check = [link for link in active_db if not link.get('status') or link.get('status') in ['active', 'unstable', 'new'] or (link.get('status') == 'dead' and (not link.get('last_check_time') or (now - datetime.fromisoformat(link['last_check_time'].replace('Z', '+00:00')) > timedelta(days=1))))]
    print(f"Found {len(links_to_check)} links to check.")

    all_proxies_with_source = []
    if links_to_check:
        async with aiohttp.ClientSession() as session:
            tasks = [fetch_url(session, link) for link in links_to_check]
            results = await asyncio.gather(*tasks)
        
        results_map = {res['url']: res for res in results}
        for link in active_db:
            for field in ['success_streak', 'failure_streak', 'node_count']: link[field] = int(link.get(field) or 0)
            
            if link['url'] in results_map:
                link['last_check_time'] = now.isoformat()
                result = results_map[link['url']]
                if result['status'] == 'success':
                    link.update({'success_streak': link['success_streak'] + 1, 'failure_streak': 0, 'status': 'active', 'last_success_time': now.isoformat(), 'node_count': len(result['proxies'])})
                    for proxy in result['proxies']:
                        all_proxies_with_source.append({'proxy': proxy, 'streak': link['success_streak']})
                else:
                    link.update({'failure_streak': link['failure_streak'] + 1, 'success_streak': 0})
                    if link['failure_streak'] >= CONFIG['dead_threshold']: link['status'] = 'dead'
                    elif link['failure_streak'] >= CONFIG['unstable_threshold']: link['status'] = 'unstable'

    # 去重并保留最高健康度的来源
    unique_proxies_with_health = {}
    for item in all_proxies_with_source:
        fingerprint = generate_fingerprint(item['proxy'])
        if fingerprint:
            if fingerprint not in unique_proxies_with_health or item['streak'] > unique_proxies_with_health[fingerprint]['streak']:
                unique_proxies_with_health[fingerprint] = {'proxy': item['proxy'], 'streak': item['streak']}
    
    sorted_proxies = sorted(unique_proxies_with_health.values(), key=lambda x: x['streak'], reverse=True)
    unique_proxies = [item['proxy'] for item in sorted_proxies]
    
    print(f"Deduplication complete. Found {len(unique_proxies)} unique nodes.")

    # 生成文件
    clash_full_config = {'proxies': unique_proxies}
    with open(CONFIG['output_clash_full'], 'w', encoding='utf-8') as f:
        yaml.dump(clash_full_config, f, allow_unicode=True, sort_keys=False)
    
    selected_count = min(len(unique_proxies), CONFIG['selected_node_count'])
    selected_proxies = unique_proxies[:selected_count]
    clash_selected_config = {'proxies': selected_proxies}
    with open(CONFIG['output_clash_selected'], 'w', encoding='utf-8') as f:
        yaml.dump(clash_selected_config, f, allow_unicode=True, sort_keys=False)

    with open(CONFIG['output_raw_links'], 'w', encoding='utf-8') as f:
        f.write("# Raw proxy links can be generated here.\n")

    # 写回数据库
    with open(CONFIG['db_file'], 'w', newline='', encoding='utf-8') as f:
        if active_db:
            writer = csv.DictWriter(f, fieldnames=active_db[0].keys())
            writer.writeheader()
            writer.writerows(active_db)

    # 更新README
    stats = {'last_update_time': now.strftime('%Y-%m-%d %H:%M:%S'), 'total_nodes': len(unique_proxies), 'active_links': len([l for l in active_db if l['status'] == 'active']), 'total_links': len(active_db)}
    update_readme(stats)

if __name__ == "__main__":
    asyncio.run(main())

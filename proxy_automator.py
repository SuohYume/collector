import os
import csv
import yaml
import random
import asyncio
import aiohttp
from datetime import datetime, timedelta, timezone

# --- 配置区 (保持不变) ---
CONFIG = {
    "db_file": "link_database.csv", "archive_file": "archive.csv",
    "readme_template": "README_TEMPLATE.md", "readme_output": "README.md",
    "output_clash_full": "subscription_full.yaml", "output_clash_lite": "subscription_lite.yaml",
    "output_raw_links": "proxies.txt", "max_concurrent_requests": 100,
    "request_timeout": 10, "max_retries": 3, "unstable_threshold": 5,
    "dead_threshold": 20, "archive_days": 30, "lite_node_count": 1000,
}

# =================================================================================
# --- 核心功能函数 (这里是修改的重点) ---
# =================================================================================

async def fetch_url(session, link_data):
    """异步获取单个URL的节点信息，并加入详细的调试日志"""
    base_url = link_data['url'].strip()
    url = base_url + "/clash/proxies"
    headers = {'User-Agent': 'Clash/1.11.0'} # 模拟Clash客户端的User-Agent

    print(f"\n[DEBUG] 开始处理链接: {base_url}")

    for attempt in range(CONFIG['max_retries']):
        try:
            if attempt > 0:
                print(f"[DEBUG] 第 {attempt + 1}/{CONFIG['max_retries']} 次尝试...")
                await asyncio.sleep(2 * attempt)
            
            async with session.get(url, headers=headers, timeout=CONFIG['request_timeout']) as response:
                print(f"[DEBUG] HTTP状态码: {response.status} @ {base_url}")
                response.raise_for_status()
                text = await response.text()
                
                try:
                    content = yaml.safe_load(text)
                    if isinstance(content, dict) and 'proxies' in content and isinstance(content['proxies'], list):
                        print(f"[SUCCESS] ✅ 成功从 {base_url} 获取到 {len(content['proxies'])} 个节点。")
                        return {"url": base_url, "status": "success", "proxies": content['proxies']}
                    else:
                        # 成功访问，但内容格式不符
                        print(f"[FAIL] ❌ 访问成功，但内容格式不正确 @ {base_url}")
                        print(f"[DEBUG] 返回内容预览 (前200字符): {text[:200]}")
                        return {"url": base_url, "status": "fail", "reason": "Invalid content format"}
                except yaml.YAMLError as e:
                    # YAML解析失败
                    print(f"[FAIL] ❌ YAML解析失败 @ {base_url} - {e}")
                    print(f"[DEBUG] 返回内容预览 (前200字符): {text[:200]}")
                    return {"url": base_url, "status": "fail", "reason": f"YAML parse error: {e}"}

        except Exception as e:
            # 请求失败 (超时、连接错误等)
            print(f"[FAIL] ❌ 第 {attempt + 1} 次尝试失败 @ {base_url} - 错误: {e}")
            if attempt == CONFIG['max_retries'] - 1:
                return {"url": base_url, "status": "fail", "reason": str(e)}
            continue
            
    return {"url": base_url, "status": "fail", "reason": "Unknown retry failure"}

# --- 其他函数 (保持不变) ---
def generate_fingerprint(node):
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
    try:
        with open(CONFIG['readme_template'], 'r', encoding='utf-8') as f:
            template = f.read()
        repo_url = f"https://raw.githubusercontent.com/{os.environ['GITHUB_REPOSITORY']}/main"
        clash_yaml_filename = CONFIG['output_clash_full']
        replacements = {
            "{last_update_time}": stats['last_update_time'], "{total_nodes}": str(stats['total_nodes']),
            "{active_links}": str(stats['active_links']), "{total_links}": str(stats['total_links']),
            "{newly_added_nodes}": str(stats['total_nodes']), "{sub_full_url}": f"{repo_url}/{clash_yaml_filename}",
            "{sub_lite_url}": f"{repo_url}/{CONFIG['output_clash_lite']}", "{raw_links_url}": f"{repo_url}/{CONFIG['output_raw_links']}",
        }
        for placeholder, value in replacements.items():
            template = template.replace(placeholder, value)
        with open(CONFIG['readme_output'], 'w', encoding='utf-8') as f:
            f.write(template)
        print("✅ README.md updated successfully.")
    except Exception as e:
        print(f"❌ Failed to update README.md: {e}")

async def main():
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
                    links_to_archive.append(link); continue
        active_db.append(link)

    if links_to_archive:
        file_exists = os.path.isfile(CONFIG['archive_file'])
        with open(CONFIG['archive_file'], 'a', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=links_to_archive[0].keys())
            if not file_exists: writer.writeheader()
            writer.writerows(links_to_archive)
        print(f"🗄️ Archived {len(links_to_archive)} dead links.")

    if not active_db: print("No active links to process."); return

    links_to_check = [link for link in active_db if link.get('status', 'new') in ['active', 'unstable', 'new']]
    for link in active_db:
        if link.get('status') == 'dead':
            last_check_str = link.get('last_check_time')
            if last_check_str and now - datetime.fromisoformat(last_check_str) > timedelta(days=1):
                links_to_check.append(link)

    print(f"--- 准备检查 {len(links_to_check)} 个链接 ---")
    
    all_proxies = []
    async with aiohttp.ClientSession() as session:
        tasks = [fetch_url(session, link) for link in links_to_check]
        results = await asyncio.gather(*tasks)

    results_map = {res['url']: res for res in results}
    successful_fetches = 0
    for link in active_db:
        link['last_check_time'] = now.isoformat()
        if link['url'] in results_map:
            result = results_map[link['url']]
            if result['status'] == 'success':
                successful_fetches += 1
                link.update({'success_streak': link['success_streak'] + 1, 'failure_streak': 0, 'status': 'active',
                             'last_success_time': now.isoformat(), 'node_count': len(result['proxies'])})
                all_proxies.extend(result['proxies'])
            else:
                link['failure_streak'] += 1; link['success_streak'] = 0
                if link['failure_streak'] >= CONFIG['dead_threshold']: link['status'] = 'dead'
                elif link['failure_streak'] >= CONFIG['unstable_threshold']: link['status'] = 'unstable'

    print(f"\n--- 检查完成 ---")
    print(f"总共成功获取了 {successful_fetches} 个链接的数据。")
    print(f"总共收集到 {len(all_proxies)} 个原始节点。")
    
    unique_proxies = []
    seen_fingerprints = set()
    for proxy in all_proxies:
        fingerprint = generate_fingerprint(proxy)
        if fingerprint and fingerprint not in seen_fingerprints:
            seen_fingerprints.add(fingerprint)
            unique_proxies.append(proxy)
            
    print(f"去重后剩余 {len(unique_proxies)} 个唯一节点。")

    clash_full_config = {'proxies': unique_proxies}
    with open(CONFIG['output_clash_full'], 'w', encoding='utf-8') as f:
        yaml.dump(clash_full_config, f, allow_unicode=True, sort_keys=False)
    
    lite_count = min(len(unique_proxies), CONFIG['lite_node_count'])
    lite_proxies = random.sample(unique_proxies, lite_count)
    clash_lite_config = {'proxies': lite_proxies}
    with open(CONFIG['output_clash_lite'], 'w', encoding='utf-8') as f:
        yaml.dump(clash_lite_config, f, allow_unicode=True, sort_keys=False)
    
    with open(CONFIG['output_raw_links'], 'w', encoding='utf-8') as f: f.write("# Raw proxy links can be generated here.\n")

    with open(CONFIG['db_file'], 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=active_db[0].keys())
        writer.writeheader(); writer.writerows(active_db)

    stats = {'last_update_time': now.strftime('%Y-%m-%d %H:%M:%S'), 'total_nodes': len(unique_proxies),
             'active_links': len([l for l in active_db if l['status'] == 'active']), 'total_links': len(active_db)}
    update_readme(stats)

if __name__ == "__main__":
    asyncio.run(main())

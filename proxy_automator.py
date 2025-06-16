import os
import csv
import yaml
import random
import asyncio
import aiohttp
import base64
from urllib.parse import quote
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


# #################################################################
# ### 新增的辅助函数：将节点字典转换为分享链接 ###
# #################################################################
def convert_to_raw_link(node):
    """
    将单个节点字典转换为通用的分享链接格式。
    """
    if not isinstance(node, dict): return None

    node_type = node.get('type')
    
    try:
        if node_type == 'ss':
            # SS Format: ss://method:password@server:port#name
            # Base64 encoded part: method:password
            encoded_part = base64.b64encode(f"{node['cipher']}:{node['password']}".encode()).decode()
            return f"ss://{encoded_part}@{node['server']}:{node['port']}#{quote(node['name'])}"

        elif node_type == 'vmess':
            # VMess Format: vmess://base64_encode(json)
            vmess_json = {
                "v": "2",
                "ps": node.get('name', ''),
                "add": node.get('server', ''),
                "port": node.get('port', ''),
                "id": node.get('uuid', ''),
                "aid": node.get('alterId', 0),
                "net": node.get('network', 'tcp'),
                "type": node.get('headerType', 'none'),
                "host": node.get('ws-opts', {}).get('headers', {}).get('Host', ''),
                "path": node.get('ws-opts', {}).get('path', '/'),
                "tls": "tls" if node.get('tls') else ""
            }
            return f"vmess://{base64.b64encode(str(vmess_json).encode()).decode()}"

        elif node_type == 'trojan':
            # Trojan Format: trojan://password@server:port#name
            return f"trojan://{quote(node['password'])}@{node['server']}:{node['port']}#{quote(node['name'])}"
        
        elif node_type == 'vless' or node_type == 'ssr':
            # VLESS 和 SSR 的链接格式更复杂，此处作为预留，暂不实现
            # 如果您的节点包含这两种类型，它们将被跳过
            return None
        
        else:
            return None
            
    except (KeyError, TypeError) as e:
        # print(f"Skipping node due to missing key for raw link conversion: {e}")
        return None

# --- 其他核心功能函数 (保持不变) ---
async def fetch_url(session, link_data):
    base_url = link_data['url'].strip(); url = base_url + "/clash/proxies"
    headers = {'User-Agent': 'Clash/1.11.0'}
    for attempt in range(CONFIG['max_retries']):
        try:
            if attempt > 0: await asyncio.sleep(2 * attempt)
            async with session.get(url, headers=headers, timeout=CONFIG['request_timeout']) as response:
                response.raise_for_status(); text = await response.text()
                try:
                    content = yaml.safe_load(text)
                    if isinstance(content, dict) and 'proxies' in content and isinstance(content['proxies'], list):
                        return {"url": base_url, "status": "success", "proxies": content['proxies']}
                    return {"url": base_url, "status": "fail", "reason": "Invalid content format"}
                except yaml.YAMLError: return {"url": base_url, "status": "fail", "reason": f"YAML parse error"}
        except Exception as e:
            if attempt == CONFIG['max_retries'] - 1: return {"url": base_url, "status": "fail", "reason": str(e)}
            continue
    return {"url": base_url, "status": "fail", "reason": "Unknown retry failure"}

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
    except KeyError: return None

def update_readme(stats):
    try:
        with open(CONFIG['readme_template'], 'r', encoding='utf-8') as f: template = f.read()
        repo_url = f"https://raw.githubusercontent.com/{os.environ['GITHUB_REPOSITORY']}/main"
        replacements = {
            "{last_update_time}": stats['last_update_time'], "{total_nodes}": str(stats['total_nodes']),
            "{active_links}": str(stats['active_links']), "{total_links}": str(stats['total_links']),
            "{newly_added_nodes}": str(stats['total_nodes']), "{sub_full_url}": f"{repo_url}/{CONFIG['output_clash_full']}",
            "{sub_lite_url}": f"{repo_url}/{CONFIG['output_clash_lite']}", "{raw_links_url}": f"{repo_url}/{CONFIG['output_raw_links']}",
        }
        for placeholder, value in replacements.items(): template = template.replace(placeholder, value)
        with open(CONFIG['readme_output'], 'w', encoding='utf-8') as f: f.write(template)
    except Exception: pass

async def main():
    try:
        with open(CONFIG['db_file'], 'r', newline='', encoding='utf-8') as f: links = list(csv.DictReader(f))
    except FileNotFoundError: return

    now = datetime.now(timezone.utc)
    active_db, links_to_archive = [], []
    for link in links:
        for field in ['success_streak', 'failure_streak', 'node_count']: link[field] = int(link.get(field) or 0)
        if link.get('status') == 'dead':
            last_success_str = link.get('last_success_time')
            if last_success_str and now - datetime.fromisoformat(last_success_str) > timedelta(days=CONFIG['archive_days']):
                links_to_archive.append(link); continue
        active_db.append(link)

    if links_to_archive:
        file_exists = os.path.isfile(CONFIG['archive_file'])
        with open(CONFIG['archive_file'], 'a', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=links_to_archive[0].keys())
            if not file_exists: writer.writeheader()
            writer.writerows(links_to_archive)

    if not active_db: return

    links_to_check = [link for link in active_db if (link.get('status') or 'new') in ['active', 'unstable', 'new']]
    for link in active_db:
        if link.get('status') == 'dead':
            last_check_str = link.get('last_check_time')
            if last_check_str and now - datetime.fromisoformat(last_check_str) > timedelta(days=1):
                if link not in links_to_check: links_to_check.append(link)
    
    if not links_to_check: return
    
    all_proxies = []
    async with aiohttp.ClientSession() as session:
        tasks = [fetch_url(session, link) for link in links_to_check]
        results = await asyncio.gather(*tasks)

    results_map = {res['url']: res for res in results}
    for link in active_db:
        link['last_check_time'] = now.isoformat()
        if link['url'] in results_map:
            result = results_map[link['url']]
            if result['status'] == 'success':
                link.update({'success_streak': link['success_streak'] + 1, 'failure_streak': 0, 'status': 'active',
                             'last_success_time': now.isoformat(), 'node_count': len(result['proxies'])})
                all_proxies.extend(result['proxies'])
            else:
                link['failure_streak'] += 1; link['success_streak'] = 0
                if link['failure_streak'] >= CONFIG['dead_threshold']: link['status'] = 'dead'
                elif link['failure_streak'] >= CONFIG['unstable_threshold']: link['status'] = 'unstable'
    
    unique_proxies = []
    seen_fingerprints = set()
    for proxy in all_proxies:
        fingerprint = generate_fingerprint(proxy)
        if fingerprint and fingerprint not in seen_fingerprints: seen_fingerprints.add(fingerprint); unique_proxies.append(proxy)

    # 生成 Full YAML
    clash_full_config = {'proxies': unique_proxies}
    with open(CONFIG['output_clash_full'], 'w', encoding='utf-8') as f:
        yaml.dump(clash_full_config, f, allow_unicode=True, sort_keys=False)
    
    # 生成 Lite YAML
    lite_count = min(len(unique_proxies), CONFIG['lite_node_count'])
    lite_proxies = random.sample(unique_proxies, lite_count)
    clash_lite_config = {'proxies': lite_proxies}
    with open(CONFIG['output_clash_lite'], 'w', encoding='utf-8') as f:
        yaml.dump(clash_lite_config, f, allow_unicode=True, sort_keys=False)
    
    # ###############################################################
    # ### 这里是修改点：生成并写入 proxies.txt 的内容 ###
    # ###############################################################
    print(f"正在生成 {CONFIG['output_raw_links']} ...")
    raw_links = []
    for node in unique_proxies:
        link_str = convert_to_raw_link(node)
        if link_str:
            raw_links.append(link_str)
    
    with open(CONFIG['output_raw_links'], 'w', encoding='utf-8') as f:
        f.write("\n".join(raw_links))
    print(f"✅ {CONFIG['output_raw_links']} 文件已成功生成，包含 {len(raw_links)} 个链接。")
    # ###############################################################

    # 写回数据库
    with open(CONFIG['db_file'], 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=active_db[0].keys()); writer.writeheader(); writer.writerows(active_db)

    # 更新README
    stats = {'last_update_time': now.strftime('%Y-%m-%d %H:%M:%S'), 'total_nodes': len(unique_proxies),
             'active_links': len([l for l in active_db if l['status'] == 'active']), 'total_links': len(active_db)}
    update_readme(stats)

if __name__ == "__main__":
    asyncio.run(main())

import os
import csv
import yaml
import random
import asyncio
import aiohttp
import base64
import re
import json
from urllib.parse import urlparse, unquote, parse_qs
from hashlib import md5
from datetime import datetime, timezone, timedelta

# =================================================================================
# --- 配置区 ---
# =================================================================================
CONFIG = {
    "db_file": "link_database.csv", "archive_file": "archive.csv",
    "report_file": "quality_report.csv", "cache_dir": "cached_subs",
    "output_task_package": "task_package.yaml", "readme_template": "README_TEMPLATE.md",
    "readme_output": "README.md", "node_quota": 20000, "max_concurrent_requests": 50,
    "request_timeout": 15, "max_low_rate_runs": 10, "archive_days": 60,
}

# =================================================================================
# --- 核心功能函数 ---
# =================================================================================

def get_url_hash(url):
    """为URL生成一个安全的文件名"""
    return md5(url.encode()).hexdigest()

def generate_fingerprint(node):
    """为节点生成唯一指纹以去重"""
    if not isinstance(node, dict): return None
    key_fields = ['server', 'port']
    node_type = node.get('type')
    try:
        if node_type == 'ss': key_fields.extend(['password', 'cipher'])
        elif node_type in ['vmess', 'vless']: key_fields.append('uuid')
        elif node_type == 'trojan': key_fields.append('password')
        return f"{node_type}://" + "-".join(sorted([f"{k}:{node.get(k, '')}" for k in key_fields]))
    except (KeyError, TypeError): return None

def parse_link_to_dict(link):
    """将单个协议链接字符串转换为Clash字典"""
    try:
        if link.startswith('vmess://'):
            b64_str = link.replace('vmess://', '')
            decoded_json = json.loads(base64.b64decode(b64_str).decode())
            return {
                "name": decoded_json.get('ps', decoded_json.get('add', '')), "type": "vmess",
                "server": decoded_json.get('add'), "port": int(decoded_json.get('port')),
                "uuid": decoded_json.get('id'), "alterId": int(decoded_json.get('aid')),
                "cipher": "auto", "network": decoded_json.get('net'),
                "ws-opts": {"path": decoded_json.get('path'), "headers": {"Host": decoded_json.get('host')}} if decoded_json.get('net') == 'ws' else None,
                "tls": decoded_json.get('tls') == 'tls'
            }
        elif link.startswith('vless://') or link.startswith('trojan://'):
            parsed_url = urlparse(link)
            params = parse_qs(parsed_url.query)
            node = {
                "name": unquote(parsed_url.fragment) if parsed_url.fragment else parsed_url.hostname,
                "type": parsed_url.scheme,
                "server": parsed_url.hostname,
                "port": parsed_url.port,
                "network": params.get('type', ['tcp'])[0],
                "tls": params.get('security', ['none'])[0] == 'tls'
            }
            if node['type'] == 'vless':
                node['uuid'] = parsed_url.username
            else: # trojan
                node['password'] = parsed_url.username
            
            if node['network'] == 'ws':
                node['ws-opts'] = {"path": params.get('path', ['/'])[0], "headers": {"Host": params.get('host', [parsed_url.hostname])[0]}}
            return node
        elif link.startswith('ss://'):
            # First part is Base64, second part is the name after #
            main_part, _, name = link.replace('ss://', '').partition('#')
            name = unquote(name) if name else None
            
            try:
                decoded_part = base64.b64decode(main_part).decode()
                method, password_server = decoded_part.split(':', 1)
                password, server_port = password_server.rsplit('@', 1)
                server, port = server_port.split(':', 1)
            except Exception: # Fallback for non-base64 format like method:pass@server:port
                creds_server, _, name = link.replace('ss://', '').partition('#')
                creds, server_port = creds_server.rsplit('@', 1)
                method, password = creds.split(':', 1)
                server, port = server_port.split(':', 1)
                name = unquote(name) if name else server
            
            return {"name": name, "type": "ss", "server": server, "port": int(port), "cipher": method, "password": password}
    except Exception as e:
        # print(f"解析链接失败: {link}, 原因: {e}")
        return None

def parse_nodes_from_plaintext(text):
    """使用正则表达式从纯文本中提取所有代理链接"""
    proxy_pattern = r"(vmess|vless|ss|trojan)://[a-zA-Z0-9+/=_{},'\"\-?&%.#@:\[\]]+"
    found_links = re.findall(proxy_pattern, text, re.IGNORECASE)
    
    nodes = []
    for link in found_links:
        node_dict = parse_link_to_dict(link)
        if node_dict:
            nodes.append(node_dict)
    return nodes

def parse_nodes_from_content(text):
    """智能解析函数，尝试多种格式从文本中提取节点列表。"""
    if not text: return []
    try: # 1. 尝试YAML
        content = yaml.safe_load(text)
        if isinstance(content, dict) and 'proxies' in content and isinstance(content['proxies'], list):
            return content['proxies']
    except Exception: pass
    try: # 2. 尝试Base64
        decoded_text = base64.b64decode(''.join(text.split())).decode('utf-8')
        nodes = parse_nodes_from_plaintext(decoded_text)
        if nodes: return nodes
    except Exception: pass
    # 3. 尝试纯文本
    return parse_nodes_from_plaintext(text)

async def fetch_content(session, url):
    """获取URL原始内容"""
    headers = {'User-Agent': 'Clash'}
    try:
        async with session.get(url, headers=headers, timeout=CONFIG['request_timeout']) as response:
            response.raise_for_status()
            return await response.text()
    except Exception:
        # 如果直链失败，尝试添加 /clash/proxies
        if not url.endswith('/clash/proxies'):
            try:
                api_url = url.rstrip('/') + '/clash/proxies'
                async with session.get(api_url, headers=headers, timeout=CONFIG['request_timeout']) as response:
                    response.raise_for_status()
                    return await response.text()
            except Exception as e:
                print(f"❌ 获取内容失败 (两种方式均失败): {url}, 原因: {e}")
                return None
    return None

async def main():
    if not os.path.exists(CONFIG['cache_dir']): os.makedirs(CONFIG['cache_dir'])
    for f in os.listdir(CONFIG['cache_dir']): os.remove(os.path.join(CONFIG['cache_dir'], f))

    try:
        with open(CONFIG['db_file'], 'r', newline='', encoding='utf-8') as f:
            links_db = {row['url']: row for row in csv.DictReader(f)}
    except FileNotFoundError: links_db = {}
    
    try:
        with open(CONFIG['report_file'], 'r', newline='', encoding='utf-8') as f:
            naughty_list = {row['failed_url'] for row in csv.DictReader(f)}
    except FileNotFoundError: naughty_list = set()

    now = datetime.now(timezone.utc)
    for url, data in links_db.items():
        data['consecutive_low_rate_runs'] = int(data.get('consecutive_low_rate_runs', 0))
        cache_filename = f"{get_url_hash(url)}.yaml"
        repo = os.environ.get('GITHUB_REPOSITORY', 'user/repo')
        cached_file_url = f"https://raw.githubusercontent.com/{repo}/main/{CONFIG['cache_dir']}/{cache_filename}"
        if cached_file_url in naughty_list:
            data['consecutive_low_rate_runs'] += 1
            if data['consecutive_low_rate_runs'] >= CONFIG['max_low_rate_runs']: data['status'] = 'dead'
            else: data['status'] = 'unstable'
        else:
            data['consecutive_low_rate_runs'] = 0; data['status'] = 'active'
        data['last_report_time'] = now.isoformat()

    def get_priority(link_data_tuple):
        status = link_data_tuple[1].get('status', 'new'); runs = int(link_data_tuple[1].get('consecutive_low_rate_runs', 0))
        if status == 'active': return (0, -runs)
        if status == 'unstable': return (1, -runs)
        return (2, -runs)
    sorted_links = sorted(links_db.items(), key=get_priority)

    master_fingerprints = set()
    source_to_nodes_map = {}
    
    async with aiohttp.ClientSession() as session:
        for url, data in sorted_links:
            if data.get('status') == 'dead': continue
            if len(master_fingerprints) >= CONFIG['node_quota']: print(f"✅ 节点配额 {CONFIG['node_quota']} 已达到，停止收集。"); break
            content = await fetch_content(session, url)
            if not content: continue
            nodes = parse_nodes_from_content(content)
            if not nodes: print(f"⚠️ 从 {url} 未解析到节点。"); continue
            source_to_nodes_map[url] = []
            new_nodes_count = 0
            for node in nodes:
                fingerprint = generate_fingerprint(node)
                if fingerprint and fingerprint not in master_fingerprints:
                    master_fingerprints.add(fingerprint); source_to_nodes_map[url].append(node); new_nodes_count += 1
            print(f"处理: {url}, 发现 {new_nodes_count} 个新节点。当前总数: {len(master_fingerprints)}")

    sub_list_for_testing = []
    repo = os.environ.get('GITHUB_REPOSITORY', 'user/repo')
    for url, nodes in source_to_nodes_map.items():
        if not nodes: continue
        cache_filename = f"{get_url_hash(url)}.yaml"
        cache_filepath = os.path.join(CONFIG['cache_dir'], cache_filename)
        with open(cache_filepath, 'w', encoding='utf-8') as f:
            yaml.dump({'proxies': nodes}, f, allow_unicode=True, sort_keys=False)
        sub_list_for_testing.append(f"https://raw.githubusercontent.com/{repo}/main/{cache_filepath}")
        
    with open(CONFIG['output_task_list'], 'w', encoding='utf-8') as f: f.write("\n".join(sub_list_for_testing))
    print(f"\n✅ 最终任务链接集 {CONFIG['output_task_list']} 已生成，包含 {len(sub_list_for_testing)} 个独立的源镜像。")

    header = list(links_db.values())[0].keys() if links_db else []
    if header:
        with open(CONFIG['db_file'], 'w', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=header); writer.writeheader(); writer.writerows(links_db.values())

if __name__ == "__main__":
    asyncio.run(main())

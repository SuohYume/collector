import os
import csv
import yaml
import random
import asyncio
import aiohttp
import ssl
import base64
import re
import json
from urllib.parse import urlparse, unquote, parse_qs
from hashlib import md5
from datetime import datetime, timezone, timedelta

CONFIG = {
    "db_file": "link_database.csv", "archive_file": "archive.csv",
    "report_file": "quality_report.csv", "cache_dir": "cached_subs",
    "output_task_list": "sub_list_for_testing.txt", "output_full_package": "clash.yaml",
    "output_lite_package": "clash_lite.yaml", "node_quota": 20000,
    "lite_node_count": 1000, "max_concurrent_requests": 50, "request_timeout": 8,
    "max_low_rate_runs": 10, "archive_days": 60,
}

def get_url_hash(url): return md5(url.encode()).hexdigest()

def generate_fingerprint(node):
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
    """【强化版】将单个协议链接字符串转换为Clash字典"""
    link = link.strip()
    try:
        if link.startswith('vmess://'):
            b64_str = link.replace('vmess://', '')
            padding = len(b64_str) % 4
            if padding > 0: b64_str += "=" * (4 - padding)
            decoded_json = json.loads(base64.b64decode(b64_str).decode('utf-8'))
            node = {"name": decoded_json.get('ps', decoded_json.get('add', '')), "type": "vmess", "server": decoded_json.get('add'), "port": int(decoded_json.get('port')), "uuid": decoded_json.get('id'), "alterId": int(decoded_json.get('aid', 0)), "cipher": "auto", "network": decoded_json.get('net', 'tcp'), "tls": decoded_json.get('tls') == 'tls'}
            if node['network'] == 'ws': node['ws-opts'] = {"path": decoded_json.get('path', '/'), "headers": {"Host": decoded_json.get('host', '')}}
            return node
        elif link.startswith('vless://') or link.startswith('trojan://'):
            parsed_url = urlparse(link); params = parse_qs(parsed_url.query)
            node = {"name": unquote(parsed_url.fragment) if parsed_url.fragment else parsed_url.hostname, "type": parsed_url.scheme, "server": parsed_url.hostname, "port": parsed_url.port, "network": params.get('type', ['tcp'])[0], "tls": params.get('security', ['none'])[0] == 'tls'}
            if node['type'] == 'vless': node['uuid'] = parsed_url.username
            else: node['password'] = parsed_url.username
            if node['network'] == 'ws': node['ws-opts'] = {"path": params.get('path', ['/'])[0], "headers": {"Host": params.get('host', [parsed_url.hostname])[0]}}
            return node
        elif link.startswith('ss://'):
            main_part, _, name = link.replace('ss://', '').partition('#'); name = unquote(name) if name else None
            if '@' in main_part:
                creds, server_port = main_part.rsplit('@', 1)
            else: return None # Invalid SS format
            try:
                decoded_creds = base64.b64decode(creds).decode()
                method, password = decoded_creds.split(':', 1)
            except Exception: method, password = creds.split(':', 1)
            server, port = server_port.split(':', 1); name = name if name else server
            return {"name": name, "type": "ss", "server": server, "port": int(port), "cipher": method, "password": password}
    except Exception: return None

def parse_nodes_from_plaintext(text):
    """【强化版】使用正则表达式从纯文本中提取所有代理链接"""
    protocols = ['vmess', 'vless', 'ss', 'trojan']
    # 这个正则表达式会查找所有协议头，作为分割点
    pattern = f"({'|'.join(protocols)})://"
    
    nodes = []
    # 使用finditer来处理无分隔符的连续链接
    matches = list(re.finditer(pattern, text))
    for i, match in enumerate(matches):
        start_pos = match.start()
        # 链接的结束位置是下一个链接的开始，或者是文本末尾
        end_pos = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        link_str = text[start_pos:end_pos]
        
        node_dict = parse_link_to_dict(link_str)
        if node_dict:
            nodes.append(node_dict)
    return nodes

def parse_nodes_from_content(text):
    """【强化版】智能解析函数，应对所有已知情况"""
    if not text: return []
    nodes = []
    try: # 1. 尝试YAML
        content = yaml.safe_load(text)
        if isinstance(content, dict) and 'proxies' in content and isinstance(content['proxies'], list):
            for item in content['proxies']:
                if isinstance(item, dict): # 标准YAML字典
                    nodes.append(item)
                elif isinstance(item, str): # YAML中的JSON字符串
                    try: nodes.append(json.loads(item))
                    except json.JSONDecodeError: pass
            if nodes: return nodes
    except Exception: pass
    try: # 2. 尝试Base64
        decoded_text = base64.b64decode(''.join(text.split())).decode('utf-8')
        nodes = parse_nodes_from_plaintext(decoded_text)
        if nodes: return nodes
    except Exception: pass
    # 3. 尝试作为普通纯文本处理
    return parse_nodes_from_plaintext(text)

async def fetch_content(session, url):
    """获取URL原始内容，并忽略SSL证书错误"""
    headers = {'User-Agent': 'ClashNode/1.0'}
    ssl_context = ssl.create_default_context()
    ssl_context.check_hostname = False
    ssl_context.verify_mode = ssl.CERT_NONE

    async def get_url(target_url):
        return await session.get(target_url, headers=headers, timeout=CONFIG['request_timeout'], ssl=ssl_context)

    try:
        async with await get_url(url) as response:
            response.raise_for_status(); return await response.text()
    except Exception:
        if not url.endswith('/clash/proxies'):
            try:
                api_url = url.rstrip('/') + '/clash/proxies'
                async with await get_url(api_url) as response:
                    response.raise_for_status(); return await response.text()
            except Exception as e: print(f"❌ 获取内容失败 (两种方式均失败): {url}, 原因: {e}"); return None
    return None

async def main():
    # ... main函数的所有其他部分，包括文件读写、健康度更新、排序、输出等，都保持不变 ...
    # 此处只粘贴main函数以保持简洁，您可以直接复制上面的新版函数替换旧脚本中的对应函数
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
        data['consecutive_low_rate_runs'] = int(data.get('consecutive_low_rate_runs') or 0)
        repo = os.environ.get('GITHUB_REPOSITORY', 'user/repo'); cache_filename = f"{get_url_hash(url)}.yaml"
        cached_file_url = f"https://raw.githubusercontent.com/{repo}/main/{CONFIG['cache_dir']}/{cache_filename}"
        if cached_file_url in naughty_list:
            data['consecutive_low_rate_runs'] += 1
            if data['consecutive_low_rate_runs'] >= CONFIG['max_low_rate_runs']: data['status'] = 'dead'
            else: data['status'] = 'unstable'
        else: data['consecutive_low_rate_runs'] = 0; data['status'] = 'active'
        data['last_report_time'] = now.isoformat()
    def get_priority(link_data_tuple):
        status = link_data_tuple[1].get('status', 'new'); runs = int(link_data_tuple[1].get('consecutive_low_rate_runs', 0))
        if status == 'active': return (0, runs)
        if status == 'unstable': return (1, runs)
        return (2, runs)
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
    all_collected_nodes = []
    for url, nodes in source_to_nodes_map.items():
        if not nodes: continue
        all_collected_nodes.extend(nodes)
        cache_filename = f"{get_url_hash(url)}.yaml"
        cache_filepath = os.path.join(CONFIG['cache_dir'], cache_filename)
        with open(cache_filepath, 'w', encoding='utf-8') as f:
            yaml.dump({'proxies': nodes}, f, allow_unicode=True)
        sub_list_for_testing.append(f"https://raw.githubusercontent.com/{repo}/main/{cache_filepath}")
    with open(CONFIG['output_task_list'], 'w', encoding='utf-8') as f: f.write("\n".join(sub_list_for_testing))
    print(f"\n✅ 任务链接集 {CONFIG['output_task_list']} 已生成，包含 {len(sub_list_for_testing)} 个独立的源镜像。")
    full_config = {'proxies': all_collected_nodes}
    with open(CONFIG['output_full_package'], 'w', encoding='utf-8') as f:
        yaml.dump(full_config, f, allow_unicode=True, sort_keys=False)
    print(f"✅ 完整节点包 {CONFIG['output_full_package']} 已生成，包含 {len(all_collected_nodes)} 个节点。")
    lite_count = min(len(all_collected_nodes), CONFIG.get('lite_node_count', 1000))
    lite_nodes = random.sample(all_collected_nodes, lite_count) if all_collected_nodes else []
    lite_config = {'proxies': lite_nodes}
    with open(CONFIG['output_lite_package'], 'w', encoding='utf-8') as f:
        yaml.dump(lite_config, f, allow_unicode=True, sort_keys=False)
    print(f"✅ 轻量版节点包 {CONFIG['output_lite_package']} 已生成，包含 {lite_count} 个节点。")
    header = list(links_db.values())[0].keys() if links_db else []
    if header:
        with open(CONFIG['db_file'], 'w', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=header); writer.writeheader(); writer.writerows(links_db.values())

if __name__ == "__main__":
    asyncio.run(main())

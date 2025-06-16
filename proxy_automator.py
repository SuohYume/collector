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
from datetime import datetime, timezone
from config import CONFIG

SSL_CONTEXT = ssl.create_default_context()
SSL_CONTEXT.check_hostname = False
SSL_CONTEXT.verify_mode = ssl.CERT_NONE

def ensure_id(row: dict) -> bool:
    if not row.get('id'):
        if row.get('url'):
            row['id'] = f"sub_{md5(row['url'].encode()).hexdigest()[:10]}"
            return True
    return False

def parse_link_to_dict(link: str) -> dict | None:
    link = link.strip()
    try:
        if link.startswith('vmess://'):
            b64_str = link.replace('vmess://', ''); padding = len(b64_str) % 4
            if padding > 0: b64_str += "=" * (4 - padding)
            decoded_json = json.loads(base64.b64decode(b64_str).decode('utf-8'))
            node = {"name": decoded_json.get('ps', decoded_json.get('add', '')), "type": "vmess", "server": decoded_json.get('add'), "port": int(decoded_json.get('port')), "uuid": decoded_json.get('id'), "alterId": int(decoded_json.get('aid', 0)), "cipher": "auto", "network": decoded_json.get('net', 'tcp'), "tls": decoded_json.get('tls') == 'tls'}
            if node['network'] == 'ws': node['ws-opts'] = {"path": decoded_json.get('path', '/'), "headers": {"Host": decoded_json.get('host', '')}}
            return node
        elif link.startswith('vless://') or link.startswith('trojan://'):
            parsed_url = urlparse(link); params = parse_qs(parsed_url.query)
            node = {"name": unquote(parsed_url.fragment) if parsed_url.fragment else parsed_url.hostname, "type": parsed_url.scheme, "server": parsed_url.hostname, "port": parsed_url.port, "network": params.get('type', ['tcp'])[0], "tls": params.get('security', ['none'])[0] == 'tls', "skip-cert-verify": True}
            if node['type'] == 'vless': node['uuid'] = parsed_url.username
            else: node['password'] = parsed_url.username
            if node['network'] == 'ws': node['ws-opts'] = {"path": params.get('path', ['/'])[0], "headers": {"Host": params.get('host', [parsed_url.hostname])[0]}}
            if node['tls']: node['servername'] = params.get('sni', [node.get('ws-opts', {}).get('headers', {}).get('Host', parsed_url.hostname)])[0]
            return node
        elif link.startswith('ss://'):
            main_part, _, name = link.replace('ss://', '').partition('#'); name = unquote(name) if name else None
            if '@' not in main_part:
                padding = len(main_part) % 4
                if padding > 0: main_part += "=" * (4 - padding)
                decoded_creds = base64.b64decode(main_part).decode('utf-8')
            else: decoded_creds = main_part
            creds_part, server_part = decoded_creds.rsplit('@', 1)
            server, port = server_part.split(':', 1)
            try: method, password = creds_part.split(':', 1)
            except ValueError: return None
            return {"name": name if name else server, "type": "ss", "server": server, "port": int(port), "cipher": method, "password": password}
    except Exception: return None
    return None

def parse_nodes_from_plaintext(text: str) -> list:
    proxy_pattern = r"(ss|trojan|vless|vmess)://[a-zA-Z0-9+/=_{},'\"\-?&%.#@:\[\]\\]+"
    links = re.findall(proxy_pattern, text, re.IGNORECASE)
    return [node for link in links if (node := parse_link_to_dict(link))]

def parse_nodes_from_content(text: str) -> list:
    if not text: return []
    try:
        content = yaml.safe_load(text)
        if isinstance(content, dict) and 'proxies' in content and isinstance(content['proxies'], list):
            nodes = []
            for item in content['proxies']:
                if isinstance(item, dict): nodes.append(item)
                elif isinstance(item, str):
                    try: nodes.append(json.loads(item))
                    except json.JSONDecodeError: pass
            return nodes
    except Exception: pass
    try:
        decoded_text = base64.b64decode(''.join(text.split())).decode('utf-8')
        nodes = parse_nodes_from_plaintext(decoded_text)
        if nodes: return nodes
    except Exception: pass
    return parse_nodes_from_plaintext(text)

async def fetch_content(session: aiohttp.ClientSession, url: str, url_type: str) -> tuple[str | None, str | None]:
    headers = {'User-Agent': 'Clash'}
    proxy = CONFIG.get("FETCHER_PROXY")
    async def get(target_url: str):
        try:
            async with session.get(target_url, headers=headers, timeout=CONFIG['REQUEST_TIMEOUT'], ssl=SSL_CONTEXT, proxy=proxy) as response:
                response.raise_for_status()
                return await response.text()
        except Exception: return None

    if url_type == 'api': return await get(url.rstrip('/') + '/clash/proxies'), 'api'
    if url_type == 'raw': return await get(url), 'raw'

    content = await get(url.rstrip('/') + '/clash/proxies')
    if content and parse_nodes_from_content(content): return content, 'api'
    content = await get(url)
    if content and parse_nodes_from_content(content): return content, 'raw'

    print(f"❌ 自动探测失败: {url}")
    return None, None

def generate_fingerprint(node):
    # ... (代码与上一版相同) ...

async def main():
    try:
        with open(CONFIG['DB_FILE'], 'r', newline='', encoding='utf-8') as f:
            links_db = list(csv.DictReader(f))
    except FileNotFoundError: links_db = []

    any(ensure_id(row) for row in links_db)

    try:
        with open(CONFIG['REPORT_FILE'], 'r', newline='', encoding='utf-8') as f:
            naughty_list = {row['failed_id'] for row in csv.DictReader(f)}
    except FileNotFoundError: naughty_list = set()

    for row in links_db:
        row['failure_streak'] = int(row.get('failure_streak', 0))
        if row.get('id') in naughty_list: row['failure_streak'] += 1
        else: row['failure_streak'] = 0
        row['status'] = 'dead' if row['failure_streak'] >= CONFIG['MAX_FAILURE_STREAK'] else 'active'
        row['last_report_time'] = datetime.now(timezone.utc).isoformat()

    def get_priority(row):
        return (0 if row.get('status') == 'active' else 1, row['failure_streak'])
    links_db.sort(key=get_priority)

    all_nodes = []
    async with aiohttp.ClientSession() as session:
        tasks = [fetch_content(session, ld['url'], ld.get('type', 'auto')) for ld in links_db if ld.get('status') != 'dead']
        results = await asyncio.gather(*tasks)

    for i, content_tuple in enumerate(results):
        content, detected_type = content_tuple
        link_data = [ld for ld in links_db if ld.get('status') != 'dead'][i]
        if content:
            nodes = parse_nodes_from_content(content)
            if nodes:
                link_data['type'] = detected_type # 更新探测到的类型
                for node in nodes: node['_source_url'] = link_data['url'] #注入来源
                all_nodes.extend(nodes)

    # 全局去重
    unique_nodes_map = {generate_fingerprint(node): node for node in all_nodes if generate_fingerprint(node)}
    unique_nodes = list(unique_nodes_map.values())

    # 精准切片
    if len(unique_nodes) > CONFIG['NODE_QUOTA']:
        final_nodes = random.sample(unique_nodes, CONFIG['NODE_QUOTA'])
    else:
        final_nodes = unique_nodes

    # 来源标记
    def get_source_tag(url): return f"[{urlparse(url).netloc}]"
    for node in final_nodes:
        source_url = node.pop('_source_url', 'Unknown')
        node['name'] = f"{get_source_tag(source_url)}{node.get('name', 'Unnamed')}"

    # 输出最终文件
    with open(CONFIG['FINAL_PACKAGE_FILE'], 'w', encoding='utf-8') as f:
        yaml.dump({'proxies': final_nodes}, f, allow_unicode=True, sort_keys=False)
    print(f"✅ 最终任务包 {CONFIG['FINAL_PACKAGE_FILE']} 已生成，包含 {len(final_nodes)} 个节点。")

    # 回写数据库
    header = ['id', 'url', 'type', 'status', 'last_report_time', 'failure_streak']
    with open(CONFIG['DB_FILE'], 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=header, extrasaction='ignore'); writer.writeheader(); writer.writerows(links_db)

if __name__ == "__main__":
    asyncio.run(main())

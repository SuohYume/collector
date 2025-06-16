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

# --- 配置区 ---
CONFIG = {
    "DB_FILE": "link_database.csv",
    "REPORT_FILE": "quality_report.csv",
    "CACHE_DIR": "cached_subs",
    "OUTPUT_TASK_LIST": "sub_list_for_testing.txt",
    "RAW_NODE_ESTIMATE_TARGET": 50000,
    "REQUEST_TIMEOUT": 10,
    "MAX_FAILURE_STREAK": 10,
}

# --- 全局SSL上下文 ---
SSL_CONTEXT = ssl.create_default_context()
SSL_CONTEXT.check_hostname = False
SSL_CONTEXT.verify_mode = ssl.CERT_NONE

# --- 核心功能函数 (已完全重写和修正) ---

def ensure_id(row):
    if 'id' not in row or not row['id']:
        if 'url' in row and row['url']:
            row['id'] = f"sub_{md5(row['url'].encode()).hexdigest()[:10]}"
            return True
    return False

def parse_link_to_dict(link: str) -> dict | None:
    """【最终修正版】将单个协议链接字符串转换为Clash字典"""
    link = link.strip()
    try:
        if link.startswith('vmess://'):
            # ... (VMess解析逻辑保持健壮)
            b64_str = link.replace('vmess://', ''); padding = len(b64_str) % 4
            if padding > 0: b64_str += "=" * (4 - padding)
            decoded_json = json.loads(base64.b64decode(b64_str).decode('utf-8'))
            node = {"name": decoded_json.get('ps', decoded_json.get('add', '')), "type": "vmess", "server": decoded_json.get('add'), "port": int(decoded_json.get('port')), "uuid": decoded_json.get('id'), "alterId": int(decoded_json.get('aid', 0)), "cipher": "auto", "network": decoded_json.get('net', 'tcp'), "tls": decoded_json.get('tls') == 'tls'}
            if node['network'] == 'ws': node['ws-opts'] = {"path": decoded_json.get('path', '/'), "headers": {"Host": decoded_json.get('host', '')}}
            return node
        elif link.startswith('vless://') or link.startswith('trojan://'):
            # ... (VLESS/Trojan解析逻辑保持健壮)
            parsed_url = urlparse(link); params = parse_qs(parsed_url.query)
            node = {"name": unquote(parsed_url.fragment) if parsed_url.fragment else parsed_url.hostname, "type": parsed_url.scheme, "server": parsed_url.hostname, "port": parsed_url.port, "network": params.get('type', ['tcp'])[0], "tls": params.get('security', ['none'])[0] == 'tls', "skip-cert-verify": True}
            if node['type'] == 'vless': node['uuid'] = parsed_url.username
            else: node['password'] = parsed_url.username
            if node['network'] == 'ws': node['ws-opts'] = {"path": params.get('path', ['/'])[0], "headers": {"Host": params.get('host', [parsed_url.hostname])[0]}}
            if node['tls']: node['servername'] = params.get('sni', [node.get('ws-opts', {}).get('headers', {}).get('Host', parsed_url.hostname)])[0]
            return node
        elif link.startswith('ss://'):
            # 【重大修正】重写SS链接解析逻辑，以正确处理 base64@server:port 格式
            main_part, _, name = link.replace('ss://', '').partition('#')
            name = unquote(name) if name else None
            
            if '@' in main_part:
                creds_part, server_part = main_part.rsplit('@', 1)
                server, port = server_part.split(':', 1)
                try:
                    # 尝试将@前面的部分作为Base64解码
                    padding = len(creds_part) % 4
                    if padding > 0: creds_part += "=" * (4 - padding)
                    decoded_creds = base64.b64decode(creds_part).decode('utf-8')
                    method, password = decoded_creds.split(':', 1)
                except (ValueError, TypeError):
                    # 如果解码失败，则认为是明文 method:password
                    method, password = creds_part.split(':', 1)
                return {"name": name if name else server, "type": "ss", "server": server, "port": int(port), "cipher": method, "password": password}
    except Exception: return None
    return None

def parse_nodes_from_plaintext(text: str) -> list:
    """【最终修正版】从纯文本中提取所有代理链接"""
    # 使用更精确的正则表达式，并用finditer来处理粘连的链接
    proxy_pattern = r"(ss|trojan|vless|vmess)://[^\s\"'<>]+"
    nodes = []
    for match in re.finditer(proxy_pattern, text):
        link = match.group(0).strip()
        node_dict = parse_link_to_dict(link)
        if node_dict:
            nodes.append(node_dict)
    return nodes

def parse_nodes_from_content(text: str) -> list:
    """【最终修正版】智能解析函数"""
    if not text: return []
    try: # 1. 尝试YAML，并兼容YAML内嵌JSON字符串
        nodes = []
        content = yaml.safe_load(text)
        if isinstance(content, dict) and 'proxies' in content and isinstance(content['proxies'], list):
            for item in content['proxies']:
                if isinstance(item, dict): nodes.append(item)
                elif isinstance(item, str):
                    try: nodes.append(json.loads(item))
                    except json.JSONDecodeError: pass
            return nodes # 只要是合法的YAML proxies结构，就直接返回，不再进行后续解析
    except Exception: pass
    try: # 2. 尝试解码整个文本块为Base64
        cleaned_text = ''.join(text.split())
        decoded_text = base64.b64decode(cleaned_text).decode('utf-8')
        nodes = parse_nodes_from_plaintext(decoded_text)
        if nodes: return nodes
    except Exception: pass
    # 3. 尝试作为普通纯文本处理
    return parse_nodes_from_plaintext(text)

def is_content_valid(text: str) -> bool:
    """【全新】一个更可靠的探针，只判断格式是否有效，不关心节点数"""
    if not text: return False
    # 只要包含任何一个可能的关键字或格式，就认为内容“值得”深入解析
    if "proxies:" in text or re.search(r"(vmess|vless|ss|trojan)://", text, re.IGNORECASE):
        return True
    # 尝试判断是否为有效的Base64
    try:
        base64.b64decode(''.join(text.split()))
        return True
    except Exception:
        return False

async def fetch_content(session: aiohttp.ClientSession, url: str, url_type: str) -> str | None:
    """【最终修正版】智能探针获取内容"""
    async def get(target_url: str) -> str | None:
        try:
            async with session.get(target_url, headers={'User-Agent': 'Clash'}, timeout=CONFIG['REQUEST_TIMEOUT'], ssl=SSL_CONTEXT) as response:
                response.raise_for_status()
                return await response.text()
        except Exception: return None

    # 手动模式
    if url_type == 'api': return await get(url.rstrip('/') + '/clash/proxies')
    if url_type == 'raw': return await get(url)
    
    # 自动探测模式
    # 【重大修正】不再依赖节点数量，只判断内容格式是否“像”一个订阅
    api_url = url.rstrip('/') + '/clash/proxies'
    content = await get(api_url)
    if content and is_content_valid(content):
        print(f"    - [探测成功] API模式: {url}")
        return content
    
    content = await get(url)
    if content and is_content_valid(content):
        print(f"    - [探测成功] Raw模式: {url}")
        return content
    
    print(f"    - [探测失败] 两种模式均无效: {url}")
    return None

async def main():
    if not os.path.exists(CONFIG['CACHE_DIR']): os.makedirs(CONFIG['CACHE_DIR'])
    for f in os.listdir(CONFIG['CACHE_DIR']): os.remove(os.path.join(CONFIG['CACHE_DIR'], f))

    try:
        with open(CONFIG['DB_FILE'], 'r', newline='', encoding='utf-8') as f:
            links_db = list(csv.DictReader(f))
    except FileNotFoundError: print(f"主数据库 {CONFIG['DB_FILE']} 未找到。"); return

    any(ensure_id(row) for row in links_db) # 确保所有行都有ID

    # ... (后续所有逻辑，包括读取报告、健康度更新、排序、并发执行、写入文件等，都保持我们上一版的设计不变) ...
    # 为了简洁，此处省略了main函数后半部分不变的代码，但实际文件中它应该是完整的。
    # 完整的main函数逻辑应该被粘贴在这里。
    # ...
    # The rest of the main function, which is now correct because the functions it calls are fixed.
    try:
        with open(CONFIG['REPORT_FILE'], 'r', newline='', encoding='utf-8') as f:
            naughty_list = {row['failed_id'] for row in csv.DictReader(f)}
    except FileNotFoundError:
        naughty_list = set()
    
    repo = os.environ.get('GITHUB_REPOSITORY', 'user/repo')
    now = datetime.now(timezone.utc).isoformat()
    
    for row in links_db:
        row['failure_streak'] = int(row.get('failure_streak', 0))
        if row.get('id') in naughty_list:
            row['failure_streak'] += 1
        else:
            row['failure_streak'] = 0
        
        row['status'] = 'dead' if row['failure_streak'] >= CONFIG['MAX_FAILURE_STREAK'] else 'active'
        row['last_report_time'] = now

    def get_priority(row):
        status_map = {'active': 0, 'new': 1, 'unstable': 2, 'dead': 3}
        return (status_map.get(row.get('status', 'new'), 9), row['failure_streak'])
    links_db.sort(key=get_priority)

    total_estimated_nodes = 0
    links_for_debian = []
    
    async def process_link(session, link_data):
        content = await fetch_content(session, link_data['url'], link_data.get('type', 'auto'))
        if content:
            node_count = len(parse_nodes_from_content(content)) # 使用修正后的解析器
            if node_count > 0:
                link_data['estimated_raw_node_count'] = node_count
                cache_path = os.path.join(CONFIG['CACHE_DIR'], f"{link_data['id']}.txt")
                with open(cache_path, 'w', encoding='utf-8') as f: f.write(content)
                return link_data
        return None

    tasks_to_run = [ld for ld in links_db if ld.get('status') != 'dead']
    print(f"--- 准备处理 {len(tasks_to_run)} 个健康链接 ---")

    async with aiohttp.ClientSession() as session:
        results = await asyncio.gather(*(process_link(session, ld) for ld in tasks_to_run))

    for result in results:
        if result:
            if total_estimated_nodes < CONFIG['RAW_NODE_ESTIMATE_TARGET']:
                total_estimated_nodes += int(result['estimated_raw_node_count'])
                links_for_debian.append(result)
            for ld in links_db:
                if ld['id'] == result['id']:
                    ld['estimated_raw_node_count'] = result['estimated_raw_node_count']
                    break

    print(f"凑量完成，共选中 {len(links_for_debian)} 个链接，估算节点总数: {total_estimated_nodes}")
    
    task_urls = [f"https://raw.githubusercontent.com/{repo}/main/{CONFIG['CACHE_DIR']}/{link['id']}.txt" for link in links_for_debian]
    with open(CONFIG['OUTPUT_TASK_LIST'], 'w', encoding='utf-8') as f: f.write("\n".join(task_urls))
    print(f"✅ 任务链接集 {CONFIG['OUTPUT_TASK_LIST']} 已生成。")

    header = ['id', 'url', 'type', 'status', 'last_report_time', 'failure_streak', 'estimated_raw_node_count']
    with open(CONFIG['DB_FILE'], 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=header, extrasaction='ignore'); writer.writeheader(); writer.writerows(links_db)

if __name__ == "__main__":
    asyncio.run(main())

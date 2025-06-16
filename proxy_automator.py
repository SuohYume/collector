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

# --- 全局SSL上下文 ---
SSL_CONTEXT = ssl.create_default_context()
SSL_CONTEXT.check_hostname = False
SSL_CONTEXT.verify_mode = ssl.CERT_NONE

# =================================================================================
# --- “工业级”全协议解析引擎 (对标 subs-check 逻辑) ---
# =================================================================================

def parse_link_to_dict(link: str) -> dict | None:
    """将单个协议链接字符串(ss/vmess/vless/trojan)转换为Clash字典"""
    link = link.strip()
    try:
        if link.startswith('vmess://'):
            b64_str = link.replace('vmess://', '')
            padding = len(b64_str) % 4
            if padding > 0: b64_str += "=" * (4 - padding)
            decoded_json_str = base64.b64decode(b64_str).decode('utf-8')
            vmess_data = json.loads(decoded_json_str)
            node = {
                "name": vmess_data.get('ps', vmess_data.get('add', 'vmess_node')),
                "type": "vmess", "server": vmess_data.get('add'),
                "port": int(vmess_data.get('port')), "uuid": vmess_data.get('id'),
                "alterId": int(vmess_data.get('aid', 0)), "cipher": vmess_data.get('scy', 'auto'),
                "network": vmess_data.get('net', 'tcp'), "tls": vmess_data.get('tls') == 'tls'
            }
            if node['network'] == 'ws':
                node['ws-opts'] = {"path": vmess_data.get('path', '/'), "headers": {"Host": vmess_data.get('host', '')}}
            return node

        elif link.startswith('vless://') or link.startswith('trojan://'):
            parsed_url = urlparse(link)
            params = parse_qs(parsed_url.query)
            node = {
                "name": unquote(parsed_url.fragment) if parsed_url.fragment else parsed_url.hostname,
                "type": parsed_url.scheme, "server": parsed_url.hostname, "port": parsed_url.port,
                "network": params.get('type', ['tcp'])[0], "tls": params.get('security', ['none'])[0] in ['tls', 'xtls'],
                "skip-cert-verify": True
            }
            if node['type'] == 'vless':
                node['uuid'] = parsed_url.username
            else:  # trojan
                node['password'] = parsed_url.username
            
            if node['tls']:
                node['servername'] = params.get('sni', [params.get('host', [parsed_url.hostname])[0]])[0]

            if node['network'] == 'ws':
                node['ws-opts'] = {"path": params.get('path', ['/'])[0], "headers": {"Host": params.get('host', [parsed_url.hostname])[0]}}
            return node

        elif link.startswith('ss://'):
            main_part, _, name_part = link.replace('ss://', '').partition('#')
            name = unquote(name_part) if name_part else None
            
            if '@' in main_part:
                creds_part, server_part = main_part.rsplit('@', 1)
                try:
                    decoded_creds = base64.b64decode(creds_part + '==' * (-len(creds_part) % 4)).decode('utf-8')
                    method, password = decoded_creds.split(':', 1)
                except (ValueError, TypeError):
                    return None # Invalid Base64 creds
            else: # URL-encoded format
                server_part = main_part
                password = ''
                method = ''

            server, port = server_part.split(':', 1)
            name = name if name else server
            return {"name": name, "type": "ss", "server": server, "port": int(port), "cipher": method, "password": password}

    except Exception: return None
    return None

def parse_nodes_from_plaintext(text: str) -> list:
    """从纯文本中提取所有代理链接"""
    proxy_pattern = r"(ss|trojan|vless|vmess)://[a-zA-Z0-9+/=_{},'\"\-?&%.#@:\[\]\\]+"
    links = re.findall(proxy_pattern, text, re.IGNORECASE)
    return [node for link in links if (node := parse_link_to_dict(link))]

def parse_nodes_from_content(text: str) -> list:
    """智能解析函数，应对所有已知情况"""
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
            return nodes
    except Exception: pass
    try: # 2. 尝试解码整个文本块为Base64
        cleaned_text = ''.join(text.split())
        decoded_text = base64.b64decode(cleaned_text).decode('utf-8')
        nodes = parse_nodes_from_plaintext(decoded_text)
        if nodes: return nodes
    except Exception: pass
    # 3. 尝试作为普通纯文本处理
    return parse_nodes_from_plaintext(text)

# --- 其他辅助函数 ---
def ensure_id(row):
    if not row.get('id'):
        if row.get('url'):
            row['id'] = f"sub_{md5(row['url'].encode()).hexdigest()[:10]}"
            return True
    return False

def generate_fingerprint(node):
    if not isinstance(node, dict): return None
    key_fields = ['server', 'port', 'type']
    try:
        if node['type'] == 'ss': key_fields.extend(['password', 'cipher'])
        elif node['type'] in ['vmess', 'vless']: key_fields.append('uuid')
        elif node['type'] == 'trojan': key_fields.append('password')
        return "-".join(sorted([f"{k}:{node.get(k, '')}" for k in key_fields]))
    except KeyError: return None

async def fetch_content(session, url):
    """获取URL原始内容"""
    headers = {'User-Agent': 'Clash'}
    proxy = CONFIG.get("FETCHER_PROXY")
    try:
        async with session.get(url, headers=headers, timeout=CONFIG['REQUEST_TIMEOUT'], ssl=SSL_CONTEXT, proxy=proxy) as response:
            response.raise_for_status()
            return await response.text()
    except Exception as e:
        print(f"❌ 获取内容失败: {url}, 原因: {e}")
        return None

async def process_link(session, link_data):
    """处理单个链接：获取、解析、返回带来源的节点列表"""
    content = await fetch_content(session, link_data['url'])
    if content:
        nodes = parse_nodes_from_content(content)
        if nodes:
            for node in nodes:
                node['_source_id'] = link_data['id']
            return nodes
    return []

# --- 主执行逻辑 ---
async def main():
    # 1. 初始化和数据库ID管理
    try:
        with open(CONFIG['DB_FILE'], 'r', newline='', encoding='utf-8') as f:
            links_db = list(csv.DictReader(f))
    except FileNotFoundError: links_db = []
    any(ensure_id(row) for row in links_db)
    
    # 2. 读取“差生报告”并更新健康度
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

    # 3. 按优先级排序链接
    def get_priority(row):
        return (0 if row.get('status') == 'active' else 1, row['failure_streak'])
    links_db.sort(key=get_priority)

    # 4. 并发获取所有健康链接的节点
    tasks_to_run = [ld for ld in links_db if ld.get('status') != 'dead']
    print(f"--- 准备处理 {len(tasks_to_run)} 个健康链接 ---")
    
    all_nodes_with_source = []
    async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(limit=CONFIG['MAX_CONCURRENT_REQUESTS'])) as session:
        results = await asyncio.gather(*(process_link(session, ld) for ld in tasks_to_run))
        for node_list in results:
            if node_list: all_nodes_with_source.extend(node_list)

    print(f"--- 收集完成，共获取 {len(all_nodes_with_source)} 个原始节点 ---")
    
    # 5. 全局去重
    unique_nodes = list({generate_fingerprint(node): node for node in all_nodes_with_source if generate_fingerprint(node)}.values())
    print(f"--- 全局去重完成，剩余 {len(unique_nodes)} 个唯一节点 ---")

    # 6. 精准切片
    if len(unique_nodes) > CONFIG['NODE_QUOTA']:
        final_nodes = random.sample(unique_nodes, CONFIG['NODE_QUOTA'])
    else:
        final_nodes = unique_nodes
    print(f"--- 精准切片完成，最终打包 {len(final_nodes)} 个节点 ---")

    # 7. 生成最终产物
    repo = os.environ.get('GITHUB_REPOSITORY', 'user/repo')
    used_source_ids = {node['_source_id'] for node in final_nodes}
    
    # a. 生成最终的Clash任务包
    final_package_proxies = []
    for node in final_nodes:
        node_copy = node.copy()
        node_copy.pop('_source_id', None) # 移除内部使用的source_id
        final_package_proxies.append(node_copy)

    with open(CONFIG['FINAL_PACKAGE_FILE'], 'w', encoding='utf-8') as f:
        yaml.dump({'proxies': final_package_proxies}, f, allow_unicode=True, sort_keys=False)

    # b. 生成一个轻量版，作为便利选项
    lite_nodes = random.sample(final_package_proxies, min(len(final_package_proxies), CONFIG.get('LITE_NODE_COUNT', 1000)))
    with open(CONFIG['LITE_PACKAGE_FILE'], 'w', encoding='utf-8') as f:
        yaml.dump({'proxies': lite_nodes}, f, allow_unicode=True, sort_keys=False)

    # c. 生成供您本地报告使用的、实际被打包了的源链接列表
    final_source_links = [ld['url'] for ld in links_db if ld['id'] in used_source_ids]
    with open(CONFIG['FINAL_SOURCES_LIST'], 'w', encoding='utf-8') as f:
        f.write("\n".join(final_source_links))

    print(f"✅ 所有产物生成完毕!")
    
    # 8. 回写数据库
    header = ['id', 'url', 'type', 'status', 'last_report_time', 'failure_streak']
    with open(CONFIG['DB_FILE'], 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=header, extrasaction='ignore'); writer.writeheader(); writer.writerows(links_db)

if __name__ == "__main__":
    asyncio.run(main())

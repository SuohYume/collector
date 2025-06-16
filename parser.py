import csv
import json
import yaml
import base64
import re
from urllib.parse import urlparse, unquote, parse_qs
from config import CONFIG

# =================================================================================
# --- 核心解析函数 (之前遗漏的部分) ---
# =================================================================================

def parse_link_to_dict(link: str) -> dict | None:
    """【强化版】将单个协议链接字符串转换为Clash字典"""
    link = link.strip()
    try:
        if link.startswith('vmess://'):
            b64_str = link.replace('vmess://', '')
            # 修正Base64字符串的padding
            padding = len(b64_str) % 4
            if padding > 0:
                b64_str += "=" * (4 - padding)
            decoded_json = json.loads(base64.b64decode(b64_str).decode('utf-8'))
            node = {
                "name": decoded_json.get('ps', decoded_json.get('add', '')),
                "type": "vmess",
                "server": decoded_json.get('add'),
                "port": int(decoded_json.get('port')),
                "uuid": decoded_json.get('id'),
                "alterId": int(decoded_json.get('aid', 0)),
                "cipher": "auto",
                "network": decoded_json.get('net', 'tcp'),
                "tls": decoded_json.get('tls') == 'tls'
            }
            if node['network'] == 'ws':
                node['ws-opts'] = {
                    "path": decoded_json.get('path', '/'),
                    "headers": {"Host": decoded_json.get('host', '')}
                }
            return node
        elif link.startswith('vless://') or link.startswith('trojan://'):
            parsed_url = urlparse(link)
            params = parse_qs(parsed_url.query)
            node = {
                "name": unquote(parsed_url.fragment) if parsed_url.fragment else parsed_url.hostname,
                "type": parsed_url.scheme,
                "server": parsed_url.hostname,
                "port": parsed_url.port,
                "network": params.get('type', ['tcp'])[0],
                "tls": params.get('security', ['none'])[0] == 'tls',
                "skip-cert-verify": True # 普遍需要
            }
            if node['type'] == 'vless':
                node['uuid'] = parsed_url.username
            else:  # trojan
                node['password'] = parsed_url.username
            
            if node['network'] == 'ws':
                node['ws-opts'] = {
                    "path": params.get('path', ['/'])[0],
                    "headers": {"Host": params.get('host', [parsed_url.hostname])[0]}
                }
            if node['tls']:
                node['servername'] = params.get('sni', [node['ws-opts']['headers']['Host'] if 'ws-opts' in node and 'Host' in node['ws-opts']['headers'] else parsed_url.hostname])[0]
            return node
        elif link.startswith('ss://'):
            main_part, _, name = link.replace('ss://', '').partition('#')
            name = unquote(name) if name else None
            
            if '@' not in main_part:
                # Base64格式: base64(method:password)@server:port
                padding = len(main_part) % 4
                if padding > 0:
                    main_part += "=" * (4 - padding)
                decoded_creds = base64.b64decode(main_part).decode('utf-8')
            else:
                decoded_creds = main_part

            creds_part, server_part = decoded_creds.rsplit('@', 1)
            method, password = creds_part.split(':', 1)
            server, port = server_part.split(':', 1)
            
            return {
                "name": name if name else server, "type": "ss",
                "server": server, "port": int(port),
                "cipher": method, "password": password
            }
    except Exception as e:
        # print(f"解析链接失败: {link}, 原因: {e}")
        return None

def parse_nodes_from_plaintext(text: str) -> list:
    """【强化版】使用正则表达式从纯文本中提取所有代理链接"""
    # 修正正则表达式以正确处理各种字符
    proxy_pattern = r"(ss|trojan|vless|vmess)://[a-zA-Z0-9+/=_{},'\"\-?&%.#@:\[\]\\]+"
    found_links = re.findall(proxy_pattern, text, re.IGNORECASE)
    
    nodes = []
    for link in found_links:
        node_dict = parse_link_to_dict(link)
        if node_dict:
            nodes.append(node_dict)
    return nodes

def parse_nodes_from_content(text: str) -> list:
    """【强化版】智能解析函数，应对所有已知情况"""
    if not text:
        return []
    
    # 1. 尝试YAML，并兼容YAML内嵌JSON字符串的特殊格式
    try:
        nodes = []
        content = yaml.safe_load(text)
        if isinstance(content, dict) and 'proxies' in content and isinstance(content['proxies'], list):
            for item in content['proxies']:
                if isinstance(item, dict):
                    nodes.append(item)
                elif isinstance(item, str):
                    try:
                        nodes.append(json.loads(item))
                    except json.JSONDecodeError:
                        # 如果字符串不是JSON，可能是个单独的链接
                        node_dict = parse_link_to_dict(item)
                        if node_dict: nodes.append(node_dict)
            if nodes:
                return nodes
    except Exception:
        pass

    # 2. 尝试解码整个文本块为Base64
    try:
        # 清理可能的空白字符
        cleaned_text = ''.join(text.split())
        decoded_text = base64.b64decode(cleaned_text).decode('utf-8')
        # 解码后进行纯文本解析
        nodes = parse_nodes_from_plaintext(decoded_text)
        if nodes:
            return nodes
    except Exception:
        pass

    # 3. 尝试作为普通纯文本处理
    return parse_nodes_from_plaintext(text)


# =================================================================================
# --- 主执行逻辑 ---
# =================================================================================

def main():
    """
    主函数：读取fetcher的结果，解析所有缓存文件，生成包含所有节点的原始JSON文件。
    """
    try:
        with open(CONFIG['FETCH_RESULTS_FILE'], 'r', newline='', encoding='utf-8') as f:
            fetch_results = list(csv.DictReader(f))
    except FileNotFoundError:
        print("Parser: Fetch results file not found. Is fetcher.py running correctly? Exiting.")
        return
    
    all_nodes = []
    print(f"Parser: Starting to parse {len(fetch_results)} fetched contents...")
    for result in fetch_results:
        if result['status'] == 'success':
            try:
                with open(result['cache_path'], 'r', encoding='utf-8') as f:
                    content = f.read()
                
                nodes = parse_nodes_from_content(content)
                
                if nodes:
                    # 为每个节点注入来源ID，供下一步打包时使用
                    for node in nodes:
                        node['_source_id'] = result['id']
                    all_nodes.extend(nodes)
                    print(f"    - Parsed {len(nodes)} nodes from cache of {result['id']}")
            except Exception as e:
                print(f"    - Failed to read or parse cache file {result['cache_path']}: {e}")
    
    with open(CONFIG['RAW_NODES_FILE'], 'w', encoding='utf-8') as f:
        json.dump(all_nodes, f)
    
    print(f"Parser: Finished. Extracted a total of {len(all_nodes)} raw nodes into {CONFIG['RAW_NODES_FILE']}.")

if __name__ == "__main__":
    main()

# packager.py
import json, yaml, random
# ... (粘贴上一版回复中的 generate_fingerprint, get_domain, generate_source_tag 函数) ...
from config import CONFIG

def main():
    with open(CONFIG['RAW_NODES_FILE'], 'r', encoding='utf-8') as f:
        all_nodes = json.load(f)

    unique_nodes_map = {} # 使用字典去重，保留第一个遇到的节点
    for node in all_nodes:
        fingerprint = generate_fingerprint(node)
        if fingerprint and fingerprint not in unique_nodes_map:
            unique_nodes_map[fingerprint] = node

    unique_nodes = list(unique_nodes_map.values())

    if len(unique_nodes) > CONFIG['NODE_QUOTA']:
        final_nodes = random.sample(unique_nodes, CONFIG['NODE_QUOTA'])
    else:
        final_nodes = unique_nodes

    # 来源标记
    used_source_ids = set()
    for node in final_nodes:
        source_id = node.pop('_source_id', 'Unknown')
        used_source_ids.add(source_id)
        # ... 此处可以添加标记逻辑，但为简化，最终包不加标记，只在报告时使用

    # 打包最终产物
    with open(CONFIG['FULL_PACKAGE_FILE'], 'w', encoding='utf-8') as f:
        yaml.dump({'proxies': final_nodes}, f, allow_unicode=True)

    # ... (写入lite package和final_source_list.txt的逻辑)

    print(f"Packager: Final package '{CONFIG['FULL_PACKAGE_FILE']}' created with {len(final_nodes)} nodes.")

if __name__ == "__main__":
    main()

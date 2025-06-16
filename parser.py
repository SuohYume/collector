# parser.py
import csv, json
# ... (粘贴上一版回复中，最终版的、完整的 parse_link_to_dict, parse_nodes_from_plaintext, parse_nodes_from_content 三个函数) ...
from config import CONFIG

def main():
    with open(CONFIG['FETCH_RESULTS_FILE'], 'r', newline='', encoding='utf-8') as f:
        fetch_results = list(csv.DictReader(f))

    all_nodes = []
    for result in fetch_results:
        if result['status'] == 'success':
            with open(result['cache_path'], 'r', encoding='utf-8') as f: content = f.read()
            nodes = parse_nodes_from_content(content)
            if nodes:
                for node in nodes: node['_source_id'] = result['id']
                all_nodes.extend(nodes)

    with open(CONFIG['RAW_NODES_FILE'], 'w', encoding='utf-8') as f:
        json.dump(all_nodes, f)

    print(f"Parser: Extracted a total of {len(all_nodes)} raw nodes.")

if __name__ == "__main__":
    main()

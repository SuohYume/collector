import requests
import yaml
import time

def check_url(base_url):
    """
    检查单个URL，返回节点数量或错误信息。
    """
    # 构造要测试的完整URL
    test_url = base_url.strip() + "/clash/proxies"
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
    }
    
    try:
        # 设置一个合理的超时时间（例如10秒）
        response = requests.get(test_url, headers=headers, timeout=10)
        # 如果HTTP状态码不是200-299，则引发异常
        response.raise_for_status()
        
        # 尝试使用 PyYAML 解析内容
        content = yaml.safe_load(response.text)
        
        # 检查解析后的内容是否是字典，并且是否包含 'proxies' 键
        if isinstance(content, dict) and 'proxies' in content:
            # 检查 'proxies' 是否是一个列表
            if isinstance(content['proxies'], list):
                node_count = len(content['proxies'])
                if node_count > 0:
                    print(f"✅ 成功: {base_url} - 发现 {node_count} 个节点")
                    return "success", node_count
                else:
                    print(f"🟡 注意: {base_url} - 节点列表为空")
                    return "fail", "节点数为0"
            else:
                print(f"❌ 失败: {base_url} - 'proxies' 格式不正确")
                return "fail", "格式错误"
        else:
            print(f"❌ 失败: {base_url} - 未找到 'proxies' 键")
            return "fail", "无proxies键"
            
    except requests.exceptions.RequestException as e:
        # 处理网络请求相关的错误（超时、连接失败等）
        print(f"❌ 失败: {base_url} - 请求错误: {e}")
        return "fail", "请求失败"
    except yaml.YAMLError:
        # 处理YAML解析错误
        print(f"❌ 失败: {base_url} - 内容不是有效的YAML格式")
        return "fail", "YAML解析失败"
    except Exception as e:
        # 捕获其他所有未知异常
        print(f"❌ 失败: {base_url} - 未知错误: {e}")
        return "fail", "未知错误"

if __name__ == "__main__":
    successful_results = []
    failed_results = []

    # 从 urls.txt 读取URL列表
    with open('urls.txt', 'r', encoding='utf-8') as f:
        urls = f.readlines()

    print(f"--- 开始检查 {len(urls)} 个URL ---")
    
    for url in urls:
        base_url = url.strip()
        if not base_url:
            continue
        
        status, result = check_url(base_url)
        
        if status == "success":
            successful_results.append((base_url, result))
        else:
            failed_results.append((base_url, result))
        # 稍微暂停一下，避免请求过于频繁
        time.sleep(0.1)

    # 将成功的结果按节点数量从多到少排序
    successful_results.sort(key=lambda x: x[1], reverse=True)

    print("\n--- 检查完成，正在生成 results.txt ---")

    # 将所有结果写入 results.txt 文件
    with open('results.txt', 'w', encoding='utf-8') as f:
        f.write(f"--- ✅ 有效节点列表 (共 {len(successful_results)} 个) ---\n")
        f.write(f"--- 更新时间: {time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())} ---\n\n")
        for url, count in successful_results:
            f.write(f"节点数量: {count:<5} | URL: {url}\n")
        
        f.write(f"\n\n--- ❌ 无效或失败的链接 (共 {len(failed_results)} 个) ---\n\n")
        for url, reason in failed_results:
            f.write(f"原因: {reason:<15} | URL: {url}\n")
            
    print("✅ results.txt 文件已成功生成！")

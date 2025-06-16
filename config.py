# config.py
CONFIG = {
    # --- 文件路径 ---
    "DB_FILE": "link_database.csv",
    "REPORT_FILE": "quality_report.csv",
    "FINAL_PACKAGE_FILE": "clash.yaml", # 这是给您Debian服务器订阅的最终产物
    "LITE_PACKAGE_FILE": "clash_lite.yaml",

    # --- 核心行为控制 ---
    "NODE_QUOTA": 20500,  # 最终精准切片到此数量，确保“20000出头”
    "LITE_NODE_COUNT": 1000,
    "REQUEST_TIMEOUT": 10,  # 每个链接的请求超时

    # --- 健康度与生命周期 ---
    "MAX_FAILURE_STREAK": 5, # 连续5次上“差生报告”则标记为dead

    # 【重要】如果您的GitHub Actions也需要代理才能访问订阅链接，请在这里配置
    # 留空则不使用代理
    "FETCHER_PROXY": "", # 例如: "http://proxy.server:port"
}

# config.py
CONFIG = {
    # --- 文件路径 ---
    "DB_FILE": "link_database.csv",
    "REPORT_FILE": "quality_report.csv",
    "CACHE_DIR": "cached_subs",
    "OUTPUT_TASK_LIST": "sub_list_for_testing.txt",

    # --- 核心行为控制 ---
    "RAW_NODE_ESTIMATE_TARGET": 50000,
    "MAX_CONCURRENT_REQUESTS": 100,
    "REQUEST_TIMEOUT": 10,

    # --- 健康度与生命周期 ---
    "MAX_FAILURE_STREAK": 10,

    # 【重要】如果您的GitHub Actions也需要代理才能访问订阅链接，请在这里配置
    # 留空则不使用代理。例如: "http://proxy.server:port"
    "FETCHER_PROXY": "",
}

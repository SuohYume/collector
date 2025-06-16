# config.py
CONFIG = {
    # --- 文件路径 ---
    "DB_FILE": "link_database.csv",
    "REPORT_FILE": "quality_report.csv",
    "CACHE_DIR": "cached_subs",
    "OUTPUT_TASK_LIST": "sub_list_for_testing.txt",

    # --- 核心行为控制 ---
    "RAW_NODE_ESTIMATE_TARGET": 50000,
    "REQUEST_TIMEOUT": 10,
    # 【已添加】修正缺失的并发请求数配置
    "MAX_CONCURRENT_REQUESTS": 100, 

    # --- 健康度与生命周期 ---
    "MAX_FAILURE_STREAK": 10,

    # --- 代理配置 ---
    "FETCHER_PROXY": "https://github.serein.cc/", # 例如: "http://proxy.server:port"
}

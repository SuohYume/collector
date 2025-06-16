# config.py
CONFIG = {
    # --- 文件路径 ---
    "DB_FILE": "link_database.csv",
    "REPORT_FILE": "quality_report.csv",
    "FINAL_PACKAGE_FILE": "clash.yaml", # 给您Debian服务器订阅的最终产物
    "FINAL_SOURCES_LIST": "final_source_list.txt", # 告知Debian报告哪些源

    # --- 核心行为控制 ---
    "NODE_QUOTA": 20500,  # 最终精准切片到此数量，确保“20000出头”
    "MAX_CONCURRENT_REQUESTS": 100, # 并发请求数
    "REQUEST_TIMEOUT": 10, # 请求超时

    # --- 健康度与生命周期 ---
    "MAX_FAILURE_STREAK": 5, # 连续5次在“差生报告”上，则标记为dead
}

# config.py
CONFIG = {
    # --- 文件路径 ---
    "DB_FILE": "link_database.csv",
    "ARCHIVE_FILE": "archive.csv",
    "REPORT_FILE": "quality_report.csv",
    "CACHE_DIR": "cached_subs",
    "TASK_LIST_FILE": "sub_list_for_testing.txt",
    "FULL_PACKAGE_FILE": "clash.yaml",
    "LITE_PACKAGE_FILE": "clash_lite.yaml",

    # --- 行为控制 ---
    "RAW_NODE_ESTIMATE_TARGET": 50000,
    "LITE_NODE_COUNT": 1000,
    "MAX_CONCURRENT_REQUESTS": 100,
    "REQUEST_TIMEOUT": 8,
    
    # --- 健康度与生命周期 ---
    "MAX_FAILURE_STREAK": 10,
    "ARCHIVE_DAYS": 60,
}

# config.py
CONFIG = {
    "DB_FILE": "link_database.csv",
    "ARCHIVE_FILE": "archive.csv",
    "REPORT_FILE": "quality_report.csv",
    "CACHE_DIR": "cached_subs",
    "TASK_LIST_FILE": "task_list.csv",
    "FETCH_RESULTS_FILE": "fetch_results.csv",
    "RAW_NODES_FILE": "all_nodes_raw.json",
    "FINAL_PACKAGE_FILE": "clash.yaml",
    "FINAL_SOURCES_LIST": "final_source_list.txt",
    "NODE_QUOTA": 20500, # 最终切片数量，确保“20000出头”
    "MAX_CONCURRENT_REQUESTS": 100,
    "REQUEST_TIMEOUT": 8,
    "MAX_FAILURE_STREAK": 10,
}

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

    # --- 健康度与生命周期 ---
    "MAX_FAILURE_STREAK": 10,

    # ===================================================================
    # ### 【重要】请在这里填入您本地使用的、可靠的GitHub代理 ###
    # ===================================================================
    "FETCHER_PROXY": "https://github.serein.cc/", 
    # ===================================================================
}

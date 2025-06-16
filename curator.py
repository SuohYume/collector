# curator.py
import csv
from datetime import datetime, timezone
from config import CONFIG

def main():
    with open(CONFIG['DB_FILE'], 'r', newline='', encoding='utf-8') as f:
        links_db = list(csv.DictReader(f))

    try:
        with open(CONFIG['REPORT_FILE'], 'r', newline='', encoding='utf-8') as f:
            naughty_list = {row['failed_id'] for row in csv.DictReader(f)}
    except FileNotFoundError:
        naughty_list = set()

    now = datetime.now(timezone.utc).isoformat()
    for row in links_db:
        row['failure_streak'] = int(row.get('failure_streak', 0))
        if row.get('id') in naughty_list:
            row['failure_streak'] += 1
        else:
            row['failure_streak'] = 0

        if row['failure_streak'] >= CONFIG['MAX_FAILURE_STREAK']:
            row['status'] = 'dead'
        else:
            row['status'] = 'active' if row['failure_streak'] == 0 else 'unstable'
        row['last_report_time'] = now

    def get_priority(row):
        status_map = {'active': 0, 'new': 1, 'unstable': 2, 'dead': 3}
        return (status_map.get(row.get('status', 'new'), 9), row['failure_streak'])
    links_db.sort(key=get_priority)

    tasks = [r for r in links_db if r.get('status') != 'dead']

    with open(CONFIG['DB_FILE'], 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=links_db[0].keys()); writer.writeheader(); writer.writerows(links_db)

    with open(CONFIG['TASK_LIST_FILE'], 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=['id', 'url', 'type']); writer.writeheader()
        writer.writerows([{'id': t['id'], 'url': t['url'], 'type': t.get('type', 'auto')} for t in tasks])

    print(f"Curator: Task list generated with {len(tasks)} links.")

if __name__ == "__main__":
    main()

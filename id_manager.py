# id_manager.py
import csv
from hashlib import md5
from config import CONFIG

def main():
    try:
        with open(CONFIG['DB_FILE'], 'r', newline='', encoding='utf-8') as f:
            rows = list(csv.DictReader(f))
    except FileNotFoundError:
        print(f"Database not found. Please create {CONFIG['DB_FILE']} with a 'url' header.")
        return

    header = rows[0].keys() if rows else ['url', 'id', 'type']
    if 'id' not in header: header.append('id')

    changed = False
    for row in rows:
        if 'id' not in row or not row['id']:
            if 'url' in row and row['url']:
                row['id'] = f"sub_{md5(row['url'].encode()).hexdigest()[:10]}"
                changed = True

    if changed:
        with open(CONFIG['DB_FILE'], 'w', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=header, extrasaction='ignore')
            writer.writeheader()
            writer.writerows(rows)
        print("ID Manager: New IDs assigned and database updated.")
    else:
        print("ID Manager: No new links found, no changes made.")

if __name__ == "__main__":
    main()

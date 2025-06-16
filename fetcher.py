# fetcher.py
import os, csv, asyncio, aiohttp, ssl
from config import CONFIG

# ... (粘贴上一版回复中的 fetch_content 函数) ...

async def main():
    if not os.path.exists(CONFIG['CACHE_DIR']): os.makedirs(CONFIG['CACHE_DIR'])
    for f in os.listdir(CONFIG['CACHE_DIR']): os.remove(os.path.join(CONFIG['CACHE_DIR'], f))

    with open(CONFIG['TASK_LIST_FILE'], 'r', newline='', encoding='utf-8') as f:
        tasks = list(csv.DictReader(f))

    fetch_results = []
    async with aiohttp.ClientSession() as session:
        aio_tasks = [fetch_content(session, t['url'], t['type']) for t in tasks]
        contents = await asyncio.gather(*aio_tasks)

    for i, content in enumerate(contents):
        task = tasks[i]
        if content:
            cache_path = os.path.join(CONFIG['CACHE_DIR'], f"{task['id']}.txt")
            with open(cache_path, 'w', encoding='utf-8') as f:
                f.write(content)
            fetch_results.append({'id': task['id'], 'cache_path': cache_path, 'status': 'success'})

    with open(CONFIG['FETCH_RESULTS_FILE'], 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=['id', 'cache_path', 'status']); writer.writeheader(); writer.writerows(fetch_results)

    print(f"Fetcher: Cached {len(fetch_results)} contents.")

# ...

import sys
import os
import time
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from curl_cffi import requests

# Add project root to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../..')))

from resources.utils.organize_sources import clean_sources # Ensure sources are clean first

def check_source(url):
    """
    Fetches a single source and returns metadata:
    (url, status, proxy_count, error_msg)
    """
    regex = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}:\d{2,5}\b")
    try:
        resp = requests.get(url, timeout=10, impersonate="chrome120")
        if resp.status_code == 200:
            matches = regex.findall(resp.text)
            count = len(set(matches)) # Unique proxies
            return (url, "OK", count, None)
        else:
            return (url, f"HTTP {resp.status_code}", 0, None)
    except Exception as e:
        return (url, "Error", 0, str(e))

def run_health_check():
    source_file = "resources/sources.txt"
    if not os.path.exists(source_file):
        print(f"Source file not found at {source_file}")
        return

    with open(source_file, "r", encoding="utf-8") as f:
        urls = [line.strip() for line in f if line.strip() and not line.startswith("#") and "http" in line]

    print(f"Checking {len(urls)} sources...")
    
    results = []
    start_time = time.time()
    
    with ThreadPoolExecutor(max_workers=50) as ex:
        futures = {ex.submit(check_source, url): url for url in urls}
        
        completed = 0
        total = len(urls)
        
        for f in as_completed(futures):
            res = f.result()
            results.append(res)
            completed += 1
            if completed % 50 == 0:
                print(f"Progress: {completed}/{total}")

    duration = time.time() - start_time
    
    # Analysis
    working = [r for r in results if r[2] > 0]
    dead = [r for r in results if r[2] == 0]
    
    total_proxies_found = sum(r[2] for r in working)
    
    print("\n" + "="*40)
    print(f"SOURCE HEALTH REPORT (Time: {duration:.2f}s)")
    print("="*40)
    print(f"Total Sources: {len(urls)}")
    print(f"Working Sources: {len(working)} ({len(working)/len(urls)*100:.1f}%)")
    print(f"Dead/Empty Sources: {len(dead)}")
    print(f"Total Unique Proxies Found (Sum of lists): {total_proxies_found}")
    
    print("\nTOP 10 SOURCES:")
    working.sort(key=lambda x: x[2], reverse=True)
    for r in working[:10]:
        print(f"[{r[2]} proxies] {r[0]}")

    # Optional: Save clean list
    # print("\nWriting 'resources/sources_verified.txt' with only working sources...")
    # with open("resources/sources_verified.txt", "w") as f:
    #     for r in working:
    #         f.write(r[0] + "\n")

if __name__ == "__main__":
    run_health_check()

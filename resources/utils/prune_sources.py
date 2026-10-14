import sys
import os
import time
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from curl_cffi import requests

# Add project root to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../..')))

def check_source(url):
    """
    Fetches a single source and returns metadata:
    (url, status, proxy_count)
    """
    regex = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}:\d{2,5}\b")
    try:
        # 15s timeout for stricter verification
        resp = requests.get(url, timeout=15, impersonate="chrome120")
        if resp.status_code == 200:
            matches = regex.findall(resp.text)
            count = len(set(matches))
            return (url, "OK", count)
        else:
            return (url, f"HTTP {resp.status_code}", 0)
    except Exception as e:
        return (url, "Error", 0)

def prune_sources():
    source_file = "resources/sources.txt"
    dead_file = "resources/dead_sources.txt"
    
    if not os.path.exists(source_file):
        print(f"Source file not found at {source_file}")
        return

    with open(source_file, "r", encoding="utf-8") as f:
        # Read lines, keeping comments for context if we wanted, 
        # but we are rewriting based on health.
        # We will strip comments for the new file to keep it clean.
        raw_urls = [line.strip() for line in f if line.strip() and not line.startswith("#") and "http" in line]

    print(f"Verifying {len(raw_urls)} sources...")
    
    working = []
    dead = []
    
    start_time = time.time()
    
    with ThreadPoolExecutor(max_workers=50) as ex:
        futures = {ex.submit(check_source, url): url for url in raw_urls}
        
        completed = 0
        total = len(raw_urls)
        
        for f in as_completed(futures):
            res = f.result()
            url, status, count = res
            
            if count > 0:
                working.append(url)
            else:
                dead.append(url)
                
            completed += 1
            if completed % 100 == 0:
                print(f"Verified: {completed}/{total}")

    duration = time.time() - start_time
    
    # Sort for neatness
    working.sort()
    dead.sort()
    
    print(f"\nVerification Complete in {duration:.2f}s")
    print(f"Working: {len(working)}")
    print(f"Dead/Empty: {len(dead)}")
    
    # Write Working
    with open(source_file, "w", encoding="utf-8") as f:
        f.write("# --- Active Sources (Verified) ---\n")
        f.write("\n".join(working))
    
    # Write Dead
    with open(dead_file, "w", encoding="utf-8") as f:
        f.write("# --- Dead/Empty Sources (Verified) ---\n")
        f.write("\n".join(dead))
        
    print(f"Updated '{source_file}' and created '{dead_file}'.")

if __name__ == "__main__":
    prune_sources()

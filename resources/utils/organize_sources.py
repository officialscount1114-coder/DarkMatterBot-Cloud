import os

def validate_url(url):
    """Checks if a URL is valid."""
    url = url.strip()
    if " " in url: return None
    if not (url.startswith("http://") or url.startswith("https://")):
        # Attempt to fix
        if "." in url and not url.startswith("#"):
             return "http://" + url
        return None
    if "." not in url: return None
    return url

def clean_sources(filename="resources/sources.txt"):
    # Fallback path logic
    target_file = filename
    if not os.path.exists(target_file):
        if os.path.exists("../sources.txt"):
             target_file = "../sources.txt"
        else:
             print("File not found.")
             return

    with open(target_file, "r", encoding="utf-8") as f:
        lines = f.readlines()

    cat1 = set()
    cat2 = set()
    gsa = set()
    
    current_section = None

    for line in lines:
        line = line.strip()
        if not line: continue
        
        # Headers detection
        if "Category 1" in line:
            current_section = "cat1"
            continue
        if "Category 2" in line:
            current_section = "cat2"
            continue
        if "GSA Sources" in line:
            current_section = "gsa"
            continue
            
        if line.startswith("#"): continue

        # Validation
        valid_url = validate_url(line)
        if not valid_url: continue

        if current_section == "cat1":
            cat1.add(valid_url)
        elif current_section == "cat2":
            cat2.add(valid_url)
        elif current_section == "gsa":
            gsa.add(valid_url)
        else:
            # Default to cat1 or gsa depending on content? 
            # If it looks like a raw list, put in GSA.
            # If it's a github raw link, put in cat1.
            if "githubusercontent" in valid_url or "raw" in valid_url:
                cat1.add(valid_url)
            else:
                gsa.add(valid_url)

    # Convert to sorted lists
    sorted_cat1 = sorted(list(cat1))
    sorted_cat2 = sorted(list(cat2))
    sorted_gsa = sorted(list(gsa))

    # Reconstruct
    output = []
    
    output.append("# --- Category 1: Raw Text Sources (GitHub & Static) ---")
    output.extend(sorted_cat1)
    output.append("")
    
    output.append("# --- Category 2: Dynamic APIs (Text & JSON) ---")
    output.extend(sorted_cat2)
    output.append("")
    
    output.append("##ALL GSA Sources##")
    output.extend(sorted_gsa)
    
    with open(target_file, "w", encoding="utf-8") as f:
        f.write("\n".join(output))
    
    print(f"Validated and Cleaned sources. Total: {len(sorted_cat1) + len(sorted_cat2) + len(sorted_gsa)}")

if __name__ == "__main__":
    clean_sources()

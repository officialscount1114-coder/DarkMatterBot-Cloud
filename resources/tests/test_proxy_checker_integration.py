import unittest
import sys
import os

# Add project root to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../..')))

from core.models import ProxyConfig

class TestProxyParsing(unittest.TestCase):
    def test_main_logic_simulation(self):
        proxies = [
            "HTTP://1.1.1.1:80",   # Uppercase
            "https://2.2.2.2:443", # HTTPS scheme
            "3.3.3.3:1080"         # Ambiguous
        ]
        
        allowed_protos = ["http", "socks4", "socks5"]
        check_configs = []
        
        print(f"\nAllowed: {allowed_protos}")
        
        for p_str in proxies:
            try:
                if "://" in p_str:
                    parts = p_str.split("://")
                    scheme = parts[0].lower() # Fixed
                    addr = parts[1]
                    
                    # Fixed: Treat https as http
                    check_scheme = "http" if scheme == "https" else scheme
                    
                    if check_scheme not in allowed_protos: 
                        print(f"Skipped {p_str} (Scheme '{scheme}' not allowed)")
                        continue
                else:
                    addr = p_str
                    scheme = "ambiguous"

                if ":" in addr:
                    host, port = addr.split(":")[:2]
                    port = int(port)
                    
                    if scheme == "ambiguous":
                        for proto in allowed_protos:
                            check_configs.append(ProxyConfig(host=host, port=port, protocol=proto))
                    else:
                        protocol = "http" if scheme == "https" else scheme
                        check_configs.append(ProxyConfig(host=host, port=port, protocol=protocol))
            except Exception as e:
                print(e)
                pass

        print(f"Generated {len(check_configs)} configs.")
        
        # We expect:
        # 1. HTTP://1.1.1.1 -> Accepted as http (normalized)
        # 2. https://2.2.2.2 -> Accepted as http (normalized)
        # 3. 3.3.3.3 -> Accepted as 3 configs (http, s4, s5)
        # Total expected: 1 + 1 + 3 = 5
        
        # But with current bugs, we likely get 3 (only the ambiguous one).
        self.assertEqual(len(check_configs), 5, f"Expected 5 configs, got {len(check_configs)}")

if __name__ == "__main__":
    unittest.main()

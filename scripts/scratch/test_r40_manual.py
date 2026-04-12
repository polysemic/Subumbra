import hmac
import hashlib
import time
import secrets
import httpx

hmac_key = "a6615c5ea1b834343e22baea1b84c9afdb8e3e591773cb531bcf67c5b9d848b5".encode()
token = "031328a3a17eb4a55251e5f18d7fed92e7880544cda27d63a06f80d6d4d15fa8"
url = "http://localhost:9090/keys/anthropic_prod"

def get_headers(nonce=None, ts=None):
    if ts is None: ts = str(int(time.time()))
    if nonce is None: nonce = secrets.token_hex(16)
    sig = hmac.new(hmac_key, f"anthropic_prod:{ts}:{nonce}".encode(), hashlib.sha256).hexdigest()
    return {
        "X-Forge-Token": token,
        "X-Forge-Timestamp": ts,
        "X-Forge-Nonce": nonce,
        "X-Forge-Signature": sig
    }

print("Running manual verification checks...")

# 1. Normal fetch
resp1 = httpx.get(url, headers=get_headers())
print(f"1. Normal Fetch: {resp1.status_code}")
if resp1.status_code != 200:
    print(f"   Response: {resp1.text}")

# 2. Replay
headers2 = get_headers()
resp2_1 = httpx.get(url, headers=headers2)
resp2_2 = httpx.get(url, headers=headers2)
print(f"2. First Fetch: {resp2_1.status_code}, Replay: {resp2_2.status_code}")

# 3. Missing nonce
h_no_nonce = get_headers()
del h_no_nonce["X-Forge-Nonce"]
resp3 = httpx.get(url, headers=h_no_nonce)
print(f"3. Missing Nonce: {resp3.status_code} (Expected 400)")

# 4. Long Nonce (65 chars)
long_nonce = "a" * 65
# We sign with the long nonce, then send it.
resp4 = httpx.get(url, headers=get_headers(nonce=long_nonce))
print(f"4. Long Nonce: {resp4.status_code} (Expected 400)")

# 5. Nonce Store failure (Simulated by sending request while DB is locked or unavailable)
# This is harder to simulate manually without changing code, but I'll skip it for now and trust official verification.

import hmac
import hashlib
import time
import secrets
import urllib.request
import urllib.error

hmac_key = b"a6615c5ea1b834343e22baea1b84c9afdb8e3e591773cb531bcf67c5b9d848b5"
token = "031328a3a17eb4a55251e5f18d7fed92e7880544cda27d63a06f80d6d4d15fa8"
url = "http://localhost:9090/keys/anthropic_prod"

def req_with_headers(nonce=None, ts=None):
    if ts is None: ts = str(int(time.time()))
    if nonce is None: nonce = secrets.token_hex(16)
    sig = hmac.new(hmac_key, f"anthropic_prod:{ts}:{nonce}".encode(), hashlib.sha256).hexdigest()
    headers = {
        "X-Forge-Token": token,
        "X-Forge-Timestamp": ts,
        "X-Forge-Nonce": nonce,
        "X-Forge-Signature": sig
    }
    return urllib.request.Request(url, headers=headers)

print("Running manual verification checks...")

# 1. Normal fetch
try:
    resp1 = urllib.request.urlopen(req_with_headers())
    print(f"1. Normal Fetch: {resp1.status}")
except urllib.error.HTTPError as e:
    print(f"1. Normal Fetch: {e.code}")

# 2. Replay
req2 = req_with_headers()
try:
    resp2_1 = urllib.request.urlopen(req2)
    s2_1 = resp2_1.status
except urllib.error.HTTPError as e:
    s2_1 = e.code

try:
    resp2_2 = urllib.request.urlopen(req2)
    s2_2 = resp2_2.status
except urllib.error.HTTPError as e:
    s2_2 = e.code

print(f"2. First Fetch: {s2_1}, Replay: {s2_2} (Expected Replay to be 401)")

# 3. Missing nonce
h_no_nonce = {
    "X-Forge-Token": token,
    "X-Forge-Timestamp": str(int(time.time())),
    "X-Forge-Signature": "dummy"
}
req3 = urllib.request.Request(url, headers=h_no_nonce)
try:
    resp3 = urllib.request.urlopen(req3)
    s3 = resp3.status
except urllib.error.HTTPError as e:
    s3 = e.code
print(f"3. Missing Nonce: {s3} (Expected 400)")

# 4. Long Nonce
try:
    resp4 = urllib.request.urlopen(req_with_headers(nonce="a" * 65))
    s4 = resp4.status
except urllib.error.HTTPError as e:
    s4 = e.code
print(f"4. Long Nonce: {s4} (Expected 400)")

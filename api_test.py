

import time
import hmac
import hashlib
import requests

API_KEY =    g6ve6R3mjNT3JiakEEdE2GptLO8SCLmRxGF51HXg08m7v7KLEX8R2d00FWWeV6Zr
SECRET_KEY = qzW8q1R9YSHKHsfMO4Ay6IRvSdRg2U4hZufNXHKQdCw4cVpTcAGKPp1Dc8AshxYU

BASE_URL = "https://fapi.binance.com"

headers = {
    "X-MBX-APIKEY": API_KEY
}

def signed_request(endpoint):
    timestamp = int(time.time() * 1000)
    query = f"timestamp={timestamp}"

    signature = hmac.new(
        SECRET_KEY.encode(),
        query.encode(),
        hashlib.sha256
    ).hexdigest()

    url = f"{BASE_URL}{endpoint}?{query}&signature={signature}"
    return requests.get(url, headers=headers).json()

# TEST: Futures account info
response = signed_request("/fapi/v2/account")
print(response)
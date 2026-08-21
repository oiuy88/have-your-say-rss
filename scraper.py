import json
import requests

URL = "https://ec.europa.eu/info/law/better-regulation/brpapi/searchInitiatives"

params = {
    "size": 10,
    "language": "EN",
}

headers = {
    "Accept": "application/json",
    "User-Agent": "Mozilla/5.0",
}

print("Requesting Have Your Say API...")
print(URL)
print(params)

response = requests.get(
    URL,
    params=params,
    headers=headers,
    timeout=60,
)

print()
print("STATUS:", response.status_code)
print()

print("URL ACTUALLY REQUESTED:")
print(response.url)
print()

print("RESPONSE:")
print(response.text[:20000])

response.raise_for_status()

data = response.json()

print()
print("JSON TYPE:")
print(type(data))

print()
print("TOP-LEVEL KEYS:")

if isinstance(data, dict):
    print(list(data.keys()))

print()
print("FIRST 10 ITEMS:")

if isinstance(data, list):
    print(json.dumps(
        data[:10],
        indent=2,
        ensure_ascii=False
    ))

elif isinstance(data, dict):

    for key in (
        "content",
        "initiatives",
        "results",
        "items",
    ):

        if isinstance(data.get(key), list):

            print(json.dumps(
                data[key][:10],
                indent=2,
                ensure_ascii=False
            ))

            break

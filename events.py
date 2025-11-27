import requests

url = "https://gamma-api.polymarket.com/events"
params = {}

params['limit'] = 5
params['order'] = "createdAt"
params['ascending'] = "false"
# params['offset']=10
# params['tag_id']="102127"
params['slug']=["tiktok-sale-announced-in-2025"]
response = requests.get(url, params=params)

events = response.json()

for event in events:
    print(f"Event ID: {event['id']}")
    print(f"Title: {event['title']}")
    print(f"Created At: {event['createdAt']}")
    print(f"Updated At: {event['updatedAt']}")
    print(f"Slug: {event['slug']}")
    print(f"Tags: {event['tags']}")
    for market in event['markets']:
        print(f"  Market ID: {market['id']}")
        print(f"  Question: {market['question']}")
        print(f"  Created At: {market['createdAt']}")
        print(f"  Updated At: {market['updatedAt']}")
        print(f"  Slug: {market['slug']}")
        print(f"  Outcomes: {market['outcomes']}")
        print(f"  Outcome Prices: {market['outcomePrices']}")
        print(f"  CLOB Token IDs: {market['clobTokenIds']}")
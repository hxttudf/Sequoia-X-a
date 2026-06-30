#!/usr/bin/env python3
"""Minimal Scweet test: authenticate and fetch 1 tweet from @dylan522p."""
import os

# Clear proxy env vars before any network calls by Scweet
for var in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy"):
    os.environ.pop(var, None)

from Scweet import Scweet

AUTH_TOKEN = "3e9046d1e317e08523a037b8c78f0a5013413057"

print("Initializing Scweet client...")
s = Scweet(auth_token=AUTH_TOKEN, manifest_scrape_on_init=True)

print("Fetching 1 tweet from @dylan522p...")
tweets = s.get_profile_tweets(["dylan522p"], limit=1, save=False)

print(f"Got {len(tweets)} tweet(s)")
for i, t in enumerate(tweets):
    print(f"\n--- Tweet {i+1} ---")
    print(f"ID: {t.get('tweet_id')}")
    user_info = t.get('user', {})
    print(f"User: {user_info.get('screen_name') if isinstance(user_info, dict) else user_info}")
    print(f"Text: {t.get('text', '')[:200]}")
    print(f"Likes: {t.get('likes')}, Retweets: {t.get('retweets')}")
    print(f"Timestamp: {t.get('timestamp')}")

print("\nScweet test completed successfully!")

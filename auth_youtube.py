"""
One-time helper to authorize the YouTube Analytics API and print a refresh token.

Real audience-retention (average-view-percentage) comes from the YouTube
*Analytics* API, which — unlike the Data API key — requires an OAuth token owned
by the channel. This runs Google's device flow so you can authorize from any
browser (no local redirect / server needed), then prints the refresh token to
paste into your .env as YOUTUBE_OAUTH_REFRESH_TOKEN.

Setup (once):
  1. In Google Cloud Console → Credentials, create an OAuth client of type
     "TVs and Limited Input devices". Note its client id + secret.
  2. export YOUTUBE_OAUTH_CLIENT_ID=... YOUTUBE_OAUTH_CLIENT_SECRET=...
  3. python auth_youtube.py   → follow the on-screen URL + code.
  4. Paste the printed refresh token into .env.
"""

import os
import sys
import time

import requests

SCOPE = "https://www.googleapis.com/auth/yt-analytics.readonly"
DEVICE_CODE_URL = "https://oauth2.googleapis.com/device/code"
TOKEN_URL = "https://oauth2.googleapis.com/token"


def main():
    cid = os.environ.get("YOUTUBE_OAUTH_CLIENT_ID")
    secret = os.environ.get("YOUTUBE_OAUTH_CLIENT_SECRET")
    if not (cid and secret):
        sys.exit("Set YOUTUBE_OAUTH_CLIENT_ID and YOUTUBE_OAUTH_CLIENT_SECRET first "
                 "(OAuth client type: 'TVs and Limited Input devices').")

    r = requests.post(DEVICE_CODE_URL, data={"client_id": cid, "scope": SCOPE}, timeout=20)
    r.raise_for_status()
    d = r.json()
    print("\n  1. Open:  " + d["verification_url"])
    print("  2. Enter code:  " + d["user_code"] + "\n")
    print("Waiting for you to authorize…")

    interval = d.get("interval", 5)
    deadline = time.time() + d.get("expires_in", 600)
    while time.time() < deadline:
        time.sleep(interval)
        t = requests.post(TOKEN_URL, data={
            "client_id": cid, "client_secret": secret,
            "device_code": d["device_code"],
            "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
        }, timeout=20).json()
        if "refresh_token" in t:
            print("\n✓ Authorized. Add this line to your .env:\n")
            print("YOUTUBE_OAUTH_REFRESH_TOKEN=" + t["refresh_token"] + "\n")
            return
        if t.get("error") in ("authorization_pending", "slow_down"):
            if t["error"] == "slow_down":
                interval += 5
            continue
        sys.exit("Authorization failed: " + t.get("error_description", t.get("error", "unknown")))
    sys.exit("Timed out waiting for authorization.")


if __name__ == "__main__":
    main()

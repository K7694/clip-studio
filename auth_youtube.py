"""One-time YouTube login for the clips channel.

1. Put the OAuth "Desktop app" JSON from Google Cloud next to this file as client_secrets.json
2. python auth_youtube.py            (opens a browser; sign in with the NEW channel's Gmail)
   python auth_youtube.py --manual   (no browser here: open the printed link on your phone,
                                      then paste back the address you land on)
3. Copy the contents of youtube_token.json into the GitHub secret YOUTUBE_TOKEN_JSON
   (repo -> Settings -> Secrets and variables -> Actions -> New repository secret).
   Then delete both local files.
"""
import sys

from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = ["https://www.googleapis.com/auth/youtube.upload",
          "https://www.googleapis.com/auth/youtube.readonly"]

flow = InstalledAppFlow.from_client_secrets_file("client_secrets.json", SCOPES)
if "--manual" in sys.argv:
    flow.redirect_uri = "http://localhost:8090/"
    url, _ = flow.authorization_url(access_type="offline", prompt="consent")
    print("\nOpen this link, sign in with the clips channel's Gmail, allow access:\n\n" + url)
    print("\nYour browser will then show an error page at localhost — that's expected.")
    back = input("Paste the FULL address from the browser bar here: ").strip()
    flow.fetch_token(authorization_response=back.replace("http://", "https://", 1))
    creds = flow.credentials
else:
    creds = flow.run_local_server(port=8090, access_type="offline", prompt="consent")

with open("youtube_token.json", "w") as f:
    f.write(creds.to_json())
print("\n✅ youtube_token.json created — copy its contents into the YOUTUBE_TOKEN_JSON secret.")

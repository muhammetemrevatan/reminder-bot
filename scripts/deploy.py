"""Forward the push webhook only after the CI test job succeeds."""
import os
import json
import sys
import subprocess
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen


def main():
    url = os.getenv("DOKPLOY_DEPLOY_URL")
    if not url:
        print("::notice::Set DOKPLOY_DEPLOY_URL and disable the direct GitHub webhook to enable test-gated deployment.")
        return 0
    parts = urlsplit(url)
    if parts.scheme != "https" or not parts.netloc or not parts.path.startswith("/api/deploy/"):
        print("::error::DOKPLOY_DEPLOY_URL must be an HTTPS Dokploy deployment webhook URL.")
        return 1
    try:
        latest = subprocess.run(
            ["git", "ls-remote", "origin", "refs/heads/main"],
            capture_output=True, text=True, check=True, timeout=30,
        ).stdout.split()[0]
    except (subprocess.SubprocessError, IndexError):
        print("::error::Could not verify the current main commit before deployment.")
        return 1
    if latest != os.environ.get("GITHUB_SHA"):
        print("::notice::A newer push exists; skipping deployment of this older test run.")
        return 0
    request = Request(url, data=Path(os.environ["GITHUB_EVENT_PATH"]).read_bytes(), headers={
        "Content-Type": "application/json", "X-GitHub-Event": "push",
    }, method="POST")
    try:
        with urlopen(request, timeout=60) as response:
            body = response.read(65536).decode("utf-8", errors="replace")
            if response.status < 200 or response.status >= 300 or "text/html" in response.headers.get("Content-Type", "").lower() or "cloudflare access" in body.lower():
                print("::error::The deployment webhook did not return a valid success response. Check Cloudflare Access and Dokploy.")
                return 1
            try:
                accepted = json.loads(body).get("message") == "Application deployed successfully"
            except (ValueError, AttributeError):
                accepted = False
            if not accepted:
                print("::error::Dokploy did not confirm that the deployment was queued.")
                return 1
    except (HTTPError, URLError, TimeoutError):
        # Do not print exceptions: they may contain the secret webhook URL.
        print("::error::The deployment webhook request failed.")
        return 1
    print("Deployment request accepted. Check Dokploy for build and runtime status.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

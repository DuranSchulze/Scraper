"""Google API clients through a service account with domain-wide delegation.

Every scope is read-only. Each client asks only for the scopes its API needs,
so the admin must authorize exactly the scopes listed in README.md.
"""

import random
import time

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from .config import env

SCOPES = {
    "gmail": ["https://www.googleapis.com/auth/gmail.readonly"],
    "people": [
        "https://www.googleapis.com/auth/contacts.readonly",
        "https://www.googleapis.com/auth/contacts.other.readonly",
    ],
    "admin": ["https://www.googleapis.com/auth/admin.directory.user.readonly"],
    "sheets": ["https://www.googleapis.com/auth/spreadsheets.readonly"],
}
APIS = {"gmail": ("gmail", "v1"), "people": ("people", "v1"),
        "admin": ("admin", "directory_v1"), "sheets": ("sheets", "v4")}
RETRY_STATUS = {429, 500, 502, 503, 504}


def service(kind: str, subject: str):
    """API client acting as `subject` (a user in the workspace)."""
    key_file = env("GOOGLE_SERVICE_ACCOUNT_FILE")
    if not key_file:
        raise RuntimeError("GOOGLE_SERVICE_ACCOUNT_FILE is not set in .env")
    creds = service_account.Credentials.from_service_account_file(
        key_file, scopes=SCOPES[kind], subject=subject
    )
    name, version = APIS[kind]
    return build(name, version, credentials=creds, cache_discovery=False)


def execute(request, tries: int = 6, *, on_rate_limit=None):
    """Run a request, backing off on rate limits and server errors."""
    for attempt in range(tries):
        try:
            return request.execute()
        except HttpError as e:
            rate_limited = e.resp.status == 403 and "ateLimitExceeded" in str(e)
            if not (e.resp.status in RETRY_STATUS or rate_limited) or attempt == tries - 1:
                raise
            if on_rate_limit and (e.resp.status == 429 or rate_limited):
                on_rate_limit()
            time.sleep(min(60, 2**attempt + random.random()))
        except OSError:  # ConnectionResetError, TimeoutError, ssl.SSLError: transient network drops
            if attempt == tries - 1:
                raise
            time.sleep(min(60, 2**attempt + random.random()))


def pages(make_request, items_key: str, *, on_rate_limit=None):
    """Yield items from every page of a paged list call. make_request(token) builds the request."""
    token = None
    while True:
        resp = execute(make_request(token), on_rate_limit=on_rate_limit)
        yield from resp.get(items_key, [])
        token = resp.get("nextPageToken")
        if not token:
            return

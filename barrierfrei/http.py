"""A shared requests session with timeouts and automatic retries."""

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

DEFAULT_TIMEOUT = 30


def make_session() -> requests.Session:
    # Retry rate limits and server errors with exponential backoff (1s, 2s, 4s, ...),
    # honouring the server's Retry-After header when it sends one.
    retry = Retry(
        total=5,
        backoff_factor=1,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=("GET",),
        respect_retry_after_header=True,
    )
    session = requests.Session()
    session.mount("https://", HTTPAdapter(max_retries=retry))
    return session

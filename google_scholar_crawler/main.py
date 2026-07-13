import json
import os
import signal
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from scholarly import ProxyGenerator, scholarly


DEFAULT_GOOGLE_SCHOLAR_ID = "mJhOACUAAAAJ"
MAX_ATTEMPTS = 3
RETRY_DELAYS_SECONDS = (60, 120)
FETCH_TIMEOUT_SECONDS = int(os.environ.get("SCHOLAR_FETCH_TIMEOUT_SECONDS", "180"))


class ScholarFetchTimeout(TimeoutError):
    pass


@contextmanager
def fetch_timeout(seconds: int):
    """Bound Scholar requests so a blocked run does not burn the whole workflow."""
    if not hasattr(signal, "SIGALRM"):
        yield
        return

    def handle_timeout(signum, frame):
        raise ScholarFetchTimeout(f"Scholar request timed out after {seconds} seconds")

    previous_handler = signal.signal(signal.SIGALRM, handle_timeout)
    signal.alarm(seconds)
    try:
        yield
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, previous_handler)


def configure_proxy():
    scraper_api_key = os.environ.get("SCRAPER_API_KEY", "").strip()
    if scraper_api_key:
        proxy = ProxyGenerator()
        proxy.ScraperAPI(scraper_api_key)
        scholarly.use_proxy(proxy)
        print("Using ScraperAPI proxy for Google Scholar requests.", flush=True)
        return

    if os.environ.get("SCHOLARLY_USE_FREE_PROXIES", "").lower() in {"1", "true", "yes"}:
        proxy = ProxyGenerator()
        if proxy.FreeProxies(timeout=1, wait_time=30):
            scholarly.use_proxy(proxy)
            print("Using free proxy rotation for Google Scholar requests.", flush=True)
        else:
            print("Free proxy rotation was requested but no proxy was configured.", flush=True)


def normalize_scholar_id(raw_value: str | None) -> str:
    value = (raw_value or DEFAULT_GOOGLE_SCHOLAR_ID).strip()
    if "://" in value:
        parsed = urlparse(value)
        value = parse_qs(parsed.query).get("user", [value])[0]
    return value.split("&", 1)[0].strip()


def fetch_author(scholar_id: str) -> dict:
    last_error = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            with fetch_timeout(FETCH_TIMEOUT_SECONDS):
                author = scholarly.search_author_id(scholar_id)
                scholarly.fill(author, sections=["basics", "indices", "counts", "publications"])
            return author
        except Exception as error:
            last_error = error
            if attempt == MAX_ATTEMPTS:
                break
            delay = RETRY_DELAYS_SECONDS[attempt - 1]
            print(
                f"Scholar fetch attempt {attempt}/{MAX_ATTEMPTS} failed: {error}. "
                f"Retrying in {delay} seconds...",
                flush=True,
            )
            time.sleep(delay)
    raise RuntimeError(f"Google Scholar fetch failed after {MAX_ATTEMPTS} attempts") from last_error


def validate_citations(current: int, previous_file: str | None) -> None:
    if not isinstance(current, int) or current < 0:
        raise ValueError(f"Invalid citation count returned by Scholar: {current!r}")

    if not previous_file:
        return

    path = Path(previous_file)
    if not path.is_file():
        print("Previous citation data is unavailable; skipping decrease check.", flush=True)
        return

    try:
        previous = json.loads(path.read_text(encoding="utf-8")).get("citedby")
    except (OSError, json.JSONDecodeError) as error:
        print(f"Could not read previous citation data: {error}", flush=True)
        return

    if not isinstance(previous, int) or previous < 0:
        print("Previous citation count is invalid; skipping decrease check.", flush=True)
        return

    allowed_drop = max(10, round(previous * 0.10))
    if current < previous - allowed_drop:
        raise ValueError(
            f"Citation count dropped unexpectedly from {previous} to {current}; "
            "keeping the previously published data."
        )


configure_proxy()

scholar_id = normalize_scholar_id(os.environ.get("GOOGLE_SCHOLAR_ID"))
if not scholar_id:
    raise ValueError("GOOGLE_SCHOLAR_ID is empty")

author = fetch_author(scholar_id)
citations = author.get("citedby")
validate_citations(citations, os.environ.get("PREVIOUS_STATS_FILE"))

author["updated"] = datetime.now(timezone.utc).isoformat()
author["publications"] = {
    publication["author_pub_id"]: publication
    for publication in author.get("publications", [])
    if publication.get("author_pub_id")
}
print(json.dumps(author, indent=2, ensure_ascii=False))

results_dir = Path("results")
results_dir.mkdir(exist_ok=True)
(results_dir / "gs_data.json").write_text(
    json.dumps(author, ensure_ascii=False), encoding="utf-8"
)

shieldio_data = {
    "schemaVersion": 1,
    "label": "citations",
    "message": str(citations),
}
(results_dir / "gs_data_shieldsio.json").write_text(
    json.dumps(shieldio_data, ensure_ascii=False), encoding="utf-8"
)

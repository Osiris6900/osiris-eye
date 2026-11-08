"""Validate source provenance of offline market events."""
from datetime import datetime, timezone

def validate_events(events):
    if not isinstance(events, list):
        raise ValueError("events must be a list")
    ids = set()
    for event in events:
        if not isinstance(event, dict):
            raise ValueError("invalid event")
        for field in ("id", "source", "source_url", "published_at"):
            if not isinstance(event.get(field), str) or not event[field].strip():
                raise ValueError("missing event provenance")
        if not event["source_url"].startswith("https://"):
            raise ValueError("event source must use HTTPS")
        if event["id"] in ids:
            raise ValueError("duplicate event id")
        ids.add(event["id"])
        try:
            published = datetime.fromisoformat(event["published_at"].replace("Z", "+00:00"))
        except ValueError:
            raise ValueError("invalid published_at") from None
        if published.tzinfo is None or published.astimezone(timezone.utc) > datetime.now(timezone.utc):
            raise ValueError("naive or future event timestamp")
    return len(ids)

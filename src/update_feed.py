
#!/usr/bin/env python3

import hashlib
import html
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

import requests


API_BASE = "https://ec.europa.eu/info/law/better-regulation/brpapi"

SEARCH_URL = f"{API_BASE}/searchInitiatives"
DETAIL_URL = f"{API_BASE}/groupInitiatives/{{initiative_id}}"

LANGUAGE = "EN"
PAGE_SIZE = 100

STATE_FILE = Path("data/state.json")
FEED_FILE = Path("site/feed.xml")

# Keep the RSS feed bounded. The state database still remembers
# everything we've seen.
MAX_FEED_ITEMS = 500

USER_AGENT = (
    "have-your-say-rss/1.0 "
    "(GitHub Actions; monitoring European Commission Have Your Say API)"
)

session = requests.Session()
session.headers.update({
    "User-Agent": USER_AGENT,
    "Accept": "application/json",
})


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def load_state():
    if not STATE_FILE.exists():
        return {
            "version": 1,
            "initiatives": {},
            "events": [],
        }

    with STATE_FILE.open("r", encoding="utf-8") as f:
        return json.load(f)


def save_state(state):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)

    tmp = STATE_FILE.with_suffix(".tmp")

    with tmp.open("w", encoding="utf-8") as f:
        json.dump(
            state,
            f,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )

    tmp.replace(STATE_FILE)


def get_json(url, params=None, retries=4):
    last_error = None

    for attempt in range(retries):
        try:
            response = session.get(
                url,
                params=params,
                timeout=60,
            )

            response.raise_for_status()
            return response.json()

        except Exception as exc:
            last_error = exc

            if attempt < retries - 1:
                time.sleep(2 ** attempt)

    raise RuntimeError(f"Unable to fetch {url}: {last_error}")


def canonical_json(obj):
    return json.dumps(
        obj,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def fingerprint(obj):
    payload = canonical_json(obj).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def clean_text(value):
    if value is None:
        return ""

    if isinstance(value, (dict, list)):
        value = json.dumps(value, ensure_ascii=False)

    value = str(value)

    value = re.sub(r"<[^>]+>", " ", value)
    value = re.sub(r"\s+", " ", value)

    return value.strip()


def first_value(obj, *names):
    if not isinstance(obj, dict):
        return None

    lowered = {
        str(k).lower(): v
        for k, v in obj.items()
    }

    for name in names:
        if name.lower() in lowered:
            return lowered[name.lower()]

    return None


def initiative_title(data):
    candidates = [
        "title",
        "initiativeTitle",
        "name",
        "label",
    ]

    value = first_value(data, *candidates)

    if value:
        return clean_text(value)

    # Some API responses may nest the initiative.
    for key in ("initiative", "groupInitiative", "data"):
        nested = data.get(key) if isinstance(data, dict) else None

        if isinstance(nested, dict):
            value = first_value(nested, *candidates)

            if value:
                return clean_text(value)

    return "Untitled initiative"


def initiative_url(initiative_id, data=None):
    # The public portal currently uses URLs of this general form.
    # The numeric ID is sufficient for navigation.
    return (
        "https://ec.europa.eu/info/law/better-regulation/"
        f"have-your-say/initiatives/{initiative_id}_en"
    )


def extract_items(value, path=""):
    """
    Recursively find dictionaries that look like publications/documents.
    This is deliberately generic because the API's response structure can
    evolve without requiring the whole monitor to be rewritten.
    """
    found = []

    if isinstance(value, dict):
        for key, child in value.items():

            key_lower = str(key).lower()

            if (
                "publication" in key_lower
                or key_lower in {
                    "documents",
                    "attachments",
                    "consultations",
                }
            ):
                if isinstance(child, list):
                    for item in child:
                        if isinstance(item, dict):
                            found.append(
                                (f"{path}/{key}", item)
                            )

                elif isinstance(child, dict):
                    found.append(
                        (f"{path}/{key}", child)
                    )

            found.extend(
                extract_items(
                    child,
                    f"{path}/{key}",
                )
            )

    elif isinstance(value, list):
        for index, child in enumerate(value):
            found.extend(
                extract_items(
                    child,
                    f"{path}/{index}",
                )
            )

    return found


def describe_change(old, new):
    """
    Produce a useful human-readable description rather than merely
    saying that the JSON hash changed.
    """

    changes = []

    if not old:
        return ["New initiative discovered"]

    old_title = initiative_title(old)
    new_title = initiative_title(new)

    if old_title != new_title:
        changes.append(
            f"Title changed: {old_title} → {new_title}"
        )

    # Look for common date/status/deadline fields.
    interesting = [
        "status",
        "state",
        "closingDate",
        "deadline",
        "endDate",
        "publicationDate",
        "modifiedDate",
        "lastModified",
    ]

    for field in interesting:
        old_value = first_value(old, field)
        new_value = first_value(new, field)

        if old_value != new_value and (
            old_value is not None or new_value is not None
        ):
            changes.append(
                f"{field}: "
                f"{clean_text(old_value)} → "
                f"{clean_text(new_value)}"
            )

    # Compare publications/documents by their canonical representation.
    old_nested = extract_items(old)
    new_nested = extract_items(new)

    old_fingerprints = {
        fingerprint(item)
        for _, item in old_nested
    }

    new_fingerprints = {
        fingerprint(item)
        for _, item in new_nested
    }

    added = len(new_fingerprints - old_fingerprints)
    removed = len(old_fingerprints - new_fingerprints)

    if added:
        changes.append(f"{added} publication/document item(s) added")

    if removed:
        changes.append(
            f"{removed} publication/document item(s) removed or changed"
        )

    if not changes:
        changes.append("Initiative data changed")

    return changes


def discover_initiatives():
    """
    Retrieve all initiatives using the paginated API.
    """

    initiatives = {}

    page = 0

    while True:
        payload = get_json(
            SEARCH_URL,
            params={
                "page": page,
                "size": PAGE_SIZE,
                "language": LANGUAGE,
            },
        )

        # The exact list property can evolve. Try common possibilities.
        records = None

        if isinstance(payload, list):
            records = payload

        elif isinstance(payload, dict):
            for key in (
                "content",
                "initiatives",
                "results",
                "items",
                "data",
            ):
                candidate = payload.get(key)

                if isinstance(candidate, list):
                    records = candidate
                    break

        if records is None:
            raise RuntimeError(
                "Could not find initiative records in API response"
            )

        for item in records:
            if not isinstance(item, dict):
                continue

            initiative_id = first_value(
                item,
                "id",
                "initiativeId",
                "groupInitiativeId",
            )

            if initiative_id is None:
                continue

            initiatives[str(initiative_id)] = item

        # Determine whether another page exists.
        total_pages = None

        if isinstance(payload, dict):
            total_pages = first_value(
                payload,
                "totalPages",
            )

        if total_pages is not None:
            try:
                if page + 1 >= int(total_pages):
                    break
            except (ValueError, TypeError):
                pass

        # Fallback: a short page means we're done.
        if len(records) < PAGE_SIZE:
            break

        page += 1

    return initiatives


def fetch_details(initiative_ids):
    detailed = {}

    for index, initiative_id in enumerate(initiative_ids, start=1):
        url = DETAIL_URL.format(
            initiative_id=quote(str(initiative_id))
        )

        try:
            detailed[str(initiative_id)] = get_json(url)

        except Exception as exc:
            print(
                f"WARNING: could not fetch initiative "
                f"{initiative_id}: {exc}",
                file=sys.stderr,
            )

        # Be polite to the Commission endpoint.
        if index < len(initiative_ids):
            time.sleep(0.1)

    return detailed


def make_event(
    initiative_id,
    title,
    changes,
    event_type,
):
    timestamp = now_iso()

    event_id = hashlib.sha256(
        (
            f"{initiative_id}|"
            f"{timestamp}|"
            f"{event_type}|"
            f"{'|'.join(changes)}"
        ).encode("utf-8")
    ).hexdigest()

    return {
        "id": event_id,
        "initiative_id": str(initiative_id),
        "title": title,
        "changes": changes,
        "event_type": event_type,
        "timestamp": timestamp,
        "url": initiative_url(initiative_id),
    }


def xml_escape(value):
    return html.escape(
        str(value),
        quote=True,
    )


def rfc822_date(iso_string):
    dt = datetime.fromisoformat(
        iso_string.replace("Z", "+00:00")
    )

    return dt.strftime(
        "%a, %d %b %Y %H:%M:%S +0000"
    )


def build_rss(events):
    generated = now_iso()

    items = []

    for event in events[:MAX_FEED_ITEMS]:
        description = (
            "<ul>"
            + "".join(
                f"<li>{xml_escape(change)}</li>"
                for change in event["changes"]
            )
            + "</ul>"
        )

        item = f"""
    <item>
      <title>{xml_escape(event["title"])} — {xml_escape(event["event_type"])}</title>
      <link>{xml_escape(event["url"])}</link>
      <guid isPermaLink="false">{xml_escape(event["id"])}</guid>
      <pubDate>{rfc822_date(event["timestamp"])}</pubDate>
      <description><![CDATA[{description}]]></description>
      <category>Have Your Say</category>
      <category>{xml_escape(event["event_type"])}</category>
    </item>
"""

        items.append(item)

    return f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>European Commission — Have Your Say Updates</title>
    <link>https://ec.europa.eu/info/law/better-regulation/have-your-say/initiatives_en</link>
    <description>Updates to European Commission Have Your Say initiatives.</description>
    <language>en</language>
    <lastBuildDate>{rfc822_date(generated)}</lastBuildDate>
    <ttl>15</ttl>
{''.join(items)}
  </channel>
</rss>
"""


def main():
    print("Discovering initiatives...")

    state = load_state()

    previous = state.get("initiatives", {})
    events = state.get("events", [])

    discovered = discover_initiatives()

    print(
        f"Discovered {len(discovered)} initiatives."
    )

    ids = list(discovered.keys())

    detailed = fetch_details(ids)

    changed_count = 0

    for initiative_id, data in detailed.items():

        new_hash = fingerprint(data)
        old_record = previous.get(initiative_id)

        old_hash = (
            old_record.get("hash")
            if isinstance(old_record, dict)
            else None
        )

        if old_hash == new_hash:
            continue

        old_data = (
            old_record.get("data")
            if isinstance(old_record, dict)
            else None
        )

        title = initiative_title(data)

        if old_data is None:
            event_type = "NEW"
        else:
            event_type = "UPDATED"

        changes = describe_change(
            old_data,
            data,
        )

        event = make_event(
            initiative_id,
            title,
            changes,
            event_type,
        )

        events.insert(0, event)

        previous[initiative_id] = {
            "hash": new_hash,
            "title": title,
            "data": data,
            "last_seen": now_iso(),
        }

        changed_count += 1

        print(
            f"{event_type}: {initiative_id} — {title}"
        )

    # Keep the historical state, but don't allow the committed file
    # to grow without bound.
    state["initiatives"] = previous
    state["events"] = events[:MAX_FEED_ITEMS]

    save_state(state)

    FEED_FILE.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    FEED_FILE.write_text(
        build_rss(state["events"]),
        encoding="utf-8",
    )

    print(
        f"Done. {changed_count} initiative(s) changed."
    )


if __name__ == "__main__":
    main()

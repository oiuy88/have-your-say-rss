import html
import xml.etree.ElementTree as ET

from datetime import datetime, timezone
from email.utils import format_datetime

import requests


# ============================================================
# CONFIG
# ============================================================

API_URL = (
    "https://ec.europa.eu/info/law/better-regulation/"
    "brpapi/searchInitiatives"
)

OUTPUT_FILE = "have-your-say.xml"

PAGE_SIZE = 100

LANGUAGE = "EN"

HEADERS = {
    "Accept": "application/json",
    "User-Agent": (
        "Mozilla/5.0 "
        "(compatible; HaveYourSay-RSS/1.0)"
    ),
}


# ============================================================
# API
# ============================================================

def get_initiatives(page=0):

    params = {
        "page": page,
        "size": PAGE_SIZE,
        "language": LANGUAGE,
    }

    print()
    print(
        f"Fetching page {page}..."
    )

    print(
        "GET:",
        API_URL
    )

    response = requests.get(
        API_URL,
        params=params,
        headers=HEADERS,
        timeout=60,
    )

    print(
        f"API status: {response.status_code}"
    )

    if response.status_code != 200:

        print()
        print("API response:")
        print(response.text)

        response.raise_for_status()

    return response.json()


# ============================================================
# FIND VALUES IN JSON
# ============================================================

def find_value(
    data,
    possible_names
):

    if not isinstance(data, dict):
        return None

    # Exact key matches first
    for name in possible_names:

        if name in data:

            value = data[name]

            if value is not None:
                return value

    # Case-insensitive key match
    lowered = {
        str(key).lower(): value
        for key, value in data.items()
    }

    for name in possible_names:

        value = lowered.get(
            name.lower()
        )

        if value is not None:
            return value

    return None


# ============================================================
# NORMALISE API RESPONSE
# ============================================================

def extract_items(data):

    if isinstance(data, list):
        return data

    if not isinstance(data, dict):
        return []

    # Common Spring-style pagination
    for key in (
        "content",
        "initiatives",
        "results",
        "items",
    ):

        value = data.get(key)

        if isinstance(value, list):
            return value

    return []


# ============================================================
# EXTRACT INITIATIVE
# ============================================================

def extract_initiative(item):

    if not isinstance(item, dict):
        return None

    initiative_id = find_value(
        item,
        [
            "id",
            "initiativeId",
        ],
    )

    title = find_value(
        item,
        [
            "title",
            "initiativeTitle",
            "name",
        ],
    )

    url = find_value(
        item,
        [
            "url",
            "link",
            "initiativeUrl",
        ],
    )

    # Some API versions may return the title
    # as a nested language object.
    if isinstance(title, dict):

        title = (
            title.get("EN")
            or title.get("en")
            or title.get("value")
        )

    if isinstance(url, dict):

        url = (
            url.get("EN")
            or url.get("en")
            or url.get("value")
        )

    # --------------------------------------------------------
    # Date candidates
    # --------------------------------------------------------

    publication_date = find_value(
        item,
        [
            "publicationDate",
            "publishedDate",
            "date",
            "startDate",
            "creationDate",
        ],
    )

    closing_date = find_value(
        item,
        [
            "closingDate",
            "endDate",
            "feedbackEndDate",
        ],
    )

    initiative_type = find_value(
        item,
        [
            "type",
            "initiativeType",
            "publicationType",
        ],
    )

    dg = find_value(
        item,
        [
            "dg",
            "directorateGeneral",
            "responsibleService",
        ],
    )

    return {
        "id": initiative_id,
        "title": title,
        "url": url,
        "publication_date": publication_date,
        "closing_date": closing_date,
        "type": initiative_type,
        "dg": dg,
        "raw": item,
    }


# ============================================================
# DATE PARSER
# ============================================================

def parse_date(value):

    if not value:
        return None

    if isinstance(value, dict):

        value = (
            value.get("value")
            or value.get("date")
            or value.get("EN")
            or value.get("en")
        )

    if not isinstance(value, str):
        return None

    value = value.strip()

    # ISO datetime
    try:

        return datetime.fromisoformat(
            value.replace(
                "Z",
                "+00:00"
            )
        )

    except ValueError:
        pass

    # Common EU date formats
    for fmt in (
        "%Y-%m-%d",
        "%d.%m.%Y",
        "%d/%m/%Y",
    ):

        try:

            return datetime.strptime(
                value,
                fmt
            ).replace(
                tzinfo=timezone.utc
            )

        except ValueError:
            pass

    return None


# ============================================================
# URL
# ============================================================

def make_url(item):

    url = item.get("url")

    if url:
        return str(url)

    initiative_id = item.get("id")

    if initiative_id:

        return (
            "https://ec.europa.eu/info/law/"
            "better-regulation/have-your-say/"
            f"initiatives/{initiative_id}"
        )

    return None


# ============================================================
# RSS
# ============================================================

def create_rss(
    initiatives
):

    rss = ET.Element(
        "rss",
        {
            "version": "2.0"
        },
    )

    channel = ET.SubElement(
        rss,
        "channel"
    )

    ET.SubElement(
        channel,
        "title"
    ).text = (
        "European Commission — Have Your Say"
    )

    ET.SubElement(
        channel,
        "link"
    ).text = (
        "https://ec.europa.eu/info/law/"
        "better-regulation/have-your-say_en"
    )

    ET.SubElement(
        channel,
        "description"
    ).text = (
        "European Commission Have Your Say "
        "public consultations and feedback initiatives"
    )

    ET.SubElement(
        channel,
        "language"
    ).text = "en"

    ET.SubElement(
        channel,
        "lastBuildDate"
    ).text = format_datetime(
        datetime.now(
            timezone.utc
        )
    )

    # --------------------------------------------------------
    # Sort newest first
    # --------------------------------------------------------

    initiatives.sort(
        key=lambda x: (
            x["_date"] is not None,
            x["_date"]
            or datetime.min.replace(
                tzinfo=timezone.utc
            ),
        ),
        reverse=True,
    )

    # --------------------------------------------------------
    # Items
    # --------------------------------------------------------

    for initiative in initiatives:

        title = initiative.get(
            "title"
        )

        url = initiative.get(
            "_url"
        )

        if not title or not url:
            continue

        item = ET.SubElement(
            channel,
            "item"
        )

        ET.SubElement(
            item,
            "title"
        ).text = str(title)

        ET.SubElement(
            item,
            "link"
        ).text = url

        # Stable GUID
        guid = (
            str(
                initiative.get("id")
            )
            if initiative.get("id")
            else url
        )

        ET.SubElement(
            item,
            "guid",
            {
                "isPermaLink": "false"
            },
        ).text = (
            "have-your-say:"
            + guid
        )

        if initiative["_date"]:

            ET.SubElement(
                item,
                "pubDate"
            ).text = format_datetime(
                initiative["_date"]
            )

        # ----------------------------------------------------
        # Categories
        # ----------------------------------------------------

        initiative_type = initiative.get(
            "type"
        )

        if isinstance(
            initiative_type,
            dict
        ):

            initiative_type = (
                initiative_type.get("label")
                or initiative_type.get("name")
                or initiative_type.get("EN")
                or initiative_type.get("en")
            )

        if initiative_type:

            ET.SubElement(
                item,
                "category"
            ).text = str(
                initiative_type
            )

        dg = initiative.get(
            "dg"
        )

        if isinstance(
            dg,
            dict
        ):

            dg = (
                dg.get("label")
                or dg.get("name")
                or dg.get("code")
            )

        if dg:

            ET.SubElement(
                item,
                "category"
            ).text = str(
                dg
            )

        # ----------------------------------------------------
        # Description
        # ----------------------------------------------------

        description_parts = []

        if initiative_type:

            description_parts.append(
                f"Type: {initiative_type}"
            )

        if dg:

            description_parts.append(
                f"Responsible service: {dg}"
            )

        if initiative.get(
            "closing_date"
        ):

            description_parts.append(
                "Closing date: "
                + str(
                    initiative[
                        "closing_date"
                    ]
                )
            )

        if description_parts:

            description = "<br/>".join(
                description_parts
            )

        else:

            description = (
                "European Commission "
                "Have Your Say initiative."
            )

        ET.SubElement(
            item,
            "description"
        ).text = html.escape(
            description
        )

    return ET.ElementTree(
        rss
    )


# ============================================================
# MAIN
# ============================================================

def main():

    print("=" * 60)
    print("EU HAVE YOUR SAY RSS")
    print("=" * 60)

    all_items = []

    # --------------------------------------------------------
    # Fetch first 5 pages.
    #
    # 5 × 100 = up to 500 initiatives.
    # --------------------------------------------------------

    for page in range(5):

        data = get_initiatives(
            page=page
        )

        items = extract_items(
            data
        )

        print(
            f"Found {len(items)} initiatives"
        )

        if not items:
            break

        for raw in items:

            initiative = extract_initiative(
                raw
            )

            if initiative:

                all_items.append(
                    initiative
                )

    # --------------------------------------------------------
    # Deduplicate
    # --------------------------------------------------------

    unique = {}

    for initiative in all_items:

        key = (
            initiative.get("id")
            or initiative.get("url")
            or initiative.get("title")
        )

        if key:
            unique[key] = initiative

    initiatives = list(
        unique.values()
    )

    print()
    print(
        f"Unique initiatives: "
        f"{len(initiatives)}"
    )

    # --------------------------------------------------------
    # Prepare URLs and dates
    # --------------------------------------------------------

    valid = []

    for initiative in initiatives:

        initiative["_url"] = make_url(
            initiative
        )

        initiative["_date"] = parse_date(
            initiative.get(
                "publication_date"
            )
        )

        if initiative.get(
            "title"
        ) and initiative.get(
            "_url"
        ):

            valid.append(
                initiative
            )

    initiatives = valid

    # --------------------------------------------------------
    # Limit RSS to 500
    # --------------------------------------------------------

    initiatives = initiatives[
        :500
    ]

    print(
        f"RSS items: "
        f"{len(initiatives)}"
    )

    if not initiatives:

        raise RuntimeError(
            "No initiatives were found."
        )

    # --------------------------------------------------------
    # Create RSS
    # --------------------------------------------------------

    rss = create_rss(
        initiatives
    )

    ET.indent(
        rss,
        space="  "
    )

    rss.write(
        OUTPUT_FILE,
        encoding="utf-8",
        xml_declaration=True
    )

    print()
    print(
        f"RSS successfully written to "
        f"{OUTPUT_FILE}"
    )

    print()
    print("=" * 60)
    print("SUCCESS")
    print("=" * 60)


if __name__ == "__main__":
    main()

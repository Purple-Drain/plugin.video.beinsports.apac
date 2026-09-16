# Pure helpers over this add-on's own api._menu() response (POST
# /api/layout/menu, {"Genre": ""}) -- the exact call live_channels()
# already makes, just read in full instead of filtered to Type 2. No
# network, no Kodi import, so this file is importable and testable
# completely off-device.
#
# Background: upstream 0.4.1 had a Catch Up route (POST
# /api/content/catchups, its own endpoint); that endpoint 404s under the
# current API and the method was dropped from api.py in 0.5.2. But the
# SAME menu call live_channels() already makes turns out to carry
# everything needed for on-demand browsing too -- confirmed live against a
# signed-in Shield on 2026-09-15: Type 6 rows are VOD catalogues (one per
# competition, plus a "LATEST VIDEOS" row), Type 4/5 are upcoming/recently-
# live event listings. api.play(vod_id=<Id>) already accepts a Type 6
# item's Id unmodified, so no api.py change was ever needed for any of
# this -- only new routes in plugin.py (see that file).

import re

VOD_TYPE = 6
UPCOMING_TYPE = 4
RECENT_TYPE = 5

LATEST_VIDEOS_NAME = "LATEST VIDEOS"


def menu_rows(menu_items, kind):
    """[row, ...] from a raw api._menu() response, filtered to one Type."""
    return [row for row in (menu_items or []) if row.get("Type") == kind]


def select_vod_rows(menu_items):
    """Type 6 rows as folder names, LATEST VIDEOS pinned first and the rest
    in the menu's own SortOrder -- matches the on-device probe's shape
    ('LATEST VIDEOS first, then the competitions')."""
    rows = menu_rows(menu_items, VOD_TYPE)
    return sorted(rows, key=lambda r: (
        0 if (r.get("Name") or "").strip().upper() == LATEST_VIDEOS_NAME else 1,
        r.get("SortOrder") if r.get("SortOrder") is not None else 0))


def vod_items(menu_items, row_name):
    """Playable items for one Type 6 row, by its Name. Every item carries an
    Id that api.play(vod_id=...) accepts unmodified (confirmed live:
    play-vod probe returned Path/PlayUrl/DrmToken/DrmTicket/CdnTicket, the
    same shape play() already unpacks for a live channel_id)."""
    for row in menu_rows(menu_items, VOD_TYPE):
        if (row.get("Name") or "") == row_name:
            return list((row.get("Data") or {}).get("Items") or [])
    return []


def live_events(menu_items, kind):
    """Info items for Type 4 (Upcoming Live) / Type 5 (Recently Live).
    Deliberately never wired to a playable path: these items carry EventId,
    not ChannelId or VodId (confirmed absent from the probed response, not
    merely unmapped by this code) -- info-only until a playable id turns up
    in a future probe."""
    rows = menu_rows(menu_items, kind)
    return list((rows[0].get("Data") or {}).get("Items") or []) if rows else []


# ---------------------------------------------------------------------------
# Fixture grouping (added 16.09.26). beIN publishes up to three videos per
# fixture (full match, extended highlights, short highlights) that share one
# content id: the trailing MP<digits> of the item Id, e.g.
#   carabao-cup-6mp000170295   (full match, ~2h, no VideoTag)
#   HL10m_MP000170295          (extended highlights, ~10 min)
#   HL2m_MP000170295           (short highlights, ~2 min)
# Grouping on that id is beIN's own identity, not a title heuristic, so it
# cannot mis-pair two different matches. Items without the suffix (round
# recaps, Motorsport/Tennis clip ids) stay ungrouped and list as they are.
# Verified against the 15.09.26 captured menu: 186 of 222 VOD items carry it.
# ---------------------------------------------------------------------------

_FIXTURE_ID_RE = re.compile(r"mp0*(\d+)$", re.IGNORECASE)
_EXTENDED_RE = re.compile(r"(^HL10m_)|(-\s*extended\s*$)", re.IGNORECASE)
_SHORT_RE = re.compile(r"^HL2m_", re.IGNORECASE)

FULL_MATCH = "Full match"
EXTENDED = "Extended highlights"
SHORT = "Highlights"
_VARIANT_ORDER = {FULL_MATCH: 0, EXTENDED: 1, SHORT: 2}


def fixture_key(item):
    """beIN's shared content id for an item, or None if the Id has none."""
    m = _FIXTURE_ID_RE.search(item.get("Id") or "")
    return m.group(1) if m else None


def variant_label(item):
    """Which of the three videos this is, from beIN's own Id/tag/duration.
    Falls back to the raw Title for any shape not seen before, so nothing
    is ever hidden behind a wrong label."""
    item_id = item.get("Id") or ""
    title = item.get("Title") or ""
    if _SHORT_RE.search(item_id):
        return SHORT
    if _EXTENDED_RE.search(item_id) or _EXTENDED_RE.search(title):
        return EXTENDED
    if (item.get("VideoTag") or "") in ("", "None") and (item.get("Duration") or 0) >= 3600:
        return FULL_MATCH
    return title


def fixture_title(items):
    """The fixture's display name: the first title without the '- Extended'
    suffix (all variants of one fixture share the base title), else the
    first title with that suffix stripped."""
    for it in items:
        t = it.get("Title") or ""
        if not _EXTENDED_RE.search(t):
            return t
    return _EXTENDED_RE.sub("", items[0].get("Title") or "").strip()


def group_fixtures(items):
    """[(kind, payload)] in first-appearance order (beIN's newest-first stays
    intact): ('fixture', {key, title, poster, items}) per MP-id group, items
    sorted full/extended/short; ('single', item) for anything ungrouped.
    Pure, one pass over the ~20 items a row carries."""
    groups = {}
    out = []
    for it in items:
        key = fixture_key(it)
        if key is None:
            out.append(("single", it))
            continue
        if key not in groups:
            groups[key] = {"key": key, "items": [], "poster": it.get("Poster")}
            out.append(("fixture", groups[key]))
        groups[key]["items"].append(it)
    for g in groups.values():
        g["items"].sort(key=lambda i: _VARIANT_ORDER.get(variant_label(i), 9))
        g["title"] = fixture_title(g["items"])
        if not g["poster"]:
            g["poster"] = next((i.get("Poster") for i in g["items"] if i.get("Poster")), None)
    return out


def fixture_items(items, key):
    """The variants of one fixture by MP id, in full/extended/short order."""
    for kind, payload in group_fixtures(items):
        if kind == "fixture" and payload["key"] == key:
            return payload["items"]
    return []

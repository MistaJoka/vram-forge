#!/usr/bin/env python3
"""The gate between untrusted price candidates and the public prices.json.

Candidates come from an AI reading the open web, so a hostile or simply wrong page
can influence them. Nothing here trusts that input: an item only changes when its
price, date and sources pass every rule below. Anything else keeps its last good
value and is listed in "held", so the page can say so rather than show a bad number.

CLI: merge.py --prices prices.json --rules feed/rules.json
              [--candidates candidates.json] [--signal market_signal.json]
"""

import argparse
import calendar
import copy
import json
import math
import os
import re
import sys
import tempfile
from datetime import date
from pathlib import Path
from urllib.parse import urlsplit

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
MONTH_RE = re.compile(r"^\d{4}-\d{2}$")
REDDIT_PREFIX = "https://www.reddit.com/r/"
MARKETS = ("used", "new_deal")
MAX_NAME_LENGTH = 80


def is_price(value: object) -> bool:
    """True for a real positive number; bool and NaN are rejected."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return False
    return math.isfinite(value) and value > 0


def allowed_host(url: object, allowed: list[str]) -> str | None:
    """Return the allowlist entry a URL belongs to, or None.

    Matches whole domain labels, so evil-newegg.com and newegg.com.evil.io fail,
    and only https passes, which also keeps javascript: links off the page.
    """
    if not isinstance(url, str):
        return None
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    if parts.scheme != "https":
        return None
    for entry in allowed:
        if host == entry or host.endswith("." + entry):
            return entry
    return None


def clean_name(value: object) -> str:
    """Reduce a source name to short printable text."""
    text = value if isinstance(value, str) else "source"
    printable = "".join(character for character in text if character.isprintable())
    return printable.strip()[:MAX_NAME_LENGTH] or "source"


def date_span(as_of: str) -> tuple[date, date]:
    """Return the first and last day a date covers; raises ValueError if invalid.

    A month-only date covers the whole month: it is only "future" once the month
    has not started, and it ages from the month's last day.
    """
    if DATE_RE.match(as_of):
        day = date.fromisoformat(as_of)
        return day, day
    if MONTH_RE.match(as_of):
        first = date.fromisoformat(as_of + "-01")
        return first, first.replace(day=calendar.monthrange(first.year, first.month)[1])
    raise ValueError(as_of)


def date_problem(as_of: object, previous_as_of: str, today: str, max_age_days: int) -> str | None:
    """Explain why a candidate's date is unusable, or return None if it is fine."""
    if not isinstance(as_of, str):
        return "bad date"
    try:
        first, last = date_span(as_of)
    except ValueError:
        return "bad date"
    current = date.fromisoformat(today)
    if first > current:
        return "date in the future"
    if (current - last).days > max_age_days:
        return "source date past the age limit"
    if as_of < previous_as_of:
        return "older than the current price"
    return None


def tracker_vouches(usable: list[dict], price: float, rules: dict) -> bool:
    """True if a trusted monthly tracker in the sources shows about this price."""
    return any(
        allowed_host(source["url"], rules["month_only_hosts"])
        and is_price(source.get("price"))
        and abs(source["price"] / price - 1) <= rules["agree_tolerance"]
        for source in usable
    )


def repeats_current_price(item: object, entry: dict) -> bool:
    """True if a candidate restates the current price with no newer date: nothing to do."""
    if not isinstance(item, dict) or not isinstance(item.get("as_of"), str):
        return False
    return item.get("price") == entry["price"] and item["as_of"] <= entry["as_of"]


def review(item: object, previous: dict, today: str, rules: dict) -> tuple[list[dict], str | None]:
    """Check one candidate; return its usable sources and any reason to hold it."""
    if not isinstance(item, dict) or not is_price(item.get("price")):
        return [], "bad price"
    problem = date_problem(item.get("as_of"), previous["as_of"], today, rules["max_age_days"])
    if problem:
        return [], problem
    sources = item.get("sources")
    usable = [
        source
        for source in (sources if isinstance(sources, list) else [])
        if isinstance(source, dict) and allowed_host(source.get("url"), rules["allowed_hosts"])
    ]
    if not usable:
        return [], "no allowed source"
    price = item["price"]
    if MONTH_RE.match(item["as_of"]) and not tracker_vouches(usable, price, rules):
        return [], "month-only date needs a trusted tracker showing this price"
    jump = abs(price / previous["price"] - 1)
    if jump > rules["max_jump"]:
        return [], f"{jump:.0%} move exceeds the {rules['max_jump']:.0%} limit; review by hand"
    if jump > rules["single_source_jump"]:
        agreeing = {
            allowed_host(source["url"], rules["allowed_hosts"])
            for source in usable
            if is_price(source.get("price"))
            and abs(source["price"] / price - 1) <= rules["agree_tolerance"]
        }
        if len(agreeing) < 2:
            return [], f"{jump:.0%} move needs 2 hosts agreeing, found {len(agreeing)}"
    return usable, None


def apply_price(entry: dict, item: dict, usable: list[dict]) -> None:
    """Write an accepted price onto an item, keeping one history point per month."""
    entry["price"] = item["price"]
    entry["as_of"] = item["as_of"]
    entry["sources"] = [
        {"name": clean_name(source.get("name")), "url": source["url"]} for source in usable
    ]
    month = item["as_of"][:7]
    history = entry["hist"]
    if history and history[-1][0] == month:
        history[-1] = [month, item["price"]]
    else:
        history.append([month, item["price"]])


def clean_signal(signal: dict, known_ids: set[str]) -> dict:
    """Rebuild the signal from known fields only, so no post text can pass through."""
    items: dict[str, dict] = {}
    raw_items = signal.get("items") if isinstance(signal.get("items"), dict) else {}
    for item_id, markets in raw_items.items():
        if item_id not in known_ids or not isinstance(markets, dict):
            continue
        for market in MARKETS:
            summary = markets.get(market)
            if not isinstance(summary, dict) or not all(
                is_price(summary.get(key)) for key in ("median", "low", "high")
            ):
                continue
            samples = [
                {"price": sample["price"], "url": sample["url"], "date": sample["date"]}
                for sample in summary.get("samples", [])
                if isinstance(sample, dict)
                and is_price(sample.get("price"))
                and isinstance(sample.get("url"), str)
                and sample["url"].startswith(REDDIT_PREFIX)
                and DATE_RE.match(str(sample.get("date", "")))
            ]
            items.setdefault(item_id, {})[market] = {
                "median": summary["median"],
                "low": summary["low"],
                "high": summary["high"],
                "n": int(summary.get("n", len(samples))),
                "last_seen": str(summary.get("last_seen", ""))[:10],
                "samples": samples,
            }
    return {
        "generated_at": str(signal.get("generated_at", ""))[:32],
        "window_days": int(signal.get("window_days", 30)),
        "items": items,
    }


def merge(
    previous: dict, candidates: dict, signal: dict | None, today: str, rules: dict
) -> tuple[dict, dict]:
    """Return (new feed, run report). previous is never modified."""
    feed = copy.deepcopy(previous)
    feed["generated_at"] = today
    feed["held"] = []
    accepted: list[str] = []
    raw_items = candidates.get("items") if isinstance(candidates.get("items"), dict) else {}
    for item_id, item in raw_items.items():
        entry = feed["items"].get(item_id)
        if entry is None:
            continue
        if repeats_current_price(item, entry):
            continue
        usable, reason = review(item, entry, today, rules)
        if reason:
            feed["held"].append({"id": item_id, "reason": reason})
        else:
            apply_price(entry, item, usable)
            accepted.append(item_id)
    if signal is not None:
        feed["signal"] = clean_signal(signal, set(feed["items"]))
    return feed, {"accepted": accepted, "held": feed["held"]}


def main(argv: list[str] | None = None) -> int:
    """Merge files in place; any malformed input leaves prices.json untouched."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prices", type=Path, required=True)
    parser.add_argument("--rules", type=Path, required=True)
    parser.add_argument("--candidates", type=Path)
    parser.add_argument("--signal", type=Path)
    parser.add_argument("--today", default=date.today().isoformat())
    args = parser.parse_args(argv)
    try:
        previous = json.loads(args.prices.read_text())
        rules = json.loads(args.rules.read_text())
        candidates = json.loads(args.candidates.read_text()) if args.candidates else {}
        signal = json.loads(args.signal.read_text()) if args.signal else None
        feed, report = merge(previous, candidates, signal, args.today, rules)
        text = json.dumps(feed, indent=1, sort_keys=True, allow_nan=False) + "\n"
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
        print(f"merge refused: {exc}", file=sys.stderr)
        return 1
    handle, temp_name = tempfile.mkstemp(dir=args.prices.parent, suffix=".tmp")
    with os.fdopen(handle, "w") as temp_file:
        temp_file.write(text)
    os.chmod(temp_name, 0o644)
    os.replace(temp_name, args.prices)
    print(json.dumps(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Tests for the gate between untrusted price candidates and the public feed."""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from feed.merge import merge  # noqa: E402

TODAY = "2026-11-05"
RULES = {
    "allowed_hosts": ["newegg.com", "gpupoet.com", "bestbuy.com"],
    "month_only_hosts": ["gpupoet.com"],
    "single_source_jump": 0.25,
    "max_jump": 0.60,
    "agree_tolerance": 0.10,
    "max_age_days": 21,
}


def previous() -> dict:
    """A feed holding one item at $1,000 and one at $2,000."""
    return {
        "schema": 1,
        "generated_at": "2026-11-01T00:00:00+00:00",
        "items": {
            "5080": {
                "price": 1000,
                "as_of": "2026-10-20",
                "sources": [{"name": "old", "url": "https://gpupoet.com/old"}],
                "hist": [["2026-02", 900], ["2026-10", 1000]],
            },
            "3090": {
                "price": 2000,
                "as_of": "2026-09",
                "sources": [],
                "hist": [["2026-09", 2000]],
            },
        },
        "signal": {"items": {}},
        "held": [],
    }


def candidate(price: float, *sources: tuple[str, float], as_of: str = TODAY) -> dict:
    """A candidate price citing the given (url, price) sources."""
    return {
        "price": price,
        "as_of": as_of,
        "sources": [{"name": "src", "url": url, "price": seen} for url, seen in sources],
    }


def merged(candidates: dict, signal: dict | None = None) -> tuple[dict, dict]:
    """Run merge with the standard fixtures."""
    return merge(previous(), {"items": candidates}, signal, TODAY, RULES)


class AcceptsGoodPrices(unittest.TestCase):
    def test_small_move_from_one_allowed_source_updates_the_item(self) -> None:
        feed, report = merged({"5080": candidate(1100, ("https://www.newegg.com/p/1", 1100))})
        self.assertEqual(feed["items"]["5080"]["price"], 1100)
        self.assertEqual(feed["items"]["5080"]["as_of"], TODAY)
        self.assertEqual(report["accepted"], ["5080"])

    def test_big_move_with_two_agreeing_hosts_updates_the_item(self) -> None:
        feed, _ = merged(
            {"5080": candidate(1400, ("https://newegg.com/a", 1400), ("https://gpupoet.com/b", 1350))}
        )
        self.assertEqual(feed["items"]["5080"]["price"], 1400)

    def test_new_month_appends_history(self) -> None:
        feed, _ = merged({"5080": candidate(1100, ("https://newegg.com/a", 1100))})
        self.assertEqual(feed["items"]["5080"]["hist"][-1], ["2026-11", 1100])
        self.assertEqual(len(feed["items"]["5080"]["hist"]), 3)

    def test_same_month_replaces_the_month_point(self) -> None:
        october = dict(candidate(1050, ("https://newegg.com/a", 1050), as_of="2026-10-28"))
        feed, _ = merge(previous(), {"items": {"5080": october}}, None, "2026-10-29", RULES)
        self.assertEqual(feed["items"]["5080"]["hist"], [["2026-02", 900], ["2026-10", 1050]])


class HoldsBadPrices(unittest.TestCase):
    def assert_held(self, item: dict, reason_fragment: str, item_id: str = "5080") -> None:
        before = previous()["items"][item_id]
        feed, report = merged({item_id: item})
        self.assertEqual(feed["items"][item_id], before)
        self.assertEqual([h["id"] for h in feed["held"]], [item_id])
        self.assertIn(reason_fragment, feed["held"][0]["reason"])
        self.assertEqual(report["accepted"], [])

    def test_big_move_from_one_source_is_held(self) -> None:
        self.assert_held(candidate(1400, ("https://newegg.com/a", 1400)), "2 hosts")

    def test_big_move_from_two_urls_on_one_host_is_held(self) -> None:
        self.assert_held(
            candidate(1400, ("https://newegg.com/a", 1400), ("https://www.newegg.com/b", 1400)),
            "2 hosts",
        )

    def test_big_move_where_sources_disagree_is_held(self) -> None:
        self.assert_held(
            candidate(1400, ("https://newegg.com/a", 1400), ("https://gpupoet.com/b", 1000)),
            "2 hosts",
        )

    def test_extreme_move_is_held_even_with_two_hosts(self) -> None:
        self.assert_held(
            candidate(3000, ("https://newegg.com/a", 3000), ("https://gpupoet.com/b", 3000)),
            "exceeds",
        )

    def test_source_host_outside_allowlist_is_held(self) -> None:
        self.assert_held(candidate(1100, ("https://evil.example/a", 1100)), "no allowed source")

    def test_lookalike_host_is_held(self) -> None:
        self.assert_held(candidate(1100, ("https://evil-newegg.com/a", 1100)), "no allowed source")
        self.assert_held(candidate(1100, ("https://newegg.com.evil.io/a", 1100)), "no allowed source")

    def test_non_https_source_is_held(self) -> None:
        self.assert_held(candidate(1100, ("http://newegg.com/a", 1100)), "no allowed source")
        self.assert_held(candidate(1100, ("javascript:alert(1)//newegg.com", 1100)), "no allowed source")

    def test_non_numeric_price_is_held(self) -> None:
        self.assert_held(candidate("1100", ("https://newegg.com/a", 1100)), "price")  # type: ignore[arg-type]
        self.assert_held(candidate(True, ("https://newegg.com/a", 1100)), "price")  # type: ignore[arg-type]
        self.assert_held(candidate(-5, ("https://newegg.com/a", 1100)), "price")
        self.assert_held(candidate(float("nan"), ("https://newegg.com/a", 1100)), "price")

    def test_future_date_is_held(self) -> None:
        self.assert_held(candidate(1100, ("https://newegg.com/a", 1100), as_of="2026-12-01"), "date")

    def test_malformed_date_is_held(self) -> None:
        self.assert_held(candidate(1100, ("https://newegg.com/a", 1100), as_of="<b>soon"), "date")

    def test_stale_source_date_is_held(self) -> None:
        self.assert_held(candidate(1100, ("https://newegg.com/a", 1100), as_of="2026-10-01"), "date")

    def test_date_older_than_current_price_is_held(self) -> None:
        october = candidate(1100, ("https://newegg.com/a", 1100), as_of="2026-10-19")
        feed, _ = merge(previous(), {"items": {"5080": october}}, None, "2026-10-29", RULES)
        self.assertEqual(feed["items"]["5080"]["price"], 1000)
        self.assertIn("older", feed["held"][0]["reason"])


class MonthOnlyDates(unittest.TestCase):
    """Some trackers publish monthly figures, with no day to report."""

    def run_on(self, item: dict, today: str = TODAY) -> dict:
        feed, _ = merge(previous(), {"items": {"3090": item}}, None, today, RULES)
        return feed

    def test_trusted_tracker_month_updates_the_item(self) -> None:
        item = candidate(2100, ("https://gpupoet.com/gpu/3090", 2100), as_of="2026-10")
        feed = self.run_on(item)
        self.assertEqual(feed["items"]["3090"]["price"], 2100)
        self.assertEqual(feed["items"]["3090"]["as_of"], "2026-10")
        self.assertEqual(feed["items"]["3090"]["hist"][-1], ["2026-10", 2100])
        self.assertEqual(feed["held"], [])

    def test_current_month_is_accepted(self) -> None:
        item = candidate(2100, ("https://gpupoet.com/gpu/3090", 2100), as_of="2026-11")
        self.assertEqual(self.run_on(item)["items"]["3090"]["price"], 2100)

    def assert_month_held(self, item: dict, fragment: str, today: str = TODAY) -> None:
        feed = self.run_on(item, today)
        self.assertEqual(feed["items"]["3090"]["price"], 2000)
        self.assertEqual(feed["held"][0]["id"], "3090")
        self.assertIn(fragment, feed["held"][0]["reason"])

    def test_month_from_a_site_not_on_the_tracker_list_is_held(self) -> None:
        item = candidate(2100, ("https://newegg.com/a", 2100), as_of="2026-10")
        self.assert_month_held(item, "month-only")

    def test_tracker_showing_a_different_price_does_not_vouch_for_it(self) -> None:
        item = candidate(
            2100,
            ("https://newegg.com/a", 2100),
            ("https://gpupoet.com/gpu/3090", 1500),
            as_of="2026-10",
        )
        self.assert_month_held(item, "month-only")

    def test_tracker_cited_without_a_price_does_not_vouch_for_it(self) -> None:
        item = candidate(2100, ("https://gpupoet.com/gpu/3090", None), as_of="2026-10")
        self.assert_month_held(item, "month-only")

    def test_lookalike_tracker_host_does_not_vouch_for_it(self) -> None:
        item = candidate(
            2100,
            ("https://newegg.com/a", 2100),
            ("https://evil-gpupoet.com/x", 2100),
            as_of="2026-10",
        )
        self.assert_month_held(item, "month-only")

    def test_future_month_is_held(self) -> None:
        item = candidate(2100, ("https://gpupoet.com/gpu/3090", 2100), as_of="2026-12")
        self.assert_month_held(item, "future")

    def test_month_ended_past_the_age_limit_is_held(self) -> None:
        item = candidate(2100, ("https://gpupoet.com/gpu/3090", 2100), as_of="2026-09")
        self.assert_month_held(item, "age limit", today="2026-10-25")

    def test_month_age_counts_from_the_end_of_the_month(self) -> None:
        item = candidate(2100, ("https://gpupoet.com/gpu/3090", 2100), as_of="2026-10")
        self.assertEqual(self.run_on(item, "2026-11-21")["items"]["3090"]["price"], 2100)
        self.assert_month_held(item, "age limit", today="2026-11-22")

    def test_impossible_month_is_held(self) -> None:
        item = candidate(2100, ("https://gpupoet.com/gpu/3090", 2100), as_of="2026-13")
        self.assert_month_held(item, "bad date")

    def test_year_only_date_is_held(self) -> None:
        item = candidate(2100, ("https://gpupoet.com/gpu/3090", 2100), as_of="2026")
        self.assert_month_held(item, "bad date")

    def test_month_cannot_replace_a_price_dated_within_that_month(self) -> None:
        item = candidate(1100, ("https://gpupoet.com/gpu/5080", 1100), as_of="2026-10")
        feed, _ = merge(previous(), {"items": {"5080": item}}, None, TODAY, RULES)
        self.assertEqual(feed["items"]["5080"]["price"], 1000)
        self.assertIn("older", feed["held"][0]["reason"])


class DropsUnknownInput(unittest.TestCase):
    def test_unknown_item_id_never_enters_the_feed(self) -> None:
        feed, _ = merged({"<img src=x>": candidate(1100, ("https://newegg.com/a", 1100))})
        self.assertNotIn("<img src=x>", feed["items"])

    def test_missing_candidate_keeps_previous_price_and_date(self) -> None:
        feed, report = merged({})
        self.assertEqual(feed["items"]["3090"], previous()["items"]["3090"])
        self.assertEqual(report["accepted"], [])

    def test_source_keeps_only_https_url_and_short_plain_name(self) -> None:
        item = candidate(1100, ("https://newegg.com/a", 1100))
        item["sources"][0]["name"] = "x" * 300 + "\n\x07"
        item["sources"][0]["extra"] = "drop me"
        feed, _ = merged({"5080": item})
        kept = feed["items"]["5080"]["sources"][0]
        self.assertEqual(set(kept), {"name", "url"})
        self.assertLessEqual(len(kept["name"]), 80)
        self.assertNotIn("\x07", kept["name"])


class SignalHandling(unittest.TestCase):
    def good_signal(self) -> dict:
        return {
            "generated_at": "2026-11-05T00:00:00+00:00",
            "window_days": 30,
            "items": {
                "3090": {
                    "used": {
                        "median": 1500.0,
                        "low": 1400.0,
                        "high": 1900.0,
                        "n": 3,
                        "last_seen": "2026-11-04",
                        "samples": [
                            {
                                "price": 1500.0,
                                "url": "https://www.reddit.com/r/hardwareswap/comments/a/",
                                "date": "2026-11-04",
                            }
                        ],
                    }
                }
            },
        }

    def test_good_signal_passes_through(self) -> None:
        feed, _ = merged({}, self.good_signal())
        self.assertEqual(feed["signal"]["items"]["3090"]["used"]["median"], 1500.0)

    def test_signal_never_changes_the_headline_price(self) -> None:
        feed, _ = merged({}, self.good_signal())
        self.assertEqual(feed["items"]["3090"]["price"], 2000)

    def test_signal_unknown_item_and_market_are_dropped(self) -> None:
        signal = self.good_signal()
        signal["items"]["nope"] = signal["items"]["3090"]
        signal["items"]["3090"]["scam"] = signal["items"]["3090"]["used"]
        feed, _ = merged({}, signal)
        self.assertEqual(set(feed["signal"]["items"]), {"3090"})
        self.assertEqual(set(feed["signal"]["items"]["3090"]), {"used"})

    def test_signal_sample_with_non_reddit_url_is_dropped(self) -> None:
        signal = self.good_signal()
        signal["items"]["3090"]["used"]["samples"][0]["url"] = "https://evil.example/x"
        feed, _ = merged({}, signal)
        self.assertEqual(feed["signal"]["items"]["3090"]["used"]["samples"], [])

    def test_signal_extra_fields_such_as_titles_are_dropped(self) -> None:
        signal = self.good_signal()
        signal["items"]["3090"]["used"]["title"] = "leak"
        signal["items"]["3090"]["used"]["samples"][0]["title"] = "leak"
        feed, _ = merged({}, signal)
        self.assertNotIn("leak", json.dumps(feed))

    def test_absent_signal_keeps_the_previous_one(self) -> None:
        prior = previous()
        prior["signal"] = {"items": {"3090": {"used": {"median": 1}}}, "window_days": 30}
        feed, _ = merge(prior, {"items": {}}, None, TODAY, RULES)
        self.assertEqual(feed["signal"], prior["signal"])


class CommandLine(unittest.TestCase):
    def run_cli(self, directory: Path, candidates_text: str | None) -> subprocess.CompletedProcess:
        (directory / "prices.json").write_text(json.dumps(previous()))
        (directory / "rules.json").write_text(json.dumps(RULES))
        args = [
            sys.executable,
            str(ROOT / "feed" / "merge.py"),
            "--prices", str(directory / "prices.json"),
            "--rules", str(directory / "rules.json"),
            "--today", TODAY,
        ]
        if candidates_text is not None:
            (directory / "candidates.json").write_text(candidates_text)
            args += ["--candidates", str(directory / "candidates.json")]
        return subprocess.run(args, capture_output=True, text=True, check=False)

    def test_updates_the_feed_file(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            good = {"items": {"5080": candidate(1100, ("https://newegg.com/a", 1100))}}
            result = self.run_cli(directory, json.dumps(good))
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                json.loads((directory / "prices.json").read_text())["items"]["5080"]["price"], 1100
            )

    def test_malformed_candidates_fail_and_leave_the_feed_alone(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            result = self.run_cli(directory, "not json {")
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(json.loads((directory / "prices.json").read_text()), previous())


if __name__ == "__main__":
    unittest.main()

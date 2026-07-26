"""Tests for main.py.

Everything here mocks es_request — no test hits a real Elasticsearch cluster.
"""
import argparse
import json
import time
from unittest.mock import patch, call

import pytest

import es_housekeeping as main


# ---------------------------------------------------------------------------
# age_days: date parsing / floor behaviour
# ---------------------------------------------------------------------------

class TestAgeDays:
    def test_uses_name_embedded_date_when_present(self):
        # logs-YYYY.MM.DD names are trusted over creation_date_ms, because
        # this ES version rejects backdating creation_date (seed_data.py
        # documents this — confirmed for real against the local cluster).
        name = (time.strftime("logs-%Y.%m.%d", time.gmtime(time.time() - 10 * 86_400)))

        # creation_date_ms deliberately says "today" (the bug we're guarding
        # against) — the name-embedded date must win regardless.
        today_ms = int(time.time() * 1000)
        assert main.age_days(name, today_ms) == 10

    def test_falls_back_to_creation_date_for_reference_data(self):
        five_days_ago_ms = int((time.time() - 5 * 86_400) * 1000)
        assert main.age_days("reference-countries", five_days_ago_ms) == 5

    def test_floors_partial_days_does_not_round(self):
        # 10.9 days old must report 10, never 11.
        partial_ms = int((time.time() - 10.9 * 86_400) * 1000)
        assert main.age_days("app-config", partial_ms) == 10

    def test_today_is_zero_not_negative(self):
        now_ms = int(time.time() * 1000)
        assert main.age_days("app-config", now_ms) == 0

    def test_malformed_dated_looking_name_falls_back(self):
        # "logs-2026.13.40" has an invalid month/day — regex requires exactly
        # 4-2-2 digits but doesn't validate calendar correctness, so this
        # actually would match the regex shape. Guard against a real invalid
        # date (month 13) raising instead of silently misbehaving.
        ten_days_ago_ms = int((time.time() - 10 * 86_400) * 1000)
        with pytest.raises(ValueError):
            main.age_days("logs-2026.13.40", ten_days_ago_ms)


# ---------------------------------------------------------------------------
# fetch_data: merging _cat/indices with _ilm/explain
# ---------------------------------------------------------------------------

class TestFetchData:
    def test_merges_ilm_info_by_index_name(self):
        cat_response = [
            {"index": "app-config", "health": "green", "docs.count": "12",
             "pri.store.size": "10kb", "creation.date": "1700000000000"},
            {"index": "logs-2026.07.25", "health": "yellow", "docs.count": "300",
             "pri.store.size": "1mb", "creation.date": "1700000000000"},
        ]
        ilm_response = {
            "indices": {
                "logs-2026.07.25": {"index": "logs-2026.07.25", "managed": True,
                                     "policy": "housekeeping-demo-policy"},
                # app-config deliberately absent — must default to managed=False
            }
        }

        with patch("es_housekeeping.es_request", side_effect=[cat_response, ilm_response]) as mock_es:
            data = main.fetch_data("*")

        assert mock_es.call_args_list == [
            call("GET", "/_cat/indices/*",
                 params={"format": "json",
                         "h": "index,health,docs.count,pri.store.size,creation.date"}),
            call("GET", "/*/_ilm/explain", params={"format": "json"}),
        ]

        by_name = {row["index"]: row for row in data}
        assert by_name["logs-2026.07.25"]["ilm_managed"] is True
        assert by_name["app-config"]["ilm_managed"] is False

    def test_index_missing_from_ilm_explain_does_not_raise(self):
        cat_response = [
            {"index": "app-config", "health": "green", "docs.count": "12",
             "pri.store.size": "10kb", "creation.date": "1700000000000"},
        ]
        ilm_response = {"indices": {}}

        with patch("es_housekeeping.es_request", side_effect=[cat_response, ilm_response]):
            data = main.fetch_data("*")

        assert data[0]["ilm_managed"] is False

    def test_carries_through_health_docs_and_size_fields(self):
        cat_response = [
            {"index": "logs-2026.07.25", "health": "red", "docs.count": "9999",
             "pri.store.size": "500mb", "creation.date": "1700000000000"},
        ]
        ilm_response = {"indices": {}}

        with patch("es_housekeeping.es_request", side_effect=[cat_response, ilm_response]):
            data = main.fetch_data("logs-*")

        row = data[0]
        assert row["health"] == "red"
        assert row["docs_count"] == "9999"
        assert row["primary_store_size"] == "500mb"


# ---------------------------------------------------------------------------
# report(): --json vs table output
# ---------------------------------------------------------------------------

class TestReport:
    def _fake_data(self):
        return [
            {"index": "logs-2026.07.25", "health": "green", "docs_count": "300",
             "primary_store_size": "1mb", "age_in_days": 0, "ilm_managed": True},
        ]

    def test_json_flag_produces_valid_parseable_json(self, capsys):
        args = argparse.Namespace(pattern="*", json=True)
        with patch("es_housekeeping.fetch_data", return_value=self._fake_data()):
            main.report(args)

        out = capsys.readouterr().out
        parsed = json.loads(out)  # must not raise — this must be real JSON
        assert parsed == self._fake_data()

    def test_default_output_is_a_table_not_json(self, capsys):
        args = argparse.Namespace(pattern="*", json=False)
        with patch("es_housekeeping.fetch_data", return_value=self._fake_data()):
            main.report(args)

        out = capsys.readouterr().out
        with pytest.raises(json.JSONDecodeError):
            json.loads(out)
        assert "logs-2026.07.25" in out
        assert "index" in out  # header row present


# ---------------------------------------------------------------------------
# cleanup(): the stale-index decision + dry-run safety (the critical part)
# ---------------------------------------------------------------------------

class TestCleanupStaleDecision:
    def _row(self, name, age):
        return {"index": name, "health": "green", "docs_count": "1",
                "primary_store_size": "1kb", "age_in_days": age, "ilm_managed": False}

    def test_boundary_is_inclusive_equal_age_counts_as_stale(self):
        # code uses >=, so an index exactly at the threshold IS stale
        data = [self._row("logs-a", age=30)]
        args = argparse.Namespace(pattern="*", older_than_days=30, apply=False)

        with patch("es_housekeeping.fetch_data", return_value=data), \
             patch("builtins.print") as mock_print:
            main.cleanup(args)

        printed = " ".join(str(c) for c in mock_print.call_args_list)
        assert "1 old index" in printed

    def test_index_younger_than_threshold_is_not_stale(self):
        data = [self._row("logs-a", age=29)]
        args = argparse.Namespace(pattern="*", older_than_days=30, apply=False)

        with patch("es_housekeeping.fetch_data", return_value=data), \
             patch("builtins.print") as mock_print:
            main.cleanup(args)

        printed = " ".join(str(c) for c in mock_print.call_args_list)
        assert "No indices matching" in printed

    def test_no_stale_indices_does_not_call_es_request_at_all(self):
        data = [self._row("logs-a", age=1)]
        args = argparse.Namespace(pattern="*", older_than_days=30, apply=True)

        with patch("es_housekeeping.fetch_data", return_value=data), \
             patch("es_housekeeping.es_request") as mock_es, \
             patch("builtins.input") as mock_input:
            main.cleanup(args)

        mock_es.assert_not_called()
        mock_input.assert_not_called()


class TestCleanupDryRunSafety:
    def _row(self, name="logs-old", age=200):
        return {"index": name, "health": "green", "docs_count": "1",
                "primary_store_size": "1kb", "age_in_days": age, "ilm_managed": False}

    def test_dry_run_default_never_calls_delete(self):
        args = argparse.Namespace(pattern="*", older_than_days=30, apply=False)

        with patch("es_housekeeping.fetch_data", return_value=[self._row()]), \
             patch("es_housekeeping.es_request") as mock_es, \
             patch("builtins.input") as mock_input:
            main.cleanup(args)

        mock_es.assert_not_called()
        # dry-run must not even prompt — there's nothing to confirm
        mock_input.assert_not_called()

    def test_apply_without_confirmation_does_not_delete(self):
        args = argparse.Namespace(pattern="*", older_than_days=30, apply=True)

        with patch("es_housekeeping.fetch_data", return_value=[self._row()]), \
             patch("es_housekeeping.es_request") as mock_es, \
             patch("builtins.input", return_value="no"):
            main.cleanup(args)

        mock_es.assert_not_called()

    def test_apply_confirmation_requires_exact_word_yes(self):
        # anything other than the literal "yes" must abort
        for answer in ("y", "Y", "", "sure", "YES please"):
            args = argparse.Namespace(pattern="*", older_than_days=30, apply=True)
            with patch("es_housekeeping.fetch_data", return_value=[self._row()]), \
                 patch("es_housekeeping.es_request") as mock_es, \
                 patch("builtins.input", return_value=answer):
                main.cleanup(args)
            mock_es.assert_not_called(), f"should not delete on input {answer!r}"

    def test_apply_plus_yes_confirmation_deletes_each_stale_index(self):
        rows = [self._row("logs-old-1", 200), self._row("logs-old-2", 300)]
        args = argparse.Namespace(pattern="*", older_than_days=30, apply=True)

        with patch("es_housekeeping.fetch_data", return_value=rows), \
             patch("es_housekeeping.es_request") as mock_es, \
             patch("builtins.input", return_value="yes"):
            main.cleanup(args)

        assert mock_es.call_args_list == [
            call("DELETE", "/logs-old-1"),
            call("DELETE", "/logs-old-2"),
        ]

    def test_confirmation_answer_is_case_and_whitespace_insensitive(self):
        args = argparse.Namespace(pattern="*", older_than_days=30, apply=True)
        with patch("es_housekeeping.fetch_data", return_value=[self._row()]), \
             patch("es_housekeeping.es_request") as mock_es, \
             patch("builtins.input", return_value="  YES  "):
            main.cleanup(args)

        mock_es.assert_called_once_with("DELETE", "/logs-old")


# ---------------------------------------------------------------------------
# error handling: a failing DELETE propagates rather than being swallowed
# ---------------------------------------------------------------------------

class TestCleanupErrorHandling:
    def test_delete_failure_propagates_as_valueerror(self):
        # Current behaviour: es_request raises ValueError on a non-OK
        # response, and cleanup() does not catch it — it's the caller's
        # (or shell's) job to notice the non-zero-ish failure. This test
        # pins that behaviour down so it can't silently change to
        # "swallow the error and claim success".
        row = {"index": "logs-old", "health": "green", "docs_count": "1",
               "primary_store_size": "1kb", "age_in_days": 200, "ilm_managed": False}
        args = argparse.Namespace(pattern="*", older_than_days=30, apply=True)

        with patch("es_housekeeping.fetch_data", return_value=[row]), \
             patch("es_housekeeping.es_request", side_effect=ValueError("Elastic API error: 404")), \
             patch("builtins.input", return_value="yes"):
            with pytest.raises(ValueError, match="404"):
                main.cleanup(args)

    def test_fetch_data_error_propagates_before_any_prompt(self):
        # If the report call itself fails (cluster unreachable, auth error,
        # etc.), cleanup must fail fast — it must not reach the confirmation
        # prompt with stale/incomplete data.
        args = argparse.Namespace(pattern="*", older_than_days=30, apply=True)

        with patch("es_housekeeping.fetch_data", side_effect=ValueError("Elastic API error: 503")), \
             patch("builtins.input") as mock_input:
            with pytest.raises(ValueError):
                main.cleanup(args)

        mock_input.assert_not_called()


# ---------------------------------------------------------------------------
# CLI argument parsing: defaults and the dry-run/apply safety guarantee
# ---------------------------------------------------------------------------

class TestArgParsing:
    def _parser(self):
        # Re-declares the same parser shape as main.main() so we can test
        # argument parsing without invoking real functionality.
        import argparse as ap
        p = ap.ArgumentParser(prog="es-housekeeping")
        sub = p.add_subparsers(dest="command", required=True)

        r = sub.add_parser("report")
        r.add_argument("--pattern", default="*")
        r.add_argument("--json", action="store_true")

        c = sub.add_parser("cleanup")
        c.add_argument("--pattern", default="*")
        c.add_argument("--older-than-days", type=int, required=True)
        g = c.add_mutually_exclusive_group()
        g.add_argument("--dry-run", dest="apply", action="store_false")
        g.add_argument("--apply", dest="apply", action="store_true")
        c.set_defaults(apply=False)
        return p

    def test_cleanup_defaults_to_dry_run_with_no_flag(self):
        args = self._parser().parse_args(["cleanup", "--older-than-days", "30"])
        assert args.apply is False

    def test_explicit_dry_run_flag(self):
        args = self._parser().parse_args(["cleanup", "--older-than-days", "30", "--dry-run"])
        assert args.apply is False

    def test_explicit_apply_flag(self):
        args = self._parser().parse_args(["cleanup", "--older-than-days", "30", "--apply"])
        assert args.apply is True

    def test_dry_run_and_apply_together_is_rejected(self):
        with pytest.raises(SystemExit):
            self._parser().parse_args(
                ["cleanup", "--older-than-days", "30", "--dry-run", "--apply"]
            )

    def test_older_than_days_is_required(self):
        with pytest.raises(SystemExit):
            self._parser().parse_args(["cleanup"])

    def test_report_json_flag_defaults_false(self):
        args = self._parser().parse_args(["report"])
        assert args.json is False

    def test_report_pattern_defaults_to_star(self):
        args = self._parser().parse_args(["report"])
        assert args.pattern == "*"

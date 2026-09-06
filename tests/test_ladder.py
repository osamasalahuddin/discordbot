"""Tests for the ladder maths and the bot's rating logic.

Stdlib unittest only, and nothing here imports discord.py, so this runs on a bare
checkout:

    python3 -m unittest discover -s tests -v
"""
import json
import random
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "data"))
sys.path.insert(0, str(REPO / "code"))

import ladder_common as lc  # noqa: E402
import ladder_data  # noqa: E402
import team_balancer  # noqa: E402

PATHS = list(lc.TRACKED)


def player(user_path, rating_change=None, name=None, civ="franks"):
    return {
        "user_path": user_path,
        "name": name or lc.TRACKED.get(user_path, "someone"),
        "civ": civ,
        "is_ai": False,
        "rating": 1000,
        "rating_change": rating_change,
    }


def match(teams, match_id=1, duration="40m 00s", exact_time="March 5, 2024, 1:46 p.m.", map_name="Arena"):
    return {
        "match_id": match_id,
        "map": map_name,
        "duration": duration,
        "exact_time": exact_time,
        "teams": teams,
        "ladder": "unranked",
    }


def two_sided(won_a, won_b, changes=(None, None), **kw):
    return match(
        [
            {"won": won_a, "players": [player(PATHS[0], changes[0])]},
            {"won": won_b, "players": [player(PATHS[1], changes[1])]},
        ],
        **kw,
    )


class TestParsers(unittest.TestCase):
    def test_parse_duration(self):
        self.assertEqual(lc.parse_duration("40m 00s"), 2400)
        self.assertEqual(lc.parse_duration("1h 04m 12s"), 3852)
        self.assertEqual(lc.parse_duration("59s"), 59)
        self.assertIsNone(lc.parse_duration(None))
        self.assertIsNone(lc.parse_duration(""))

    def test_parse_exact_time_am_pm(self):
        self.assertEqual(
            lc.parse_exact_time("March 5, 2024, 1:46 p.m."),
            datetime(2024, 3, 5, 13, 46, tzinfo=timezone.utc),
        )
        self.assertEqual(
            lc.parse_exact_time("Sept. 5, 2026, 11:05 a.m."),
            datetime(2026, 9, 5, 11, 5, tzinfo=timezone.utc),
        )

    def test_parse_exact_time_boundaries(self):
        # 12-hour wraparound is easy to get backwards in both directions.
        self.assertEqual(
            lc.parse_exact_time("Jan. 1, 2025, 12:30 a.m.").hour, 0
        )
        self.assertEqual(
            lc.parse_exact_time("Jan. 1, 2025, 12:30 p.m.").hour, 12
        )
        self.assertEqual(lc.parse_exact_time("Jan. 1, 2025, midnight").hour, 0)
        self.assertEqual(lc.parse_exact_time("Jan. 1, 2025, noon").hour, 12)

    def test_parse_exact_time_rejects_junk(self):
        self.assertIsNone(lc.parse_exact_time("not a date"))
        self.assertIsNone(lc.parse_exact_time(None))

    def test_match_datetime_falls_back_to_epoch(self):
        self.assertEqual(lc.match_datetime(match([], exact_time="???")), lc.EPOCH)


class TestResolveWonFlags(unittest.TestCase):
    def test_site_reported_winner_is_used(self):
        self.assertEqual(lc.resolve_won_flags(two_sided(True, False)), [True, False])
        self.assertEqual(lc.resolve_won_flags(two_sided(False, True)), [False, True])

    def test_shared_victory_is_undecidable(self):
        self.assertIsNone(lc.resolve_won_flags(two_sided(True, True)))

    def test_no_winner_inferred_from_rating_change(self):
        # This is the bug that deflated the ladder: no '.won' class on the page meant
        # every team came back False and everyone was scored as a loser.
        self.assertEqual(
            lc.resolve_won_flags(two_sided(False, False, changes=(16, -9))), [True, False]
        )
        self.assertEqual(
            lc.resolve_won_flags(two_sided(False, False, changes=(-9, 16))), [False, True]
        )

    def test_no_winner_and_no_rating_change_is_undecidable(self):
        self.assertIsNone(lc.resolve_won_flags(two_sided(False, False)))

    def test_zero_rating_change_is_undecidable(self):
        self.assertIsNone(lc.resolve_won_flags(two_sided(False, False, changes=(0, 0))))

    def test_mixed_signs_within_a_team_is_undecidable(self):
        m = match([
            {"won": False, "players": [player(PATHS[0], 12), player(PATHS[1], -4)]},
            {"won": False, "players": [player(PATHS[2], -9)]},
        ])
        self.assertIsNone(lc.resolve_won_flags(m))

    def test_multi_team_gains_on_two_sides_is_undecidable(self):
        m = match([
            {"won": False, "players": [player(PATHS[0], 12)]},
            {"won": False, "players": [player(PATHS[1], 9)]},
            {"won": False, "players": [player(PATHS[2], -9)]},
        ])
        self.assertIsNone(lc.resolve_won_flags(m))


class TestSelectQualifying(unittest.TestCase):
    def test_requires_tracked_players_on_two_sides(self):
        m = match([
            {"won": True, "players": [player(PATHS[0]), player(PATHS[1])]},
            {"won": False, "players": [player("/user/999999/", name="stranger")]},
        ])
        qualifying, stats = lc.select_qualifying({1: m})
        self.assertEqual(qualifying, [])
        self.assertEqual(stats["excluded_no_opposition"], 1)

    def test_excludes_short_games(self):
        qualifying, stats = lc.select_qualifying({1: two_sided(True, False, duration="14m 59s")})
        self.assertEqual(qualifying, [])
        self.assertEqual(stats["excluded_short_game"], 1)
        self.assertEqual(stats["excluded_short_game_match_ids"], [1])

    def test_excludes_undecidable_results(self):
        qualifying, stats = lc.select_qualifying({1: two_sided(False, False)})
        self.assertEqual(qualifying, [])
        self.assertEqual(stats["excluded_no_result"], 1)
        self.assertEqual(stats["excluded_no_result_match_ids"], [1])

    def test_excludes_matches_before_start_date(self):
        old = two_sided(True, False, exact_time="March 5, 2023, 1:46 p.m.")
        qualifying, stats = lc.select_qualifying({1: old})
        self.assertEqual(qualifying, [])
        self.assertEqual(stats["excluded_before_start_date"], 1)

    def test_normalises_won_flags_on_returned_matches(self):
        qualifying, _ = lc.select_qualifying({1: two_sided(False, False, changes=(16, -9))})
        self.assertEqual([t["won"] for t in qualifying[0]["teams"]], [True, False])

    def test_sorts_oldest_first(self):
        a = two_sided(True, False, match_id=1, exact_time="June 5, 2024, 1:00 p.m.")
        b = two_sided(True, False, match_id=2, exact_time="Jan. 5, 2024, 1:00 p.m.")
        qualifying, _ = lc.select_qualifying({1: a, 2: b})
        self.assertEqual([m["match_id"] for m in qualifying], [2, 1])


class TestSimulateWinloss(unittest.TestCase):
    def test_winner_gains_what_loser_loses(self):
        out = lc.simulate_winloss([two_sided(True, False)])
        a = out[lc.TRACKED[PATHS[0]]]
        b = out[lc.TRACKED[PATHS[1]]]
        self.assertGreater(a["current_elo"], lc.STARTING_ELO)
        self.assertLess(b["current_elo"], lc.STARTING_ELO)
        self.assertAlmostEqual(
            a["current_elo"] - lc.STARTING_ELO, lc.STARTING_ELO - b["current_elo"], places=1
        )

    def test_even_1v1_ladder_is_zero_sum(self):
        matches = [
            two_sided(i % 2 == 0, i % 2 == 1, match_id=i, exact_time=f"March {i+1}, 2024, 1:00 p.m.")
            for i in range(10)
        ]
        out = lc.simulate_winloss(matches)
        total = sum(p["current_elo"] for p in out.values())
        self.assertAlmostEqual(total, lc.STARTING_ELO * len(lc.TRACKED), places=0)

    def test_records_win_loss_counts(self):
        out = lc.simulate_winloss([two_sided(True, False)])
        a = out[lc.TRACKED[PATHS[0]]]
        self.assertEqual((a["matches_played"], a["wins"], a["losses"], a["win_rate"]), (1, 1, 0, 100.0))

    def test_untouched_players_keep_starting_elo(self):
        out = lc.simulate_winloss([two_sided(True, False)])
        self.assertEqual(out[lc.TRACKED[PATHS[5]]]["current_elo"], float(lc.STARTING_ELO))
        self.assertEqual(out[lc.TRACKED[PATHS[5]]]["win_rate"], 0.0)


class TestBalanceTeams(unittest.TestCase):
    def test_finds_the_even_split(self):
        result = team_balancer.balance_teams(
            [("a", 1200), ("b", 1000), ("c", 1100), ("d", 1100)]
        )
        self.assertEqual(result["elo_diff"], 0)
        self.assertAlmostEqual(result["avg_a"], result["avg_b"])

    def test_odd_count_adds_a_neutral_ai(self):
        players = [("a", 900), ("b", 1000), ("c", 1100)]
        result = team_balancer.balance_teams(players)
        self.assertTrue(result["ai_added"])
        self.assertIn(result["ai_team"], ("A", "B"))
        ai_elo = next(e for n, e in result["team_a"] + result["team_b"] if n == "AI")
        # The old hardcoded 1000 sat above every real rating once the ladder deflated,
        # which made the filler the strongest entity on the board.
        self.assertAlmostEqual(ai_elo, 1000.0)

        deflated = [("a", 700), ("b", 800), ("c", 900)]
        ai_elo = next(
            e for n, e in (lambda r: r["team_a"] + r["team_b"])(team_balancer.balance_teams(deflated))
            if n == "AI"
        )
        self.assertAlmostEqual(ai_elo, 800.0)
        self.assertLess(ai_elo, max(e for _, e in deflated))
        self.assertGreater(ai_elo, min(e for _, e in deflated))

    def test_explicit_ai_elo_is_respected(self):
        result = team_balancer.balance_teams([("a", 900), ("b", 1000), ("c", 1100)], ai_elo=1)
        ai_elo = next(e for n, e in result["team_a"] + result["team_b"] if n == "AI")
        self.assertEqual(ai_elo, 1.0)

    def test_teams_are_equal_size_and_keep_every_player(self):
        players = [(chr(97 + i), 900 + 20 * i) for i in range(7)]
        result = team_balancer.balance_teams(players)
        self.assertEqual(len(result["team_a"]), len(result["team_b"]))
        names = {n for n, _ in result["team_a"] + result["team_b"]}
        self.assertEqual(names, {n for n, _ in players} | {"AI"})

    def test_even_count_adds_no_ai(self):
        result = team_balancer.balance_teams([("a", 900), ("b", 1000)])
        self.assertFalse(result["ai_added"])
        self.assertIsNone(result["ai_team"])

    def test_rejects_empty_input(self):
        with self.assertRaises(ValueError):
            team_balancer.balance_teams([])

    def test_greedy_fallback_matches_exhaustive_closely(self):
        """The >20-player path is approximate; check it isn't materially worse.

        Measured over these 60 lobbies: identical to the exhaustive optimum in 55, worst
        case 2.9 Elo of total team difference, i.e. under 0.25 Elo per player on a 6v6.
        """
        rng = random.Random(1234)
        exact_hits = 0
        for _ in range(60):
            players = [(f"p{i}", rng.uniform(700, 1300)) for i in range(12)]
            half = len(players) // 2
            _, exact = team_balancer._best_split_exhaustive(players, half)
            _, greedy = team_balancer._best_split_greedy(players, half)
            self.assertLessEqual(greedy, exact + 5.0)
            exact_hits += greedy <= exact + 1e-9
        self.assertGreaterEqual(exact_hits, 50)

    def test_large_lobby_stays_fast_and_valid(self):
        players = [(f"p{i}", 800 + i * 7) for i in range(25)]
        result = team_balancer.balance_teams(players)
        self.assertEqual(len(result["team_a"]), len(result["team_b"]))
        self.assertEqual(len(result["team_a"]) + len(result["team_b"]), 26)


class TestMapAdjustedElos(unittest.TestCase):
    OVERALL = {"a": 900.0, "b": 800.0, "c": 850.0}
    MAP_DATA = {
        "Arena": {
            "total_matches": 30,
            "players": {
                "a": {"current_elo": 1100.0, "matches_played": 20},
                "b": {"current_elo": 1000.0, "matches_played": 10},
                "c": {"current_elo": 1200.0, "matches_played": 2},
            },
        }
    }

    def test_no_map_means_overall_elo(self):
        rated = ladder_data.map_adjusted_elos(self.OVERALL, None, self.MAP_DATA)
        self.assertEqual(rated, {n: (e, False) for n, e in self.OVERALL.items()})

    def test_qualified_group_is_rescaled_onto_the_overall_mean(self):
        rated = ladder_data.map_adjusted_elos(self.OVERALL, "Arena", self.MAP_DATA)
        # a and b qualify (>=5 games); their map mean 1050 shifts to their overall mean 850.
        self.assertTrue(rated["a"][1])
        self.assertTrue(rated["b"][1])
        self.assertAlmostEqual((rated["a"][0] + rated["b"][0]) / 2, 850.0)
        # The map-relative gap between them survives the shift.
        self.assertAlmostEqual(rated["a"][0] - rated["b"][0], 100.0)

    def test_unqualified_player_keeps_overall_elo(self):
        rated = ladder_data.map_adjusted_elos(self.OVERALL, "Arena", self.MAP_DATA)
        self.assertEqual(rated["c"], (850.0, False))

    def test_scales_are_comparable_after_adjustment(self):
        """The whole point: a fallback player must not look 200 points weak."""
        rated = ladder_data.map_adjusted_elos(self.OVERALL, "Arena", self.MAP_DATA)
        values = [e for e, _ in rated.values()]
        raw_gap = min(v["current_elo"] for v in self.MAP_DATA["Arena"]["players"].values()) - min(self.OVERALL.values())
        self.assertGreater(raw_gap, 150)  # the untreated mismatch
        self.assertLess(max(values) - min(values), 150)

    def test_nobody_qualified_means_all_overall(self):
        sparse = {"Arena": {"players": {"a": {"current_elo": 1100.0, "matches_played": 1}}}}
        rated = ladder_data.map_adjusted_elos(self.OVERALL, "Arena", sparse)
        self.assertEqual(rated, {n: (e, False) for n, e in self.OVERALL.items()})

    def test_unknown_map_means_all_overall(self):
        rated = ladder_data.map_adjusted_elos(self.OVERALL, "Nowhere", self.MAP_DATA)
        self.assertEqual(rated, {n: (e, False) for n, e in self.OVERALL.items()})


class TestFallbackNames(unittest.TestCase):
    RATED = {"a": (900.0, True), "b": (800.0, False), "c": (850.0, True)}

    def test_only_unrated_players_are_flagged(self):
        self.assertEqual(ladder_data.fallback_names(self.RATED, ["a", "b", "c"], "Arena"), ["b"])

    def test_nothing_is_flagged_without_a_map(self):
        # Without a map everyone is on overall Elo, so asterisking the whole roster - as
        # an earlier version of the embed did - conveys nothing.
        self.assertEqual(ladder_data.fallback_names(self.RATED, ["a", "b", "c"], None), [])

    def test_respects_the_given_subset(self):
        self.assertEqual(ladder_data.fallback_names(self.RATED, ["a", "c"], "Arena"), [])


class TestTopCivs(unittest.TestCase):
    def _ladder(self, civs):
        return {
            "players": {},
            "match_log": [
                {
                    "map": "Arena",
                    "teams": [{"won": True, "players": [{"user_path": "/user/1/", "civ": civ}]}],
                }
                for civ in civs
            ],
        }

    def test_counts_and_orders_civs(self):
        out = ladder_data.top_civs(self._ladder(["franks", "mayans", "franks"]), "/user/1/", "Arena")
        self.assertEqual(out, [("Franks", 2), ("Mayans", 1)])

    def test_null_civ_is_labelled_not_crashed(self):
        # Formatting a null civ used to raise AttributeError and make /playerstats
        # respond with "the application did not respond".
        out = ladder_data.top_civs(self._ladder([None, None, "franks"]), "/user/1/", "Arena")
        self.assertEqual(out, [("Unknown", 2), ("Franks", 1)])

    def test_limit_is_applied(self):
        out = ladder_data.top_civs(self._ladder(["a", "b", "c", "d"]), "/user/1/", "Arena", limit=2)
        self.assertEqual(len(out), 2)

    def test_missing_user_path_is_empty(self):
        self.assertEqual(ladder_data.top_civs(self._ladder(["franks"]), None, "Arena"), [])


class TestAgainstCommittedData(unittest.TestCase):
    """Guards that use the real published files, so regressions show up on real shapes."""

    @classmethod
    def setUpClass(cls):
        with open(REPO / "data" / "unranked_ladder.json", encoding="utf-8") as f:
            cls.ladder = json.load(f)
        with open(REPO / "data" / "map_elo.json", encoding="utf-8") as f:
            cls.map_elo = json.load(f)

    def test_inference_agrees_with_the_site_wherever_both_have_a_verdict(self):
        agree = disagree = 0
        for m in lc.load_all_matches().values():
            flags = [bool(t.get("won")) for t in m["teams"]]
            if sum(flags) != 1:
                continue
            stripped = {
                **m,
                "teams": [{**t, "won": False} for t in m["teams"]],
            }
            inferred = lc.resolve_won_flags(stripped)
            if inferred is None:
                continue
            if inferred == flags:
                agree += 1
            else:
                disagree += 1
        self.assertEqual(disagree, 0)
        self.assertGreater(agree, 500)

    def test_playerstats_civ_path_survives_every_player_and_map(self):
        for name, p in self.ladder["players"].items():
            for map_name in self.map_elo:
                civs = ladder_data.top_civs(self.ladder, p.get("user_path"), map_name)
                for label, count in civs:
                    self.assertIsInstance(label, str)
                    self.assertGreater(count, 0)

    def test_map_adjustment_closes_the_scale_gap_on_real_data(self):
        overall = {n: p["current_elo"] for n, p in self.ladder["players"].items()}
        busiest = sorted(self.map_elo, key=lambda k: -self.map_elo[k]["total_matches"])[:10]
        for map_name in busiest:
            rated = ladder_data.map_adjusted_elos(overall, map_name, self.map_elo)
            used = [e for e, used_map in rated.values() if used_map]
            if len(used) < 2:
                continue
            fell_back = [e for e, used_map in rated.values() if not used_map]
            if not fell_back:
                continue
            # A player on overall Elo must land inside the adjusted spread, not far below
            # it, which is what the raw per-map numbers did.
            self.assertLess(
                min(used) - max(fell_back), 150,
                f"{map_name}: fallback players still sit far below the map-rated ones",
            )


if __name__ == "__main__":
    unittest.main()

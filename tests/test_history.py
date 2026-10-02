import contextlib
import importlib.util
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd
import requests
import main as liq

spec = importlib.util.spec_from_file_location('site_build', Path(__file__).parents[1] / 'site/build.py')
site_build = importlib.util.module_from_spec(spec)
spec.loader.exec_module(site_build)


class HistoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.history = Path(self.temp.name) / 'history.csv'
        p = patch.object(liq, 'HISTORY_FILE', self.history)
        p.start()
        self.addCleanup(p.stop)

    def write(self, rows):
        defaults = dict(date='2026-03-09', draft_group='1', oprk_weight='0',
                        role='Util', name='Player', team='MIN', salary='1000',
                        projected_fppg='20', actual_fppg='', game_date='2026-03-09', league='NBA')
        pd.DataFrame([defaults | row for row in rows]).to_csv(self.history, index=False)

    def review(self):
        with contextlib.redirect_stdout(io.StringIO()):
            liq._review()
        return pd.read_csv(self.history, dtype=str).fillna('')

    def test_leagues_are_cached_separately_and_captain_multiplier_applies(self):
        self.write([dict(league='NBA'), dict(league='WNBA', draft_group='2', role='Captain')])
        with patch.object(liq, '_fetch_box_scores', side_effect=lambda d, league: {('Player', 'MIN'): 10 if league == 'NBA' else 30}) as fetch:
            df = self.review()
        self.assertEqual(list(df.actual_fppg), ['10.0', '45.0'])
        self.assertEqual(fetch.call_count, 2)

    def test_missing_player_is_not_guessed_to_be_a_dnp(self):
        self.write([{}, dict(name='DNP', draft_group='2')])
        with patch.object(liq, '_fetch_box_scores', return_value={('Other', 'MIN'): 40, ('DNP', 'MIN'): 0}):
            df = self.review()
        self.assertEqual(list(df.actual_fppg), ['', '0.0'])

    def test_reviewed_lineup_is_not_overwritten(self):
        self.write([dict(actual_fppg='35.5')])
        before = self.history.read_bytes()
        liq._save_lineup([dict(role='Util', name='Different', team='MIN', salary=1000,
                              projected_fppg=99, game_date='2026-03-09')], 1, '2026-03-09', 0)
        self.assertEqual(self.history.read_bytes(), before)

    def test_legacy_nba_history_remains_readable(self):
        self.write([{}])
        df = pd.read_csv(self.history).drop(columns='league')
        df.to_csv(self.history, index=False)
        with patch.object(liq, '_fetch_box_scores', return_value={('Player', 'MIN'): 20}):
            self.assertEqual(self.review().at[0, 'league'], 'NBA')

    def test_export_has_league_and_pending_totals(self):
        self.write([dict(league='WNBA'), dict(league='WNBA', name='Second', actual_fppg='10')])
        result = site_build.build_history()
        self.assertEqual(result['lineups'][0]['league'], 'WNBA')
        self.assertIsNone(result['lineups'][0]['total_actual_fppg'])
        self.assertFalse(result['lineups'][0]['reviewed'])

    def test_empty_history_has_stable_schema(self):
        self.assertEqual(site_build.build_history(), {'summary': None, 'lineups': []})


class SourceTests(unittest.TestCase):
    def summary(self, completed=True):
        return {'header': {'competitions': [{'status': {'type': {'completed': completed}}}]},
                'boxscore': {'players': [{'team': {'abbreviation': 'NY'}, 'statistics': [{
                    'labels': ['PTS', '3PT', 'REB', 'AST', 'STL', 'BLK', 'TO'],
                    'athletes': [
                        {'athlete': {'displayName': 'Scorer'}, 'stats': ['20', '2-5', '8', '4', '1', '2', '3']},
                        {'athlete': {'displayName': 'DNP'}, 'didNotPlay': True, 'stats': []},
                        {'athlete': {'displayName': 'Missing'}, 'stats': []},
                    ]}]}]}}

    def test_final_boxscore_scoring_dnp_and_missing_stats(self):
        board = {'events': [{'id': '1', 'status': {'type': {'completed': True}}}]}
        with patch.object(liq, '_api_get', side_effect=[board, self.summary()]):
            result = liq._fetch_espn_box_scores('2026-03-09', 'WNBA')
        self.assertEqual(result, {('Scorer', 'NYL'): 41.5, ('DNP', 'NYL'): 0})

    def test_live_games_are_never_recorded(self):
        board = {'events': [{'id': '1', 'status': {'type': {'completed': False}}}]}
        with patch.object(liq, '_api_get', return_value=board) as fetch:
            self.assertEqual(liq._fetch_espn_box_scores('2026-03-09', 'NBA'), {})
        self.assertEqual(fetch.call_count, 1)

    def test_summary_must_also_be_final(self):
        board = {'events': [{'id': '1', 'status': {'type': {'completed': True}}}]}
        with patch.object(liq, '_api_get', side_effect=[board, self.summary(False)]):
            self.assertEqual(liq._fetch_espn_box_scores('2026-03-09', 'NBA'), {})

    def test_source_failure_falls_back_with_correct_league(self):
        with patch.object(liq, '_fetch_espn_box_scores', side_effect=requests.Timeout), patch.object(liq, '_fetch_nba_box_scores', return_value={}) as fallback:
            liq._fetch_box_scores('2026-03-09', 'WNBA')
            fallback.assert_called_once_with('2026-03-09', 'WNBA')

    def test_empty_completed_results_do_not_trigger_unverified_fallback(self):
        with patch.object(liq, '_fetch_espn_box_scores', return_value={}), patch.object(liq, '_fetch_nba_box_scores') as fallback:
            self.assertEqual(liq._fetch_box_scores('2026-03-09'), {})
            fallback.assert_not_called()

    def test_future_dates_do_not_request_scores(self):
        with patch.object(liq, '_api_get') as fetch:
            self.assertEqual(liq._fetch_box_scores('2999-01-01'), {})
            fetch.assert_not_called()

    def test_stats_fallback_uses_wnba_league_and_calendar_season(self):
        with patch('nba_api.stats.endpoints.LeagueDashPlayerStats') as endpoint:
            endpoint.return_value.get_data_frames.return_value = [pd.DataFrame([{'PLAYER_NAME':'Player','TEAM_ABBREVIATION':'MIN','PTS':10}])]
            result = liq._fetch_nba_box_scores('2026-09-29', 'WNBA')
        self.assertEqual(endpoint.call_args.kwargs['league_id_nullable'], '10')
        self.assertEqual(endpoint.call_args.kwargs['season'], '2026')
        self.assertEqual(result, {('Player', 'MIN'): 10})


class SlateTests(unittest.TestCase):
    def lobby(self):
        return {'DraftGroups': [dict(DraftGroupId=1, GameTypeId=81, Sport='NBA'), dict(DraftGroupId=2, GameTypeId=81, Sport='NBA')],
                'Contests': [dict(dg=1, n='WNBA Showdown $6K'), dict(dg=2, n='NBA Showdown $10K')]}

    def test_mislabeled_wnba_group_is_excluded(self):
        self.assertEqual([g['DraftGroupId'] for g in liq._groups_for_mode(self.lobby(), 'Captain')], [2])
        self.assertIsNone(liq._mode_for_draft_group(self.lobby(), 1))
        self.assertEqual(liq._mode_for_draft_group(self.lobby(), 2), 'Captain')

    def test_wnba_only_lobby_means_no_nba_games(self):
        data = self.lobby()
        data['DraftGroups'] = data['DraftGroups'][:1]
        self.assertIsNone(liq._choose_mode_from_lobby(data))

    def test_contest_fallback_cannot_reintroduce_wnba(self):
        data = self.lobby()
        data['DraftGroups'] = []
        data['Contests'][0]['gameTypeId'] = 81
        self.assertEqual(liq._groups_for_mode(data, 'Captain'), [])


if __name__ == '__main__':
    unittest.main()

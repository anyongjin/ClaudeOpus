import copy
import importlib.util
import json
import math
import pathlib
import sys
import tempfile
import unittest
import urllib.error
from unittest.mock import Mock, call, patch


ROOT = pathlib.Path(__file__).resolve().parents[1]
NOW = 10000.0


def load_module():
    spec = importlib.util.spec_from_file_location('chain_health', ROOT / 'chain_health.py')
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class ChainHealthTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = load_module()

    def result(self, name, latency=400.0, stream_ok=True, attempts=2, samples=None):
        return self.module.ProbeResult(
            name=name,
            latencies_ms=[latency] * attempts if samples is None else samples,
            attempts=attempts,
            stream_ok=stream_ok,
            stream_seconds=6.0 if stream_ok else 0.0,
        )

    def history(self, latency, updated_at=NOW, stream_at=NOW, stream_ok=True):
        return {
            'score_ms': latency,
            'updated_at': updated_at,
            'stream_at': stream_at,
            'stream_ok': stream_ok,
        }

    def decide(self, results, state=None, now=NOW, **kwargs):
        return self.module.decide_switch(
            'current', results, {} if state is None else state, now=now, **kwargs
        )

    def test_score_combines_median_and_jitter(self):
        result = self.result('node', samples=[100.0, 300.0])
        self.assertEqual(self.module.score(result), 300.0)

    def test_score_penalizes_failed_short_requests(self):
        result = self.result('node', samples=[100.0])
        self.assertEqual(self.module.score(result), 2600.0)

    def test_score_is_infinite_when_every_request_fails(self):
        self.assertTrue(math.isinf(self.module.score(self.result('node', samples=[]))))

    def test_candidate_limit_includes_current_and_has_no_duplicates(self):
        state = {}
        result = self.module.choose_candidates(
            ['current', 'a', 'b', 'c', 'd'], 'current', state, limit=3, now=NOW
        )
        self.assertEqual(result[0], 'current')
        self.assertEqual(len(result), 3)
        self.assertEqual(len(result), len(set(result)))

    def test_single_candidate_budget_keeps_current(self):
        result = self.module.choose_candidates(
            ['a', 'current', 'b'], 'current', {}, limit=1, now=NOW
        )
        self.assertEqual(result, ['current'])

    def test_recent_stream_verified_champions_precede_exploration(self):
        state = {'nodes': {
            'best': self.history(50.0),
            'second': self.history(100.0),
            'third': self.history(200.0),
        }}
        result = self.module.choose_candidates(
            ['current', 'unknown', 'third', 'second', 'best'],
            'current', state, limit=3, now=NOW,
        )
        self.assertEqual(result, ['current', 'best', 'second'])

    def test_expired_measurement_is_not_promoted_as_champion(self):
        state = {'nodes': {
            'expired': self.history(1.0, updated_at=NOW - 1801),
            'best': self.history(100.0),
            'second': self.history(200.0),
        }}
        result = self.module.choose_candidates(
            ['current', 'expired', 'best', 'second'],
            'current', state, limit=3, now=NOW,
        )
        self.assertEqual(result, ['current', 'best', 'second'])

    def test_expired_stream_probe_is_not_promoted_as_champion(self):
        state = {'nodes': {
            'expired': self.history(1.0, stream_at=NOW - 1801),
            'best': self.history(100.0),
            'second': self.history(200.0),
        }}
        result = self.module.choose_candidates(
            ['current', 'expired', 'best', 'second'],
            'current', state, limit=3, now=NOW,
        )
        self.assertEqual(result, ['current', 'best', 'second'])

    def test_failed_stream_history_does_not_outrank_verified_champions(self):
        state = {'nodes': {
            'broken': self.history(1.0, stream_ok=False),
            'best': self.history(100.0),
            'second': self.history(200.0),
        }}
        result = self.module.choose_candidates(
            ['current', 'broken', 'best', 'second'],
            'current', state, limit=3, now=NOW,
        )
        self.assertEqual(result, ['current', 'best', 'second'])

    def test_history_cannot_reintroduce_removed_nodes(self):
        state = {'nodes': {'removed': self.history(1.0)}}
        result = self.module.choose_candidates(
            ['current', 'available'], 'current', state, limit=8, now=NOW
        )
        self.assertEqual(set(result), {'current', 'available'})

    def test_round_robin_eventually_tests_all_unknown_nodes(self):
        names = ['current', 'a', 'b', 'c', 'd', 'e']
        state = {}
        seen = set()
        for _ in range(len(names)):
            result = self.module.choose_candidates(names, 'current', state, limit=3, now=NOW)
            self.assertEqual(result[0], 'current')
            self.assertLessEqual(len(result), 3)
            seen.update(result)
        self.assertEqual(seen, set(names))

    def test_candidate_picker_excludes_direct_and_reject(self):
        result = self.module.choose_candidates(
            ['DIRECT', 'REJECT', 'current', 'candidate'],
            'current', {}, limit=8, now=NOW,
        )
        self.assertEqual(set(result), {'current', 'candidate'})

    def test_healthy_current_requires_two_consecutive_wins(self):
        state = {}
        results = [self.result('current', 400), self.result('better', 100)]
        self.assertIsNone(self.decide(results, state).target)
        self.assertEqual(self.decide(results, state, now=NOW + 180).target, 'better')

    def test_different_winner_does_not_inherit_previous_wins(self):
        state = {}
        first = [self.result('current', 400), self.result('a', 100)]
        second = [self.result('current', 400), self.result('b', 100)]
        self.assertIsNone(self.decide(first, state).target)
        self.assertIsNone(self.decide(second, state, now=NOW + 180).target)
        self.assertEqual(self.decide(second, state, now=NOW + 360).target, 'b')

    def test_small_absolute_gain_does_not_trigger_switch(self):
        results = [self.result('current', 200), self.result('better', 140)]
        self.assertIsNone(self.decide(results, required_wins=1).target)

    def test_small_relative_gain_does_not_trigger_switch(self):
        results = [self.result('current', 1000), self.result('better', 900)]
        self.assertIsNone(self.decide(results, required_wins=1).target)

    def test_exact_relative_threshold_is_eligible(self):
        results = [self.result('current', 400), self.result('better', 300)]
        self.assertEqual(self.decide(results, required_wins=1).target, 'better')

    def test_cooldown_blocks_latency_optimization(self):
        state = {'last_switch_at': NOW - 599}
        results = [self.result('current', 400), self.result('better', 100)]
        self.assertIsNone(self.decide(results, state, required_wins=1).target)

    def test_expired_cooldown_permits_latency_optimization(self):
        state = {'last_switch_at': NOW - 600}
        results = [self.result('current', 400), self.result('better', 100)]
        self.assertEqual(self.decide(results, state, required_wins=1).target, 'better')

    def test_stream_failure_triggers_recovery_without_waiting_for_wins(self):
        results = [self.result('current', 100, stream_ok=False), self.result('stable', 500)]
        self.assertEqual(self.decide(results).target, 'stable')

    def test_total_short_failure_triggers_recovery(self):
        results = [self.result('current', samples=[], stream_ok=None), self.result('stable', 500)]
        self.assertEqual(self.decide(results).target, 'stable')

    def test_cooldown_does_not_prevent_failure_recovery(self):
        state = {'last_switch_at': NOW - 1}
        results = [self.result('current', 100, stream_ok=False), self.result('stable', 500)]
        self.assertEqual(self.decide(results, state).target, 'stable')

    def test_fast_candidate_requires_stream_verification(self):
        for stream_ok in [False, None]:
            with self.subTest(stream_ok=stream_ok):
                results = [self.result('current', stream_ok=False),
                           self.result('unverified', 1, stream_ok=stream_ok)]
                self.assertIsNone(self.decide(results).target)

    def test_candidate_with_failed_short_request_cannot_replace_current(self):
        results = [self.result('current', stream_ok=False),
                   self.result('flaky', samples=[10.0])]
        self.assertIsNone(self.decide(results).target)

    def test_candidate_needs_at_least_two_successful_short_probes(self):
        results = [self.result('current', stream_ok=False),
                   self.result('under_sampled', 10.0, attempts=1)]
        self.assertIsNone(self.decide(results).target)

    def test_all_candidates_failed_keeps_current(self):
        results = [self.result('current', samples=[], stream_ok=False),
                   self.result('broken', samples=[], stream_ok=False)]
        self.assertIsNone(self.decide(results).target)

    def test_direct_and_reject_are_never_switch_targets(self):
        results = [self.result('current', stream_ok=False),
                   self.result('DIRECT', 1), self.result('REJECT', 2)]
        self.assertIsNone(self.decide(results).target)

    def test_long_shortlist_prioritizes_healthy_and_unknown_over_recent_stream_failures(self):
        state = {'nodes': {
            'fast-bad-a': self.history(10, stream_ok=False),
            'fast-bad-b': self.history(20, stream_ok=False),
            'slower-good': self.history(300),
        }}
        results = [self.result('current', 1, stream_ok=None),
                   self.result('fast-bad-a', 10, stream_ok=None),
                   self.result('fast-bad-b', 20, stream_ok=None),
                   self.result('slower-good', 300, stream_ok=None),
                   self.result('unknown', 400, stream_ok=None)]
        selected = self.module.long_candidates(results, 'current', state, now=NOW, limit=2)
        self.assertEqual([result.name for result in selected], ['slower-good', 'unknown'])

    def test_long_shortlist_restores_expired_stream_failures_to_latency_order(self):
        state = {'nodes': {
            'previously-bad-a': self.history(10, stream_at=NOW - 601, stream_ok=False),
            'previously-bad-b': self.history(20, stream_at=NOW - 601, stream_ok=False),
        }}
        results = [self.result('current', 1, stream_ok=None),
                   self.result('previously-bad-a', 10, stream_ok=None),
                   self.result('previously-bad-b', 20, stream_ok=None),
                   self.result('slower-good', 300, stream_ok=None)]
        selected = self.module.long_candidates(results, 'current', state, now=NOW, limit=2)
        self.assertEqual([result.name for result in selected], ['previously-bad-a', 'previously-bad-b'])

    def test_long_shortlist_demotion_still_allows_failed_nodes_to_be_retested(self):
        state = {'nodes': {
            'a': self.history(10, stream_ok=False),
            'b': self.history(20, stream_ok=False),
        }}
        results = [self.result('current', 1, stream_ok=None),
                   self.result('a', 10, stream_ok=None), self.result('b', 20, stream_ok=None)]
        selected = self.module.long_candidates(results, 'current', state, now=NOW, limit=2)
        self.assertEqual([result.name for result in selected], ['a', 'b'])

    def test_long_shortlist_excludes_failed_short_probes_and_forbidden_targets(self):
        results = [self.result('current', 1, stream_ok=None),
                   self.result('partial', samples=[2], stream_ok=None),
                   self.result('DIRECT', 3, stream_ok=None),
                   self.result('REJECT', 4, stream_ok=None),
                   self.result('verified-short', 300, stream_ok=None)]
        selected = self.module.long_candidates(results, 'current', {}, now=NOW, limit=2)
        self.assertEqual([result.name for result in selected], ['verified-short'])

    def test_long_shortlist_respects_limit_and_orders_unfailed_nodes_by_score(self):
        results = [self.result('current', 1, stream_ok=None),
                   self.result('slower', 300, stream_ok=None),
                   self.result('faster', 100, stream_ok=None)]
        selected = self.module.long_candidates(results, 'current', {}, now=NOW, limit=1)
        self.assertEqual([result.name for result in selected], ['faster'])


class TransportAndSwitchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = load_module()

    def setUp(self):
        patcher = patch.object(self.module, 'log')
        patcher.start()
        self.addCleanup(patcher.stop)
        self.settings = {'group': 'IKUUU', 'exit_ip': '192.0.2.20'}
        self.deadline = self.module.time.monotonic() + 100

    def carrier(self, tracker='old-carrier'):
        return {
            'id': tracker,
            'metadata': {
                'type': 'Inner',
                'network': 'udp',
                'destinationIP': '192.0.2.20',
                'destinationPort': '443',
            },
            'chains': ['old', 'IKUUU'],
        }

    def api(self, initial='old'):
        api = Mock()
        selection = {'now': initial}
        api.group.side_effect = lambda group: dict(selection)

        def select(group, name):
            selection['now'] = name

        api.select.side_effect = select
        return api, selection

    def transports(self, api, group, node, settings, exit_port):
        return [node + '-carrier']

    def test_transport_scope_accepts_only_exact_carrier(self):
        carrier = self.carrier()
        self.assertEqual(
            self.module.transport_ids([carrier], 'IKUUU', 'old', '192.0.2.20', 443),
            ['old-carrier'],
        )

    def test_transport_scope_accepts_integer_destination_port(self):
        carrier = self.carrier()
        carrier['metadata']['destinationPort'] = 443
        self.assertEqual(
            self.module.transport_ids([carrier], 'IKUUU', 'old', '192.0.2.20', '443'),
            ['old-carrier'],
        )

    def test_transport_scope_rejects_wrong_metadata_or_chain(self):
        changes = [
            ('type', 'HTTP'),
            ('network', 'tcp'),
            ('destinationIP', '192.0.2.21'),
            ('destinationPort', 444),
            ('chains', ['old', 'IKUUU-backup']),
            ('chains', ['old-other', 'IKUUU']),
            ('chains', ['HY2-EXIT']),
            ('id', ''),
        ]
        for key, value in changes:
            with self.subTest(key=key, value=value):
                carrier = copy.deepcopy(self.carrier())
                if key in {'chains', 'id'}:
                    carrier[key] = value
                else:
                    carrier['metadata'][key] = value
                self.assertEqual(
                    self.module.transport_ids(
                        [carrier], 'IKUUU', 'old', '192.0.2.20', 443
                    ),
                    [],
                )

    def test_transport_scope_does_not_include_business_flows(self):
        business = self.carrier('business-flow')
        business['metadata'].update(type='HTTP', network='tcp')
        self.assertEqual(
            self.module.transport_ids(
                [business, self.carrier(), {'id': 'unrelated'}],
                'IKUUU', 'old', '192.0.2.20', 443,
            ),
            ['old-carrier'],
        )

    def test_null_connections_response_is_an_empty_transport_list(self):
        api = Mock()
        api.request.return_value = {'connections': None}
        self.assertEqual(
            self.module.matching_transports(api, 'IKUUU', 'old', self.settings, 443),
            [],
        )
        api.request.assert_called_once_with('/connections')

    def test_selection_changed_before_switch_is_not_overwritten(self):
        api, selection = self.api(initial='external-choice')
        with patch.object(self.module, 'matching_transports') as matching, \
                patch.object(self.module, 'production_check') as check:
            switched = self.module.switch_verified(
                api, self.settings, 'old', 'new', 443, self.deadline
            )
        self.assertFalse(switched)
        self.assertEqual(selection['now'], 'external-choice')
        api.select.assert_not_called()
        api.request.assert_not_called()
        matching.assert_not_called()
        check.assert_not_called()

    def test_selection_changed_during_preflight_is_not_overwritten(self):
        api, _ = self.api()
        api.group.side_effect = [{'now': 'old'}, {'now': 'external-choice'}]
        with patch.object(self.module, 'matching_transports', side_effect=self.transports), \
                patch.object(self.module, 'production_check'):
            switched = self.module.switch_verified(
                api, self.settings, 'old', 'new', 443, self.deadline
            )
        self.assertFalse(switched)
        api.select.assert_not_called()
        api.request.assert_not_called()

    def test_missing_transport_scope_prevents_switch(self):
        api, _ = self.api()
        with patch.object(self.module, 'matching_transports', return_value=[]), \
                patch.object(self.module, 'production_check', return_value=True) as check:
            switched = self.module.switch_verified(
                api, self.settings, 'old', 'new', 443, self.deadline
            )
        self.assertFalse(switched)
        api.select.assert_not_called()
        api.request.assert_not_called()
        check.assert_called_once_with(self.settings, self.deadline, with_stream=False)

    def test_switch_selects_then_deletes_only_old_transport_and_checks_new(self):
        api, selection = self.api()
        with patch.object(self.module, 'matching_transports', side_effect=self.transports) as matching, \
                patch.object(self.module, 'production_check', return_value=True) as check:
            switched = self.module.switch_verified(
                api, self.settings, 'old', 'new', 443, self.deadline
            )
        self.assertTrue(switched)
        self.assertEqual(selection['now'], 'new')
        api.select.assert_called_once_with('IKUUU', 'new')
        api.request.assert_called_once_with('/connections/old-carrier', 'DELETE')
        self.assertLess(
            api.mock_calls.index(call.select('IKUUU', 'new')),
            api.mock_calls.index(call.request('/connections/old-carrier', 'DELETE')),
        )
        matching.assert_any_call(api, 'IKUUU', 'new', self.settings, 443)
        check.assert_called_once_with(self.settings, self.deadline - 12)

    def test_tracker_id_is_url_encoded_before_precise_delete(self):
        api, _ = self.api()
        with patch.object(self.module, 'matching_transports', return_value=['carrier/id ?']):
            deleted = self.module.reset_transports(api, 'IKUUU', 'old', self.settings, 443)
        self.assertEqual(deleted, ['carrier/id ?'])
        api.request.assert_called_once_with('/connections/carrier%2Fid%20%3F', 'DELETE')

    def test_failed_verification_rolls_back_and_deletes_new_transport(self):
        api, selection = self.api()
        with patch.object(self.module, 'matching_transports', side_effect=self.transports), \
                patch.object(self.module, 'production_check', side_effect=[False, False, True]), \
                patch.object(self.module.time, 'sleep'):
            switched = self.module.switch_verified(
                api, self.settings, 'old', 'new', 443, self.deadline
            )
        self.assertFalse(switched)
        self.assertEqual(selection['now'], 'old')
        self.assertEqual(api.select.call_args_list, [call('IKUUU', 'new'), call('IKUUU', 'old')])
        self.assertEqual(api.request.call_args_list, [
            call('/connections/old-carrier', 'DELETE'),
            call('/connections/new-carrier', 'DELETE'),
        ])

    def test_same_tracker_is_not_accepted_as_new_transport(self):
        api, selection = self.api()
        with patch.object(self.module, 'matching_transports', return_value=['same-carrier']), \
                patch.object(self.module, 'production_check', return_value=True), \
                patch.object(self.module.time, 'sleep'):
            switched = self.module.switch_verified(
                api, self.settings, 'old', 'new', 443, self.deadline
            )
        self.assertFalse(switched)
        self.assertEqual(selection['now'], 'old')
        self.assertEqual(api.select.call_args_list, [call('IKUUU', 'new'), call('IKUUU', 'old')])

    def test_external_selection_during_verification_is_not_rolled_back(self):
        api, selection = self.api()

        def fail_after_external_change(*args, **kwargs):
            selection['now'] = 'external-choice'
            return False

        with patch.object(self.module, 'matching_transports', side_effect=self.transports), \
                patch.object(self.module, 'production_check', side_effect=fail_after_external_change), \
                patch.object(self.module.time, 'sleep'):
            switched = self.module.switch_verified(
                api, self.settings, 'old', 'new', 443, self.deadline
            )
        self.assertFalse(switched)
        self.assertEqual(selection['now'], 'external-choice')
        api.select.assert_called_once_with('IKUUU', 'new')
        api.request.assert_called_once_with('/connections/old-carrier', 'DELETE')

    def test_put_applied_before_client_timeout_is_detected_and_rolled_back(self):
        api, selection = self.api()

        def select_with_lost_response(group, name):
            selection['now'] = name
            if name == 'new':
                raise urllib.error.URLError('response lost after selector was updated')

        api.select.side_effect = select_with_lost_response
        with patch.object(self.module, 'matching_transports', side_effect=self.transports) as matching, \
                patch.object(self.module, 'production_check', return_value=True) as check:
            switched = self.module.switch_verified(
                api, self.settings, 'old', 'new', 443, self.deadline
            )
        self.assertFalse(switched)
        self.assertEqual(selection['now'], 'old')
        self.assertEqual(api.select.call_args_list, [call('IKUUU', 'new'), call('IKUUU', 'old')])
        matching.assert_any_call(api, 'IKUUU', 'new', self.settings, 443)
        api.request.assert_any_call('/connections/new-carrier', 'DELETE')
        check.assert_called_once_with(self.settings, self.deadline, with_stream=False)

    def pending_transaction(self, directory):
        settings = dict(self.settings, state_dir=directory)
        path = pathlib.Path(directory) / 'transaction.json'
        pending = dict(self.settings, old='old', new='new', exit_port=443)
        path.write_text(json.dumps(pending))
        return settings, path

    def test_atomic_json_replaces_file_with_restricted_permissions(self):
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / 'transaction.json'
            path.write_text('{"previous":true}')
            path.chmod(0o644)
            self.module.atomic_json(path, {'old': 'a', 'new': 'b'})
            self.assertEqual(json.loads(path.read_text()), {'old': 'a', 'new': 'b'})
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(list(path.parent.iterdir()), [path])

    def test_atomic_json_serialization_failure_keeps_previous_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / 'transaction.json'
            path.write_text('{"previous":true}')
            with self.assertRaises(ValueError):
                self.module.atomic_json(path, {'invalid': float('nan')})
            self.assertEqual(json.loads(path.read_text()), {'previous': True})
            self.assertEqual(list(path.parent.iterdir()), [path])

    def test_transaction_recovery_restores_old_and_closes_new_carrier(self):
        api, selection = self.api(initial='new')
        with tempfile.TemporaryDirectory() as directory:
            settings, path = self.pending_transaction(directory)
            with patch.object(self.module, 'matching_transports', side_effect=self.transports), \
                    patch.object(self.module, 'production_check', return_value=True) as check:
                self.module.recover_transaction(api, settings, 443, self.deadline)
            self.assertFalse(path.exists())
        self.assertEqual(selection['now'], 'old')
        api.select.assert_called_once_with('IKUUU', 'old')
        api.request.assert_called_once_with('/connections/new-carrier', 'DELETE')
        check.assert_called_once_with(settings, self.deadline, with_stream=False)

    def test_transaction_recovery_cleans_new_carrier_when_already_rolled_back(self):
        api, selection = self.api(initial='old')
        with tempfile.TemporaryDirectory() as directory:
            settings, path = self.pending_transaction(directory)
            with patch.object(self.module, 'matching_transports', side_effect=self.transports), \
                    patch.object(self.module, 'production_check') as check:
                self.module.recover_transaction(api, settings, 443, self.deadline)
            self.assertFalse(path.exists())
        self.assertEqual(selection['now'], 'old')
        api.select.assert_not_called()
        api.request.assert_called_once_with('/connections/new-carrier', 'DELETE')
        check.assert_not_called()

    def test_transaction_recovery_preserves_external_selection(self):
        api, selection = self.api(initial='external-choice')
        with tempfile.TemporaryDirectory() as directory:
            settings, path = self.pending_transaction(directory)
            with patch.object(self.module, 'matching_transports') as matching, \
                    patch.object(self.module, 'production_check') as check:
                self.module.recover_transaction(api, settings, 443, self.deadline)
            self.assertFalse(path.exists())
        self.assertEqual(selection['now'], 'external-choice')
        api.select.assert_not_called()
        api.request.assert_not_called()
        matching.assert_not_called()
        check.assert_not_called()

    def test_transaction_scope_mismatch_is_not_mutated_or_deleted(self):
        api, _ = self.api(initial='new')
        with tempfile.TemporaryDirectory() as directory:
            settings, path = self.pending_transaction(directory)
            pending = json.loads(path.read_text())
            pending['exit_ip'] = '192.0.2.99'
            path.write_text(json.dumps(pending))
            with self.assertRaises(RuntimeError):
                self.module.recover_transaction(api, settings, 443, self.deadline)
            self.assertEqual(json.loads(path.read_text()), pending)
        self.assertEqual(api.mock_calls, [])

    def test_transaction_is_persisted_before_put_and_cleared_after_verified_switch(self):
        api, selection = self.api()
        with tempfile.TemporaryDirectory() as directory:
            settings = dict(self.settings, state_dir=directory)
            path = pathlib.Path(directory) / 'transaction.json'

            def select_after_journal(group, name):
                pending = json.loads(path.read_text())
                self.assertEqual((pending['old'], pending['new']), ('old', 'new'))
                self.assertEqual(pending['exit_ip'], settings['exit_ip'])
                selection['now'] = name

            api.select.side_effect = select_after_journal
            with patch.object(self.module, 'matching_transports', side_effect=self.transports), \
                    patch.object(self.module, 'production_check', return_value=True):
                switched = self.module.switch_verified(
                    api, settings, 'old', 'new', 443, self.deadline
                )
            self.assertTrue(switched)
            self.assertFalse(path.exists())
        self.assertEqual(selection['now'], 'new')

    def test_unavailable_controller_keeps_indeterminate_transaction_for_next_run(self):
        api, selection = self.api()
        api.group.side_effect = [
            {'now': 'old'}, {'now': 'old'}, urllib.error.URLError('controller unavailable')
        ]

        def select_then_timeout(group, name):
            selection['now'] = name
            raise urllib.error.URLError('lost response')

        api.select.side_effect = select_then_timeout
        with tempfile.TemporaryDirectory() as directory:
            settings = dict(self.settings, state_dir=directory)
            with patch.object(self.module, 'matching_transports', side_effect=self.transports), \
                    patch.object(self.module, 'production_check') as check:
                switched = self.module.switch_verified(
                    api, settings, 'old', 'new', 443, self.deadline
                )
            pending = json.loads((pathlib.Path(directory) / 'transaction.json').read_text())
            self.assertEqual((pending['old'], pending['new']), ('old', 'new'))
        self.assertFalse(switched)
        self.assertEqual(selection['now'], 'new')
        api.select.assert_called_once_with('IKUUU', 'new')
        api.request.assert_not_called()
        check.assert_not_called()


class CurlProbeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = load_module()

    def probe(self, stdout, returncode):
        details = {}
        completed = Mock(stdout=stdout, returncode=returncode)
        with patch.object(self.module.subprocess, 'run', return_value=completed):
            result = self.module.curl_probe(
                'http://127.0.0.1:17993',
                'https://stream.example.test/drip?duration=6&numbytes=12',
                self.module.time.monotonic() + 20,
                timeout=11,
                stream=True,
                details=details,
            )
        return result, details

    def test_partial_stream_fails_despite_http_200(self):
        result, details = self.probe('200 6.0 5', 18)
        self.assertEqual(result, (False, 6.0))
        self.assertEqual(details, {'http_status': '200', 'bytes': 5, 'curl_exit': 18})

    def test_complete_paced_stream_succeeds(self):
        result, details = self.probe('200 6.0 12', 0)
        self.assertEqual(result, (True, 6.0))
        self.assertEqual(details, {'http_status': '200', 'bytes': 12, 'curl_exit': 0})

    def test_complete_but_unpaced_response_is_not_a_valid_stream_probe(self):
        result, details = self.probe('200 0.2 12', 0)
        self.assertEqual(result, (False, 0.2))
        self.assertEqual(details, {'http_status': '200', 'bytes': 12, 'curl_exit': 0})


if __name__ == '__main__':
    unittest.main()

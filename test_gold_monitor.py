import tempfile
import unittest
from unittest.mock import patch
from datetime import datetime
from pathlib import Path
import gold_monitor as g


class MonitorTests(unittest.TestCase):
    def current(self, value='2026-11-09 09:30:00'):
        return datetime.strptime(value, '%Y-%m-%d %H:%M:%S').replace(tzinfo=g.BJT)

    def payload(self):
        return dict(heyue='Au99.99', delaystr='2026年11月09日 09:30:00', now='09:30',
                    times=['20:00', '09:00', '09:29', '09:30', '09:31', '15:30'],
                    data=[870, 868, 860, 855, 840, 830])

    def test_exact_threshold_and_no_other_volatility(self):
        self.assertTrue(g.should_send_alert({}, 835, 835))
        self.assertTrue(g.should_send_alert({}, 834, 835))
        self.assertFalse(g.should_send_alert({}, 836, 835))
        self.assertFalse(g.should_send_alert({'notified_threshold': 835}, 800, 835))

    def test_persisted_alert_survives_restart(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'state.json'
            g.save_state(path, {'notified_threshold': 835})
            self.assertFalse(g.should_send_alert(g.load_json(path), 835, 835))

    def test_high_alert_boundary_disabled_and_deduplication(self):
        self.assertFalse(g.should_send_high_alert({}, 879.99, 880))
        self.assertTrue(g.should_send_high_alert({}, 880, 880))
        self.assertTrue(g.should_send_high_alert({}, 900, 880))
        self.assertFalse(g.should_send_high_alert({}, 900, None))
        self.assertTrue(g.should_send_high_alert({'notified_threshold': 835}, 880, 880))
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'state.json'
            g.save_state(path, {'notified_high_threshold': 880})
            self.assertFalse(g.should_send_high_alert(g.load_json(path), 900, 880))
            self.assertTrue(g.should_send_alert(g.load_json(path), 835, 835))

    def test_no_alert_between_low_and_high_targets(self):
        for price in (835.01, 850, 870, 879.99):
            self.assertFalse(g.should_send_alert({}, price, 835))
            self.assertFalse(g.should_send_high_alert({}, price, 880))

    def test_ignore_future_price_slots_and_night_open(self):
        quote = g.extract_quote(self.payload(), self.current())
        self.assertEqual(quote['price'], 855)
        self.assertEqual(quote['open_price'], 868)

    def test_reject_stale_future_and_inconsistent_data(self):
        for when in ['2026-11-10 09:30:00', '2026-11-08 09:30:00', '2026-11-09 10:00:00']:
            with self.assertRaises(ValueError):
                g.extract_quote(self.payload(), self.current(when))
        data = self.payload()
        data['now'] = '20:00'
        with self.assertRaises(ValueError):
            g.extract_quote(data, self.current())

    def test_report_once_morning_only(self):
        quote = g.extract_quote(self.payload(), self.current())
        self.assertIsNotNone(g.build_report_if_due({}, quote, self.current()))
        for when in ['2026-11-09 09:29:00', '2026-11-09 20:00:00']:
            self.assertIsNone(g.build_report_if_due({}, quote, self.current(when)))
        self.assertIsNone(g.build_report_if_due({'last_morning_report_date': '2026-11-09'}, quote, self.current()))

    def test_report_contains_both_configured_targets(self):
        quote = g.extract_quote(self.payload(), self.current())
        report = g.build_report_if_due({}, quote, self.current(), 835, 880)
        self.assertIn('≤835', report[1])
        self.assertIn('≥880', report[1])

    def test_sessions(self):
        self.assertTrue(g.should_run(self.current('2026-11-08 09:30:00')))
        self.assertTrue(g.should_run(self.current('2026-11-07 09:30:00')))
        self.assertTrue(g.should_run(self.current('2026-11-07 01:00:00')))
        self.assertTrue(g.should_run(self.current('2026-11-09 20:00:00')))
        self.assertFalse(g.should_run(self.current('2026-11-09 12:00:00')))

    def run_with_payload(self, current, payload):
        with tempfile.TemporaryDirectory() as temp:
            state_path = Path(temp) / 'state.json'
            with patch.object(g, 'CONFIG_PATH', Path(temp) / 'config.json'), \
                 patch.object(g, 'now_bjt', return_value=current), \
                 patch.object(g, 'fetch_quote_payload', return_value=payload), \
                 patch.object(g, 'send_bark') as send, \
                 patch('sys.argv', ['gold-monitor', '--state-path', str(state_path)]):
                g.main()
                first_count = send.call_count
                g.main()
                return first_count, send.call_count, g.load_json(state_path)

    def test_weekend_open_market_sends_morning_report_once(self):
        for day in ('2026-11-07', '2026-11-08'):
            current = self.current(day + ' 09:30:00')
            payload = self.payload()
            payload['delaystr'] = current.strftime('%Y年%m月%d日 %H:%M:%S')
            first, total, state = self.run_with_payload(current, payload)
            self.assertEqual((first, total), (1, 1))
            self.assertEqual(state['last_morning_report_date'], day)

    def test_closed_market_with_stale_quote_sends_nothing(self):
        # A fresh quote on the preceding trading day is stale on a closure,
        # whether that closure falls on a weekday or a weekend.
        for day in ('2026-11-10', '2026-11-14'):
            first, total, state = self.run_with_payload(
                self.current(day + ' 09:30:00'), self.payload())
            self.assertEqual((first, total), (0, 0))
            self.assertNotIn('last_morning_report_date', state)


if __name__ == '__main__':
    unittest.main()

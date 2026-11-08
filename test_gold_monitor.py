import tempfile
import unittest
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
        self.assertTrue(g.should_send_alert({}, 850, 850))
        self.assertTrue(g.should_send_alert({}, 849, 850))
        self.assertFalse(g.should_send_alert({}, 851, 850))
        self.assertFalse(g.should_send_alert({'notified_threshold': 850}, 800, 850))

    def test_persisted_alert_survives_restart(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'state.json'
            g.save_state(path, {'notified_threshold': 850})
            self.assertFalse(g.should_send_alert(g.load_json(path), 850, 850))

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
        for when in ['2026-11-08 09:30:00', '2026-11-09 09:29:00', '2026-11-09 20:00:00']:
            self.assertIsNone(g.build_report_if_due({}, quote, self.current(when)))
        self.assertIsNone(g.build_report_if_due({'last_morning_report_date': '2026-11-09'}, quote, self.current()))

    def test_sessions(self):
        self.assertFalse(g.should_run(self.current('2026-11-08 09:30:00')))
        self.assertFalse(g.should_run(self.current('2026-11-07 09:30:00')))
        self.assertTrue(g.should_run(self.current('2026-11-07 01:00:00')))
        self.assertTrue(g.should_run(self.current('2026-11-09 20:00:00')))
        self.assertFalse(g.should_run(self.current('2026-11-09 12:00:00')))


if __name__ == '__main__':
    unittest.main()

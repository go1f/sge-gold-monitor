#!/usr/bin/env python3
"""SGE Au99.99: low/high target alerts and trading-day morning reports."""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
import tempfile
import urllib.parse
import urllib.request
from datetime import datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

BJT = ZoneInfo('Asia/Shanghai')
ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / 'gold_monitor.config.json'
STATE_PATH = ROOT / '.state/gold_monitor_state.json'
SYMBOL = 'Au99.99'
QUOTE_URL = 'https://www.sge.com.cn/graph/quotations'


def now_bjt():
    return datetime.now(BJT)


def should_run(current):
    t = current.time()
    # Probe every calendar day, including special weekend trading sessions.
    # Only fresh, validated SGE data may trigger reports or price alerts.
    return (time(9) <= t <= time(11, 30) or
            time(13, 30) <= t <= time(15, 30) or
            t >= time(20) or t <= time(2, 30))


def load_json(path):
    if not path.exists():
        return {}
    value = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(value, dict):
        raise ValueError('Expected a JSON object')
    return value


def save_state(path, state):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=path.parent, prefix='.state-')
    try:
        with os.fdopen(fd, 'w') as out:
            json.dump(state, out, ensure_ascii=False, indent=2)
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def fetch_quote_payload():
    headers = {'User-Agent': 'Mozilla/5.0', 'Referer': 'https://www.sge.com.cn/',
               'X-Requested-With': 'XMLHttpRequest'}
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor())
    with opener.open(urllib.request.Request('https://www.sge.com.cn/', headers=headers), timeout=15) as r:
        r.read()
    request = urllib.request.Request(QUOTE_URL,
        data=urllib.parse.urlencode({'instid': SYMBOL}).encode(), headers=headers)
    with opener.open(request, timeout=15) as r:
        return json.load(r)


def extract_quote(payload, current):
    if payload.get('heyue') != SYMBOL:
        raise ValueError('Unexpected instrument')
    source = datetime.strptime(payload['delaystr'].strip(), '%Y年%m月%d日 %H:%M:%S').replace(tzinfo=BJT)
    age = (current - source).total_seconds()
    if age < -120 or age > 900:
        raise ValueError('行情时间异常或超过15分钟未更新；不推送')
    times, prices = payload['times'], payload['data']
    if len(times) != len(prices):
        raise ValueError('Mismatched quote series')
    # The API includes a complete trading-session axis, including future slots.
    # Use its current-minute cursor rather than blindly taking the final price.
    cursor = payload['now']
    if cursor not in times:
        raise ValueError('行情当前分钟不在交易时间轴；不推送')
    end = times.index(cursor)
    cursor_time = datetime.strptime(cursor, '%H:%M').time()
    cursor_dt = datetime.combine(source.date(), cursor_time, BJT)
    if abs((source - cursor_dt).total_seconds()) > 900:
        raise ValueError('行情时间戳与当前分钟不一致；不推送')
    valid = []
    for i, raw in enumerate(prices[:end + 1]):
        if raw in (None, '', '-', '--'):
            continue
        price = float(raw)
        if math.isfinite(price) and price > 0:
            valid.append((i, price))
    if not valid:
        raise ValueError('No valid prices')
    last_index, price = valid[-1]
    last_time = datetime.strptime(times[last_index], '%H:%M').time()
    last_dt = datetime.combine(source.date(), last_time, BJT)
    if not 0 <= (cursor_dt - last_dt).total_seconds() <= 900:
        raise ValueError('当前分钟缺少有效成交价格；不推送')
    daytime = [(i, p) for i, p in valid if time(9) <= datetime.strptime(times[i], '%H:%M').time() <= time(15, 30)]
    return {'price': price, 'open_price': daytime[0][1] if daytime else None,
            'quote_time': source.isoformat(), 'minute': times[last_index]}


def build_report_if_due(state, quote, current, threshold=835, high_threshold=None):
    day = current.date().isoformat()
    if (time(9, 25) <= current.time() < time(11, 31)
            and quote['open_price'] is not None and state.get('last_morning_report_date') != day):
        targets = f'低位 ≤{threshold:g} 元/克'
        if high_threshold is not None:
            targets += f'，高位 ≥{high_threshold:g} 元/克'
        return (f'{SYMBOL} 开盘早报',
                f"今日开盘 {quote['open_price']:.2f} 元/克，现价 {quote['price']:.2f} 元/克。"
                f"行情时间 {quote['quote_time']}。监控运行中，{targets}。源：上金所")
    return None


def should_send_alert(state, price, threshold):
    # Persist across days and restarts: no repeat notifications while below target.
    return price <= threshold and state.get('notified_threshold') != threshold


def should_send_high_alert(state, price, threshold):
    return (threshold is not None and price >= threshold
            and state.get('notified_high_threshold') != threshold)


def send_bark(url, title, body):
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != 'https' or not parsed.netloc or not parsed.path.strip('/'):
        raise ValueError('Invalid Bark endpoint')
    # POST keeps the private device key out of URLs in error logs.
    request = urllib.request.Request(f'{parsed.scheme}://{parsed.netloc}/push',
        data=json.dumps({'device_key': parsed.path.strip('/'), 'title': title,
                         'body': body, 'group': '黄金监控'}).encode(),
        headers={'Content-Type': 'application/json'}, method='POST')
    with urllib.request.urlopen(request, timeout=15) as response:
        result = json.load(response)
    if result.get('code') != 200:
        raise ValueError('Bark did not acknowledge delivery')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--test-push', action='store_true')
    parser.add_argument('--state-path', type=Path, default=STATE_PATH)
    args = parser.parse_args()
    current = now_bjt()
    try:
        config = load_json(CONFIG_PATH)
        threshold = float(config.get('price_threshold', 835))
        if not math.isfinite(threshold) or threshold <= 0:
            raise ValueError('Invalid target price')
        high_threshold = config.get('high_price_threshold')
        if high_threshold is not None:
            high_threshold = float(high_threshold)
            if not math.isfinite(high_threshold) or high_threshold <= threshold:
                raise ValueError('High target price must exceed the low target price')
        bark_url = os.getenv('BARK_URL') or config.get('bark_url', '')
        if args.test_push:
            high_description = (f' ≥{high_threshold:g} 元/克时高位提醒一次。'
                                if high_threshold is not None else '')
            send_bark(bark_url, '黄金监控已部署',
                      f'Au99.99 ≤{threshold:g} 元/克时提醒一次；交易日09:25开盘早报。'
                      + high_description + '周末如开市照常播报；休市和其它波动不推送。此消息为部署验证。')
            print('Bark accepted test notification')
            return 0
        if not args.dry_run and not should_run(current):
            return 0
        state = load_json(args.state_path)
        try:
            quote = extract_quote(fetch_quote_payload(), current)
        except Exception as exc:
            if args.dry_run:
                print(json.dumps({'checked_at': current.isoformat(), 'threshold': threshold,
                                  'high_threshold': high_threshold,
                                  'market_session': should_run(current), 'quote_usable': False,
                                  'reason': str(exc)}, ensure_ascii=False, indent=2))
                return 0
            state.update(last_check=current.isoformat(), status='quote_unavailable',
                         last_error=type(exc).__name__)
            save_state(args.state_path, state)
            print(f'{current.isoformat()} quote unavailable ({type(exc).__name__}); no push', file=sys.stderr)
            return 1
        if args.dry_run:
            print(json.dumps(dict(quote, threshold=threshold, high_threshold=high_threshold,
                                 quote_usable=True,
                                 would_high_alert=should_send_high_alert(state, quote['price'], high_threshold),
                                 would_alert=should_send_alert(state, quote['price'], threshold)),
                             ensure_ascii=False, indent=2))
            return 0
        state.update(last_check=current.isoformat(), status='ok', last_seen=quote)
        # Alert first. Save each acknowledgement immediately, even if a later push fails.
        if should_send_alert(state, quote['price'], threshold):
            send_bark(bark_url, f'黄金已到 {threshold:g} 元/克购入价位',
                      f"{SYMBOL} 现价 {quote['price']:.2f} 元/克，已达到你设定的购入提醒条件。"
                      f"行情时间 {quote['quote_time']}。源：上金所")
            state.update(notified_threshold=threshold, last_alert_at=current.isoformat())
            save_state(args.state_path, state)
        if should_send_high_alert(state, quote['price'], high_threshold):
            send_bark(bark_url, f'黄金已到 {high_threshold:g} 元/克高位价位',
                      f"{SYMBOL} 现价 {quote['price']:.2f} 元/克，已达到你设定的高位提醒条件。"
                      f"行情时间 {quote['quote_time']}。源：上金所")
            state.update(notified_high_threshold=high_threshold, last_high_alert_at=current.isoformat())
            save_state(args.state_path, state)
        report = build_report_if_due(state, quote, current, threshold, high_threshold)
        if report:
            send_bark(bark_url, report[0], report[1])
            state['last_morning_report_date'] = current.date().isoformat()
            save_state(args.state_path, state)
        save_state(args.state_path, state)
        return 0
    except Exception as exc:
        # Never include exception URLs: Bark keys are private.
        print(f'{current.isoformat()} monitor failed ({type(exc).__name__})', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())

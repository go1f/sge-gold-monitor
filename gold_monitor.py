#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from datetime import datetime, time
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


BJT = ZoneInfo("Asia/Shanghai")
HOME_URL = "https://www.sge.com.cn/"
QUOTE_API_URL = "https://www.sge.com.cn/graph/quotations"
DEFAULT_SYMBOL = "Au99.99"
DEFAULT_DROP_THRESHOLD = 0.05
DEFAULT_PRICE_THRESHOLD = 1080.0
DEFAULT_APPROACH_RATIO = 0.02
STATE_PATH = Path(__file__).resolve().parent / ".state" / "gold_monitor_state.json"
CONFIG_PATH = Path(__file__).resolve().with_name("gold_monitor.config.json")
TIMEOUT_SECONDS = 20
MAX_FETCH_RETRIES = 6
SOURCE_LABEL = "源:上金所"
DAY_SESSION_START = time(9, 0)
DAY_SESSION_END = time(15, 0)
MORNING_REPORT_TIME = time(9, 30)
EVENING_REPORT_TIME = time(15, 0)


@dataclass
class TrendSnapshot:
    delta_15m: float | None
    delta_60m: float | None
    distance_to_target: float
    status: str


@dataclass
class Quote:
    symbol: str
    price: float
    open_price: float
    quote_time: str
    trend: TrendSnapshot
    price_threshold: float
    drop_threshold: float

    @property
    def drop_ratio(self) -> float:
        if self.open_price <= 0:
            return 0.0
        return max(0.0, (self.open_price - self.price) / self.open_price)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Monitor SGE gold spot price and push Bark alerts.")
    parser.add_argument("--dry-run", action="store_true", help="Print the current quote and exit without sending Bark.")
    parser.add_argument("--force-send", action="store_true", help="Send Bark even if the alert state has not changed.")
    parser.add_argument("--test-push", action="store_true", help="Send a test Bark notification immediately.")
    parser.add_argument("--bark-url", default=os.getenv("BARK_URL"), help="Bark endpoint.")
    parser.add_argument("--state-path", type=Path, default=STATE_PATH, help="Path to the monitor state file.")
    return parser.parse_args()


def now_bjt() -> datetime:
    return datetime.now(tz=BJT)


def should_run(current: datetime) -> bool:
    if current.weekday() >= 5:
        return False
    return DAY_SESSION_START <= current.time() <= DAY_SESSION_END


def build_quote_headers() -> dict[str, str]:
    return {
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        "Origin": "https://www.sge.com.cn",
        "Pragma": "no-cache",
        "Referer": HOME_URL,
        "sec-ch-ua": '"Google Chrome";v="122", "Chromium";v="122", "Not(A:Brand";v="24"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"macOS"',
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "same-origin",
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        ),
        "X-Requested-With": "XMLHttpRequest",
    }


def fetch_quote_payload(symbol: str = DEFAULT_SYMBOL) -> dict[str, Any]:
    last_error: str | None = None
    headers = build_quote_headers()

    for _ in range(MAX_FETCH_RETRIES):
        opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor())
        try:
            warmup = urllib.request.Request(HOME_URL, headers={"User-Agent": headers["User-Agent"]})
            with opener.open(warmup, timeout=TIMEOUT_SECONDS) as response:
                response.read()

            payload = urllib.parse.urlencode({"instid": symbol}).encode("utf-8")
            request = urllib.request.Request(QUOTE_API_URL, data=payload, headers=headers, method="POST")
            with opener.open(request, timeout=TIMEOUT_SECONDS) as response:
                text = response.read().decode("utf-8", errors="ignore")
        except urllib.error.URLError as exc:
            last_error = str(exc)
            continue

        if not text.lstrip().startswith("{"):
            last_error = "non-json response from SGE quote endpoint"
            continue

        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            last_error = f"invalid json: {exc}"

    raise ValueError(last_error or "failed to fetch SGE quote payload")


def parse_hhmm(value: str) -> time:
    return datetime.strptime(value, "%H:%M").time()


def as_float_list(values: list[Any]) -> list[float]:
    result: list[float] = []
    for value in values:
        try:
            result.append(float(value))
        except (TypeError, ValueError):
            continue
    return result


def extract_quote(
    payload: dict[str, Any],
    symbol: str = DEFAULT_SYMBOL,
    price_threshold: float = DEFAULT_PRICE_THRESHOLD,
    drop_threshold: float = DEFAULT_DROP_THRESHOLD,
    approach_ratio: float = DEFAULT_APPROACH_RATIO,
) -> Quote:
    if payload.get("heyue") != symbol:
        raise ValueError(f"Unexpected symbol in quote payload: {payload.get('heyue')}")

    times = payload.get("times") or []
    prices = as_float_list(payload.get("data") or [])
    if not times or not prices or len(times) != len(prices):
        raise ValueError("Unexpected time series format in quote payload.")

    day_prices = [
        price
        for quoted_at, price in zip(times, prices)
        if DAY_SESSION_START <= parse_hhmm(quoted_at) <= DAY_SESSION_END and price > 0
    ]
    active_prices = day_prices or [price for price in prices if price > 0]
    if not active_prices:
        raise ValueError("No valid quote prices returned by SGE.")

    current_price = active_prices[-1]
    open_price = active_prices[0]
    trend = build_trend(active_prices, current_price, price_threshold, approach_ratio)
    return Quote(
        symbol=symbol,
        price=current_price,
        open_price=open_price,
        quote_time=str(payload.get("delaystr") or now_bjt().strftime("%Y-%m-%d %H:%M:%S")),
        trend=trend,
        price_threshold=price_threshold,
        drop_threshold=drop_threshold,
    )


def build_trend(
    prices: list[float], current_price: float, price_threshold: float, approach_ratio: float
) -> TrendSnapshot:
    delta_15m = current_price - prices[-16] if len(prices) >= 16 else None
    delta_60m = current_price - prices[-61] if len(prices) >= 61 else None
    distance = current_price - price_threshold

    if distance <= 0:
        status = "已破位"
    elif delta_15m is not None and delta_60m is not None and delta_15m < 0 and delta_60m < 0:
        if current_price <= price_threshold * (1 + approach_ratio):
            status = "下行接近目标"
        else:
            status = "下行"
    elif delta_15m is not None and delta_15m < 0:
        status = "短线走弱"
    else:
        status = "暂稳"

    return TrendSnapshot(
        delta_15m=delta_15m,
        delta_60m=delta_60m,
        distance_to_target=distance,
        status=status,
    )


def clean_html(value: str) -> str:
    value = re.sub(r"<[^>]+>", "", value)
    return value.replace("&nbsp;", " ").strip()


def parse_number(value: str) -> float:
    normalized = clean_html(value).replace(",", "").strip()
    return float(normalized)


def load_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def load_config(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def get_float_config(config: dict[str, Any], key: str, default: float) -> float:
    value = config.get(key, default)
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def save_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, ensure_ascii=True, indent=2), encoding="utf-8")


def trend_summary(trend: TrendSnapshot) -> str:
    if trend.delta_15m is None or trend.delta_60m is None:
        return f"趋势:{trend.status}"
    return (
        f"趋势:{trend.status}"
        f" 15m{trend.delta_15m:+.1f}"
        f" 60m{trend.delta_60m:+.1f}"
        f" 距目标{trend.distance_to_target:+.1f}"
    )


def trading_day_key(current: datetime) -> str:
    return current.strftime("%Y-%m-%d")


def build_report_if_due(state: dict[str, Any], quote: Quote, current: datetime) -> dict[str, str] | None:
    current_time = current.time()
    day_key = trading_day_key(current)

    if current_time >= EVENING_REPORT_TIME and state.get("last_evening_report_date") != day_key:
        return {
            "kind": "evening",
            "title": f"{quote.symbol} 晚报",
            "body": (
                f"收市价 {quote.price:.2f} 元/克，开市价 {quote.open_price:.2f}。"
                f" {trend_summary(quote.trend)}。 {SOURCE_LABEL}"
            ),
        }

    if current_time >= MORNING_REPORT_TIME and state.get("last_morning_report_date") != day_key:
        return {
            "kind": "morning",
            "title": f"{quote.symbol} 早报",
            "body": (
                f"开市价 {quote.open_price:.2f} 元/克，现价 {quote.price:.2f}。"
                f" {trend_summary(quote.trend)}。 {SOURCE_LABEL}"
            ),
        }

    return None


def build_alert(quote: Quote) -> dict[str, Any] | None:
    reasons: list[str] = []
    if quote.drop_ratio > quote.drop_threshold:
        reasons.append(f"跌幅 {quote.drop_ratio * 100:.2f}%")
    if quote.price < quote.price_threshold:
        reasons.append(f"跌破 {quote.price_threshold:.0f}")
    if not reasons:
        return None

    title = f"{quote.symbol} 告警"
    body = (
        f"现价 {quote.price:.2f} 元/克，今开 {quote.open_price:.2f}。"
        f" 触发:{'；'.join(reasons)}。"
        f" {trend_summary(quote.trend)}。 {SOURCE_LABEL}"
    )
    dedupe_key = "|".join(
        [
            quote.quote_time,
            f"drop:{int(quote.drop_ratio > quote.drop_threshold)}",
            f"below:{int(quote.price < quote.price_threshold)}",
            quote.trend.status,
        ]
    )
    return {"title": title, "body": body, "dedupe_key": dedupe_key}


def should_send_alert(state: dict[str, Any], alert: dict[str, Any], force_send: bool) -> bool:
    if force_send:
        return True
    return state.get("last_alert_key") != alert["dedupe_key"]


def send_bark(bark_url: str, title: str, body: str) -> None:
    if not bark_url:
        raise ValueError("Missing Bark URL. Set BARK_URL or pass --bark-url.")
    parsed = urllib.parse.urlparse(bark_url)
    if not parsed.scheme or not parsed.netloc:
        raise ValueError("Invalid Bark URL.")

    encoded_title = urllib.parse.quote(title, safe="")
    encoded_body = urllib.parse.quote(body, safe="")
    url = bark_url.rstrip("/") + f"/{encoded_title}/{encoded_body}"

    request = urllib.request.Request(url, headers={"User-Agent": "gold-monitor/1.0"})
    with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
        response.read()


def main() -> int:
    args = parse_args()
    current = now_bjt()
    config = load_config(CONFIG_PATH)
    bark_url = args.bark_url or config.get("bark_url")
    price_threshold = get_float_config(config, "price_threshold", DEFAULT_PRICE_THRESHOLD)
    drop_threshold = get_float_config(config, "drop_threshold", DEFAULT_DROP_THRESHOLD)
    approach_ratio = get_float_config(config, "approach_ratio", DEFAULT_APPROACH_RATIO)

    if not args.dry_run and not args.test_push and not should_run(current):
        return 0

    try:
        payload = fetch_quote_payload()
        quote = extract_quote(
            payload,
            price_threshold=price_threshold,
            drop_threshold=drop_threshold,
            approach_ratio=approach_ratio,
        )
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        print(f"[gold-monitor] failed to fetch quote: {exc}", file=sys.stderr)
        return 1

    if args.test_push:
        title = "Au99.99 监控测试"
        body = (
            f"现价 {quote.price:.2f} 元/克，今开 {quote.open_price:.2f}。"
            f" 行情时间 {quote.quote_time}。"
            f" {trend_summary(quote.trend)}。 {SOURCE_LABEL}"
        )
        try:
            send_bark(bark_url, title, body)
        except (urllib.error.URLError, TimeoutError, ValueError) as exc:
            print(f"[gold-monitor] failed to send Bark: {exc}", file=sys.stderr)
            return 1
        print("[gold-monitor] test push sent")
        return 0

    state = load_state(args.state_path)
    state["last_seen"] = {
        "checked_at": current.isoformat(),
        "price": quote.price,
        "open_price": quote.open_price,
        "quote_time": quote.quote_time,
        "trend": asdict(quote.trend),
    }

    alert = build_alert(quote)
    if args.dry_run:
        print(
            json.dumps(
                {
                    "checked_at": current.isoformat(),
                    "price": quote.price,
                    "open_price": quote.open_price,
                    "drop_percent": round(quote.drop_ratio * 100, 4),
                    "quote_time": quote.quote_time,
                    "trend": asdict(quote.trend),
                    "alert": alert,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        save_state(args.state_path, state)
        return 0

    report = build_report_if_due(state, quote, current)
    if report:
        try:
            send_bark(bark_url, report["title"], report["body"])
        except (urllib.error.URLError, TimeoutError, ValueError) as exc:
            print(f"[gold-monitor] failed to send report: {exc}", file=sys.stderr)
            return 1
        if report["kind"] == "morning":
            state["last_morning_report_date"] = trading_day_key(current)
        elif report["kind"] == "evening":
            state["last_evening_report_date"] = trading_day_key(current)

    if alert and should_send_alert(state, alert, args.force_send):
        try:
            send_bark(bark_url, alert["title"], alert["body"])
        except (urllib.error.URLError, TimeoutError, ValueError) as exc:
            print(f"[gold-monitor] failed to send Bark: {exc}", file=sys.stderr)
            return 1
        state["last_alert_key"] = alert["dedupe_key"]

    save_state(args.state_path, state)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

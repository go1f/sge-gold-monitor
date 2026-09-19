# SGE Gold Monitor

Local macOS monitor for Shanghai Gold Exchange `Au99.99`, with Bark push notifications.

## Purpose

Lightweight local macOS monitor for SGE `Au99.99`, with Bark push notifications.

## Features

- polls the official SGE minute quote endpoint every 15 minutes
- watches `Au99.99`
- monitors the trading window `09:30-20:00` Beijing time
- sends a morning report after market open
- sends an evening report after market close
- alerts on configurable drop and target-price thresholds
- appends a short trend summary
- suppresses repeat alerts when the price move since the last alert is under `5`

## Configurable Values

All important thresholds are local config values instead of hardcoded constants:

- `bark_url`
- `price_threshold`
- `drop_threshold`
- `approach_ratio`
- `alert_repeat_delta`

Example:

```json
{
  "bark_url": "https://api.day.app/YOUR_BARK_KEY/",
  "price_threshold": 880,
  "drop_threshold": 0.05,
  "approach_ratio": 0.02,
  "alert_repeat_delta": 5
}
```

Use `gold_monitor.config.example.json` as the template, then create a local `gold_monitor.config.json`.

## Files

- `gold_monitor.py`
- `com.gold-monitor.plist`
- `gold_monitor.config.example.json`
- `README.md` for the primary Chinese documentation

## Operation Flow

```mermaid
flowchart LR
    A["launchd"] --> B["Fetch SGE quotes"]
    B --> C["Compute price and trend"]
    C --> D["Reports / Alerts"]
    D --> E["Bark push"]
```

## Scheduling

The LaunchAgent runs the installed `gold-monitor` entrypoint directly every 15 minutes, instead of wrapping it with `zsh`.

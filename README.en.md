# SGE gold monitor

A local macOS LaunchAgent monitors Shanghai Gold Exchange Au99.99 every 60 seconds during day and night session hours on every calendar day, including special weekend trading days.

Notifications via Bark:

- One purchase-target notification when price is **at or below CNY 835/gram**. The acknowledgement persists across restarts and days. Other fluctuations do not trigger notifications.
- One independent high-price notification at or above **CNY 880/gram**, deduplicated across days and restarts. Set `high_price_threshold` to null or omit it to disable.
- One morning report from **09:30 Asia/Shanghai**, only with fresh valid trading data; retries until 11:30. No evening reports. Weekend openings are eligible for morning reports and target alerts. Only mainland SGE quotes determine market activity; overseas holiday calendars are not used.
- Closed-market, stale, inconsistent and unavailable quotes remain silent. Closures are inferred from official quote freshness rather than a separate holiday calendar.

Copy `gold_monitor.config.example.json` to the git-ignored `gold_monitor.config.json` and set your private Bark URL. Run `python3 -m unittest -v`, `python3 gold_monitor.py --dry-run`, `python3 gold_monitor.py --test-push`, then `python3 install.py`.

The installer requires a macOS framework Python and Xcode Command Line Tools (`cc`). It resolves the current user and Python runtime automatically and compiles a native `gold-monitor` executable. The background process is named `gold-monitor`; it embeds Python in-process without spawning a `python3` child. It installs `com.gold-monitor` under `~/Library/LaunchAgents` and stores runtime files and persistent state under `~/Library/Application Support/gold-monitor`. Logs are under `~/Library/Logs/gold-monitor`. Config permissions are 600. Reinstalling preserves state.

The Mac must be awake, logged in and online. An absent morning report can also mean a source failure or offline Mac. Quotes may be delayed; a 60-second poll is not an exchange real-time feed. See [the Chinese README](README.md) for operational details.

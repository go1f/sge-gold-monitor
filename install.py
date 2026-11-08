#!/usr/bin/env python3
"""Install or update this checkout as a macOS LaunchAgent."""
import os
import plistlib
import shutil
import subprocess
import sys
from pathlib import Path

root = Path(__file__).resolve().parent
home = Path.home()
dest = home / 'Library/Application Support/gold-monitor'
logs = home / 'Library/Logs/gold-monitor'
agent = home / 'Library/LaunchAgents/com.gold-monitor.plist'
config = root / 'gold_monitor.config.json'
if not config.exists():
    raise SystemExit('Create gold_monitor.config.json before installing.')
for directory in (dest, logs, agent.parent):
    directory.mkdir(parents=True, exist_ok=True)
for filename in ('gold_monitor.py', 'gold-monitor', 'gold_monitor.config.json'):
    shutil.copy2(root / filename, dest / filename)
os.chmod(dest / 'gold_monitor.config.json', 0o600)
with (root / 'com.gold-monitor.plist').open('rb') as stream:
    spec = plistlib.load(stream)
spec.update(ProgramArguments=[sys.executable, str(dest / 'gold_monitor.py')],
            WorkingDirectory=str(dest), StandardOutPath=str(logs / 'gold_monitor.log'),
            StandardErrorPath=str(logs / 'gold_monitor.err.log'))
with agent.open('wb') as stream:
    plistlib.dump(spec, stream)
domain = f'gui/{os.getuid()}'
subprocess.run(['launchctl', 'bootout', f'{domain}/com.gold-monitor'], capture_output=True)
subprocess.run(['launchctl', 'bootstrap', domain, str(agent)], check=True)
subprocess.run(['launchctl', 'kickstart', '-k', f'{domain}/com.gold-monitor'], check=True)
print('Installed com.gold-monitor; checking every 60 seconds.')

#!/usr/bin/env python3
"""Install or update this checkout as a macOS LaunchAgent."""
import os
import plistlib
import shutil
import subprocess
import sys
import tempfile
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
for filename in ('gold_monitor.py', 'gold_monitor.config.json'):
    shutil.copy2(root / filename, dest / filename)
os.chmod(dest / 'gold_monitor.config.json', 0o600)
# Embed the existing Python runtime in a native executable. No python3 child
# process is spawned: launchd and Activity Monitor both see gold-monitor.
runtime = Path(sys.base_prefix)
library = runtime / 'Python3'
if not library.exists():
    library = runtime / 'Python'
if not library.exists():
    raise SystemExit('A macOS framework Python installation is required.')
with tempfile.TemporaryDirectory(dir=dest) as build:
    executable = Path(build) / 'gold-monitor'
    subprocess.run(['cc', '-O2', '-Wall', '-Wextra',
                    '-DPYTHON_LIBRARY=' + '"' + str(library) + '"',
                    '-DPYTHON_HOME=' + '"' + str(runtime) + '"',
                    str(root / 'gold_monitor_launcher.c'), '-o', str(executable)], check=True)
    subprocess.run([str(executable), '--help'], cwd=root, check=True,
                   stdout=subprocess.DEVNULL,
                   env=dict(os.environ, GOLD_MONITOR_SCRIPT=str(root / 'gold_monitor.py')))
    os.replace(executable, dest / 'gold-monitor')
with (root / 'com.gold-monitor.plist').open('rb') as stream:
    spec = plistlib.load(stream)
spec.update(ProgramArguments=[str(dest / 'gold-monitor')],
            WorkingDirectory=str(dest), StandardOutPath=str(logs / 'gold_monitor.log'),
            StandardErrorPath=str(logs / 'gold_monitor.err.log'))
with agent.open('wb') as stream:
    plistlib.dump(spec, stream)
domain = f'gui/{os.getuid()}'
subprocess.run(['launchctl', 'bootout', f'{domain}/com.gold-monitor'], capture_output=True)
subprocess.run(['launchctl', 'bootstrap', domain, str(agent)], check=True)
subprocess.run(['launchctl', 'kickstart', '-k', f'{domain}/com.gold-monitor'], check=True)
print('Installed com.gold-monitor; checking every 60 seconds.')

"""
Scout Watchdog - checks heartbeat and restarts if stale.
Run via Task Scheduler every 5 minutes.
"""
import json
import os
import sys
import subprocess
import time
from datetime import datetime
from pathlib import Path

STATE_DIR = Path(r"C:\Users\Matthew\Documents\claude\smart-scout\state")
SCOUT_SCRIPT = r"C:\Users\Matthew\Documents\claude\smart-scout\smart_scout.py"
HEARTBEAT_FILE = STATE_DIR / "heartbeat.json"
PID_FILE = STATE_DIR / "scout.pid"
MAX_STALE_SECONDS = 180  # 3 minutes without heartbeat = dead

def check_and_restart():
    # Check heartbeat
    if HEARTBEAT_FILE.exists():
        try:
            data = json.loads(HEARTBEAT_FILE.read_text())
            last_beat = datetime.fromisoformat(data["timestamp"])
            age = (datetime.now() - last_beat).total_seconds()
            pid = data.get("pid")

            if age < MAX_STALE_SECONDS:
                print(f"[OK] Scout alive (PID {pid}, beat {age:.0f}s ago, "
                      f"sent={data.get('send_count', 0)}, state={data.get('state', '?')})")
                return
            else:
                print(f"[!] Heartbeat stale ({age:.0f}s old)")
        except Exception as e:
            print(f"[!] Bad heartbeat: {e}")
    else:
        print("[!] No heartbeat file")

    # Kill old process
    if PID_FILE.exists():
        try:
            old_pid = int(PID_FILE.read_text().strip())
            print(f"[!] Killing stale scout PID {old_pid}...")
            os.kill(old_pid, 9)
            time.sleep(1)
        except (ProcessLookupError, OSError):
            pass
        try:
            PID_FILE.unlink()
        except Exception:
            pass

    # Restart
    print("[*] Starting fresh scout...")
    subprocess.Popen(
        [sys.executable, "-u", SCOUT_SCRIPT, "start", "--force"],
        cwd=str(Path(SCOUT_SCRIPT).parent),
        creationflags=subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS,
        stdout=open(STATE_DIR / "scout_stdout.log", "a"),
        stderr=subprocess.STDOUT
    )
    print("[OK] Scout restarted!")

if __name__ == "__main__":
    check_and_restart()

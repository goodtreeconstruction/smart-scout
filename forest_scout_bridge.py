"""
Forest Chat → Smart Scout Bridge
==================================
Polls Forest Chat for direct messages to bigc-redwood
and feeds them into Smart Scout's queue for delivery
to Claude Desktop.

Only forwards DIRECT messages (to=bigc-redwood).
Ignores to=all broadcasts.

Run: python forest_scout_bridge.py
Stop: Ctrl+C
"""

import sys
import time
import json
import requests
from datetime import datetime
from pathlib import Path

# Config
FOREST_CHAT_URL = "http://127.0.0.1:5001"
IDENTITY = "bigc-redwood"
POLL_INTERVAL = 3  # seconds

# Smart Scout queue
sys.path.insert(0, str(Path(__file__).parent))
from smart_scout import add_to_queue


def poll_and_forward():
    """Read unread direct messages and forward to Scout queue."""
    try:
        r = requests.get(
            f"{FOREST_CHAT_URL}/api/read",
            params={"for": IDENTITY, "mark_read": "true"},
            timeout=5
        )
        if r.status_code != 200:
            print(f"[Bridge] Forest Chat returned {r.status_code}")
            return 0

        messages = r.json()
        forwarded = 0

        for msg in messages:
            # ONLY forward direct messages to us, skip "all" broadcasts
            if msg.get("to") != IDENTITY:
                continue

            sender = msg.get("from", "unknown")
            content = msg.get("message", "")
            msg_id = msg.get("id", "?")

            if not content.strip():
                continue

            # Format for Claude Desktop
            scout_msg = f"[Forest Chat from {sender}] {content}"
            queue_id = add_to_queue("forest-chat", scout_msg, **{"forest_id": msg_id, "from": sender})
            print(f"[Bridge] Forwarded msg #{msg_id} from {sender} -> Scout queue ({queue_id})")
            forwarded += 1

        return forwarded

    except requests.exceptions.ConnectionError:
        print("[Bridge] Forest Chat unreachable")
        return 0
    except Exception as e:
        print(f"[Bridge] Error: {e}")
        return 0

def main():
    print(f"[Bridge] Forest Chat -> Smart Scout bridge started")
    print(f"[Bridge] Identity: {IDENTITY}")
    print(f"[Bridge] Polling every {POLL_INTERVAL}s for direct messages only")
    print(f"[Bridge] Press Ctrl+C to stop")
    print()

    while True:
        try:
            poll_and_forward()
            time.sleep(POLL_INTERVAL)
        except KeyboardInterrupt:
            print("\n[Bridge] Stopped")
            break


if __name__ == "__main__":
    main()
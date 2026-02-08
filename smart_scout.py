"""
Smart Scout V3 — Resilient UIA Message Sender
================================================
Drop-in replacement for V2 with resilience improvements.

V3 over V2:
- Single-instance PID lock (prevents duplicate scouts)
- Heartbeat file for external watchdog monitoring
- New-chat persistence (survives /new_chat, re-finds window)
- Stale window recovery (auto-reconnects if window handle dies)
- --force flag to kill old instance

Same API: get_scout(), add_to_queue(), wake(), start(), stop(), status()

Transport: pywinauto (find window, scan buttons) + pyautogui (paste, Enter)
Queue: state/queue.json (identical format to V1/V2)
Model: Event-driven — sleeps until wake(), never polls constantly.
"""

import json
import os
import sys
import atexit
import threading
import time
import uuid
import ctypes
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Optional, Any

import pyperclip
import pyautogui
from pywinauto import Desktop

# Disable pyautogui failsafe
pyautogui.FAILSAFE = False
pyautogui.PAUSE = 0.05

QUEUE_FILE = Path(r"C:\Users\Matthew\Documents\claude\smart-scout\state\queue.json")
STATE_DIR = Path(r"C:\Users\Matthew\Documents\claude\smart-scout\state")
MESSAGE_SEPARATOR = "\n\n---\n"

# Window identification
CLAUDE_WINDOW_TITLE = "claude"
CLAUDE_CLASS_NAME = "Chrome_WidgetWin_1"

# Button names to detect (flexible matching)
STOP_BUTTON_NAMES = {"Stop response", "Stop"}
SEND_BUTTON_NAMES = {"Send Message", "Send"}

# Heartbeat interval
HEARTBEAT_INTERVAL = 60


# --- Single Instance PID Lock ---

class PidLock:
    """Ensures only one scout instance runs at a time."""
    
    def __init__(self):
        self.lock_file = STATE_DIR / "scout.pid"
    
    def acquire(self) -> bool:
        if self.lock_file.exists():
            try:
                old_pid = int(self.lock_file.read_text().strip())
                try:
                    os.kill(old_pid, 0)  # Check if alive
                    print(f"[Scout V3] Another scout is running (PID {old_pid})")
                    print(f"[Scout V3] Kill it first: taskkill /F /PID {old_pid}")
                    print(f"[Scout V3] Or use: python smart_scout.py start --force")
                    return False
                except (OSError, ProcessLookupError):
                    print(f"[Scout V3] Removing stale PID file (old PID {old_pid} is dead)")
            except Exception:
                pass
        
        self.lock_file.parent.mkdir(parents=True, exist_ok=True)
        self.lock_file.write_text(str(os.getpid()))
        atexit.register(self.release)
        return True
    
    def force_acquire(self) -> bool:
        """Kill existing instance and take over."""
        if self.lock_file.exists():
            try:
                old_pid = int(self.lock_file.read_text().strip())
                print(f"[Scout V3] Force-killing PID {old_pid}...")
                os.kill(old_pid, 9)
                time.sleep(1)
            except Exception:
                pass
        return self.acquire()
    
    def release(self):
        try:
            if self.lock_file.exists():
                stored_pid = int(self.lock_file.read_text().strip())
                if stored_pid == os.getpid():
                    self.lock_file.unlink()
        except Exception:
            pass


class ScoutService:
    """
    Event-driven message sender for Claude Desktop via UIA.
    V3: Adds heartbeat, new-chat persistence, stale window recovery.
    """

    def __init__(self):
        self._wake_event = threading.Event()
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._new_chat_requested = threading.Event()
        self._cached_window = None
        self._window_title = None
        self.heartbeat_file = STATE_DIR / "heartbeat.json"

        # Status tracking
        self.running = False
        self.last_sent: Optional[str] = None
        self.last_error: Optional[str] = None
        self.send_count = 0

    # ─────────────────────────────────────────────────────────────
    # Public API
    # ─────────────────────────────────────────────────────────────

    def start(self):
        """Start the scout background thread."""
        if self._thread and self._thread.is_alive():
            print("[Scout V3] Already running")
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()
        self.running = True
        print("[Scout V3] Started (UIA backend, event-driven, resilient)")

    def stop(self):
        """Stop the scout gracefully."""
        self._stop_event.set()
        self._wake_event.set()
        if self._thread:
            self._thread.join(timeout=3)
        self.running = False
        print("[Scout V3] Stopped")

    def wake(self):
        """Wake the scout to check for pending messages."""
        self._wake_event.set()

    def check_ready(self) -> Dict[str, Any]:
        """Check if Claude is ready to receive messages."""
        win = self._find_claude_window()
        if not win:
            return {"window_found": False, "stop_button": False, "ready": False}
        buttons = self._scan_buttons(win)
        ready = win is not None and not buttons["stop_button"]
        return {"window_found": True, **buttons, "ready": ready}

    # ─────────────────────────────────────────────────────────────
    # Heartbeat
    # ─────────────────────────────────────────────────────────────

    def write_heartbeat(self, state="idle", pending=0):
        """Write heartbeat for watchdog monitoring."""
        try:
            data = {
                "pid": os.getpid(),
                "timestamp": datetime.now().isoformat(),
                "state": state,
                "pending_messages": pending,
                "send_count": self.send_count,
                "last_sent": self.last_sent,
                "last_error": self.last_error,
                "alive": True
            }
            self.heartbeat_file.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except Exception:
            pass

    # ─────────────────────────────────────────────────────────────
    # Queue Management
    # ─────────────────────────────────────────────────────────────

    def get_pending_messages(self) -> List[Dict]:
        """Load pending messages from queue file."""
        if not QUEUE_FILE.exists():
            return []
        try:
            with self._lock:
                with open(QUEUE_FILE, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                return [m for m in data.get("pending", []) if m.get("status") == "pending"]
        except Exception as e:
            self.last_error = f"Queue read error: {e}"
            return []

    def mark_sent(self, msg_ids: List[str]):
        """Mark messages as sent in queue file."""
        try:
            with self._lock:
                with open(QUEUE_FILE, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                for msg in data.get("pending", []):
                    if msg.get("id") in msg_ids:
                        msg["status"] = "sent"
                        msg["sent_at"] = datetime.now().isoformat()
                processed = [m for m in data.get("pending", []) if m.get("status") == "sent"]
                data["pending"] = [m for m in data.get("pending", []) if m.get("status") != "sent"]
                if "processed" not in data:
                    data["processed"] = []
                data["processed"].extend(processed)
                data["processed"] = data["processed"][-50:]
                data["updated_at"] = datetime.now().isoformat()
                with open(QUEUE_FILE, 'w', encoding='utf-8') as f:
                    json.dump(data, f, indent=2)
        except Exception as e:
            self.last_error = f"Queue update error: {e}"

    def build_message_text(self, messages: List[Dict]) -> str:
        """Combine multiple messages into one paste-ready string."""
        texts = [m.get("content", "") for m in messages if m.get("content", "").strip()]
        return MESSAGE_SEPARATOR.join(texts) if texts else ""

    # ─────────────────────────────────────────────────────────────
    # UIA Operations — Window Management
    # ─────────────────────────────────────────────────────────────

    def _find_claude_window(self):
        """Find Claude Desktop window via UIA. Caches result, validates before reuse."""
        # Try cached window first
        if self._cached_window:
            try:
                title = self._cached_window.window_text()
                if title:  # Window still valid
                    return self._cached_window
            except Exception:
                self._cached_window = None
                self._window_title = None
        
        # Fresh search
        try:
            desktop = Desktop(backend="uia")
            for w in desktop.windows():
                title = w.window_text().lower().strip()
                if title == "claude" or (
                    "claude" in title and w.class_name() == CLAUDE_CLASS_NAME
                ):
                    self._cached_window = w
                    self._window_title = w.window_text()
                    return w
        except Exception as e:
            self.last_error = f"Window search error: {e}"
        return None

    def _invalidate_window(self):
        """Force re-find on next call (after new_chat, chat switch, etc)."""
        self._cached_window = None
        self._window_title = None

    # ─────────────────────────────────────────────────────────────
    # UIA Operations — Button & Input Detection
    # ─────────────────────────────────────────────────────────────

    def _scan_buttons(self, win) -> Dict[str, bool]:
        """Single scan of all buttons. Returns presence flags."""
        has_stop = False
        has_send = False
        try:
            buttons = win.descendants(control_type="Button")
            for btn in buttons:
                try:
                    name = btn.element_info.name or ""
                    name_lower = name.lower()
                    if any(s.lower() in name_lower for s in STOP_BUTTON_NAMES):
                        has_stop = True
                    if any(s.lower() in name_lower for s in SEND_BUTTON_NAMES):
                        has_send = True
                except Exception:
                    continue
        except Exception as e:
            self.last_error = f"Button scan error: {e}"
        return {"stop_button": has_stop, "send_button": has_send}

    def _has_stop_button(self, win) -> bool:
        return self._scan_buttons(win)["stop_button"]

    def _find_input_element(self, win):
        """Find the text input area in Claude."""
        try:
            edits = win.descendants(control_type="Edit")
            for e in edits:
                try:
                    name = e.element_info.name or ""
                    if "prompt" in name.lower() or "reply" in name.lower() or "write" in name.lower():
                        return e
                except Exception:
                    continue
            if edits:
                return edits[-1]
        except Exception as e:
            self.last_error = f"Input search error: {e}"
        return None

    # ─────────────────────────────────────────────────────────────
    # UIA Operations — Focus, Paste, Send
    # ─────────────────────────────────────────────────────────────

    def _get_foreground_hwnd(self) -> int:
        """Get handle of currently focused window."""
        try:
            return ctypes.windll.user32.GetForegroundWindow()
        except Exception:
            return 0

    def _restore_foreground(self, hwnd: int):
        """Restore a window to foreground by handle."""
        if hwnd:
            try:
                ctypes.windll.user32.SetForegroundWindow(hwnd)
            except Exception:
                pass

    def _focus_and_paste(self, win, text: str) -> bool:
        """Focus Claude window, find input, clear it, paste text."""
        try:
            win.set_focus()
            time.sleep(0.3)
            inp = self._find_input_element(win)
            if not inp:
                self.last_error = "Input element not found"
                return False
            try:
                inp.click_input()
                time.sleep(0.2)
            except Exception:
                pass
            pyautogui.hotkey('ctrl', 'a')
            time.sleep(0.1)
            pyautogui.press('delete')
            time.sleep(0.1)
            pyperclip.copy(text)
            pyautogui.hotkey('ctrl', 'v')
            time.sleep(0.2)
            return True
        except Exception as e:
            self.last_error = f"Focus/paste error: {e}"
            return False

    def _send_enter(self) -> bool:
        """Press Enter to send the message."""
        try:
            pyautogui.press('enter')
            return True
        except Exception as e:
            self.last_error = f"Enter key error: {e}"
            return False

    def _restore_window(self, prev_hwnd: int):
        """Restore previously focused window after sending."""
        if prev_hwnd:
            time.sleep(0.3)
            self._restore_foreground(prev_hwnd)

    # ─────────────────────────────────────────────────────────────
    # Slash Commands — /new_chat with persistence
    # ─────────────────────────────────────────────────────────────

    def new_chat(self) -> bool:
        """Click 'New chat' in Claude's sidebar. Maintains scout connection afterward."""
        win = self._find_claude_window()
        if not win:
            self.last_error = "Claude window not found"
            print("[Scout V3] X new_chat: Claude window not found")
            return False

        prev_hwnd = self._get_foreground_hwnd()

        try:
            win.set_focus()
            time.sleep(0.3)

            found = False
            for el in win.descendants():
                try:
                    name = (el.element_info.name or "").strip()
                    ct = el.element_info.control_type
                    if name == "New chat" and ct in ("Hyperlink", "Button", "Text"):
                        print(f"[Scout V3] Found '{name}' ({ct}), clicking...")
                        el.click_input()
                        found = True
                        break
                except Exception:
                    continue

            if not found:
                print("[Scout V3] 'New chat' element not found, trying Ctrl+N...")
                pyautogui.hotkey('ctrl', 'n')
                found = True

            time.sleep(1.5)  # Wait for new chat to fully load

            # Persistence: invalidate cached window handle so next operation
            # re-finds the window with fresh state
            self._invalidate_window()
            
            # Signal the run loop that a new chat happened
            self._new_chat_requested.set()

            # Verify we can still find the window
            new_win = self._find_claude_window()
            if new_win:
                print(f"[Scout V3] New chat started, window re-acquired: '{new_win.window_text()}'")
            else:
                print("[Scout V3] New chat started but window temporarily lost (will re-find on next cycle)")

            return found

        except Exception as e:
            self.last_error = f"new_chat error: {e}"
            print(f"[Scout V3] X new_chat error: {e}")
            return False
        finally:
            self._restore_window(prev_hwnd)

    # ─────────────────────────────────────────────────────────────
    # Main Event Loop (V3 — with heartbeat + new chat persistence)
    # ─────────────────────────────────────────────────────────────

    def _run_loop(self):
        """
        Main scout loop (runs in background thread).

        1. Sleep until woken (or 5s fallback tick)
        2. Write heartbeat periodically
        3. Handle new_chat transitions (re-find window)
        4. Check for pending messages — if none, back to sleep
        5. Find Claude window (with stale-handle recovery)
        6. Wait for ready (no Stop button) — max 60s
        7. Focus, paste combined text, press Enter
        8. Mark sent, restore previous window
        9. Back to sleep
        """
        print("[Scout V3] Loop started")
        last_heartbeat = 0

        while not self._stop_event.is_set():
            try:
                # Sleep until woken or 5s fallback
                self._wake_event.wait(timeout=5)
                self._wake_event.clear()

                if self._stop_event.is_set():
                    break

                # --- Heartbeat ---
                now = time.time()
                if now - last_heartbeat >= HEARTBEAT_INTERVAL:
                    pending_count = len(self.get_pending_messages())
                    self.write_heartbeat(state="running", pending=pending_count)
                    last_heartbeat = now

                # --- New chat transition ---
                if self._new_chat_requested.is_set():
                    self._new_chat_requested.clear()
                    print("[Scout V3] New chat detected, re-acquiring window...")
                    self._invalidate_window()
                    time.sleep(1)
                    win = self._find_claude_window()
                    if win:
                        print(f"[Scout V3] Window re-acquired after new chat")
                    else:
                        print("[Scout V3] Window lost after new chat, will retry...")
                    continue

                # Brief settle for burst queue additions
                time.sleep(0.5)

                # Get pending messages
                pending = self.get_pending_messages()
                if not pending:
                    continue

                print(f"[Scout V3] {len(pending)} pending message(s)")

                combined_text = self.build_message_text(pending)
                if not combined_text.strip():
                    continue

                # Find Claude window (with cache validation)
                win = self._find_claude_window()
                if not win:
                    self.last_error = "Claude window not found"
                    print("[Scout V3] X Claude window not found, will retry on next wake")
                    self._invalidate_window()
                    continue

                # Wait for ready (no Stop button = not streaming)
                ready_timeout = 60
                ready_start = time.time()
                print("[Scout V3] Waiting for Claude to be ready...")

                while time.time() - ready_start < ready_timeout:
                    if self._stop_event.is_set():
                        break
                    if not self._has_stop_button(win):
                        break
                    time.sleep(1.5)

                if self._stop_event.is_set():
                    break

                # Re-check — if still streaming, try re-finding window
                # (might have switched chats)
                if self._has_stop_button(win):
                    print(f"[Scout V3] Still streaming after {ready_timeout}s, re-finding window...")
                    self._invalidate_window()
                    win = self._find_claude_window()
                    if win and not self._has_stop_button(win):
                        print("[Scout V3] Window recovered (maybe chat switched)")
                    else:
                        self.last_error = f"Still streaming after {ready_timeout}s timeout"
                        print(f"[Scout V3] X Claude still streaming, skipping this cycle")
                        continue

                # Save foreground window to restore after
                prev_hwnd = self._get_foreground_hwnd()

                # Focus Claude, clear input, paste combined text
                print(f"[Scout V3] Pasting {len(combined_text)} chars...")
                if not self._focus_and_paste(win, combined_text):
                    print(f"[Scout V3] X Paste failed: {self.last_error}")
                    # Maybe window went stale — invalidate for next try
                    self._invalidate_window()
                    continue

                time.sleep(0.3)

                # Send!
                if self._send_enter():
                    ids = [p.get("id") for p in pending if p.get("id")]
                    self.mark_sent(ids)
                    self.send_count += 1
                    self.last_sent = datetime.now().isoformat()
                    print(f"[Scout V3] OK Sent {len(pending)} message(s)")
                    self.write_heartbeat(state="sent", pending=0)
                else:
                    print(f"[Scout V3] X Send failed: {self.last_error}")

                self._restore_window(prev_hwnd)
                time.sleep(1)

            except Exception as e:
                self.last_error = str(e)
                print(f"[Scout V3] Error: {e}")
                self._invalidate_window()  # Reset on any error
                time.sleep(2)


# ─────────────────────────────────────────────────────────────────
# Singleton
# ─────────────────────────────────────────────────────────────────

_scout_instance: Optional[ScoutService] = None
_scout_lock = threading.Lock()


def get_scout() -> ScoutService:
    """Get singleton ScoutService instance."""
    global _scout_instance
    if _scout_instance is None:
        with _scout_lock:
            if _scout_instance is None:
                _scout_instance = ScoutService()
    return _scout_instance


# ─────────────────────────────────────────────────────────────────
# Queue Helper (for other modules to add messages)
# ─────────────────────────────────────────────────────────────────

def add_to_queue(msg_type: str, content: str, **kwargs) -> str:
    """Add a message to the queue and wake scout."""
    QUEUE_FILE.parent.mkdir(parents=True, exist_ok=True)

    msg_id = f"{msg_type}-{datetime.now().strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:6]}"

    message = {
        "id": msg_id,
        "type": msg_type,
        "content": content,
        "timestamp": datetime.now().isoformat(),
        "status": "pending",
        **kwargs
    }

    if QUEUE_FILE.exists():
        try:
            with open(QUEUE_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
        except Exception:
            data = {"pending": [], "processed": []}
    else:
        data = {"pending": [], "processed": []}

    data["pending"].append(message)
    data["updated_at"] = datetime.now().isoformat()

    with open(QUEUE_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2)

    scout = get_scout()
    if scout.running:
        scout.wake()

    return msg_id


# ─────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Smart Scout V3 — Resilient UIA Backend")
        print()
        print("Usage:")
        print("  python smart_scout.py start [--force]  - Run scout (Ctrl+C to stop)")
        print("  python smart_scout.py status            - Show readiness status")
        print("  python smart_scout.py test              - Test paste into Claude input")
        print("  python smart_scout.py send <msg>        - Paste and send a message")
        print("  python smart_scout.py queue <msg>       - Add message to queue")
        print("  python smart_scout.py new_chat / new    - Start a new chat")
        print("  python smart_scout.py window            - Test window discovery")
        sys.exit(1)

    cmd = sys.argv[1]

    # Commands that need the PID lock
    if cmd == "start":
        lock = PidLock()
        force = "--force" in sys.argv
        if force:
            if not lock.force_acquire():
                sys.exit(1)
        else:
            if not lock.acquire():
                sys.exit(1)
        
        print(f"[Scout V3] PID lock acquired (PID {os.getpid()})")
        scout = get_scout()
        scout.start()
        print("Press Ctrl+C to stop...")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            scout.stop()

    elif cmd == "status":
        scout = get_scout()
        print("Checking Claude readiness...")
        result = scout.check_ready()
        print(json.dumps(result, indent=2))
        # Also show heartbeat if available
        hb = STATE_DIR / "heartbeat.json"
        if hb.exists():
            try:
                data = json.loads(hb.read_text())
                print(f"\nHeartbeat: PID {data.get('pid')}, state={data.get('state')}, "
                      f"sends={data.get('send_count')}, last={data.get('last_sent', 'never')}")
            except Exception:
                pass

    elif cmd == "window":
        scout = get_scout()
        print("Searching for Claude window...")
        win = scout._find_claude_window()
        if win:
            print(f"OK Found: '{win.window_text()}' (class: {win.class_name()})")
            rect = win.rectangle()
            print(f"  Position: ({rect.left}, {rect.top}) Size: {rect.width()}x{rect.height()}")
            buttons = scout._scan_buttons(win)
            print(f"  Stop button: {buttons['stop_button']}")
            print(f"  Send button: {buttons['send_button']}")
            inp = scout._find_input_element(win)
            if inp:
                print(f"  Input found: '{inp.element_info.name}'")
            else:
                print("  Input: NOT FOUND")
        else:
            print("X Claude window not found")

    elif cmd == "test":
        scout = get_scout()
        print("Testing paste into Claude input (will NOT send)...")
        win = scout._find_claude_window()
        if not win:
            print("X Claude window not found")
            sys.exit(1)
        test_text = "TEST MESSAGE FROM SCOUT V3 — This was pasted via UIA, not CDP!"
        prev_hwnd = scout._get_foreground_hwnd()
        result = scout._focus_and_paste(win, test_text)
        print(f"Paste result: {result}")
        if result:
            print("OK Text pasted into input. Check Claude window — it should NOT have sent.")
        scout._restore_window(prev_hwnd)

    elif cmd == "send":
        if len(sys.argv) < 3:
            print("Usage: python smart_scout.py send <message>")
            sys.exit(1)
        scout = get_scout()
        msg = " ".join(sys.argv[2:])
        print(f"Sending: '{msg[:80]}...'")
        win = scout._find_claude_window()
        if not win:
            print("X Claude window not found")
            sys.exit(1)
        if scout._has_stop_button(win):
            print("X Claude is currently streaming — wait for it to finish")
            sys.exit(1)
        prev_hwnd = scout._get_foreground_hwnd()
        if scout._focus_and_paste(win, msg):
            time.sleep(0.3)
            if scout._send_enter():
                print("OK Message sent!")
            else:
                print("X Enter key failed")
        else:
            print(f"X Paste failed: {scout.last_error}")
        scout._restore_window(prev_hwnd)

    elif cmd == "queue":
        if len(sys.argv) < 3:
            print("Usage: python smart_scout.py queue <message>")
            sys.exit(1)
        msg = " ".join(sys.argv[2:])
        msg_id = add_to_queue("cypress", msg)
        print(f"Queued: {msg_id}")
        print("(Start scout with 'start' to process queue)")

    elif cmd in ("new_chat", "new", "newchat"):
        scout = get_scout()
        print("Starting new chat...")
        result = scout.new_chat()
        if result:
            print("OK New chat started!")
        else:
            print(f"X Failed: {scout.last_error}")

    else:
        print(f"Unknown command: {cmd}")

"""
Smart Scout V2 — No Debug Port
================================
Drop-in replacement for claude_scout.py using Windows UI Automation + pyautogui.
No CDP, no port 9222, no special Claude launch args needed.

Same API: get_scout(), add_to_queue(), wake(), start(), stop(), status()

Transport: pywinauto (find window, scan buttons) + pyautogui (paste, Enter)
Queue: state/queue.json (identical format to V1)
Model: Event-driven — sleeps until wake(), never polls constantly.
"""

import json
import os
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

# Disable pyautogui failsafe (mouse-to-corner abort) — we control focus carefully
pyautogui.FAILSAFE = False
# Speed up pyautogui pauses
pyautogui.PAUSE = 0.05

QUEUE_FILE = Path(r"C:\Users\Matthew\Documents\claude\smart-scout\state\queue.json")
MESSAGE_SEPARATOR = "\n\n---\n"

# Window identification
CLAUDE_WINDOW_TITLE = "claude"
CLAUDE_CLASS_NAME = "Chrome_WidgetWin_1"

# Button names to detect (flexible matching)
STOP_BUTTON_NAMES = {"Stop response", "Stop"}
SEND_BUTTON_NAMES = {"Send Message", "Send"}


class ScoutService:
    """
    Event-driven message sender for Claude Desktop via UIA.

    Usage:
        scout = ScoutService()
        scout.start()  # Starts background thread
        scout.wake()   # Call when message added to queue
        scout.stop()   # Graceful shutdown
    """

    def __init__(self):
        self._wake_event = threading.Event()
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()

        # Status tracking
        self.running = False
        self.last_sent: Optional[str] = None
        self.last_error: Optional[str] = None
        self.send_count = 0

    # ─────────────────────────────────────────────────────────────
    # Public API (identical to V1)
    # ─────────────────────────────────────────────────────────────

    def start(self):
        """Start the scout background thread."""
        if self._thread and self._thread.is_alive():
            print("[Scout V2] Already running")
            return

        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()
        self.running = True
        print("[Scout V2] Started (UIA backend, event-driven)")

    def stop(self):
        """Stop the scout gracefully."""
        self._stop_event.set()
        self._wake_event.set()  # Unblock if sleeping
        if self._thread:
            self._thread.join(timeout=3)
        self.running = False
        print("[Scout V2] Stopped")

    def wake(self):
        """Wake the scout to check for pending messages."""
        self._wake_event.set()

    def status(self) -> Dict[str, Any]:
        """Get current scout status."""
        pending = self.get_pending_messages()
        return {
            "running": self.running,
            "backend": "uia",
            "pending_count": len(pending),
            "last_sent": self.last_sent,
            "last_error": self.last_error,
            "send_count": self.send_count
        }
    # ─────────────────────────────────────────────────────────────
    # Queue Operations (identical to V1 — file-based, no CDP)
    # ─────────────────────────────────────────────────────────────

    def get_pending_messages(self) -> List[Dict]:
        """Get all pending messages from queue."""
        if not QUEUE_FILE.exists():
            return []
        try:
            with open(QUEUE_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
            return [p for p in data.get("pending", []) if p.get("status") != "sent"]
        except Exception as e:
            self.last_error = f"Queue read error: {e}"
            return []

    def mark_sent(self, ids: List[str]):
        """Move messages from pending to processed."""
        if not QUEUE_FILE.exists():
            return

        with self._lock:
            try:
                with open(QUEUE_FILE, 'r', encoding='utf-8') as f:
                    data = json.load(f)

                pending = data.get("pending", [])
                processed = data.get("processed", [])

                new_pending = []
                for p in pending:
                    if p.get("id") in ids:
                        p["sent_at"] = datetime.now().isoformat()
                        p["status"] = "sent"
                        processed.append(p)
                    else:
                        new_pending.append(p)

                data["pending"] = new_pending
                data["processed"] = processed[-100:]  # Keep last 100
                data["updated_at"] = datetime.now().isoformat()

                with open(QUEUE_FILE, 'w', encoding='utf-8') as f:
                    json.dump(data, f, indent=2)

            except Exception as e:
                self.last_error = f"Mark sent error: {e}"

    def build_message_text(self, messages: List[Dict]) -> str:
        """Build combined message text from pending messages."""
        lines = []

        for msg in messages:
            msg_type = msg.get("type", "unknown")

            if msg_type == "cypress":
                lines.append(msg.get("content", ""))

            elif msg_type == "ping":
                if msg.get("from") or msg.get("ping_type") or msg.get("task_id"):
                    from_agent = msg.get("from", "?")
                    ping_type = msg.get("ping_type", "STATUS")
                    task_id = msg.get("task_id", "")
                    lines.append(f"AGENT PING: {from_agent} {ping_type} task:{task_id}")
                else:
                    lines.append(msg.get("content", "AGENT PING: ? STATUS task:"))

            else:
                content = msg.get("content") or msg.get("message") or str(msg)
                lines.append(content)

        return MESSAGE_SEPARATOR.join(lines)
    # ─────────────────────────────────────────────────────────────
    # UIA Operations — Window Discovery
    # ─────────────────────────────────────────────────────────────

    def _find_claude_window(self):
        """Find Claude Desktop window via UIA. Returns pywinauto wrapper or None."""
        try:
            desktop = Desktop(backend="uia")
            for w in desktop.windows():
                title = w.window_text().lower().strip()
                if title == "claude" or (
                    "claude" in title and w.class_name() == CLAUDE_CLASS_NAME
                ):
                    return w
        except Exception as e:
            self.last_error = f"Window search error: {e}"
        return None

    # ─────────────────────────────────────────────────────────────
    # UIA Operations — Readiness Detection
    # ─────────────────────────────────────────────────────────────

    def _scan_buttons(self, win) -> Dict[str, bool]:
        """
        Single scan of all buttons. Returns presence flags.
        One scan serves both Stop detection and Send detection.
        """
        has_stop = False
        has_send = False
        try:
            buttons = win.descendants(control_type="Button")
            for btn in buttons:
                try:
                    name = btn.element_info.name or ""
                    # Flexible matching — contains, not exact
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

    def check_ready(self) -> Dict[str, bool]:
        """Check if Claude is ready to receive a message."""
        win = self._find_claude_window()
        if not win:
            return {"window_found": False, "stop_button": False, "send_button": False, "ready": False}

        buttons = self._scan_buttons(win)
        ready = not buttons["stop_button"]  # Ready if not currently streaming
        return {
            "window_found": True,
            "stop_button": buttons["stop_button"],
            "send_button": buttons["send_button"],
            "ready": ready
        }

    def _has_stop_button(self, win) -> bool:
        """Quick check: is Claude currently streaming?"""
        return self._scan_buttons(win)["stop_button"]
    # ─────────────────────────────────────────────────────────────
    # UIA Operations — Text Input & Send
    # ─────────────────────────────────────────────────────────────

    def _get_foreground_hwnd(self) -> int:
        """Get current foreground window handle (to restore later)."""
        try:
            return ctypes.windll.user32.GetForegroundWindow()
        except Exception:
            return 0

    def _restore_foreground(self, hwnd: int):
        """Restore a previously active window to foreground."""
        if hwnd:
            try:
                ctypes.windll.user32.SetForegroundWindow(hwnd)
            except Exception:
                pass

    def _find_input_element(self, win):
        """Find Claude's input box (Edit control) via UIA."""
        try:
            edits = win.descendants(control_type="Edit")
            for edit in edits:
                try:
                    name = (edit.element_info.name or "").lower()
                    # Claude's input has placeholder text like "Reply..." or "Write your prompt"
                    if any(hint in name for hint in ["reply", "write", "prompt", "message"]):
                        return edit
                except Exception:
                    continue
            # Fallback: last Edit control (usually the input box)
            if edits:
                return edits[-1]
        except Exception as e:
            self.last_error = f"Input search error: {e}"
        return None

    def _focus_and_paste(self, win, text: str) -> bool:
        """
        Focus Claude window, click input area, clear it, paste text.
        Preserves and restores clipboard.
        """
        prev_hwnd = self._get_foreground_hwnd()
        prev_clipboard = None

        try:
            # Save clipboard
            try:
                prev_clipboard = pyperclip.paste()
            except Exception:
                prev_clipboard = None

            # Focus Claude window
            try:
                win.set_focus()
                time.sleep(0.2)
            except Exception as e:
                self.last_error = f"Focus error: {e}"
                return False

            # Find and click the input element
            input_el = self._find_input_element(win)
            if not input_el:
                self.last_error = "Could not find input element"
                return False

            try:
                input_el.click_input()
                time.sleep(0.15)
            except Exception:
                # Fallback: click center of input rectangle
                try:
                    rect = input_el.rectangle()
                    cx = (rect.left + rect.right) // 2
                    cy = (rect.top + rect.bottom) // 2
                    pyautogui.click(cx, cy)
                    time.sleep(0.15)
                except Exception as e:
                    self.last_error = f"Click input error: {e}"
                    return False

            # Select all + delete (clear existing input)
            pyautogui.hotkey('ctrl', 'a')
            time.sleep(0.05)
            pyautogui.press('delete')
            time.sleep(0.05)

            # Copy text to clipboard and paste
            pyperclip.copy(text)
            time.sleep(0.05)
            pyautogui.hotkey('ctrl', 'v')
            time.sleep(0.2)

            return True

        finally:
            # Restore clipboard
            if prev_clipboard is not None:
                try:
                    time.sleep(0.1)
                    pyperclip.copy(prev_clipboard)
                except Exception:
                    pass

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
    # Main Event Loop
    # ─────────────────────────────────────────────────────────────

    def _run_loop(self):
        """
        Main scout loop (runs in background thread).

        1. Sleep until woken (or 5s fallback tick)
        2. Brief settle delay for burst queue additions
        3. Check for pending messages — if none, back to sleep
        4. Find Claude window
        5. Wait for ready (no Stop button) — max 60s
        6. Focus, paste combined text, press Enter
        7. Mark sent, restore previous window
        8. Back to sleep
        """
        print("[Scout V2] Loop started")

        while not self._stop_event.is_set():
            try:
                # Sleep until woken or 5s fallback
                self._wake_event.wait(timeout=5)
                self._wake_event.clear()

                if self._stop_event.is_set():
                    break

                # Brief settle for burst queue additions (0.5s)
                time.sleep(0.5)

                # Get pending messages
                pending = self.get_pending_messages()
                if not pending:
                    continue

                print(f"[Scout V2] {len(pending)} pending message(s)")

                # Build combined message text (all pending stacked)
                combined_text = self.build_message_text(pending)
                if not combined_text.strip():
                    continue

                # Find Claude window
                win = self._find_claude_window()
                if not win:
                    self.last_error = "Claude window not found"
                    print("[Scout V2] X Claude window not found, will retry on next wake")
                    continue

                # Wait for ready (no Stop button = not streaming)
                ready_timeout = 60
                ready_start = time.time()
                print("[Scout V2] Waiting for Claude to be ready...")

                while time.time() - ready_start < ready_timeout:
                    if self._stop_event.is_set():
                        break

                    if not self._has_stop_button(win):
                        break

                    time.sleep(1.5)  # UIA scan takes ~1s, so 1.5s poll is reasonable

                if self._stop_event.is_set():
                    break

                # Re-check after wait
                if self._has_stop_button(win):
                    self.last_error = f"Still streaming after {ready_timeout}s timeout"
                    print(f"[Scout V2] X Claude still streaming after {ready_timeout}s, skipping")
                    continue

                # Save foreground window to restore after
                prev_hwnd = self._get_foreground_hwnd()

                # Focus Claude, clear input, paste combined text
                print(f"[Scout V2] Pasting {len(combined_text)} chars...")
                if not self._focus_and_paste(win, combined_text):
                    print(f"[Scout V2] X Paste failed: {self.last_error}")
                    continue

                # Brief pause for DOM to register the paste
                time.sleep(0.3)

                # Send!
                if self._send_enter():
                    ids = [p.get("id") for p in pending if p.get("id")]
                    self.mark_sent(ids)
                    self.send_count += 1
                    self.last_sent = datetime.now().isoformat()
                    print(f"[Scout V2] OK Sent {len(pending)} message(s)")
                else:
                    print(f"[Scout V2] X Send failed: {self.last_error}")

                # Restore previous foreground window
                self._restore_window(prev_hwnd)

                # Brief cooldown after sending
                time.sleep(1)

            except Exception as e:
                self.last_error = str(e)
                print(f"[Scout V2] Error: {e}")
                time.sleep(2)

        print("[Scout V2] Loop ended")

# ─────────────────────────────────────────────────────────────────
# Singleton Instance
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
    """
    Add a message to the queue and wake scout.

    Args:
        msg_type: "cypress" or "ping"
        content: Message content
        **kwargs: Additional fields (from, ping_type, task_id, etc.)

    Returns:
        Message ID
    """
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

    # Load existing queue
    if QUEUE_FILE.exists():
        try:
            with open(QUEUE_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
        except Exception:
            data = {"pending": [], "processed": []}
    else:
        data = {"pending": [], "processed": []}

    # Add message
    data["pending"].append(message)
    data["updated_at"] = datetime.now().isoformat()

    # Save
    with open(QUEUE_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2)

    # Wake scout
    scout = get_scout()
    if scout.running:
        scout.wake()

    return msg_id


# ─────────────────────────────────────────────────────────────────
# CLI for testing
# ─────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Smart Scout V2 — UIA Backend (no debug port)")
        print()
        print("Usage:")
        print("  python claude_scout_v2.py start      - Run scout (Ctrl+C to stop)")
        print("  python claude_scout_v2.py status     - Show readiness status")
        print("  python claude_scout_v2.py test       - Test paste into Claude input")
        print("  python claude_scout_v2.py send <msg> - Paste and send a message")
        print("  python claude_scout_v2.py queue <msg> - Add message to queue")
        print("  python claude_scout_v2.py window     - Test window discovery")
        sys.exit(1)

    cmd = sys.argv[1]
    scout = get_scout()

    if cmd == "start":
        scout.start()
        print("Press Ctrl+C to stop...")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            scout.stop()

    elif cmd == "status":
        print("Checking Claude readiness...")
        result = scout.check_ready()
        print(json.dumps(result, indent=2))

    elif cmd == "window":
        print("Searching for Claude window...")
        win = scout._find_claude_window()
        if win:
            print(f"OK Found: '{win.window_text()}' (class: {win.class_name()})")
            rect = win.rectangle()
            print(f"  Position: ({rect.left}, {rect.top}) Size: {rect.width()}x{rect.height()}")
            # Quick button scan
            buttons = scout._scan_buttons(win)
            print(f"  Stop button: {buttons['stop_button']}")
            print(f"  Send button: {buttons['send_button']}")
            # Find input
            inp = scout._find_input_element(win)
            if inp:
                print(f"  Input found: '{inp.element_info.name}'")
            else:
                print("  Input: NOT FOUND")
        else:
            print("X Claude window not found")

    elif cmd == "test":
        print("Testing paste into Claude input (will NOT send)...")
        win = scout._find_claude_window()
        if not win:
            print("X Claude window not found")
            sys.exit(1)
        test_text = "TEST MESSAGE FROM SCOUT V2 — This was pasted via UIA, not CDP!"
        prev_hwnd = scout._get_foreground_hwnd()
        result = scout._focus_and_paste(win, test_text)
        print(f"Paste result: {result}")
        if result:
            print("OK Text pasted into input. Check Claude window — it should NOT have sent.")
        scout._restore_window(prev_hwnd)

    elif cmd == "send":
        if len(sys.argv) < 3:
            print("Usage: python claude_scout_v2.py send <message>")
            sys.exit(1)
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
            print("Usage: python claude_scout_v2.py queue <message>")
            sys.exit(1)
        msg = " ".join(sys.argv[2:])
        msg_id = add_to_queue("cypress", msg)
        print(f"Queued: {msg_id}")
        print("(Start scout with 'start' to process queue)")

    else:
        print(f"Unknown command: {cmd}")
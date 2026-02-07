# Smart Scout V2

Send messages to Claude Desktop via Windows UI Automation. **No debug port required.**

## What it does

Smart Scout watches a message queue (`queue.json`) and delivers messages to Claude Desktop by:
1. Finding the Claude window via pywinauto (UIA backend)
2. Waiting for Claude to finish streaming (Stop button detection)
3. Focusing the window, pasting text via clipboard, pressing Enter
4. Restoring your previous active window

## Why V2?

V1 required launching Claude Desktop with `--remote-debugging-port=9222` (Chrome DevTools Protocol). This broke constantly — updates wiped the shortcut args, port conflicts with Chrome, etc.

V2 uses Windows UI Automation instead. Works with a normally-launched Claude Desktop. Zero special setup.

## Install

```bash
pip install pywinauto pyautogui pyperclip
```

## Quick Test

```bash
# Check if Scout can find Claude
python smart_scout.py window

# Paste text into Claude's input (won't send)
python smart_scout.py test

# Send a message
python smart_scout.py send "Hello from Smart Scout!"

# Run as background service
python smart_scout.py start
```

## API

```python
from smart_scout import get_scout, add_to_queue

# Add message to queue (auto-wakes scout)
add_to_queue("ping", "AGENT PING: alpha BUILD:COMPLETE task:001")

# Manual control
scout = get_scout()
scout.start()       # Start background thread
scout.wake()        # Wake to check queue
scout.status()      # Get status dict
scout.check_ready() # Check if Claude is ready
scout.stop()        # Graceful shutdown
```

## Event-Driven

Scout sleeps until woken — no constant polling. Messages queue up and get combined into a single paste+send when Claude is ready.

## Requirements

- Windows (pywinauto is Windows-only)
- Python 3.10+
- Claude Desktop running (no special launch args)

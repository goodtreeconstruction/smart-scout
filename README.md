# Smart Scout V2

UIA-based message delivery system for Claude Desktop. Receives messages from Forest Chat via a polling bridge and delivers them into the active Claude Desktop conversation using Windows UI Automation.

## Architecture

```
Forest Chat Hub (5001) → forest_scout_bridge.py (polls /api/read) → state/queue.json → smart_scout.py (UIA paste+Enter)
```

## Components

| File | Purpose |
|------|---------|
| `smart_scout.py` | Core delivery engine - event-driven, UIA-based paste into Claude Desktop |
| `forest_scout_bridge.py` | Polls Forest Chat for bigc-redwood messages, writes to queue, wakes Scout |
| `send_rules.py` | Broadcasts Forest Chat protocol rules to all bots |
| `check_chat.py` | Debug: check recent Forest Chat messages |
| `check_unread.py` | Debug: check unread messages for bigc-redwood |
| `notify_forest.py` | Send notifications to Forest Chat |
| `reply_cypress.py` | Quick reply to Cypress via Forest Chat |
| `debug_messages.py` | Debug: inspect message state |

## Usage

```powershell
# Start Scout (event-driven, waits for queue items)
python -u smart_scout.py start

# Start Forest Chat bridge (polls every 3s)
python -u forest_scout_bridge.py

# CLI commands
python smart_scout.py send "Hello Claude"
python smart_scout.py paste "Test paste without sending"
python smart_scout.py queue "Add to queue"
python smart_scout.py status
```

## Key Behaviors

- **Event-driven**: Sleeps until woken by bridge or 5s fallback tick
- **Streaming detection**: Waits up to 60s for Stop button to disappear before delivering
- **Window targeting**: Delivers to active Claude Desktop window (Chrome_WidgetWin_1)
- **Queue-based**: All messages go through `state/queue.json`
- **Clipboard-safe**: Preserves and restores clipboard during paste operations

## Dependencies

```
pywinauto
pyautogui
pyperclip
requests
```

## Related

- [Forest Chat](https://github.com/goodtreeconstruction/forest-chat) - Bot-to-bot communication hub

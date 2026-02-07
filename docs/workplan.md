# Workplan: Smart Scout V2 — No Debug Port

**Created:** 2026-02-06
**Status:** Planning
**Location:** `central-command-dashboard/server/claude_scout_v2.py`

---

## Problem Statement

**Current State:** Smart Scout V1 (`claude_scout.py`) uses Chrome DevTools Protocol (CDP) on port 9222 to inject JavaScript into Claude Desktop — set input text, click send, detect streaming. This requires launching Claude with `--remote-debugging-port=9222` which constantly breaks (Cypress overwrites shortcut, Claude updates reset it, port conflicts with Chrome, etc.).

**Problem:** Debug port is fragile. Every Claude update or shortcut change kills Scout. Matthew has been fighting this for weeks. Meanwhile, the Passive Logger project proved that Windows UI Automation (pywinauto) can read Claude Desktop without any debug port at all.

**Desired State:** A drop-in replacement `claude_scout_v2.py` that:
1. Uses pywinauto + pyautogui instead of CDP/JavaScript injection
2. Works with a normally-launched Claude Desktop (no special args)
3. Keeps the exact same external API (queue, wake, start/stop, add_to_queue)
4. Event-driven — sleeps until queue has messages, never polls constantly

**Constraints:**
- Must be Windows-only (pywinauto is Windows-only, that's fine)
- Claude Desktop window must exist (doesn't need focus until send time)
- pyautogui requires brief window focus for paste+Enter — acceptable since Scout only fires on wake events
- ~1-1.6s UIA scan time is fine for event-driven model

**Success Criteria:**
- [ ] Scout sends queued messages to Claude Desktop without debug port
- [ ] Multiple queued messages stack with separator (never overwrite)
- [ ] Scout waits for Send button to be primed (no Stop button visible) before writing
- [ ] Event-driven: sleeps when queue empty, wakes on add_to_queue()
- [ ] Drop-in replacement: same add_to_queue(), get_scout(), wake(), status() API
- [ ] Notifier module works unchanged

---

## Checklist Legend

- `[ ]` Unstarted
- `[x]` Complete
- `[~]` In Progress
- `[!]` Blocked

---

## Architecture: V1 → V2 Comparison

| Operation | V1 (CDP/JS) | V2 (UIA/pyautogui) |
|-----------|-------------|---------------------|
| Find Claude window | HTTP to port 9222, filter by URL | `pywinauto.Desktop(backend="uia")`, find by title |
| Check readiness | JS: query Stop button in DOM | UIA: scan buttons, check for "Stop response" |
| Set input text | JS: innerHTML on contenteditable div | pyautogui: focus window → click input → clipboard paste |
| Append to input | JS: read innerText + concat + innerHTML | pyautogui: focus → click end of input → paste new text |
| Click Send | JS: synthetic KeyboardEvent('Enter') | pyautogui: press Enter key (real keypress) |
| Read input text | JS: input.innerText | UIA: find Edit control, read value (for verification) |

---

## Reusable Code from Passive Logger

From `claude_uia_logger_v2.py` (v18):
- `get_claude_window()` — finds Claude by title + class name
- `SKIP_STRINGS`, `SKIP_PARENT_TYPES` — UI noise filters (reference only)
- Button scanning pattern — `win.descendants(control_type="Button")`

**NOT reusing:** Text extraction, streaming detection, change-detection state machine (Scout doesn't need to read conversations — it only writes)

---

## Checklist

### Phase 1: Core UIA Engine

- [ ] 1. **[File: claude_scout_v2.py]** Create V2 ScoutService with UIA backend
  - [ ] 1.a. [DEPS] Ensure dependencies available
    - [ ] 1.a.i. `pywinauto` (already installed for passive logger)
    - [ ] 1.a.ii. `pyautogui` — `pip install pyautogui`
    - [ ] 1.a.iii. `pyperclip` — `pip install pyperclip` (clipboard ops)
  - [ ] 1.b. [IMPL] Window discovery module
    - [ ] 1.b.i. `_find_claude_window()` — find by title "Claude" + class "Chrome_WidgetWin_1"
    - [ ] 1.b.ii. Cache window handle, re-find if stale (window closed/reopened)
    - [ ] 1.b.iii. Return None gracefully if Claude not running
  - [ ] 1.c. [IMPL] Readiness detection
    - [ ] 1.c.i. `_has_stop_button()` — scan buttons for "Stop response" (name match)
    - [ ] 1.c.ii. `_find_send_button()` — scan for Send/submit button by name or aria-label
    - [ ] 1.c.iii. `_find_input_area()` — find the Edit control (contenteditable input box)
    - [ ] 1.c.iv. `check_ready()` → returns `{has_input: bool, stop_button: bool, ready: bool}`
    - [ ] 1.c.v. Optimization: scan buttons ONCE per check, filter for both Stop and Send
  - [ ] 1.d. [IMPL] Text input via clipboard paste
    - [ ] 1.d.i. `_focus_input()` — bring Claude to front, click the input area element
    - [ ] 1.d.ii. `_paste_text(text)` — pyperclip.copy(text) → pyautogui Ctrl+V
    - [ ] 1.d.iii. `_clear_input()` — Ctrl+A then Delete (clear existing before paste)
    - [ ] 1.d.iv. `set_input_text(text)` — clear + paste (full replace)
    - [ ] 1.d.v. `append_to_input(text)` — click End → type separator → paste new text
  - [ ] 1.e. [IMPL] Send message
    - [ ] 1.e.i. `click_send()` — press Enter key via pyautogui (input must be focused)
    - [ ] 1.e.ii. Verify: brief pause + check Stop button appears (confirmation send worked)
  - [ ] 1.f. [CRITERIA] Acceptance for Phase 1
    - [ ] 1.f.i. Can find Claude window without debug port
    - [ ] 1.f.ii. Can detect Stop button presence/absence
    - [ ] 1.f.iii. Can paste text into input box
    - [ ] 1.f.iv. Can send message via Enter
    - [ ] 1.f.v. Standalone CLI test: `python claude_scout_v2.py test` pastes and sends

---

### Phase 2: Queue & Event Loop (port from V1)

- [ ] 2. **[File: claude_scout_v2.py]** Port the event-driven queue system
  - [ ] 2.a. [IMPL] Queue operations (copy from V1 — these are file-based, no CDP)
    - [ ] 2.a.i. `get_pending_messages()` — read state/queue.json (identical to V1)
    - [ ] 2.a.ii. `mark_sent(ids)` — move pending → processed (identical to V1)
    - [ ] 2.a.iii. `build_message_text(messages)` — format cypress/ping messages (identical to V1)
  - [ ] 2.b. [IMPL] Event-driven main loop
    - [ ] 2.b.i. `_run_loop()` — sleep on `threading.Event`, wake on `wake()` or 5s fallback
    - [ ] 2.b.ii. On wake: get pending → if none, go back to sleep
    - [ ] 2.b.iii. Build combined text from all pending messages (stacking with separator)
    - [ ] 2.b.iv. **Wait for ready:** poll `check_ready()` until no Stop button (max 60s timeout)
    - [ ] 2.b.v. Once ready: `set_input_text(combined)` → brief pause → `click_send()`
    - [ ] 2.b.vi. `mark_sent()` all processed IDs
    - [ ] 2.b.vii. Back to sleep
  - [ ] 2.c. [IMPL] Public API (same as V1)
    - [ ] 2.c.i. `start()` — launch background thread
    - [ ] 2.c.ii. `stop()` — set stop event, join thread
    - [ ] 2.c.iii. `wake()` — set wake event
    - [ ] 2.c.iv. `status()` → dict with running, pending_count, last_sent, last_error, send_count
  - [ ] 2.d. [IMPL] Singleton + add_to_queue helper (identical to V1)
    - [ ] 2.d.i. `get_scout()` — singleton accessor
    - [ ] 2.d.ii. `add_to_queue(msg_type, content, **kwargs)` — write to queue.json + wake scout
  - [ ] 2.e. [CRITERIA] Acceptance for Phase 2
    - [ ] 2.e.i. Queue messages from CLI: `python claude_scout_v2.py queue "hello"`
    - [ ] 2.e.ii. Scout wakes, waits for ready, pastes, sends
    - [ ] 2.e.iii. Multiple rapid queue additions stack into one combined message
    - [ ] 2.e.iv. Scout goes back to sleep after sending

---

### Phase 3: Integration & Swap

- [ ] 3. **[File: claude_desktop_notifier.py]** Update import path
  - [ ] 3.a. [IMPL] Change import from `claude_scout` to `claude_scout_v2`
    - [ ] 3.a.i. `from server.claude_scout_v2 import get_scout, add_to_queue`
    - [ ] 3.a.ii. All existing notify_ping/send_to_claude functions work unchanged
  - [ ] 3.b. [CRITERIA]
    - [ ] 3.b.i. `notify_ping("alpha", "BUILD:COMPLETE", "task-001")` queues and sends

- [ ] 4. **[File: app.py / mcp_server.py]** Update any direct imports of claude_scout
  - [ ] 4.a. [IMPL] Search for `from server.claude_scout import` and update to v2
  - [ ] 4.b. [IMPL] Search for `from server.claude_scout ` (space) in case of other import styles
  - [ ] 4.c. [CRITERIA]
    - [ ] 4.c.i. `python run_mcp_clean.py` starts without import errors
    - [ ] 4.c.ii. Dashboard starts without import errors

- [ ] 5. **[File: claude_scout.py]** Archive V1
  - [ ] 5.a. [IMPL] Rename to `claude_scout_v1_cdp.py` (keep for reference, don't delete)
  - [ ] 5.b. [COMMIT] `feat(scout): Smart Scout V2 — UIA backend, no debug port`

---

### Phase 4: Edge Cases & Hardening

- [ ] 6. **[File: claude_scout_v2.py]** Handle edge cases
  - [ ] 6.a. [IMPL] Window not found recovery
    - [ ] 6.a.i. If Claude closed mid-send: log error, re-queue messages, retry on next wake
    - [ ] 6.a.ii. Re-find window on each wake cycle (don't cache stale handles across sends)
  - [ ] 6.b. [IMPL] Focus contention
    - [ ] 6.b.i. Save current foreground window before focusing Claude
    - [ ] 6.b.ii. Restore previous window after send completes
    - [ ] 6.b.iii. Brief sleep after restore to avoid race conditions
  - [ ] 6.c. [IMPL] Clipboard preservation
    - [ ] 6.c.i. Save clipboard contents before paste
    - [ ] 6.c.ii. Restore original clipboard after send
  - [ ] 6.d. [IMPL] Long message handling
    - [ ] 6.d.i. Clipboard paste handles any length (no char-by-char limit)
    - [ ] 6.d.ii. Verify: test with 5000+ character message
  - [ ] 6.e. [IMPL] Rapid queue bursts
    - [ ] 6.e.i. When multiple messages arrive during a single wake cycle, combine ALL pending
    - [ ] 6.e.ii. Small delay (0.5s) after wake before reading queue — lets burst settle
  - [ ] 6.f. [CRITERIA]
    - [ ] 6.f.i. Claude window recovery works after close/reopen
    - [ ] 6.f.ii. User's clipboard not clobbered after Scout sends
    - [ ] 6.f.iii. Active window restored after Scout focuses Claude

---

## Risks & Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| pyautogui needs Claude window visible | Medium | Brief focus, then restore. Scout only fires on events, not constant. |
| UIA button scan takes 1-1.6s | Low | Event-driven, not polling. One scan per send cycle is fine. |
| Claude UI update changes button names | Medium | Use flexible matching ("Stop" contains, not exact). Keep names in constants for easy update. |
| Clipboard clobbered during paste | Low | Save/restore clipboard around paste operation. |
| Focus steal annoys user | Medium | Restore previous window after send. Log when focus stolen so user knows why. |
| pywinauto finds wrong "Claude" window | Low | Match both title AND class name "Chrome_WidgetWin_1". |

---

## Key Design Decisions

1. **Full replace, not append** — When Scout wakes, it builds ONE combined message from ALL pending items, clears the input, and pastes the combined text. Simpler than trying to append to whatever might be in the input box. This matches V1 behavior.

2. **Clipboard paste over character typing** — `pyautogui.typewrite()` is slow and can't handle unicode. Clipboard paste is instant and handles any content.

3. **Same queue.json format** — Zero changes to queue operations. V2 is purely a transport swap (CDP → UIA/pyautogui).

4. **No constant polling** — Scout thread sleeps on `threading.Event.wait()`. Only wakes when `add_to_queue()` calls `scout.wake()` or on 5s fallback tick. Passive logger's constant-poll approach is NOT used here.

5. **Re-find window each cycle** — Don't cache the pywinauto window object across wake cycles. Claude might restart between sends. Fresh lookup each time is 0.1s, negligible.

---

## Files Touched Summary

| File | Action |
|------|--------|
| `server/claude_scout_v2.py` | **CREATE** — new UIA-based scout |
| `server/claude_desktop_notifier.py` | **EDIT** — swap import |
| `server/app.py` | **EDIT** — swap import (if applicable) |
| `server/mcp_server.py` | **EDIT** — swap import (if applicable) |
| `server/claude_scout.py` | **RENAME** → `claude_scout_v1_cdp.py` |

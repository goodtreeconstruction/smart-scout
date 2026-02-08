# Smart Scout V2 — Machine Configuration

Smart Scout requires machine-specific settings. Before running on a new machine, update these three values.

## Required Settings

### 1. `smart_scout.py` — Queue File Path

```python
QUEUE_FILE = Path(r"C:\Users\<USERNAME>\Documents\...\smart-scout\state\queue.json")
```

| Machine | User | Path |
|---------|------|------|
| Redwood (Dell) | Matthew | `C:\Users\Matthew\Documents\claude\smart-scout\state\queue.json` |
| Elm (Laptop) | mattb | `C:\Users\mattb\Documents\Claude_Projects\smart-scout\state\queue.json` |

### 2. `forest_scout_bridge.py` — Identity

```python
IDENTITY = "bigc-<machine>"
```

| Machine | Identity |
|---------|----------|
| Redwood | `bigc-redwood` |
| Elm | `bigc-elm` |

### 3. `forest_scout_bridge.py` — Forest Chat URL

```python
FOREST_CHAT_URL = "http://<forest-chat-host>:5001"
```

| Machine | URL |
|---------|-----|
| Redwood (runs FC locally) | `http://127.0.0.1:5001` |
| Elm (remote) | `http://100.119.22.92:5001` |

## Quick Setup Checklist

1. Clone repo to local machine
2. Update `QUEUE_FILE` in `smart_scout.py` to match local user/path
3. Update `IDENTITY` in `forest_scout_bridge.py` to match machine name
4. Update `FOREST_CHAT_URL` in `forest_scout_bridge.py` (localhost if FC runs here, Tailscale IP otherwise)
5. Create `state/` directory: `mkdir state`
6. Start Scout: `python -u smart_scout.py start`
7. Start Bridge: `python -u forest_scout_bridge.py`

## Forest Chat Host

Forest Chat currently runs on **Redwood** (Dell desktop) at Tailscale IP `100.119.22.92:5001`.

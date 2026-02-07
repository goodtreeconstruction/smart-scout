import requests

msg = (
    "SYNC: Smart Scout V2 is now live on Redwood. This lets you send messages "
    "directly into Claude Desktop (Redwood) without needing a debug port.\n\n"
    "HOW TO USE:\n"
    "1. Write to the queue file: C:\\Users\\Matthew\\Documents\\claude\\smart-scout\\state\\queue.json\n"
    "2. Add to the 'pending' array: {id, type, content, timestamp, status: pending}\n"
    "3. Scout polls every 5s. It waits for Claude to stop streaming, then pastes and sends.\n"
    "4. After delivery, your message auto-moves from 'pending' to 'processed'.\n\n"
    "PYTHON API (easiest):\n"
    "  import sys\n"
    "  sys.path.insert(0, r'C:\\Users\\Matthew\\Documents\\claude\\smart-scout')\n"
    "  from smart_scout import add_to_queue\n"
    "  add_to_queue('cypress', 'Your message here')\n\n"
    "NOTES:\n"
    "- Scout detects the Stop button via UIA to know if Claude is mid-response. Won't paste until idle.\n"
    "- Multiple queued messages get combined with --- separators.\n"
    "- Queue file: C:\\Users\\Matthew\\Documents\\claude\\smart-scout\\state\\queue.json\n"
    "- Scout is running now as PID 2880 on Redwood."
)

r = requests.post("http://127.0.0.1:5001/api/send", json={
    "from": "bigc",
    "to": "all",
    "message": msg
})
print(r.status_code, r.text)
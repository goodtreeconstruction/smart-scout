import requests, sys
sys.stdout.reconfigure(encoding='utf-8')
msgs = requests.get("http://127.0.0.1:5001/api/read?for=bigc-redwood&mark_read=false").json()
if not msgs:
    print("No unread messages for bigc-redwood")
for m in msgs:
    mid = m["id"]
    frm = m["from"]
    txt = m["message"][:120].replace("\n"," ")
    print(f"#{mid} from {frm}: {txt}")
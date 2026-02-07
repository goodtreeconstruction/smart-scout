import requests, sys
sys.stdout.reconfigure(encoding='utf-8')
msgs = requests.get("http://127.0.0.1:5001/api/messages?limit=20").json()
for m in msgs:
    mid = m["id"]
    frm = m["from"]
    to = m["to"]
    txt = m["message"][:120].replace("\n", " ")
    print(f"#{mid:>3} | {frm:>15} -> {to:>15} | {txt}")
# redeem.py
import os, time, json, hashlib, urllib.parse, sys
import urllib.request
from datetime import datetime

# ====== Config via env (set in GitHub Secrets) ======
SECRET            = os.getenv("SECRET", "").strip()                       # e.g. tB87#kPtkxqOS2
CURRENT_CODE      = os.getenv("CURRENT_CODE", "").strip()                 # e.g. CandyloveWOS
IDS_CSV           = os.getenv("IDS_CSV", "").strip()                      # e.g. 123,456,789
PLAYER_ENDPOINT   = os.getenv("PLAYER_ENDPOINT", "https://wos-giftcode-api.centurygame.com/api/player").strip()
GIFT_ENDPOINT     = os.getenv("GIFT_ENDPOINT",   "https://wos-giftcode-api.centurygame.com/api/gift_code").strip()
TIME_UNIT         = os.getenv("TIME_UNIT", "s").strip().lower()           # "s" or "ms"
INCLUDE_KID       = os.getenv("INCLUDE_KID", "false").strip().lower()     # "true" or "false"
DISCORD_WEBHOOK   = os.getenv("DISCORD_WEBHOOK", "").strip()              # optional: post summary to a channel

HEADERS = {
    "Content-Type": "application/x-www-form-urlencoded",
    "Accept": "application/json, text/plain, */*",
    "Origin": "https://wos-giftcode.centurygame.com",
    "Referer": "https://wos-giftcode.centurygame.com/",
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123 Safari/537.36",
}

def log(msg):
    print(msg, flush=True)

def ts():
    if TIME_UNIT == "ms":
        return str(int(time.time() * 1000))
    return str(int(time.time()))

def md5_hex(s: str) -> str:
    return hashlib.md5(s.encode("utf-8")).hexdigest()

def urlencode(d: dict) -> str:
    return urllib.parse.urlencode(d)

def post(url: str, data: dict) -> (int, str):
    dat = urlencode(data).encode("utf-8")
    req = urllib.request.Request(url, dat, headers=HEADERS, method="POST")
    with urllib.request.urlopen(req, timeout=20) as resp:
        code = resp.getcode()
        body = resp.read().decode("utf-8", "ignore")
        return code, body

def fetch_player(fid: str):
    t = ts()
    # sign base must be key-sorted
    base_fields = {"fid": fid, "time": t}
    base_qs = "&".join(f"{k}={base_fields[k]}" for k in sorted(base_fields))
    sign = md5_hex(base_qs + SECRET)
    payload = {"sign": sign, **base_fields}
    log(f'DEBUG /api/player signBase: {json.dumps(base_fields, indent=2)}')
    log(f'DEBUG /api/player sign={sign}')
    log(f'DEBUG /api/player payload: {json.dumps(payload, indent=2)}')
    code, body = post(PLAYER_ENDPOINT, payload)
    log(f'INFO /api/player HTTP {code}')
    log(f'INFO /api/player body: {body}')
    try:
        j = json.loads(body)
        if j.get("code") == 0 and isinstance(j.get("data"), dict):
            return j["data"]  # contains kid, nickname, etc.
    except Exception:
        pass
    return None

def redeem(fid: str, cdk: str, kid: str | None):
    # try a few variants (sorted order; with/without kid; time s/ms)
    variants = []
    # current TIME_UNIT first
    t1 = ts()
    base1 = {"fid": fid, "cdk": cdk, "time": t1}
    variants.append(("sorted|kid:N|time:"+TIME_UNIT, base1))

    if INCLUDE_KID == "true" and kid:
        base2 = {"fid": fid, "cdk": cdk, "time": t1, "kid": str(kid)}
        variants.append(("sorted|kid:Y|time:"+TIME_UNIT, base2))

    # Alternate time unit (if first one fails)
    alt_unit = "ms" if TIME_UNIT == "s" else "s"
    t2 = str(int(time.time() * 1000)) if alt_unit == "ms" else str(int(time.time()))
    base3 = {"fid": fid, "cdk": cdk, "time": t2}
    variants.append(("sorted|kid:N|time:"+alt_unit, base3))
    if INCLUDE_KID == "true" and kid:
        base4 = {"fid": fid, "cdk": cdk, "time": t2, "kid": str(kid)}
        variants.append(("sorted|kid:Y|time:"+alt_unit, base4))

    for name, fields in variants:
        base_qs = "&".join(f"{k}={fields[k]}" for k in sorted(fields))
        sign = md5_hex(base_qs + SECRET)
        payload = {"sign": sign, **fields}
        log(f'DEBUG /api/gift_code[{name}] signBase: {json.dumps(fields, indent=2)}')
        log(f'DEBUG /api/gift_code[{name}] sign={sign}')
        log(f'DEBUG /api/gift_code[{name}] payload: {json.dumps(payload, indent=2)}')
        code, body = post(GIFT_ENDPOINT, payload)
        log(f'INFO /api/gift_code[{name}] HTTP {code}')
        log(f'INFO /api/gift_code[{name}] body: {body}')
        try:
            j = json.loads(body)
            msg = (j.get("msg") or "").lower()
            if j.get("code") == 0 or "success" in msg or "received" in msg or "same type" in msg:
                return ("OK", j)
            if "Sign Error".lower() in msg or "params error" in msg:
                # keep trying next variant
                continue
        except Exception:
            continue
    return ("FAIL", {"reason": "All variants returned params/sign error (likely endpoint requirement or IP/WAF)."})


def notify_webhook(summary: str):
    if not DISCORD_WEBHOOK:
        return
    try:
        data = {"content": summary[:1900]}
        req = urllib.request.Request(DISCORD_WEBHOOK,
                                     data=json.dumps(data).encode("utf-8"),
                                     headers={"Content-Type":"application/json"},
                                     method="POST")
        urllib.request.urlopen(req, timeout=10).read()
    except Exception as e:
        log(f"Webhook error: {e}")

def main():
    if not SECRET or not CURRENT_CODE or not IDS_CSV:
        log("ERROR: SECRET, CURRENT_CODE, and IDS_CSV must be set as secrets.")
        sys.exit(1)

    fids = [x.strip() for x in IDS_CSV.split(",") if x.strip()]
    log(f"=== Daily run start @ {datetime.utcnow().isoformat()}Z; IDs={len(fids)}; code={CURRENT_CODE} ===")

    ok, fail = 0, 0
    details = []
    for i, fid in enumerate(fids, start=1):
        log(f"INFO Row {i} BEGIN (fid={fid})")
        p = fetch_player(fid)
        kid = str(p["kid"]) if (p and "kid" in p) else None
        status, resp = redeem(fid, CURRENT_CODE, kid)
        if status == "OK":
            ok += 1
            details.append(f"{fid}: OK")
        else:
            fail += 1
            details.append(f"{fid}: FAIL - {resp.get('reason','')}")
        time.sleep(0.2)  # gentle pacing

    summary = f"Daily gift run finished. OK={ok} FAIL={fail}\n" + "\n".join(details)
    log(summary)
    notify_webhook(summary)

if __name__ == "__main__":
    main()

"""UNIQLO / GU 男装 M/L码"七折以上+有货"监控，命中后推送到手机(ntfy)，带商品图。

官网接口不提供原价(base=promo=现价)，所以原价参考来自三处，取最大值：
  1. 每次运行扫一遍男装全目录，记下"没打折时"的价格(prices.json，越跑越全)
  2. 同一价格组里没打折的其他颜色的价格
  3. 同商品其他价格组(00/01/02)里没打折的价格
找不到原价参考的商品不推送(不瞎猜)，在 last_run.log 里记为"原价未知"。
"""

import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime

if sys.stdout and sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

NTFY_TOPIC = "yi-ufm-q7k2m9x4tw"

TARGET_SIZES = {"004": "M", "005": "L"}
DISCOUNT_THRESHOLD = 0.7  # 现价 <= 原价*0.7
REQUEST_INTERVAL = 0.3

SITES = {
    "UNIQLO": {"base": "https://www.uniqlo.com/jp", "men": 1072},
    "GU": {"base": "https://www.gu-global.com/jp", "men": 2257},
}
DISCOUNT_FLAGS = {"discount", "limitedOffer"}

# 夏季/不需要的商品，名字里含这些词就跳过
EXCLUDE_KEYWORDS = [
    "エアリズム", "AIRism", "UV", "ドライEX", "リネン", "ショートパンツ", "ハーフパンツ",
    "ショーツ", "半袖", "5分袖", "ノースリーブ", "タンクトップ", "サンダル", "水着", "スイム",
    "ドライ", "グラフィックT", "ボクサー", "トランクス", "ゲームシャツ", "ソックス", "ジョーツ",
]

HERE = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = os.path.join(HERE, "state.json")
PRICES_FILE = os.path.join(HERE, "prices.json")
LOG_FILE = os.path.join(HERE, "last_run.log")

_log_lines = []


def log(msg):
    _log_lines.append(msg)
    try:
        print(msg)
    except Exception:
        pass


def get_json(url):
    time.sleep(REQUEST_INTERVAL)
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.load(resp)


def load(path):
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def save(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)


def list_products(site, flag=None):
    cfg = SITES[site]
    items, offset = [], 0
    while True:
        url = (f"{cfg['base']}/api/commerce/v5/ja/products?path={cfg['men']}"
               f"&limit=100&offset={offset}&httpFailure=true")
        if flag:
            url += f"&flagCodes={flag}"
        r = get_json(url)["result"]
        items += r["items"]
        offset += r["pagination"]["count"]
        if r["pagination"]["count"] == 0 or offset >= r["pagination"]["total"]:
            return items


def price_flags(obj):
    return {f["code"] for f in (obj.get("flags") or {}).get("priceFlags", [])}


def learn_regular_prices(site, prices):
    for it in list_products(site):
        flags = price_flags(it.get("representative") or {})
        if flags & DISCOUNT_FLAGS:
            continue
        value = (it.get("prices") or {}).get("base", {}).get("value")
        if value:
            key = f"{site}:{it['productId']}"
            prices[key] = max(prices.get(key, 0), value)


def fetch_l2s(site, product_id, pg):
    url = (f"{SITES[site]['base']}/api/commerce/v5/ja/products/{product_id}/price-groups/{pg}/l2s"
           f"?withPrices=true&withStocks=true&includePreviousPrice=false&withMemberPricing=true&httpFailure=true")
    try:
        r = get_json(url).get("result") or {}
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
        return []
    rows = []
    for l2 in r.get("l2s", []):
        l2_id = l2["l2Id"]
        p = (r.get("prices", {}).get(l2_id) or {}).get("guest", {})
        price = (p.get("promo") or {}).get("value") or (p.get("base") or {}).get("value")
        st = r.get("stocks", {}).get(l2_id, {})
        rows.append({
            "size": l2["size"]["displayCode"],
            "color": l2["color"]["displayCode"],
            "price": price,
            "in_stock": st.get("statusCode") == "IN_STOCK",
            "qty": st.get("quantity", 0),
            "discounted": bool(price_flags(l2) & DISCOUNT_FLAGS),
        })
    return rows


def excluded(name):
    if any(k in name for k in EXCLUDE_KEYWORDS):
        return True
    return "Tシャツ" in name and "長袖" not in name


def send_ntfy(title, message, image_url=None, click_url=None):
    headers = {"Title": title.encode("utf-8"), "Priority": "high", "Tags": "shirt"}
    if image_url:
        headers["Attach"] = image_url
    if click_url:
        headers["Click"] = click_url
    req = urllib.request.Request(f"https://ntfy.sh/{NTFY_TOPIC}", data=message.encode("utf-8"),
                                 headers=headers, method="POST")
    try:
        urllib.request.urlopen(req, timeout=15)
    except urllib.error.URLError as e:
        log(f"[错误] 推送失败: {e}")


def check_site(site, prices, state, seen_keys):
    base = SITES[site]["base"]
    candidates = {}
    for flag in DISCOUNT_FLAGS:
        for it in list_products(site, flag):
            candidates[(it["productId"], it["priceGroup"])] = it

    for (pid, pg), it in candidates.items():
        name = it["name"]
        if excluded(name):
            continue
        if not any(s.get("displayCode") in TARGET_SIZES for s in it.get("sizes", [])):
            continue

        rows = fetch_l2s(site, pid, pg)
        hits = [r for r in rows if r["size"] in TARGET_SIZES and r["discounted"] and r["in_stock"]]
        if not hits:
            continue

        refs = [prices.get(f"{site}:{pid}", 0)]
        refs += [r["price"] for r in rows if not r["discounted"] and r["price"]]
        if max(refs) == 0:
            for other in ("00", "01", "02"):
                if other != pg:
                    refs += [r["price"] for r in fetch_l2s(site, pid, other) if not r["discounted"] and r["price"]]
        ref = max(refs)

        by_color = {}
        for h in hits:
            by_color.setdefault(h["color"], []).append(h)

        for color, hs in by_color.items():
            price = min(h["price"] for h in hs)
            if not ref:
                log(f"[原价未知] {site} {name} 色{color} ¥{price}")
                continue
            ratio = price / ref
            if ratio > DISCOUNT_THRESHOLD:
                continue
            off = round((1 - ratio) * 100)
            sizes = "/".join(TARGET_SIZES[h["size"]] for h in sorted(hs, key=lambda h: h["size"]))
            key = f"{site}:{pid}:{pg}:{color}"
            seen_keys.add(key)
            log(f"[命中] {site} {name} 色{color} {sizes}码 ¥{ref}→¥{price} ({off}%off)")
            if state.get(key, {}).get("price") == price and state[key].get("sizes") == sizes:
                continue  # 同价同尺码已推送过
            img = ((it.get("images") or {}).get("main") or {}).get(color, {}).get("image")
            url = f"{base}/ja/products/{pid}/{pg}?colorDisplayCode={color}&sizeDisplayCode={hs[0]['size']}"
            send_ntfy(f"[{site}] {off}%off {sizes}码有货 ¥{price}",
                      f"{name}\n¥{ref} → ¥{price}（{off}%off）",
                      image_url=img, click_url=url)
            state[key] = {"price": price, "sizes": sizes, "at": datetime.now().isoformat(timespec="minutes")}


def main():
    log(f"=== {datetime.now():%Y-%m-%d %H:%M} ===")
    prices, state = load(PRICES_FILE), load(STATE_FILE)
    seen = set()
    for site in SITES:
        try:
            learn_regular_prices(site, prices)
            check_site(site, prices, state, seen)
        except Exception as e:
            log(f"[错误] {site}: {e!r}")
    # 卖完/不再打折的条目清掉，下次补货或再降价会重新推送
    state = {k: v for k, v in state.items() if k in seen}
    save(PRICES_FILE, prices)
    save(STATE_FILE, state)
    log(f"原价库 {len(prices)} 件，当前命中 {len(seen)} 件")
    with open(LOG_FILE, "w", encoding="utf-8") as f:
        f.write("\n".join(_log_lines) + "\n")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
Trump Oranje-Index — dagelijkse meet-engine.
Leest per nieuwsbron de RSS-feed, zoekt het recentste Trump-artikel met een
bruikbare gezichtsfoto, en berekent de Oranje-Index met exact dezelfde logica
als de webpagina. Schrijft data.json weg (die Netlify dan serveert).

pip install feedparser pillow requests
"""

import io, json, datetime, colorsys
import requests, feedparser
from PIL import Image

# ---- bronnen: pas de feed-URLs gerust aan, outlets wijzigen ze soms ----
SOURCES = [
    {"id": "cnn", "label": "CNN", "feed": "http://rss.cnn.com/rss/edition_us.rss"},
    {"id": "fox", "label": "FOX", "feed": "https://moxie.foxnews.com/google-publisher/politics.xml"},
    {"id": "vrt", "label": "VRT", "feed": "https://www.vrt.be/vrtnws/nl.rss.articles.xml"},
    {"id": "hln", "label": "HLN", "feed": "https://www.hln.be/buitenland/rss.xml"},
    {"id": "bbc", "label": "BBC", "feed": "https://feeds.bbci.co.uk/news/world/rss.xml"},
    {"id": "google", "label": "Google", "feed": "https://news.google.com/rss/search?q=Trump&hl=nl&gl=BE"},
]
KEYWORDS = ("trump",)
REF = (237, 205, 184)          # bleke referentie-huid (#EDCDB8)
UA = {"User-Agent": "Mozilla/5.0 (orange-index bot)"}


# ---------------- kleur-engine (1:1 met de JS op de pagina) ----------------
def rgb2hsv(r, g, b):
    h, s, v = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
    return h * 360, s, v


REF_S = rgb2hsv(*REF)[1]


def is_skin(r, g, b):
    mx, mn = max(r, g, b), min(r, g, b)
    rgb_rule = (r > 95 and g > 40 and b > 20 and (mx - mn) > 15
                and abs(r - g) > 15 and r > g and r > b)
    cb = -0.168736 * r - 0.331264 * g + 0.5 * b + 128
    cr = 0.5 * r - 0.418688 * g - 0.081312 * b + 128
    ycc = 77 <= cb <= 135 and 133 <= cr <= 180
    return rgb_rule and ycc


def hue_gate(h):
    if 14 <= h <= 42:
        return 1.0
    if 0 <= h < 14:
        return h / 14
    if 42 < h <= 70:
        return (70 - h) / 28
    return 0.0


def analyse(img_bytes):
    """-> (oi, hex) of None als er geen bruikbaar gezicht in zit."""
    img = Image.open(io.BytesIO(img_bytes)).convert("RGB")
    img.thumbnail((260, 260))
    sr = sg = sb = n = 0
    for r, g, b in img.getdata():
        if is_skin(r, g, b):
            sr += r; sg += g; sb += b; n += 1
    if n < 30:                       # geen huid = geen bruikbare gezichtsfoto
        return None
    r, g, b = sr / n, sg / n, sb / n
    h, s, _ = rgb2hsv(r, g, b)
    oi = max(0.0, min(100.0, hue_gate(h) * (max(0, s - REF_S) / 0.55) * 100))
    return round(oi, 1), "#%02x%02x%02x" % (round(r), round(g), round(b))


# ---------------- foto's uit een RSS-item halen ----------------
def image_urls(entry):
    urls = []
    for m in entry.get("media_content", []) + entry.get("media_thumbnail", []):
        if m.get("url"):
            urls.append(m["url"])
    for enc in entry.get("enclosures", []):
        if "image" in enc.get("type", "") and enc.get("href"):
            urls.append(enc["href"])
    return urls


def measure_source(src):
    feed = feedparser.parse(src["feed"])
    for entry in feed.entries:
        text = (entry.get("title", "") + entry.get("summary", "")).lower()
        if not any(k in text for k in KEYWORDS):
            continue
        for url in image_urls(entry):
            try:
                resp = requests.get(url, headers=UA, timeout=15)
                resp.raise_for_status()
                result = analyse(resp.content)
                if result:
                    oi, hexcol = result
                    return {
                        "id": src["id"], "label": src["label"],
                        "oi": oi, "swatch": hexcol,
                        "ts": datetime.datetime.now().strftime("%H:%M"),
                    }
            except Exception as e:
                print(f"  ! {src['label']} {url[:50]}… → {e}")
    return None


def main():
    out = []
    for src in SOURCES:
        print(f"→ {src['label']}")
        r = measure_source(src)
        if r:
            print(f"  ✓ OI {r['oi']}  {r['swatch']}")
            out.append(r)
        else:
            print("  – geen bruikbare Trump-foto gevonden")

    if not out:
        print("Niets gemeten, data.json niet overschreven.")
        return

    data = {
        "updated": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "live": True,
        "sources": out,
    }
    with open("data.json", "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    avg = sum(s["oi"] for s in out) / len(out)
    print(f"\nGemiddelde Oranje-Index: {avg:.1f} — data.json geschreven.")


if __name__ == "__main__":
    main()

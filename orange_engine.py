#!/usr/bin/env python3
"""
Trump Oranje-Index — dagelijkse meet-engine.
Leest per nieuwsbron de RSS-feed, zoekt het recentste Trump-artikel met een
bruikbare gezichtsfoto, en berekent de Oranje-Index met exact dezelfde logica
als de webpagina. Schrijft data.json weg (die Netlify dan serveert).

pip install feedparser pillow requests
"""

import io, re, json, datetime, colorsys
import requests, feedparser
from PIL import Image

# ---- bronnen: pas de feed-URLs gerust aan, outlets wijzigen ze soms ----
SOURCES = [
    # --- native feeds (officiële RSS van de bron) ---
    {"id": "fox",    "label": "FOX",         "feed": "https://moxie.foxnews.com/google-publisher/politics.xml"},
    {"id": "nyt",    "label": "NYT",         "feed": "https://rss.nytimes.com/services/xml/rss/nyt/Politics.xml"},
    {"id": "nytw",   "label": "NYT World",   "feed": "https://rss.nytimes.com/services/xml/rss/nyt/World.xml"},
    {"id": "bbcus",  "label": "BBC US",      "feed": "https://feeds.bbci.co.uk/news/world/us_and_canada/rss.xml"},
    {"id": "bbcw",   "label": "BBC World",   "feed": "https://feeds.bbci.co.uk/news/world/rss.xml"},
    {"id": "guard",  "label": "Guardian",    "feed": "https://www.theguardian.com/us-news/rss"},
    {"id": "aljaz",  "label": "Al Jazeera",  "feed": "https://www.aljazeera.com/xml/rss/all.xml"},
    {"id": "wapo",   "label": "Wash. Post",  "feed": "https://feeds.washingtonpost.com/rss/national"},
    {"id": "npr",    "label": "NPR",         "feed": "https://feeds.npr.org/1014/rss.xml"},
    {"id": "cbs",    "label": "CBS",         "feed": "https://www.cbsnews.com/latest/rss/politics"},

    # --- Google News, al voorgefilterd op 'Trump' (gegarandeerd werkend) ---
    {"id": "cnn",    "label": "CNN",         "feed": "https://news.google.com/rss/search?q=Trump+site:cnn.com&hl=en-US&gl=US&ceid=US:en"},
    {"id": "reuters","label": "Reuters",     "feed": "https://news.google.com/rss/search?q=Trump+site:reuters.com&hl=en-US&gl=US&ceid=US:en"},
    {"id": "ap",     "label": "AP",          "feed": "https://news.google.com/rss/search?q=Trump+site:apnews.com&hl=en-US&gl=US&ceid=US:en"},
    {"id": "poli",   "label": "Politico",    "feed": "https://news.google.com/rss/search?q=Trump+site:politico.com&hl=en-US&gl=US&ceid=US:en"},
    {"id": "hill",   "label": "The Hill",    "feed": "https://news.google.com/rss/search?q=Trump+site:thehill.com&hl=en-US&gl=US&ceid=US:en"},
    {"id": "nbc",    "label": "NBC",         "feed": "https://news.google.com/rss/search?q=Trump+site:nbcnews.com&hl=en-US&gl=US&ceid=US:en"},
    {"id": "abc",    "label": "ABC",         "feed": "https://news.google.com/rss/search?q=Trump+site:abcnews.go.com&hl=en-US&gl=US&ceid=US:en"},
    {"id": "sky",    "label": "Sky News",    "feed": "https://news.google.com/rss/search?q=Trump+site:news.sky.com&hl=en-GB&gl=GB&ceid=GB:en"},
    {"id": "destd",  "label": "De Standaard","feed": "https://news.google.com/rss/search?q=Trump+site:standaard.be&hl=nl&gl=BE&ceid=BE:nl"},
    {"id": "nos",    "label": "NOS",         "feed": "https://news.google.com/rss/search?q=Trump+site:nos.nl&hl=nl&gl=BE&ceid=BE:nl"},
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
    # ook eventuele <img> in de samenvatting meepakken
    for m in re.findall(r'<img[^>]+src=["\']([^"\']+)', entry.get("summary", "")):
        urls.append(m)
    return urls


def og_image(article_url):
    """Haalt de og:image (hoofdfoto) van een artikelpagina; volgt redirects
    (ook de Google News-redirect). Geeft None terug als er niets te vinden is."""
    try:
        r = requests.get(article_url, headers=UA, timeout=15, allow_redirects=True)
        m = re.search(r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)', r.text, re.I)
        if not m:
            m = re.search(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image', r.text, re.I)
        return m.group(1) if m else None
    except Exception:
        return None


def candidate_images(entry):
    """Eerst snelle RSS-beelden, dan de og:image van het artikel zelf."""
    seen = set()
    for u in image_urls(entry):
        if u and u not in seen:
            seen.add(u); yield u
    link = entry.get("link")
    if link:
        og = og_image(link)
        if og and og not in seen:
            yield og


def measure_source(src):
    feed = feedparser.parse(src["feed"])
    for entry in feed.entries:
        text = (entry.get("title", "") + entry.get("summary", "")).lower()
        if not any(k in text for k in KEYWORDS):
            continue
        for url in candidate_images(entry):
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

#!/usr/bin/env python3
"""
Trump Oranje-Index — dagelijkse meet-engine.
Leest per nieuwsbron de RSS-feed, zoekt het recentste Trump-artikel met een
bruikbare gezichtsfoto, en berekent de Oranje-Index met exact dezelfde logica
als de webpagina. Schrijft data.json weg (die Netlify dan serveert).

pip install feedparser pillow requests opencv-python-headless numpy
"""

import io, re, json, datetime, colorsys, calendar
from zoneinfo import ZoneInfo
import requests, feedparser, numpy as np, cv2
from PIL import Image

# Haar-gezichtsdetector (zit in opencv-python-headless ingebakken)
FACE_CASCADE = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")


def detect_faces(pil_img):
    """Geeft de gevonden gezichten als (x, y, w, h)-boxen (streng afgesteld)."""
    gray = cv2.cvtColor(np.array(pil_img), cv2.COLOR_RGB2GRAY)
    return FACE_CASCADE.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=7, minSize=(60, 60))

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
TZ_NAME = "Europe/Brussels"    # tijdzone voor de uur/dag-statistiek

# ---- ernst-lexicon (grof, bewust transparant — pas gerust aan) ----
# Nucleair telt ALLEEN als top-ernst wanneer er ook een conflict-context is
# ('nuclear energy' / 'nuclear plant' mag de meter niet laten ontploffen).
NUCLEAR_WORDS = ["nuclear", "nuke", "atomic", "atoom", "kernwapen",
                 "kernoorlog", "warhead", "kernkop"]
CONFLICT_CTX = ["war", "weapon", "strike", "attack", "missile", "bomb", "launch",
                "military", "threat", "arsenal", "oorlog", "wapen", "aanval",
                "raket", "dreig"]
EXPLICIT_EXISTENTIAL = ["nuclear war", "nuclear weapon", "nuclear strike",
                        "nuclear attack", "first strike", "kernoorlog",
                        "kernwapen", "atoomwapen"]

# Per tier: (basisscore, label, [trefwoorden]). Hoogste tier met een treffer wint.
SEVERITY_TIERS = [
    (82, "Oorlog / militair", [
        "war", "invasion", "invade", "airstrike", "air strike", "missile", "bombing",
        "bombard", "warship", "troops", "ground troops", "military strike", "combat",
        "oorlog", "invasie", "luchtaanval", "raket", "bombardement", "gevechten"]),
    (66, "Escalatie / crisis", [
        "sanction", "embargo", "martial law", "insurrection", "coup", "crackdown",
        "constitutional crisis", "impeach", "national guard", "state of emergency",
        "seize", "sancties", "staat van beleg", "staatsgreep", "noodtoestand",
        "afzetting", "inval", "grondwettelijke crisis"]),
    (50, "Concrete maatregel", [
        "executive order", "signs order", "signs bill", "tariff", "deport",
        "travel ban", "pardon", "fired", "fires ", "dismiss", "decree", "shutdown",
        "veto", "freeze", "tarief", "decreet", "deporteren", "ontslag", "gratie",
        "verbod", "blokkeert", "schrapt", "ondertekent"]),
    (34, "Beleidssignaal / dreiging", [
        "threaten", "threat", "vows", "warns", "plans to", "proposes", "pledges",
        "demands", "calls for", "considers", "weighs", "dreigt", "belooft",
        "waarschuwt", "eist", "kondigt aan", "overweegt", "wil "]),
    (20, "Politiek rumoer", [
        "slams", "attacks", "mocks", "blasts", "rips", "claims", "insists",
        "lawsuit", "sues", "feud", "spat", "rally", "endorses", "blames",
        "haalt uit", "beschuldigt", "rechtszaak", "ruzie", "sneert", "hekelt"]),
    (8, "Triviaal", [
        "golf", "dinner", "mar-a-lago", "melania", "party", "cake", "birthday",
        "selfie", "gala", "golft", "diner", "feest", "verjaardag", "vakantie"]),
]
DEFAULT_SEV = (15, "Onbepaald", "")
LAST_N = 10                    # de 10 recentste koppen tonen we bij de Ernst-Index


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
    """-> (oi, hex) of None als er geen ECHT gezicht in de foto zit.
    Eist een gedetecteerd gezicht (geen kast/gebouw/logo meer) en meet de
    huidtint bínnen dat gezicht, niet op de hele foto."""
    img = Image.open(io.BytesIO(img_bytes)).convert("RGB")
    img.thumbnail((640, 640))        # genoeg resolutie voor gezichtsdetectie
    faces = detect_faces(img)
    if len(faces) == 0:
        return None                  # geen gezicht → onbruikbaar
    x, y, w, h = max(faces, key=lambda f: f[2] * f[3])   # grootste gezicht
    if w * h < 0.015 * img.width * img.height:            # te klein → wellicht valse treffer
        return None
    face = img.crop((int(x), int(y), int(x + w), int(y + h)))
    sr = sg = sb = n = 0
    for r, g, b in face.getdata():
        if is_skin(r, g, b):
            sr += r; sg += g; sb += b; n += 1
    if n < 30:
        return None
    r, g, b = sr / n, sg / n, sb / n
    hue, sat, _ = rgb2hsv(r, g, b)
    oi = max(0.0, min(100.0, hue_gate(hue) * (max(0, sat - REF_S) / 0.55) * 100))
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


OTHER_POLITICIANS = [
    "biden", "kamala", "harris", "obama", "vance", "pence", "clinton", "pelosi",
    "newsom", "desantis", "putin", "zelensky", "zelenskyy", "netanyahu", "macron",
    "starmer", "modi", "xi-jinping", "jinping", "musk",
]


def _name_ok(url):
    """Een beeld-URL is OK als 'trump' erin staat, of als er géén andere
    bekende politicus in de bestandsnaam zit."""
    u = (url or "").lower()
    if "trump" in u:
        return True
    return not any(name in u for name in OTHER_POLITICIANS)


def candidate_images(entry):
    """RSS-beelden eerst ('trump' in de naam vooraan, andere politici eruit),
    daarna de og:image van het artikel."""
    rss, seen = [], set()
    for u in image_urls(entry):
        if u and u not in seen and _name_ok(u):
            seen.add(u); rss.append(u)
    rss.sort(key=lambda u: 0 if "trump" in u.lower() else 1)   # trump-foto's voorrang
    for u in rss:
        yield u
    link = entry.get("link")
    if link:
        og = og_image(link)
        if og and og not in seen and _name_ok(og):
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
                        "oi": oi, "swatch": hexcol, "photo": url,
                        "ts": datetime.datetime.now().strftime("%H:%M"),
                    }
            except Exception as e:
                print(f"  ! {src['label']} {url[:50]}… → {e}")
    return None


# ---------------- ernst-scoring (trefwoord-heuristiek) ----------------
def score_severity(text):
    """Geeft (score, tier-label, gevonden_trefwoord) voor een stuk tekst."""
    t = text.lower()
    # Top-ernst alleen bij nucleair MÉT conflict-context (niet 'nuclear energy').
    if any(p in t for p in EXPLICIT_EXISTENTIAL) or \
       (any(w in t for w in NUCLEAR_WORDS) and any(c in t for c in CONFLICT_CTX)):
        return 95, "Nucleair conflict", "nuclear + oorlog"
    # Anders de getrapte lexicon: hoogste tier met een treffer wint.
    for base, label, words in SEVERITY_TIERS:
        hits = [w.strip() for w in words if w in t]
        if hits:
            sev = min(100, base + 2 * (len(set(hits)) - 1))   # kleine bonus per extra treffer
            return sev, label, hits[0].strip()
    return DEFAULT_SEV


def _entry_epoch(entry):
    st = entry.get("published_parsed") or entry.get("updated_parsed")
    return calendar.timegm(st) if st else 0


def gather_items():
    """Alle unieke Trump-items uit alle feeds, gescoord op ernst, recentste eerst."""
    items, seen = [], set()
    for src in SOURCES:
        feed = feedparser.parse(src["feed"])
        for entry in feed.entries:
            title = (entry.get("title") or "").strip()
            summary = entry.get("summary") or ""
            blob = (title + " " + summary)
            if "trump" not in blob.lower() or not title:
                continue
            key = title.lower()[:80]
            if key in seen:
                continue
            seen.add(key)
            sev, tier, kw = score_severity(blob)
            epoch = _entry_epoch(entry)
            ts = (datetime.datetime.fromtimestamp(epoch, datetime.timezone.utc).strftime("%d/%m %H:%M")
                  if epoch else datetime.datetime.now().strftime("%d/%m %H:%M"))
            items.append({
                "label": src["label"], "sev": sev, "tier": tier, "kw": kw,
                "title": title, "url": entry.get("link", ""),
                "ts": ts, "_epoch": epoch, "_key": key,
            })
    items.sort(key=lambda x: x["_epoch"], reverse=True)   # recentste eerst
    return items


def gravity_from(items):
    """De LAST_N recentste koppen + de piek-ernst daarbinnen (voor de meter)."""
    latest = [{k: v for k, v in it.items() if not k.startswith("_")} for it in items[:LAST_N]]
    peak = max((it["sev"] for it in latest), default=0)
    return {"index": peak, "count": len(items), "items": latest}


# ---------------- volume-statistiek (accumuleert over de dagen) ----------------
STATS_FILE = "stats.json"


def load_stats():
    try:
        with open(STATS_FILE, encoding="utf-8") as f:
            s = json.load(f)
    except Exception:
        s = {}
    s.setdefault("by_hour", [0] * 24)
    s.setdefault("by_weekday", [0] * 7)        # 0 = maandag … 6 = zondag
    s.setdefault("by_week_of_month", [0] * 5)  # week 1..5 binnen de maand
    s.setdefault("total", 0)
    s.setdefault("seen", {})                   # key -> iso (ontdubbeling tussen runs)
    s.setdefault("since", None)
    s["by_hour"] = (list(s["by_hour"]) + [0] * 24)[:24]
    s["by_weekday"] = (list(s["by_weekday"]) + [0] * 7)[:7]
    s["by_week_of_month"] = (list(s["by_week_of_month"]) + [0] * 5)[:5]
    return s


def update_volume(items):
    """Telt elke NIEUWE kop bij in de uur/weekdag/week-histogrammen, op basis
    van het publicatietijdstip. Ontdubbelt tussen runs via een 'seen'-venster,
    zodat feeds die een kop dagenlang blijven tonen niet dubbel tellen."""
    tz = ZoneInfo(TZ_NAME)
    stats = load_stats()
    now = datetime.datetime.now(datetime.timezone.utc)
    if not stats["since"]:
        stats["since"] = now.date().isoformat()
    for it in items:
        key = it["_key"]
        if key in stats["seen"]:
            continue
        stats["seen"][key] = now.isoformat()
        if not it["_epoch"]:
            continue
        local = datetime.datetime.fromtimestamp(it["_epoch"], tz)
        stats["by_hour"][local.hour] += 1
        stats["by_weekday"][local.weekday()] += 1
        stats["by_week_of_month"][min(4, (local.day - 1) // 7)] += 1
        stats["total"] += 1
    cutoff = (now - datetime.timedelta(days=10)).isoformat()
    stats["seen"] = {k: v for k, v in stats["seen"].items() if v >= cutoff}
    stats["updated"] = now.isoformat()
    with open(STATS_FILE, "w", encoding="utf-8") as f:
        json.dump(stats, f, ensure_ascii=False, indent=2)
    return stats


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

    print("\n→ Ernst-scoring + volume over alle feeds…")
    items = gather_items()
    gravity = gravity_from(items)
    stats = update_volume(items)
    print(f"  {gravity['count']} koppen · piek-ernst {gravity['index']} · totaal geteld {stats['total']}")

    if not out and not gravity["items"]:
        print("Niets gemeten, data.json niet overschreven.")
        return

    data = {
        "updated": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "live": True,
        "sources": out,
        "gravity": gravity,
        "volume": {
            "by_hour": stats["by_hour"],
            "by_weekday": stats["by_weekday"],
            "by_week_of_month": stats["by_week_of_month"],
            "total": stats["total"],
            "since": stats["since"],
            "tz": TZ_NAME,
        },
    }
    with open("data.json", "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    if out:
        avg = sum(s["oi"] for s in out) / len(out)
        print(f"\nGem. Oranje-Index {avg:.1f} · piek-ernst {gravity['index']} — data.json geschreven.")
    else:
        print(f"\nGeen foto's, wel {gravity['count']} ernst-items — data.json geschreven.")


if __name__ == "__main__":
    main()

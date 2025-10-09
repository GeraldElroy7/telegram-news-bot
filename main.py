import os, json, time, re, hashlib
import requests
import feedparser
from bs4 import BeautifulSoup
from telegram import Bot
from datetime import datetime, timezone

# ========= Konfigurasi =========
FEEDS = {
    "CNBC": "https://www.cnbcindonesia.com/market/rss",
    "Kontan": "https://www.kontan.co.id/rss",
    "Bisnis": "https://www.bisnis.com/rss",
    "IDNFinancials": "https://www.idnfinancials.com/rss/news",
    "IDX": "https://www.idx.co.id/umbraco/Surface/RssFeed/GetRssFeed?feedName=News"
}

KEYWORDS = [
    "IHSG","BEI","IDX","rupiah","inflasi","BI rate","suku bunga","obligasi","SUN",
    "BBCA","BBRI","BMRI","BBNI","ASII","TLKM","ANTM","INCO","MDKA","ADRO","PGAS",
    "PTBA","BRIS","AMMN","GOTO","ARTO","UNVR","ICBP","INDF","KLBF","CPIN","SMGR",
    "INTP","ASSA","BUKA","SIDO","HEAL","MTEL","MEDC","IPO","dividen","buyback",
    "emiten","right issue","pasar modal","saham"
]
ALLOW_ALL_IF_NO_MATCH = False
SUMMARY_LIMIT = 600

# ========= ENVIRONMENT (isi di GitHub Secrets) =========
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHANNEL_ID = os.getenv("CHANNEL_ID")  # contoh: @HappyTradeNews
HF_TOKEN = os.getenv("HF_TOKEN")      # opsional, kalau kosong pakai ringkasan sederhana

assert BOT_TOKEN and CHANNEL_ID, "BOT_TOKEN dan CHANNEL_ID wajib diisi lewat GitHub Secrets"

bot = Bot(token=BOT_TOKEN)

# ========= Cache anti-dobel =========
DB_PATH = "sent_db.json"
if os.path.exists(DB_PATH):
    with open(DB_PATH, "r", encoding="utf-8") as f:
        sent_db = json.load(f)
else:
    sent_db = {"items": []}
sent_hashes = set(sent_db.get("items", []))

# ========= Utility Functions =========
def normalize_text(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()

def sentence_split(text: str):
    return [p.strip() for p in re.split(r"(?<=[.!?])\s+", text) if p.strip()]

def simple_lead_summary(text: str, max_chars: int = SUMMARY_LIMIT) -> str:
    sents = sentence_split(text)
    out, total = [], 0
    for s in sents:
        if total + len(s) > max_chars or len(out) >= 3:
            break
        out.append(s)
        total += len(s)
    return " ".join(out) if out else text[:max_chars]

def hf_summarize(text: str, max_chars: int = SUMMARY_LIMIT) -> str:
    if not HF_TOKEN:
        return simple_lead_summary(text, max_chars)
    endpoint = "https://api-inference.huggingface.co/models/facebook/bart-large-cnn"
    headers = {"Authorization": f"Bearer {HF_TOKEN}"}
    try:
        r = requests.post(endpoint, headers=headers, json={"inputs": text[:2000]}, timeout=60)
        r.raise_for_status()
        data = r.json()
        if isinstance(data, list) and data and "summary_text" in data[0]:
            return normalize_text(data[0]["summary_text"])[:max_chars]
    except Exception as e:
        print(f"[WARN] Summarization fallback: {e}")
    return simple_lead_summary(text, max_chars)

def fetch_article_text(url: str) -> str:
    try:
        resp = requests.get(url, timeout=20, headers={"User-Agent": "Mozilla/5.0 (NewsBot)"})
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "lxml")
        for tag in soup(["script","style","nav","header","footer","aside"]):
            tag.decompose()
        paras = [normalize_text(p.get_text(" ")) for p in soup.find_all("p")]
        return " ".join([p for p in paras if len(p) > 40])[:8000]
    except Exception:
        return ""

def match_keywords(title: str, summary: str, body: str) -> bool:
    blob = f"{title} {summary} {body}".lower()
    for k in KEYWORDS:
        if k.lower() in blob:
            return True
    return ALLOW_ALL_IF_NO_MATCH

def mk_hash(source: str, title: str, link: str) -> str:
    return hashlib.sha256(f"{source}::{title}::{link}".encode("utf-8")).hexdigest()

def format_message(source: str, title: str, link: str, summary: str) -> str:
    safe_title = title.replace("*","").replace("_","").replace("`","")
    safe_sum = summary.replace("*","").replace("_","").replace("`","")
    now = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M")
    msg = (
        f"📰 *{safe_title}*\n"
        f"_{source}_ • 🕒 {now}\n\n"
        f"{safe_sum}\n\n"
        f"👉 {link}"
    )
    return msg[:4000]

# ========= Core Logic =========
def process_feed(source: str, url: str):
    global sent_hashes
    print(f"[INFO] Checking {source} feed...")

    print(f"[TEST] Trying to fetch {url}")
    try:
        r = requests.get(url, timeout=15, headers={"User-Agent": "Mozilla/5.0 (GitHubBot)"})
        print(f"[TEST] HTTP {r.status_code}, {len(r.text)} chars")
    except Exception as e:
        print(f"[TEST ERROR] {e}")

    feed = feedparser.parse(url)
    sent_any = False

    for entry in feed.entries[:3]:  # check top 3 articles only for speed
        title = entry.get("title", "").strip()
        link = entry.get("link", "").strip()
        summary_hint = entry.get("summary", "")
        if not title or not link:
            continue

        print(f"[DEBUG] Found article: {title}")

        h = mk_hash(source, title, link)
        if h in sent_hashes:
            print(f"[SKIP] Already sent: {title}")
            continue

        # Always send at least one article for test
        body = fetch_article_text(link)
        matched = match_keywords(title, summary_hint, body)
        if not matched:
            print(f"[INFO] No keyword match for: {title}")
            # still send one article per source for testing
            if not sent_any:
                print(f"[FORCE] Sending first article anyway for test.")
            else:
                continue

        base_text = body or summary_hint or title
        summary = hf_summarize(base_text, SUMMARY_LIMIT)
        msg = format_message(source, title, link, summary)

        try:
            bot.send_message(chat_id=CHANNEL_ID, text=msg, parse_mode="Markdown")
            print(f"[SENT] {source} - {title}")
            sent_hashes.add(h)
            sent_any = True
            time.sleep(1.5)
        except Exception as e:
            print(f"[ERROR SEND] {e}")
        # send only one per source for clarity
        break

def main():
    print("[START] Running Telegram News Bot (debug mode)")
    for src, u in FEEDS.items():
        try:
            process_feed(src, u)
        except Exception as e:
            print(f"[ERROR FEED {src}] {e}")

    with open(DB_PATH, "w", encoding="utf-8") as f:
        json.dump({"items": list(sent_hashes)}, f, ensure_ascii=False, indent=2)
    print("[DONE] Debug run completed.")


if __name__ == "__main__":
    main()



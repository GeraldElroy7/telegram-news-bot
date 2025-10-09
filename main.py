import os, json, time, re, hashlib
import requests
import feedparser
from bs4 import BeautifulSoup
from telegram import Bot
from datetime import datetime, timezone

# ========= Config =========
FEEDS = {
    "CNBC": "https://api.rss2json.com/v1/api.json?rss_url=https://www.cnbcindonesia.com/market/rss",
    "Kontan": "https://api.rss2json.com/v1/api.json?rss_url=https://www.kontan.co.id/rss",
    "Bisnis": "https://api.rss2json.com/v1/api.json?rss_url=https://www.bisnis.com/rss",
    "IDNFinancials": "https://api.rss2json.com/v1/api.json?rss_url=https://www.idnfinancials.com/rss/news",
    "IDX": "https://api.rss2json.com/v1/api.json?rss_url=https://www.idx.co.id/umbraco/Surface/RssFeed/GetRssFeed?feedName=News"
}

KEYWORDS = [
    "IHSG","BEI","IDX","rupiah","inflasi","BI rate","suku bunga","obligasi","SUN",
    "BBCA","BBRI","BMRI","BBNI","ASII","TLKM","ANTM","INCO","MDKA","ADRO","PGAS",
    "PTBA","BRIS","AMMN","GOTO","ARTO","UNVR","ICBP","INDF","KLBF","CPIN","SMGR",
    "INTP","ASSA","BUKA","SIDO","HEAL","MTEL","MEDC","IPO","dividen","buyback",
    "emiten","right issue","pasar modal","saham","ekonomi","market","bursa"
]
ALLOW_ALL_IF_NO_MATCH = True
SUMMARY_LIMIT = 600

# ========= Environment =========
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHANNEL_ID = os.getenv("CHANNEL_ID")
THREAD_ID = os.getenv("THREAD_ID")
HF_TOKEN = os.getenv("HF_TOKEN")

assert BOT_TOKEN and CHANNEL_ID, "BOT_TOKEN and CHANNEL_ID must be set in GitHub Secrets"
bot = Bot(token=BOT_TOKEN)

# ========= Cache =========
DB_PATH = "sent_db.json"
if os.path.exists(DB_PATH):
    with open(DB_PATH, "r", encoding="utf-8") as f:
        sent_db = json.load(f)
else:
    sent_db = {"items": []}
sent_hashes = set(sent_db.get("items", []))

# ========= Utility =========
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
    except Exception as e:
        print(f"[ERROR FETCH] {url} -> {e}")
        return ""

def match_keywords(title: str, summary: str, body: str):
    blob = f"{title} {summary} {body}".lower()
    matched = [k for k in KEYWORDS if k.lower() in blob]
    return matched

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
    feed = feedparser.parse(url)
    sent_count = 0
    total_checked = 0

    for entry in feed.entries:
        if sent_count >= 3:
            break
        total_checked += 1

        title = entry.get("title", "").strip()
        link = entry.get("link", "").strip()
        summary_html = entry.get("summary", entry.get("description", ""))
        summary_hint = BeautifulSoup(summary_html, "lxml").get_text(" ", strip=True)

        if not title or not link:
            print(f"[SKIP] {source} - Missing title/link")
            continue

        h = mk_hash(source, title, link)
        if h in sent_hashes:
            print(f"[SKIP] {source} - Already sent: {title}")
            continue

        body = fetch_article_text(link)
        matched = match_keywords(title, summary_hint, body)
        if not matched and not ALLOW_ALL_IF_NO_MATCH:
            print(f"[SKIP] {source} - No keyword match: {title}")
            continue

        print(f"[MATCH] {source} - {title} | Keywords: {matched if matched else 'All allowed'}")
        base_text = body or summary_hint or title
        summary = hf_summarize(base_text, SUMMARY_LIMIT)
        msg = format_message(source, title, link, summary)

        try:
            if THREAD_ID:
                bot.send_message(
                    chat_id=CHANNEL_ID,
                    text=msg,
                    parse_mode="Markdown",
                    message_thread_id=int(THREAD_ID)
                )
                print(f"[SENT] {source} -> thread {THREAD_ID}")
            else:
                bot.send_message(chat_id=CHANNEL_ID, text=msg, parse_mode="Markdown")
                print(f"[SENT] {source} -> main chat")

            sent_hashes.add(h)
            sent_count += 1
            time.sleep(2)
        except Exception as e:
            print(f"[ERROR SEND] {source} - {title} -> {e}")

    print(f"[SUMMARY] {source}: checked={total_checked}, sent={sent_count}")

def main():
    print("[START] Running Telegram News Bot (debug mode)")
    print(f"BOT: {CHANNEL_ID}, THREAD: {THREAD_ID}, ALLOW_ALL_IF_NO_MATCH={ALLOW_ALL_IF_NO_MATCH}")
    for src, u in FEEDS.items():
        try:
            process_feed(src, u)
        except Exception as e:
            print(f"[ERROR FEED {src}] {e}")

    with open(DB_PATH, "w", encoding="utf-8") as f:
        json.dump({"items": list(sent_hashes)}, f, ensure_ascii=False, indent=2)
    print("[DONE] Bot run completed.")

if __name__ == "__main__":
    main()


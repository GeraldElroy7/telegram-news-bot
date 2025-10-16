import os, json, re, hashlib, asyncio, time
import requests
from bs4 import BeautifulSoup
from telegram import Bot
from datetime import datetime, timezone
from html import unescape

# ========= Konfigurasi =========
RSS_BASE = "https://api.rss2json.com/v1/api.json?rss_url="

FEEDS = {
    "CNBC": RSS_BASE + "https://www.cnbcindonesia.com/market/rss",
    "CNN Indonesia": RSS_BASE + "https://www.cnnindonesia.com/ekonomi/rss",
    "Kompas": RSS_BASE + "https://rss.kompas.com/kompascom_ekonomi",
    "Okezone": RSS_BASE + "https://economy.okezone.com/rss",
    "Tempo": RSS_BASE + "https://rss.tempo.co/bisnis",
    "Detik": RSS_BASE + "https://finance.detik.com/rss"
}

KEYWORDS = [
    "IHSG","BEI","IDX","LQ45","BI rate","suku bunga","emas",
    "BBCA","BBRI","BMRI","BBNI","ASII","TLKM","ANTM","INCO","MDKA","ADRO","PGAS",
    "PTBA","BRIS","AMMN","GOTO","ARTO","UNVR","ICBP","INDF","KLBF","CPIN","SMGR",
    "INTP","ASSA","BUKA","SIDO","CDIA","MTEL","MEDC","IPO","dividen","buyback",
    "emiten","right issue","pasar modal","saham","prajogo","BRPT","CDIA"
]
ALLOW_ALL_IF_NO_MATCH = False
SUMMARY_LIMIT = 600

# ========= CONFIG BOT =========
BOT_TOKEN = "7574393949:AAEHMnzh7UyNQ6N3iZXWUJ2fhRVbO0lv9Gg"
CHANNEL_ID = -1002489204952   # group/channel id
THREAD_ID = 3750              # thread/topic id
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
def normalize_text(s): return re.sub(r"\s+", " ", s).strip()
def sentence_split(text): return [p.strip() for p in re.split(r"(?<=[.!?])\s+", text) if p.strip()]

def simple_summary(text, max_chars=SUMMARY_LIMIT):
    sents = sentence_split(text)
    return " ".join(sents[:3])[:max_chars]

def fetch_article_text(url):
    try:
        resp = requests.get(url, timeout=20, headers={"User-Agent": "Mozilla/5.0 (NewsBot)"})
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "lxml")
        for tag in soup(["script","style","nav","header","footer","aside"]): tag.decompose()
        paras = [normalize_text(p.get_text(" ")) for p in soup.find_all("p")]
        return " ".join([p for p in paras if len(p) > 40])[:8000]
    except Exception:
        return ""

def match_keywords(title, summary, body):
    blob = f"{title} {summary} {body}".lower()
    return any(k.lower() in blob for k in KEYWORDS) or ALLOW_ALL_IF_NO_MATCH

def mk_hash(source, title, link):
    return hashlib.sha256(f"{source}::{title}::{link}".encode("utf-8")).hexdigest()

def format_message(source, title, link, summary):
    safe_title = title.replace("*","").replace("_","").replace("`","")
    safe_sum = summary.replace("*","").replace("_","").replace("`","")
    now = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M")
    msg = f"📰 *{safe_title}*\n_{source}_ • 🕒 {now}\n\n{safe_sum}\n\n👉 {link}"
    return msg[:4000]

# ========= Core Logic =========
async def process_feed(source, url):
    print(f"[INFO] Fetching {source} via rss2json...")
    try:
        r = requests.get(url, timeout=30)
        data = r.json()
        entries = data.get("items", [])
    except Exception as e:
        print(f"[ERROR FETCH {source}] {e}")
        entries = []

    sent_count = 0
    for entry in entries:
        if sent_count >= 3:
            break

        title = entry.get("title", "").strip()
        link = entry.get("link", "").strip()
        summary_html = entry.get("description", "")
        if not title or not link:
            continue

        soup = BeautifulSoup(summary_html, "lxml")
        img_tag = soup.find("img")
        img_url = img_tag["src"] if img_tag else None
        summary_hint = unescape(soup.get_text(" ", strip=True))

        h = mk_hash(source, title, link)
        if h in sent_hashes:
            continue

        body = fetch_article_text(link)
        if not match_keywords(title, summary_hint, body):
            continue

        base_text = body or summary_hint or title
        summary = simple_summary(base_text)
        msg = format_message(source, title, link, summary)

        try:
            if img_url:
                await bot.send_photo(
                    chat_id=CHANNEL_ID,
                    photo=img_url,
                    caption=msg,
                    parse_mode="Markdown",
                    message_thread_id=THREAD_ID
                )
            else:
                await bot.send_message(
                    chat_id=CHANNEL_ID,
                    text=msg,
                    parse_mode="Markdown",
                    message_thread_id=THREAD_ID
                )

            print(f"[SENT] {source} - {title}")
            sent_hashes.add(h)
            sent_count += 1
            await asyncio.sleep(1.5)
        except Exception as e:
            print(f"[ERROR SEND] {e}")

    print(f"[SUMMARY] {source}: sent {sent_count} news.")

# ========= Main =========
async def main():
    print("[START] Running Telegram News Bot (async, no HuggingFace)")
    print(f"BOT posting to {CHANNEL_ID}, thread={THREAD_ID}")
    for src, u in FEEDS.items():
        await process_feed(src, u)
    with open(DB_PATH, "w", encoding="utf-8") as f:
        json.dump({"items": list(sent_hashes)}, f, ensure_ascii=False, indent=2)
    print("[DONE] Bot run completed.")

if __name__ == "__main__":
    asyncio.run(main())





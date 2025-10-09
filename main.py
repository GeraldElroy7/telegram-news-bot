import os, json, time, re, hashlib
import requests
from bs4 import BeautifulSoup
from telegram import Bot
from datetime import datetime, timezone
from html import unescape
import asyncio

# ========= Konfigurasi =========
RSS_BASE = "https://api.rss2json.com/v1/api.json?rss_url="

FEEDS = {
    "CNBC": RSS_BASE + "https://www.cnbcindonesia.com/market/rss",
    "Kontan": RSS_BASE + "https://www.kontan.co.id/rss",
    "Bisnis": RSS_BASE + "https://www.bisnis.com/rss",
    "IDNFinancials": RSS_BASE + "https://www.idnfinancials.com/rss/news",
    "IDX": RSS_BASE + "https://www.idx.co.id/umbraco/Surface/RssFeed/GetRssFeed?feedName=News"
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
BOT_TOKEN = "7574393949:AAEHMnzh7UyNQ6N3iZXWUJ2fhRVbO0lv9Gg"
CHANNEL_ID = -1002489204952     # id group/channel kamu
THREAD_ID = 3750                # id topik "NEWS" kamu
HF_TOKEN = os.getenv("HF_TOKEN")

assert BOT_TOKEN and CHANNEL_ID, "BOT_TOKEN dan CHANNEL_ID wajib diisi lewat GitHub Secrets"
bot = Bot(token=BOT_TOKEN)

# ========= Cache =========
DB_PATH = "sent_db.json"
if os.path.exists(DB_PATH):
    with open(DB_PATH, "r", encoding="utf-8") as f:
        sent_db = json.load(f)
else:
    sent_db = {"items": []}
sent_hashes = set(sent_db.get("items", []))

# ========= Helper =========
def normalize_text(s): return re.sub(r"\s+", " ", s).strip()
def sentence_split(text): return [p.strip() for p in re.split(r"(?<=[.!?])\s+", text) if p.strip()]

def simple_lead_summary(text, max_chars=SUMMARY_LIMIT):
    sents = sentence_split(text)
    out, total = [], 0
    for s in sents:
        if total + len(s) > max_chars or len(out) >= 3: break
        out.append(s); total += len(s)
    return " ".join(out) if out else text[:max_chars]

def hf_summarize(text, max_chars=SUMMARY_LIMIT):
    if not HF_TOKEN: return simple_lead_summary(text, max_chars)
    try:
        r = requests.post(
            "https://api-inference.huggingface.co/models/csebuetnlp/mT5_m2o_mbart_mlsum_summary_indonesian",
            headers={"Authorization": f"Bearer {HF_TOKEN}"},
            json={"inputs": text[:2000]}, timeout=60)
        r.raise_for_status()
        data = r.json()
        if isinstance(data, list) and data and "summary_text" in data[0]:
            return normalize_text(data[0]["summary_text"])[:max_chars]
    except Exception as e:
        print(f"[WARN] Summarization fallback: {e}")
    return simple_lead_summary(text, max_chars)

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
    for k in KEYWORDS:
        if k.lower() in blob: return True
    return ALLOW_ALL_IF_NO_MATCH

def mk_hash(source, title, link):
    return hashlib.sha256(f"{source}::{title}::{link}".encode("utf-8")).hexdigest()

def format_message(source, title, link, summary):
    safe_title = title.replace("*","").replace("_","").replace("`","")
    safe_sum = summary.replace("*","").replace("_","").replace("`","")
    now = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M")
    msg = f"📰 *{safe_title}*\n_{source}_ • 🕒 {now}\n\n{safe_sum}\n\n👉 {link}"
    return msg[:4000]

# ========= Main Logic =========
from html import unescape

def process_feed(source, url):
    global sent_hashes
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

        # 🖼️ Extract image URL if exists
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
        summary = hf_summarize(base_text)
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
            time.sleep(1.5)
        except Exception as e:
            print(f"[ERROR SEND] {e}")

def main():
    print("[START] Running Telegram News Bot (rss2json mode with topic support)")
    print(f"BOT posting to {CHANNEL_ID}, thread={THREAD_ID}")
    for src, u in FEEDS.items():
        process_feed(src, u)
    with open(DB_PATH, "w", encoding="utf-8") as f:
        json.dump({"items": list(sent_hashes)}, f, ensure_ascii=False, indent=2)
    print("[DONE] Bot run completed.")

if __name__ == "__main__":
    asyncio.run(main())






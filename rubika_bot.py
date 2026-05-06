import os
import re
import json
import asyncio
import aiohttp
import base64
import mimetypes
from datetime import datetime, timedelta
from bs4 import BeautifulSoup  # pip install beautifulsoup4

# ═══════════════════ تنظیمات ═══════════════════

TELEGRAM_PROXY_CHANNELS = [
    "iRoProxy", "P1000Y", "ProxyDaemi", "darkproxy",
    "MTProxyStar", "Myporoxy", "Proxy_Qavi", "NPROXY", "mtpproxyirani", "ProxyMTProto"
]
V2RAY_CHANNELS = ["ConfigsHubPlus", "configsmeli", "erfwp", "Connect_sho", "byiroh", "ConfigX2ray", "V2ray_TunnelVIP", "apmode_ir", "SoftNetConnect", "BlueShekan", "DarkTeam_VPN", "King_VPNx", "sponserv2raymiting", "V2rayconfigAmir", "proxymtprotoir", "v2ray_configs_pool", "V2ray_TunnelVIP", "v2rayngvpn", "V2rayEnglish"]
ALL_CHANNELS = TELEGRAM_PROXY_CHANNELS + V2RAY_CHANNELS

FILE_EXTENSIONS = [".npv", ".zip", ".ovpn", ".conf"]

# فایل‌های ذخیره‌سازی وضعیت
PROXIES_FILE     = "proxies.json"
V2RAY_FILE       = "v2ray_configs.json"
SENT_FILES_FILE  = "sent_files.json"
SENT_IDS_FILE    = "sent_filter_ids.json"       # برای فیلتر پست‌ها
LAST_SCRAPE_FILE = "last_scrape.txt"
LAST_FILTER_FILE = "last_filter.txt"

# متغیرهای محیطی ضروری (در GitHub Secrets یا env سیستم)
RUBIKA_TOKEN = os.environ["RUBIKA_BOT_TOKEN"]
CHANNEL_ID   = os.environ["RUBIKA_CHANNEL_ID"]

# فیلتر کلمات کلیدی (اختیاری، با کاما جدا کنید. مثال: "MTProto,VLESS,Trojan")
FILTER_KEYWORDS = os.environ.get("FILTER_KEYWORDS", "")
ENABLE_FILTER   = bool(FILTER_KEYWORDS.strip())
FILTER_LIST     = [k.strip() for k in FILTER_KEYWORDS.split(",") if k.strip()] if ENABLE_FILTER else []

# ═══════════════════ پیکربندی ═══════════════════

BASE_TELEGRAM    = "https://t.me/s/"
API_BASE         = f"https://botapi.rubika.ir/v3/{RUBIKA_TOKEN}"
HEADERS_TELEGRAM = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/91.0.4472.124 Safari/537.36")
}
MAX_MSG_LEN = 4000          # حداکثر طول مجاز یک پیام متنی روبیکا
MAX_ITEMS_FOR_SINGLE_MSG = 20  # اگر تعداد آیتم‌ها از این حد بگذرد، مستقیماً فایل TXT ساخته می‌شود
SEM_LIMIT = 4

# ═══════════════════ توابع ابزاری ═══════════════════

def load_json(path):
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return []

def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def get_new_items(old, new):
    old_set = set(old)
    return [item for item in new if item not in old_set]

# ═══════════════════ استخراج داده‌ها از تلگرام ═══════════════════

def extract_proxies(html):
    patterns = [
        r'https?://t\.me/proxy\?\S+',
        r'tg://proxy\?\S+',
        r'https?://t\.me/socks\?\S+',
        r'tg://socks\?\S+',
    ]
    found = set()
    for pat in patterns:
        for m in re.findall(pat, html, re.IGNORECASE):
            found.add(m.rstrip('.,;:!?"\'').strip())
    return list(found)

def extract_v2ray(html):
    pattern = r'(vmess|vless|trojan|ss)://\S+'
    raw = re.findall(pattern, html, re.IGNORECASE)
    clean = []
    for m in raw:
        m = m.rstrip('.,;:!?"\'').strip()
        if m.startswith("vmess://") and len(m) > 100:
            try:
                decoded = base64.b64decode(m[8:].encode()).decode("utf-8")
                m = "vmess://" + decoded
            except:
                pass
        clean.append(m)
    return list(set(clean))

def extract_file_links(html, extensions):
    pattern = r'href="(https?://t\.me/[^"]+?(?:' + \
              '|'.join(re.escape(ext) for ext in extensions) + r'))"'
    return list(set(re.findall(pattern, html, re.IGNORECASE)))

def extract_filtered_posts(html, channel_name):
    """پست‌هایی که شامل کلمات فیلتر باشند را برمی‌گرداند."""
    soup = BeautifulSoup(html, "html.parser")
    posts = []
    for msg_wrap in soup.select("div.tgme_widget_message_wrap"):
        data_post = msg_wrap.get("data-post")
        if not data_post:
            continue
        text_div = msg_wrap.select_one("div.tgme_widget_message_text")
        if not text_div:
            continue
        text = text_div.get_text(strip=True)
        if any(kw.lower() in text.lower() for kw in FILTER_LIST):
            link = f"https://t.me/{data_post}"
            posts.append({"id": data_post, "text": text, "link": link})
    return posts

# ═══════════════════ ارتباط با روبیکا ═══════════════════

async def post_rubika(session, method, data):
    """ارسال درخواست به API روبیکا و بررسی وضعیت 'status'"""
    url = f"{API_BASE}/{method}"
    try:
        async with session.post(url, json=data, timeout=15) as resp:
            result = await resp.json()
            if resp.status == 200 and result.get("status") == "OK":
                return result
            else:
                print(f"❌ Rubika error [{method}]: {result}")
                return None
    except Exception as e:
        print(f"🚫 Rubika connection error [{method}]: {e}")
        return None

async def send_text(session, chat_id, text):
    """ارسال یک پیام متنی ساده (در صورت طولانی بودن تقطیع می‌کند)"""
    for i in range(0, len(text), MAX_MSG_LEN):
        part = text[i:i+MAX_MSG_LEN]
        if await post_rubika(session, "sendMessage", {"chat_id": chat_id, "text": part}):
            await asyncio.sleep(0.2)
        else:
            return False
    return True

async def send_file_from_text(session, chat_id, file_name, text_content):
    """
    ساخت فایل TXT در حافظه و ارسال آن به صورت document.
    بازگشت: True اگر موفق بود، False در غیر این صورت.
    """
    file_bytes = text_content.encode("utf-8")
    b64 = base64.b64encode(file_bytes).decode("ascii")
    mime = "text/plain"
    payload = {
        "chat_id": chat_id,
        "file_name": file_name,
        "file_size": len(file_bytes),
        "file": b64,
        "mime_type": mime
    }
    result = await post_rubika(session, "sendFile", payload)
    return result is not None

async def send_items_smart(session, chat_id, header, items):
    """
    ارسال لیستی از آیتم‌ها با سیاست:
      - اگر مناسب یک پیام باشند → ارسال به صورت متنی
      - اگر خیلی زیاد باشند (بیش از MAX_ITEMS_FOR_SINGLE_MSG) → فایل TXT
      - اگر فایل ناموفق بود → ارسال دسته‌بندی‌شده (بدون نصف کردن آیتم‌ها)
    """
    if not items:
        return

    # اولویت با فایل TXT اگر تعداد خیلی زیاد باشد
    if len(items) > MAX_ITEMS_FOR_SINGLE_MSG:
        file_content = "\n".join(items)
        file_name = f"items_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
        if await send_file_from_text(session, CHANNEL_ID, file_name, file_content):
            print(f"📎 فایل {file_name} با {len(items)} آیتم ارسال شد.")
            return
        else:
            print("⚠️ خطا در ارسال فایل، فال‌بک به پیام‌های دسته‌بندی شده...")

    # روش دوم: دسته‌بندی هوشمند متنی
    full = header + "\n\n" + "\n".join(items)
    if len(full) <= MAX_MSG_LEN:
        await send_text(session, chat_id, full)
        return

    batch = []
    batch_len = len(header) + 2
    batch_idx = 1
    for item in items:
        item_len = len(item) + 1
        if batch_len + item_len > MAX_MSG_LEN:
            title = header if batch_idx == 1 else f"{header} (بخش {batch_idx})"
            msg = title + "\n\n" + "\n".join(batch)
            await send_text(session, chat_id, msg)
            await asyncio.sleep(0.5)
            batch = [item]
            batch_len = len(header) + len(f" (بخش {batch_idx+1})") + 2 + item_len
            batch_idx += 1
        else:
            batch.append(item)
            batch_len += item_len
    if batch:
        title = header if batch_idx == 1 else f"{header} (بخش {batch_idx})"
        msg = title + "\n\n" + "\n".join(batch)
        await send_text(session, chat_id, msg)

# ═══════════════════ دریافت اطلاعات کانال‌ها ═══════════════════

async def fetch_channel(session, channel, sem):
    async with sem:
        url = f"{BASE_TELEGRAM}{channel}"
        try:
            async with session.get(url, headers=HEADERS_TELEGRAM, timeout=20) as resp:
                if resp.status == 200:
                    return await resp.text()
                else:
                    print(f"⚠️ تلگرام {channel}: HTTP {resp.status}")
        except Exception as e:
            print(f"❌ تلگرام {channel}: {e}")
        return None

# ═══════════════════ روال‌های اصلی اسکرپ ═══════════════════

async def scrape_proxies_and_files(session):
    """اسکرپ پروکسی‌ها، V2Ray و فایل‌ها و ارسال موارد جدید."""
    sem = asyncio.Semaphore(SEM_LIMIT)
    tasks = [fetch_channel(session, ch, sem) for ch in ALL_CHANNELS]
    html_pages = await asyncio.gather(*tasks)

    all_proxies = set()
    all_v2ray   = set()
    all_files   = set()

    for ch, html in zip(ALL_CHANNELS, html_pages):
        if not html:
            continue
        if ch in TELEGRAM_PROXY_CHANNELS:
            proxies = extract_proxies(html)
            all_proxies.update(proxies)
            print(f"  🔹 {ch}: {len(proxies)} پروکسی")
        if ch in V2RAY_CHANNELS:
            v2rays = extract_v2ray(html)
            all_v2ray.update(v2rays)
            print(f"  🔸 {ch}: {len(v2rays)} کانفیگ")
        files = extract_file_links(html, FILE_EXTENSIONS)
        all_files.update(files)
        if files:
            print(f"  📁 {ch}: {len(files)} فایل")

    old_proxies = load_json(PROXIES_FILE)
    old_v2ray   = load_json(V2RAY_FILE)
    old_files   = load_json(SENT_FILES_FILE)

    new_proxies = get_new_items(old_proxies, all_proxies)
    new_v2ray   = get_new_items(old_v2ray, all_v2ray)
    new_files   = get_new_items(old_files, all_files)

    # ارسال پروکسی‌های جدید
    if new_proxies:
        header = f"🔰 <b>پروکسی‌های جدید</b> ({len(new_proxies)})"
        await send_items_smart(session, CHANNEL_ID, header, new_proxies)

    # ارسال V2Ray های جدید
    if new_v2ray:
        header = f"⚡️ <b>کانفیگ‌های V2Ray جدید</b> ({len(new_v2ray)})"
        await send_items_smart(session, CHANNEL_ID, header, new_v2ray)

    # فایل‌های جدید
    for file_url in new_files:
        await send_text(session, CHANNEL_ID, f"📁 <b>فایل جدید:</b>\n{file_url}")
        await asyncio.sleep(0.5)

    # ذخیره وضعیت
    if new_proxies: save_json(PROXIES_FILE, old_proxies + new_proxies)
    if new_v2ray:   save_json(V2RAY_FILE, old_v2ray + new_v2ray)
    if new_files:   save_json(SENT_FILES_FILE, old_files + new_files)

    with open(LAST_SCRAPE_FILE, "w") as f:
        f.write(datetime.now().isoformat())

    print(f"✅ اسکرپ اصلی: +{len(new_proxies)} پروکسی, +{len(new_v2ray)} V2Ray, +{len(new_files)} فایل")

async def scrape_filtered_posts(session):
    """اسکرپ پست‌های فیلتردار و ارسال تکی آن‌ها."""
    if not ENABLE_FILTER:
        return

    sem = asyncio.Semaphore(SEM_LIMIT)
    tasks = [fetch_channel(session, ch, sem) for ch in ALL_CHANNELS]
    html_pages = await asyncio.gather(*tasks)

    sent_ids = set(load_json(SENT_IDS_FILE))
    new_sent = False

    for ch, html in zip(ALL_CHANNELS, html_pages):
        if not html:
            continue
        posts = extract_filtered_posts(html, ch)
        for post in posts:
            if post["id"] in sent_ids:
                continue
            msg = f"📢 <b>پست جدید از {ch}</b>\n{post['link']}\n\n{post['text']}"
            if await send_text(session, CHANNEL_ID, msg):
                sent_ids.add(post["id"])
                new_sent = True
                print(f"   ✅ پست فیلترشده: {post['id']}")
                await asyncio.sleep(0.3)

    if new_sent:
        save_json(SENT_IDS_FILE, list(sent_ids))
        with open(LAST_FILTER_FILE, "w") as f:
            f.write(datetime.now().isoformat())
        print("✅ پست‌های فیلترشده ارسال شدند.")

# ═══════════════════ حلقه اصلی ═══════════════════

async def main():
    print(f"🚀 ربات شروع به کار کرد (فیلتر کلمات: {FILTER_LIST if ENABLE_FILTER else 'غیرفعال'})")
    async with aiohttp.ClientSession() as session:

        # ۱. اسکرپ پروکسی و فایل: هر ۲ ساعت یکبار
        try:
            with open(LAST_SCRAPE_FILE, "r") as f:
                last = datetime.fromisoformat(f.read().strip())
        except:
            last = datetime.min

        if datetime.now() - last > timedelta(hours=2):
            print("🔄 شروع اسکرپ اصلی...")
            await scrape_proxies_and_files(session)
        else:
            print("⏳ اسکرپ اصلی هنوز موعدش نرسیده.")

        # ۲. اسکرپ پست‌های فیلتردار: هر ۱۰ دقیقه یکبار (در صورت فعال بودن)
        if ENABLE_FILTER:
            try:
                with open(LAST_FILTER_FILE, "r") as f:
                    last = datetime.fromisoformat(f.read().strip())
            except:
                last = datetime.min

            if datetime.now() - last > timedelta(minutes=10):
                print("🔍 شروع اسکرپ فیلترشده...")
                await scrape_filtered_posts(session)
            else:
                print("⏳ فیلتر: هنوز موعد نرسیده.")

        print("🏁 اجرا کامل شد.")

if __name__ == "__main__":
    asyncio.run(main())

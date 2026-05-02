import os
import re
import json
import time
import asyncio
import aiohttp
from datetime import datetime, timedelta

# ═══════════ تنظیمات ═══════════
TELEGRAM_PROXY_CHANNELS = [
    "iRoProxy", "P1000Y", "ProxyDaemi", "darkproxy",
    "MTProxyStar", "Myporoxy", "Proxy_Qavi"
]
V2RAY_CHANNELS = ["ConfigsHubPlus", "configsmeli"]
ALL_CHANNELS = TELEGRAM_PROXY_CHANNELS + V2RAY_CHANNELS

FILE_EXTENSIONS = [".npv", ".zip", ".ovpn", ".conf"]

PROXIES_FILE = "proxies.json"
V2RAY_FILE = "v2ray_configs.json"
SENT_FILES_FILE = "sent_files.json"
OFFSET_FILE = "rubika_offset.txt"
LAST_SCRAPE_FILE = "last_scrape.txt"

RUBIKA_TOKEN = os.environ["BDACCD0EFPPZBPHLCZWTAIBTCNAVJMBULCAWRZOVWZIUAUZIMULPOPVWSDJSLVBX"]
CHANNEL_ID = os.environ["c0D8UIf0c4e5224e5e19123f699e7e40"]  # آیدی عددی کانال

BASE_TELEGRAM = "https://t.me/s/"
API_BASE = f"https://botapi.rubika.ir/v3/{RUBIKA_TOKEN}"

HEADERS_TELEGRAM = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 ... Chrome/91.0"
}

SEM_LIMIT = 4  # محدودیت همزمانی

# ═══════════ توابع کمکی ═══════════

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

def get_new_items(old_list, new_list):
    old_set = set(old_list)
    return [item for item in new_list if item not in old_set]

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
            found.add(m.rstrip('.,;:!?"').strip())
    return list(found)

def extract_v2ray(html):
    import base64
    pattern = r'(vmess|vless|trojan|ss)://\S+'
    raw = re.findall(pattern, html, re.IGNORECASE)
    clean = []
    for m in raw:
        m = m.rstrip('.,;:!?"').strip()
        if m.startswith("vmess://") and len(m) > 100:
            try:
                decoded = base64.b64decode(m[8:]).decode("utf-8")
                m = "vmess://" + decoded
            except:
                pass
        clean.append(m)
    return list(set(clean))

def extract_file_links(html, extensions):
    pattern = r'href="(https?://t\.me/[^"]+?(?:' + \
              '|'.join(re.escape(ext) for ext in extensions) + r'))"'
    return list(set(re.findall(pattern, html, re.IGNORECASE)))

# ═══════════ بخش Async ═══════════

async def fetch_channel(session, channel, sem):
    async with sem:
        url = f"{BASE_TELEGRAM}{channel}"
        try:
            async with session.get(url, headers=HEADERS_TELEGRAM, timeout=20) as resp:
                if resp.status == 200:
                    return await resp.text()
        except:
            pass
        return None

async def post_rubika(session, method, data):
    """ارسال درخواست به روبیکا با متد POST و JSON"""
    url = f"{API_BASE}/{method}"
    try:
        async with session.post(url, json=data) as resp:
            if resp.status != 200:
                print(f"Rubika error {method}: {await resp.text()}")
                return None
            return await resp.json()
    except Exception as e:
        print(f"Rubika request error {method}: {e}")
        return None

async def send_message(session, chat_id, text):
    """ارسال پیام متنی (تکه‌تکه برای پیام‌های بلند)"""
    MAX_LEN = 4000
    for i in range(0, len(text), MAX_LEN):
        part = text[i:i+MAX_LEN]
        await post_rubika(session, "sendMessage", {
            "chat_id": str(chat_id),
            "text": part
        })
        await asyncio.sleep(0.2)

async def send_file_link(session, chat_id, file_url, caption=""):
    """ارسال لینک فایل به صورت یک پیام (چون sendFile نیازمند آپلود است)"""
    message = f"📁 <b>فایل جدید:</b>\n{file_url}"
    if caption:
        message = f"{caption}\n{file_url}"
    await send_message(session, chat_id, message)

async def scrape_and_send(session):
    """اسکرپ کانال‌های تلگرام و ارسال موارد جدید به روبیکا"""
    sem = asyncio.Semaphore(SEM_LIMIT)
    tasks = [fetch_channel(session, ch, sem) for ch in ALL_CHANNELS]
    html_pages = await asyncio.gather(*tasks)

    all_proxies = set()
    all_v2ray = set()
    all_files = set()

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
    old_v2ray = load_json(V2RAY_FILE)
    old_files = load_json(SENT_FILES_FILE)

    new_proxies = get_new_items(old_proxies, all_proxies)
    new_v2ray = get_new_items(old_v2ray, all_v2ray)
    new_files = get_new_items(old_files, all_files)

    if new_proxies or new_v2ray:
        msg_parts = []
        if new_proxies:
            msg_parts.append(
                f"🔰 <b>پروکسی‌های جدید</b> ({len(new_proxies)})\n\n" +
                "\n".join(new_proxies)
            )
        if new_v2ray:
            msg_parts.append(
                f"⚡️ <b>کانفیگ‌های V2Ray جدید</b> ({len(new_v2ray)})\n\n" +
                "\n".join(new_v2ray)
            )
        full_msg = "\n\n".join(msg_parts)
        await send_message(session, CHANNEL_ID, full_msg)
        print("📨 پیام پروکسی/V2Ray ارسال شد.")

    for file_url in new_files:
        await send_file_link(session, CHANNEL_ID, file_url)
        await asyncio.sleep(0.5)

    if new_proxies:
        save_json(PROXIES_FILE, old_proxies + new_proxies)
    if new_v2ray:
        save_json(V2RAY_FILE, old_v2ray + new_v2ray)
    if new_files:
        save_json(SENT_FILES_FILE, old_files + new_files)

    # بروزرسانی زمان آخرین اسکرپ
    with open(LAST_SCRAPE_FILE, "w") as f:
        f.write(datetime.now().isoformat())

    print(f"✅ ذخیره شد. پروکسی جدید: {len(new_proxies)}، V2Ray: {len(new_v2ray)}، فایل: {len(new_files)}")

async def process_updates(session):
    """دریافت پیام‌های جدید روبیکا و پاسخ به فرمان‌ها"""
    try:
        with open(OFFSET_FILE, "r") as f:
            offset_id = f.read().strip()
    except:
        offset_id = None

    data = {"limit": 10}
    if offset_id:
        data["offset_id"] = offset_id

    result = await post_rubika(session, "getUpdates", data)
    if not result or "updates" not in result:
        return

    for update in result["updates"]:
        if update["type"] == "NewMessage":
            msg = update["new_message"]
            chat_id = msg["chat_id"]
            text = msg.get("text", "")

            if text == "/start":
                await send_message(session, chat_id,
                    "سلام! 👋\n"
                    "/proxies - لیست پروکسی‌ها\n"
                    "/v2ray - لیست کانفیگ‌های V2Ray\n"
                    "/files - فایل‌های جدید\n"
                    "/update - بروزرسانی دستی"
                )
            elif text == "/proxies":
                data = load_json(PROXIES_FILE)
                resp = "\n".join(data[-15:]) if data else "هیچ پروکسی ذخیره نشده."
                await send_message(session, chat_id, resp)
            elif text == "/v2ray":
                data = load_json(V2RAY_FILE)
                resp = "\n".join(data[-15:]) if data else "هیچ کانفیگی ذخیره نشده."
                await send_message(session, chat_id, resp)
            elif text == "/files":
                data = load_json(SENT_FILES_FILE)
                resp = "\n".join(data[-15:]) if data else "هیچ فایلی ذخیره نشده."
                await send_message(session, chat_id, resp)
            elif text == "/update":
                await scrape_and_send(session)
                await send_message(session, chat_id, "✅ بروزرسانی انجام شد.")

    next_offset = result.get("next_offset_id")
    if next_offset:
        with open(OFFSET_FILE, "w") as f:
            f.write(str(next_offset))

async def main():
    async with aiohttp.ClientSession() as session:
        # ۱. پردازش پیام‌های جدید روبیکا (پاسخ به فرمان‌ها)
        await process_updates(session)

        # ۲. اسکرپ خودکار (اگر بیش از ۲ ساعت از آخرین اسکرپ گذشته)
        try:
            with open(LAST_SCRAPE_FILE, "r") as f:
                last_str = f.read().strip()
                last_scrape = datetime.fromisoformat(last_str)
        except:
            last_scrape = datetime.min

        if datetime.now() - last_scrape > timedelta(hours=2):
            await scrape_and_send(session)

if __name__ == "__main__":
    asyncio.run(main())
import os
import re
from datetime import datetime
import pytz
import requests
from bs4 import BeautifulSoup

URL = "https://www.ztoe.com.ua/unhooking-search.php"
TARGET_SUBQUEUE = "5.2"
KYIV_TZ = pytz.timezone("Europe/Kiev")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
}


def is_red_cell(td) -> bool:
    """Перевіряє, чи має клітинка червоне забарвлення (відключення)."""
    style = (td.get("style") or "").lower().replace(" ", "")
    bgcolor = (td.get("bgcolor") or "").lower().strip()
    classes = " ".join(td.get("class") or []).lower()

    if any(m in style for m in ["background-color:red", "background:red", "#ff0000", "#f00", "rgb(255,0,0)"]):
        return True
    if bgcolor in ["red", "#ff0000", "#f00"]:
        return True
    if "red" in classes or "off" in classes:
        return True
    hex_match = re.search(r"background(?:-color)?:\s*#([0-9a-f]{6})", style)
    if hex_match:
        hex_val = hex_match.group(1)
        r, g, b = int(hex_val[0:2], 16), int(hex_val[2:4], 16), int(hex_val[4:6], 16)
        if r > 180 and g < 80 and b < 80:
            return True
    return False


def merge_intervals(slots: list[tuple[str, str, bool]]) -> list[str]:
    """Об'єднує сусідні півгодинні слоти відключень у суцільні діапазони."""
    outages = []
    start_time = None
    end_time = None

    for slot_start, slot_end, is_off in slots:
        if is_off:
            if start_time is None:
                start_time = slot_start
            end_time = slot_end
        else:
            if start_time is not None:
                outages.append(f"{start_time} – {end_time}")
                start_time = None
                end_time = None

    if start_time is not None:
        outages.append(f"{start_time} – {end_time}")

    return outages


def generate_48_slots() -> list[tuple[str, str]]:
    """Генерує стандартні 48 інтервалів по 30 хвилин (00:00-00:30 ... 23:30-24:00)."""
    slots = []
    for h in range(24):
        slots.append((f"{h:02d}:00", f"{h:02d}:30"))
        next_h = h + 1
        slots.append((f"{h:02d}:30", f"{next_h:02d}:00"))
    return slots


def fetch_and_parse():
    resp = requests.get(URL, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    resp.encoding = resp.apparent_encoding or "utf-8"

    soup = BeautifulSoup(resp.text, "html.parser")

    update_info = "Невідомо"
    update_match = re.search(r"Дата оновлення інформації\s*[-–—:]\s*([^\n<]+)", soup.get_text())
    if update_match:
        update_info = update_match.group(1).strip()

    schedules = []
    seen_dates = set()
    standard_slots = generate_48_slots()

    for table in soup.find_all("table"):
        table_text = table.get_text(" ", strip=True)
        date_match = re.search(r"\b(\d{2}\.\d{2}\.\d{4})\b", table_text)
        if not date_match or TARGET_SUBQUEUE not in table_text:
            continue

        date_str = date_match.group(1)
        
        if date_str in seen_dates:
            continue

        for tr in table.find_all("tr"):
            cells = tr.find_all(["td", "th"])
            cell_texts = [c.get_text(strip=True) for c in cells]

            if TARGET_SUBQUEUE in cell_texts:
                idx = cell_texts.index(TARGET_SUBQUEUE)
                slot_cells = cells[idx + 1:]

                if len(slot_cells) >= 48:
                    slot_cells = slot_cells[:48]
                    parsed_slots = []
                    for i, td in enumerate(slot_cells):
                        s_start, s_end = standard_slots[i]
                        parsed_slots.append((s_start, s_end, is_red_cell(td)))

                    outages = merge_intervals(parsed_slots)
                    total_off_hours = sum(0.5 for _, _, off in parsed_slots if off)

                    schedules.append({
                        "date": date_str,
                        "outages": outages,
                        "total_hours": total_off_hours,
                        "slots": parsed_slots
                    })
                    seen_dates.add(date_str)
                    break

    return update_info, schedules


def build_html(update_info: str, schedules: list[dict], checked_at: str) -> str:
    cards_html = ""
    for item in schedules:
        if item["outages"]:
            outage_list = "".join(f"<li>🔴 <strong>{rng}</strong></li>" for rng in item["outages"])
        else:
            outage_list = "<li>🟢 Відключень не заплановано</li>"

        timeline_blocks = "".join(
            f'<div class="slot {"off" if off else "on"}" title="{s} - {e}: {"Немає світла" if off else "Є світло"}"></div>'
            for s, e, off in item["slots"]
        )

        cards_html += f"""
        <div class="card">
            <div class="card-header">
                <h2>📅 {item['date']}</h2>
                <span class="badge">Без світла: {item['total_hours']:g} год</span>
            </div>
            <ul class="outage-list">{outage_list}</ul>
            <div class="timeline-label">Погодинна шкала (00:00 — 24:00):</div>
            <div class="timeline">{timeline_blocks}</div>
            <div class="hours-scale">
                <span>00:00</span><span>06:00</span><span>12:00</span><span>18:00</span><span>24:00</span>
            </div>
        </div>
        """

    if not cards_html:
        cards_html = '<div class="card"><p>⚠️ Не вдалося отримати дані для черги 5.2. Перевірте джерело.</p></div>'

    return f"""<!DOCTYPE html>
<html lang="uk">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Графік відключень — Черга {TARGET_SUBQUEUE} (Житомир)</title>
    <style>
        :root {{
            --bg: #0f172a;
            --card: #1e293b;
            --text: #f8fafc;
            --muted: #94a3b8;
            --red: #ef4444;
            --green: #22c55e;
            --border: #334155;
        }}
        * {{ box-sizing: border-box; margin: 0; padding: 0; }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
            background: var(--bg);
            color: var(--text);
            padding: 20px 16px;
            line-height: 1.5;
        }}
        .container {{ max-width: 640px; margin: 0 auto; }}
        header {{ text-align: center; margin-bottom: 24px; }}
        h1 {{ font-size: 1.5rem; margin-bottom: 6px; }}
        .meta {{ font-size: 0.85rem; color: var(--muted); }}
        .card {{
            background: var(--card);
            border: 1px solid var(--border);
            border-radius: 14px;
            padding: 18px;
            margin-bottom: 18px;
        }}
        .card-header {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 14px;
            border-bottom: 1px solid var(--border);
            padding-bottom: 10px;
        }}
        .card-header h2 {{ font-size: 1.15rem; }}
        .badge {{
            background: rgba(239, 68, 68, 0.15);
            color: #fca5a5;
            padding: 4px 10px;
            border-radius: 999px;
            font-size: 0.85rem;
            font-weight: 600;
        }}
        .outage-list {{ list-style: none; margin-bottom: 16px; }}
        .outage-list li {{ font-size: 1.05rem; padding: 4px 0; }}
        .timeline-label {{ font-size: 0.8rem; color: var(--muted); margin-bottom: 6px; }}
        .timeline {{
            display: grid;
            grid-template-columns: repeat(48, 1fr);
            gap: 2px;
            height: 22px;
        }}
        .slot {{ border-radius: 2px; }}
        .slot.off {{ background: var(--red); }}
        .slot.on {{ background: var(--green); opacity: 0.35; }}
        .hours-scale {{
            display: flex;
            justify-content: space-between;
            font-size: 0.72rem;
            color: var(--muted);
            margin-top: 4px;
        }}
        footer {{ text-align: center; font-size: 0.8rem; color: var(--muted); margin-top: 20px; }}
        a {{ color: #60a5fa; text-decoration: none; }}
    </style>
</head>
<body>
    <div class="container">
        <header>
            <h1>⚡ Графік відключень — Черга {TARGET_SUBQUEUE}</h1>
            <p class="meta">Житомирська область • Оновлено на сайті: <strong>{update_info}</strong></p>
            <p class="meta">Перевірено скриптом: {checked_at}</p>
        </header>
        {cards_html}
        <footer>
            Джерело: <a href="{URL}" target="_blank">АТ «Житомиробленерго»</a>
        </footer>
    </div>
</body>
</html>
"""


def send_telegram(update_info: str, schedules: list[dict], page_url: str):
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_ids_raw = os.getenv("TELEGRAM_CHAT_ID")
    
    if not token or not chat_ids_raw:
        print("Telegram credentials not set, skipping notification.")
        return

    # Розбиваємо рядок на список ID за допомогою кому або пробілів
    chat_ids = [cid.strip() for cid in re.split(r"[,;\s]+", chat_ids_raw) if cid.strip()]

    lines = [
        f"⚡ <b>Графік відключень світла | Черга {TARGET_SUBQUEUE}</b>",
        f"🕒 <i>Оновлення обленерго: {update_info}</i>\n"
    ]

    if not schedules:
        lines.append("⚠️ Не вдалося розпізнати таблицю на сайті.")
    else:
        for item in schedules:
            lines.append(f"📅 <b>{item['date']}</b> (всього без світла: {item['total_hours']:g} год):")
            if item["outages"]:
                for rng in item["outages"]:
                    lines.append(f"  🔴 <code>{rng}</code>")
            else:
                lines.append("  🟢 Відключень немає")
            lines.append("")

    if page_url:
        lines.append(f'🌐 <a href="{page_url}">Відкрити веб-сторінку з графіком</a>')

    text = "\n".join(lines)
    api_url = f"https://api.telegram.org/bot{token}/sendMessage"

    for chat_id in chat_ids:
        payload = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True
        }
        try:
            r = requests.post(api_url, json=payload, timeout=20)
            r.raise_for_status()
            print(f"Telegram message sent successfully to chat_id: {chat_id}")
        except Exception as e:
            print(f"Failed to send message to chat_id {chat_id}: {e}")


if __name__ == "__main__":
    now_kyiv = datetime.now(KYIV_TZ).strftime("%d.%m.%Y %H:%M")
    update_info, schedules = fetch_and_parse()

    html_content = build_html(update_info, schedules, now_kyiv)
    with open("index.html", "w", encoding="utf-8") as f:
        f.write(html_content)

    page_url = os.getenv("PAGES_URL", "")
    send_telegram(update_info, schedules, page_url)

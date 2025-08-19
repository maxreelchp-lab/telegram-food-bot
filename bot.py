# bot.py
# -------------------------------------------------------------
# Telegram Food Bot with Location (fallback-friendly)
# -------------------------------------------------------------
# ✅ What changed in this revision
# - **Fixed SyntaxError** caused by accidentally pasting requirements
#   lines (e.g., `requests==2.32.3`) into the Python file.
#   Those belong in a separate `requirements.txt`, NOT inside code.
# - Code now remains a clean, single Python module.
# - Added **more unit tests** for URL building, DB absence, and geo fallback.
# -------------------------------------------------------------
# 🚀 Quick start (local)
#   pip install "python-telegram-bot==21.4" requests==2.32.3
#   export TELEGRAM_TOKEN=YOUR_TOKEN  # (Windows: setx TELEGRAM_TOKEN "...")
#   python bot.py --mode bot
#
# 🧪 Test & Demo
#   python bot.py --mode test   # run unit tests
#   python bot.py --mode demo   # simulate without Telegram SDK
#
# 📦 Deployment notes (Railway/Render)
#   Create files BESIDE this bot.py (not in it):
#     requirements.txt →
#         python-telegram-bot==21.4\nrequests==2.32.3
#     Procfile →
#         worker: python bot.py --mode bot
#   Then set environment variable TELEGRAM_TOKEN to your BotFather token.
# -------------------------------------------------------------

from __future__ import annotations
import os
import sys
import time
import sqlite3
import argparse
import tempfile
from typing import Optional, Tuple, List
from urllib.parse import quote_plus

import requests

# Try importing Telegram SDK. If not available, fall back to demo/test modes.
try:
    from telegram import (
        Update,
        KeyboardButton,
        ReplyKeyboardMarkup,
        ReplyKeyboardRemove,
        InlineKeyboardMarkup,
        InlineKeyboardButton,
    )
    from telegram.ext import (
        Application,
        CommandHandler,
        MessageHandler,
        ContextTypes,
        filters,
    )
    TELEGRAM_AVAILABLE = True
except ModuleNotFoundError:
    TELEGRAM_AVAILABLE = False

# -------------------- Config --------------------
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "PUT-YOUR-TOKEN-HERE")
DB_PATH = os.getenv("DB_PATH", "bot.db")
OSM_REVERSE_URL = "https://nominatim.openstreetmap.org/reverse"
USER_AGENT = "TelegramFoodBot/1.0 (contact: you@example.com)"

BASE_WEB = "https://snappfood.ir"  # public website base
CATEGORY_QUERIES = {
    "pizza": "پیتزا",
    "kebab": "کباب",
    "burger": "برگر",
    "sandwich": "ساندویچ",
    "irani": "ایرانی",
}

# -------------------- DB --------------------

def init_db(db_path: str = DB_PATH) -> None:
    con = sqlite3.connect(db_path)
    cur = con.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            lat REAL,
            lon REAL,
            city TEXT,
            address TEXT,
            updated_at INTEGER
        )
        """
    )
    con.commit()
    con.close()


def save_user_location(
    user_id: int,
    lat: float,
    lon: float,
    city: str,
    address: str,
    db_path: str = DB_PATH,
) -> None:
    con = sqlite3.connect(db_path)
    cur = con.cursor()
    cur.execute(
        """
        INSERT INTO users (user_id, lat, lon, city, address, updated_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET
            lat=excluded.lat,
            lon=excluded.lon,
            city=excluded.city,
            address=excluded.address,
            updated_at=excluded.updated_at
        """,
        (user_id, lat, lon, city, address, int(time.time())),
    )
    con.commit()
    con.close()


def get_user_location(user_id: int, db_path: str = DB_PATH) -> Optional[Tuple[float, float, str, str]]:
    con = sqlite3.connect(db_path)
    cur = con.cursor()
    cur.execute("SELECT lat, lon, city, address FROM users WHERE user_id=?", (user_id,))
    row = cur.fetchone()
    con.close()
    if row:
        return float(row[0]), float(row[1]), row[2] or "", row[3] or ""
    return None

# -------------------- Geo --------------------

def reverse_geocode(lat: float, lon: float) -> Tuple[str, str]:
    """Return (city, address). On failure, empty strings.
    Uses OpenStreetMap Nominatim; keep request volume modest and set a UA.
    """
    try:
        params = {
            "lat": lat,
            "lon": lon,
            "format": "jsonv2",
            "accept-language": "fa,en",
        }
        headers = {"User-Agent": USER_AGENT}
        r = requests.get(OSM_REVERSE_URL, params=params, headers=headers, timeout=10)
        r.raise_for_status()
        data = r.json()
        address = data.get("display_name", "")
        comp = data.get("address", {})
        city = (
            comp.get("city")
            or comp.get("town")
            or comp.get("county")
            or comp.get("state")
            or ""
        )
        return city, address
    except Exception:
        return "", ""

# -------------------- Link Builder --------------------

def build_snappfood_link(category_key: str, city: str | None = None) -> str:
    """Build a public web search link. City is currently informational only.
    URL-encode query to be robust against non-ASCII. If you know the official
    sorting parameter for cheapest results, append it (e.g., &sort=cheap).
    """
    q = CATEGORY_QUERIES.get(category_key, "")
    return f"{BASE_WEB}/search?query={quote_plus(q)}"


def build_inline_pairs(city: str | None = None) -> List[Tuple[str, str]]:
    """Return a list[(text, url)] for inline keyboard construction.
    This is Telegram-agnostic so we can unit test it.
    """
    keys = [
        ("🍕 پیتزا ارزان", build_snappfood_link("pizza", city)),
        ("🍖 کباب ارزان", build_snappfood_link("kebab", city)),
        ("🍔 برگر ارزان", build_snappfood_link("burger", city)),
        ("🥪 ساندویچ ارزان", build_snappfood_link("sandwich", city)),
        ("🍽 ایرانی ارزان", build_snappfood_link("irani", city)),
    ]
    return keys

# -------------------- Telegram handlers (only if SDK available) --------------------
if TELEGRAM_AVAILABLE:

    async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
        btn = KeyboardButton(text="📍 ارسال لوکیشن", request_location=True)
        kb = ReplyKeyboardMarkup([[btn]], resize_keyboard=True, one_time_keyboard=True)
        await update.message.reply_text(
            "سلام! برای شروع، لطفاً لوکیشن خودت رو ارسال کن تا بر اساس همون شهر و آدرس، لینک‌های مناسب اسنپ‌فود رو بسازم.",
            reply_markup=kb,
        )

    async def handle_location(update: Update, context: ContextTypes.DEFAULT_TYPE):
        loc = update.message.location
        if not loc:
            await update.message.reply_text("لوکیشن دریافت نشد. دوباره امتحان کن.")
            return
        lat, lon = loc.latitude, loc.longitude
        city, address = reverse_geocode(lat, lon)

        user_id = update.effective_user.id
        init_db()  # ensure table exists
        save_user_location(user_id, lat, lon, city, address)

        city_txt = city or "شهر نامشخص"
        address_txt = address or "آدرس نامشخص"

        pairs = build_inline_pairs(city_txt)
        keyboard = [[InlineKeyboardButton(text, url=url)] for text, url in pairs]

        await update.message.reply_text(
            f"✅ لوکیشن ذخیره شد.\n🏙 شهر: {city_txt}\n📫 آدرس: {address_txt}\n\nحالا یکی از دسته‌بندی‌ها رو انتخاب کن:",
            reply_markup=ReplyKeyboardRemove(),
        )
        await update.message.reply_text("👇 دسته‌بندی‌ها:", reply_markup=InlineKeyboardMarkup(keyboard))

    async def mylocation(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        row = get_user_location(user_id)
        if not row:
            await update.message.reply_text("هنوز لوکیشن ثبت نکردی. /start رو بزن و لوکیشن بده.")
            return
        lat, lon, city, address = row
        await update.message.reply_text(
            f"📍 لوکیشن فعلی تو:\nLat: {lat}\nLon: {lon}\n🏙 {city}\n📫 {address}"
        )

    async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text(
            "دستورات:\n/start شروع و ثبت لوکیشن\n/mylocation دیدن لوکیشن ذخیره‌شده"
        )

    def run_bot() -> None:
        if TELEGRAM_TOKEN == "PUT-YOUR-TOKEN-HERE":
            print("[!] Please set TELEGRAM_TOKEN environment variable.")
            sys.exit(1)
        init_db()
        app = Application.builder().token(TELEGRAM_TOKEN).build()
        app.add_handler(CommandHandler("start", start))
        app.add_handler(CommandHandler("mylocation", mylocation))
        app.add_handler(CommandHandler("help", help_cmd))
        app.add_handler(MessageHandler(filters.LOCATION, handle_location))
        app.run_polling(drop_pending_updates=True)

# -------------------- Demo mode (no Telegram SDK needed) --------------------

def run_demo() -> None:
    print("=== DEMO MODE ===")
    print("Telegram SDK not required. This simulates the flow.")
    try:
        lat = float(input("Enter latitude (e.g., 35.7153 for Tehran): ").strip())
        lon = float(input("Enter longitude (e.g., 51.4043 for Tehran): ").strip())
    except Exception:
        print("Invalid input.")
        return
    city, address = reverse_geocode(lat, lon)
    city_txt = city or "شهر نامشخص"
    address_txt = address or "آدرس نامشخص"
    print(f"Resolved city: {city_txt}\nAddress: {address_txt}")

    pairs = build_inline_pairs(city_txt)
    print("Suggested buttons (text → url):")
    for text, url in pairs:
        print(f" - {text} → {url}")

# -------------------- Tests --------------------

def run_tests() -> None:
    import unittest
    from unittest.mock import patch, Mock

    class TestLinks(unittest.TestCase):
        def test_build_snappfood_link_encoding(self):
            url = build_snappfood_link("pizza", "تهران")
            self.assertIn("/search?query=", url)
            # Persian encoded
            self.assertIn("%D9%BE%DB%8C%D8%AA%D8%B2%D8%A7", url)

        def test_inline_pairs_count_and_labels(self):
            pairs = build_inline_pairs("تهران")
            self.assertEqual(len(pairs), 5)
            texts = [t for t, _ in pairs]
            self.assertIn("🍕 پیتزا ارزان", texts)
            self.assertIn("🍽 ایرانی ارزان", texts)

        def test_unknown_category_yields_empty_query(self):
            url = build_snappfood_link("unknown")
            self.assertTrue(url.endswith("/search?query="))

        def test_inline_pair_urls_start_with_base(self):
            pairs = build_inline_pairs("تهران")
            for _, url in pairs:
                self.assertTrue(url.startswith(BASE_WEB))

    class TestReverseGeocode(unittest.TestCase):
        @patch("requests.get")
        def test_reverse_geocode_success(self, mock_get):
            resp = Mock()
            resp.json.return_value = {
                "display_name": "Some Address, Tehran, Iran",
                "address": {"city": "Tehran"},
            }
            resp.raise_for_status.return_value = None
            mock_get.return_value = resp
            city, address = reverse_geocode(35.7, 51.4)
            self.assertEqual(city, "Tehran")
            self.assertTrue(address.startswith("Some Address"))

        @patch("requests.get")
        def test_reverse_geocode_fallback_to_county(self, mock_get):
            resp = Mock()
            resp.json.return_value = {
                "display_name": "Addr, Tehran County, Iran",
                "address": {"county": "Tehran County"},
            }
            resp.raise_for_status.return_value = None
            mock_get.return_value = resp
            city, address = reverse_geocode(35.7, 51.4)
            self.assertEqual(city, "Tehran County")
            self.assertTrue(address.startswith("Addr"))

        @patch("requests.get", side_effect=Exception("network error"))
        def test_reverse_geocode_failure(self, _):
            city, address = reverse_geocode(0, 0)
            self.assertEqual(city, "")
            self.assertEqual(address, "")

    class TestDB(unittest.TestCase):
        def test_save_and_get_user_location(self):
            with tempfile.NamedTemporaryFile(suffix=".db") as tf:
                path = tf.name
                init_db(path)
                save_user_location(123, 1.1, 2.2, "CityX", "AddrY", path)
                row = get_user_location(123, path)
                self.assertIsNotNone(row)
                lat, lon, city, addr = row
                self.assertAlmostEqual(lat, 1.1)
                self.assertAlmostEqual(lon, 2.2)
                self.assertEqual(city, "CityX")
                self.assertEqual(addr, "AddrY")

        def test_get_user_location_absent(self):
            with tempfile.NamedTemporaryFile(suffix=".db") as tf:
                path = tf.name
                init_db(path)
                self.assertIsNone(get_user_location(999, path))

    suite = unittest.TestLoader().loadTestsFromModule(sys.modules[__name__])
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if not result.wasSuccessful():
        sys.exit(1)

# -------------------- Entrypoint --------------------

def main(argv: Optional[List[str]] = None) -> None:
    parser = argparse.ArgumentParser(description="Telegram Food Bot with fallback modes")
    parser.add_argument(
        "--mode",
        choices=["bot", "demo", "test"],
        default=("bot" if TELEGRAM_AVAILABLE else "demo"),
        help="Execution mode",
    )
    args = parser.parse_args(argv)

    if args.mode == "test":
        run_tests()
    elif args.mode == "demo":
        run_demo()
    else:  # bot
        if not TELEGRAM_AVAILABLE:
            print("[!] python-telegram-bot not installed. Install it or use --mode demo/test.")
            sys.exit(1)
        run_bot()


if __name__ == "__main__":
    main()

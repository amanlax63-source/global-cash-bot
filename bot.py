import os
import re
import sqlite3
import logging
from datetime import datetime, timezone
from html import escape
from contextlib import contextmanager

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.constants import ChatMemberStatus
from telegram.error import BadRequest
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# ============================================================
# GLOBAL CASH BOT (አማርኛ ስሪት)
# ============================================================

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))

BOT_USERNAME = "GloballCashh_Bot"
SUPPORT_USERNAME = "@AmanM_12"

REQUIRED_CHANNELS = [
    ("@Sheger_tech1", "https://t.me/Sheger_tech1"),
    ("@EthioVortex1", "https://t.me/EthioVortex1"),
    ("@ethiocashflow", "https://t.me/ethiocashflow"),
    ("@AmanIncomeLab", "https://t.me/AmanIncomeLab"),
    ("@OnlineIncomeHub07", "https://t.me/OnlineIncomeHub07"),
    ("@Paymentprooff2", "https://t.me/Paymentprooff2"),
]

MIN_WITHDRAWAL = 30.0
DB_FILE = "global_cash.db"

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)

logger = logging.getLogger("global_cash_bot")


# ============================================================
# DATABASE HELPERS
# ============================================================

def db():
    conn = sqlite3.connect(DB_FILE, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


@contextmanager
def get_db_cursor():
    """ደህንነቱ የተጠበቀ የዳታቤዝ ግንኙነት"""
    conn = db()
    try:
        cur = conn.cursor()
        yield cur
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def now():
    return datetime.now(timezone.utc).isoformat()


def init_db():
    with get_db_cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                balance REAL NOT NULL DEFAULT 0,
                referred_by INTEGER,
                referral_paid INTEGER NOT NULL DEFAULT 0,
                referral_status TEXT NOT NULL DEFAULT 'none',
                joined_all INTEGER NOT NULL DEFAULT 0,
                suspicious INTEGER NOT NULL DEFAULT 0,
                wallet_type TEXT,
                wallet_number TEXT,
                created_at TEXT NOT NULL
            )
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS referrals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                referrer_id INTEGER NOT NULL,
                referred_id INTEGER NOT NULL UNIQUE,
                reward_amount REAL NOT NULL,
                status TEXT NOT NULL DEFAULT 'paid',
                created_at TEXT NOT NULL
            )
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS withdrawals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                amount REAL NOT NULL,
                wallet_type TEXT NOT NULL,
                wallet_number TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                created_at TEXT NOT NULL
            )
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                description TEXT NOT NULL,
                channel_username TEXT NOT NULL,
                channel_url TEXT NOT NULL,
                reward REAL NOT NULL,
                active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL
            )
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS user_tasks (
                user_id INTEGER NOT NULL,
                task_id INTEGER NOT NULL,
                verified INTEGER NOT NULL DEFAULT 0,
                paid INTEGER NOT NULL DEFAULT 0,
                paid_at TEXT,
                PRIMARY KEY (user_id, task_id)
            )
        """)

        cur.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS idx_unique_wallet
            ON users(wallet_type, wallet_number)
            WHERE wallet_type IS NOT NULL
              AND wallet_number IS NOT NULL
              AND wallet_number != ''
        """)

        cur.execute("""
            INSERT OR IGNORE INTO settings(key, value)
            VALUES('referral_reward', '2.00')
        """)


def get_user(user_id):
    conn = db()
    row = conn.execute(
        "SELECT * FROM users WHERE user_id=?",
        (user_id,),
    ).fetchone()
    conn.close()
    return row


def create_or_update_user(tg_user, referred_by=None):
    existing = get_user(tg_user.id)

    with get_db_cursor() as cur:
        if existing is None:
            ref = None
            if referred_by and referred_by != tg_user.id:
                ref = referred_by

            cur.execute("""
                INSERT INTO users(
                    user_id, username, first_name, referred_by, created_at
                ) VALUES(?,?,?,?,?)
            """, (
                tg_user.id,
                tg_user.username,
                tg_user.first_name or "",
                ref,
                now(),
            ))
        else:
            cur.execute("""
                UPDATE users
                SET username=?, first_name=?
                WHERE user_id=?
            """, (
                tg_user.username,
                tg_user.first_name or "",
                tg_user.id,
            ))


def set_joined_all(user_id, value):
    with get_db_cursor() as cur:
        cur.execute(
            "UPDATE users SET joined_all=? WHERE user_id=?",
            (1 if value else 0, user_id),
        )


def add_balance(user_id, amount):
    with get_db_cursor() as cur:
        cur.execute(
            "UPDATE users SET balance=balance+? WHERE user_id=?",
            (amount, user_id),
        )


def get_balance(user_id):
    row = get_user(user_id)
    return float(row["balance"]) if row else 0.0


def get_wallet(user_id):
    row = get_user(user_id)
    if not row or not row["wallet_type"] or not row["wallet_number"]:
        return None
    return row["wallet_type"], row["wallet_number"]


def wallet_exists_for_other_user(wallet_type, wallet_number, user_id):
    conn = db()
    row = conn.execute("""
        SELECT user_id FROM users
        WHERE wallet_type=? AND wallet_number=? AND user_id != ?
    """, (wallet_type, wallet_number, user_id)).fetchone()
    conn.close()
    return row is not None


def set_wallet(user_id, wallet_type, wallet_number):
    try:
        with get_db_cursor() as cur:
            cur.execute("""
                UPDATE users
                SET wallet_type=?, wallet_number=?
                WHERE user_id=?
            """, (wallet_type, wallet_number, user_id))
        return True
    except sqlite3.IntegrityError:
        return False


def get_setting(key, default=None):
    conn = db()
    row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    conn.close()
    return row["value"] if row else default


def set_setting(key, value):
    with get_db_cursor() as cur:
        cur.execute("""
            INSERT INTO settings(key,value) VALUES(?,?)
            ON CONFLICT(key) DO UPDATE SET value=excluded.value
        """, (key, str(value)))


def get_referral_reward():
    try:
        return float(get_setting("referral_reward", "2.00"))
    except Exception:
        return 2.0


def get_referral_count(user_id):
    conn = db()
    row = conn.execute("""
        SELECT COUNT(*) AS c FROM referrals
        WHERE referrer_id=? AND status='paid'
    """, (user_id,)).fetchone()
    conn.close()
    return int(row["c"])


def process_referral_reward(referred_id):
    user = get_user(referred_id)
    if not user or not user["referred_by"] or user["referred_by"] == referred_id:
        return None

    if user["referral_paid"] or user["suspicious"]:
        return None

    inviter = user["referred_by"]
    inviter_row = get_user(inviter)
    if not inviter_row or inviter_row["suspicious"]:
        return None

    conn = db()
    try:
        conn.execute("BEGIN IMMEDIATE")
        existing = conn.execute(
            "SELECT id FROM referrals WHERE referred_id=?", (referred_id,)
        ).fetchone()

        if existing:
            conn.execute("""
                UPDATE users SET referral_paid=1, referral_status='paid' WHERE user_id=?
            """, (referred_id,))
            conn.commit()
            return None

        reward = get_referral_reward()
        conn.execute("UPDATE users SET balance=balance+? WHERE user_id=?", (reward, inviter))
        conn.execute("""
            INSERT INTO referrals(referrer_id, referred_id, reward_amount, status, created_at)
            VALUES(?,?,?,?,?)
        """, (inviter, referred_id, reward, "paid", now()))

        conn.execute("""
            UPDATE users SET referral_paid=1, referral_status='paid' WHERE user_id=?
        """, (referred_id,))

        conn.commit()
        return {"inviter_id": inviter, "referred_id": referred_id, "amount": reward}
    except Exception:
        conn.rollback()
        logger.exception("Referral reward error")
        return None
    finally:
        conn.close()


# ============================================================
# TASKS
# ============================================================

def get_active_tasks():
    conn = db()
    rows = conn.execute("SELECT * FROM tasks WHERE active=1 ORDER BY id DESC").fetchall()
    conn.close()
    return rows


def get_task(task_id):
    conn = db()
    row = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
    conn.close()
    return row


def user_task_paid(user_id, task_id):
    conn = db()
    row = conn.execute(
        "SELECT paid FROM user_tasks WHERE user_id=? AND task_id=?", (user_id, task_id)
    ).fetchone()
    conn.close()
    return bool(row and row["paid"])


def save_user_task(user_id, task_id, verified, paid):
    with get_db_cursor() as cur:
        cur.execute("""
            INSERT INTO user_tasks(user_id, task_id, verified, paid, paid_at)
            VALUES(?,?,?,?,?)
            ON CONFLICT(user_id,task_id) DO UPDATE SET
                verified=excluded.verified,
                paid=excluded.paid,
                paid_at=excluded.paid_at
        """, (
            user_id, task_id, 1 if verified else 0, 1 if paid else 0, now() if paid else None
        ))


def create_task(title, description, username, url, reward):
    with get_db_cursor() as cur:
        cur.execute("""
            INSERT INTO tasks(title, description, channel_username, channel_url, reward, active, created_at)
            VALUES(?,?,?,?,?,1,?)
        """, (title, description, username, url, reward, now()))
        return cur.lastrowid


# ============================================================
# CHANNEL VERIFICATION & SAFE MESSAGING
# ============================================================

async def missing_channels(bot, user_id):
    missing = []
    for username, url in REQUIRED_CHANNELS:
        try:
            member = await bot.get_chat_member(chat_id=username, user_id=user_id)
            if member.status not in (
                ChatMemberStatus.MEMBER,
                ChatMemberStatus.ADMINISTRATOR,
                ChatMemberStatus.OWNER,
            ):
                missing.append((username, url))
        except Exception:
            missing.append((username, url))
    return missing


async def safe_edit_message(query, text, reply_markup=None, parse_mode="HTML"):
    try:
        await query.edit_message_text(text=text, reply_markup=reply_markup, parse_mode=parse_mode)
    except BadRequest as e:
        if "Message is not modified" in str(e):
            pass
        else:
            raise


# ============================================================
# KEYBOARDS (አማርኛ)
# ============================================================

def channel_keyboard(missing):
    buttons = [[InlineKeyboardButton(f"📢 {u}", url=url)] for u, url in missing]
    buttons.append([InlineKeyboardButton("✅ አረጋግጥ (Verify)", callback_data="verify")])
    return InlineKeyboardMarkup(buttons)


def main_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("💰 ሂሳብ (Balance)", callback_data="balance"), InlineKeyboardButton("👥 ሪፈራል (Referral)", callback_data="referral")],
        [InlineKeyboardButton("🎯 ስራዎች (Tasks)", callback_data="tasks"), InlineKeyboardButton("💸 ገንዘብ ማውጣት (Withdraw)", callback_data="withdraw")],
        [InlineKeyboardButton("👛 ወሌት (Wallet)", callback_data="wallet"), InlineKeyboardButton("📞 ድጋፍ (Support)", callback_data="support")],
    ])


def back_keyboard():
    return InlineKeyboardMarkup([[InlineKeyboardButton("🔙 ተመለስ (Back)", callback_data="home")]])


def referral_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📤 ሊንክ አጋራ", callback_data="share_ref")],
        [InlineKeyboardButton("🔙 ተመለስ", callback_data="home")],
    ])


def wallet_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🏦 የኢትዮጵያ ንግድ ባንክ (CBE)", callback_data="wallet_cbe"), InlineKeyboardButton("📱 ቴሌብር (Telebirr)", callback_data="wallet_telebirr")],
        [InlineKeyboardButton("🔙 ተመለስ", callback_data="home")],
    ])


def admin_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📊 ስታቲስቲክስ", callback_data="admin_stats"), InlineKeyboardButton("💰 የሪፈራል ክፍያ", callback_data="admin_reward")],
        [InlineKeyboardButton("👥 የሪፈራል ዝርዝር", callback_data="admin_referrals"), InlineKeyboardButton("💸 ወጪ ጥያቄዎች", callback_data="admin_withdrawals")],
        [InlineKeyboardButton("⚠️ አጠራጣሪ መለያዎች", callback_data="admin_suspicious"), InlineKeyboardButton("🎯 አዲስ ስራ ጨምር", callback_data="admin_add_task")],
    ])


# ============================================================
# USER PAGES (አማርኛ)
# ============================================================

async def show_home(query, user_id):
    await safe_edit_message(
        query,
        "💰 <b>ግሎባል ካሽ ቦት (Global Cash Bot)</b>\n\nእንኳን ደህና መጡ! 👋\nለመቀጠል ከታች ካሉት አማራጮች አንዱን ይምረጡ።",
        reply_markup=main_keyboard(),
    )


async def show_balance(query, user_id):
    balance = get_balance(user_id)
    text = (
        "💰 <b>የእርስዎ የሂሳብ መጠን</b>\n\n"
        f"💵 አጠቃላይ ቀሪ ሂሳብ፦ <b>{balance:.2f} ETB</b>\n\n"
        "ተጨማሪ ስራዎችን እና ሪፈራሎችን በመስራት ሂሳብዎን ማሳደግ ይችላሉ።"
    )
    # እዚህ ጋር የተፈለገው '🔙 ተመለስ' ብቻ እንዲኖር ነው
    await safe_edit_message(query, text, reply_markup=back_keyboard())


async def show_referral(query, user_id):
    count = get_referral_count(user_id)
    reward = get_referral_reward()
    link = f"https://t.me/{BOT_USERNAME}?start={user_id}"

    text = (
        "👥 <b>የሪፈራል መርሃ ግብር</b>\n\n"
        f"👤 የተጋበዙ አባላት፦ <b>{count} ሰዎች</b>\n"
        f"🎁 ለአንድ ሰው የሚከፈል፦ <b>{reward:.2f} ETB</b>\n\n"
        "የእርስዎን ልዩ የግብዣ ሊንክ በመጠቀም ጓደኞችዎን ይጋብዙ። "
        "እነሱ ቦቱን ተቀላቅለው ቻናሎችን ሲቀላቀሉ ክፍያው በቀጥታ ወደ ሂሳብዎ ይገባል!\n\n"
        "🔗 <b>የእርስዎ የግብዣ ሊንክ፦</b>\n"
        f"<code>{escape(link)}</code>"
    )
    await safe_edit_message(query, text, reply_markup=referral_keyboard())


async def show_tasks(query, user_id):
    tasks = get_active_tasks()
    lines = ["🎯 <b>የስራዎች ዝርዝር</b>", "", "ስራዎችን በመስራት ተጨማሪ ገንዘብ ያግኙ።", ""]
    buttons = []

    reward = get_referral_reward()
    lines.append(f"👥 <b>ሰው የመጋበዝ ስራ</b> — ለአንድ ሰው <b>{reward:.2f} ETB</b> ያግኙ።\n")

    if tasks:
        for task in tasks:
            lines.append(
                f"🎯 <b>{escape(task['title'])}</b>\n"
                f"{escape(task['description'])}\n"
                f"💰 ክፍያ፦ <b>{task['reward']:.2f} ETB</b>"
            )
            buttons.append([InlineKeyboardButton(f"🎯 {task['title']}", callback_data=f"task_{task['id']}")])
    else:
        lines.append("📌 ተጨማሪ አዳዲስ ስራዎች በቅርቡ ይጨመራሉ።")

    buttons.append([InlineKeyboardButton("🔙 ተመለስ", callback_data="home")])
    await safe_edit_message(query, "\n".join(lines), reply_markup=InlineKeyboardMarkup(buttons))


async def show_task(query, user_id, task_id):
    task = get_task(task_id)
    if not task or not task["active"]:
        await query.answer("ይህ ስራ አሁን አይገኝም።", show_alert=True)
        return

    paid = user_task_paid(user_id, task_id)
    buttons = [[InlineKeyboardButton("📢 ቻናሉን ተቀላቀል", url=task["channel_url"])]]

    if not paid:
        buttons.append([InlineKeyboardButton("✅ አረጋግጥ (Verify)", callback_data=f"verify_task_{task_id}")])
    buttons.append([InlineKeyboardButton("🔙 ተመለስ", callback_data="tasks")])

    status = "✅ <b>ተጠናቋል</b>\n\nየዚህ ስራ ክፍያ ቀደም ብሎ ገብቶልዎታል።" if paid else "⏳ <b>ሁኔታ፦</b> አልተጠናቀቀም።\n\nቻናሉን ተቀላቅለው 'አረጋግጥ' የሚለውን ይጫኑ።"

    text = (
        f"🎯 <b>{escape(task['title'])}</b>\n\n"
        f"{escape(task['description'])}\n\n"
        f"💰 ክፍያ፦ <b>{task['reward']:.2f} ETB</b>\n"
        f"📢 ቻናል፦ <b>{escape(task['channel_username'])}</b>\n\n"
        f"{status}"
    )
    await safe_edit_message(query, text, reply_markup=InlineKeyboardMarkup(buttons))


async def show_wallet(query, user_id):
    wallet = get_wallet(user_id)
    if wallet:
        wallet_type, wallet_number = wallet
        text = (
            "👛 <b>የተመዘገበ ወሌት</b>\n\n"
            f"💳 የባንክ/አገልግሎት አይነት፦ <b>{escape(wallet_type)}</b>\n"
            f"🔢 ሂሳብ ቁጥር፦ <code>{escape(wallet_number)}</code>\n\n"
            "የመረጡትን የክፍያ መንገድ መቀየር ከፈለጉ ከታች ይምረጡ።"
        )
    else:
        text = "👛 <b>ወሌት ይመዝግቡ</b>\n\nእስካሁን ምንም አይነት የክፍያ አካውንት አልመዘገቡም።\n\nእባክዎን ገንዘብ መቀበያ መንገድዎን ይምረጡ፦"

    await safe_edit_message(query, text, reply_markup=wallet_keyboard())


async def show_withdraw(query, user_id, context):
    user = get_user(user_id)
    if not user:
        return

    if user["suspicious"]:
        await safe_edit_message(
            query,
            f"⚠️ <b>የደህንነት ምርመራ</b>\n\nአካውንትዎ በኦዲት ላይ ስለሆነ ማውጣት አይችሉም።\n\n📞 ድጋፍ ለማግኘት፦ {SUPPORT_USERNAME}",
            reply_markup=back_keyboard(),
        )
        return

    balance = float(user["balance"])

    # 1. ከ 30 ETB በታች ከሆነ (ለምሳሌ Mr. B - 24 ETB)
    if balance < MIN_WITHDRAWAL:
        await safe_edit_message(
            query,
            f"❌ <b>የቀሪ ሂሳብ ማሳሰቢያ!</b>\n\n"
            f"ያልዎት ቀሪ ሂሳብ፦ <b>{balance:.2f} ETB</b> ሲሆን ዝቅተኛው የማውጫ መጠን (Minimum Withdrawal) ደግሞ <b>{MIN_WITHDRAWAL:.2f} ETB</b> ነው።\n\n"
            "ይቅርታ! ገንዘብ ለማውጣት የሚጠበቅብዎትን ዝቅተኛ መጠን አላሟሉም። እባክዎን በሪፈራል ወይም በታስክ ተጨማሪ ገንዘብ ይሰብስቡ!",
            reply_markup=back_keyboard(),
        )
        return

    # 2. ወሌት ካልመዘገበ
    wallet = get_wallet(user_id)
    if not wallet:
        await safe_edit_message(
            query,
            "💸 <b>ገንዘብ ማውጣት</b>\n\n⚠️ በመጀመሪያ ገንዘብ መቀበያ ወሌት መመዝገብ አለብዎት።\n\nእባክዎን የCBE ወይም የቴሌብር ቁጥርዎን ያስገቡ።",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("👛 ወሌት መዝግብ", callback_data="wallet")],
                [InlineKeyboardButton("🔙 ተመለስ", callback_data="home")]
            ]),
        )
        return

    # 3. ከ 30 ETB በላይ ከሆነ (ለምሳሌ Mr. A - 54 ETB)
    wallet_type, wallet_number = wallet
    context.user_data["withdraw_step"] = "amount"

    await safe_edit_message(
        query,
        f"💸 <b>ገንዘብ ማውጣት</b>\n\n"
        f"💰 ማውጣት የሚችሉት፦ <b>{balance:.2f} ETB</b>\n"
        f"🏦 መቀበያ አይነት፦ <b>{escape(wallet_type)}</b>\n"
        f"🔢 መቀበያ ቁጥር፦ <code>{escape(wallet_number)}</code>\n\n"
        "እባክዎን ማውጣት የሚፈልጉትን የገንዘብ መጠን በቁጥር ብቻ ይጻፉ፦\n\n"
        f"ዝቅተኛ፦ <b>{MIN_WITHDRAWAL:.2f} ETB</b>\n"
        f"ከፍተኛ፦ <b>{balance:.2f} ETB</b>\n\n"
        "ምሳሌ፦\n<code>50</code>",
        reply_markup=back_keyboard(),
    )


async def show_support(query, user_id):
    text = (
        "📞 <b>ማስታወቂያ እና የማህበራዊ ሚዲያ ማሳደጊያ አገልግሎት</b>\n\n"
        "የቴሌግራም ቻናል እና የተለያዩ የዲጂታል ፕሮሞሽን አገልግሎቶችን እንሰጣለን።\n\n"
        "📢 <b>የቻናል ፕሮሞሽን (Channel Promotion)</b>\n"
        "🚀 <b>የቻናል አባላት ማበራከቻ</b>\n"
        "👥 <b>የግሩፕ ማስታወቂያዎች</b>\n"
        "🏢 <b>የንግድ ድርጅት ፕሮሞሽን</b>\n"
        "📱 <b>የአፕሊኬሽን እና ዌብሳይት ፕሮሞሽን</b>\n\n"
        "ለበለጠ መረጃ እና አገልግሎቱን ለማግኘት የድጋፍ መስመራችንን ያናግሩ።"
    )
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("💬 ድጋፍ ለማግኘት ያናግሩን", url="https://t.me/AmanM_12")],
        [InlineKeyboardButton("🔙 ተመለስ", callback_data="home")],
    ])
    await safe_edit_message(query, text, reply_markup=keyboard)


# ============================================================
# CHANNEL JOIN / VERIFY
# ============================================================

async def show_join_required(update, context, missing):
    text = (
        "👋 <b>እንኳን ወደ ግሎባል ካሽ ቦት በደህና መጡ!</b>\n\n"
        "ቦቱን መጠቀም ለመጀመር እባክዎን ከታች ያሉትን ቻናሎች በሙሉ ይቀላቀሉ።\n\n"
        "ሁሉንም ከተቀላቀሉ በኋላ <b>✅ አረጋግጥ (Verify)</b> የሚለውን ይጫኑ።\n\n"
        f"📌 የቀሩዎት ቻናሎች፦ <b>{len(missing)}</b>"
    )
    if update.callback_query:
        await safe_edit_message(update.callback_query, text, reply_markup=channel_keyboard(missing))
    else:
        await update.message.reply_text(text, reply_markup=channel_keyboard(missing), parse_mode="HTML")


async def ask_wallet(query, user_id, wallet_type, context):
    context.user_data["wallet_type"] = wallet_type
    context.user_data["wallet_step"] = "number"

    if wallet_type == "CBE":
        instruction = (
            "🏦 <b>የኢትዮጵያ ንግድ ባንክ (CBE)</b>\n\nየባንክ ሂሳብ ቁጥርዎን ያስገቡ።\n\n"
            "መስፈርቶች፦\n• ልክ 13 ዲጂት (ቁጥር)\n• በ <b>1000</b> የሚጀምር\n\n"
            "ምሳሌ፦\n<code>1000123456789</code>"
        )
    else:
        instruction = (
            "📱 <b>ቴሌብር (Telebirr)</b>\n\nየቴሌብር ስልክ ቁጥርዎን ያስገቡ።\n\n"
            "መስፈርቶች፦\n• ልክ 10 ዲጂት (ቁጥር)\n• በ <b>09</b> ወይም <b>07</b> የሚጀምር\n\n"
            "ምሳሌ፦\n<code>0912345678</code>"
        )
    await safe_edit_message(query, instruction, reply_markup=back_keyboard())


# ============================================================
# ADMIN FUNCTIONS
# ============================================================

def is_admin(user_id):
    return user_id == ADMIN_ID


async def show_admin(query):
    if not is_admin(query.from_user.id):
        await query.answer("⛔ ለአድሚን ብቻ የተፈቀደ።", show_alert=True)
        return

    await safe_edit_message(
        query,
        "🛠 <b>የአድሚን መቆጣጠሪያ ፓነል</b>\n\nየሚፈልጉትን ይምረጡ፦",
        reply_markup=admin_keyboard(),
    )


async def show_admin_stats(query):
    if not is_admin(query.from_user.id):
        return

    conn = db()
    total_users = conn.execute("SELECT COUNT(*) AS c FROM users").fetchone()["c"]
    verified_users = conn.execute("SELECT COUNT(*) AS c FROM users WHERE joined_all=1").fetchone()["c"]
    total_balance = conn.execute("SELECT COALESCE(SUM(balance),0) AS s FROM users").fetchone()["s"]
    successful_referrals = conn.execute("SELECT COUNT(*) AS c FROM referrals WHERE status='paid'").fetchone()["c"]
    pending_withdrawals = conn.execute("SELECT COUNT(*) AS c FROM withdrawals WHERE status='pending'").fetchone()["c"]
    suspicious_accounts = conn.execute("SELECT COUNT(*) AS c FROM users WHERE suspicious=1").fetchone()["c"]
    conn.close()

    text = (
        "📊 <b>አጠቃላይ ስታቲስቲክስ</b>\n\n"
        f"👥 ጠቅላላ ተጠቃሚዎች፦ <b>{total_users}</b>\n"
        f"✅ የተረጋገጡ (Verified)፦ <b>{verified_users}</b>\n"
        f"💰 በቦቱ ያለ ጠቅላላ ገንዘብ፦ <b>{float(total_balance):.2f} ETB</b>\n"
        f"👥 የተሳኩ ሪፈራሎች፦ <b>{successful_referrals}</b>\n"
        f"💸 ያልተከፈሉ ወጪዎች፦ <b>{pending_withdrawals}</b>\n"
        f"⚠️ አጠራጣሪ መለያዎች፦ <b>{suspicious_accounts}</b>"
    )
    await safe_edit_message(query, text, reply_markup=back_keyboard())


async def show_admin_reward(query):
    if not is_admin(query.from_user.id):
        return

    reward = get_referral_reward()
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("✏️ ክፍያውን ቀይር", callback_data="admin_change_reward")],
        [InlineKeyboardButton("🔙 ተመለስ", callback_data="admin")],
    ])
    await safe_edit_message(
        query,
        f"💰 <b>የሪፈራል ክፍያ ቅንብር</b>\n\nአሁን ያለው ክፍያ፦ <b>{reward:.2f} ETB</b>\n\n"
        "የክፍያውን መጠን መቀየር ይችላሉ። የተቀየረው መጠን ለአዳዲስ ሪፈራሎች ብቻ ይሰራል፤",
        reply_markup=keyboard,
    )


async def show_admin_referrals(query):
    if not is_admin(query.from_user.id):
        return

    conn = db()
    rows = conn.execute("""
        SELECT referrer_id, COUNT(*) AS count
        FROM referrals WHERE status='paid'
        GROUP BY referrer_id ORDER BY count DESC LIMIT 20
    """).fetchall()
    conn.close()

    if not rows:
        text = "👥 <b>የተሳኩ ሪፈራሎች</b>\n\nእስካሁን ምንም ሪፈራል የለም።"
    else:
        lines = ["👥 <b>ከፍተኛ ሪፈራል ያደረጉ</b>", "", ""]
        for row in rows:
            lines.append(f"👤 <code>{row['referrer_id']}</code> — <b>{row['count']}</b> ያበዙ")
        text = "\n".join(lines)

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔎 ዝርዝር ፍለጋ", callback_data="admin_referral_lookup")],
        [InlineKeyboardButton("🔙 ተመለስ", callback_data="admin")],
    ])
    await safe_edit_message(query, text, reply_markup=keyboard)


async def show_admin_withdrawals(query):
    if not is_admin(query.from_user.id):
        return

    conn = db()
    rows = conn.execute("""
        SELECT * FROM withdrawals WHERE status='pending' ORDER BY id DESC LIMIT 20
    """).fetchall()
    conn.close()

    if not rows:
        await safe_edit_message(query, "💸 <b>የወጪ ጥያቄዎች</b>\n\nምንም ያልተከፈለ የገንዘብ ጥያቄ የለም።", reply_markup=back_keyboard())
        return

    buttons = []
    lines = ["💸 <b>ያልተከፈሉ የወጪ ጥያቄዎች</b>", ""]
    for row in rows:
        lines.append(
            f"🆔 <b>#{row['id']}</b> | ተጠቃሚ ID፦ <code>{row['user_id']}</code>\n"
            f"💰 መጠን፦ {row['amount']:.2f} ETB | መንገድ፦ {escape(row['wallet_type'])}\n"
            f"🔢 ቁጥር፦ <code>{escape(row['wallet_number'])}</code>\n"
        )
        buttons.append([
            InlineKeyboardButton(f"#{row['id']} ፈቅድ (Approve)", callback_data=f"approve_wd_{row['id']}"),
            InlineKeyboardButton(f"#{row['id']} ሰርዝ (Reject)", callback_data=f"reject_wd_{row['id']}"),
        ])

    buttons.append([InlineKeyboardButton("🔙 ተመለስ", callback_data="admin")])
    await safe_edit_message(query, "\n".join(lines), reply_markup=InlineKeyboardMarkup(buttons))


async def show_admin_suspicious(query):
    if not is_admin(query.from_user.id):
        return

    conn = db()
    rows = conn.execute("""
        SELECT user_id, username, first_name, balance FROM users WHERE suspicious=1 ORDER BY user_id DESC LIMIT 50
    """).fetchall()
    conn.close()

    if not rows:
        text = "⚠️ <b>አጠራጣሪ መለያዎች</b>\n\nምንም የታገደ ወይም አጠራጣሪ አካውንት የለም።"
    else:
        lines = ["⚠️ <b>የታገዱ/አጠራጣሪ መለያዎች</b>", ""]
        for row in rows:
            name = f"@{row['username']}" if row['username'] else row['first_name'] or "ስም የለውም"
            lines.append(f"👤 {escape(name)}\n🆔 <code>{row['user_id']}</code>\n💰 {row['balance']:.2f} ETB\n")
        text = "\n".join(lines)

    await safe_edit_message(query, text, reply_markup=back_keyboard())


async def admin_referral_lookup(query, context):
    if not is_admin(query.from_user.id):
        return

    context.user_data["admin_step"] = "referral_lookup"
    await safe_edit_message(
        query,
        "🔎 <b>የሪፈራል ፍለጋ</b>\n\nየጋባዡን የ Telegram User ID ያስገቡ።\n\nምሳሌ፦\n<code>123456789</code>",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ ሰርዝ", callback_data="admin")]]),
    )


async def show_referral_details(query, referrer_id):
    if not is_admin(query.from_user.id):
        return

    conn = db()
    rows = conn.execute("""
        SELECT r.referred_id, r.reward_amount, r.created_at, u.username, u.first_name
        FROM referrals r
        LEFT JOIN users u ON u.user_id = r.referred_id
        WHERE r.referrer_id=? AND r.status='paid'
        ORDER BY r.id DESC LIMIT 30
    """, (referrer_id,)).fetchall()
    conn.close()

    if not rows:
        text = f"👥 <b>የተጋበዙ ሰዎች ዝርዝር</b>\n\nየጋባዥ ID፦ <code>{referrer_id}</code>\n\nምንም ተጋባዥ አልተገኘም።"
    else:
        lines = [
            "👥 <b>የተጋበዙ ሰዎች ዝርዝር</b>", "",
            f"የጋባዥ ID፦ <code>{referrer_id}</code>",
            f"የተገኙት፦ <b>{len(rows)}</b>", ""
        ]
        for index, row in enumerate(rows, start=1):
            person = f"@{row['username']}" if row['username'] else row['first_name'] or "ስም የለውም"
            lines.append(f"{index}. 👤 <b>{escape(person)}</b>\n   🆔 <code>{row['referred_id']}</code>\n   🎁 ክፍያ፦ <b>{row['reward_amount']:.2f} ETB</b>")
        text = "\n\n".join(lines)

    await safe_edit_message(
        query,
        text,
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔙 ተመለስ", callback_data="admin_referrals")],
            [InlineKeyboardButton("🏠 አድሚን ፓነል", callback_data="admin")],
        ]),
    )


async def start_add_task(query, context):
    if not is_admin(query.from_user.id):
        return

    context.user_data.clear()
    context.user_data["admin_step"] = "task_title"
    await safe_edit_message(
        query,
        "🎯 <b>አዲስ ስራ መጨመሪያ</b>\n\nደረጃ 1/4\n\nየስራውን ርዕስ ያስገቡ።\n\nምሳሌ፦\n<code>የአማን ኢንካም ላብ ቻናልን ተቀላቀል</code>",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ ሰርዝ", callback_data="admin")]]),
    )


# ============================================================
# START & VERIFY COMMANDS
# ============================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    referrer_id = None

    if context.args:
        try:
            referrer_id = int(context.args[0])
        except ValueError:
            referrer_id = None

    create_or_update_user(user, referred_by=referrer_id)
    missing = await missing_channels(context.bot, user.id)

    if missing:
        set_joined_all(user.id, False)
        await show_join_required(update, context, missing)
        return

    set_joined_all(user.id, True)
    result = process_referral_reward(user.id)

    if result:
        try:
            await context.bot.send_message(
                chat_id=result["inviter_id"],
                text=(
                    "🎉 <b>አዲስ ሰው ጋብዘዋል!</b>\n\n"
                    f"👤 የተጋባዥ ID፦ <code>{result['referred_id']}</code>\n"
                    f"🎁 ያገኙት ክፍያ፦ <b>{result['amount']:.2f} ETB</b>\n\n"
                    "ክፍያው ወደ ሂሳብዎ ተጨምሯል።"
                ),
                parse_mode="HTML",
            )
        except Exception:
            logger.exception("Could not notify inviter.")

    await update.message.reply_text(
        "🎉 <b>እንኳን ወደ ግሎባል ካሽ ቦት በደህና መጡ!</b>\n\nአካውንትዎ በትክክል ተረጋግጧል።\n\n"
        "💰 ሰዎችን በመጋበዝ እና ስራዎችን በመስራት ገንዘብ ይሰብስቡ።\nከታች ካሉት አማራጮች ይምረጡ፦",
        reply_markup=main_keyboard(),
        parse_mode="HTML",
    )


async def verify_user(query, context):
    user_id = query.from_user.id
    missing = await missing_channels(context.bot, user_id)

    if missing:
        set_joined_all(user_id, False)
        text = (
            "❌ <b>ማረጋገጥ አልተቻለም!</b>\n\nሁሉንም ቻናሎች አልተቀላቀሉም።\n\n"
            f"📌 የቀሩዎት፦ <b>{len(missing)}</b>\n\nእባክዎን የቀሩትን ተቀላቅለው ድጋሚ <b>አረጋግጥ (Verify)</b> የሚለውን ይጫኑ።"
        )
        await safe_edit_message(query, text, reply_markup=channel_keyboard(missing))
        return

    set_joined_all(user_id, True)
    result = process_referral_reward(user_id)

    if result:
        try:
            await context.bot.send_message(
                chat_id=result["inviter_id"],
                text=(
                    "🎉 <b>አዲስ ሰው ጋብዘዋል!</b>\n\n"
                    f"👤 የተጋባዥ ID፦ <code>{result['referred_id']}</code>\n"
                    f"🎁 ያገኙት ክፍያ፦ <b>{result['amount']:.2f} ETB</b>\n\n"
                    "ክፍያው ወደ ሂሳብዎ ተጨምሯል።"
                ),
                parse_mode="HTML",
            )
        except Exception:
            logger.exception("Could not notify inviter.")

    await safe_edit_message(
        query,
        "✅ <b>በትክክል ተረጋግጧል!</b>\n\nሁሉንም ቻናሎች ተቀላቅለዋል።\n\nእንኳን ወደ <b>ግሎባል ካሽ ቦት</b> በደህና መጡ! 💰",
        reply_markup=main_keyboard(),
    )


async def verify_task(query, context, task_id):
    user_id = query.from_user.id
    task = get_task(task_id)

    if not task or not task["active"]:
        await query.answer("ይህ ስራ አሁን አይገኝም።", show_alert=True)
        return

    if user_task_paid(user_id, task_id):
        await query.answer("ይህንን ስራ አስቀድመው ሰርተዋል።", show_alert=True)
        return

    try:
        member = await context.bot.get_chat_member(chat_id=task["channel_username"], user_id=user_id)
        if member.status not in (ChatMemberStatus.MEMBER, ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER):
            await query.answer("❌ ቻናሉን ገና አልተቀላቀሉም።", show_alert=True)
            return
    except Exception:
        await query.answer("ማረጋገጥ አልተቻለም። ድጋሚ ይሞክሩ።", show_alert=True)
        return

    reward = float(task["reward"])
    add_balance(user_id, reward)
    save_user_task(user_id, task_id, verified=True, paid=True)

    await query.answer(f"🎉 +{reward:.2f} ETB ወደ ሂሳብዎ ተጨምሯል!", show_alert=True)
    await show_task(query, user_id, task_id)


# ============================================================
# WALLET & WITHDRAW PROCESSORS
# ============================================================

async def save_wallet_from_input(update, context):
    user = update.effective_user
    wallet_type = context.user_data.get("wallet_type")
    if not wallet_type:
        return False

    number = update.message.text.strip()
    valid = False

    if wallet_type == "CBE":
        valid = number.isdigit() and len(number) == 13 and number.startswith("1000")
    elif wallet_type == "Telebirr":
        valid = number.isdigit() and len(number) == 10 and (number.startswith("09") or number.startswith("07"))

    if not valid:
        msg = "❌ የገባው የባንክ ሂሳብ ቁጥር ትክክል አይደለም።\n\n13 ዲጂት መሆን እና በ 1000 መጀምር አለበት።" if wallet_type == "CBE" else "❌ የገባው የቴሌብር ቁጥር ትክክል አይደለም።\n\n10 ዲጂት መሆን እና በ 09 ወይም 07 መጀምር አለበት።"
        await update.message.reply_text(msg + "\n\nእባክዎን ድጋሚ ይሞክሩ።")
        return True

    if wallet_exists_for_other_user(wallet_type, number, user.id):
        await update.message.reply_text(
            "⚠️ <b>ይህ ቁጥር ከሌላ አካውንት ጋር ተያይዟል!</b>\n\nለደህንነት ሲባል አንድ የወሌት ቁጥር ለአንድ አካውንት ብቻ ያገለግላል።",
            parse_mode="HTML",
        )
        return True

    saved = set_wallet(user.id, wallet_type, number)
    if not saved:
        await update.message.reply_text("⚠️ ወሌቱን መመዝገብ አልተቻለም።", parse_mode="HTML")
        return True

    context.user_data.pop("wallet_type", None)
    context.user_data.pop("wallet_step", None)

    await update.message.reply_text(
        "✅ <b>ወሌትዎ በትክክል ተመዝግቧል!</b>\n\n"
        f"💳 የክፍያ መንገድ፦ <b>{escape(wallet_type)}</b>\n"
        f"🔢 ቁጥር፦ <code>{escape(number)}</code>\n\n"
        "አሁን ገንዘብ ማውጣት ይችላሉ።",
        reply_markup=main_keyboard(),
        parse_mode="HTML",
    )
    return True


async def create_withdrawal(update, context):
    user = update.effective_user
    text = update.message.text.strip()

    try:
        amount = float(text)
    except ValueError:
        await update.message.reply_text("❌ እባክዎን ትክክለኛ ቁጥር ያስገቡ።\n\nምሳሌ፦ <code>50</code>", parse_mode="HTML")
        return True

    if amount < MIN_WITHDRAWAL:
        await update.message.reply_text(f"❌ ዝቅተኛው የማውጫ መጠን <b>{MIN_WITHDRAWAL:.2f} ETB</b> ነው።", parse_mode="HTML")
        return True

    if amount <= 0:
        await update.message.reply_text("❌ ያስገቡት መጠን ከ 0 በላይ መሆን አለበት።")
        return True

    # የተጠቃሚውን አሁናዊ balance ደግመን እንፈትሻለን
    current_balance = get_balance(user.id)
    if amount > current_balance:
        await update.message.reply_text(
            f"❌ <b>የሂሳብ ማነስ!</b>\n\nያልዎት ቀሪ ሂሳብ <b>{current_balance:.2f} ETB</b> ነው። ከዚህ በላይ ማውጣት አይችሉም።",
            parse_mode="HTML",
        )
        return True

    wallet = get_wallet(user.id)
    if not wallet:
        await update.message.reply_text("❌ ምንም የተመዘገበ ወሌት አልተገኘም።\n\nእባክዎን አስቀድመው ወሌት ይመዝግቡ።", reply_markup=wallet_keyboard(), parse_mode="HTML")
        context.user_data.pop("withdraw_step", None)
        return True

    wallet_type, wallet_number = wallet

    conn = db()
    try:
        conn.execute("BEGIN IMMEDIATE")
        cur = conn.execute("""
            UPDATE users SET balance = balance - ?
            WHERE user_id=? AND balance >= ? AND suspicious=0
        """, (amount, user.id, amount))

        if cur.rowcount != 1:
            conn.rollback()
            await update.message.reply_text(
                "❌ ጥያቄዎን ማስተናገድ አልተቻለም። ቀሪ ሂሳብዎን ያረጋግጡ።",
                reply_markup=back_keyboard(),
                parse_mode="HTML",
            )
            return True

        cur = conn.execute("""
            INSERT INTO withdrawals(user_id, amount, wallet_type, wallet_number, status, created_at)
            VALUES(?,?,?,?,?,?)
        """, (user.id, amount, wallet_type, wallet_number, "pending", now()))

        withdrawal_id = cur.lastrowid
        conn.commit()
    except Exception:
        conn.rollback()
        logger.exception("Withdrawal creation failed.")
        await update.message.reply_text("❌ የወጪ ጥያቄ ሲላክ ስህተት አጋጥሟል።", reply_markup=back_keyboard())
        return True
    finally:
        conn.close()

    context.user_data.pop("withdraw_step", None)

    await update.message.reply_text(
        "✅ <b>የወጪ ጥያቄዎ በትክክል ተልኳል!</b>\n\n"
        f"🆔 የጥያቄ ቁጥር፦ <b>#{withdrawal_id}</b>\n"
        f"💰 የተጠየቀው መጠን፦ <b>{amount:.2f} ETB</b>\n"
        f"🏦 መቀበያ መንገድ፦ <b>{escape(wallet_type)}</b>\n"
        f"🔢 መቀበያ ቁጥር፦ <code>{escape(wallet_number)}</code>\n\n"
        "⏳ ሁኔታ፦ <b>በሂደት ላይ (Pending)</b>\n\n"
        "አድሚን መርምሮ በቅርቡ ገቢ ያደርግልዎታል።",
        reply_markup=main_keyboard(),
        parse_mode="HTML",
    )

    try:
        await context.bot.send_message(
            chat_id=ADMIN_ID,
            text=(
                "💳 <b>አዲስ የወጪ ጥያቄ!</b>\n\n"
                f"🆔 ጥያቄ ID፦ <b>#{withdrawal_id}</b>\n"
                f"👤 ተጠቃሚ ID፦ <code>{user.id}</code>\n"
                f"💰 መጠን፦ <b>{amount:.2f} ETB</b>\n"
                f"🏦 መንገድ፦ <b>{escape(wallet_type)}</b>\n"
                f"🔢 ቁጥር፦ <code>{escape(wallet_number)}</code>"
            ),
            parse_mode="HTML",
        )
    except Exception:
        logger.exception("Could not notify admin about withdrawal.")

    return True


async def approve_withdrawal(query, withdrawal_id, context):
    if not is_admin(query.from_user.id):
        return

    conn = db()
    cur = conn.execute("UPDATE withdrawals SET status='approved' WHERE id=? AND status='pending'", (withdrawal_id,))

    if cur.rowcount != 1:
        conn.rollback()
        conn.close()
        await query.answer("ይህ ጥያቄ ቀደም ብሎ አስተናግዷል።", show_alert=True)
        return

    row = conn.execute("SELECT * FROM withdrawals WHERE id=?", (withdrawal_id,)).fetchone()
    conn.commit()
    conn.close()

    await query.answer("✅ ጥያቄው ጸድቋል።", show_alert=True)

    try:
        await context.bot.send_message(
            chat_id=row["user_id"],
            text=(
                "✅ <b>ገንዘብዎ ተልኳል! (Approved)</b>\n\n"
                f"🆔 የጥያቄ ቁጥር፦ <b>#{row['id']}</b>\n"
                f"💰 መጠን፦ <b>{row['amount']:.2f} ETB</b>\n"
                f"🏦 መቀበያ፦ <b>{escape(row['wallet_type'])}</b>\n"
                f"🔢 ቁጥር፦ <code>{escape(row['wallet_number'])}</code>\n\n"
                "ገንዘቡ ወደ አካውንትዎ ገቢ ሆኗል። ስላገለገልንዎት ደስ ብሎናል!",
            ),
            parse_mode="HTML",
        )
    except Exception:
        logger.exception("Could not notify user about approval.")

    await show_admin_withdrawals(query)


async def reject_withdrawal(query, withdrawal_id, context):
    if not is_admin(query.from_user.id):
        return

    conn = db()
    cur = conn.execute("UPDATE withdrawals SET status='rejected' WHERE id=? AND status='pending'", (withdrawal_id,))

    if cur.rowcount != 1:
        conn.rollback()
        conn.close()
        await query.answer("ይህ ጥያቄ ቀደም ብሎ ተስተናግዷል።", show_alert=True)
        return

    row = conn.execute("SELECT * FROM withdrawals WHERE id=?", (withdrawal_id,)).fetchone()
    conn.execute("UPDATE users SET balance = balance + ? WHERE user_id=?", (row["amount"], row["user_id"]))
    conn.commit()
    conn.close()

    await query.answer("❌ ጥያቄው ተሰርዟል፤ ገንዘቡም ተመልሷል።", show_alert=True)

    try:
        await context.bot.send_message(
            chat_id=row["user_id"],
            text=(
                "❌ <b>የወጪ ጥያቄዎ አልተቀበለም!</b>\n\n"
                f"🆔 የጥያቄ ቁጥር፦ <b>#{row['id']}</b>\n"
                f"💰 መጠን፦ <b>{row['amount']:.2f} ETB</b>\n\n"
                "የተጠየቀው ገንዘብ ወደ ቦት ሂሳብዎ ተመልሷል።",
            ),
            parse_mode="HTML",
        )
    except Exception:
        logger.exception("Could not notify user about rejection.")

    await show_admin_withdrawals(query)


# ============================================================
# CALLBACK HANDLER
# ============================================================

async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    data = query.data
    user = query.from_user
    create_or_update_user(user)

    if data == "home":
        context.user_data.clear()
        missing = await missing_channels(context.bot, user.id)
        if missing:
            set_joined_all(user.id, False)
            await show_join_required(update, context, missing)
            return

        set_joined_all(user.id, True)
        await show_home(query, user.id)
        return

    if data == "verify":
        await verify_user(query, context)
        return

    if data == "balance":
        await show_balance(query, user.id)
        return

    if data == "referral":
        await show_referral(query, user.id)
        return

    if data == "tasks":
        await show_tasks(query, user.id)
        return

    if data == "withdraw":
        await show_withdraw(query, user.id, context)
        return

    if data == "wallet":
        await show_wallet(query, user.id)
        return

    if data == "support":
        await show_support(query, user.id)
        return

    if data == "share_ref":
        link = f"https://t.me/{BOT_USERNAME}?start={user.id}"
        await query.answer("የመጋበዣ ሊንክዎን ኮፒ አድርገው ይላኩ።", show_alert=True)
        await context.bot.send_message(
            chat_id=user.id,
            text=f"📤 <b>የእርስዎ የግብዣ ሊንክ፦</b>\n\n<code>{escape(link)}</code>\n\nይህንን ሊንክ ለወዳጆችዎ ያጋሩ።",
            parse_mode="HTML",
        )
        return

    if data == "wallet_cbe":
        await ask_wallet(query, user.id, "CBE", context)
        return

    if data == "wallet_telebirr":
        await ask_wallet(query, user.id, "Telebirr", context)
        return

    if data.startswith("task_"):
        try:
            task_id = int(data.split("_", 1)[1])
            await show_task(query, user.id, task_id)
        except ValueError:
            pass
        return

    if data.startswith("verify_task_"):
        try:
            task_id = int(data.split("_", 2)[2])
            await verify_task(query, context, task_id)
        except ValueError:
            pass
        return

    # ADMIN CALLBACKS
    if data == "admin":
        await show_admin(query)
        return

    if data == "admin_stats":
        await show_admin_stats(query)
        return

    if data == "admin_reward":
        await show_admin_reward(query)
        return

    if data == "admin_change_reward":
        if not is_admin(user.id):
            return
        context.user_data.clear()
        context.user_data["admin_step"] = "reward"
        await safe_edit_message(
            query,
            f"💰 <b>የሪፈራል ክፍያ መቀየሪያ</b>\n\nአሁን ያለው፦ <b>{get_referral_reward():.2f} ETB</b>\n\nአዲሱን የክፍያ መጠን በቁጥር ያስገቡ።",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ ሰርዝ", callback_data="admin_reward")]]),
        )
        return

    if data == "admin_referrals":
        await show_admin_referrals(query)
        return

    if data == "admin_referral_lookup":
        await admin_referral_lookup(query, context)
        return

    if data == "admin_withdrawals":
        await show_admin_withdrawals(query)
        return

    if data == "admin_suspicious":
        await show_admin_suspicious(query)
        return

    if data == "admin_add_task":
        await start_add_task(query, context)
        return

    if data.startswith("approve_wd_"):
        try:
            withdrawal_id = int(data.split("_")[-1])
            await approve_withdrawal(query, withdrawal_id, context)
        except ValueError:
            pass
        return

    if data.startswith("reject_wd_"):
        try:
            withdrawal_id = int(data.split("_")[-1])
            await reject_withdrawal(query, withdrawal_id, context)
        except ValueError:
            pass
        return


# ============================================================
# MESSAGE HANDLER
# ============================================================

async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not update.message:
        return

    text = update.message.text.strip()

    if is_admin(user.id):
        admin_step = context.user_data.get("admin_step")

        if admin_step == "reward":
            try:
                amount = float(text)
                if amount <= 0:
                    raise ValueError
            except ValueError:
                await update.message.reply_text("❌ እባክዎን ትክክለኛ አዎንታዊ ቁጥር ያስገቡ።")
                return

            set_setting("referral_reward", f"{amount:.2f}")
            context.user_data.pop("admin_step", None)
            await update.message.reply_text(
                f"✅ <b>የሪፈራል ክፍያ ተቀይሯል!</b>\n\nአዲሱ ክፍያ፦ <b>{amount:.2f} ETB</b>",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("💰 የክፍያ ቅንብሮች", callback_data="admin_reward")],
                    [InlineKeyboardButton("🛠 አድሚን ፓነል", callback_data="admin")],
                ]),
                parse_mode="HTML",
            )
            return

        if admin_step == "referral_lookup":
            try:
                referrer_id = int(text)
            except ValueError:
                await update.message.reply_text("❌ እባክዎን ትክክለኛ የ Telegram User ID ያስገቡ።")
                return

            context.user_data.pop("admin_step", None)
            await show_referral_details(update.message, referrer_id)
            return

        if admin_step == "task_title":
            context.user_data["task_title"] = text
            context.user_data["admin_step"] = "task_description"
            await update.message.reply_text("🎯 <b>አዲስ ስራ መጨመሪያ</b>\n\nደረጃ 2/4\n\nየስራውን ማብራሪያ ያስገቡ።", parse_mode="HTML")
            return

        if admin_step == "task_description":
            context.user_data["task_description"] = text
            context.user_data["admin_step"] = "task_channel"
            await update.message.reply_text("🎯 <b>አዲስ ስራ መጨመሪያ</b>\n\nደረጃ 3/4\n\nየቻናሉን የተጠቃሚ ስም ያስገቡ (ምሳሌ፦ <code>@MyChannel</code>)።", parse_mode="HTML")
            return

        if admin_step == "task_channel":
            username = text.strip()
            if not username.startswith("@"):
                username = "@" + username
            context.user_data["task_channel"] = username
            context.user_data["task_url"] = f"https://t.me/{username.lstrip('@')}"
            context.user_data["admin_step"] = "task_reward"
            await update.message.reply_text("🎯 <b>አዲስ ስራ መጨመሪያ</b>\n\nደረጃ 4/4\n\nለዚህ ስራ የሚከፈለው በ ETB ያስገቡ።", parse_mode="HTML")
            return

        if admin_step == "task_reward":
            try:
                reward = float(text)
                if reward <= 0:
                    raise ValueError
            except ValueError:
                await update.message.reply_text("❌ እባክዎን ትክክለኛ የክፍያ መጠን ያስገቡ።")
                return

            title = context.user_data.get("task_title", "Task")
            description = context.user_data.get("task_description", "")
            channel = context.user_data.get("task_channel", "")
            url = context.user_data.get("task_url", "")

            task_id = create_task(title, description, channel, url, reward)
            context.user_data.clear()

            await update.message.reply_text(
                f"✅ <b>ስራው በትክክል ተፈጥሯል!</b>\n\n🆔 የስራ ID፦ <b>{task_id}</b>\n🎯 ርዕስ፦ <b>{escape(title)}</b>\n📢 ቻናል፦ <b>{escape(channel)}</b>\n💰 ክፍያ፦ <b>{reward:.2f} ETB</b>",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🎯 ስራዎች", callback_data="tasks")],
                    [InlineKeyboardButton("🛠 አድሚን ፓነል", callback_data="admin")],
                ]),
                parse_mode="HTML",
            )
            return

    if context.user_data.get("wallet_step") == "number":
        if await save_wallet_from_input(update, context):
            return

    if context.user_data.get("withdraw_step") == "amount":
        if await create_withdrawal(update, context):
            return

    await update.message.reply_text("እባክዎን ከታች ያሉትን በተኖች ይጠቀሙ።", reply_markup=main_keyboard())


# ============================================================
# COMMAND HANDLERS & MAIN
# ============================================================

async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("⛔ ለአድሚን ብቻ የተፈቀደ።")
        return
    context.user_data.clear()
    await update.message.reply_text("🛠 <b>የአድሚን መቆጣጠሪያ ፓነል</b>\n\nየሚፈልጉትን ይምረጡ፦", reply_markup=admin_keyboard(), parse_mode="HTML")


async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("❌ ክንውኑ ተሰርዟል።", reply_markup=main_keyboard())


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.exception("ያልተጠበቀ ስህተት አጋጥሟል፦", exc_info=context.error)


def main():
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN አልተዘጋጀም።")
    if not ADMIN_ID:
        raise RuntimeError("ADMIN_ID አልተዘጋጀም።")

    init_db()

    application = Application.builder().token(BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("admin", admin_command))
    application.add_handler(CommandHandler("cancel", cancel_command))
    application.add_handler(CallbackQueryHandler(callback_handler))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler))
    application.add_error_handler(error_handler)

    logger.info("ግሎባል ካሽ ቦት በመስራት ላይ ይገኛል...")
    application.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()

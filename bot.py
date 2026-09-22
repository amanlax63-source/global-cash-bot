import os
import re
import sqlite3
import logging
import asyncio
from datetime import datetime, timezone
from html import escape
from contextlib import contextmanager

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.constants import ChatMemberStatus
from telegram.error import BadRequest, TelegramError
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# ============================================================
# GLOBAL CASH BOT (Config & Constants)
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
                is_banned INTEGER NOT NULL DEFAULT 0,
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
    row = conn.execute("SELECT * FROM users WHERE user_id=?", (user_id,)).fetchone()
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
        cur.execute("UPDATE users SET joined_all=? WHERE user_id=?", (1 if value else 0, user_id))


def ban_user(user_id):
    with get_db_cursor() as cur:
        cur.execute("UPDATE users SET is_banned=1, suspicious=1 WHERE user_id=?", (user_id,))


def is_banned(user_id):
    u = get_user(user_id)
    return bool(u and u["is_banned"])


def add_balance(user_id, amount):
    with get_db_cursor() as cur:
        cur.execute("UPDATE users SET balance=balance+? WHERE user_id=?", (amount, user_id))


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
    row = conn.execute("SELECT COUNT(*) AS c FROM referrals WHERE referrer_id=? AND status='paid'", (user_id,)).fetchone()
    conn.close()
    return int(row["c"])


def get_user_referrals_list(user_id, limit=10):
    conn = db()
    rows = conn.execute("""
        SELECT r.referred_id, u.username, u.first_name, u.created_at
        FROM referrals r
        LEFT JOIN users u ON r.referred_id = u.user_id
        WHERE r.referrer_id=? AND r.status='paid'
        ORDER BY r.id DESC LIMIT ?
    """, (user_id, limit)).fetchall()
    conn.close()
    return rows


def process_referral_reward(referred_id):
    user = get_user(referred_id)
    if not user or not user["referred_by"] or user["referred_by"] == referred_id:
        return None

    if user["referral_paid"] or user["suspicious"] or user["is_banned"]:
        return None

    inviter = user["referred_by"]
    inviter_row = get_user(inviter)
    if not inviter_row or inviter_row["suspicious"] or inviter_row["is_banned"]:
        return None

    conn = db()
    try:
        conn.execute("BEGIN IMMEDIATE")
        existing = conn.execute("SELECT id FROM referrals WHERE referred_id=?", (referred_id,)).fetchone()

        if existing:
            conn.execute("UPDATE users SET referral_paid=1, referral_status='paid' WHERE user_id=?", (referred_id,))
            conn.commit()
            return None

        reward = get_referral_reward()
        conn.execute("UPDATE users SET balance=balance+? WHERE user_id=?", (reward, inviter))
        conn.execute("""
            INSERT INTO referrals(referrer_id, referred_id, reward_amount, status, created_at)
            VALUES(?,?,?,?,?)
        """, (inviter, referred_id, reward, "paid", now()))

        conn.execute("UPDATE users SET referral_paid=1, referral_status='paid' WHERE user_id=?", (referred_id,))

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
    row = conn.execute("SELECT paid FROM user_tasks WHERE user_id=? AND task_id=?", (user_id, task_id)).fetchone()
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
        """, (user_id, task_id, 1 if verified else 0, 1 if paid else 0, now() if paid else None))


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
            if member.status not in (ChatMemberStatus.MEMBER, ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER):
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
# KEYBOARDS
# ============================================================

def channel_keyboard(missing):
    buttons = [[InlineKeyboardButton(f"📢 Join Channel", url=url)] for u, url in missing]
    buttons.append([InlineKeyboardButton("Verify ✅", callback_data="verify")])
    return InlineKeyboardMarkup(buttons)


def main_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Balance 💰", callback_data="balance"), InlineKeyboardButton("Referral 👥", callback_data="referral")],
        [InlineKeyboardButton("Tasks 🎯", callback_data="tasks"), InlineKeyboardButton("Withdraw 💸", callback_data="withdraw")],
        [InlineKeyboardButton("Wallet 👛", callback_data="wallet"), InlineKeyboardButton("Support 📞", callback_data="support")],
    ])


def back_keyboard():
    return InlineKeyboardMarkup([[InlineKeyboardButton("Back 🔙", callback_data="home")]])


def referral_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Share Link 📤", callback_data="share_ref")],
        [InlineKeyboardButton("Back 🔙", callback_data="home")],
    ])


def wallet_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("CBE Bank 🏦", callback_data="wallet_cbe"), InlineKeyboardButton("Telebirr 📱", callback_data="wallet_telebirr")],
        [InlineKeyboardButton("Back 🔙", callback_data="home")],
    ])


def admin_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Statistics 📊", callback_data="admin_stats"), InlineKeyboardButton("Referral Reward 💰", callback_data="admin_reward")],
        [InlineKeyboardButton("Referrals List 👥", callback_data="admin_referrals"), InlineKeyboardButton("Withdrawals 💸", callback_data="admin_withdrawals")],
        [InlineKeyboardButton("Suspicious Users ⚠️", callback_data="admin_suspicious"), InlineKeyboardButton("Add Task 🎯", callback_data="admin_add_task")],
        [InlineKeyboardButton("Broadcast 📢", callback_data="admin_broadcast")],
    ])


# ============================================================
# USER PAGES
# ============================================================

async def show_home(query, user_id):
    await safe_edit_message(
        query,
        "💰 <b>Global Cash Bot</b> 👋\n\nWelcome back / እንኳን ደህና መጡ!\n\nPlease choose an option below to continue / ለመቀጠል ከታች ካሉት አማራጮች ይምረጡ።",
        reply_markup=main_keyboard(),
    )


async def show_balance(query, user_id):
    balance = get_balance(user_id)
    text = (
        "💰 <b>Your Account Balance / የቀሪ ሂሳብ መረጃ</b>\n\n"
        f"💵 Current Balance: <b>{balance:.2f} ETB</b>\n\n"
        "ተጨማሪ Referral እና Tasks በመስራት Account Balance ማሳደግ ይችላሉ!"
    )
    await safe_edit_message(query, text, reply_markup=back_keyboard())


async def show_referral(query, user_id):
    count = get_referral_count(user_id)
    reward = get_referral_reward()
    link = f"https://t.me/{BOT_USERNAME}?start={user_id}"

    text = (
        "👥 <b>Referral Program / የሪፈራል መርሃ ግብር</b>\n\n"
        f"👤 Total Invited: <b>{count} Users</b>\n"
        f"🎁 Earn Per Refer: <b>{reward:.2f} ETB</b>\n\n"
        "Invite your friends and earn instant rewards! የእርስዎን ልዩ Link በመጠቀም ጓደኞችዎን ይጋብዙ እና ተጨማሪ ገንዘብ ይሰብስቡ።\n\n"
        "🔗 <b>Your Invite Link / የእርስዎ ሊንክ፦</b>\n"
        f"<code>{escape(link)}</code>"
    )
    await safe_edit_message(query, text, reply_markup=referral_keyboard())


async def show_tasks(query, user_id):
    tasks = get_active_tasks()
    lines = ["🎯 <b>Available Tasks / የሚሰሩ ስራዎች</b>", "", "Complete tasks to earn extra income! ስራዎችን በመስራት ተጨማሪ ገንዘብ ያግኙ።", ""]
    buttons = []

    reward = get_referral_reward()
    lines.append(f"👥 <b>Invite Friends</b> — Earn <b>{reward:.2f} ETB</b> per referral.\n")

    if tasks:
        for task in tasks:
            lines.append(
                f"🎯 <b>{escape(task['title'])}</b>\n"
                f"{escape(task['description'])}\n"
                f"💰 Reward: <b>{task['reward']:.2f} ETB</b>"
            )
            buttons.append([InlineKeyboardButton(f"Task: {task['title']}", callback_data=f"task_{task['id']}")])
    else:
        lines.append("📌 More tasks coming soon! ተጨማሪ አዳዲስ ስራዎች በቅርቡ ይጨመራሉ።")

    buttons.append([InlineKeyboardButton("Back 🔙", callback_data="home")])
    await safe_edit_message(query, "\n".join(lines), reply_markup=InlineKeyboardMarkup(buttons))


async def show_task(query, user_id, task_id):
    task = get_task(task_id)
    if not task or not task["active"]:
        await query.answer("Task not available / ይህ ስራ አሁን አይገኝም።", show_alert=True)
        return

    paid = user_task_paid(user_id, task_id)
    buttons = [[InlineKeyboardButton("Join Channel 📢", url=task["channel_url"])]]

    if not paid:
        buttons.append([InlineKeyboardButton("Verify Task ✅", callback_data=f"verify_task_{task_id}")])
    buttons.append([InlineKeyboardButton("Back 🔙", callback_data="tasks")])

    status = "✅ <b>Status: Completed</b>\n\nየዚህ ስራ ክፍያ ገብቶልዎታል።" if paid else "⏳ <b>Status: Pending</b>\n\nቻናሉን ተቀላቅለው Verify የሚለውን ይጫኑ።"

    text = (
        f"🎯 <b>{escape(task['title'])}</b>\n\n"
        f"{escape(task['description'])}\n\n"
        f"💰 Reward: <b>{task['reward']:.2f} ETB</b>\n"
        f"📢 Channel: <b>{escape(task['channel_username'])}</b>\n\n"
        f"{status}"
    )
    await safe_edit_message(query, text, reply_markup=InlineKeyboardMarkup(buttons))


async def show_wallet(query, user_id):
    wallet = get_wallet(user_id)
    if wallet:
        wallet_type, wallet_number = wallet
        text = (
            "👛 <b>Wallet Information / የተመዘገበ ወሌት</b>\n\n"
            f"💳 Payment Method: <b>{escape(wallet_type)}</b>\n"
            f"🔢 Account Number: <code>{escape(wallet_number)}</code>\n\n"
            "የክፍያ መንገድዎን መቀየር ከፈለጉ ከታች ባሉት በተኖች ይምረጡ።"
        )
    else:
        text = (
            "👛 <b>Connect Wallet / ወሌት መዝግብ</b>\n\n"
            "⚠️ No wallet connected yet! እስካሁን ምንም አይነት የክፍያ መንገድ አልመዘገቡም።\n\n"
            "Please select your payment method below / እባክዎን የክፍያ መንገድዎን ይምረጡ፦"
        )

    await safe_edit_message(query, text, reply_markup=wallet_keyboard())


async def show_withdraw(query, user_id, context):
    user = get_user(user_id)
    if not user:
        return

    if user["suspicious"] or user["is_banned"]:
        await safe_edit_message(
            query,
            f"⚠️ <b>Account Suspended / የደህንነት ማሳሰቢያ</b>\n\nYour account is under audit. Support ለማግኘት፦ {SUPPORT_USERNAME}",
            reply_markup=back_keyboard(),
        )
        return

    wallet = get_wallet(user_id)
    if not wallet:
        await safe_edit_message(
            query,
            "💸 <b>Withdrawal / ገንዘብ ማውጣት</b>\n\n"
            "⚠️ <b>Connect Wallet First!</b>\n\n"
            "ገንዘብ ለማውጣት በመጀመሪያ Wallet Connect ማድረግ አለብዎት።\n"
            "እባክዎን የ CBE ወይም Telebirr ቁጥርዎን አስቀድመው ይምረጡ።",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("Connect Wallet 👛", callback_data="wallet")],
                [InlineKeyboardButton("Back 🔙", callback_data="home")]
            ]),
        )
        return

    wallet_type, wallet_number = wallet
    balance = float(user["balance"])
    context.user_data["withdraw_step"] = "amount"

    await safe_edit_message(
        query,
        f"💸 <b>Withdraw Money / ገንዘብ ማውጣት</b>\n\n"
        f"💰 Available Balance: <b>{balance:.2f} ETB</b>\n"
        f"💳 Method: <b>{escape(wallet_type)}</b> (<code>{escape(wallet_number)}</code>)\n"
        f"📌 Minimum Withdrawal: <b>{MIN_WITHDRAWAL:.2f} ETB</b>\n\n"
        "👉 <b>Enter Amount / ማውጣት የሚፈልጉትን መጠን ያስገቡ፦</b>\n\n"
        "ምሳሌ (Example):\n<code>50</code>",
        reply_markup=back_keyboard(),
    )


async def show_support(query, user_id):
    text = (
        "📞 <b>Support & Digital Services / ድጋፍ እና ማስታወቂያ</b>\n\n"
        "እነዚህን አገልግሎቶች ማግኘት ከፈለጉ በ Support መስመራችን DM ያድርጉልን፦\n\n"
        "🚀 <b>Buy & Sell USDT (USDT መግዛት እና መሸጥ የምትፈልጉ DM አድርጉን)</b>\n"
        "📢 Telegram Channel Promotion\n"
        "👥 Group Members & Growth\n"
        "🏢 Business & Product Promotion\n"
        "📱 App & Website Promotion\n\n"
        "Contact Admin / ለማነጋገር፦"
    )
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("Contact Support 💬", url=f"https://t.me/{SUPPORT_USERNAME.lstrip('@')}")],
        [InlineKeyboardButton("Back 🔙", callback_data="home")],
    ])
    await safe_edit_message(query, text, reply_markup=keyboard)


# ============================================================
# ADMIN FUNCTIONS & ANTI-FAKE SYSTEM
# ============================================================

def is_admin(user_id):
    return user_id == ADMIN_ID


async def show_admin(query):
    if not is_admin(query.from_user.id):
        await query.answer("⛔ Admin access only!", show_alert=True)
        return

    await safe_edit_message(
        query,
        "🛠 <b>Admin Control Panel / የአድሚን ፓነል</b>\n\nSelect an option:",
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
    suspicious_accounts = conn.execute("SELECT COUNT(*) AS c FROM users WHERE suspicious=1 OR is_banned=1").fetchone()["c"]
    conn.close()

    text = (
        "📊 <b>System Statistics</b>\n\n"
        f"👥 Total Users: <b>{total_users}</b>\n"
        f"✅ Verified Users: <b>{verified_users}</b>\n"
        f"💰 Total User Balance: <b>{float(total_balance):.2f} ETB</b>\n"
        f"👥 Successful Referrals: <b>{successful_referrals}</b>\n"
        f"💸 Pending Withdrawals: <b>{pending_withdrawals}</b>\n"
        f"⚠️ Flagged/Banned Accounts: <b>{suspicious_accounts}</b>"
    )
    await safe_edit_message(query, text, reply_markup=back_keyboard())


async def show_admin_withdrawals(query):
    if not is_admin(query.from_user.id):
        return

    conn = db()
    rows = conn.execute("SELECT * FROM withdrawals WHERE status='pending' ORDER BY id DESC LIMIT 10").fetchall()
    conn.close()

    if not rows:
        await safe_edit_message(query, "💸 <b>Pending Withdrawals</b>\n\nNo pending withdrawal requests.", reply_markup=back_keyboard())
        return

    buttons = []
    lines = ["💸 <b>Pending Withdrawal Requests</b>", ""]
    for row in rows:
        ref_count = get_referral_count(row['user_id'])
        lines.append(
            f"🆔 <b>#{row['id']}</b> | User ID: <code>{row['user_id']}</code>\n"
            f"💰 Amount: <b>{row['amount']:.2f} ETB</b> | Method: <b>{escape(row['wallet_type'])}</b>\n"
            f"🔢 Number: <code>{escape(row['wallet_number'])}</code>\n"
            f"👥 Invited Total: <b>{ref_count} users</b>\n"
        )
        buttons.append([
            InlineKeyboardButton(f"Approve #{row['id']} ✅", callback_data=f"approve_wd_{row['id']}"),
            InlineKeyboardButton(f"Reject #{row['id']} ❌", callback_data=f"reject_wd_{row['id']}"),
            InlineKeyboardButton(f"Ban User 🚫", callback_data=f"ban_user_{row['user_id']}"),
        ])

    buttons.append([InlineKeyboardButton("Back 🔙", callback_data="admin")])
    await safe_edit_message(query, "\n".join(lines), reply_markup=InlineKeyboardMarkup(buttons))


async def confirm_withdrawal_process(query, context):
    user = query.from_user
    amount = context.user_data.get("pending_withdraw_amount")

    if not amount:
        await query.answer("No pending request!", show_alert=True)
        return

    wallet = get_wallet(user.id)
    if not wallet:
        await query.answer("Wallet not found!", show_alert=True)
        return

    wallet_type, wallet_number = wallet

    conn = db()
    try:
        conn.execute("BEGIN IMMEDIATE")
        cur = conn.execute("""
            UPDATE users SET balance = balance - ?
            WHERE user_id=? AND balance >= ? AND suspicious=0 AND is_banned=0
        """, (amount, user.id, amount))

        if cur.rowcount != 1:
            conn.rollback()
            await safe_edit_message(
                query,
                "❌ Transaction Failed / የቀሪ ሂሳብ ማነስ አጋጥሟል!",
                reply_markup=back_keyboard(),
            )
            return

        cur = conn.execute("""
            INSERT INTO withdrawals(user_id, amount, wallet_type, wallet_number, status, created_at)
            VALUES(?,?,?,?,?,?)
        """, (user.id, amount, wallet_type, wallet_number, "pending", now()))

        withdrawal_id = cur.lastrowid
        conn.commit()
    except Exception:
        conn.rollback()
        logger.exception("Withdrawal creation failed.")
        await safe_edit_message(query, "❌ Error creating withdrawal request.", reply_markup=back_keyboard())
        return
    finally:
        conn.close()

    context.user_data.pop("pending_withdraw_amount", None)

    await safe_edit_message(
        query,
        "✅ <b>Withdrawal Requested Successfully!</b>\n\n"
        f"🆔 Request ID: <b>#{withdrawal_id}</b>\n"
        f"💰 Amount: <b>{amount:.2f} ETB</b>\n"
        f"🏦 Method: <b>{escape(wallet_type)}</b>\n"
        f"🔢 Account: <code>{escape(wallet_number)}</code>\n\n"
        "⏳ Status: <b>Pending Admin Approval</b>\n\n"
        "ጥያቄዎ ለአድሚን ተልኳል፤ በቅርቡ ገቢ ይደረጋል።",
        reply_markup=main_keyboard(),
    )

    # DETAILED ADMIN NOTIFICATION WITH ANTI-FAKE REFERRAL LIST
    ref_count = get_referral_count(user.id)
    ref_list = get_user_referrals_list(user.id, limit=5)
    
    ref_details = []
    for r in ref_list:
        uname = f"@{r['username']}" if r['username'] else (r['first_name'] or "No Name")
        ref_details.append(f"• 👤 <b>{escape(uname)}</b> (<code>{r['referred_id']}</code>)")
    
    ref_text = "\n".join(ref_details) if ref_details else "No referrals found."

    admin_msg = (
        "💳 <b>New Withdrawal Request! (አዲስ የክፍያ ጥያቄ)</b>\n\n"
        f"🆔 Request ID: <b>#{withdrawal_id}</b>\n"
        f"👤 User: <b>{escape(user.first_name)}</b> (<code>{user.id}</code>)\n"
        f"💰 Amount: <b>{amount:.2f} ETB</b>\n"
        f"🏦 Method: <b>{escape(wallet_type)}</b>\n"
        f"🔢 Account: <code>{escape(wallet_number)}</code>\n\n"
        f"👥 Total Referrals: <b>{ref_count} Users</b>\n"
        f"📋 <b>Recent Referred Accounts (ለአድሚን ማረጋገጫ)፦</b>\n{ref_text}"
    )

    admin_keyboard_markup = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(f"Approve #{withdrawal_id} ✅", callback_data=f"approve_wd_{withdrawal_id}"),
            InlineKeyboardButton(f"Reject #{withdrawal_id} ❌", callback_data=f"reject_wd_{withdrawal_id}"),
        ],
        [InlineKeyboardButton(f"Ban User 🚫", callback_data=f"ban_user_{user.id}")],
    ])

    try:
        await context.bot.send_message(
            chat_id=ADMIN_ID,
            text=admin_msg,
            reply_markup=admin_keyboard_markup,
            parse_mode="HTML",
        )
    except Exception:
        logger.exception("Could not notify admin.")


# ============================================================
# START & VERIFY COMMANDS
# ============================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user

    if is_banned(user.id):
        await update.message.reply_text("⛔ Your account has been suspended.")
        return

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
                    "🎉 <b>New Referral Joined! / አዲስ ሰው ተቀላቅሏል!</b>\n\n"
                    f"👤 User ID: <code>{result['referred_id']}</code>\n"
                    f"🎁 Earned Reward: <b>{result['amount']:.2f} ETB</b>"
                ),
                parse_mode="HTML",
            )
        except Exception:
            logger.exception("Could not notify inviter.")

    await update.message.reply_text(
        "🎉 <b>Welcome to Global Cash Bot! / እንኳን ደህና መጡ!</b>\n\n"
        "Your account is verified! አካውንትዎ በትክክል ተረጋግጧል።\n\n"
        "Choose an option below to start earning / ከታች ካሉት አማራጮች ይምረጡ፦",
        reply_markup=main_keyboard(),
        parse_mode="HTML",
    )


# ============================================================
# CALLBACK HANDLER
# ============================================================

async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    data = query.data
    user = query.from_user

    if is_banned(user.id):
        await query.answer("⛔ Account suspended!", show_alert=True)
        return

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

    if data == "confirm_withdrawal":
        await confirm_withdrawal_process(query, context)
        return

    if data == "wallet":
        await show_wallet(query, user.id)
        return

    if data == "support":
        await show_support(query, user.id)
        return

    if data == "share_ref":
        link = f"https://t.me/{BOT_USERNAME}?start={user.id}"
        await query.answer("Share link copied!", show_alert=True)
        await context.bot.send_message(
            chat_id=user.id,
            text=f"📤 <b>Your Referral Link:</b>\n\n<code>{escape(link)}</code>\n\nይህንን Link ለጓደኞችዎ ያጋሩ!",
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

    # ADMIN ACTIONS
    if data == "admin":
        await show_admin(query)
        return

    if data == "admin_stats":
        await show_admin_stats(query)
        return

    if data == "admin_reward":
        await show_admin_reward(query)
        return

    if data == "admin_withdrawals":
        await show_admin_withdrawals(query)
        return

    if data == "admin_referrals":
        await show_admin_referrals(query)
        return

    if data == "admin_add_task":
        await start_add_task(query, context)
        return

    if data == "admin_broadcast":
        await start_broadcast(query, context)
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

    if data.startswith("ban_user_"):
        if not is_admin(user.id):
            return
        try:
            target_user_id = int(data.split("_")[-1])
            ban_user(target_user_id)
            await query.answer(f"🚫 User {target_user_id} has been Banned!", show_alert=True)
        except ValueError:
            pass
        return


# ============================================================
# HELPER VERIFICATION FUNCTIONS
# ============================================================

async def show_join_required(update, context, missing):
    text = (
        "👋 <b>Welcome to Global Cash Bot!</b>\n\n"
        "To start using the bot, please join all required channels below.\n\n"
        "ሁሉንም ቻናሎች ከተቀላቀሉ በኋላ <b>Verify ✅</b> የሚለውን ይጫኑ።\n\n"
        f"📌 Remaining Channels: <b>{len(missing)}</b>"
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
            "🏦 <b>CBE Bank Account / የባንክ ሂሳብ</b>\n\n"
            "Please enter your 13-digit CBE account number.\n"
            "የባንክ ቁጥርዎን ያስገቡ (በ <b>1000</b> የሚጀምር 13 ዲጂት ቁጥር)።\n\n"
            "Example:\n<code>1000123456789</code>"
        )
    else:
        instruction = (
            "📱 <b>Telebirr Account / ቴሌብር</b>\n\n"
            "Please enter your 10-digit Telebirr phone number.\n"
            "የቴሌብር ስልክ ቁጥርዎን ያስገቡ (በ <b>09</b> ወይም <b>07</b> የሚጀምር)።\n\n"
            "Example:\n<code>0912345678</code>"
        )
    await safe_edit_message(query, instruction, reply_markup=back_keyboard())


async def verify_user(query, context):
    user_id = query.from_user.id
    missing = await missing_channels(context.bot, user_id)

    if missing:
        set_joined_all(user_id, False)
        text = (
            "❌ <b>Verification Failed! / አልተረጋገጠም</b>\n\n"
            "You haven't joined all required channels.\n"
            f"📌 Remaining Channels: <b>{len(missing)}</b>\n\n"
            "እባክዎን የቀሩትን ቻናሎች ተቀላቅለው ድጋሚ Verify የሚለውን ይጫኑ።"
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
                    "🎉 <b>New Referral Joined! / አዲስ ሰው ተቀላቅሏል!</b>\n\n"
                    f"👤 User ID: <code>{result['referred_id']}</code>\n"
                    f"🎁 Earned Reward: <b>{result['amount']:.2f} ETB</b>"
                ),
                parse_mode="HTML",
            )
        except Exception:
            logger.exception("Could not notify inviter.")

    await safe_edit_message(
        query,
        "✅ <b>Successfully Verified! / በትክክል ተረጋግጧል!</b>\n\nWelcome to <b>Global Cash Bot</b>! 💰",
        reply_markup=main_keyboard(),
    )


async def verify_task(query, context, task_id):
    user_id = query.from_user.id
    task = get_task(task_id)

    if not task or not task["active"]:
        await query.answer("Task not available / ይህ ስራ አይገኝም።", show_alert=True)
        return

    if user_task_paid(user_id, task_id):
        await query.answer("Task already completed / አስቀድመው ሰርተውታል።", show_alert=True)
        return

    try:
        member = await context.bot.get_chat_member(chat_id=task["channel_username"], user_id=user_id)
        if member.status not in (ChatMemberStatus.MEMBER, ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER):
            await query.answer("❌ Please join the channel first! ገና አልተቀላቀሉም።", show_alert=True)
            return
    except Exception:
        await query.answer("Verification failed! ድጋሚ ይሞክሩ።", show_alert=True)
        return

    reward = float(task["reward"])
    add_balance(user_id, reward)
    save_user_task(user_id, task_id, verified=True, paid=True)

    await query.answer(f"🎉 +{reward:.2f} ETB Added to your balance!", show_alert=True)
    await show_task(query, user_id, task_id)


async def approve_withdrawal(query, withdrawal_id, context):
    if not is_admin(query.from_user.id):
        return

    conn = db()
    cur = conn.execute("UPDATE withdrawals SET status='approved' WHERE id=? AND status='pending'", (withdrawal_id,))

    if cur.rowcount != 1:
        conn.rollback()
        conn.close()
        await query.answer("Already processed / ተስተናግዷል።", show_alert=True)
        return

    row = conn.execute("SELECT * FROM withdrawals WHERE id=?", (withdrawal_id,)).fetchone()
    conn.commit()
    conn.close()

    await query.answer("✅ Approved!", show_alert=True)

    try:
        await context.bot.send_message(
            chat_id=row["user_id"],
            text=(
                "✅ <b>Withdrawal Approved! / ገቢ ሆኗል!</b>\n\n"
                f"🆔 Request ID: <b>#{row['id']}</b>\n"
                f"💰 Amount: <b>{row['amount']:.2f} ETB</b>\n"
                f"🏦 Account: <code>{escape(row['wallet_number'])}</code>\n\n"
                "ገንዘቡ ወደ አካውንትዎ ገቢ ሆኗል! Thank you for using our bot.",
            ),
            parse_mode="HTML",
        )
    except Exception:
        logger.exception("Could not notify user.")

    await show_admin_withdrawals(query)


async def reject_withdrawal(query, withdrawal_id, context):
    if not is_admin(query.from_user.id):
        return

    conn = db()
    cur = conn.execute("UPDATE withdrawals SET status='rejected' WHERE id=? AND status='pending'", (withdrawal_id,))

    if cur.rowcount != 1:
        conn.rollback()
        conn.close()
        await query.answer("Already processed!", show_alert=True)
        return

    row = conn.execute("SELECT * FROM withdrawals WHERE id=?", (withdrawal_id,)).fetchone()
    conn.execute("UPDATE users SET balance = balance + ? WHERE user_id=?", (row["amount"], row["user_id"]))
    conn.commit()
    conn.close()

    await query.answer("❌ Rejected and refunded!", show_alert=True)

    try:
        await context.bot.send_message(
            chat_id=row["user_id"],
            text=(
                "❌ <b>Withdrawal Rejected / አልተቀበለም!</b>\n\n"
                f"🆔 Request ID: <b>#{row['id']}</b>\n"
                f"💰 Refunded Amount: <b>{row['amount']:.2f} ETB</b>\n\n"
                "ገንዘቡ ወደ ቦት አካውንትዎ ተመልሷል።",
            ),
            parse_mode="HTML",
        )
    except Exception:
        logger.exception("Could not notify user.")

    await show_admin_withdrawals(query)


async def show_admin_reward(query):
    if not is_admin(query.from_user.id):
        return

    reward = get_referral_reward()
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("Change Reward ✏️", callback_data="admin_change_reward")],
        [InlineKeyboardButton("Back 🔙", callback_data="admin")],
    ])
    await safe_edit_message(
        query,
        f"💰 <b>Referral Reward Settings</b>\n\nCurrent Reward: <b>{reward:.2f} ETB</b>",
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
        text = "👥 <b>Top Referrers</b>\n\nNo referral data available."
    else:
        lines = ["👥 <b>Top Referrers List</b>", ""]
        for row in rows:
            lines.append(f"👤 <code>{row['referrer_id']}</code> — <b>{row['count']} Refers</b>")
        text = "\n".join(lines)

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("Search User 🔎", callback_data="admin_referral_lookup")],
        [InlineKeyboardButton("Back 🔙", callback_data="admin")],
    ])
    await safe_edit_message(query, text, reply_markup=keyboard)


async def start_add_task(query, context):
    if not is_admin(query.from_user.id):
        return

    context.user_data.clear()
    context.user_data["admin_step"] = "task_title"
    await safe_edit_message(
        query,
        "🎯 <b>Add New Task</b>\n\nStep 1/4: Enter Task Title.\n\nExample:\n<code>Join Aman Income Lab Channel</code>",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Cancel ❌", callback_data="admin")]]),
    )


async def start_broadcast(query, context):
    if not is_admin(query.from_user.id):
        return

    context.user_data.clear()
    context.user_data["admin_step"] = "broadcast"
    await safe_edit_message(
        query,
        "📢 <b>Broadcast Message</b>\n\nለሌሎች ተጠቃሚዎች ማስተላለፍ የሚፈልጉትን መልእክት ይጻፉ፦",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Cancel ❌", callback_data="admin")]]),
    )


# ============================================================
# MESSAGE HANDLER & WALLET PROCESSOR
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
        msg = "❌ Invalid CBE Account! Must be 13 digits starting with 1000." if wallet_type == "CBE" else "❌ Invalid Telebirr Number! Must be 10 digits starting with 09 or 07."
        await update.message.reply_text(msg + "\n\nእባክዎን ድጋሚ ይሞክሩ።")
        return True

    if wallet_exists_for_other_user(wallet_type, number, user.id):
        await update.message.reply_text("⚠️ <b>Wallet already connected to another account!</b>\n\nይህ ቁጥር ከሌላ አካውንት ጋር ተያይዟል።", parse_mode="HTML")
        return True

    saved = set_wallet(user.id, wallet_type, number)
    if not saved:
        await update.message.reply_text("⚠️ Failed to save wallet.", parse_mode="HTML")
        return True

    context.user_data.pop("wallet_type", None)
    context.user_data.pop("wallet_step", None)

    await update.message.reply_text(
        "✅ <b>Wallet Connected Successfully! / ወሌትዎ ተመዝግቧል!</b>\n\n"
        f"💳 Method: <b>{escape(wallet_type)}</b>\n"
        f"🔢 Account: <code>{escape(number)}</code>\n\n"
        "Now you can withdraw your balance / አሁን ማውጣት ይችላሉ።",
        reply_markup=main_keyboard(),
        parse_mode="HTML",
    )
    return True


async def handle_withdraw_amount_input(update, context):
    user = update.effective_user
    text = update.message.text.strip()

    try:
        amount = float(text)
    except ValueError:
        await update.message.reply_text("❌ Invalid input! Please enter a valid number.\n\nእባክዎን ትክክለኛ ቁጥር ያስገቡ (ምሳሌ፦ <code>50</code>)።", parse_mode="HTML")
        return True

    balance = get_balance(user.id)

    if balance < MIN_WITHDRAWAL or amount > balance:
        await update.message.reply_text(
            f"❌ <b>Insufficient Balance / የቀሪ ሂሳብ ማሳሰቢያ!</b>\n\n"
            f"Your Current Balance: <b>{balance:.2f} ETB</b>\n"
            f"Minimum Withdrawal: <b>{MIN_WITHDRAWAL:.2f} ETB</b>\n\n"
            "ይቅርታ! ያሎት ቀሪ ሂሳብ ለማውጣት በቂ አይደለም። "
            "እባክዎን በ <b>Referral (ጓደኞችን በመጋበዝ)</b> ወይም በ <b>Tasks</b> ተጨማሪ ገንዘብ ይሰብስቡ!",
            reply_markup=back_keyboard(),
            parse_mode="HTML",
        )
        context.user_data.pop("withdraw_step", None)
        return True

    if amount <= 0:
        await update.message.reply_text("❌ Amount must be greater than 0!")
        return True

    wallet = get_wallet(user.id)
    if not wallet:
        await update.message.reply_text("❌ No wallet connected!", reply_markup=wallet_keyboard())
        context.user_data.pop("withdraw_step", None)
        return True

    wallet_type, wallet_number = wallet

    context.user_data["pending_withdraw_amount"] = amount
    context.user_data.pop("withdraw_step", None)

    confirm_keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("Confirm 💸", callback_data="confirm_withdrawal")],
        [InlineKeyboardButton("Cancel ❌", callback_data="home")],
    ])

    await update.message.reply_text(
        "💸 <b>Withdrawal Confirmation / ማረጋገጫ</b>\n\n"
        f"💰 Requested Amount: <b>{amount:.2f} ETB</b>\n"
        f"🏦 Payment Method: <b>{escape(wallet_type)}</b>\n"
        f"🔢 Account Number: <code>{escape(wallet_number)}</code>\n\n"
        "ለመቀጠል <b>Confirm 💸</b> የሚለውን ይጫኑ፦",
        reply_markup=confirm_keyboard,
        parse_mode="HTML",
    )
    return True


async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not update.message:
        return

    if is_banned(user.id):
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
                await update.message.reply_text("❌ Please enter a positive number!")
                return

            set_setting("referral_reward", f"{amount:.2f}")
            context.user_data.pop("admin_step", None)
            await update.message.reply_text(
                f"✅ <b>Referral Reward Updated!</b>\n\nNew Reward: <b>{amount:.2f} ETB</b>",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("Referral Settings 💰", callback_data="admin_reward")],
                    [InlineKeyboardButton("Admin Panel 🛠", callback_data="admin")],
                ]),
                parse_mode="HTML",
            )
            return

        if admin_step == "task_title":
            context.user_data["task_title"] = text
            context.user_data["admin_step"] = "task_description"
            await update.message.reply_text("🎯 <b>Add Task</b>\n\nStep 2/4: Enter task description.", parse_mode="HTML")
            return

        if admin_step == "task_description":
            context.user_data["task_description"] = text
            context.user_data["admin_step"] = "task_channel"
            await update.message.reply_text("🎯 <b>Add Task</b>\n\nStep 3/4: Enter Channel Username (e.g. <code>@MyChannel</code>).", parse_mode="HTML")
            return

        if admin_step == "task_channel":
            username = text.strip()
            if not username.startswith("@"):
                username = "@" + username
            context.user_data["task_channel"] = username
            context.user_data["task_url"] = f"https://t.me/{username.lstrip('@')}"
            context.user_data["admin_step"] = "task_reward"
            await update.message.reply_text("🎯 <b>Add Task</b>\n\nStep 4/4: Enter reward amount in ETB.", parse_mode="HTML")
            return

        if admin_step == "task_reward":
            try:
                reward = float(text)
                if reward <= 0:
                    raise ValueError
            except ValueError:
                await update.message.reply_text("❌ Enter a valid reward amount!")
                return

            title = context.user_data.get("task_title", "Task")
            description = context.user_data.get("task_description", "")
            channel = context.user_data.get("task_channel", "")
            url = context.user_data.get("task_url", "")

            task_id = create_task(title, description, channel, url, reward)
            context.user_data.clear()

            await update.message.reply_text(
                f"✅ <b>Task Created Successfully!</b>\n\n🆔 Task ID: <b>{task_id}</b>\n🎯 Title: <b>{escape(title)}</b>\n📢 Channel: <b>{escape(channel)}</b>\n💰 Reward: <b>{reward:.2f} ETB</b>",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("Tasks 🎯", callback_data="tasks")],
                    [InlineKeyboardButton("Admin Panel 🛠", callback_data="admin")],
                ]),
                parse_mode="HTML",
            )
            return

        if admin_step == "broadcast":
            conn = db()
            users = conn.execute("SELECT user_id FROM users").fetchall()
            conn.close()

            context.user_data.pop("admin_step", None)
            await update.message.reply_text(f"⏳ Broadcasting message to {len(users)} users...")

            sent, failed = 0, 0
            for u in users:
                try:
                    await context.bot.send_message(chat_id=u["user_id"], text=text, parse_mode="HTML")
                    sent += 1
                    await asyncio.sleep(0.05)
                except Exception:
                    failed += 1

            await update.message.reply_text(
                f"✅ <b>Broadcast Finished!</b>\n\n🟢 Sent: {sent}\n🔴 Failed: {failed}",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Admin Panel 🛠", callback_data="admin")]]),
                parse_mode="HTML",
            )
            return

    if context.user_data.get("wallet_step") == "number":
        if await save_wallet_from_input(update, context):
            return

    if context.user_data.get("withdraw_step") == "amount":
        if await handle_withdraw_amount_input(update, context):
            return

    await update.message.reply_text("Please use the buttons below / እባክዎን ከታች ያሉትን በተኖች ይጠቀሙ፦", reply_markup=main_keyboard())


# ============================================================
# MAIN
# ============================================================

async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("⛔ Admin access only!")
        return
    context.user_data.clear()
    await update.message.reply_text("🛠 <b>Admin Control Panel / የአድሚን ፓነል</b>\n\nSelect an option:", reply_markup=admin_keyboard(), parse_mode="HTML")


async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("❌ Action cancelled / ክንውኑ ተሰርዟል።", reply_markup=main_keyboard())


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.exception("Unexpected error occurred:", exc_info=context.error)


def main():
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN is missing!")
    if not ADMIN_ID:
        raise RuntimeError("ADMIN_ID is missing!")

    init_db()

    application = Application.builder().token(BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("admin", admin_command))
    application.add_handler(CommandHandler("cancel", cancel_command))
    application.add_handler(CallbackQueryHandler(callback_handler))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler))
    application.add_error_handler(error_handler)

    logger.info("Global Cash Bot starting...")
    application.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()

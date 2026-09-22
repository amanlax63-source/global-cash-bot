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
# GLOBAL CASH BOT
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
    """Context manager for safe database connections and transactions."""
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


def mark_suspicious(user_id, value=1):
    with get_db_cursor() as cur:
        cur.execute(
            "UPDATE users SET suspicious=? WHERE user_id=?",
            (1 if value else 0, user_id),
        )


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
# KEYBOARDS
# ============================================================

def channel_keyboard(missing):
    buttons = [[InlineKeyboardButton(f"📢 {u}", url=url)] for u, url in missing]
    buttons.append([InlineKeyboardButton("✅ Verify", callback_data="verify")])
    return InlineKeyboardMarkup(buttons)


def main_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("💰 Balance", callback_data="balance"), InlineKeyboardButton("👥 Referral", callback_data="referral")],
        [InlineKeyboardButton("🎯 Tasks", callback_data="tasks"), InlineKeyboardButton("💸 Withdraw", callback_data="withdraw")],
        [InlineKeyboardButton("👛 Wallet", callback_data="wallet"), InlineKeyboardButton("📞 Support", callback_data="support")],
    ])


def back_keyboard():
    return InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="home")]])


def balance_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("💸 Withdraw", callback_data="withdraw"), InlineKeyboardButton("👥 Referral", callback_data="referral")],
        [InlineKeyboardButton("🔙 Back", callback_data="home")],
    ])


def referral_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📤 Share Link", callback_data="share_ref")],
        [InlineKeyboardButton("🔙 Back", callback_data="home")],
    ])


def wallet_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🏦 CBE", callback_data="wallet_cbe"), InlineKeyboardButton("📱 Telebirr", callback_data="wallet_telebirr")],
        [InlineKeyboardButton("🔙 Back", callback_data="home")],
    ])


def withdraw_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("👛 Set / Change Wallet", callback_data="wallet")],
        [InlineKeyboardButton("🔙 Back", callback_data="home")],
    ])


def admin_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📊 Statistics", callback_data="admin_stats"), InlineKeyboardButton("💰 Reward", callback_data="admin_reward")],
        [InlineKeyboardButton("👥 Referrals", callback_data="admin_referrals"), InlineKeyboardButton("💸 Withdrawals", callback_data="admin_withdrawals")],
        [InlineKeyboardButton("⚠️ Suspicious", callback_data="admin_suspicious"), InlineKeyboardButton("🎯 Add Task", callback_data="admin_add_task")],
    ])


# ============================================================
# USER PAGES
# ============================================================

async def show_home(query, user_id):
    await safe_edit_message(
        query,
        "💰 <b>Global Cash Bot</b>\n\nWelcome! 👋\nChoose an option below to continue.",
        reply_markup=main_keyboard(),
    )


async def show_balance(query, user_id):
    balance = get_balance(user_id)
    text = (
        "💰 <b>Your Balance</b>\n\n"
        f"💵 Balance: <b>{balance:.2f} ETB</b>\n"
        f"💸 Minimum Withdrawal: <b>{MIN_WITHDRAWAL:.2f} ETB</b>\n\n"
        "Keep completing tasks and referrals to increase your balance."
    )
    await safe_edit_message(query, text, reply_markup=balance_keyboard())


async def show_referral(query, user_id):
    count = get_referral_count(user_id)
    reward = get_referral_reward()
    link = f"https://t.me/{BOT_USERNAME}?start={user_id}"

    text = (
        "👥 <b>Referral Program</b>\n\n"
        f"👤 Successful Referrals: <b>{count}</b>\n"
        f"🎁 Current Reward: <b>{reward:.2f} ETB</b>\n\n"
        "Invite your friends using your personal link.\n"
        "When your referred user joins all required channels and completes verification, "
        "the current referral reward will be added to your balance.\n\n"
        "🔗 <b>Your Referral Link:</b>\n"
        f"<code>{escape(link)}</code>"
    )
    await safe_edit_message(query, text, reply_markup=referral_keyboard())


async def show_tasks(query, user_id):
    tasks = get_active_tasks()
    lines = ["🎯 <b>Tasks</b>", "", "Complete tasks and earn ETB.", ""]
    buttons = []

    reward = get_referral_reward()
    lines.append(f"👥 <b>Referral Task</b> — Earn <b>{reward:.2f} ETB</b> per successful referral.\n")

    if tasks:
        for task in tasks:
            lines.append(
                f"🎯 <b>{escape(task['title'])}</b>\n"
                f"{escape(task['description'])}\n"
                f"💰 Reward: <b>{task['reward']:.2f} ETB</b>"
            )
            buttons.append([InlineKeyboardButton(f"🎯 {task['title']}", callback_data=f"task_{task['id']}")])
    else:
        lines.append("📌 More earning tasks will be added soon.")

    buttons.append([InlineKeyboardButton("🔙 Back", callback_data="home")])
    await safe_edit_message(query, "\n".join(lines), reply_markup=InlineKeyboardMarkup(buttons))


async def show_task(query, user_id, task_id):
    task = get_task(task_id)
    if not task or not task["active"]:
        await query.answer("This task is no longer available.", show_alert=True)
        return

    paid = user_task_paid(user_id, task_id)
    buttons = [[InlineKeyboardButton("📢 Join Channel", url=task["channel_url"])]]

    if not paid:
        buttons.append([InlineKeyboardButton("✅ Verify", callback_data=f"verify_task_{task_id}")])
    buttons.append([InlineKeyboardButton("🔙 Back", callback_data="tasks")])

    status = "✅ <b>Completed</b>\n\nThis task has already been paid." if paid else "⏳ <b>Status:</b> Not completed yet.\n\nJoin the channel and press Verify."

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
            "👛 <b>Your Wallet</b>\n\n"
            f"💳 Method: <b>{escape(wallet_type)}</b>\n"
            f"🔢 Number: <code>{escape(wallet_number)}</code>\n\n"
            "You can switch between CBE and Telebirr anytime."
        )
    else:
        text = "👛 <b>Wallet</b>\n\nNo wallet is saved yet.\n\nChoose your preferred withdrawal method:"

    await safe_edit_message(query, text, reply_markup=wallet_keyboard())


async def show_withdraw(query, user_id, context):
    user = get_user(user_id)
    if not user:
        return

    if user["suspicious"]:
        await safe_edit_message(
            query,
            f"⚠️ <b>Security Review</b>\n\nYour account is currently under security review.\n\n📞 Contact Support: {SUPPORT_USERNAME}",
            reply_markup=back_keyboard(),
        )
        return

    balance = float(user["balance"])
    if balance < MIN_WITHDRAWAL:
        await safe_edit_message(
            query,
            f"💸 <b>Withdraw</b>\n\nYour balance: <b>{balance:.2f} ETB</b>\nMinimum withdrawal: <b>{MIN_WITHDRAWAL:.2f} ETB</b>\n\nYou need more balance before requesting a withdrawal.",
            reply_markup=balance_keyboard(),
        )
        return

    wallet = get_wallet(user_id)
    if not wallet:
        await safe_edit_message(
            query,
            "💸 <b>Withdraw</b>\n\n⚠️ You need to set a withdrawal wallet first.\n\nChoose CBE or Telebirr and save your wallet.",
            reply_markup=withdraw_keyboard(),
        )
        return

    wallet_type, wallet_number = wallet
    context.user_data["withdraw_step"] = "amount"

    await safe_edit_message(
        query,
        f"💸 <b>Withdraw</b>\n\n"
        f"💰 Available Balance: <b>{balance:.2f} ETB</b>\n"
        f"🏦 Method: <b>{escape(wallet_type)}</b>\n"
        f"🔢 Wallet: <code>{escape(wallet_number)}</code>\n\n"
        "Please enter the amount you want to withdraw.\n\n"
        f"Minimum: <b>{MIN_WITHDRAWAL:.2f} ETB</b>\n"
        f"Maximum: <b>{balance:.2f} ETB</b>\n\n"
        "Send only the amount, for example:\n<code>50</code>",
        reply_markup=back_keyboard(),
    )


async def show_support(query, user_id):
    text = (
        "📞 <b>Support & Promotion</b>\n\n"
        "We provide Telegram and digital promotion services.\n\n"
        "📢 <b>Channel Promotion</b>\n"
        "🚀 <b>Channel Growth</b>\n"
        "👥 <b>Group Advertising</b>\n"
        "🏢 <b>Business Promotion</b>\n"
        "📱 <b>App & Website Promotion</b>\n"
        "✨ <b>Custom Promotion</b>\n\n"
        "For price, availability and more information, contact our support team."
    )
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("💬 Contact Support", url="https://t.me/AmanM_12")],
        [InlineKeyboardButton("🔙 Back", callback_data="home")],
    ])
    await safe_edit_message(query, text, reply_markup=keyboard)


# ============================================================
# CHANNEL JOIN / VERIFY
# ============================================================

async def show_join_required(update, context, missing):
    text = (
        "👋 <b>Welcome to Global Cash Bot!</b>\n\n"
        "Before using the bot, please join all required channels below.\n\n"
        "After joining them, press <b>✅ Verify</b>.\n\n"
        f"📌 Remaining channels: <b>{len(missing)}</b>"
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
            "🏦 <b>CBE Wallet</b>\n\nSend your CBE account number.\n\n"
            "Requirements:\n• Exactly 13 digits\n• Must start with <b>1000</b>\n\n"
            "Example:\n<code>1000123456789</code>"
        )
    else:
        instruction = (
            "📱 <b>Telebirr Wallet</b>\n\nSend your Telebirr phone number.\n\n"
            "Requirements:\n• Exactly 10 digits\n• Must start with <b>09</b> or <b>07</b>\n\n"
            "Example:\n<code>0912345678</code>"
        )
    await safe_edit_message(query, instruction, reply_markup=back_keyboard())


# ============================================================
# ADMIN FUNCTIONS
# ============================================================

def is_admin(user_id):
    return user_id == ADMIN_ID


async def show_admin(query):
    if not is_admin(query.from_user.id):
        await query.answer("⛔ Admin only.", show_alert=True)
        return

    await safe_edit_message(
        query,
        "🛠 <b>Global Cash Bot Admin Panel</b>\n\nChoose an option:",
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
        "📊 <b>Statistics</b>\n\n"
        f"👥 Total Users: <b>{total_users}</b>\n"
        f"✅ Verified Users: <b>{verified_users}</b>\n"
        f"💰 Total Balance: <b>{float(total_balance):.2f} ETB</b>\n"
        f"👥 Successful Referrals: <b>{successful_referrals}</b>\n"
        f"💸 Pending Withdrawals: <b>{pending_withdrawals}</b>\n"
        f"⚠️ Suspicious Accounts: <b>{suspicious_accounts}</b>"
    )
    await safe_edit_message(query, text, reply_markup=back_keyboard())


async def show_admin_reward(query):
    if not is_admin(query.from_user.id):
        return

    reward = get_referral_reward()
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("✏️ Change Reward", callback_data="admin_change_reward")],
        [InlineKeyboardButton("🔙 Back", callback_data="admin")],
    ])
    await safe_edit_message(
        query,
        f"💰 <b>Referral Reward Settings</b>\n\nCurrent Reward: <b>{reward:.2f} ETB</b>\n\n"
        "You can change the reward amount.\nThe new amount will apply only to <b>new successful referrals</b>.",
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
        text = "👥 <b>Successful Referrals</b>\n\nNo successful referrals yet."
    else:
        lines = ["👥 <b>Successful Referrals</b>", "", "Top referrers:", ""]
        for row in rows:
            lines.append(f"👤 <code>{row['referrer_id']}</code> — <b>{row['count']}</b> successful")
        lines.extend(["", "To see the exact users referred by an account, use the detailed lookup below."])
        text = "\n".join(lines)

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔎 Detailed Lookup", callback_data="admin_referral_lookup")],
        [InlineKeyboardButton("🔙 Back", callback_data="admin")],
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
        await safe_edit_message(query, "💸 <b>Pending Withdrawals</b>\n\nNo pending withdrawals.", reply_markup=back_keyboard())
        return

    buttons = []
    lines = ["💸 <b>Pending Withdrawals</b>", ""]
    for row in rows:
        lines.append(
            f"🆔 <b>#{row['id']}</b> | User: <code>{row['user_id']}</code>\n"
            f"💰 {row['amount']:.2f} ETB | {escape(row['wallet_type'])}\n"
            f"🔢 <code>{escape(row['wallet_number'])}</code>\n"
        )
        buttons.append([
            InlineKeyboardButton(f"#{row['id']} Approve", callback_data=f"approve_wd_{row['id']}"),
            InlineKeyboardButton(f"#{row['id']} Reject", callback_data=f"reject_wd_{row['id']}"),
        ])

    buttons.append([InlineKeyboardButton("🔙 Back", callback_data="admin")])
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
        text = "⚠️ <b>Suspicious Accounts</b>\n\nNo suspicious accounts."
    else:
        lines = ["⚠️ <b>Suspicious Accounts</b>", ""]
        for row in rows:
            name = f"@{row['username']}" if row['username'] else row['first_name'] or "No username"
            lines.append(f"👤 {escape(name)}\n🆔 <code>{row['user_id']}</code>\n💰 {row['balance']:.2f} ETB\n")
        text = "\n".join(lines)

    await safe_edit_message(query, text, reply_markup=back_keyboard())


async def admin_referral_lookup(query, context):
    if not is_admin(query.from_user.id):
        return

    context.user_data["admin_step"] = "referral_lookup"
    await safe_edit_message(
        query,
        "🔎 <b>Successful Referral Lookup</b>\n\nSend the Telegram User ID of the referrer.\n\nExample:\n<code>123456789</code>",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data="admin")]]),
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
        text = f"👥 <b>Successful Referrals</b>\n\nReferrer ID: <code>{referrer_id}</code>\n\nNo successful referrals found."
    else:
        lines = [
            "👥 <b>Successful Referrals</b>", "",
            f"Referrer ID: <code>{referrer_id}</code>",
            f"Showing last: <b>{len(rows)}</b>", ""
        ]
        for index, row in enumerate(rows, start=1):
            person = f"@{row['username']}" if row['username'] else row['first_name'] or "No username"
            lines.append(f"{index}. 👤 <b>{escape(person)}</b>\n   🆔 <code>{row['referred_id']}</code>\n   🎁 Reward: <b>{row['reward_amount']:.2f} ETB</b>")
        text = "\n\n".join(lines)

    await safe_edit_message(
        query,
        text,
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔙 Back", callback_data="admin_referrals")],
            [InlineKeyboardButton("🏠 Admin Panel", callback_data="admin")],
        ]),
    )


async def start_add_task(query, context):
    if not is_admin(query.from_user.id):
        return

    context.user_data.clear()
    context.user_data["admin_step"] = "task_title"
    await safe_edit_message(
        query,
        "🎯 <b>Add New Task</b>\n\nStep 1/4\n\nSend the task title.\n\nExample:\n<code>Join Smart Money Lab</code>",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data="admin")]]),
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
                    "🎉 <b>New Successful Referral!</b>\n\n"
                    f"👤 User ID: <code>{result['referred_id']}</code>\n"
                    f"🎁 Reward: <b>{result['amount']:.2f} ETB</b>\n\n"
                    "The reward has been added to your balance."
                ),
                parse_mode="HTML",
            )
        except Exception:
            logger.exception("Could not notify inviter.")

    await update.message.reply_text(
        "🎉 <b>Welcome to Global Cash Bot!</b>\n\nYour account is verified successfully.\n\n"
        "💰 Earn ETB through referrals and tasks.\nChoose an option below:",
        reply_markup=main_keyboard(),
        parse_mode="HTML",
    )


async def verify_user(query, context):
    user_id = query.from_user.id
    missing = await missing_channels(context.bot, user_id)

    if missing:
        set_joined_all(user_id, False)
        text = (
            "❌ <b>Verification Failed</b>\n\nYou have not joined all required channels yet.\n\n"
            f"📌 Remaining: <b>{len(missing)}</b>\n\nJoin the remaining channels and press <b>Verify</b> again."
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
                    "🎉 <b>New Successful Referral!</b>\n\n"
                    f"👤 User ID: <code>{result['referred_id']}</code>\n"
                    f"🎁 Reward: <b>{result['amount']:.2f} ETB</b>\n\n"
                    "The reward has been added to your balance."
                ),
                parse_mode="HTML",
            )
        except Exception:
            logger.exception("Could not notify inviter.")

    await safe_edit_message(
        query,
        "✅ <b>Verified Successfully!</b>\n\nAll required channels have been verified.\n\nWelcome to <b>Global Cash Bot</b> 💰",
        reply_markup=main_keyboard(),
    )


async def verify_task(query, context, task_id):
    user_id = query.from_user.id
    task = get_task(task_id)

    if not task or not task["active"]:
        await query.answer("This task is no longer available.", show_alert=True)
        return

    if user_task_paid(user_id, task_id):
        await query.answer("This task is already completed.", show_alert=True)
        return

    try:
        member = await context.bot.get_chat_member(chat_id=task["channel_username"], user_id=user_id)
        if member.status not in (ChatMemberStatus.MEMBER, ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER):
            await query.answer("❌ You have not joined the channel yet.", show_alert=True)
            return
    except Exception:
        await query.answer("Could not verify membership. Try again.", show_alert=True)
        return

    reward = float(task["reward"])
    add_balance(user_id, reward)
    save_user_task(user_id, task_id, verified=True, paid=True)

    await query.answer(f"🎉 +{reward:.2f} ETB added!", show_alert=True)
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
        msg = "❌ Invalid CBE account number.\n\nIt must contain exactly 13 digits and start with 1000." if wallet_type == "CBE" else "❌ Invalid Telebirr number.\n\nIt must contain exactly 10 digits and start with 09 or 07."
        await update.message.reply_text(msg + "\n\nPlease try again.")
        return True

    if wallet_exists_for_other_user(wallet_type, number, user.id):
        await update.message.reply_text(
            "⚠️ <b>This wallet is already linked to another account.</b>\n\nFor security reasons, one wallet can only be connected to one account.",
            parse_mode="HTML",
        )
        return True

    saved = set_wallet(user.id, wallet_type, number)
    if not saved:
        await update.message.reply_text("⚠️ Could not save this wallet.\n\nIt may already be linked to another account.", parse_mode="HTML")
        return True

    context.user_data.pop("wallet_type", None)
    context.user_data.pop("wallet_step", None)

    await update.message.reply_text(
        "✅ <b>Wallet Saved Successfully!</b>\n\n"
        f"💳 Method: <b>{escape(wallet_type)}</b>\n"
        f"🔢 Number: <code>{escape(number)}</code>\n\n"
        "You can now use this wallet for withdrawals.",
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
        await update.message.reply_text("❌ Please enter a valid number.\n\nExample: <code>50</code>", parse_mode="HTML")
        return True

    if amount < MIN_WITHDRAWAL:
        await update.message.reply_text(f"❌ Minimum withdrawal is <b>{MIN_WITHDRAWAL:.2f} ETB</b>.", parse_mode="HTML")
        return True

    if amount <= 0:
        await update.message.reply_text("❌ Amount must be greater than zero.")
        return True

    wallet = get_wallet(user.id)
    if not wallet:
        await update.message.reply_text("❌ No wallet is saved.\n\nPlease set your wallet first.", reply_markup=wallet_keyboard(), parse_mode="HTML")
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
                "❌ Withdrawal could not be created.\n\nYour balance may be insufficient or your account is under review.",
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
        await update.message.reply_text("❌ Something went wrong while creating your withdrawal request.", reply_markup=back_keyboard())
        return True
    finally:
        conn.close()

    context.user_data.pop("withdraw_step", None)

    await update.message.reply_text(
        "✅ <b>Withdrawal Request Submitted!</b>\n\n"
        f"🆔 Request: <b>#{withdrawal_id}</b>\n"
        f"💰 Amount: <b>{amount:.2f} ETB</b>\n"
        f"🏦 Method: <b>{escape(wallet_type)}</b>\n"
        f"🔢 Wallet: <code>{escape(wallet_number)}</code>\n\n"
        "⏳ Status: <b>Pending</b>\n\n"
        "Your request is waiting for admin approval.",
        reply_markup=main_keyboard(),
        parse_mode="HTML",
    )

    try:
        await context.bot.send_message(
            chat_id=ADMIN_ID,
            text=(
                "💳 <b>New Withdrawal</b>\n\n"
                f"🆔 Request: <b>#{withdrawal_id}</b>\n"
                f"👤 User ID: <code>{user.id}</code>\n"
                f"💰 Amount: <b>{amount:.2f} ETB</b>\n"
                f"🏦 Method: <b>{escape(wallet_type)}</b>\n"
                f"🔢 Wallet: <code>{escape(wallet_number)}</code>"
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
        await query.answer("This withdrawal is already processed.", show_alert=True)
        return

    row = conn.execute("SELECT * FROM withdrawals WHERE id=?", (withdrawal_id,)).fetchone()
    conn.commit()
    conn.close()

    await query.answer("✅ Withdrawal approved.", show_alert=True)

    try:
        await context.bot.send_message(
            chat_id=row["user_id"],
            text=(
                "✅ <b>Withdrawal Approved</b>\n\n"
                f"🆔 Request: <b>#{row['id']}</b>\n"
                f"💰 Amount: <b>{row['amount']:.2f} ETB</b>\n"
                f"🏦 Method: <b>{escape(row['wallet_type'])}</b>\n"
                f"🔢 Wallet: <code>{escape(row['wallet_number'])}</code>\n\n"
                "Your withdrawal has been approved."
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
        await query.answer("This withdrawal is already processed.", show_alert=True)
        return

    row = conn.execute("SELECT * FROM withdrawals WHERE id=?", (withdrawal_id,)).fetchone()
    conn.execute("UPDATE users SET balance = balance + ? WHERE user_id=?", (row["amount"], row["user_id"]))
    conn.commit()
    conn.close()

    await query.answer("❌ Withdrawal rejected and refunded.", show_alert=True)

    try:
        await context.bot.send_message(
            chat_id=row["user_id"],
            text=(
                "❌ <b>Withdrawal Rejected</b>\n\n"
                f"🆔 Request: <b>#{row['id']}</b>\n"
                f"💰 Amount: <b>{row['amount']:.2f} ETB</b>\n\n"
                "The amount has been returned to your balance."
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
        await query.answer("Copy your referral link from the message.", show_alert=True)
        await context.bot.send_message(
            chat_id=user.id,
            text=f"📤 <b>Your Referral Link</b>\n\n<code>{escape(link)}</code>\n\nShare this link with your friends.",
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
            f"💰 <b>Change Referral Reward</b>\n\nCurrent Reward: <b>{get_referral_reward():.2f} ETB</b>\n\nSend the new reward amount.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data="admin_reward")]]),
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
                await update.message.reply_text("❌ Please send a valid positive number.")
                return

            set_setting("referral_reward", f"{amount:.2f}")
            context.user_data.pop("admin_step", None)
            await update.message.reply_text(
                f"✅ <b>Referral Reward Updated!</b>\n\nNew Reward: <b>{amount:.2f} ETB</b>",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("💰 Reward Settings", callback_data="admin_reward")],
                    [InlineKeyboardButton("🛠 Admin Panel", callback_data="admin")],
                ]),
                parse_mode="HTML",
            )
            return

        if admin_step == "referral_lookup":
            try:
                referrer_id = int(text)
            except ValueError:
                await update.message.reply_text("❌ Please send a valid Telegram User ID.")
                return

            context.user_data.pop("admin_step", None)
            await show_referral_details(update.message, referrer_id)
            return

        if admin_step == "task_title":
            context.user_data["task_title"] = text
            context.user_data["admin_step"] = "task_description"
            await update.message.reply_text("🎯 <b>Add New Task</b>\n\nStep 2/4\n\nSend the task description.", parse_mode="HTML")
            return

        if admin_step == "task_description":
            context.user_data["task_description"] = text
            context.user_data["admin_step"] = "task_channel"
            await update.message.reply_text("🎯 <b>Add New Task</b>\n\nStep 3/4\n\nSend channel username (e.g., <code>@MyChannel</code>).", parse_mode="HTML")
            return

        if admin_step == "task_channel":
            username = text.strip()
            if not username.startswith("@"):
                username = "@" + username
            context.user_data["task_channel"] = username
            context.user_data["task_url"] = f"https://t.me/{username.lstrip('@')}"
            context.user_data["admin_step"] = "task_reward"
            await update.message.reply_text("🎯 <b>Add New Task</b>\n\nStep 4/4\n\nSend the task reward in ETB.", parse_mode="HTML")
            return

        if admin_step == "task_reward":
            try:
                reward = float(text)
                if reward <= 0:
                    raise ValueError
            except ValueError:
                await update.message.reply_text("❌ Please enter a valid reward amount.")
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
                    [InlineKeyboardButton("🎯 Tasks", callback_data="tasks")],
                    [InlineKeyboardButton("🛠 Admin Panel", callback_data="admin")],
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

    await update.message.reply_text("Please use the buttons below.", reply_markup=main_keyboard())


# ============================================================
# COMMAND HANDLERS & MAIN
# ============================================================

async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("⛔ Admin only.")
        return
    context.user_data.clear()
    await update.message.reply_text("🛠 <b>Global Cash Bot Admin Panel</b>\n\nChoose an option:", reply_markup=admin_keyboard(), parse_mode="HTML")


async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("❌ Action cancelled.", reply_markup=main_keyboard())


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.exception("Unhandled exception:", exc_info=context.error)


def main():
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN environment variable is missing.")
    if not ADMIN_ID:
        raise RuntimeError("ADMIN_ID environment variable is missing.")

    init_db()

    application = Application.builder().token(BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("admin", admin_command))
    application.add_handler(CommandHandler("cancel", cancel_command))
    application.add_handler(CallbackQueryHandler(callback_handler))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler))
    application.add_error_handler(error_handler)

    logger.info("Global Cash Bot is starting...")
    application.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()

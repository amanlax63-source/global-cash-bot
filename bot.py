import os
import re
import html
import sqlite3
import logging
from datetime import datetime

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.constants import ChatMemberStatus
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
)


# =========================================================
# CONFIG
# =========================================================

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

DB_FILE = os.getenv("DB_FILE", "global_cash.db")

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)

logger = logging.getLogger(__name__)


# =========================================================
# ENVIRONMENT
# =========================================================

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_ID_RAW = os.getenv("ADMIN_ID", "").strip()

if not BOT_TOKEN:
    raise RuntimeError(
        "BOT_TOKEN is missing. Add BOT_TOKEN in Railway Variables."
    )

if not ADMIN_ID_RAW:
    raise RuntimeError(
        "ADMIN_ID is missing. Add ADMIN_ID in Railway Variables."
    )

try:
    ADMIN_ID = int(ADMIN_ID_RAW)
except ValueError:
    raise RuntimeError(
        "ADMIN_ID must be a numeric Telegram User ID."
    )


# =========================================================
# DATABASE
# =========================================================

def db_connect():
    conn = sqlite3.connect(DB_FILE, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def now():
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")


def init_db():
    conn = db_connect()
    cur = conn.cursor()

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
            created_at TEXT NOT NULL,
            processed_at TEXT
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
            channel_username TEXT,
            channel_url TEXT,
            reward REAL NOT NULL DEFAULT 0,
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
        CREATE UNIQUE INDEX IF NOT EXISTS unique_wallet
        ON users(wallet_type, wallet_number)
        WHERE wallet_type IS NOT NULL
        AND wallet_number IS NOT NULL
        AND wallet_number != ''
    """)

    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_referrer
        ON referrals(referrer_id)
    """)

    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_withdraw_status
        ON withdrawals(status)
    """)

    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_user_tasks
        ON user_tasks(user_id)
    """)

    # Default referral reward
    cur.execute("""
        INSERT OR IGNORE INTO settings(key, value)
        VALUES('referral_reward', '2.00')
    """)

    conn.commit()
    conn.close()


# =========================================================
# SETTINGS
# =========================================================

def get_setting(key, default=None):
    conn = db_connect()
    row = conn.execute(
        "SELECT value FROM settings WHERE key = ?",
        (key,),
    ).fetchone()
    conn.close()

    if row:
        return row["value"]

    return default


def set_setting(key, value):
    conn = db_connect()

    conn.execute("""
        INSERT INTO settings(key, value)
        VALUES(?, ?)
        ON CONFLICT(key)
        DO UPDATE SET value = excluded.value
    """, (key, str(value)))

    conn.commit()
    conn.close()


def get_referral_reward():
    value = get_setting("referral_reward", "2.00")

    try:
        return float(value)
    except (TypeError, ValueError):
        return 2.0


# =========================================================
# USERS
# =========================================================

def create_or_update_user(user, referred_by=None):
    conn = db_connect()

    row = conn.execute(
        "SELECT * FROM users WHERE user_id = ?",
        (user.id,),
    ).fetchone()

    username = user.username or ""
    first_name = user.first_name or ""

    if row is None:
        referrer = None

        if referred_by:
            try:
                referred_by = int(referred_by)

                if referred_by != user.id:
                    referrer = referred_by
            except ValueError:
                referrer = None

        conn.execute("""
            INSERT INTO users(
                user_id,
                username,
                first_name,
                balance,
                referred_by,
                referral_paid,
                referral_status,
                joined_all,
                suspicious,
                wallet_type,
                wallet_number,
                created_at
            )
            VALUES(?, ?, ?, 0, ?, 0, ?, 0, 0, NULL, NULL, ?)
        """, (
            user.id,
            username,
            first_name,
            referrer,
            "pending" if referrer else "none",
            now(),
        ))

    else:
        conn.execute("""
            UPDATE users
            SET username = ?,
                first_name = ?
            WHERE user_id = ?
        """, (
            username,
            first_name,
            user.id,
        ))

    conn.commit()
    conn.close()


def get_user(user_id):
    conn = db_connect()

    row = conn.execute(
        "SELECT * FROM users WHERE user_id = ?",
        (user_id,),
    ).fetchone()

    conn.close()
    return row


def get_balance(user_id):
    row = get_user(user_id)

    if not row:
        return 0.0

    return float(row["balance"])


def add_balance(user_id, amount):
    conn = db_connect()

    conn.execute("""
        UPDATE users
        SET balance = balance + ?
        WHERE user_id = ?
    """, (
        amount,
        user_id,
    ))

    conn.commit()
    conn.close()


def set_joined_all(user_id, value):
    conn = db_connect()

    conn.execute("""
        UPDATE users
        SET joined_all = ?
        WHERE user_id = ?
    """, (
        1 if value else 0,
        user_id,
    ))

    conn.commit()
    conn.close()


def mark_suspicious(user_id, value=True):
    conn = db_connect()

    conn.execute("""
        UPDATE users
        SET suspicious = ?
        WHERE user_id = ?
    """, (
        1 if value else 0,
        user_id,
    ))

    conn.commit()
    conn.close()


def is_suspicious(user_id):
    row = get_user(user_id)

    if not row:
        return False

    return bool(row["suspicious"])


# =========================================================
# WALLET
# =========================================================

def get_wallet(user_id):
    row = get_user(user_id)

    if not row:
        return None

    if not row["wallet_type"] or not row["wallet_number"]:
        return None

    return (
        row["wallet_type"],
        row["wallet_number"],
    )


def wallet_exists_for_other_user(user_id, wallet_type, wallet_number):
    conn = db_connect()

    row = conn.execute("""
        SELECT user_id
        FROM users
        WHERE wallet_type = ?
        AND wallet_number = ?
        AND user_id != ?
    """, (
        wallet_type,
        wallet_number,
        user_id,
    )).fetchone()

    conn.close()

    return row is not None


def save_wallet(user_id, wallet_type, wallet_number):
    conn = db_connect()

    try:
        conn.execute("""
            UPDATE users
            SET wallet_type = ?,
                wallet_number = ?
            WHERE user_id = ?
        """, (
            wallet_type,
            wallet_number,
            user_id,
        ))

        conn.commit()
        return True

    except sqlite3.IntegrityError:
        conn.rollback()
        return False

    finally:
        conn.close()


# =========================================================
# REFERRALS
# =========================================================

def get_successful_referral_count(user_id):
    conn = db_connect()

    row = conn.execute("""
        SELECT COUNT(*) AS count
        FROM referrals
        WHERE referrer_id = ?
        AND status = 'paid'
    """, (user_id,)).fetchone()

    conn.close()

    return int(row["count"])


def get_referrals(user_id):
    conn = db_connect()

    rows = conn.execute("""
        SELECT
            r.referred_id,
            r.reward_amount,
            r.created_at,
            u.username,
            u.first_name
        FROM referrals r
        LEFT JOIN users u
            ON u.user_id = r.referred_id
        WHERE r.referrer_id = ?
        AND r.status = 'paid'
        ORDER BY r.id DESC
    """, (user_id,)).fetchall()

    conn.close()

    return rows


def process_referral_reward(user_id):
    conn = db_connect()

    try:
        user = conn.execute(
            "SELECT * FROM users WHERE user_id = ?",
            (user_id,),
        ).fetchone()

        if not user:
            conn.commit()
            return None

        referrer_id = user["referred_by"]

        if not referrer_id:
            conn.commit()
            return None

        if user["referral_paid"]:
            conn.commit()
            return None

        if user["suspicious"]:
            conn.execute("""
                UPDATE users
                SET referral_status = 'blocked'
                WHERE user_id = ?
            """, (user_id,))

            conn.commit()
            return None

        if int(referrer_id) == int(user_id):
            conn.execute("""
                UPDATE users
                SET referral_status = 'blocked',
                    referral_paid = 0
                WHERE user_id = ?
            """, (user_id,))

            conn.commit()
            return None

        referrer = conn.execute(
            "SELECT * FROM users WHERE user_id = ?",
            (referrer_id,),
        ).fetchone()

        if not referrer:
            conn.commit()
            return None

        if referrer["suspicious"]:
            conn.execute("""
                UPDATE users
                SET referral_status = 'blocked'
                WHERE user_id = ?
            """, (user_id,))

            conn.commit()
            return None

        existing = conn.execute("""
            SELECT id
            FROM referrals
            WHERE referred_id = ?
        """, (user_id,)).fetchone()

        if existing:
            conn.execute("""
                UPDATE users
                SET referral_paid = 1,
                    referral_status = 'paid'
                WHERE user_id = ?
            """, (user_id,))

            conn.commit()
            return None

        reward = get_referral_reward()

        conn.execute("""
            UPDATE users
            SET balance = balance + ?
            WHERE user_id = ?
        """, (
            reward,
            referrer_id,
        ))

        conn.execute("""
            INSERT INTO referrals(
                referrer_id,
                referred_id,
                reward_amount,
                status,
                created_at
            )
            VALUES(?, ?, ?, 'paid', ?)
        """, (
            referrer_id,
            user_id,
            reward,
            now(),
        ))

        conn.execute("""
            UPDATE users
            SET referral_paid = 1,
                referral_status = 'paid'
            WHERE user_id = ?
        """, (user_id,))

        conn.commit()

        return {
            "referrer_id": referrer_id,
            "reward": reward,
        }

    except Exception:
        conn.rollback()
        logger.exception("Referral processing error")
        return None

    finally:
        conn.close()


# =========================================================
# CHANNEL VERIFICATION
# =========================================================

async def check_channel_membership(bot, user_id):
    missing = []

    for username, url in REQUIRED_CHANNELS:
        try:
            member = await bot.get_chat_member(
                chat_id=username,
                user_id=user_id,
            )

            status = member.status

            if status not in (
                ChatMemberStatus.MEMBER,
                ChatMemberStatus.ADMINISTRATOR,
                ChatMemberStatus.OWNER,
            ):
                missing.append((username, url))

        except Exception as e:
            logger.warning(
                "Channel check failed for %s: %s",
                username,
                e,
            )

            missing.append((username, url))

    return missing


# =========================================================
# KEYBOARDS
# =========================================================

def join_keyboard(missing):
    buttons = []

    for username, url in missing:
        buttons.append([
            InlineKeyboardButton(
                f"📢 {username}",
                url=url,
            )
        ])

    buttons.append([
        InlineKeyboardButton(
            "✅ Verify / አረጋግጥ",
            callback_data="verify",
        )
    ])

    return InlineKeyboardMarkup(buttons)


def main_menu_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("💰 Balance", callback_data="balance"),
            InlineKeyboardButton("👥 Referral", callback_data="referral"),
        ],
        [
            InlineKeyboardButton("🎯 Tasks", callback_data="tasks"),
            InlineKeyboardButton("💸 Withdraw", callback_data="withdraw"),
        ],
        [
            InlineKeyboardButton("👛 Wallet", callback_data="wallet"),
            InlineKeyboardButton("📞 Support", callback_data="support"),
        ],
    ])


def back_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "🔙 Back",
                callback_data="home",
            )
        ]
    ])


def wallet_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "🏦 CBE",
                callback_data="wallet_cbe",
            ),
            InlineKeyboardButton(
                "📱 Telebirr",
                callback_data="wallet_telebirr",
            ),
        ],
        [
            InlineKeyboardButton(
                "🔙 Back",
                callback_data="home",
            )
        ],
    ])


def withdraw_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "💸 Withdraw",
                callback_data="withdraw",
            )
        ],
        [
            InlineKeyboardButton(
                "👛 Wallet",
                callback_data="wallet",
            )
        ],
        [
            InlineKeyboardButton(
                "🔙 Back",
                callback_data="home",
            )
        ],
    ])


def admin_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "📊 Statistics",
                callback_data="admin_stats",
            )
        ],
        [
            InlineKeyboardButton(
                "💰 Referral Reward",
                callback_data="admin_reward",
            )
        ],
        [
            InlineKeyboardButton(
                "💸 Pending Withdrawals",
                callback_data="admin_withdrawals",
            )
        ],
        [
            InlineKeyboardButton(
                "👥 Successful Referrals",
                callback_data="admin_referrals",
            )
        ],
        [
            InlineKeyboardButton(
                "⚠️ Suspicious Accounts",
                callback_data="admin_suspicious",
            )
        ],
        [
            InlineKeyboardButton(
                "🔙 Close",
                callback_data="home",
            )
        ],
    ])


def admin_cancel_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "❌ Cancel",
                callback_data="admin_cancel",
            )
        ]
    ])


# =========================================================
# TEXT HELPERS
# =========================================================

def user_display(row):
    username = row["username"] or ""

    if username:
        return f"@{html.escape(username)}"

    name = row["first_name"] or "Unknown"

    return (
        f"{html.escape(name)} "
        f"(<code>{row['user_id']}</code>)"
    )


def referral_link(user_id):
    return f"https://t.me/{BOT_USERNAME}?start={user_id}"


# =========================================================
# START
# =========================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user

    referred_by = None

    if context.args:
        raw = context.args[0].strip()

        if raw.isdigit():
            try:
                ref_id = int(raw)

                if ref_id != user.id:
                    referred_by = ref_id

            except ValueError:
                pass

    create_or_update_user(
        user,
        referred_by=referred_by,
    )

    missing = await check_channel_membership(
        context.bot,
        user.id,
    )

    if missing:
        set_joined_all(user.id, False)

        text = (
            "💎 <b>Welcome to Global Cash Bot!</b>\n\n"
            "🇪🇹 ለቦቱን ሙሉ በሙሉ ለመጠቀም "
            "ከታች ያሉትን channels መጀመሪያ ይቀላቀሉ።\n\n"
            "👇 <b>Required Channels</b>\n"
            "ከጨረሱ በኋላ <b>Verify</b> ይጫኑ።"
        )

        await update.message.reply_text(
            text,
            reply_markup=join_keyboard(missing),
            parse_mode="HTML",
        )

        return

    set_joined_all(user.id, True)

    result = process_referral_reward(user.id)

    if result:
        try:
            await context.bot.send_message(
                chat_id=result["referrer_id"],
                text=(
                    "🎉 <b>New Successful Referral!</b>\n\n"
                    f"👤 User ID: <code>{user.id}</code>\n"
                    f"💰 Reward: <b>{result['reward']:.2f} ETB</b>\n\n"
                    "Your referral reward has been added."
                ),
                parse_mode="HTML",
            )
        except Exception:
            logger.exception(
                "Could not notify referrer."
            )

    await update.message.reply_text(
        "🎉 <b>Verification Successful!</b>\n\n"
        "🇪🇹 እንኳን ወደ <b>Global Cash Bot</b> በደህና መጡ።\n\n"
        "💎 ከታች ያሉትን options በመጠቀም ይጀምሩ።",
        reply_markup=main_menu_keyboard(),
        parse_mode="HTML",
    )


# =========================================================
# HOME
# =========================================================

async def show_home(query):
    await query.edit_message_text(
        "💎 <b>Global Cash Bot</b>\n\n"
        "🇪🇹 እንኳን በደህና መጡ።\n"
        "ከታች ያለውን menu ይምረጡ።",
        reply_markup=main_menu_keyboard(),
        parse_mode="HTML",
    )


# =========================================================
# BALANCE
# =========================================================

async def show_balance(query, user_id):
    balance = get_balance(user_id)

    await query.edit_message_text(
        "💰 <b>Your Balance</b>\n\n"
        f"💵 Balance: <b>{balance:.2f} ETB</b>\n"
        f"💸 Minimum Withdrawal: <b>{MIN_WITHDRAWAL:.2f} ETB</b>\n\n"
        "🇪🇹 የሚያገኙትን ገንዘብ ከዚህ ላይ ማየት ይችላሉ።",
        reply_markup=withdraw_keyboard(),
        parse_mode="HTML",
    )


# =========================================================
# REFERRAL
# =========================================================

async def show_referral(query, user_id):
    count = get_successful_referral_count(user_id)
    reward = get_referral_reward()
    link = referral_link(user_id)

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "📤 Share Link",
                url=(
                    "https://t.me/share/url?"
                    f"url={link}"
                ),
            )
        ],
        [
            InlineKeyboardButton(
                "🔙 Back",
                callback_data="home",
            )
        ],
    ])

    text = (
        "👥 <b>Referral Program</b>\n\n"
        f"👤 Successful Referrals: <b>{count}</b>\n"
        f"💰 Current Reward: <b>{reward:.2f} ETB</b>\n\n"
        "🔗 <b>Your Referral Link:</b>\n"
        f"<code>{html.escape(link)}</code>\n\n"
        "📌 ሰው በእርስዎ link መጥቶ "
        "ሁሉንም required channels ከተቀላቀለና Verify ካደረገ "
        "ብቻ successful referral ይቆጠራል።"
    )

    await query.edit_message_text(
        text,
        reply_markup=keyboard,
        parse_mode="HTML",
    )


# =========================================================
# TASKS
# =========================================================

async def show_tasks(query, user_id):
    conn = db_connect()

    tasks = conn.execute("""
        SELECT *
        FROM tasks
        WHERE active = 1
        ORDER BY id DESC
    """).fetchall()

    conn.close()

    if not tasks:
        await query.edit_message_text(
            "🎯 <b>Tasks</b>\n\n"
            "📌 አሁን ላይ የሚገኙ active tasks የሉም።\n\n"
            "🔥 New earning tasks ሲጨመሩ እዚህ ይታያሉ።",
            reply_markup=back_keyboard(),
            parse_mode="HTML",
        )
        return

    buttons = []

    for task in tasks:
        buttons.append([
            InlineKeyboardButton(
                f"🎯 {task['title']}",
                callback_data=f"task_{task['id']}",
            )
        ])

    buttons.append([
        InlineKeyboardButton(
            "🔙 Back",
            callback_data="home",
        )
    ])

    await query.edit_message_text(
        "🎯 <b>Available Tasks</b>\n\n"
        "ከታች ያለውን task ይምረጡ።",
        reply_markup=InlineKeyboardMarkup(buttons),
        parse_mode="HTML",
    )


async def show_task(query, user_id, task_id):
    conn = db_connect()

    task = conn.execute("""
        SELECT *
        FROM tasks
        WHERE id = ?
        AND active = 1
    """, (task_id,)).fetchone()

    done = conn.execute("""
        SELECT *
        FROM user_tasks
        WHERE user_id = ?
        AND task_id = ?
    """, (
        user_id,
        task_id,
    )).fetchone()

    conn.close()

    if not task:
        await query.answer(
            "Task not found.",
            show_alert=True,
        )
        return

    buttons = []

    if task["channel_url"]:
        buttons.append([
            InlineKeyboardButton(
                "📢 Open Task",
                url=task["channel_url"],
            )
        ])

    if done and done["paid"]:
        status = "✅ Completed"
    else:
        status = "⏳ Not verified"

        buttons.append([
            InlineKeyboardButton(
                "✅ Verify Task",
                callback_data=f"verify_task_{task_id}",
            )
        ])

    buttons.append([
        InlineKeyboardButton(
            "🔙 Back",
            callback_data="tasks",
        )
    ])

    text = (
        f"🎯 <b>{html.escape(task['title'])}</b>\n\n"
        f"{html.escape(task['description'])}\n\n"
        f"💰 Reward: <b>{task['reward']:.2f} ETB</b>\n"
        f"📌 Status: <b>{status}</b>"
    )

    await query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup(buttons),
        parse_mode="HTML",
    )


async def verify_task(query, user_id, task_id):
    conn = db_connect()

    task = conn.execute("""
        SELECT *
        FROM tasks
        WHERE id = ?
        AND active = 1
    """, (task_id,)).fetchone()

    if not task:
        conn.close()

        await query.answer(
            "Task not found.",
            show_alert=True,
        )
        return

    existing = conn.execute("""
        SELECT *
        FROM user_tasks
        WHERE user_id = ?
        AND task_id = ?
    """, (
        user_id,
        task_id,
    )).fetchone()

    if existing and existing["paid"]:
        conn.close()

        await query.answer(
            "Already completed.",
            show_alert=True,
        )
        return

    conn.close()

    if not task["channel_username"]:
        await query.answer(
            "No verification channel configured.",
            show_alert=True,
        )
        return

    try:
        member = await query.get_bot().get_chat_member(
            chat_id=task["channel_username"],
            user_id=user_id,
        )

        if member.status not in (
            ChatMemberStatus.MEMBER,
            ChatMemberStatus.ADMINISTRATOR,
            ChatMemberStatus.OWNER,
        ):
            await query.answer(
                "Please join the task channel first.",
                show_alert=True,
            )
            return

    except Exception:
        await query.answer(
            "Verification failed. Try again.",
            show_alert=True,
        )
        return

    conn = db_connect()

    existing = conn.execute("""
        SELECT *
        FROM user_tasks
        WHERE user_id = ?
        AND task_id = ?
    """, (
        user_id,
        task_id,
    )).fetchone()

    if existing and existing["paid"]:
        conn.close()

        await query.answer(
            "Already completed.",
            show_alert=True,
        )
        return

    reward = float(task["reward"])

    conn.execute("""
        INSERT INTO user_tasks(
            user_id,
            task_id,
            verified,
            paid,
            paid_at
        )
        VALUES(?, ?, 1, 1, ?)
        ON CONFLICT(user_id, task_id)
        DO UPDATE SET
            verified = 1,
            paid = 1,
            paid_at = excluded.paid_at
    """, (
        user_id,
        task_id,
        now(),
    ))

    conn.execute("""
        UPDATE users
        SET balance = balance + ?
        WHERE user_id = ?
    """, (
        reward,
        user_id,
    ))

    conn.commit()
    conn.close()

    await query.answer(
        f"Success! +{reward:.2f} ETB",
        show_alert=True,
    )

    await show_tasks(query, user_id)


# =========================================================
# WALLET
# =========================================================

async def show_wallet(query, user_id):
    wallet = get_wallet(user_id)

    if wallet:
        wallet_text = (
            f"🏦 Method: <b>{html.escape(wallet[0])}</b>\n"
            f"🔢 Number: <code>{html.escape(wallet[1])}</code>\n\n"
            "ይህን wallet በመቀየር ሌላ wallet ማስቀመጥ ይችላሉ።"
        )
    else:
        wallet_text = (
            "⚠️ <b>No Wallet Saved</b>\n\n"
            "Withdrawal ለማድረግ CBE ወይም Telebirr "
            "wallet ያስገቡ።"
        )

    await query.edit_message_text(
        "👛 <b>Wallet</b>\n\n" + wallet_text,
        reply_markup=wallet_keyboard(),
        parse_mode="HTML",
    )


async def ask_wallet_number(query, context, wallet_type):
    context.user_data["wallet_type"] = wallet_type

    if wallet_type == "CBE":
        text = (
            "🏦 <b>CBE Wallet</b>\n\n"
            "13-digit CBE account number ያስገቡ።\n\n"
            "Example:\n"
            "<code>1000123456789</code>\n\n"
            "🔙 ለመመለስ /cancel ይጻፉ።"
        )

    else:
        text = (
            "📱 <b>Telebirr Wallet</b>\n\n"
            "10-digit Telebirr number ያስገቡ።\n\n"
            "Number 09 ወይም 07 መጀመር አለበት።\n\n"
            "Example:\n"
            "<code>0912345678</code>\n\n"
            "🔙 ለመመለስ /cancel ይጻፉ።"
        )

    await query.edit_message_text(
        text,
        parse_mode="HTML",
    )


# =========================================================
# WITHDRAW
# =========================================================

async def show_withdraw(query, user_id):
    row = get_user(user_id)

    if not row:
        await query.edit_message_text(
            "User not found.",
            reply_markup=back_keyboard(),
        )
        return

    if row["suspicious"]:
        await query.edit_message_text(
            "⚠️ <b>Security Review</b>\n\n"
            "Your account is currently under security review.\n\n"
            f"📞 Contact: {SUPPORT_USERNAME}",
            reply_markup=back_keyboard(),
            parse_mode="HTML",
        )
        return

    balance = float(row["balance"])

    if balance < MIN_WITHDRAWAL:
        await query.edit_message_text(
            "💸 <b>Withdraw</b>\n\n"
            f"Your Balance: <b>{balance:.2f} ETB</b>\n"
            f"Minimum: <b>{MIN_WITHDRAWAL:.2f} ETB</b>\n\n"
            "⚠️ ቢያንስ 30 ETB ሲደርስ withdrawal ማድረግ ይችላሉ።",
            reply_markup=withdraw_keyboard(),
            parse_mode="HTML",
        )
        return

    wallet = get_wallet(user_id)

    if not wallet:
        await query.edit_message_text(
            "👛 <b>Wallet Required</b>\n\n"
            "Withdrawal ከማድረግዎ በፊት CBE ወይም Telebirr "
            "wallet ያስቀምጡ።",
            reply_markup=wallet_keyboard(),
            parse_mode="HTML",
        )
        return

    context_text = (
        "💸 <b>Withdraw</b>\n\n"
        f"💰 Available Balance: <b>{balance:.2f} ETB</b>\n"
        f"🏦 Method: <b>{html.escape(wallet[0])}</b>\n"
        f"🔢 Wallet: <code>{html.escape(wallet[1])}</code>\n\n"
        "የሚወጣውን amount በETB ቁጥር ያስገቡ።\n\n"
        "Example: <code>30</code>"
    )

    context.user_data["withdraw_mode"] = True

    await query.edit_message_text(
        context_text,
        parse_mode="HTML",
    )


async def create_withdrawal(user_id, amount):
    conn = db_connect()

    try:
        row = conn.execute("""
            SELECT balance, wallet_type, wallet_number, suspicious
            FROM users
            WHERE user_id = ?
        """, (user_id,)).fetchone()

        if not row:
            conn.rollback()
            return False, "User not found."

        if row["suspicious"]:
            conn.rollback()
            return False, "Security review."

        if not row["wallet_type"] or not row["wallet_number"]:
            conn.rollback()
            return False, "Wallet required."

        # Deduct only if enough balance exists.
        cur = conn.execute("""
            UPDATE users
            SET balance = balance - ?
            WHERE user_id = ?
            AND balance >= ?
            AND suspicious = 0
        """, (
            amount,
            user_id,
            amount,
        ))

        if cur.rowcount != 1:
            conn.rollback()
            return False, "Insufficient balance."

        conn.execute("""
            INSERT INTO withdrawals(
                user_id,
                amount,
                wallet_type,
                wallet_number,
                status,
                created_at
            )
            VALUES(?, ?, ?, ?, 'pending', ?)
        """, (
            user_id,
            amount,
            row["wallet_type"],
            row["wallet_number"],
            now(),
        ))

        withdrawal_id = conn.execute(
            "SELECT last_insert_rowid()"
        ).fetchone()[0]

        conn.commit()

        return True, withdrawal_id

    except Exception:
        conn.rollback()
        logger.exception("Withdrawal creation error")
        return False, "Database error."

    finally:
        conn.close()


# =========================================================
# SUPPORT
# =========================================================

async def show_support(query):
    text = (
        "📢 <b>Advertising & Telegram Promotion</b>\n\n"
        "🚀 <b>Channel Promotion</b>\n"
        "📈 <b>Channel Growth</b>\n"
        "👥 <b>Group Advertising</b>\n"
        "🏢 <b>Business Promotion</b>\n"
        "🌐 <b>App & Website Promotion</b>\n"
        "✨ <b>Custom Promotion</b>\n\n"
        "🇪🇹 ለማስታወቂያ እና promotion አገልግሎት "
        "ያግኙን።\n\n"
        "💰 Price & More Info ለማወቅ Contact Support ይጫኑ።"
    )

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "💬 Contact Support",
                url="https://t.me/AmanM_12",
            )
        ],
        [
            InlineKeyboardButton(
                "🔙 Back",
                callback_data="home",
            )
        ],
    ])

    await query.edit_message_text(
        text,
        reply_markup=keyboard,
        parse_mode="HTML",
    )


# =========================================================
# ADMIN
# =========================================================

async def admin_command(update, context):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text(
            "⛔ Access denied."
        )
        return

    context.user_data.clear()

    await update.message.reply_text(
        "👑 <b>Global Cash Bot Admin Panel</b>\n\n"
        "Admin-only controls:",
        reply_markup=admin_keyboard(),
        parse_mode="HTML",
    )


async def admin_stats(query):
    conn = db_connect()

    total_users = conn.execute(
        "SELECT COUNT(*) AS c FROM users"
    ).fetchone()["c"]

    verified_users = conn.execute("""
        SELECT COUNT(*) AS c
        FROM users
        WHERE joined_all = 1
    """).fetchone()["c"]

    total_balance = conn.execute("""
        SELECT COALESCE(SUM(balance), 0) AS total
        FROM users
    """).fetchone()["total"]

    successful_referrals = conn.execute("""
        SELECT COUNT(*) AS c
        FROM referrals
        WHERE status = 'paid'
    """).fetchone()["c"]

    pending_withdrawals = conn.execute("""
        SELECT COUNT(*) AS c
        FROM withdrawals
        WHERE status = 'pending'
    """).fetchone()["c"]

    suspicious = conn.execute("""
        SELECT COUNT(*) AS c
        FROM users
        WHERE suspicious = 1
    """).fetchone()["c"]

    conn.close()

    text = (
        "📊 <b>Statistics</b>\n\n"
        f"👥 Total Users: <b>{total_users}</b>\n"
        f"✅ Verified Users: <b>{verified_users}</b>\n"
        f"💰 Total Balance: <b>{float(total_balance):.2f} ETB</b>\n"
        f"👥 Successful Referrals: <b>{successful_referrals}</b>\n"
        f"💸 Pending Withdrawals: <b>{pending_withdrawals}</b>\n"
        f"⚠️ Suspicious Accounts: <b>{suspicious}</b>\n"
    )

    await query.edit_message_text(
        text,
        reply_markup=admin_keyboard(),
        parse_mode="HTML",
    )


# =========================================================
# ADMIN REFERRAL REWARD
# =========================================================

async def admin_reward(query, context):
    reward = get_referral_reward()

    context.user_data["admin_state"] = "reward"

    await query.edit_message_text(
        "💰 <b>Referral Reward Settings</b>\n\n"
        f"Current Reward: <b>{reward:.2f} ETB</b>\n\n"
        "Enter the new amount.\n\n"
        "Example:\n"
        "<code>1.00</code>\n"
        "<code>2.00</code>\n"
        "<code>5.00</code>\n\n"
        "📌 This applies only to new successful referrals.\n"
        "Previously paid rewards will NOT change.",
        reply_markup=admin_cancel_keyboard(),
        parse_mode="HTML",
    )


async def admin_referral_list(query):
    conn = db_connect()

    rows = conn.execute("""
        SELECT
            u.user_id,
            u.username,
            u.first_name,
            COUNT(r.id) AS count
        FROM users u
        JOIN referrals r
            ON r.referrer_id = u.user_id
        WHERE r.status = 'paid'
        GROUP BY u.user_id
        ORDER BY count DESC
        LIMIT 20
    """).fetchall()

    conn.close()

    if not rows:
        await query.edit_message_text(
            "👥 <b>Successful Referrals</b>\n\n"
            "No successful referrals yet.",
            reply_markup=admin_keyboard(),
            parse_mode="HTML",
        )
        return

    buttons = []

    for row in rows:
        name = row["username"]

        if name:
            label = f"@{name} — {row['count']}"
        else:
            label = f"{row['user_id']} — {row['count']}"

        buttons.append([
            InlineKeyboardButton(
                f"👤 {label}",
                callback_data=f"admin_ref_{row['user_id']}",
            )
        ])

    buttons.append([
        InlineKeyboardButton(
            "🔙 Back",
            callback_data="admin",
        )
    ])

    await query.edit_message_text(
        "👥 <b>Successful Referrals</b>\n\n"
        "Select a referrer to see exactly who they referred:",
        reply_markup=InlineKeyboardMarkup(buttons),
        parse_mode="HTML",
    )


async def admin_referral_detail(query, referrer_id):
    rows = get_referrals(referrer_id)
    referrer = get_user(referrer_id)

    if not rows:
        await query.edit_message_text(
            "No successful referrals found.",
            reply_markup=back_keyboard(),
        )
        return

    text = (
        "👥 <b>Successful Referrals</b>\n\n"
        f"Referrer: {user_display(referrer)}\n"
        f"Total: <b>{len(rows)}</b>\n\n"
    )

    for index, row in enumerate(rows, start=1):
        username = row["username"]

        if username:
            person = f"@{html.escape(username)}"
        else:
            person = (
                f"{html.escape(row['first_name'] or 'Unknown')} "
                f"(<code>{row['referred_id']}</code>)"
            )

        text += (
            f"{index}. {person}\n"
            f"   💰 Reward: {row['reward_amount']:.2f} ETB\n"
            f"   📅 {row['created_at']}\n\n"
        )

        if len(text) > 3800:
            text += "\n...and more."
            break

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "🔙 Back",
                callback_data="admin_referrals",
            )
        ],
        [
            InlineKeyboardButton(
                "👑 Admin",
                callback_data="admin",
            )
        ],
    ])

    await query.edit_message_text(
        text,
        reply_markup=keyboard,
        parse_mode="HTML",
    )


# =========================================================
# ADMIN WITHDRAWALS
# =========================================================

async def admin_withdrawals(query):
    conn = db_connect()

    rows = conn.execute("""
        SELECT *
        FROM withdrawals
        WHERE status = 'pending'
        ORDER BY id ASC
        LIMIT 20
    """).fetchall()

    conn.close()

    if not rows:
        await query.edit_message_text(
            "💸 <b>Pending Withdrawals</b>\n\n"
            "No pending withdrawals.",
            reply_markup=admin_keyboard(),
            parse_mode="HTML",
        )
        return

    buttons = []

    for row in rows:
        buttons.append([
            InlineKeyboardButton(
                f"💸 #{row['id']} — {row['amount']:.2f} ETB",
                callback_data=f"admin_wd_{row['id']}",
            )
        ])

    buttons.append([
        InlineKeyboardButton(
            "🔙 Back",
            callback_data="admin",
        )
    ])

    await query.edit_message_text(
        "💸 <b>Pending Withdrawals</b>\n\n"
        "Select a withdrawal:",
        reply_markup=InlineKeyboardMarkup(buttons),
        parse_mode="HTML",
    )


async def admin_withdrawal_detail(query, withdrawal_id):
    conn = db_connect()

    row = conn.execute("""
        SELECT *
        FROM withdrawals
        WHERE id = ?
    """, (withdrawal_id,)).fetchone()

    conn.close()

    if not row:
        await query.answer(
            "Withdrawal not found.",
            show_alert=True,
        )
        return

    user = get_user(row["user_id"])

    text = (
        "💸 <b>Withdrawal Details</b>\n\n"
        f"🆔 Withdrawal: <code>#{row['id']}</code>\n"
        f"👤 User: {user_display(user)}\n"
        f"💰 Amount: <b>{row['amount']:.2f} ETB</b>\n"
        f"🏦 Method: <b>{html.escape(row['wallet_type'])}</b>\n"
        f"🔢 Wallet: <code>{html.escape(row['wallet_number'])}</code>\n"
        f"📌 Status: <b>{html.escape(row['status'])}</b>\n"
        f"📅 Created: {row['created_at']}\n"
    )

    buttons = []

    if row["status"] == "pending":
        buttons.append([
            InlineKeyboardButton(
                "✅ Approve",
                callback_data=f"approve_wd_{row['id']}",
            ),
            InlineKeyboardButton(
                "❌ Reject",
                callback_data=f"reject_wd_{row['id']}",
            ),
        ])

    buttons.append([
        InlineKeyboardButton(
            "🔙 Back",
            callback_data="admin_withdrawals",
        )
    ])

    await query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup(buttons),
        parse_mode="HTML",
    )


async def approve_withdrawal(query, withdrawal_id):
    conn = db_connect()

    cur = conn.execute("""
        UPDATE withdrawals
        SET status = 'approved',
            processed_at = ?
        WHERE id = ?
        AND status = 'pending'
    """, (
        now(),
        withdrawal_id,
    ))

    if cur.rowcount != 1:
        conn.rollback()
        conn.close()

        await query.answer(
            "Already processed.",
            show_alert=True,
        )
        return

    row = conn.execute("""
        SELECT *
        FROM withdrawals
        WHERE id = ?
    """, (withdrawal_id,)).fetchone()

    conn.commit()
    conn.close()

    await query.answer(
        "Withdrawal approved.",
        show_alert=True,
    )

    try:
        await query.get_bot().send_message(
            chat_id=row["user_id"],
            text=(
                "✅ <b>Withdrawal Approved</b>\n\n"
                f"💰 Amount: <b>{row['amount']:.2f} ETB</b>\n"
                f"🏦 Method: <b>{html.escape(row['wallet_type'])}</b>\n"
                f"🔢 Wallet: <code>{html.escape(row['wallet_number'])}</code>\n\n"
                "Your withdrawal has been approved."
            ),
            parse_mode="HTML",
        )
    except Exception:
        logger.exception("Could not notify user.")

    await admin_withdrawals(query)


async def reject_withdrawal(query, withdrawal_id):
    conn = db_connect()

    row = conn.execute("""
        SELECT *
        FROM withdrawals
        WHERE id = ?
        AND status = 'pending'
    """, (withdrawal_id,)).fetchone()

    if not row:
        conn.close()

        await query.answer(
            "Already processed.",
            show_alert=True,
        )
        return

    cur = conn.execute("""
        UPDATE withdrawals
        SET status = 'rejected',
            processed_at = ?
        WHERE id = ?
        AND status = 'pending'
    """, (
        now(),
        withdrawal_id,
    ))

    if cur.rowcount != 1:
        conn.rollback()
        conn.close()

        await query.answer(
            "Already processed.",
            show_alert=True,
        )
        return

    # Refund exactly once.
    conn.execute("""
        UPDATE users
        SET balance = balance + ?
        WHERE user_id = ?
    """, (
        row["amount"],
        row["user_id"],
    ))

    conn.commit()
    conn.close()

    await query.answer(
        "Withdrawal rejected and refunded.",
        show_alert=True,
    )

    try:
        await query.get_bot().send_message(
            chat_id=row["user_id"],
            text=(
                "❌ <b>Withdrawal Rejected</b>\n\n"
                f"💰 Amount: <b>{row['amount']:.2f} ETB</b>\n\n"
                "The amount has been returned to your balance.\n"
                f"📞 Support: {SUPPORT_USERNAME}"
            ),
            parse_mode="HTML",
        )
    except Exception:
        logger.exception("Could not notify user.")

    await admin_withdrawals(query)


# =========================================================
# ADMIN SUSPICIOUS
# =========================================================

async def admin_suspicious(query):
    conn = db_connect()

    rows = conn.execute("""
        SELECT *
        FROM users
        WHERE suspicious = 1
        ORDER BY user_id DESC
        LIMIT 30
    """).fetchall()

    conn.close()

    if not rows:
        await query.edit_message_text(
            "⚠️ <b>Suspicious Accounts</b>\n\n"
            "No suspicious accounts.",
            reply_markup=admin_keyboard(),
            parse_mode="HTML",
        )
        return

    text = "⚠️ <b>Suspicious Accounts</b>\n\n"

    for row in rows:
        text += (
            f"👤 {user_display(row)}\n"
            f"💰 Balance: {row['balance']:.2f} ETB\n"
            f"🆔 ID: <code>{row['user_id']}</code>\n\n"
        )

    await query.edit_message_text(
        text,
        reply_markup=admin_keyboard(),
        parse_mode="HTML",
    )


# =========================================================
# ADMIN ADD TASK
# =========================================================

async def admin_add_task_start(query, context):
    context.user_data["admin_state"] = "task_title"

    await query.edit_message_text(
        "🎯 <b>Add New Task</b>\n\n"
        "Enter task title.\n\n"
        "Example:\n"
        "<code>Join Smart Money Channel</code>",
        reply_markup=admin_cancel_keyboard(),
        parse_mode="HTML",
    )


# =========================================================
# CALLBACKS
# =========================================================

async def callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query

    await query.answer()

    user = update.effective_user
    data = query.data

    create_or_update_user(user)

    # -----------------------------------------------------
    # ADMIN SECURITY
    # -----------------------------------------------------

    admin_callbacks = (
        "admin",
        "admin_stats",
        "admin_reward",
        "admin_withdrawals",
        "admin_referrals",
        "admin_suspicious",
        "admin_cancel",
        "admin_add_task",
    )

    if data.startswith("admin_") or data.startswith("approve_wd_") or data.startswith("reject_wd_"):
        if user.id != ADMIN_ID:
            await query.answer(
                "⛔ Admin only.",
                show_alert=True,
            )
            return

    if data == "admin":
        context.user_data.clear()

        await query.edit_message_text(
            "👑 <b>Global Cash Bot Admin Panel</b>\n\n"
            "Choose an option:",
            reply_markup=admin_keyboard(),
            parse_mode="HTML",
        )
        return

    if data == "admin_stats":
        await admin_stats(query)
        return

    if data == "admin_reward":
        await admin_reward(query, context)
        return

    if data == "admin_withdrawals":
        await admin_withdrawals(query)
        return

    if data == "admin_referrals":
        await admin_referral_list(query)
        return

    if data.startswith("admin_ref_"):
        try:
            referrer_id = int(data.split("_")[-1])
            await admin_referral_detail(
                query,
                referrer_id,
            )
        except ValueError:
            pass

        return

    if data == "admin_suspicious":
        await admin_suspicious(query)
        return

    if data == "admin_cancel":
        context.user_data.clear()

        await query.edit_message_text(
            "❌ Cancelled.",
            reply_markup=admin_keyboard(),
            parse_mode="HTML",
        )
        return

    if data.startswith("admin_wd_"):
        try:
            withdrawal_id = int(data.split("_")[-1])
            await admin_withdrawal_detail(
                query,
                withdrawal_id,
            )
        except ValueError:
            pass

        return

    if data.startswith("approve_wd_"):
        try:
            withdrawal_id = int(data.split("_")[-1])
            await approve_withdrawal(
                query,
                withdrawal_id,
            )
        except ValueError:
            pass

        return

    if data.startswith("reject_wd_"):
        try:
            withdrawal_id = int(data.split("_")[-1])
            await reject_withdrawal(
                query,
                withdrawal_id,
            )
        except ValueError:
            pass

        return

    # -----------------------------------------------------
    # VERIFY
    # -----------------------------------------------------

    if data == "verify":
        missing = await check_channel_membership(
            context.bot,
            user.id,
        )

        if missing:
            set_joined_all(user.id, False)

            names = "\n".join(
                f"• {html.escape(username)}"
                for username, _ in missing
            )

            await query.edit_message_text(
                "❌ <b>Verification Failed</b>\n\n"
                "እነዚህ channels ገና አልተረጋገጡም፦\n\n"
                f"{names}\n\n"
                "እባክዎ በመጀመሪያ ይቀላቀሉ፣ "
                "ከዚያ Verify ይጫኑ።",
                reply_markup=join_keyboard(missing),
                parse_mode="HTML",
            )
            return

        set_joined_all(user.id, True)

        result = process_referral_reward(user.id)

        if result:
            try:
                await context.bot.send_message(
                    chat_id=result["referrer_id"],
                    text=(
                        "🎉 <b>Successful Referral!</b>\n\n"
                        f"💰 Reward: <b>{result['reward']:.2f} ETB</b>\n"
                        "has been added to your balance."
                    ),
                    parse_mode="HTML",
                )
            except Exception:
                logger.exception(
                    "Referral notification failed."
                )

        await query.edit_message_text(
            "✅ <b>Verified Successfully!</b>\n\n"
            "🎉 ሁሉንም required channels በትክክል አረጋግጠዋል።\n\n"
            "ከታች menu ይጠቀሙ።",
            reply_markup=main_menu_keyboard(),
            parse_mode="HTML",
        )

        return

    # -----------------------------------------------------
    # HOME
    # -----------------------------------------------------

    if data == "home":
        context.user_data.clear()

        await show_home(query)
        return

    # -----------------------------------------------------
    # BALANCE
    # -----------------------------------------------------

    if data == "balance":
        await show_balance(
            query,
            user.id,
        )
        return

    # -----------------------------------------------------
    # REFERRAL
    # -----------------------------------------------------

    if data == "referral":
        await show_referral(
            query,
            user.id,
        )
        return

    # -----------------------------------------------------
    # TASKS
    # -----------------------------------------------------

    if data == "tasks":
        await show_tasks(
            query,
            user.id,
        )
        return

    if data.startswith("task_"):
        try:
            task_id = int(data.split("_")[-1])
            await show_task(
                query,
                user.id,
                task_id,
            )
        except ValueError:
            pass

        return

    if data.startswith("verify_task_"):
        try:
            task_id = int(data.split("_")[-1])

            await verify_task(
                query,
                user.id,
                task_id,
            )
        except ValueError:
            pass

        return

    # -----------------------------------------------------
    # WALLET
    # -----------------------------------------------------

    if data == "wallet":
        await show_wallet(
            query,
            user.id,
        )
        return

    if data == "wallet_cbe":
        await ask_wallet_number(
            query,
            context,
            "CBE",
        )
        return

    if data == "wallet_telebirr":
        await ask_wallet_number(
            query,
            context,
            "Telebirr",
        )
        return

    # -----------------------------------------------------
    # WITHDRAW
    # -----------------------------------------------------

    if data == "withdraw":
        await show_withdraw(
            query,
            user.id,
        )
        return

    # -----------------------------------------------------
    # SUPPORT
    # -----------------------------------------------------

    if data == "support":
        await show_support(query)
        return


# =========================================================
# TEXT HANDLER
# =========================================================

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    text = update.message.text.strip()

    create_or_update_user(user)

    # -----------------------------------------------------
    # ADMIN INPUT
    # -----------------------------------------------------

    if user.id == ADMIN_ID:
        state = context.user_data.get("admin_state")

        if state == "reward":
            try:
                amount = float(text)

                if amount <= 0:
                    raise ValueError

                if amount > 100000:
                    raise ValueError

            except ValueError:
                await update.message.reply_text(
                    "❌ Invalid amount.\n\n"
                    "Enter a positive number.\n"
                    "Example: <code>2.00</code>",
                    parse_mode="HTML",
                )
                return

            set_setting(
                "referral_reward",
                f"{amount:.2f}",
            )

            context.user_data.clear()

            await update.message.reply_text(
                "✅ <b>Referral Reward Updated</b>\n\n"
                f"New Reward: <b>{amount:.2f} ETB</b>\n\n"
                "This new amount applies to new successful "
                "referrals only.\n\n"
                "Previously paid referrals remain unchanged.",
                reply_markup=admin_keyboard(),
                parse_mode="HTML",
            )
            return

        if state == "task_title":
            context.user_data["task_title"] = text
            context.user_data["admin_state"] = "task_description"

            await update.message.reply_text(
                "📝 Enter task description.",
                reply_markup=admin_cancel_keyboard(),
            )
            return

        if state == "task_description":
            context.user_data["task_description"] = text
            context.user_data["admin_state"] = "task_channel"

            await update.message.reply_text(
                "📢 Enter channel username.\n\n"
                "Example:\n"
                "<code>@MyChannel</code>",
                reply_markup=admin_cancel_keyboard(),
                parse_mode="HTML",
            )
            return

        if state == "task_channel":
            channel = text

            if not channel.startswith("@"):
                await update.message.reply_text(
                    "❌ Channel username must start with @.\n"
                    "Example: <code>@MyChannel</code>",
                    parse_mode="HTML",
                )
                return

            context.user_data["task_channel"] = channel
            context.user_data["admin_state"] = "task_url"

            await update.message.reply_text(
                "🔗 Enter channel URL.\n\n"
                "Example:\n"
                "<code>https://t.me/MyChannel</code>",
                reply_markup=admin_cancel_keyboard(),
                parse_mode="HTML",
            )
            return

        if state == "task_url":
            if not text.startswith("https://t.me/"):
                await update.message.reply_text(
                    "❌ Invalid Telegram URL.",
                    parse_mode="HTML",
                )
                return

            context.user_data["task_url"] = text
            context.user_data["admin_state"] = "task_reward"

            await update.message.reply_text(
                "💰 Enter task reward in ETB.\n\n"
                "Example: <code>2</code>",
                reply_markup=admin_cancel_keyboard(),
                parse_mode="HTML",
            )
            return

        if state == "task_reward":
            try:
                reward = float(text)

                if reward <= 0:
                    raise ValueError

            except ValueError:
                await update.message.reply_text(
                    "❌ Invalid reward.",
                    parse_mode="HTML",
                )
                return

            title = context.user_data["task_title"]
            description = context.user_data["task_description"]
            channel = context.user_data["task_channel"]
            url = context.user_data["task_url"]

            conn = db_connect()

            conn.execute("""
                INSERT INTO tasks(
                    title,
                    description,
                    channel_username,
                    channel_url,
                    reward,
                    active,
                    created_at
                )
                VALUES(?, ?, ?, ?, ?, 1, ?)
            """, (
                title,
                description,
                channel,
                url,
                reward,
                now(),
            ))

            conn.commit()
            conn.close()

            context.user_data.clear()

            await update.message.reply_text(
                "✅ <b>Task Added Successfully</b>\n\n"
                f"🎯 {html.escape(title)}\n"
                f"💰 Reward: <b>{reward:.2f} ETB</b>",
                reply_markup=admin_keyboard(),
                parse_mode="HTML",
            )
            return

    # -----------------------------------------------------
    # WALLET INPUT
    # -----------------------------------------------------

    wallet_type = context.user_data.get("wallet_type")

    if wallet_type:
        number = re.sub(r"\s+", "", text)

        if wallet_type == "CBE":
            if not re.fullmatch(r"1000\d{9}", number):
                await update.message.reply_text(
                    "❌ Invalid CBE account.\n\n"
                    "CBE must be exactly 13 digits "
                    "and start with 1000.\n\n"
                    "Example:\n"
                    "<code>1000123456789</code>",
                    parse_mode="HTML",
                )
                return

        elif wallet_type == "Telebirr":
            if not re.fullmatch(r"(09|07)\d{8}", number):
                await update.message.reply_text(
                    "❌ Invalid Telebirr number.\n\n"
                    "Telebirr must be exactly 10 digits "
                    "and start with 09 or 07.\n\n"
                    "Example:\n"
                    "<code>0912345678</code>",
                    parse_mode="HTML",
                )
                return

        if wallet_exists_for_other_user(
            user.id,
            wallet_type,
            number,
        ):
            await update.message.reply_text(
                "⚠️ <b>Wallet Already Used</b>\n\n"
                "This wallet is already connected to another account.\n\n"
                "For security, one wallet cannot be used by multiple accounts.",
                parse_mode="HTML",
            )
            return

        success = save_wallet(
            user.id,
            wallet_type,
            number,
        )

        if not success:
            await update.message.reply_text(
                "❌ Could not save this wallet.",
                parse_mode="HTML",
            )
            return

        context.user_data.pop("wallet_type", None)

        await update.message.reply_text(
            "✅ <b>Wallet Saved Successfully</b>\n\n"
            f"🏦 Method: <b>{wallet_type}</b>\n"
            f"🔢 Number: <code>{number}</code>\n\n"
            "ይህ wallet አሁን active wallet ነው።",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "💸 Withdraw",
                        callback_data="withdraw",
                    )
                ],
                [
                    InlineKeyboardButton(
                        "🔙 Back",
                        callback_data="home",
                    )
                ],
            ]),
            parse_mode="HTML",
        )

        return

    # -----------------------------------------------------
    # WITHDRAW INPUT
    # -----------------------------------------------------

    if context.user_data.get("withdraw_mode"):
        context.user_data.pop("withdraw_mode", None)

        raw = text.replace(",", "").strip()

        try:
            amount = float(raw)

        except ValueError:
            await update.message.reply_text(
                "❌ Invalid amount.\n\n"
                "Example: <code>30</code>",
                parse_mode="HTML",
            )
            return

        if amount < MIN_WITHDRAWAL:
            await update.message.reply_text(
                f"❌ Minimum withdrawal is "
                f"<b>{MIN_WITHDRAWAL:.2f} ETB</b>.",
                parse_mode="HTML",
            )
            return

        if amount <= 0:
            await update.message.reply_text(
                "❌ Amount must be greater than zero.",
                parse_mode="HTML",
            )
            return

        wallet = get_wallet(user.id)

        if not wallet:
            await update.message.reply_text(
                "⚠️ Wallet not found. Please save a wallet first.",
                reply_markup=wallet_keyboard(),
                parse_mode="HTML",
            )
            return

        balance = get_balance(user.id)

        if amount > balance:
            await update.message.reply_text(
                "❌ <b>Insufficient Balance</b>\n\n"
                f"Available: <b>{balance:.2f} ETB</b>\n"
                f"Requested: <b>{amount:.2f} ETB</b>",
                parse_mode="HTML",
            )
            return

        success, result = create_withdrawal(
            user.id,
            amount,
        )

        if not success:
            await update.message.reply_text(
                f"❌ {html.escape(str(result))}",
                parse_mode="HTML",
            )
            return

        withdrawal_id = result

        await update.message.reply_text(
            "✅ <b>Withdrawal Submitted</b>\n\n"
            f"🆔 Request: <code>#{withdrawal_id}</code>\n"
            f"💰 Amount: <b>{amount:.2f} ETB</b>\n"
            f"🏦 Method: <b>{html.escape(wallet[0])}</b>\n"
            f"🔢 Wallet: <code>{html.escape(wallet[1])}</code>\n\n"
            "⏳ Status: <b>Pending</b>\n\n"
            "Admin approval እስኪደርስ ይጠብቁ።",
            reply_markup=back_keyboard(),
            parse_mode="HTML",
        )

        # Notify admin
        try:
            await context.bot.send_message(
                chat_id=ADMIN_ID,
                text=(
                    "💳 <b>New Withdrawal</b>\n\n"
                    f"🆔 Request: <code>#{withdrawal_id}</code>\n"
                    f"👤 User ID: <code>{user.id}</code>\n"
                    f"👤 User: {html.escape(user.username or user.first_name or 'Unknown')}\n"
                    f"💰 Amount: <b>{amount:.2f} ETB</b>\n"
                    f"🏦 Method: <b>{html.escape(wallet[0])}</b>\n"
                    f"🔢 Wallet: <code>{html.escape(wallet[1])}</code>\n\n"
                    "⏳ Status: <b>Pending</b>"
                ),
                reply_markup=InlineKeyboardMarkup([
                    [
                        InlineKeyboardButton(
                            "👀 Review",
                            callback_data=f"admin_wd_{withdrawal_id}",
                        )
                    ]
                ]),
                parse_mode="HTML",
            )

        except Exception:
            logger.exception(
                "Could not notify admin about withdrawal."
            )

        return

    # -----------------------------------------------------
    # UNKNOWN TEXT
    # -----------------------------------------------------

    await update.message.reply_text(
        "ℹ️ እባክዎ ከ menu buttons ይጠቀሙ።",
        reply_markup=main_menu_keyboard(),
    )


# =========================================================
# CANCEL
# =========================================================

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()

    await update.message.reply_text(
        "❌ Cancelled.\n\n"
        "ወደ Main Menu ተመልሰዋል።",
        reply_markup=main_menu_keyboard(),
    )


# =========================================================
# ERROR HANDLER
# =========================================================

async def error_handler(update, context):
    logger.exception(
        "Unhandled exception:",
        exc_info=context.error,
    )


# =========================================================
# MAIN
# =========================================================

def main():
    print("========================================")
    print("Global Cash Bot")
    print("Starting...")
    print(f"Admin ID: {ADMIN_ID}")
    print("========================================")

    init_db()

    application = (
        Application.builder()
        .token(BOT_TOKEN)
        .build()
    )

    # Commands
    application.add_handler(
        CommandHandler("start", start)
    )

    application.add_handler(
        CommandHandler("admin", admin_command)
    )

    application.add_handler(
        CommandHandler("cancel", cancel)
    )

    # Callback buttons
    application.add_handler(
        CallbackQueryHandler(callbacks)
    )

    # Normal text
    application.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            handle_text,
        )
    )

    # Errors
    application.add_error_handler(
        error_handler
    )

    print("Bot polling started successfully.")

    application.run_polling(
        drop_pending_updates=True
    )


if __name__ == "__main__":
    main()

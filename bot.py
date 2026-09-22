import os
import sqlite3
import logging
from datetime import datetime, timezone
from html import escape
from urllib.parse import quote

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.constants import ParseMode
from telegram.error import BadRequest
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

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_ID_RAW = os.getenv("ADMIN_ID", "").strip()

BOT_USERNAME = "GloballCashh_Bot"
SUPPORT_USERNAME = "@AmanM_12"

MIN_WITHDRAWAL = 30.0
DB_FILE = "global_cash.db"

REQUIRED_CHANNELS = [
    {
        "username": "@Sheger_tech1",
        "url": "https://t.me/Sheger_tech1",
    },
    {
        "username": "@EthioVortex1",
        "url": "https://t.me/EthioVortex1",
    },
    {
        "username": "@ethiocashflow",
        "url": "https://t.me/ethiocashflow",
    },
    {
        "username": "@AmanIncomeLab",
        "url": "https://t.me/AmanIncomeLab",
    },
    {
        "username": "@OnlineIncomeHub07",
        "url": "https://t.me/OnlineIncomeHub07",
    },
    {
        "username": "@Paymentprooff2",
        "url": "https://t.me/Paymentprooff2",
    },
]


if not BOT_TOKEN:
    raise RuntimeError(
        "BOT_TOKEN is missing. Add BOT_TOKEN in Render Environment Variables."
    )

if not ADMIN_ID_RAW:
    raise RuntimeError(
        "ADMIN_ID is missing. Add ADMIN_ID in Render Environment Variables."
    )

try:
    ADMIN_ID = int(ADMIN_ID_RAW)
except ValueError:
    raise RuntimeError(
        "ADMIN_ID must be a valid Telegram numeric User ID."
    )


# =========================================================
# LOGGING
# =========================================================

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)

logger = logging.getLogger(__name__)


# =========================================================
# DATABASE
# =========================================================

def db():
    conn = sqlite3.connect(
        DB_FILE,
        timeout=30,
        check_same_thread=False,
    )
    conn.row_factory = sqlite3.Row
    return conn


def now():
    return datetime.now(timezone.utc).isoformat()


def init_db():
    conn = db()
    cur = conn.cursor()

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            balance REAL DEFAULT 0,
            referred_by INTEGER,
            referral_paid INTEGER DEFAULT 0,
            referral_status TEXT DEFAULT 'pending',
            joined_all INTEGER DEFAULT 0,
            suspicious INTEGER DEFAULT 0,
            wallet_type TEXT,
            wallet_number TEXT,
            created_at TEXT
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS referrals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            referrer_id INTEGER NOT NULL,
            referred_id INTEGER UNIQUE NOT NULL,
            reward_amount REAL DEFAULT 0,
            status TEXT DEFAULT 'pending',
            created_at TEXT
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS withdrawals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            amount REAL NOT NULL,
            wallet_type TEXT NOT NULL,
            wallet_number TEXT NOT NULL,
            status TEXT DEFAULT 'pending',
            created_at TEXT
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            description TEXT,
            channel_username TEXT,
            channel_url TEXT,
            reward REAL DEFAULT 0,
            active INTEGER DEFAULT 1,
            created_at TEXT
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS user_tasks (
            user_id INTEGER NOT NULL,
            task_id INTEGER NOT NULL,
            verified INTEGER DEFAULT 0,
            paid INTEGER DEFAULT 0,
            paid_at TEXT,
            PRIMARY KEY (user_id, task_id)
        )
        """
    )

    cur.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_unique_wallet
        ON users(wallet_type, wallet_number)
        WHERE wallet_type IS NOT NULL
        AND wallet_number IS NOT NULL
        """
    )

    cur.execute(
        """
        INSERT OR IGNORE INTO settings(key, value)
        VALUES('referral_reward', '2.00')
        """
    )

    conn.commit()
    conn.close()


# =========================================================
# USER FUNCTIONS
# =========================================================

def get_user(user_id):
    conn = db()
    cur = conn.cursor()

    cur.execute(
        "SELECT * FROM users WHERE user_id = ?",
        (user_id,),
    )

    row = cur.fetchone()
    conn.close()

    return row


def create_or_update_user(
    user_id,
    username=None,
    first_name=None,
    referred_by=None,
):
    existing = get_user(user_id)

    conn = db()
    cur = conn.cursor()

    if existing:
        cur.execute(
            """
            UPDATE users
            SET username = ?,
                first_name = ?
            WHERE user_id = ?
            """,
            (
                username,
                first_name,
                user_id,
            ),
        )

    else:
        valid_referrer = None

        if referred_by:
            if referred_by != user_id:
                referrer = get_user(referred_by)
                if referrer:
                    valid_referrer = referred_by

        cur.execute(
            """
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
                created_at
            )
            VALUES(?, ?, ?, 0, ?, 0, 'pending', 0, 0, ?)
            """,
            (
                user_id,
                username,
                first_name,
                valid_referrer,
                now(),
            ),
        )

        if valid_referrer:
            cur.execute(
                """
                INSERT OR IGNORE INTO referrals(
                    referrer_id,
                    referred_id,
                    reward_amount,
                    status,
                    created_at
                )
                VALUES(?, ?, ?, 'pending', ?)
                """,
                (
                    valid_referrer,
                    user_id,
                    get_referral_reward(),
                    now(),
                ),
            )

    conn.commit()
    conn.close()


def set_joined_all(user_id, value=True):
    conn = db()
    cur = conn.cursor()

    cur.execute(
        """
        UPDATE users
        SET joined_all = ?
        WHERE user_id = ?
        """,
        (
            1 if value else 0,
            user_id,
        ),
    )

    conn.commit()
    conn.close()


def add_balance(user_id, amount):
    conn = db()
    cur = conn.cursor()

    cur.execute(
        """
        UPDATE users
        SET balance = balance + ?
        WHERE user_id = ?
        """,
        (
            float(amount),
            user_id,
        ),
    )

    conn.commit()
    conn.close()


def get_balance(user_id):
    user = get_user(user_id)

    if not user:
        return 0.0

    return float(user["balance"] or 0)


def get_wallet(user_id):
    user = get_user(user_id)

    if not user:
        return None, None

    return user["wallet_type"], user["wallet_number"]


def wallet_exists_for_other_user(
    wallet_type,
    wallet_number,
    user_id,
):
    conn = db()
    cur = conn.cursor()

    cur.execute(
        """
        SELECT user_id
        FROM users
        WHERE wallet_type = ?
        AND wallet_number = ?
        AND user_id != ?
        """,
        (
            wallet_type,
            wallet_number,
            user_id,
        ),
    )

    row = cur.fetchone()
    conn.close()

    return row is not None


def set_wallet(user_id, wallet_type, wallet_number):
    conn = db()
    cur = conn.cursor()

    cur.execute(
        """
        UPDATE users
        SET wallet_type = ?,
            wallet_number = ?
        WHERE user_id = ?
        """,
        (
            wallet_type,
            wallet_number,
            user_id,
        ),
    )

    conn.commit()
    conn.close()


# =========================================================
# SETTINGS
# =========================================================

def get_setting(key, default=None):
    conn = db()
    cur = conn.cursor()

    cur.execute(
        "SELECT value FROM settings WHERE key = ?",
        (key,),
    )

    row = cur.fetchone()
    conn.close()

    if row:
        return row["value"]

    return default


def set_setting(key, value):
    conn = db()
    cur = conn.cursor()

    cur.execute(
        """
        INSERT INTO settings(key, value)
        VALUES(?, ?)
        ON CONFLICT(key)
        DO UPDATE SET value = excluded.value
        """,
        (
            key,
            str(value),
        ),
    )

    conn.commit()
    conn.close()


def get_referral_reward():
    try:
        return float(
            get_setting(
                "referral_reward",
                "2.00",
            )
        )
    except Exception:
        return 2.0


# =========================================================
# REFERRALS
# =========================================================

def get_referral_count(user_id):
    conn = db()
    cur = conn.cursor()

    cur.execute(
        """
        SELECT COUNT(*) AS count
        FROM referrals
        WHERE referrer_id = ?
        AND status = 'paid'
        """,
        (user_id,),
    )

    row = cur.fetchone()
    conn.close()

    return int(row["count"] or 0)


def process_referral_reward(user_id):
    user = get_user(user_id)

    if not user:
        return False

    if not user["referred_by"]:
        return False

    if int(user["referral_paid"] or 0) == 1:
        return False

    if int(user["joined_all"] or 0) != 1:
        return False

    referrer_id = int(user["referred_by"])

    if referrer_id == user_id:
        return False

    referrer = get_user(referrer_id)

    if not referrer:
        return False

    reward = get_referral_reward()

    conn = db()
    cur = conn.cursor()

    try:
        cur.execute("BEGIN IMMEDIATE")

        cur.execute(
            """
            SELECT referral_paid, referred_by
            FROM users
            WHERE user_id = ?
            """,
            (user_id,),
        )

        current_user = cur.fetchone()

        if not current_user:
            conn.rollback()
            conn.close()
            return False

        if int(current_user["referral_paid"] or 0) == 1:
            conn.rollback()
            conn.close()
            return False

        if not current_user["referred_by"]:
            conn.rollback()
            conn.close()
            return False

        cur.execute(
            """
            UPDATE users
            SET balance = balance + ?,
                referral_paid = 1,
                referral_status = 'paid'
            WHERE user_id = ?
            """,
            (
                reward,
                referrer_id,
            ),
        )

        cur.execute(
            """
            UPDATE referrals
            SET reward_amount = ?,
                status = 'paid'
            WHERE referred_id = ?
            """,
            (
                reward,
                user_id,
            ),
        )

        cur.execute(
            """
            UPDATE users
            SET referral_paid = 1,
                referral_status = 'paid'
            WHERE user_id = ?
            """,
            (user_id,),
        )

        conn.commit()
        conn.close()

        return True

    except Exception:
        conn.rollback()
        conn.close()
        logger.exception("Referral reward failed.")
        return False


# =========================================================
# TASKS
# =========================================================

def get_active_tasks():
    conn = db()
    cur = conn.cursor()

    cur.execute(
        """
        SELECT *
        FROM tasks
        WHERE active = 1
        ORDER BY id DESC
        """
    )

    rows = cur.fetchall()
    conn.close()

    return rows


def get_task(task_id):
    conn = db()
    cur = conn.cursor()

    cur.execute(
        "SELECT * FROM tasks WHERE id = ?",
        (task_id,),
    )

    row = cur.fetchone()
    conn.close()

    return row


def user_task_paid(user_id, task_id):
    conn = db()
    cur = conn.cursor()

    cur.execute(
        """
        SELECT paid
        FROM user_tasks
        WHERE user_id = ?
        AND task_id = ?
        """,
        (
            user_id,
            task_id,
        ),
    )

    row = cur.fetchone()
    conn.close()

    return bool(row and int(row["paid"]) == 1)


def save_user_task(
    user_id,
    task_id,
    verified,
    paid,
):
    conn = db()
    cur = conn.cursor()

    cur.execute(
        """
        INSERT INTO user_tasks(
            user_id,
            task_id,
            verified,
            paid,
            paid_at
        )
        VALUES(?, ?, ?, ?, ?)
        ON CONFLICT(user_id, task_id)
        DO UPDATE SET
            verified = excluded.verified,
            paid = excluded.paid,
            paid_at = excluded.paid_at
        """,
        (
            user_id,
            task_id,
            1 if verified else 0,
            1 if paid else 0,
            now() if paid else None,
        ),
    )

    conn.commit()
    conn.close()


def create_task(
    title,
    description,
    channel_username,
    channel_url,
    reward,
):
    conn = db()
    cur = conn.cursor()

    cur.execute(
        """
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
        """,
        (
            title,
            description,
            channel_username,
            channel_url,
            reward,
            now(),
        ),
    )

    task_id = cur.lastrowid

    conn.commit()
    conn.close()

    return task_id


# =========================================================
# CHANNEL VERIFICATION
# =========================================================

def is_channel_member_status(status):
    return status in (
        "member",
        "administrator",
        "creator",
    )


async def missing_channels(bot, user_id):
    missing = []

    for channel in REQUIRED_CHANNELS:
        try:
            member = await bot.get_chat_member(
                chat_id=channel["username"],
                user_id=user_id,
            )

            if not is_channel_member_status(member.status):
                missing.append(channel)

        except Exception:
            missing.append(channel)

    return missing


# =========================================================
# SAFE MESSAGE EDIT
# =========================================================

async def safe_edit_message(
    query,
    text,
    reply_markup=None,
    parse_mode=ParseMode.HTML,
):
    try:
        await query.edit_message_text(
            text=text,
            reply_markup=reply_markup,
            parse_mode=parse_mode,
        )
    except BadRequest as e:
        if "Message is not modified" not in str(e):
            logger.warning("Edit message error: %s", e)
    except Exception:
        logger.exception("Could not edit message.")


# =========================================================
# USER KEYBOARDS
# =========================================================

def main_keyboard():
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "💰 Balance",
                    callback_data="balance",
                ),
                InlineKeyboardButton(
                    "👥 Referral",
                    callback_data="referral",
                ),
            ],
            [
                InlineKeyboardButton(
                    "🎯 Tasks",
                    callback_data="tasks",
                ),
                InlineKeyboardButton(
                    "💸 Withdraw",
                    callback_data="withdraw",
                ),
            ],
            [
                InlineKeyboardButton(
                    "👛 Wallet",
                    callback_data="wallet",
                ),
                InlineKeyboardButton(
                    "📞 Support",
                    callback_data="support",
                ),
            ],
        ]
    )


def back_keyboard():
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "🔙 Back",
                    callback_data="home",
                )
            ]
        ]
    )


def referral_keyboard():
    link = f"https://t.me/{BOT_USERNAME}?start=ref"

    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "📤 Share Referral Link",
                    url=(
                        "https://t.me/share/url?url="
                        + quote(link, safe="")
                    ),
                )
            ],
            [
                InlineKeyboardButton(
                    "🔙 Back",
                    callback_data="home",
                )
            ],
        ]
    )


def wallet_keyboard():
    return InlineKeyboardMarkup(
        [
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
        ]
    )


# =========================================================
# ADMIN KEYBOARD
# =========================================================

def admin_keyboard():
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "📊 Statistics",
                    callback_data="admin_stats",
                ),
                InlineKeyboardButton(
                    "💰 Referral Reward",
                    callback_data="admin_reward",
                ),
            ],
            [
                InlineKeyboardButton(
                    "👥 Referrals List",
                    callback_data="admin_referrals",
                ),
                InlineKeyboardButton(
                    "💸 Withdrawals",
                    callback_data="admin_withdrawals",
                ),
            ],
            [
                InlineKeyboardButton(
                    "⚠️ Suspicious Users",
                    callback_data="admin_suspicious",
                ),
                InlineKeyboardButton(
                    "🎯 Add Task",
                    callback_data="admin_add_task",
                ),
            ],
            [
                InlineKeyboardButton(
                    "🧪 Add Test Balance",
                    callback_data="admin_test_balance",
                ),
            ],
        ]
    )


# =========================================================
# JOIN REQUIRED
# =========================================================

async def show_join_required(
    update,
    context,
    missing,
    edit=False,
):
    buttons = []

    for channel in missing:
        buttons.append(
            [
                InlineKeyboardButton(
                    f"📢 Join {channel['username']}",
                    url=channel["url"],
                )
            ]
        )

    buttons.append(
        [
            InlineKeyboardButton(
                "✅ Verify",
                callback_data="verify_channels",
            )
        ]
    )

    text = (
        "🔐 <b>Welcome to Global Cash Bot</b>\n\n"
        "Botን ለመጠቀም ከታች ያሉትን channels "
        "በሙሉ Join ያድርጉ።\n\n"
        f"📌 Remaining: <b>{len(missing)}</b>\n\n"
        "ከጨረሱ በኋላ <b>Verify</b> ይጫኑ።"
    )

    markup = InlineKeyboardMarkup(buttons)

    if edit:
        await safe_edit_message(
            update,
            text,
            reply_markup=markup,
        )
    else:
        await update.message.reply_text(
            text,
            reply_markup=markup,
            parse_mode=ParseMode.HTML,
        )


# =========================================================
# HOME
# =========================================================

async def show_home(
    update,
    context,
    edit=False,
):
    user_id = update.effective_user.id

    user = get_user(user_id)

    if not user:
        create_or_update_user(
            user_id,
            update.effective_user.username,
            update.effective_user.first_name,
        )
        user = get_user(user_id)

    if not int(user["joined_all"] or 0):
        missing = await missing_channels(
            context.bot,
            user_id,
        )

        if missing:
            if edit:
                await show_join_required(
                    update,
                    context,
                    missing,
                    edit=True,
                )
            else:
                await show_join_required(
                    update,
                    context,
                    missing,
                    edit=False,
                )
            return

        set_joined_all(user_id, True)

        process_referral_reward(user_id)

    text = (
        "💸 <b>Global Cash Bot</b>\n\n"
        f"👋 Welcome, <b>{escape(update.effective_user.first_name or 'User')}</b>!\n\n"
        "Earn rewards, complete tasks, invite friends "
        "and withdraw your balance.\n\n"
        "👇 Choose an option:"
    )

    if edit:
        await safe_edit_message(
            update,
            text,
            reply_markup=main_keyboard(),
        )
    else:
        await update.message.reply_text(
            text,
            reply_markup=main_keyboard(),
            parse_mode=ParseMode.HTML,
        )


# =========================================================
# BALANCE
# =========================================================

async def show_balance(query):
    user_id = query.from_user.id

    balance = get_balance(user_id)

    await safe_edit_message(
        query,
        (
            "💰 <b>Your Balance</b>\n\n"
            f"💵 Balance: <b>{balance:.2f} ETB</b>\n\n"
            f"💸 Minimum withdrawal: <b>{MIN_WITHDRAWAL:.2f} ETB</b>"
        ),
        reply_markup=back_keyboard(),
    )


# =========================================================
# REFERRAL
# =========================================================

async def show_referral(query):
    user_id = query.from_user.id

    count = get_referral_count(user_id)
    reward = get_referral_reward()

    link = f"https://t.me/{BOT_USERNAME}?start={user_id}"

    await safe_edit_message(
        query,
        (
            "👥 <b>Referral Program</b>\n\n"
            f"👤 Successful Referrals: <b>{count}</b>\n"
            f"🎁 Reward per referral: <b>{reward:.2f} ETB</b>\n\n"
            "📌 Your referral link:\n"
            f"<code>{link}</code>\n\n"
            "የምታጋብዘው ሰው botን /start አድርጎ "
            "required channels በሙሉ join ካደረገ በኋላ "
            "referral reward ይቆጠራል።"
        ),
        reply_markup=referral_keyboard(),
    )


# =========================================================
# TASKS
# =========================================================

async def show_tasks(query):
    tasks = get_active_tasks()

    if not tasks:
        await safe_edit_message(
            query,
            (
                "🎯 <b>Tasks</b>\n\n"
                "አሁን ላይ active task የለም።\n"
                "አዲስ task ሲጨመር እዚህ ይታያል።"
            ),
            reply_markup=back_keyboard(),
        )
        return

    buttons = []

    for task in tasks:
        paid = user_task_paid(
            query.from_user.id,
            task["id"],
        )

        label = (
            f"✅ {task['title']}"
            if paid
            else f"🎯 {task['title']}"
        )

        buttons.append(
            [
                InlineKeyboardButton(
                    label,
                    callback_data=f"task_{task['id']}",
                )
            ]
        )

    buttons.append(
        [
            InlineKeyboardButton(
                "🔙 Back",
                callback_data="home",
            )
        ]
    )

    await safe_edit_message(
        query,
        (
            "🎯 <b>Available Tasks</b>\n\n"
            "Task ይምረጡ እና የተጠየቀውን "
            "ሂደት ይጨርሱ።"
        ),
        reply_markup=InlineKeyboardMarkup(buttons),
    )


async def show_task(query, task_id):
    task = get_task(task_id)

    if not task or not int(task["active"]):
        await query.answer(
            "Task not available.",
            show_alert=True,
        )
        return

    if user_task_paid(
        query.from_user.id,
        task_id,
    ):
        await safe_edit_message(
            query,
            (
                "🎯 <b>Task Completed</b>\n\n"
                f"📌 {escape(task['title'])}\n\n"
                "ይህን task አስቀድመው ጨርሰዋል።"
            ),
            reply_markup=InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            "🔙 Tasks",
                            callback_data="tasks",
                        )
                    ]
                ]
            ),
        )
        return

    buttons = []

    if task["channel_url"]:
        buttons.append(
            [
                InlineKeyboardButton(
                    "📢 Open Channel",
                    url=task["channel_url"],
                )
            ]
        )

    buttons.append(
        [
            InlineKeyboardButton(
                "✅ Verify Task",
                callback_data=f"verify_task_{task_id}",
            )
        ]
    )

    buttons.append(
        [
            InlineKeyboardButton(
                "🔙 Tasks",
                callback_data="tasks",
            )
        ]
    )

    await safe_edit_message(
        query,
        (
            f"🎯 <b>{escape(task['title'])}</b>\n\n"
            f"{escape(task['description'] or '')}\n\n"
            f"🎁 Reward: <b>{float(task['reward']):.2f} ETB</b>\n\n"
            "Channelን Join ካደረጉ በኋላ "
            "<b>Verify Task</b> ይጫኑ።"
        ),
        reply_markup=InlineKeyboardMarkup(buttons),
    )


async def verify_task(query, task_id, context):
    task = get_task(task_id)

    if not task or not int(task["active"]):
        await query.answer(
            "Task not available.",
            show_alert=True,
        )
        return

    user_id = query.from_user.id

    if user_task_paid(user_id, task_id):
        await query.answer(
            "Already completed.",
            show_alert=True,
        )
        return

    if not task["channel_username"]:
        await query.answer(
            "Task channel is not configured.",
            show_alert=True,
        )
        return

    try:
        member = await context.bot.get_chat_member(
            chat_id=task["channel_username"],
            user_id=user_id,
        )

        if not is_channel_member_status(member.status):
            await query.answer(
                "❌ Please join the channel first.",
                show_alert=True,
            )
            return

    except Exception:
        await query.answer(
            "❌ Could not verify membership.",
            show_alert=True,
        )
        return

    reward = float(task["reward"])

    save_user_task(
        user_id,
        task_id,
        verified=True,
        paid=True,
    )

    add_balance(
        user_id,
        reward,
    )

    await query.answer(
        f"✅ +{reward:.2f} ETB added!",
        show_alert=True,
    )

    await show_tasks(query)


# =========================================================
# WALLET
# =========================================================

async def show_wallet(query):
    wallet_type, wallet_number = get_wallet(
        query.from_user.id
    )

    if wallet_type and wallet_number:
        await safe_edit_message(
            query,
            (
                "👛 <b>Your Wallet</b>\n\n"
                f"💳 Type: <b>{escape(wallet_type)}</b>\n"
                f"🔢 Number: <code>{escape(wallet_number)}</code>\n\n"
                "Wallet ንለመቀየር ከታች ይምረጡ።"
            ),
            reply_markup=wallet_keyboard(),
        )
        return

    await safe_edit_message(
        query,
        (
            "👛 <b>Wallet</b>\n\n"
            "ገንዘብ ለመቀበል CBE ወይም Telebirr "
            "wallet ያስገቡ።\n\n"
            "👇 Wallet type ይምረጡ።"
        ),
        reply_markup=wallet_keyboard(),
    )


async def ask_wallet(query, wallet_type, context):
    context.user_data.clear()

    context.user_data["wallet_step"] = "number"
    context.user_data["wallet_type"] = wallet_type

    if wallet_type == "CBE":
        text = (
            "🏦 <b>CBE Wallet</b>\n\n"
            "13 digits ያለው እና <b>1000</b> በሚል "
            "የሚጀምር CBE account number ያስገቡ።\n\n"
            "Example:\n"
            "<code>1000123456789</code>"
        )
    else:
        text = (
            "📱 <b>Telebirr Wallet</b>\n\n"
            "10 digits ያለው እና <b>09</b> ወይም <b>07</b> "
            "በሚል የሚጀምር number ያስገቡ።\n\n"
            "Example:\n"
            "<code>0912345678</code>"
        )

    await safe_edit_message(
        query,
        text,
        reply_markup=InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "❌ Cancel",
                        callback_data="wallet",
                    )
                ]
            ]
        ),
    )


async def save_wallet_from_input(
    update,
    context,
):
    user_id = update.effective_user.id

    wallet_type = context.user_data.get(
        "wallet_type"
    )

    text = update.message.text.strip()

    if wallet_type == "CBE":
        if (
            len(text) != 13
            or not text.isdigit()
            or not text.startswith("1000")
        ):
            await update.message.reply_text(
                (
                    "❌ <b>Invalid CBE Number</b>\n\n"
                    "CBE number 13 digits መሆን አለበት "
                    "እና 1000 በሚል መጀመር አለበት።\n\n"
                    "Example:\n"
                    "<code>1000123456789</code>"
                ),
                parse_mode=ParseMode.HTML,
            )
            return

    elif wallet_type == "Telebirr":
        if (
            len(text) != 10
            or not text.isdigit()
            or not (
                text.startswith("09")
                or text.startswith("07")
            )
        ):
            await update.message.reply_text(
                (
                    "❌ <b>Invalid Telebirr Number</b>\n\n"
                    "Telebirr number 10 digits መሆን አለበት "
                    "እና 09 ወይም 07 በሚል መጀመር አለበት።\n\n"
                    "Example:\n"
                    "<code>0912345678</code>"
                ),
                parse_mode=ParseMode.HTML,
            )
            return

    else:
        context.user_data.clear()

        await update.message.reply_text(
            "❌ Wallet session expired.",
            reply_markup=main_keyboard(),
        )
        return

    if wallet_exists_for_other_user(
        wallet_type,
        text,
        user_id,
    ):
        await update.message.reply_text(
            (
                "❌ <b>Wallet Already Used</b>\n\n"
                "ይህ wallet ቁጥር በሌላ user ተመዝግቧል።"
            ),
            parse_mode=ParseMode.HTML,
        )
        return

    set_wallet(
        user_id,
        wallet_type,
        text,
    )

    context.user_data.clear()

    await update.message.reply_text(
        (
            "✅ <b>Wallet Saved Successfully!</b>\n\n"
            f"💳 Type: <b>{escape(wallet_type)}</b>\n"
            f"🔢 Number: <code>{escape(text)}</code>"
        ),
        reply_markup=main_keyboard(),
        parse_mode=ParseMode.HTML,
    )


# =========================================================
# WITHDRAW
# =========================================================

async def show_withdraw(query):
    user_id = query.from_user.id

    balance = get_balance(user_id)
    wallet_type, wallet_number = get_wallet(user_id)

    if not wallet_type or not wallet_number:
        await safe_edit_message(
            query,
            (
                "💸 <b>Withdraw</b>\n\n"
                "ከመውጣት በፊት Wallet ማስገባት አለብዎት።\n\n"
                "CBE ወይም Telebirr ይምረጡ።"
            ),
            reply_markup=InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            "👛 Set Wallet",
                            callback_data="wallet",
                        )
                    ],
                    [
                        InlineKeyboardButton(
                            "🔙 Back",
                            callback_data="home",
                        )
                    ],
                ]
            ),
        )
        return

    if balance < MIN_WITHDRAWAL:
        await safe_edit_message(
            query,
            (
                "💸 <b>Withdraw</b>\n\n"
                f"💰 Current Balance: <b>{balance:.2f} ETB</b>\n"
                f"📌 Minimum: <b>{MIN_WITHDRAWAL:.2f} ETB</b>\n\n"
                "Minimum withdrawal ላይ ለመድረስ "
                "ተጨማሪ balance ያስፈልግዎታል።"
            ),
            reply_markup=back_keyboard(),
        )
        return

    context_text = (
        "💸 <b>Withdraw</b>\n\n"
        f"💰 Available Balance: <b>{balance:.2f} ETB</b>\n"
        f"💳 Wallet: <b>{escape(wallet_type)}</b>\n"
        f"🔢 Number: <code>{escape(wallet_number)}</code>\n\n"
        f"Minimum withdrawal: <b>{MIN_WITHDRAWAL:.2f} ETB</b>\n\n"
        "የሚያወጡትን amount ያስገቡ።\n"
        "Example: <code>30</code>"
    )

    context.user_data["withdraw_step"] = "amount"

    await safe_edit_message(
        query,
        context_text,
        reply_markup=InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "❌ Cancel",
                        callback_data="home",
                    )
                ]
            ]
        ),
    )


async def handle_withdraw_amount_input(
    update,
    context,
):
    user_id = update.effective_user.id

    text = update.message.text.strip()

    try:
        amount = float(text)

        if amount <= 0:
            raise ValueError

    except ValueError:
        await update.message.reply_text(
            (
                "❌ <b>Invalid Amount</b>\n\n"
                "ትክክለኛ amount ያስገቡ።\n\n"
                "Example:\n"
                "<code>30</code>"
            ),
            parse_mode=ParseMode.HTML,
        )
        return

    balance = get_balance(user_id)

    if amount < MIN_WITHDRAWAL:
        await update.message.reply_text(
            (
                "❌ <b>Amount Too Low</b>\n\n"
                f"Minimum withdrawal: <b>{MIN_WITHDRAWAL:.2f} ETB</b>"
            ),
            parse_mode=ParseMode.HTML,
        )
        return

    if amount > balance:
        await update.message.reply_text(
            (
                "❌ <b>Insufficient Balance</b>\n\n"
                f"Your balance: <b>{balance:.2f} ETB</b>"
            ),
            parse_mode=ParseMode.HTML,
        )
        return

    wallet_type, wallet_number = get_wallet(
        user_id
    )

    if not wallet_type or not wallet_number:
        context.user_data.clear()

        await update.message.reply_text(
            "❌ Please set your wallet first.",
            reply_markup=main_keyboard(),
        )
        return

    context.user_data["withdraw_amount"] = amount
    context.user_data["withdraw_step"] = "confirm"

    await update.message.reply_text(
        (
            "💸 <b>Confirm Withdrawal</b>\n\n"
            f"💰 Amount: <b>{amount:.2f} ETB</b>\n"
            f"💳 Wallet: <b>{escape(wallet_type)}</b>\n"
            f"🔢 Number: <code>{escape(wallet_number)}</code>\n\n"
            "Withdrawal request ለመላክ Confirm ይጫኑ።"
        ),
        reply_markup=InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "✅ Confirm",
                        callback_data="confirm_withdrawal",
                    )
                ],
                [
                    InlineKeyboardButton(
                        "❌ Cancel",
                        callback_data="home",
                    )
                ],
            ]
        ),
        parse_mode=ParseMode.HTML,
    )


async def confirm_withdrawal_process(
    query,
    context,
):
    user_id = query.from_user.id

    amount = context.user_data.get(
        "withdraw_amount"
    )

    if not amount:
        await query.answer(
            "Withdrawal session expired.",
            show_alert=True,
        )
        context.user_data.clear()
        return

    try:
        amount = float(amount)
    except Exception:
        context.user_data.clear()
        await query.answer(
            "Invalid withdrawal amount.",
            show_alert=True,
        )
        return

    user = get_user(user_id)

    if not user:
        context.user_data.clear()
        await query.answer(
            "User not found.",
            show_alert=True,
        )
        return

    balance = float(user["balance"] or 0)

    if amount < MIN_WITHDRAWAL:
        context.user_data.clear()
        await query.answer(
            "Amount is below minimum withdrawal.",
            show_alert=True,
        )
        return

    if amount > balance:
        context.user_data.clear()
        await query.answer(
            "Insufficient balance.",
            show_alert=True,
        )
        return

    wallet_type, wallet_number = get_wallet(
        user_id
    )

    if not wallet_type or not wallet_number:
        context.user_data.clear()
        await query.answer(
            "Wallet not found.",
            show_alert=True,
        )
        return

    conn = db()
    cur = conn.cursor()

    try:
        cur.execute("BEGIN IMMEDIATE")

        cur.execute(
            """
            SELECT balance
            FROM users
            WHERE user_id = ?
            """,
            (user_id,),
        )

        current = cur.fetchone()

        if not current:
            conn.rollback()
            conn.close()

            await query.answer(
                "User not found.",
                show_alert=True,
            )
            return

        current_balance = float(
            current["balance"] or 0
        )

        if amount > current_balance:
            conn.rollback()
            conn.close()

            await query.answer(
                "Insufficient balance.",
                show_alert=True,
            )
            return

        cur.execute(
            """
            UPDATE users
            SET balance = balance - ?
            WHERE user_id = ?
            """,
            (
                amount,
                user_id,
            ),
        )

        cur.execute(
            """
            INSERT INTO withdrawals(
                user_id,
                amount,
                wallet_type,
                wallet_number,
                status,
                created_at
            )
            VALUES(?, ?, ?, ?, 'pending', ?)
            """,
            (
                user_id,
                amount,
                wallet_type,
                wallet_number,
                now(),
            ),
        )

        withdrawal_id = cur.lastrowid

        conn.commit()
        conn.close()

    except Exception:
        conn.rollback()
        conn.close()

        logger.exception(
            "Withdrawal creation failed."
        )

        await query.answer(
            "Could not create withdrawal request.",
            show_alert=True,
        )
        return

    context.user_data.clear()

    await query.answer(
        "Withdrawal request submitted.",
        show_alert=True,
    )

    await safe_edit_message(
        query,
        (
            "✅ <b>Withdrawal Request Sent</b>\n\n"
            f"🆔 Request ID: <code>#{withdrawal_id}</code>\n"
            f"💰 Amount: <b>{amount:.2f} ETB</b>\n"
            f"💳 Wallet: <b>{escape(wallet_type)}</b>\n"
            f"🔢 Number: <code>{escape(wallet_number)}</code>\n\n"
            "Admin ካጸደቀው በኋላ payment ይፈጸማል።"
        ),
        reply_markup=back_keyboard(),
    )

    # Notify admin
    try:
        await context.bot.send_message(
            chat_id=ADMIN_ID,
            text=(
                "💸 <b>New Withdrawal Request</b>\n\n"
                f"🆔 Request ID: <code>#{withdrawal_id}</code>\n"
                f"👤 User ID: <code>{user_id}</code>\n"
                f"💰 Amount: <b>{amount:.2f} ETB</b>\n"
                f"💳 Wallet: <b>{escape(wallet_type)}</b>\n"
                f"🔢 Number: <code>{escape(wallet_number)}</code>"
            ),
            reply_markup=InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            "✅ Approve",
                            callback_data=(
                                f"approve_withdrawal_{withdrawal_id}"
                            ),
                        ),
                        InlineKeyboardButton(
                            "❌ Reject",
                            callback_data=(
                                f"reject_withdrawal_{withdrawal_id}"
                            ),
                        ),
                    ]
                ]
            ),
            parse_mode=ParseMode.HTML,
        )

    except Exception:
        logger.exception(
            "Could not notify admin."
        )


# =========================================================
# SUPPORT
# =========================================================

async def show_support(query):
    await safe_edit_message(
        query,
        (
            "📞 <b>Support & Promotion</b>\n\n"
            f"👨‍💻 Support: <b>{SUPPORT_USERNAME}</b>\n\n"
            "ለBot እገዛ፣ ለadvertisement፣ ለchannel/group "
            "promotion ወይም ለbusiness promotion "
            "support ላይ ያነጋግሩን።\n\n"
            "💼 Channel Promotion\n"
            "📢 Advertisement\n"
            "🚀 Business Promotion"
        ),
        reply_markup=InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "📞 Contact Support",
                        url=(
                            "https://t.me/"
                            + SUPPORT_USERNAME.lstrip("@")
                        ),
                    )
                ],
                [
                    InlineKeyboardButton(
                        "🔙 Back",
                        callback_data="home",
                    )
                ],
            ]
        ),
    )


# =========================================================
# ADMIN
# =========================================================

def is_admin(user_id):
    return int(user_id) == int(ADMIN_ID)


async def show_admin(query):
    if not is_admin(query.from_user.id):
        await query.answer(
            "❌ Admin only.",
            show_alert=True,
        )
        return

    await query.edit_message_text(
        (
            "🛠 <b>Global Cash Bot Admin Panel</b>\n\n"
            "ከታች ያለውን option ይምረጡ።"
        ),
        reply_markup=admin_keyboard(),
        parse_mode=ParseMode.HTML,
    )


async def show_admin_stats(query):
    if not is_admin(query.from_user.id):
        await query.answer(
            "Admin only.",
            show_alert=True,
        )
        return

    conn = db()
    cur = conn.cursor()

    cur.execute(
        "SELECT COUNT(*) AS c FROM users"
    )
    total_users = cur.fetchone()["c"]

    cur.execute(
        """
        SELECT COUNT(*) AS c
        FROM users
        WHERE joined_all = 1
        """
    )
    verified_users = cur.fetchone()["c"]

    cur.execute(
        """
        SELECT COUNT(*) AS c
        FROM withdrawals
        WHERE status = 'pending'
        """
    )
    pending_withdrawals = cur.fetchone()["c"]

    cur.execute(
        """
        SELECT COUNT(*) AS c
        FROM withdrawals
        WHERE status = 'approved'
        """
    )
    approved_withdrawals = cur.fetchone()["c"]

    cur.execute(
        """
        SELECT COALESCE(SUM(balance), 0) AS total
        FROM users
        """
    )
    total_balance = float(
        cur.fetchone()["total"] or 0
    )

    conn.close()

    await safe_edit_message(
        query,
        (
            "📊 <b>Bot Statistics</b>\n\n"
            f"👥 Total Users: <b>{total_users}</b>\n"
            f"✅ Verified Users: <b>{verified_users}</b>\n"
            f"💰 Total User Balance: <b>{total_balance:.2f} ETB</b>\n"
            f"⏳ Pending Withdrawals: <b>{pending_withdrawals}</b>\n"
            f"✅ Approved Withdrawals: <b>{approved_withdrawals}</b>"
        ),
        reply_markup=InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "🔙 Admin Panel",
                        callback_data="admin",
                    )
                ]
            ]
        ),
    )


async def show_admin_reward(query):
    if not is_admin(query.from_user.id):
        await query.answer(
            "Admin only.",
            show_alert=True,
        )
        return

    reward = get_referral_reward()

    await safe_edit_message(
        query,
        (
            "💰 <b>Referral Reward</b>\n\n"
            f"Current reward: <b>{reward:.2f} ETB</b>\n\n"
            "አዲስ referral reward ለማስገባት "
            "ከታች ያለውን button ይጫኑ።"
        ),
        reply_markup=InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "✏️ Change Reward",
                        callback_data="change_reward",
                    )
                ],
                [
                    InlineKeyboardButton(
                        "🔙 Admin Panel",
                        callback_data="admin",
                    )
                ],
            ]
        ),
    )


async def start_change_reward(query, context):
    if not is_admin(query.from_user.id):
        return

    context.user_data.clear()
    context.user_data["admin_step"] = "reward"

    await safe_edit_message(
        query,
        (
            "💰 <b>Change Referral Reward</b>\n\n"
            "አዲሱን reward በETB ያስገቡ።\n\n"
            "Example:\n"
            "<code>2</code>"
        ),
        reply_markup=InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "❌ Cancel",
                        callback_data="admin",
                    )
                ]
            ]
        ),
    )


# =========================================================
# ADMIN TEST BALANCE
# =========================================================

async def start_test_balance(query, context):
    if not is_admin(query.from_user.id):
        await query.answer(
            "❌ Admin only.",
            show_alert=True,
        )
        return

    context.user_data.clear()

    context.user_data["admin_step"] = (
        "test_balance_user"
    )

    await safe_edit_message(
        query,
        (
            "🧪 <b>Add Test Balance</b>\n\n"
            "ይህ feature ለBot ሙከራ ብቻ ነው።\n\n"
            "<b>Step 1/2</b>\n"
            "የሚሰጠውን Telegram User ID ያስገቡ።\n\n"
            "ማንኛውንም registered User ID መጠቀም ይችላሉ።\n\n"
            "Example:\n"
            "<code>8727153413</code>"
        ),
        reply_markup=InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "❌ Cancel",
                        callback_data="admin",
                    )
                ]
            ]
        ),
    )


async def handle_admin_test_balance(
    update,
    context,
    text,
):
    if not is_admin(update.effective_user.id):
        return False

    admin_step = context.user_data.get(
        "admin_step"
    )

    # -----------------------------------------------------
    # STEP 1 - USER ID
    # -----------------------------------------------------

    if admin_step == "test_balance_user":

        try:
            target_user_id = int(text)

            if target_user_id <= 0:
                raise ValueError

        except ValueError:
            await update.message.reply_text(
                (
                    "❌ <b>Invalid User ID</b>\n\n"
                    "ትክክለኛ Telegram numeric User ID "
                    "ያስገቡ።\n\n"
                    "Example:\n"
                    "<code>8727153413</code>"
                ),
                parse_mode=ParseMode.HTML,
            )
            return True

        target_user = get_user(target_user_id)

        if not target_user:
            await update.message.reply_text(
                (
                    "❌ <b>User Not Found</b>\n\n"
                    f"User ID <code>{target_user_id}</code> "
                    "በbot database ውስጥ አልተገኘም።\n\n"
                    "ተጠቃሚው መጀመሪያ botን "
                    "<b>/start</b> ብሎ መጀመር አለበት።"
                ),
                parse_mode=ParseMode.HTML,
            )
            return True

        context.user_data[
            "test_balance_user_id"
        ] = target_user_id

        context.user_data["admin_step"] = (
            "test_balance_amount"
        )

        current_balance = float(
            target_user["balance"] or 0
        )

        await update.message.reply_text(
            (
                "🧪 <b>Add Test Balance</b>\n\n"
                f"👤 User ID: <code>{target_user_id}</code>\n"
                f"💰 Current Balance: "
                f"<b>{current_balance:.2f} ETB</b>\n\n"
                "<b>Step 2/2</b>\n"
                "ምን ያህል ETB መጨመር እንደሚፈልጉ "
                "ያስገቡ።\n\n"
                "Example:\n"
                "<code>100</code>"
            ),
            parse_mode=ParseMode.HTML,
        )

        return True

    # -----------------------------------------------------
    # STEP 2 - AMOUNT
    # -----------------------------------------------------

    if admin_step == "test_balance_amount":

        try:
            amount = float(text)

            if amount <= 0:
                raise ValueError

        except ValueError:
            await update.message.reply_text(
                (
                    "❌ <b>Invalid Amount</b>\n\n"
                    "ከ 0 በላይ የሆነ amount ያስገቡ።\n\n"
                    "Example:\n"
                    "<code>100</code>"
                ),
                parse_mode=ParseMode.HTML,
            )
            return True

        target_user_id = context.user_data.get(
            "test_balance_user_id"
        )

        if not target_user_id:
            context.user_data.clear()

            await update.message.reply_text(
                (
                    "❌ Test balance session expired.\n\n"
                    "እንደገና ከAdmin Panel ይጀምሩ።"
                ),
                reply_markup=InlineKeyboardMarkup(
                    [
                        [
                            InlineKeyboardButton(
                                "🛠 Admin Panel",
                                callback_data="admin",
                            )
                        ]
                    ]
                ),
            )
            return True

        target_user = get_user(target_user_id)

        if not target_user:
            context.user_data.clear()

            await update.message.reply_text(
                "❌ User not found.",
                reply_markup=InlineKeyboardMarkup(
                    [
                        [
                            InlineKeyboardButton(
                                "🛠 Admin Panel",
                                callback_data="admin",
                            )
                        ]
                    ]
                ),
            )
            return True

        old_balance = float(
            target_user["balance"] or 0
        )

        add_balance(
            target_user_id,
            amount,
        )

        new_balance = old_balance + amount

        context.user_data.clear()

        await update.message.reply_text(
            (
                "✅ <b>Test Balance Added Successfully!</b>\n\n"
                f"👤 User ID: <code>{target_user_id}</code>\n"
                f"💰 Added: <b>+{amount:.2f} ETB</b>\n"
                f"💵 Old Balance: <b>{old_balance:.2f} ETB</b>\n"
                f"💵 New Balance: <b>{new_balance:.2f} ETB</b>\n\n"
                "🧪 <b>TEST ONLY</b>\n"
                "ይህ በBot database ውስጥ የተጨመረ "
                "test balance ነው።\n\n"
                "⚠️ በCBE ወይም Telebirr ገንዘብ "
                "አልተላከም።"
            ),
            reply_markup=InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            "🛠 Admin Panel",
                            callback_data="admin",
                        )
                    ]
                ]
            ),
            parse_mode=ParseMode.HTML,
        )

        # Notify the target user if possible
        try:
            await context.bot.send_message(
                chat_id=target_user_id,
                text=(
                    "🧪 <b>Test Balance Added</b>\n\n"
                    f"💰 Added: <b>+{amount:.2f} ETB</b>\n"
                    f"💵 New Balance: "
                    f"<b>{new_balance:.2f} ETB</b>\n\n"
                    "ይህ ለBot ሙከራ ብቻ የተጨመረ "
                    "Test Balance ነው።"
                ),
                parse_mode=ParseMode.HTML,
            )

        except Exception:
            logger.exception(
                "Could not notify test user."
            )

        return True

    return False


# =========================================================
# ADMIN REFERRALS
# =========================================================

async def show_admin_referrals(query):
    if not is_admin(query.from_user.id):
        await query.answer(
            "Admin only.",
            show_alert=True,
        )
        return

    conn = db()
    cur = conn.cursor()

    cur.execute(
        """
        SELECT
            r.id,
            r.referrer_id,
            r.referred_id,
            r.reward_amount,
            r.status,
            r.created_at
        FROM referrals r
        ORDER BY r.id DESC
        LIMIT 20
        """
    )

    rows = cur.fetchall()
    conn.close()

    if not rows:
        text = (
            "👥 <b>Referrals</b>\n\n"
            "No referrals found."
        )

    else:
        lines = [
            "👥 <b>Recent Referrals</b>\n"
        ]

        for row in rows:
            lines.append(
                (
                    f"#{row['id']} | "
                    f"{row['referrer_id']} → "
                    f"{row['referred_id']} | "
                    f"{row['status']} | "
                    f"{float(row['reward_amount'] or 0):.2f} ETB"
                )
            )

        text = "\n".join(lines)

    await safe_edit_message(
        query,
        text,
        reply_markup=InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "🔍 Search Referral",
                        callback_data="admin_referral_lookup",
                    )
                ],
                [
                    InlineKeyboardButton(
                        "🔙 Admin Panel",
                        callback_data="admin",
                    )
                ],
            ]
        ),
    )


async def start_admin_referral_lookup(
    query,
    context,
):
    if not is_admin(query.from_user.id):
        return

    context.user_data.clear()
    context.user_data["admin_step"] = (
        "referral_lookup"
    )

    await safe_edit_message(
        query,
        (
            "🔍 <b>Referral Lookup</b>\n\n"
            "Referrer User ID ወይም Referred User ID "
            "ያስገቡ።\n\n"
            "Example:\n"
            "<code>8727153413</code>"
        ),
        reply_markup=InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "❌ Cancel",
                        callback_data="admin",
                    )
                ]
            ]
        ),
    )


async def show_referral_details_for_message(
    update,
    user_id,
):
    conn = db()
    cur = conn.cursor()

    cur.execute(
        """
        SELECT *
        FROM referrals
        WHERE referrer_id = ?
        OR referred_id = ?
        ORDER BY id DESC
        LIMIT 20
        """,
        (
            user_id,
            user_id,
        ),
    )

    rows = cur.fetchall()
    conn.close()

    if not rows:
        await update.message.reply_text(
            (
                "👥 <b>No referral records found.</b>\n\n"
                f"User ID: <code>{user_id}</code>"
            ),
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            "🛠 Admin Panel",
                            callback_data="admin",
                        )
                    ]
                ]
            ),
        )
        return

    lines = [
        "👥 <b>Referral Details</b>\n",
        f"🔎 User ID: <code>{user_id}</code>\n",
    ]

    for row in rows:
        lines.append(
            (
                f"#{row['id']} | "
                f"{row['referrer_id']} → "
                f"{row['referred_id']}\n"
                f"🎁 {float(row['reward_amount'] or 0):.2f} ETB | "
                f"Status: <b>{row['status']}</b>\n"
            )
        )

    await update.message.reply_text(
        "\n".join(lines),
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "🛠 Admin Panel",
                        callback_data="admin",
                    )
                ]
            ]
        ),
    )


# =========================================================
# ADMIN WITHDRAWALS
# =========================================================

async def show_admin_withdrawals(query):
    if not is_admin(query.from_user.id):
        await query.answer(
            "Admin only.",
            show_alert=True,
        )
        return

    conn = db()
    cur = conn.cursor()

    cur.execute(
        """
        SELECT *
        FROM withdrawals
        ORDER BY id DESC
        LIMIT 20
        """
    )

    rows = cur.fetchall()
    conn.close()

    if not rows:
        text = (
            "💸 <b>Withdrawals</b>\n\n"
            "No withdrawal requests."
        )

        markup = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "🔙 Admin Panel",
                        callback_data="admin",
                    )
                ]
            ]
        )

    else:
        lines = [
            "💸 <b>Recent Withdrawals</b>\n"
        ]

        buttons = []

        for row in rows:
            lines.append(
                (
                    f"#{row['id']} | "
                    f"User: {row['user_id']} | "
                    f"{float(row['amount']):.2f} ETB | "
                    f"{row['status']}"
                )
            )

            if row["status"] == "pending":
                buttons.append(
                    [
                        InlineKeyboardButton(
                            f"✅ Approve #{row['id']}",
                            callback_data=(
                                f"approve_withdrawal_{row['id']}"
                            ),
                        ),
                        InlineKeyboardButton(
                            f"❌ Reject #{row['id']}",
                            callback_data=(
                                f"reject_withdrawal_{row['id']}"
                            ),
                        ),
                    ]
                )

        buttons.append(
            [
                InlineKeyboardButton(
                    "🔙 Admin Panel",
                    callback_data="admin",
                )
            ]
        )

        text = "\n".join(lines)
        markup = InlineKeyboardMarkup(buttons)

    await safe_edit_message(
        query,
        text,
        reply_markup=markup,
    )


async def approve_withdrawal(
    query,
    context,
    withdrawal_id,
):
    if not is_admin(query.from_user.id):
        await query.answer(
            "Admin only.",
            show_alert=True,
        )
        return

    conn = db()
    cur = conn.cursor()

    cur.execute(
        """
        SELECT *
        FROM withdrawals
        WHERE id = ?
        """,
        (withdrawal_id,),
    )

    row = cur.fetchone()

    if not row:
        conn.close()

        await query.answer(
            "Withdrawal not found.",
            show_alert=True,
        )
        return

    if row["status"] != "pending":
        conn.close()

        await query.answer(
            "This request is already processed.",
            show_alert=True,
        )
        return

    cur.execute(
        """
        UPDATE withdrawals
        SET status = 'approved'
        WHERE id = ?
        """,
        (withdrawal_id,),
    )

    conn.commit()
    conn.close()

    await query.answer(
        "Withdrawal approved.",
        show_alert=True,
    )

    try:
        await context.bot.send_message(
            chat_id=row["user_id"],
            text=(
                "✅ <b>Withdrawal Approved</b>\n\n"
                f"🆔 Request: <code>#{withdrawal_id}</code>\n"
                f"💰 Amount: <b>{float(row['amount']):.2f} ETB</b>\n\n"
                "Admin ጥያቄዎን አጽድቋል። "
                "Payment ከተላከ በኋላ የpayment proof "
                "ይላካል።"
            ),
            parse_mode=ParseMode.HTML,
        )
    except Exception:
        logger.exception(
            "Could not notify approved user."
        )

    await show_admin_withdrawals(query)


async def reject_withdrawal(
    query,
    context,
    withdrawal_id,
):
    if not is_admin(query.from_user.id):
        await query.answer(
            "Admin only.",
            show_alert=True,
        )
        return

    conn = db()
    cur = conn.cursor()

    cur.execute(
        """
        SELECT *
        FROM withdrawals
        WHERE id = ?
        """,
        (withdrawal_id,),
    )

    row = cur.fetchone()

    if not row:
        conn.close()

        await query.answer(
            "Withdrawal not found.",
            show_alert=True,
        )
        return

    if row["status"] != "pending":
        conn.close()

        await query.answer(
            "This request is already processed.",
            show_alert=True,
        )
        return

    cur.execute(
        """
        UPDATE withdrawals
        SET status = 'rejected'
        WHERE id = ?
        """,
        (withdrawal_id,),
    )

    cur.execute(
        """
        UPDATE users
        SET balance = balance + ?
        WHERE user_id = ?
        """,
        (
            float(row["amount"]),
            row["user_id"],
        ),
    )

    conn.commit()
    conn.close()

    await query.answer(
        "Withdrawal rejected and balance refunded.",
        show_alert=True,
    )

    try:
        await context.bot.send_message(
            chat_id=row["user_id"],
            text=(
                "❌ <b>Withdrawal Rejected</b>\n\n"
                f"🆔 Request: <code>#{withdrawal_id}</code>\n"
                f"💰 Amount: <b>{float(row['amount']):.2f} ETB</b>\n\n"
                "ጥያቄው ተሰርዟል። "
                "የwithdrawal amount ወደ balanceዎ ተመልሷል።"
            ),
            parse_mode=ParseMode.HTML,
        )
    except Exception:
        logger.exception(
            "Could not notify rejected user."
        )

    await show_admin_withdrawals(query)


# =========================================================
# ADMIN SUSPICIOUS USERS
# =========================================================

async def show_admin_suspicious(query):
    if not is_admin(query.from_user.id):
        await query.answer(
            "Admin only.",
            show_alert=True,
        )
        return

    conn = db()
    cur = conn.cursor()

    cur.execute(
        """
        SELECT *
        FROM users
        WHERE suspicious = 1
        ORDER BY user_id DESC
        LIMIT 50
        """
    )

    rows = cur.fetchall()
    conn.close()

    if not rows:
        text = (
            "⚠️ <b>Suspicious Users</b>\n\n"
            "No suspicious users."
        )
    else:
        lines = [
            "⚠️ <b>Suspicious Users</b>\n"
        ]

        for row in rows:
            lines.append(
                (
                    f"👤 {row['user_id']} | "
                    f"{escape(row['username'] or 'No username')} | "
                    f"Balance: {float(row['balance'] or 0):.2f}"
                )
            )

        text = "\n".join(lines)

    await safe_edit_message(
        query,
        text,
        reply_markup=InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "🔙 Admin Panel",
                        callback_data="admin",
                    )
                ]
            ]
        ),
    )


# =========================================================
# ADMIN ADD TASK
# =========================================================

async def start_add_task(query, context):
    if not is_admin(query.from_user.id):
        return

    context.user_data.clear()

    context.user_data["admin_step"] = (
        "task_title"
    )

    await safe_edit_message(
        query,
        (
            "🎯 <b>Add New Task</b>\n\n"
            "<b>Step 1/5</b>\n"
            "Task title ያስገቡ።\n\n"
            "Example:\n"
            "<code>Join Telegram Channel</code>"
        ),
        reply_markup=InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "❌ Cancel",
                        callback_data="admin",
                    )
                ]
            ]
        ),
    )


async def handle_admin_task_steps(
    update,
    context,
    text,
):
    if not is_admin(update.effective_user.id):
        return False

    step = context.user_data.get(
        "admin_step"
    )

    if step == "task_title":
        context.user_data["task_title"] = text
        context.user_data["admin_step"] = (
            "task_description"
        )

        await update.message.reply_text(
            (
                "🎯 <b>Add Task</b>\n\n"
                "<b>Step 2/5</b>\n"
                "Task description ያስገቡ።"
            ),
            parse_mode=ParseMode.HTML,
        )

        return True

    if step == "task_description":
        context.user_data[
            "task_description"
        ] = text

        context.user_data["admin_step"] = (
            "task_channel"
        )

        await update.message.reply_text(
            (
                "🎯 <b>Add Task</b>\n\n"
                "<b>Step 3/5</b>\n"
                "Channel username ያስገቡ።\n\n"
                "Example:\n"
                "<code>@ExampleChannel</code>"
            ),
            parse_mode=ParseMode.HTML,
        )

        return True

    if step == "task_channel":
        if not text.startswith("@"):
            await update.message.reply_text(
                (
                    "❌ Channel username @ መጀመር አለበት።\n\n"
                    "Example: <code>@ExampleChannel</code>"
                ),
                parse_mode=ParseMode.HTML,
            )
            return True

        context.user_data[
            "task_channel"
        ] = text

        context.user_data["admin_step"] = (
            "task_reward"
        )

        await update.message.reply_text(
            (
                "🎯 <b>Add Task</b>\n\n"
                "<b>Step 4/5</b>\n"
                "Task reward በETB ያስገቡ።\n\n"
                "Example:\n"
                "<code>1</code>"
            ),
            parse_mode=ParseMode.HTML,
        )

        return True

    if step == "task_reward":
        try:
            reward = float(text)

            if reward < 0:
                raise ValueError

        except ValueError:
            await update.message.reply_text(
                (
                    "❌ Invalid reward.\n"
                    "Example: <code>1</code>"
                ),
                parse_mode=ParseMode.HTML,
            )
            return True

        context.user_data[
            "task_reward"
        ] = reward

        context.user_data["admin_step"] = (
            "task_url"
        )

        await update.message.reply_text(
            (
                "🎯 <b>Add Task</b>\n\n"
                "<b>Step 5/5</b>\n"
                "Channel URL ያስገቡ።\n\n"
                "Example:\n"
                "<code>https://t.me/ExampleChannel</code>"
            ),
            parse_mode=ParseMode.HTML,
        )

        return True

    if step == "task_url":
        channel_url = text

        if not channel_url.startswith(
            "https://t.me/"
        ):
            await update.message.reply_text(
                (
                    "❌ Invalid Telegram URL.\n\n"
                    "Example:\n"
                    "<code>https://t.me/ExampleChannel</code>"
                ),
                parse_mode=ParseMode.HTML,
            )
            return True

        title = context.user_data.get(
            "task_title"
        )

        description = context.user_data.get(
            "task_description"
        )

        channel_username = context.user_data.get(
            "task_channel"
        )

        reward = context.user_data.get(
            "task_reward"
        )

        task_id = create_task(
            title,
            description,
            channel_username,
            channel_url,
            reward,
        )

        context.user_data.clear()

        await update.message.reply_text(
            (
                "✅ <b>Task Created Successfully!</b>\n\n"
                f"🆔 Task ID: <code>{task_id}</code>\n"
                f"🎯 Title: <b>{escape(title)}</b>\n"
                f"📢 Channel: <b>{escape(channel_username)}</b>\n"
                f"🎁 Reward: <b>{float(reward):.2f} ETB</b>"
            ),
            reply_markup=InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            "🛠 Admin Panel",
                            callback_data="admin",
                        )
                    ]
                ]
            ),
            parse_mode=ParseMode.HTML,
        )

        return True

    return False


# =========================================================
# CALLBACK HANDLER
# =========================================================

async def callback_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.callback_query
    data = query.data

    # -----------------------------------------------------
    # HOME
    # -----------------------------------------------------

    if data == "home":
        await query.answer()
        await show_home(
            update,
            context,
            edit=True,
        )
        return

    # -----------------------------------------------------
    # VERIFY CHANNELS
    # -----------------------------------------------------

    if data == "verify_channels":
        user_id = query.from_user.id

        missing = await missing_channels(
            context.bot,
            user_id,
        )

        if missing:
            await query.answer(
                f"❌ {len(missing)} channel(s) remaining.",
                show_alert=True,
            )

            await show_join_required(
                update,
                context,
                missing,
                edit=True,
            )

            return

        set_joined_all(
            user_id,
            True,
        )

        rewarded = process_referral_reward(
            user_id
        )

        await query.answer(
            "✅ Verification successful!",
            show_alert=True,
        )

        await show_home(
            update,
            context,
            edit=True,
        )

        return

    # -----------------------------------------------------
    # USER MENU
    # -----------------------------------------------------

    if data == "balance":
        await query.answer()
        await show_balance(query)
        return

    if data == "referral":
        await query.answer()
        await show_referral(query)
        return

    if data == "tasks":
        await query.answer()
        await show_tasks(query)
        return

    if data == "wallet":
        await query.answer()
        await show_wallet(query)
        return

    if data == "withdraw":
        await query.answer()
        await show_withdraw(query)
        return

    if data == "support":
        await query.answer()
        await show_support(query)
        return

    # -----------------------------------------------------
    # WALLET TYPE
    # -----------------------------------------------------

    if data == "wallet_cbe":
        await query.answer()
        await ask_wallet(
            query,
            "CBE",
            context,
        )
        return

    if data == "wallet_telebirr":
        await query.answer()
        await ask_wallet(
            query,
            "Telebirr",
            context,
        )
        return

    # -----------------------------------------------------
    # TASK
    # -----------------------------------------------------

    if data.startswith("task_"):
        try:
            task_id = int(
                data.split("_", 1)[1]
            )
        except ValueError:
            await query.answer(
                "Invalid task.",
                show_alert=True,
            )
            return

        await query.answer()

        await show_task(
            query,
            task_id,
        )

        return

    if data.startswith("verify_task_"):
        try:
            task_id = int(
                data.split("_", 2)[2]
            )
        except ValueError:
            await query.answer(
                "Invalid task.",
                show_alert=True,
            )
            return

        await verify_task(
            query,
            task_id,
            context,
        )

        return

    # -----------------------------------------------------
    # WITHDRAW CONFIRM
    # -----------------------------------------------------

    if data == "confirm_withdrawal":
        await confirm_withdrawal_process(
            query,
            context,
        )
        return

    # -----------------------------------------------------
    # ADMIN PANEL
    # -----------------------------------------------------

    if data == "admin":
        await query.answer()
        await show_admin(query)
        return

    if data == "admin_stats":
        await query.answer()
        await show_admin_stats(query)
        return

    if data == "admin_reward":
        await query.answer()
        await show_admin_reward(query)
        return

    if data == "change_reward":
        await query.answer()
        await start_change_reward(
            query,
            context,
        )
        return

    if data == "admin_referrals":
        await query.answer()
        await show_admin_referrals(query)
        return

    if data == "admin_referral_lookup":
        await query.answer()
        await start_admin_referral_lookup(
            query,
            context,
        )
        return

    if data == "admin_withdrawals":
        await query.answer()
        await show_admin_withdrawals(query)
        return

    if data == "admin_suspicious":
        await query.answer()
        await show_admin_suspicious(query)
        return

    if data == "admin_add_task":
        await query.answer()
        await start_add_task(
            query,
            context,
        )
        return

    # -----------------------------------------------------
    # TEST BALANCE
    # -----------------------------------------------------

    if data == "admin_test_balance":
        await start_test_balance(
            query,
            context,
        )
        return

    # -----------------------------------------------------
    # APPROVE WITHDRAWAL
    # -----------------------------------------------------

    if data.startswith(
        "approve_withdrawal_"
    ):
        try:
            withdrawal_id = int(
                data.split("_")[-1]
            )
        except ValueError:
            await query.answer(
                "Invalid request.",
                show_alert=True,
            )
            return

        await approve_withdrawal(
            query,
            context,
            withdrawal_id,
        )
        return

    # -----------------------------------------------------
    # REJECT WITHDRAWAL
    # -----------------------------------------------------

    if data.startswith(
        "reject_withdrawal_"
    ):
        try:
            withdrawal_id = int(
                data.split("_")[-1]
            )
        except ValueError:
            await query.answer(
                "Invalid request.",
                show_alert=True,
            )
            return

        await reject_withdrawal(
            query,
            context,
            withdrawal_id,
        )
        return

    await query.answer()


# =========================================================
# MESSAGE HANDLER
# =========================================================

async def message_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not update.message:
        return

    text = update.message.text.strip()

    # -----------------------------------------------------
    # WALLET INPUT
    # -----------------------------------------------------

    wallet_step = context.user_data.get(
        "wallet_step"
    )

    if wallet_step == "number":
        await save_wallet_from_input(
            update,
            context,
        )
        return

    # -----------------------------------------------------
    # WITHDRAW INPUT
    # -----------------------------------------------------

    withdraw_step = context.user_data.get(
        "withdraw_step"
    )

    if withdraw_step == "amount":
        await handle_withdraw_amount_input(
            update,
            context,
        )
        return

    # -----------------------------------------------------
    # ADMIN
    # -----------------------------------------------------

    if is_admin(update.effective_user.id):

        handled = await handle_admin_test_balance(
            update,
            context,
            text,
        )

        if handled:
            return

        admin_step = context.user_data.get(
            "admin_step"
        )

        # Referral lookup
        if admin_step == "referral_lookup":
            try:
                target_user_id = int(text)

                if target_user_id <= 0:
                    raise ValueError

            except ValueError:
                await update.message.reply_text(
                    (
                        "❌ Invalid User ID.\n\n"
                        "Example:\n"
                        "<code>8727153413</code>"
                    ),
                    parse_mode=ParseMode.HTML,
                )
                return

            context.user_data.clear()

            await show_referral_details_for_message(
                update,
                target_user_id,
            )

            return

        # Referral reward
        if admin_step == "reward":
            try:
                reward = float(text)

                if reward < 0:
                    raise ValueError

            except ValueError:
                await update.message.reply_text(
                    (
                        "❌ Invalid reward.\n\n"
                        "Example:\n"
                        "<code>2</code>"
                    ),
                    parse_mode=ParseMode.HTML,
                )
                return

            set_setting(
                "referral_reward",
                reward,
            )

            context.user_data.clear()

            await update.message.reply_text(
                (
                    "✅ <b>Referral Reward Updated</b>\n\n"
                    f"🎁 New Reward: <b>{reward:.2f} ETB</b>"
                ),
                reply_markup=InlineKeyboardMarkup(
                    [
                        [
                            InlineKeyboardButton(
                                "🛠 Admin Panel",
                                callback_data="admin",
                            )
                        ]
                    ]
                ),
                parse_mode=ParseMode.HTML,
            )

            return

        # Add task
        handled_task = await handle_admin_task_steps(
            update,
            context,
            text,
        )

        if handled_task:
            return

    # -----------------------------------------------------
    # NORMAL USER TEXT
    # -----------------------------------------------------

    await update.message.reply_text(
        (
            "ℹ️ ከታች ያሉትን buttons ይጠቀሙ።\n\n"
            "ወይም /start በማለት botን እንደገና ይጀምሩ።"
        ),
        reply_markup=main_keyboard(),
        parse_mode=ParseMode.HTML,
    )


# =========================================================
# /START
# =========================================================

async def start_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    user = update.effective_user

    referred_by = None

    if context.args:
        arg = context.args[0].strip()

        if arg.isdigit():
            try:
                ref_id = int(arg)

                if ref_id != user.id:
                    referred_by = ref_id

            except Exception:
                referred_by = None

    create_or_update_user(
        user.id,
        user.username,
        user.first_name,
        referred_by=referred_by,
    )

    context.user_data.clear()

    await show_home(
        update,
        context,
        edit=False,
    )


# =========================================================
# /ADMIN
# =========================================================

async def admin_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text(
            "❌ Admin only."
        )
        return

    context.user_data.clear()

    await update.message.reply_text(
        (
            "🛠 <b>Global Cash Bot Admin Panel</b>\n\n"
            "ከታች ያለውን option ይምረጡ።"
        ),
        reply_markup=admin_keyboard(),
        parse_mode=ParseMode.HTML,
    )


# =========================================================
# /CANCEL
# =========================================================

async def cancel_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    context.user_data.clear()

    await update.message.reply_text(
        "❌ Cancelled.",
        reply_markup=main_keyboard(),
    )


# =========================================================
# ERROR HANDLER
# =========================================================

async def error_handler(
    update,
    context,
):
    logger.exception(
        "Unhandled exception:",
        exc_info=context.error,
    )


# =========================================================
# MAIN
# =========================================================

def main():
    init_db()

    application = (
        Application.builder()
        .token(BOT_TOKEN)
        .build()
    )

    application.add_handler(
        CommandHandler(
            "start",
            start_command,
        )
    )

    application.add_handler(
        CommandHandler(
            "admin",
            admin_command,
        )
    )

    application.add_handler(
        CommandHandler(
            "cancel",
            cancel_command,
        )
    )

    application.add_handler(
        CallbackQueryHandler(
            callback_handler
        )
    )

    application.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            message_handler,
        )
    )

    application.add_error_handler(
        error_handler
    )

    logger.info(
        "Global Cash Bot started successfully."
    )

    application.run_polling(
        allowed_updates=Update.ALL_TYPES
    )


if __name__ == "__main__":
    main()

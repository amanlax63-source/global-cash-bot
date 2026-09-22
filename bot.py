import os
import re
import sqlite3
import logging
import math
from contextlib import contextmanager
from datetime import datetime, timezone
from html import escape

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# =========================================================
# GLOBAL CASH BOT
# =========================================================

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_ID = os.getenv("ADMIN_ID", "").strip()
SUPPORT_USERNAME = "@AmanM_12"

DB_FILE = os.getenv("DB_FILE", "global_cash.db")

# =========================================================
# BOT SETTINGS
# =========================================================

BOT_NAME = "Global Cash Bot"
REFERRAL_REWARD_DEFAULT = 2.0
MIN_WITHDRAW_DEFAULT = 30.0

# =========================================================
# REQUIRED CHANNELS
# =========================================================

REQUIRED_CHANNELS = [
    {
        "username": "@Sheger_tech1",
        "link": "https://t.me/Sheger_tech1",
        "name": "Sheger Tech",
    },
    {
        "username": "@EthioVortex1",
        "link": "https://t.me/EthioVortex1",
        "name": "Ethio Vortex",
    },
    {
        "username": "@ethiocashflow",
        "link": "https://t.me/ethiocashflow",
        "name": "Ethio Cash Flow",
    },
    {
        "username": "@AmanIncomeLab",
        "link": "https://t.me/AmanIncomeLab",
        "name": "Aman Income Lab",
    },
    {
        "username": "@OnlineIncomeHub07",
        "link": "https://t.me/OnlineIncomeHub07",
        "name": "Online Income Hub",
    },
    {
        "username": "@Paymentprooff2",
        "link": "https://t.me/Paymentprooff2",
        "name": "Payment Proof",
    },
]

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

@contextmanager
def db():
    conn = sqlite3.connect(DB_FILE, timeout=30)
    conn.row_factory = sqlite3.Row

    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    with db() as conn:

        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_id INTEGER UNIQUE NOT NULL,
                username TEXT,
                first_name TEXT,
                balance REAL DEFAULT 0,
                wallet_type TEXT,
                wallet_number TEXT,
                joined_all INTEGER DEFAULT 0,
                banned INTEGER DEFAULT 0,
                suspicious INTEGER DEFAULT 0,
                created_at TEXT,
                updated_at TEXT
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS referrals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                inviter_id INTEGER NOT NULL,
                invited_id INTEGER UNIQUE NOT NULL,
                reward REAL DEFAULT 0,
                paid INTEGER DEFAULT 0,
                created_at TEXT
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS withdrawals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                amount REAL NOT NULL,
                wallet_type TEXT NOT NULL,
                wallet_number TEXT NOT NULL,
                status TEXT DEFAULT 'pending',
                created_at TEXT,
                updated_at TEXT
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                link TEXT NOT NULL,
                channel TEXT,
                reward REAL DEFAULT 0,
                active INTEGER DEFAULT 1,
                created_at TEXT
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS user_tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                task_id INTEGER NOT NULL,
                completed INTEGER DEFAULT 0,
                created_at TEXT,
                UNIQUE(user_id, task_id)
            )
        """)

        conn.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS idx_wallet_unique
            ON users(wallet_type, wallet_number)
            WHERE wallet_type IS NOT NULL
            AND wallet_number IS NOT NULL
            AND wallet_number != ''
        """)

        conn.execute("""
            INSERT OR IGNORE INTO settings(key, value)
            VALUES ('referral_reward', '2')
        """)

        conn.execute("""
            INSERT OR IGNORE INTO settings(key, value)
            VALUES ('min_withdraw', '30')
        """)


# =========================================================
# HELPERS
# =========================================================

def now():
    return datetime.now(timezone.utc).isoformat()


def admin_id_int():
    try:
        return int(ADMIN_ID)
    except Exception:
        return 0


def is_admin(user_id):
    return user_id == admin_id_int()


def get_setting(key, default):
    with db() as conn:
        row = conn.execute(
            "SELECT value FROM settings WHERE key=?",
            (key,),
        ).fetchone()

    if not row:
        return default

    try:
        value = float(row["value"])

        if not math.isfinite(value):
            return default

        return value
    except Exception:
        return default


def set_setting(key, value):
    with db() as conn:
        conn.execute("""
            INSERT INTO settings(key, value)
            VALUES (?, ?)
            ON CONFLICT(key)
            DO UPDATE SET value=excluded.value
        """, (key, str(value)))


def get_user(telegram_id):
    with db() as conn:
        return conn.execute(
            "SELECT * FROM users WHERE telegram_id=?",
            (telegram_id,),
        ).fetchone()


def create_user(
    telegram_id,
    username=None,
    first_name=None,
    referrer_id=None,
):
    existing = get_user(telegram_id)

    if existing:
        return existing

    with db() as conn:

        conn.execute("""
            INSERT INTO users(
                telegram_id,
                username,
                first_name,
                created_at,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?)
        """, (
            telegram_id,
            username or "",
            first_name or "",
            now(),
            now(),
        ))

        if referrer_id and referrer_id != telegram_id:

            inviter = conn.execute(
                "SELECT telegram_id FROM users WHERE telegram_id=?",
                (referrer_id,),
            ).fetchone()

            if inviter:
                conn.execute("""
                    INSERT OR IGNORE INTO referrals(
                        inviter_id,
                        invited_id,
                        reward,
                        paid,
                        created_at
                    )
                    VALUES (?, ?, 0, 0, ?)
                """, (
                    referrer_id,
                    telegram_id,
                    now(),
                ))

    return get_user(telegram_id)


def update_user_basic(update):
    user = update.effective_user

    if not user:
        return

    with db() as conn:
        conn.execute("""
            UPDATE users
            SET username=?,
                first_name=?,
                updated_at=?
            WHERE telegram_id=?
        """, (
            user.username or "",
            user.first_name or "",
            now(),
            user.id,
        ))


def add_balance(user_id, amount):
    with db() as conn:
        conn.execute("""
            UPDATE users
            SET balance = balance + ?,
                updated_at=?
            WHERE telegram_id=?
        """, (
            amount,
            now(),
            user_id,
        ))


def get_balance(user_id):
    with db() as conn:
        row = conn.execute(
            "SELECT balance FROM users WHERE telegram_id=?",
            (user_id,),
        ).fetchone()

    if not row:
        return 0.0

    return float(row["balance"] or 0)


def set_joined_all(user_id, value=True):
    with db() as conn:
        conn.execute("""
            UPDATE users
            SET joined_all=?,
                updated_at=?
            WHERE telegram_id=?
        """, (
            1 if value else 0,
            now(),
            user_id,
        ))


# =========================================================
# CHANNEL VERIFICATION
# =========================================================

async def check_channel_member(bot, channel_username, user_id):

    try:
        member = await bot.get_chat_member(
            chat_id=channel_username,
            user_id=user_id,
        )

        # python-telegram-bot returns string statuses:
        # member
        # administrator
        # creator
        # left
        # kicked

        return member.status in (
            "member",
            "administrator",
            "creator",
        )

    except Exception as e:
        logger.warning(
            "Channel check failed %s for %s: %s",
            channel_username,
            user_id,
            e,
        )

        return False


async def missing_channels(bot, user_id):

    missing = []

    for channel in REQUIRED_CHANNELS:

        joined = await check_channel_member(
            bot,
            channel["username"],
            user_id,
        )

        if not joined:
            missing.append(channel)

    return missing


# =========================================================
# REFERRAL REWARD
# =========================================================

def process_referral_reward(user_id):

    reward = get_setting(
        "referral_reward",
        REFERRAL_REWARD_DEFAULT,
    )

    with db() as conn:

        referral = conn.execute("""
            SELECT *
            FROM referrals
            WHERE invited_id=?
            AND paid=0
        """, (user_id,)).fetchone()

        if not referral:
            return False

        inviter_id = referral["inviter_id"]

        inviter = conn.execute("""
            SELECT *
            FROM users
            WHERE telegram_id=?
        """, (inviter_id,)).fetchone()

        if not inviter:
            return False

        if inviter["banned"]:
            return False

        if inviter["suspicious"]:
            return False

        conn.execute("""
            UPDATE referrals
            SET reward=?,
                paid=1
            WHERE id=?
        """, (
            reward,
            referral["id"],
        ))

        conn.execute("""
            UPDATE users
            SET balance=balance+?,
                updated_at=?
            WHERE telegram_id=?
        """, (
            reward,
            now(),
            inviter_id,
        ))

        return True


# =========================================================
# KEYBOARDS
# =========================================================

def join_keyboard(missing):

    buttons = []

    for channel in missing:

        buttons.append([
            InlineKeyboardButton(
                f"📢 {channel['name']}",
                url=channel["link"],
            )
        ])

    buttons.append([
        InlineKeyboardButton(
            "✅ Verify",
            callback_data="verify",
        )
    ])

    return InlineKeyboardMarkup(buttons)


def home_keyboard():

    return InlineKeyboardMarkup([

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
                "💳 Withdraw",
                callback_data="withdraw",
            ),
        ],

        [
            InlineKeyboardButton(
                "👛 Wallet",
                callback_data="wallet",
            ),
            InlineKeyboardButton(
                "🆘 Support",
                callback_data="support",
            ),
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


def admin_keyboard():

    return InlineKeyboardMarkup([

        [
            InlineKeyboardButton(
                "📊 Statistics",
                callback_data="admin_stats",
            ),
            InlineKeyboardButton(
                "💰 Reward",
                callback_data="admin_change_reward",
            ),
        ],

        [
            InlineKeyboardButton(
                "💸 Withdrawals",
                callback_data="admin_withdrawals",
            ),
            InlineKeyboardButton(
                "🔎 Referral Search",
                callback_data="admin_referral_lookup",
            ),
        ],

        [
            InlineKeyboardButton(
                "⚠️ Suspicious",
                callback_data="admin_suspicious",
            ),
            InlineKeyboardButton(
                "➕ Add Task",
                callback_data="admin_add_task",
            ),
        ],

        [
            InlineKeyboardButton(
                "📢 Broadcast",
                callback_data="admin_broadcast",
            )
        ],

    ])


def admin_back_keyboard():

    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "🔙 Admin Back",
                callback_data="admin",
            )
        ]
    ])


# =========================================================
# JOIN SCREEN
# =========================================================

async def show_join_required(update, context, missing=None):

    user = update.effective_user

    if missing is None:
        missing = await missing_channels(
            context.bot,
            user.id,
        )

    if not missing:

        set_joined_all(user.id, True)

        process_referral_reward(user.id)

        return await show_home(
            update,
            context,
        )

    text = (
        "🔐 <b>Global Cash Bot</b>\n\n"
        "Bot ለመጠቀም ከታች ያሉትን channels በሙሉ Join ያድርጉ።\n\n"
        f"📌 Remaining: <b>{len(missing)}</b>\n\n"
        "ከጨረሱ በኋላ <b>Verify</b> ይጫኑ።"
    )

    keyboard = join_keyboard(missing)

    if update.callback_query:

        await update.callback_query.edit_message_text(
            text,
            reply_markup=keyboard,
            parse_mode="HTML",
        )

    else:

        await update.message.reply_text(
            text,
            reply_markup=keyboard,
            parse_mode="HTML",
        )


# =========================================================
# HOME
# =========================================================

async def show_home(update, context):

    user = update.effective_user

    missing = await missing_channels(
        context.bot,
        user.id,
    )

    if missing:
        return await show_join_required(
            update,
            context,
            missing,
        )

    set_joined_all(user.id, True)

    process_referral_reward(user.id)

    balance = get_balance(user.id)

    text = (
        f"🎉 <b>Welcome to {BOT_NAME}</b>\n\n"
        f"Hello <b>{escape(user.first_name or 'Friend')}</b> 👋\n\n"
        f"💰 Balance: <b>{balance:.2f} ETB</b>\n\n"
        "👇 ከታች ያለውን menu ይጠቀሙ።"
    )

    if update.callback_query:

        await update.callback_query.edit_message_text(
            text,
            reply_markup=home_keyboard(),
            parse_mode="HTML",
        )

    else:

        await update.message.reply_text(
            text,
            reply_markup=home_keyboard(),
            parse_mode="HTML",
        )


# =========================================================
# BALANCE
# =========================================================

async def show_balance(query, context):

    user_id = query.from_user.id

    balance = get_balance(user_id)

    text = (
        "💰 <b>Your Balance</b>\n\n"
        f"💵 Available: <b>{balance:.2f} ETB</b>\n\n"
        "Referral reward እና task rewards እዚህ ይጨመራሉ።"
    )

    await query.edit_message_text(
        text,
        reply_markup=back_keyboard(),
        parse_mode="HTML",
    )


# =========================================================
# REFERRAL
# =========================================================

async def show_referral(query, context):

    user_id = query.from_user.id

    me = await context.bot.get_me()

    referral_link = (
        f"https://t.me/{me.username}?start=ref_{user_id}"
    )

    with db() as conn:

        row = conn.execute("""
            SELECT COUNT(*) AS total
            FROM referrals
            WHERE inviter_id=?
            AND paid=1
        """, (user_id,)).fetchone()

    total = row["total"] if row else 0

    reward = get_setting(
        "referral_reward",
        REFERRAL_REWARD_DEFAULT,
    )

    text = (
        "👥 <b>Referral Program</b>\n\n"
        f"👤 Successful referrals: <b>{total}</b>\n"
        f"💰 Reward per referral: <b>{reward:.2f} ETB</b>\n\n"
        "👇 ይህን link ለጓደኞችዎ share ያድርጉ።\n\n"
        f"<code>{referral_link}</code>"
    )

    keyboard = InlineKeyboardMarkup([

        [
            InlineKeyboardButton(
                "📤 Share Referral",
                url=(
                    "https://t.me/share/url?"
                    f"url={referral_link}"
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

    await query.edit_message_text(
        text,
        reply_markup=keyboard,
        parse_mode="HTML",
    )


# =========================================================
# TASKS
# =========================================================

async def show_tasks(query, context):

    with db() as conn:

        tasks = conn.execute("""
            SELECT *
            FROM tasks
            WHERE active=1
            ORDER BY id DESC
        """).fetchall()

    if not tasks:

        return await query.edit_message_text(
            "🎯 <b>Tasks</b>\n\n"
            "አሁን ላይ የሚገኝ task የለም።",
            reply_markup=back_keyboard(),
            parse_mode="HTML",
        )

    buttons = []

    for task in tasks:

        buttons.append([
            InlineKeyboardButton(
                f"🎯 {task['title']} • {task['reward']:.2f} ETB",
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
        "Task ይምረጡ፦",
        reply_markup=InlineKeyboardMarkup(buttons),
        parse_mode="HTML",
    )


async def show_task_detail(query, context, task_id):

    with db() as conn:

        task = conn.execute("""
            SELECT *
            FROM tasks
            WHERE id=?
            AND active=1
        """, (task_id,)).fetchone()

    if not task:

        return await query.answer(
            "Task አልተገኘም።",
            show_alert=True,
        )

    user_id = query.from_user.id

    with db() as conn:

        completed = conn.execute("""
            SELECT *
            FROM user_tasks
            WHERE user_id=?
            AND task_id=?
            AND completed=1
        """, (
            user_id,
            task_id,
        )).fetchone()

    if completed:

        status = "✅ Completed"

    else:

        status = "⏳ Not completed"

    text = (
        f"🎯 <b>{escape(task['title'])}</b>\n\n"
        f"💰 Reward: <b>{task['reward']:.2f} ETB</b>\n"
        f"📌 Status: <b>{status}</b>\n\n"
        "Task link በመክፈት የሚፈለገውን ያድርጉ።"
    )

    buttons = []

    if not completed:

        buttons.append([
            InlineKeyboardButton(
                "🚀 Open Task",
                url=task["link"],
            )
        ])

        buttons.append([
            InlineKeyboardButton(
                "✅ Verify Task",
                callback_data=f"verify_task_{task_id}",
            )
        ])

    buttons.append([
        InlineKeyboardButton(
            "🔙 Tasks",
            callback_data="tasks",
        )
    ])

    await query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup(buttons),
        parse_mode="HTML",
    )


async def verify_task(query, context, task_id):

    user_id = query.from_user.id

    with db() as conn:

        task = conn.execute("""
            SELECT *
            FROM tasks
            WHERE id=?
            AND active=1
        """, (task_id,)).fetchone()

    if not task:
        return await query.answer(
            "Task not found.",
            show_alert=True,
        )

    channel = task["channel"]

    if not channel:

        return await query.answer(
            "ይህ task ለ verification አልተዘጋጀም።",
            show_alert=True,
        )

    joined = await check_channel_member(
        context.bot,
        channel,
        user_id,
    )

    if not joined:

        return await query.answer(
            "❌ Task አልተጠናቀቀም። Channel ይቀላቀሉ።",
            show_alert=True,
        )

    with db() as conn:

        existing = conn.execute("""
            SELECT *
            FROM user_tasks
            WHERE user_id=?
            AND task_id=?
        """, (
            user_id,
            task_id,
        )).fetchone()

        if existing and existing["completed"]:

            return await query.answer(
                "Already completed.",
                show_alert=True,
            )

        conn.execute("""
            INSERT INTO user_tasks(
                user_id,
                task_id,
                completed,
                created_at
            )
            VALUES (?, ?, 1, ?)
            ON CONFLICT(user_id, task_id)
            DO UPDATE SET completed=1
        """, (
            user_id,
            task_id,
            now(),
        ))

        conn.execute("""
            UPDATE users
            SET balance=balance+?,
                updated_at=?
            WHERE telegram_id=?
        """, (
            task["reward"],
            now(),
            user_id,
        ))

    await query.answer(
        f"✅ +{task['reward']:.2f} ETB added!",
        show_alert=True,
    )

    await show_task_detail(
        query,
        context,
        task_id,
    )


# =========================================================
# WALLET
# =========================================================

async def show_wallet(query, context):

    user = get_user(query.from_user.id)

    if user and user["wallet_type"]:

        wallet_text = (
            f"💳 Type: <b>{escape(user['wallet_type'])}</b>\n"
            f"🔢 Number: <code>{escape(user['wallet_number'])}</code>"
        )

    else:

        wallet_text = "❌ Wallet አልተመዘገበም።"

    text = (
        "👛 <b>My Wallet</b>\n\n"
        f"{wallet_text}\n\n"
        "Wallet ለመቀየር ይምረጡ።"
    )

    keyboard = InlineKeyboardMarkup([

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

    await query.edit_message_text(
        text,
        reply_markup=keyboard,
        parse_mode="HTML",
    )


async def start_wallet_input(query, context, wallet_type):

    context.user_data["wallet_type"] = wallet_type
    context.user_data["awaiting_wallet"] = True

    if wallet_type == "CBE":

        example = "1000123456789"

        text = (
            "🏦 <b>CBE Wallet</b>\n\n"
            "13 digit CBE account number ያስገቡ።\n\n"
            f"Example: <code>{example}</code>"
        )

    else:

        text = (
            "📱 <b>Telebirr Wallet</b>\n\n"
            "10 digit Telebirr number ያስገቡ።\n\n"
            "Example: <code>0912345678</code>"
        )

    await query.edit_message_text(
        text,
        reply_markup=back_keyboard(),
        parse_mode="HTML",
    )


def validate_wallet(wallet_type, number):

    number = number.strip()

    if wallet_type == "CBE":

        return bool(
            re.fullmatch(r"1000\d{9}", number)
        )

    if wallet_type == "Telebirr":

        return bool(
            re.fullmatch(r"(09|07)\d{8}", number)
        )

    return False


async def save_wallet(update, context):

    user_id = update.effective_user.id

    wallet_type = context.user_data.get("wallet_type")

    number = update.message.text.strip()

    if not wallet_type:

        context.user_data["awaiting_wallet"] = False

        return

    if not validate_wallet(
        wallet_type,
        number,
    ):

        if wallet_type == "CBE":

            await update.message.reply_text(
                "❌ CBE account ትክክል አይደለም።\n\n"
                "13 digits እና 1000 በሚጀምር መሆን አለበት።"
            )

        else:

            await update.message.reply_text(
                "❌ Telebirr number ትክክል አይደለም።\n\n"
                "10 digits እና 09 ወይም 07 በሚጀምር መሆን አለበት።"
            )

        return

    with db() as conn:

        existing = conn.execute("""
            SELECT telegram_id
            FROM users
            WHERE wallet_type=?
            AND wallet_number=?
            AND telegram_id!=?
        """, (
            wallet_type,
            number,
            user_id,
        )).fetchone()

        if existing:

            return await update.message.reply_text(
                "❌ ይህ wallet ቀድሞ ተመዝግቧል።"
            )

        conn.execute("""
            UPDATE users
            SET wallet_type=?,
                wallet_number=?,
                updated_at=?
            WHERE telegram_id=?
        """, (
            wallet_type,
            number,
            now(),
            user_id,
        ))

    context.user_data["awaiting_wallet"] = False
    context.user_data.pop("wallet_type", None)

    await update.message.reply_text(
        "✅ <b>Wallet Saved!</b>\n\n"
        f"Type: <b>{wallet_type}</b>\n"
        f"Number: <code>{escape(number)}</code>",
        parse_mode="HTML",
        reply_markup=back_keyboard(),
    )


# =========================================================
# WITHDRAW
# =========================================================

async def show_withdraw(query, context):

    user = get_user(query.from_user.id)

    min_withdraw = get_setting(
        "min_withdraw",
        MIN_WITHDRAW_DEFAULT,
    )

    balance = get_balance(query.from_user.id)

    if not user or not user["wallet_type"]:

        return await query.edit_message_text(
            "💳 <b>Withdraw</b>\n\n"
            "❌ መጀመሪያ Wallet ያስገቡ።",
            reply_markup=InlineKeyboardMarkup([
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
            ]),
            parse_mode="HTML",
        )

    if balance < min_withdraw:

        return await query.edit_message_text(
            "💳 <b>Withdraw</b>\n\n"
            f"💰 Balance: <b>{balance:.2f} ETB</b>\n"
            f"📌 Minimum: <b>{min_withdraw:.2f} ETB</b>\n\n"
            "Minimum withdrawal ላይ እስኪደርሱ ይጠብቁ።",
            reply_markup=back_keyboard(),
            parse_mode="HTML",
        )

    # IMPORTANT:
    # enable withdrawal input
    context.user_data["awaiting_withdraw"] = True

    text = (
        "💳 <b>Withdraw</b>\n\n"
        f"💰 Balance: <b>{balance:.2f} ETB</b>\n"
        f"📌 Minimum: <b>{min_withdraw:.2f} ETB</b>\n\n"
        "ለመውጣት የሚፈልጉትን amount ብቻ ይላኩ።\n\n"
        "Example: <code>30</code>"
    )

    await query.edit_message_text(
        text,
        reply_markup=back_keyboard(),
        parse_mode="HTML",
    )


async def process_withdraw_amount(update, context):

    user_id = update.effective_user.id

    user = get_user(user_id)

    if not user:

        context.user_data["awaiting_withdraw"] = False
        return

    try:

        amount = float(
            update.message.text.strip()
        )

    except Exception:

        return await update.message.reply_text(
            "❌ እባክዎ valid amount ያስገቡ።\n"
            "Example: 30"
        )

    if not math.isfinite(amount):

        return await update.message.reply_text(
            "❌ Invalid amount."
        )

    min_withdraw = get_setting(
        "min_withdraw",
        MIN_WITHDRAW_DEFAULT,
    )

    balance = get_balance(user_id)

    if amount < min_withdraw:

        return await update.message.reply_text(
            f"❌ Minimum withdrawal is {min_withdraw:.2f} ETB."
        )

    if amount > balance:

        return await update.message.reply_text(
            f"❌ Balance በቂ አይደለም።\n"
            f"Balance: {balance:.2f} ETB"
        )

    with db() as conn:

        current = conn.execute("""
            SELECT balance
            FROM users
            WHERE telegram_id=?
        """, (user_id,)).fetchone()

        if not current:

            context.user_data["awaiting_withdraw"] = False

            return

        current_balance = float(
            current["balance"] or 0
        )

        if amount > current_balance:

            return await update.message.reply_text(
                "❌ Balance ተቀይሯል። እንደገና ይሞክሩ።"
            )

        conn.execute("""
            UPDATE users
            SET balance=balance-?,
                updated_at=?
            WHERE telegram_id=?
        """, (
            amount,
            now(),
            user_id,
        ))

        cursor = conn.execute("""
            INSERT INTO withdrawals(
                user_id,
                amount,
                wallet_type,
                wallet_number,
                status,
                created_at,
                updated_at
            )
            VALUES (?, ?, ?, ?, 'pending', ?, ?)
        """, (
            user_id,
            amount,
            user["wallet_type"],
            user["wallet_number"],
            now(),
            now(),
        ))

        withdrawal_id = cursor.lastrowid

    context.user_data["awaiting_withdraw"] = False

    await update.message.reply_text(
        "✅ <b>Withdrawal Request Sent!</b>\n\n"
        f"💰 Amount: <b>{amount:.2f} ETB</b>\n"
        f"💳 Wallet: <b>{escape(user['wallet_type'])}</b>\n"
        f"🔢 <code>{escape(user['wallet_number'])}</code>\n"
        f"🆔 Request: <b>#{withdrawal_id}</b>\n\n"
        "⏳ Admin እስኪያረጋግጥ ይጠብቁ።",
        parse_mode="HTML",
        reply_markup=back_keyboard(),
    )

    # Admin notification
    admin = admin_id_int()

    if admin:

        try:

            await context.bot.send_message(
                chat_id=admin,
                text=(
                    "💸 <b>NEW WITHDRAWAL</b>\n\n"
                    f"🆔 Request: <b>#{withdrawal_id}</b>\n"
                    f"👤 User: <code>{user_id}</code>\n"
                    f"💰 Amount: <b>{amount:.2f} ETB</b>\n"
                    f"💳 Wallet: <b>{escape(user['wallet_type'])}</b>\n"
                    f"🔢 <code>{escape(user['wallet_number'])}</code>"
                ),
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup([
                    [
                        InlineKeyboardButton(
                            "✅ Approve",
                            callback_data=f"approve_{withdrawal_id}",
                        ),
                        InlineKeyboardButton(
                            "❌ Reject",
                            callback_data=f"reject_{withdrawal_id}",
                        ),
                    ]
                ]),
            )

        except Exception as e:

            logger.error(
                "Admin notification failed: %s",
                e,
            )


# =========================================================
# SUPPORT
# =========================================================

async def show_support(query, context):

    text = (
        "🆘 <b>Support</b>\n\n"
        "ማንኛውም ችግር ካለዎት ከ Admin ጋር ያግኙ።\n\n"
        f"👨‍💻 Support: <b>{SUPPORT_USERNAME}</b>"
    )

    keyboard = InlineKeyboardMarkup([

        [
            InlineKeyboardButton(
                "💬 Contact Support",
                url=f"https://t.me/{SUPPORT_USERNAME.replace('@', '')}",
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
# ADMIN PANEL
# =========================================================

async def show_admin(query, context):

    if not is_admin(query.from_user.id):

        return await query.answer(
            "⛔ Admin only.",
            show_alert=True,
        )

    await query.edit_message_text(
        "🛠 <b>Global Cash Admin Panel</b>\n\n"
        "ከታች ያለውን menu ይጠቀሙ።",
        reply_markup=admin_keyboard(),
        parse_mode="HTML",
    )


async def show_admin_stats(query, context):

    if not is_admin(query.from_user.id):

        return await query.answer(
            "⛔ Admin only.",
            show_alert=True,
        )

    with db() as conn:

        users = conn.execute(
            "SELECT COUNT(*) AS c FROM users"
        ).fetchone()["c"]

        joined = conn.execute(
            "SELECT COUNT(*) AS c FROM users WHERE joined_all=1"
        ).fetchone()["c"]

        total_balance = conn.execute(
            "SELECT COALESCE(SUM(balance),0) AS s FROM users"
        ).fetchone()["s"]

        pending = conn.execute("""
            SELECT COUNT(*) AS c
            FROM withdrawals
            WHERE status='pending'
        """).fetchone()["c"]

        approved = conn.execute("""
            SELECT COUNT(*) AS c
            FROM withdrawals
            WHERE status='approved'
        """).fetchone()["c"]

        referrals = conn.execute("""
            SELECT COUNT(*) AS c
            FROM referrals
            WHERE paid=1
        """).fetchone()["c"]

    text = (
        "📊 <b>Statistics</b>\n\n"
        f"👥 Users: <b>{users}</b>\n"
        f"✅ Verified users: <b>{joined}</b>\n"
        f"👥 Paid referrals: <b>{referrals}</b>\n"
        f"💰 Total balance: <b>{total_balance:.2f} ETB</b>\n"
        f"⏳ Pending withdrawals: <b>{pending}</b>\n"
        f"✅ Approved withdrawals: <b>{approved}</b>"
    )

    await query.edit_message_text(
        text,
        reply_markup=admin_back_keyboard(),
        parse_mode="HTML",
    )


# =========================================================
# ADMIN CHANGE REWARD
# =========================================================

async def start_change_reward(query, context):

    if not is_admin(query.from_user.id):

        return await query.answer(
            "⛔ Admin only.",
            show_alert=True,
        )

    current = get_setting(
        "referral_reward",
        REFERRAL_REWARD_DEFAULT,
    )

    context.user_data["admin_action"] = "change_reward"

    await query.edit_message_text(
        "💰 <b>Change Referral Reward</b>\n\n"
        f"Current: <b>{current:.2f} ETB</b>\n\n"
        "New reward amount ያስገቡ።\n"
        "Example: <code>2.5</code>",
        reply_markup=admin_back_keyboard(),
        parse_mode="HTML",
    )


async def save_new_reward(update, context):

    try:

        value = float(
            update.message.text.strip()
        )

    except Exception:

        return await update.message.reply_text(
            "❌ Valid number ያስገቡ።"
        )

    if not math.isfinite(value) or value < 0:

        return await update.message.reply_text(
            "❌ Invalid reward."
        )

    set_setting(
        "referral_reward",
        value,
    )

    context.user_data.pop("admin_action", None)

    await update.message.reply_text(
        f"✅ Referral reward updated to <b>{value:.2f} ETB</b>.",
        parse_mode="HTML",
        reply_markup=admin_back_keyboard(),
    )


# =========================================================
# ADMIN REFERRAL SEARCH
# =========================================================

async def start_referral_lookup(query, context):

    if not is_admin(query.from_user.id):

        return await query.answer(
            "⛔ Admin only.",
            show_alert=True,
        )

    context.user_data["admin_action"] = "referral_lookup"

    await query.edit_message_text(
        "🔎 <b>Referral Search</b>\n\n"
        "User Telegram ID ወይም username ያስገቡ።\n\n"
        "Example: <code>123456789</code>",
        reply_markup=admin_back_keyboard(),
        parse_mode="HTML",
    )


async def search_referral_user(query, context, search_text):

    if not is_admin(query.from_user.id):
        return

    search_text = search_text.strip().lstrip("@")

    with db() as conn:

        if search_text.isdigit():

            user = conn.execute("""
                SELECT *
                FROM users
                WHERE telegram_id=?
            """, (int(search_text),)).fetchone()

        else:

            user = conn.execute("""
                SELECT *
                FROM users
                WHERE LOWER(username)=LOWER(?)
            """, (search_text,)).fetchone()

    if not user:

        return await query.edit_message_text(
            "❌ User not found.",
            reply_markup=admin_back_keyboard(),
        )

    with db() as conn:

        invited = conn.execute("""
            SELECT COUNT(*) AS c
            FROM referrals
            WHERE inviter_id=?
        """, (user["telegram_id"],)).fetchone()["c"]

        paid = conn.execute("""
            SELECT COUNT(*) AS c
            FROM referrals
            WHERE inviter_id=?
            AND paid=1
        """, (user["telegram_id"],)).fetchone()["c"]

    text = (
        "🔎 <b>User Referral Info</b>\n\n"
        f"👤 Name: <b>{escape(user['first_name'] or '')}</b>\n"
        f"🆔 ID: <code>{user['telegram_id']}</code>\n"
        f"📱 Username: @{escape(user['username'] or 'none')}\n"
        f"💰 Balance: <b>{user['balance']:.2f} ETB</b>\n"
        f"👥 Total referrals: <b>{invited}</b>\n"
        f"✅ Paid referrals: <b>{paid}</b>\n"
        f"⚠️ Suspicious: <b>{'Yes' if user['suspicious'] else 'No'}</b>\n"
        f"🚫 Banned: <b>{'Yes' if user['banned'] else 'No'}</b>"
    )

    await query.edit_message_text(
        text,
        reply_markup=admin_back_keyboard(),
        parse_mode="HTML",
    )


# =========================================================
# ADMIN SUSPICIOUS USERS
# =========================================================

async def show_suspicious(query, context):

    if not is_admin(query.from_user.id):

        return await query.answer(
            "⛔ Admin only.",
            show_alert=True,
        )

    with db() as conn:

        users = conn.execute("""
            SELECT *
            FROM users
            WHERE suspicious=1
            ORDER BY id DESC
            LIMIT 30
        """).fetchall()

    if not users:

        text = (
            "⚠️ <b>Suspicious Users</b>\n\n"
            "ምንም suspicious user የለም።"
        )

    else:

        lines = [
            "⚠️ <b>Suspicious Users</b>\n"
        ]

        for user in users:

            lines.append(
                f"👤 <code>{user['telegram_id']}</code> "
                f"@{escape(user['username'] or 'none')}"
            )

        text = "\n".join(lines)

    await query.edit_message_text(
        text,
        reply_markup=admin_back_keyboard(),
        parse_mode="HTML",
    )


# =========================================================
# ADMIN WITHDRAWALS
# =========================================================

async def show_admin_withdrawals(query, context):

    if not is_admin(query.from_user.id):

        return await query.answer(
            "⛔ Admin only.",
            show_alert=True,
        )

    with db() as conn:

        withdrawals = conn.execute("""
            SELECT *
            FROM withdrawals
            WHERE status='pending'
            ORDER BY id DESC
            LIMIT 30
        """).fetchall()

    if not withdrawals:

        return await query.edit_message_text(
            "💸 <b>Pending Withdrawals</b>\n\n"
            "No pending withdrawals.",
            reply_markup=admin_back_keyboard(),
            parse_mode="HTML",
        )

    buttons = []

    for w in withdrawals:

        buttons.append([
            InlineKeyboardButton(
                f"#{w['id']} • {w['amount']:.2f} ETB",
                callback_data=f"withdraw_info_{w['id']}",
            )
        ])

    buttons.append([
        InlineKeyboardButton(
            "🔙 Admin Back",
            callback_data="admin",
        )
    ])

    await query.edit_message_text(
        "💸 <b>Pending Withdrawals</b>\n\n"
        "Request ይምረጡ፦",
        reply_markup=InlineKeyboardMarkup(buttons),
        parse_mode="HTML",
    )


async def show_withdraw_info(query, context, withdrawal_id):

    if not is_admin(query.from_user.id):

        return await query.answer(
            "⛔ Admin only.",
            show_alert=True,
        )

    with db() as conn:

        w = conn.execute("""
            SELECT *
            FROM withdrawals
            WHERE id=?
        """, (withdrawal_id,)).fetchone()

    if not w:

        return await query.answer(
            "Withdrawal not found.",
            show_alert=True,
        )

    text = (
        "💸 <b>Withdrawal Request</b>\n\n"
        f"🆔 Request: <b>#{w['id']}</b>\n"
        f"👤 User: <code>{w['user_id']}</code>\n"
        f"💰 Amount: <b>{w['amount']:.2f} ETB</b>\n"
        f"💳 Wallet: <b>{escape(w['wallet_type'])}</b>\n"
        f"🔢 <code>{escape(w['wallet_number'])}</code>\n"
        f"📌 Status: <b>{escape(w['status'])}</b>"
    )

    buttons = []

    if w["status"] == "pending":

        buttons.append([
            InlineKeyboardButton(
                "✅ Approve",
                callback_data=f"approve_{w['id']}",
            ),
            InlineKeyboardButton(
                "❌ Reject",
                callback_data=f"reject_{w['id']}",
            ),
        ])

    buttons.append([
        InlineKeyboardButton(
            "🔙 Withdrawals",
            callback_data="admin_withdrawals",
        )
    ])

    await query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup(buttons),
        parse_mode="HTML",
    )


# =========================================================
# APPROVE WITHDRAWAL
# =========================================================

async def approve_withdrawal(query, context, withdrawal_id):

    if not is_admin(query.from_user.id):

        return await query.answer(
            "⛔ Admin only.",
            show_alert=True,
        )

    with db() as conn:

        w = conn.execute("""
            SELECT *
            FROM withdrawals
            WHERE id=?
        """, (withdrawal_id,)).fetchone()

        if not w:

            return await query.answer(
                "Request not found.",
                show_alert=True,
            )

        if w["status"] != "pending":

            return await query.answer(
                "Already processed.",
                show_alert=True,
            )

        conn.execute("""
            UPDATE withdrawals
            SET status='approved',
                updated_at=?
            WHERE id=?
        """, (
            now(),
            withdrawal_id,
        ))

    await query.answer(
        "Approved.",
        show_alert=True,
    )

    await query.edit_message_text(
        "✅ <b>Withdrawal Approved</b>\n\n"
        f"Request: <b>#{withdrawal_id}</b>\n"
        f"Amount: <b>{w['amount']:.2f} ETB</b>\n\n"
        "⚠️ Payment must be sent manually to the user's wallet.",
        reply_markup=admin_back_keyboard(),
        parse_mode="HTML",
    )

    try:

        await context.bot.send_message(
            chat_id=w["user_id"],
            text=(
                "✅ <b>Withdrawal Approved</b>\n\n"
                f"💰 Amount: <b>{w['amount']:.2f} ETB</b>\n"
                f"💳 Wallet: <b>{escape(w['wallet_type'])}</b>\n"
                f"🆔 Request: <b>#{withdrawal_id}</b>\n\n"
                "Admin ክፍያውን በmanual ይልካል።"
            ),
            parse_mode="HTML",
        )

    except Exception as e:

        logger.warning(
            "Could not notify user: %s",
            e,
        )


# =========================================================
# REJECT WITHDRAWAL
# =========================================================

async def reject_withdrawal(query, context, withdrawal_id):

    if not is_admin(query.from_user.id):

        return await query.answer(
            "⛔ Admin only.",
            show_alert=True,
        )

    with db() as conn:

        w = conn.execute("""
            SELECT *
            FROM withdrawals
            WHERE id=?
        """, (withdrawal_id,)).fetchone()

        if not w:

            return await query.answer(
                "Request not found.",
                show_alert=True,
            )

        if w["status"] != "pending":

            return await query.answer(
                "Already processed.",
                show_alert=True,
            )

        conn.execute("""
            UPDATE withdrawals
            SET status='rejected',
                updated_at=?
            WHERE id=?
        """, (
            now(),
            withdrawal_id,
        ))

        # Refund balance
        conn.execute("""
            UPDATE users
            SET balance=balance+?,
                updated_at=?
            WHERE telegram_id=?
        """, (
            w["amount"],
            now(),
            w["user_id"],
        ))

    await query.answer(
        "Rejected and refunded.",
        show_alert=True,
    )

    await query.edit_message_text(
        "❌ <b>Withdrawal Rejected</b>\n\n"
        f"Request: <b>#{withdrawal_id}</b>\n"
        f"Refund: <b>{w['amount']:.2f} ETB</b>",
        reply_markup=admin_back_keyboard(),
        parse_mode="HTML",
    )

    try:

        await context.bot.send_message(
            chat_id=w["user_id"],
            text=(
                "❌ <b>Withdrawal Rejected</b>\n\n"
                f"💰 Amount: <b>{w['amount']:.2f} ETB</b>\n"
                f"🆔 Request: <b>#{withdrawal_id}</b>\n\n"
                "💰 ገንዘቡ ወደ balance ተመልሷል።"
            ),
            parse_mode="HTML",
        )

    except Exception as e:

        logger.warning(
            "Could not notify user: %s",
            e,
        )


# =========================================================
# ADMIN ADD TASK
# =========================================================

async def start_add_task(query, context):

    if not is_admin(query.from_user.id):

        return await query.answer(
            "⛔ Admin only.",
            show_alert=True,
        )

    context.user_data["admin_action"] = "task_title"

    await query.edit_message_text(
        "➕ <b>Add Task</b>\n\n"
        "1️⃣ Task title ያስገቡ።\n\n"
        "Example: <code>Join Channel</code>",
        reply_markup=admin_back_keyboard(),
        parse_mode="HTML",
    )


async def handle_admin_task_input(update, context):

    action = context.user_data.get(
        "admin_action"
    )

    text = update.message.text.strip()

    if action == "task_title":

        context.user_data["task_title"] = text
        context.user_data["admin_action"] = "task_link"

        return await update.message.reply_text(
            "2️⃣ Task link ያስገቡ።\n\n"
            "Example:\n"
            "https://t.me/ExampleChannel"
        )

    if action == "task_link":

        context.user_data["task_link"] = text
        context.user_data["admin_action"] = "task_reward"

        return await update.message.reply_text(
            "3️⃣ Task reward ያስገቡ።\n\n"
            "Example: <code>1</code>",
            parse_mode="HTML",
        )

    if action == "task_reward":

        try:

            reward = float(text)

        except Exception:

            return await update.message.reply_text(
                "❌ Valid reward ያስገቡ።"
            )

        if not math.isfinite(reward) or reward < 0:

            return await update.message.reply_text(
                "❌ Invalid reward."
            )

        title = context.user_data.get(
            "task_title",
            "",
        )

        link = context.user_data.get(
            "task_link",
            "",
        )

        channel = ""

        if link.startswith("https://t.me/"):

            channel_name = link.split(
                "https://t.me/",
                1
            )[1].split("/", 1)[0]

            if channel_name:
                channel = "@" + channel_name

        elif link.startswith("@"):

            channel = link

        with db() as conn:

            cursor = conn.execute("""
                INSERT INTO tasks(
                    title,
                    link,
                    channel,
                    reward,
                    active,
                    created_at
                )
                VALUES (?, ?, ?, ?, 1, ?)
            """, (
                title,
                link,
                channel,
                reward,
                now(),
            ))

            task_id = cursor.lastrowid

        context.user_data.clear()

        await update.message.reply_text(
            "✅ <b>Task Created!</b>\n\n"
            f"🆔 ID: <b>{task_id}</b>\n"
            f"🎯 {escape(title)}\n"
            f"💰 Reward: <b>{reward:.2f} ETB</b>\n"
            f"🔗 {escape(link)}",
            parse_mode="HTML",
            reply_markup=admin_back_keyboard(),
        )


# =========================================================
# ADMIN BROADCAST
# =========================================================

async def start_broadcast(query, context):

    if not is_admin(query.from_user.id):

        return await query.answer(
            "⛔ Admin only.",
            show_alert=True,
        )

    context.user_data["admin_action"] = "broadcast"

    await query.edit_message_text(
        "📢 <b>Broadcast</b>\n\n"
        "ለሁሉም users የሚላከውን message ይላኩ።",
        reply_markup=admin_back_keyboard(),
        parse_mode="HTML",
    )


async def do_broadcast(update, context):

    if not is_admin(update.effective_user.id):

        return

    text = update.message.text

    with db() as conn:

        users = conn.execute("""
            SELECT telegram_id
            FROM users
            WHERE banned=0
        """).fetchall()

    sent = 0
    failed = 0

    for user in users:

        try:

            await context.bot.send_message(
                chat_id=user["telegram_id"],
                text=text,
            )

            sent += 1

        except Exception:

            failed += 1

    context.user_data.clear()

    await update.message.reply_text(
        "📢 <b>Broadcast Finished</b>\n\n"
        f"✅ Sent: <b>{sent}</b>\n"
        f"❌ Failed: <b>{failed}</b>",
        parse_mode="HTML",
        reply_markup=admin_back_keyboard(),
    )


# =========================================================
# MESSAGE HANDLER
# =========================================================

async def message_handler(update, context):

    if not update.message:
        return

    user_id = update.effective_user.id

    user = get_user(user_id)

    if user and user["banned"]:

        await update.message.reply_text(
            "🚫 Your account is banned."
        )

        return

    # ADMIN ACTIONS
    if is_admin(user_id):

        action = context.user_data.get(
            "admin_action"
        )

        if action == "change_reward":

            return await save_new_reward(
                update,
                context,
            )

        if action == "referral_lookup":

            search_text = update.message.text.strip()

            context.user_data.pop(
                "admin_action",
                None,
            )

            # temporary query wrapper
            class DummyQuery:

                def __init__(self, message):
                    self.message = message
                    self.from_user = message.from_user

                async def edit_message_text(
                    self,
                    text,
                    **kwargs
                ):
                    return await self.message.reply_text(
                        text,
                        **kwargs,
                    )

            dummy = DummyQuery(update.message)

            return await search_referral_user(
                dummy,
                context,
                search_text,
            )

        if action in (
            "task_title",
            "task_link",
            "task_reward",
        ):

            return await handle_admin_task_input(
                update,
                context,
            )

        if action == "broadcast":

            return await do_broadcast(
                update,
                context,
            )

    # WALLET
    if context.user_data.get(
        "awaiting_wallet"
    ):

        return await save_wallet(
            update,
            context,
        )

    # WITHDRAW
    if context.user_data.get(
        "awaiting_withdraw"
    ):

        return await process_withdraw_amount(
            update,
            context,
        )


# =========================================================
# START COMMAND
# =========================================================

async def start(update, context):

    if not update.effective_user:
        return

    user = update.effective_user

    update_user_basic(update)

    referrer_id = None

    if context.args:

        arg = context.args[0]

        if arg.startswith("ref_"):

            raw_id = arg.replace(
                "ref_",
                "",
                1,
            )

            if raw_id.isdigit():

                referrer_id = int(raw_id)

    create_user(
        telegram_id=user.id,
        username=user.username,
        first_name=user.first_name,
        referrer_id=referrer_id,
    )

    current = get_user(user.id)

    if current and current["banned"]:

        return await update.message.reply_text(
            "🚫 Your account is banned."
        )

    missing = await missing_channels(
        context.bot,
        user.id,
    )

    if missing:

        return await show_join_required(
            update,
            context,
            missing,
        )

    set_joined_all(
        user.id,
        True,
    )

    process_referral_reward(
        user.id
    )

    await show_home(
        update,
        context,
    )


# =========================================================
# VERIFY
# =========================================================

async def verify_user(query, context):

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

        text = (
            "🔐 <b>Verification</b>\n\n"
            f"❌ Remaining channels: <b>{len(missing)}</b>\n\n"
            "ሁሉንም Join ካደረጉ በኋላ Verify ይጫኑ።"
        )

        await query.edit_message_text(
            text,
            reply_markup=join_keyboard(missing),
            parse_mode="HTML",
        )

        return

    set_joined_all(
        user_id,
        True,
    )

    rewarded = process_referral_reward(
        user_id
    )

    if rewarded:

        try:

            await query.answer(
                "✅ Verified! Referral reward added.",
                show_alert=True,
            )

        except Exception:
            pass

    else:

        try:

            await query.answer(
                "✅ Verification successful!",
                show_alert=True,
            )

        except Exception:
            pass

    await show_home(
        Update(
            update_id=0,
            callback_query=query,
        ),
        context,
    )


# =========================================================
# CALLBACK HANDLER
# =========================================================

async def callback_handler(update, context):

    query = update.callback_query

    if not query:
        return

    data = query.data or ""

    user_id = query.from_user.id

    # Do not answer globally here.
    # Each branch answers when needed.

    # =====================================================
    # USER
    # =====================================================

    if data == "home":

        await query.answer()

        return await show_home(
            update,
            context,
        )

    if data == "verify":

        return await verify_user(
            query,
            context,
        )

    if data == "balance":

        await query.answer()

        return await show_balance(
            query,
            context,
        )

    if data == "referral":

        await query.answer()

        return await show_referral(
            query,
            context,
        )

    if data == "tasks":

        await query.answer()

        return await show_tasks(
            query,
            context,
        )

    if data.startswith("task_"):

        await query.answer()

        task_id = data.replace(
            "task_",
            "",
            1,
        )

        if task_id.isdigit():

            return await show_task_detail(
                query,
                context,
                int(task_id),
            )

    if data.startswith("verify_task_"):

        task_id = data.replace(
            "verify_task_",
            "",
            1,
        )

        if task_id.isdigit():

            return await verify_task(
                query,
                context,
                int(task_id),
            )

    if data == "wallet":

        await query.answer()

        return await show_wallet(
            query,
            context,
        )

    if data == "wallet_cbe":

        await query.answer()

        return await start_wallet_input(
            query,
            context,
            "CBE",
        )

    if data == "wallet_telebirr":

        await query.answer()

        return await start_wallet_input(
            query,
            context,
            "Telebirr",
        )

    if data == "withdraw":

        await query.answer()

        return await show_withdraw(
            query,
            context,
        )

    if data == "support":

        await query.answer()

        return await show_support(
            query,
            context,
        )

    # =====================================================
    # ADMIN
    # =====================================================

    if data == "admin":

        return await show_admin(
            query,
            context,
        )

    if not is_admin(user_id):

        return await query.answer(
            "⛔ Admin only.",
            show_alert=True,
        )

    if data == "admin_stats":

        await query.answer()

        return await show_admin_stats(
            query,
            context,
        )

    if data == "admin_change_reward":

        await query.answer()

        return await start_change_reward(
            query,
            context,
        )

    if data == "admin_referral_lookup":

        await query.answer()

        return await start_referral_lookup(
            query,
            context,
        )

    if data == "admin_suspicious":

        await query.answer()

        return await show_suspicious(
            query,
            context,
        )

    if data == "admin_withdrawals":

        await query.answer()

        return await show_admin_withdrawals(
            query,
            context,
        )

    if data.startswith("withdraw_info_"):

        withdrawal_id = data.replace(
            "withdraw_info_",
            "",
            1,
        )

        if withdrawal_id.isdigit():

            return await show_withdraw_info(
                query,
                context,
                int(withdrawal_id),
            )

    if data.startswith("approve_"):

        withdrawal_id = data.replace(
            "approve_",
            "",
            1,
        )

        if withdrawal_id.isdigit():

            return await approve_withdrawal(
                query,
                context,
                int(withdrawal_id),
            )

    if data.startswith("reject_"):

        withdrawal_id = data.replace(
            "reject_",
            "",
            1,
        )

        if withdrawal_id.isdigit():

            return await reject_withdrawal(
                query,
                context,
                int(withdrawal_id),
            )

    if data == "admin_add_task":

        await query.answer()

        return await start_add_task(
            query,
            context,
        )

    if data == "admin_broadcast":

        await query.answer()

        return await start_broadcast(
            query,
            context,
        )

    await query.answer(
        "Unknown action.",
        show_alert=True,
    )


# =========================================================
# ADMIN COMMAND
# =========================================================

async def admin_command(update, context):

    user_id = update.effective_user.id

    if not is_admin(user_id):

        return await update.message.reply_text(
            "⛔ Admin only."
        )

    await update.message.reply_text(
        "🛠 <b>Global Cash Admin Panel</b>",
        reply_markup=admin_keyboard(),
        parse_mode="HTML",
    )


# =========================================================
# CANCEL
# =========================================================

async def cancel(update, context):

    context.user_data.clear()

    if is_admin(update.effective_user.id):

        await update.message.reply_text(
            "❌ Cancelled.",
            reply_markup=admin_back_keyboard(),
        )

    else:

        await update.message.reply_text(
            "❌ Cancelled.",
            reply_markup=home_keyboard(),
        )


# =========================================================
# ERROR HANDLER
# =========================================================

async def error_handler(update, context):

    logger.error(
        "Unhandled error: %s",
        context.error,
        exc_info=True,
    )


# =========================================================
# MAIN
# =========================================================

def main():

    if not BOT_TOKEN:

        raise RuntimeError(
            "BOT_TOKEN environment variable is missing."
        )

    if not ADMIN_ID:

        raise RuntimeError(
            "ADMIN_ID environment variable is missing."
        )

    init_db()

    application = (
        Application.builder()
        .token(BOT_TOKEN)
        .build()
    )

    application.add_handler(
        CommandHandler(
            "start",
            start,
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
            cancel,
        )
    )

    application.add_handler(
        CallbackQueryHandler(
            callback_handler,
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
        "Global Cash Bot starting..."
    )

    application.run_polling(
        allowed_updates=Update.ALL_TYPES
    )


if __name__ == "__main__":
    main()

import os
import sqlite3
import logging
from datetime import datetime
from html import escape

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
# GLOBAL CASH BOT
# =========================================================

BOT_NAME = "Global Cash Bot"
BOT_USERNAME = "GloballCashh_Bot"
SUPPORT_USERNAME = "AmanM_12"

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_ID_RAW = os.getenv("ADMIN_ID", "").strip()

try:
    ADMIN_ID = int(ADMIN_ID_RAW)
except Exception:
    ADMIN_ID = 0

DB_FILE = "global_cash.db"

CURRENCY = "ETB"
MIN_WITHDRAW = 30.0

# Default referral reward.
# Admin can change this from Admin Panel.
DEFAULT_REFERRAL_REWARD = 1.50

REQUIRED_CHANNELS = [
    "@Sheger_tech1",
    "@EthioVortex1",
    "@ethiocashflow",
    "@AmanIncomeLab",
    "@OnlineIncomeHub07",
    "@Paymentprooff2",
]

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)

logger = logging.getLogger(__name__)


# =========================================================
# DATABASE
# =========================================================

def db():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = db()
    cur = conn.cursor()

    cur.execute("""
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
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS withdrawals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            amount REAL NOT NULL,
            wallet_type TEXT NOT NULL,
            wallet_number TEXT NOT NULL,
            status TEXT DEFAULT 'pending',
            created_at TEXT
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS promotion_tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            description TEXT NOT NULL,
            reward REAL NOT NULL,
            channel TEXT,
            required_posts INTEGER DEFAULT 1,
            status TEXT DEFAULT 'active',
            created_at TEXT
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS promotion_submissions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            proof_file_id TEXT,
            proof_type TEXT,
            status TEXT DEFAULT 'pending',
            created_at TEXT,
            UNIQUE(task_id, user_id)
        )
    """)

    # Migration for older database versions
    existing_columns = {
        row["name"]
        for row in cur.execute("PRAGMA table_info(users)").fetchall()
    }

    if "referral_status" not in existing_columns:
        cur.execute(
            "ALTER TABLE users ADD COLUMN referral_status TEXT DEFAULT 'pending'"
        )

    if "suspicious" not in existing_columns:
        cur.execute(
            "ALTER TABLE users ADD COLUMN suspicious INTEGER DEFAULT 0"
        )

    # Default setting
    cur.execute(
        "INSERT OR IGNORE INTO settings(key, value) VALUES(?, ?)",
        ("referral_reward", str(DEFAULT_REFERRAL_REWARD)),
    )

    # Unique wallet protection
    try:
        cur.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS unique_user_wallet
            ON users(wallet_type, wallet_number)
            WHERE wallet_type IS NOT NULL
            AND wallet_number IS NOT NULL
            AND wallet_number != ''
        """)
    except sqlite3.IntegrityError:
        logger.warning("Existing duplicate wallets found.")

    conn.commit()
    conn.close()


# =========================================================
# SETTINGS
# =========================================================

def get_setting(key, default=None):
    conn = db()
    row = conn.execute(
        "SELECT value FROM settings WHERE key = ?",
        (key,),
    ).fetchone()
    conn.close()

    if not row:
        return default

    return row["value"]


def set_setting(key, value):
    conn = db()
    conn.execute("""
        INSERT INTO settings(key, value)
        VALUES(?, ?)
        ON CONFLICT(key)
        DO UPDATE SET value=excluded.value
    """, (key, str(value)))
    conn.commit()
    conn.close()


def get_referral_reward():
    try:
        return float(get_setting(
            "referral_reward",
            DEFAULT_REFERRAL_REWARD,
        ))
    except Exception:
        return DEFAULT_REFERRAL_REWARD


# =========================================================
# USER FUNCTIONS
# =========================================================

def get_user(user_id):
    conn = db()
    row = conn.execute(
        "SELECT * FROM users WHERE user_id = ?",
        (user_id,),
    ).fetchone()
    conn.close()
    return row


def create_user(user_id, username, first_name, referred_by=None):
    conn = db()

    existing = conn.execute(
        "SELECT user_id, referred_by FROM users WHERE user_id = ?",
        (user_id,),
    ).fetchone()

    if existing:
        conn.close()
        return

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
            created_at
        )
        VALUES(?, ?, ?, 0, ?, 0, 'pending', 0, 0, ?)
    """, (
        user_id,
        username or "",
        first_name or "",
        referred_by,
        datetime.utcnow().isoformat(),
    ))

    conn.commit()
    conn.close()


def update_user_info(user_id, username, first_name):
    conn = db()
    conn.execute("""
        UPDATE users
        SET username = ?, first_name = ?
        WHERE user_id = ?
    """, (
        username or "",
        first_name or "",
        user_id,
    ))
    conn.commit()
    conn.close()


def get_balance(user_id):
    row = get_user(user_id)
    return float(row["balance"]) if row else 0.0


def add_balance(user_id, amount):
    conn = db()
    conn.execute("""
        UPDATE users
        SET balance = balance + ?
        WHERE user_id = ?
    """, (amount, user_id))
    conn.commit()
    conn.close()


def mark_suspicious(user_id):
    conn = db()
    conn.execute(
        "UPDATE users SET suspicious = 1 WHERE user_id = ?",
        (user_id,),
    )
    conn.commit()
    conn.close()


def is_suspicious(user_id):
    row = get_user(user_id)
    return bool(row and row["suspicious"])


def set_joined_all(user_id, value):
    conn = db()
    conn.execute(
        "UPDATE users SET joined_all = ? WHERE user_id = ?",
        (1 if value else 0, user_id),
    )
    conn.commit()
    conn.close()


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
    conn = db()

    row = conn.execute("""
        SELECT user_id
        FROM users
        WHERE wallet_type = ?
        AND wallet_number = ?
        AND user_id != ?
        LIMIT 1
    """, (
        wallet_type,
        wallet_number,
        user_id,
    )).fetchone()

    conn.close()
    return row is not None


def set_wallet(user_id, wallet_type, wallet_number):
    if wallet_exists_for_other_user(
        user_id,
        wallet_type,
        wallet_number,
    ):
        return False

    conn = db()

    try:
        conn.execute("""
            UPDATE users
            SET wallet_type = ?, wallet_number = ?
            WHERE user_id = ?
        """, (
            wallet_type,
            wallet_number,
            user_id,
        ))

        conn.commit()
        conn.close()
        return True

    except sqlite3.IntegrityError:
        conn.rollback()
        conn.close()
        return False


# =========================================================
# REFERRALS
# =========================================================

def get_referral_count(user_id):
    conn = db()

    row = conn.execute("""
        SELECT COUNT(*) AS total
        FROM users
        WHERE referred_by = ?
        AND referral_paid = 1
    """, (user_id,)).fetchone()

    conn.close()

    return int(row["total"])


def get_referred_users(user_id):
    conn = db()

    rows = conn.execute("""
        SELECT user_id, username, first_name,
               referral_paid, joined_all, suspicious
        FROM users
        WHERE referred_by = ?
        ORDER BY created_at ASC
    """, (user_id,)).fetchall()

    conn.close()

    return rows


def process_referral_reward(user_id):
    user = get_user(user_id)

    if not user:
        return None

    referred_by = user["referred_by"]

    if not referred_by:
        return None

    if int(referred_by) == int(user_id):
        mark_suspicious(user_id)
        return None

    if user["referral_paid"]:
        return None

    # Suspicious accounts do not generate referral rewards.
    if is_suspicious(user_id):
        return None

    inviter = get_user(referred_by)

    if not inviter:
        return None

    if is_suspicious(referred_by):
        return None

    reward = get_referral_reward()

    conn = db()

    # Atomic protection against double payment
    cur = conn.execute("""
        UPDATE users
        SET referral_paid = 1,
            referral_status = 'paid'
        WHERE user_id = ?
        AND referral_paid = 0
    """, (user_id,))

    if cur.rowcount != 1:
        conn.rollback()
        conn.close()
        return None

    conn.execute("""
        UPDATE users
        SET balance = balance + ?
        WHERE user_id = ?
    """, (
        reward,
        referred_by,
    ))

    conn.commit()
    conn.close()

    return {
        "inviter_id": referred_by,
        "reward": reward,
    }


# =========================================================
# CHANNEL VERIFICATION
# =========================================================

async def check_channel_membership(bot, user_id, channel):
    try:
        member = await bot.get_chat_member(
            chat_id=channel,
            user_id=user_id,
        )

        return member.status in [
            ChatMemberStatus.MEMBER,
            ChatMemberStatus.ADMINISTRATOR,
            ChatMemberStatus.OWNER,
        ]

    except Exception as e:
        logger.warning(
            "Channel check failed for %s: %s",
            channel,
            e,
        )
        return False


async def get_missing_channels(bot, user_id):
    missing = []

    for channel in REQUIRED_CHANNELS:
        joined = await check_channel_membership(
            bot,
            user_id,
            channel,
        )

        if not joined:
            missing.append(channel)

    return missing


# =========================================================
# KEYBOARDS
# =========================================================

def main_keyboard():
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
                "📋 Tasks",
                callback_data="tasks",
            ),
            InlineKeyboardButton(
                "💸 Withdraw",
                callback_data="withdraw",
            ),
        ],
        [
            InlineKeyboardButton(
                "🏦 Wallet",
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
                callback_data="main_menu",
            )
        ]
    ])


def join_keyboard(missing):
    buttons = []

    for channel in missing:
        username = channel.replace("@", "")

        buttons.append([
            InlineKeyboardButton(
                f"📢 Join {channel}",
                url=f"https://t.me/{username}",
            )
        ])

    buttons.append([
        InlineKeyboardButton(
            "✅ Verify",
            callback_data="verify",
        )
    ])

    return InlineKeyboardMarkup(buttons)


# =========================================================
# START
# =========================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user

    args = context.args

    referred_by = None

    if args:
        try:
            ref_id = int(args[0])

            if ref_id != user.id:
                referred_by = ref_id

        except ValueError:
            pass

    existing = get_user(user.id)

    if not existing:
        create_user(
            user.id,
            user.username,
            user.first_name,
            referred_by,
        )
    else:
        update_user_info(
            user.id,
            user.username,
            user.first_name,
        )

    missing = await get_missing_channels(
        context.bot,
        user.id,
    )

    if missing:
        set_joined_all(user.id, False)

        text = (
            f"🌍 <b>{BOT_NAME}</b>\n\n"
            "Welcome! 👋\n\n"
            "🇪🇹 መጀመሪያ ከታች ያሉትን channels በሙሉ Join አድርግ።\n"
            "Then press <b>Verify</b> to continue.\n\n"
            "📌 Required Channels:"
        )

        await update.message.reply_text(
            text,
            parse_mode="HTML",
            reply_markup=join_keyboard(missing),
        )
        return

    set_joined_all(user.id, True)

    reward_result = process_referral_reward(user.id)

    if reward_result:
        try:
            await context.bot.send_message(
                chat_id=reward_result["inviter_id"],
                text=(
                    "🎉 <b>New Successful Referral!</b>\n\n"
                    f"👤 User: <code>{user.id}</code>\n"
                    f"💰 Reward: <b>{reward_result['reward']:.2f} ETB</b>\n\n"
                    "Your referral reward has been added to your balance."
                ),
                parse_mode="HTML",
            )
        except Exception:
            pass

    await update.message.reply_text(
        f"🎉 <b>Verified Successfully!</b>\n\n"
        f"Welcome to <b>{BOT_NAME}</b> 🌍\n\n"
        "🇪🇹 አካውንትህ ተረጋግጧል።\n"
        "You can now start earning through Referrals and Tasks.",
        parse_mode="HTML",
        reply_markup=main_keyboard(),
    )


# =========================================================
# VERIFY
# =========================================================

async def verify_callback(update, context):
    query = update.callback_query
    await query.answer()

    user = query.from_user

    missing = await get_missing_channels(
        context.bot,
        user.id,
    )

    if missing:
        set_joined_all(user.id, False)

        text = (
            "❌ <b>Verification Failed</b>\n\n"
            "🇪🇹 እስካሁን ያልተቀላቀልካቸው channels አሉ።\n"
            "Please Join all required channels first.\n\n"
            "📌 Missing Channels:"
        )

        await query.edit_message_text(
            text,
            parse_mode="HTML",
            reply_markup=join_keyboard(missing),
        )
        return

    set_joined_all(user.id, True)

    reward_result = process_referral_reward(user.id)

    if reward_result:
        try:
            await context.bot.send_message(
                chat_id=reward_result["inviter_id"],
                text=(
                    "🎉 <b>Successful Referral!</b>\n\n"
                    f"💰 Reward Added: "
                    f"<b>{reward_result['reward']:.2f} ETB</b>\n\n"
                    "Your balance has been updated."
                ),
                parse_mode="HTML",
            )
        except Exception:
            pass

    await query.edit_message_text(
        "✅ <b>Verified Successfully!</b>\n\n"
        "🇪🇹 አሁን በቦቱ ውስጥ መጠቀም ትችላለህ።\n"
        "You can now use all available features.",
        parse_mode="HTML",
        reply_markup=main_keyboard(),
    )


# =========================================================
# MAIN MENU
# =========================================================

async def main_menu_callback(update, context):
    query = update.callback_query
    await query.answer()

    await query.edit_message_text(
        f"🌍 <b>{BOT_NAME}</b>\n\n"
        "🇪🇹 ከታች ያለውን አማራጭ ምረጥ።\n"
        "Choose an option below 👇",
        parse_mode="HTML",
        reply_markup=main_keyboard(),
    )


# =========================================================
# BALANCE
# =========================================================

async def balance_callback(update, context):
    query = update.callback_query
    await query.answer()

    balance = get_balance(query.from_user.id)

    await query.edit_message_text(
        "💰 <b>Your Balance</b>\n\n"
        f"💵 Balance: <b>{balance:.2f} ETB</b>\n\n"
        f"🇪🇹 Minimum Withdrawal: <b>{MIN_WITHDRAW:.2f} ETB</b>\n"
        "Withdrawals are reviewed by Admin.",
        parse_mode="HTML",
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
                    callback_data="main_menu",
                )
            ],
        ]),
    )


# =========================================================
# REFERRAL
# =========================================================

async def referral_callback(update, context):
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id
    count = get_referral_count(user_id)
    reward = get_referral_reward()

    link = (
        f"https://t.me/{BOT_USERNAME}"
        f"?start={user_id}"
    )

    await query.edit_message_text(
        "👥 <b>Referral Program</b>\n\n"
        f"🎯 Successful Referrals: <b>{count}</b>\n"
        f"💰 Current Reward: <b>{reward:.2f} ETB</b> / referral\n\n"
        "🇪🇹 ሰዎችን በReferral Link ጋብዝ።\n"
        "The reward is added after the referred user "
        "joins all required channels and gets verified.\n\n"
        f"🔗 <code>{link}</code>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "📤 Share Link",
                    url=(
                        "https://t.me/share/url"
                        f"?url={link}"
                    ),
                )
            ],
            [
                InlineKeyboardButton(
                    "🔙 Back",
                    callback_data="main_menu",
                )
            ],
        ]),
    )


# =========================================================
# TASKS
# =========================================================

async def tasks_callback(update, context):
    query = update.callback_query
    await query.answer()

    conn = db()

    tasks = conn.execute("""
        SELECT *
        FROM promotion_tasks
        WHERE status = 'active'
        ORDER BY id DESC
    """).fetchall()

    conn.close()

    buttons = []

    text = (
        "📋 <b>Available Tasks</b>\n\n"
        "🇪🇹 ከታች ያሉትን Tasks ሰርተህ Reward ማግኘት ትችላለህ።\n"
        "Complete a task and submit your proof for review.\n\n"
    )

    if not tasks:
        text += (
            "📢 <b>Referral</b>\n"
            f"Invite verified users and earn "
            f"<b>{get_referral_reward():.2f} ETB</b> each.\n\n"
            "More earning tasks will be added soon."
        )
    else:
        for task in tasks[:10]:
            text += (
                f"🎯 <b>Task #{task['id']}</b>\n"
                f"📌 {escape(task['title'])}\n"
                f"💰 Reward: <b>{task['reward']:.2f} ETB</b>\n\n"
            )

            buttons.append([
                InlineKeyboardButton(
                    f"▶️ Task #{task['id']}",
                    callback_data=f"task_{task['id']}",
                )
            ])

        text += (
            f"\n👥 Referral Reward: "
            f"<b>{get_referral_reward():.2f} ETB</b>"
        )

    buttons.append([
        InlineKeyboardButton(
            "🔙 Back",
            callback_data="main_menu",
        )
    ])

    await query.edit_message_text(
        text,
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(buttons),
    )


# =========================================================
# PROMOTION TASK DETAILS
# =========================================================

async def task_details_callback(update, context):
    query = update.callback_query
    await query.answer()

    try:
        task_id = int(query.data.split("_")[1])
    except Exception:
        return

    conn = db()

    task = conn.execute(
        "SELECT * FROM promotion_tasks WHERE id = ?",
        (task_id,),
    ).fetchone()

    existing = conn.execute("""
        SELECT *
        FROM promotion_submissions
        WHERE task_id = ?
        AND user_id = ?
    """, (
        task_id,
        query.from_user.id,
    )).fetchone()

    conn.close()

    if not task:
        await query.edit_message_text(
            "❌ Task not found.",
            reply_markup=back_keyboard(),
        )
        return

    text = (
        f"🎯 <b>{escape(task['title'])}</b>\n\n"
        f"📝 {escape(task['description'])}\n\n"
        f"💰 Reward: <b>{task['reward']:.2f} ETB</b>\n"
        f"📌 Required Posts: <b>{task['required_posts']}</b>\n"
    )

    if task["channel"]:
        text += (
            f"📢 Channel: <b>{escape(task['channel'])}</b>\n"
        )

    if existing:
        text += (
            "\n⏳ <b>Submission Status:</b> "
            f"{escape(existing['status'])}\n"
        )

        await query.edit_message_text(
            text,
            parse_mode="HTML",
            reply_markup=back_keyboard(),
        )
        return

    text += (
        "\n🇪🇹 ስራውን ከጨረስክ በኋላ Screenshot "
        "ወይም Proof ላክ።\n"
        "After completing the task, send your proof here."
    )

    context.user_data["waiting_for_proof_task"] = task_id

    await query.edit_message_text(
        text,
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "❌ Cancel",
                    callback_data="tasks",
                )
            ]
        ]),
    )


# =========================================================
# WALLET
# =========================================================

async def wallet_callback(update, context):
    query = update.callback_query
    await query.answer()

    wallet = get_wallet(query.from_user.id)

    if wallet:
        await query.edit_message_text(
            "🏦 <b>Your Wallet</b>\n\n"
            f"Method: <b>{escape(wallet[0])}</b>\n"
            f"Number: <code>{escape(wallet[1])}</code>\n\n"
            "🇪🇹 ለመቀየር ከፈለግክ ከታች ምረጥ።",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "🏦 Change CBE",
                        callback_data="wallet_cbe",
                    )
                ],
                [
                    InlineKeyboardButton(
                        "📱 Change Telebirr",
                        callback_data="wallet_telebirr",
                    )
                ],
                [
                    InlineKeyboardButton(
                        "🔙 Back",
                        callback_data="main_menu",
                    )
                ],
            ]),
        )
        return

    await query.edit_message_text(
        "🏦 <b>Set Wallet</b>\n\n"
        "🇪🇹 ገንዘብ ለመቀበል CBE ወይም Telebirr wallet "
        "ማስገባት አለብህ።\n\n"
        "Choose your wallet 👇",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([
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
                    callback_data="main_menu",
                )
            ],
        ]),
    )


async def wallet_cbe_callback(update, context):
    query = update.callback_query
    await query.answer()

    context.user_data["wallet_type"] = "CBE"
    context.user_data["waiting_wallet"] = True

    await query.edit_message_text(
        "🏦 <b>CBE Wallet</b>\n\n"
        "🇪🇹 13 digits ያለው CBE account number አስገባ።\n"
        "It must start with <b>1000</b>.\n\n"
        "Example: <code>1000123456789</code>",
        parse_mode="HTML",
        reply_markup=back_keyboard(),
    )


async def wallet_telebirr_callback(update, context):
    query = update.callback_query
    await query.answer()

    context.user_data["wallet_type"] = "Telebirr"
    context.user_data["waiting_wallet"] = True

    await query.edit_message_text(
        "📱 <b>Telebirr Wallet</b>\n\n"
        "🇪🇹 10 digits ያለው Telebirr number አስገባ።\n"
        "It must start with <b>09</b> or <b>07</b>.\n\n"
        "Example: <code>0912345678</code>",
        parse_mode="HTML",
        reply_markup=back_keyboard(),
    )


# =========================================================
# WITHDRAW
# =========================================================

async def withdraw_callback(update, context):
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id

    if is_suspicious(user_id):
        await query.edit_message_text(
            "🔐 <b>Security Review</b>\n\n"
            "🇪🇹 የአካውንትህ ላይ security review ተደርጓል።\n"
            "Withdrawal is temporarily unavailable.\n\n"
            f"💬 Contact Support: @{SUPPORT_USERNAME}",
            parse_mode="HTML",
            reply_markup=back_keyboard(),
        )
        return

    balance = get_balance(user_id)

    if balance < MIN_WITHDRAW:
        await query.edit_message_text(
            "💸 <b>Withdrawal</b>\n\n"
            f"Current Balance: <b>{balance:.2f} ETB</b>\n"
            f"Minimum Withdrawal: <b>{MIN_WITHDRAW:.2f} ETB</b>\n\n"
            "🇪🇹 ተጨማሪ ገንዘብ ለማግኘት referrals ማድረግ ትችላለህ።",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "👥 Referral",
                        callback_data="referral",
                    )
                ],
                [
                    InlineKeyboardButton(
                        "🔙 Back",
                        callback_data="main_menu",
                    )
                ],
            ]),
        )
        return

    wallet = get_wallet(user_id)

    if not wallet:
        await query.edit_message_text(
            "🏦 <b>Wallet Required</b>\n\n"
            "🇪🇹 Withdrawal ከመጠየቅህ በፊት "
            "CBE ወይም Telebirr ማስገባት አለብህ።",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "🏦 Set Wallet",
                        callback_data="wallet",
                    )
                ],
                [
                    InlineKeyboardButton(
                        "🔙 Back",
                        callback_data="main_menu",
                    )
                ],
            ]),
        )
        return

    context.user_data["waiting_withdraw_amount"] = True

    await query.edit_message_text(
        "💸 <b>Withdraw</b>\n\n"
        f"Available Balance: <b>{balance:.2f} ETB</b>\n"
        f"Minimum: <b>{MIN_WITHDRAW:.2f} ETB</b>\n\n"
        "🇪🇹 ማውጣት የምትፈልገውን amount በETB ጻፍ።\n"
        "Enter the amount below.\n\n"
        "Example: <code>30</code>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "❌ Cancel",
                    callback_data="cancel_withdraw",
                )
            ]
        ]),
    )


async def cancel_withdraw_callback(update, context):
    query = update.callback_query
    await query.answer()

    context.user_data.pop(
        "waiting_withdraw_amount",
        None,
    )

    await query.edit_message_text(
        "❌ Withdrawal cancelled.",
        reply_markup=main_keyboard(),
    )


# =========================================================
# SUPPORT
# =========================================================

async def support_callback(update, context):
    query = update.callback_query
    await query.answer()

    text = (
        "🆘 <b>Global Cash Support</b>\n\n"
        "📢 <b>Advertising & Promotion</b>\n\n"
        "📌 Channel Promotion\n"
        "📈 Telegram Growth\n"
        "📣 Group Advertising\n"
        "💼 Business Promotion\n"
        "📱 App & Website Promotion\n"
        "🎯 Custom Promotion\n\n"
        f"👤 Support: @{SUPPORT_USERNAME}\n\n"
        "💰 ለዋጋ እና ለተጨማሪ መረጃ "
        "Contact Support ያድርጉ።"
    )

    await query.edit_message_text(
        text,
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "💬 Contact Support",
                    url=f"https://t.me/{SUPPORT_USERNAME}",
                )
            ],
            [
                InlineKeyboardButton(
                    "🔙 Back",
                    callback_data="main_menu",
                )
            ],
        ]),
    )


# =========================================================
# TEXT MESSAGE HANDLER
# =========================================================

async def text_handler(update, context):
    user = update.effective_user
    text = (update.message.text or "").strip()

    # -------------------------------
    # WALLET
    # -------------------------------

    if context.user_data.get("waiting_wallet"):
        wallet_type = context.user_data.get("wallet_type")

        valid = False

        if wallet_type == "CBE":
            valid = (
                text.isdigit()
                and len(text) == 13
                and text.startswith("1000")
            )

        elif wallet_type == "Telebirr":
            valid = (
                text.isdigit()
                and len(text) == 10
                and (
                    text.startswith("09")
                    or text.startswith("07")
                )
            )

        if not valid:
            await update.message.reply_text(
                "❌ <b>Invalid Wallet Number</b>\n\n"
                "Please check the number and try again.",
                parse_mode="HTML",
            )
            return

        if wallet_exists_for_other_user(
            user.id,
            wallet_type,
            text,
        ):
            mark_suspicious(user.id)

            context.user_data.pop(
                "waiting_wallet",
                None,
            )

            await update.message.reply_text(
                "🔐 <b>Wallet Already Used</b>\n\n"
                "This wallet is already linked to another account.\n"
                "For security reasons your account has been flagged.",
                parse_mode="HTML",
                reply_markup=main_keyboard(),
            )
            return

        saved = set_wallet(
            user.id,
            wallet_type,
            text,
        )

        context.user_data.pop(
            "waiting_wallet",
            None,
        )

        if not saved:
            await update.message.reply_text(
                "❌ Could not save the wallet.\n"
                "Please try again.",
                reply_markup=main_keyboard(),
            )
            return

        await update.message.reply_text(
            "✅ <b>Wallet Saved</b>\n\n"
            f"Method: <b>{wallet_type}</b>\n"
            f"Number: <code>{text}</code>\n\n"
            "🇪🇹 Wallet successfully connected.",
            parse_mode="HTML",
            reply_markup=main_keyboard(),
        )
        return

    # -------------------------------
    # WITHDRAW
    # -------------------------------

    if context.user_data.get("waiting_withdraw_amount"):

        try:
            amount = float(text)
        except ValueError:
            await update.message.reply_text(
                "❌ Please enter a valid number.\n"
                "Example: <code>30</code>",
                parse_mode="HTML",
            )
            return

        if amount < MIN_WITHDRAW:
            await update.message.reply_text(
                f"❌ Minimum Withdrawal is "
                f"<b>{MIN_WITHDRAW:.2f} ETB</b>.",
                parse_mode="HTML",
            )
            return

        balance = get_balance(user.id)

        if amount > balance:
            await update.message.reply_text(
                "❌ <b>Insufficient Balance</b>\n\n"
                f"Your balance: <b>{balance:.2f} ETB</b>",
                parse_mode="HTML",
            )
            return

        if is_suspicious(user.id):
            context.user_data.pop(
                "waiting_withdraw_amount",
                None,
            )

            await update.message.reply_text(
                "🔐 Withdrawal is unavailable "
                "during Security Review.",
                reply_markup=main_keyboard(),
            )
            return

        wallet = get_wallet(user.id)

        if not wallet:
            context.user_data.pop(
                "waiting_withdraw_amount",
                None,
            )

            await update.message.reply_text(
                "🏦 Please set your wallet first.",
                reply_markup=main_keyboard(),
            )
            return

        # Atomic balance deduction
        conn = db()

        cur = conn.execute("""
            UPDATE users
            SET balance = balance - ?
            WHERE user_id = ?
            AND balance >= ?
            AND suspicious = 0
        """, (
            amount,
            user.id,
            amount,
        ))

        if cur.rowcount != 1:
            conn.rollback()
            conn.close()

            await update.message.reply_text(
                "❌ Withdrawal could not be processed.",
                reply_markup=main_keyboard(),
            )
            return

        cur = conn.execute("""
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
            user.id,
            amount,
            wallet[0],
            wallet[1],
            datetime.utcnow().isoformat(),
        ))

        withdrawal_id = cur.lastrowid

        conn.commit()
        conn.close()

        context.user_data.pop(
            "waiting_withdraw_amount",
            None,
        )

        await update.message.reply_text(
            "✅ <b>Withdrawal Request Submitted</b>\n\n"
            f"💰 Amount: <b>{amount:.2f} ETB</b>\n"
            f"🏦 Method: <b>{escape(wallet[0])}</b>\n"
            f"🔢 Wallet: <code>{escape(wallet[1])}</code>\n"
            f"🆔 Request ID: <code>#{withdrawal_id}</code>\n\n"
            "⏳ Status: <b>Pending Review</b>\n"
            "🇪🇹 Admin ጥያቄውን ከመረመረ በኋላ ውጤቱ ይገለጻል።",
            parse_mode="HTML",
            reply_markup=main_keyboard(),
        )

        await notify_admin_withdrawal(
            context,
            withdrawal_id,
            user.id,
            amount,
            wallet,
        )

        return

    # -------------------------------
    # PROMOTION PROOF
    # -------------------------------

    if context.user_data.get("waiting_for_proof_task"):
        task_id = context.user_data["waiting_for_proof_task"]

        await update.message.reply_text(
            "📸 <b>Proof Required</b>\n\n"
            "🇪🇹 Screenshot ወይም photo proof ላክ።\n"
            "Please send a screenshot/photo showing the completed task.",
            parse_mode="HTML",
        )
        return


# =========================================================
# PHOTO PROOF
# =========================================================

async def photo_handler(update, context):
    user = update.effective_user

    task_id = context.user_data.get(
        "waiting_for_proof_task"
    )

    if not task_id:
        return

    photo = update.message.photo[-1]

    conn = db()

    task = conn.execute(
        "SELECT * FROM promotion_tasks WHERE id = ?",
        (task_id,),
    ).fetchone()

    if not task:
        conn.close()

        context.user_data.pop(
            "waiting_for_proof_task",
            None,
        )

        await update.message.reply_text(
            "❌ Task not found.",
            reply_markup=main_keyboard(),
        )
        return

    try:
        conn.execute("""
            INSERT INTO promotion_submissions(
                task_id,
                user_id,
                proof_file_id,
                proof_type,
                status,
                created_at
            )
            VALUES(?, ?, ?, 'photo', 'pending', ?)
        """, (
            task_id,
            user.id,
            photo.file_id,
            datetime.utcnow().isoformat(),
        ))

        submission_id = conn.execute(
            "SELECT last_insert_rowid()"
        ).fetchone()[0]

        conn.commit()
        conn.close()

    except sqlite3.IntegrityError:
        conn.rollback()
        conn.close()

        await update.message.reply_text(
            "⚠️ You already submitted this task.",
            reply_markup=main_keyboard(),
        )
        return

    context.user_data.pop(
        "waiting_for_proof_task",
        None,
    )

    await update.message.reply_text(
        "✅ <b>Proof Submitted</b>\n\n"
        "🇪🇹 Proofህ ተልኳል። Admin review ያደርገዋል።\n"
        "⏳ Status: <b>Pending Review</b>",
        parse_mode="HTML",
        reply_markup=main_keyboard(),
    )

    await notify_admin_proof(
        context,
        submission_id,
        task,
        user,
        photo.file_id,
    )


# =========================================================
# ADMIN CHECK
# =========================================================

def is_admin(user_id):
    return ADMIN_ID != 0 and int(user_id) == int(ADMIN_ID)


# =========================================================
# ADMIN MENU
# =========================================================

def admin_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "📊 Statistics",
                callback_data="admin_stats",
            ),
            InlineKeyboardButton(
                "👥 Users",
                callback_data="admin_users",
            ),
        ],
        [
            InlineKeyboardButton(
                "💸 Withdrawals",
                callback_data="admin_withdrawals",
            ),
            InlineKeyboardButton(
                "📢 Promotion Tasks",
                callback_data="admin_tasks",
            ),
        ],
        [
            InlineKeyboardButton(
                "💰 Referral Reward",
                callback_data="admin_referral_reward",
            )
        ],
    ])


async def admin_command(update, context):
    if not is_admin(update.effective_user.id):
        return

    await update.message.reply_text(
        "🛠 <b>Global Cash Admin Panel</b>\n\n"
        "Manage users, referrals, withdrawals and promotion tasks.",
        parse_mode="HTML",
        reply_markup=admin_keyboard(),
    )


# =========================================================
# ADMIN STATS
# =========================================================

async def admin_stats_callback(update, context):
    query = update.callback_query
    await query.answer()

    if not is_admin(query.from_user.id):
        return

    conn = db()

    total_users = conn.execute(
        "SELECT COUNT(*) FROM users"
    ).fetchone()[0]

    verified_users = conn.execute(
        "SELECT COUNT(*) FROM users WHERE joined_all = 1"
    ).fetchone()[0]

    total_balance = conn.execute(
        "SELECT COALESCE(SUM(balance), 0) FROM users"
    ).fetchone()[0]

    total_referrals = conn.execute("""
        SELECT COUNT(*)
        FROM users
        WHERE referred_by IS NOT NULL
        AND referral_paid = 1
    """).fetchone()[0]

    pending_withdrawals = conn.execute("""
        SELECT COUNT(*)
        FROM withdrawals
        WHERE status = 'pending'
    """).fetchone()[0]

    suspicious = conn.execute("""
        SELECT COUNT(*)
        FROM users
        WHERE suspicious = 1
    """).fetchone()[0]

    conn.close()

    await query.edit_message_text(
        "📊 <b>Admin Statistics</b>\n\n"
        f"👥 Total Users: <b>{total_users}</b>\n"
        f"✅ Verified Users: <b>{verified_users}</b>\n"
        f"💰 Total User Balance: <b>{total_balance:.2f} ETB</b>\n"
        f"🔗 Successful Referrals: <b>{total_referrals}</b>\n"
        f"💸 Pending Withdrawals: <b>{pending_withdrawals}</b>\n"
        f"🔐 Suspicious Accounts: <b>{suspicious}</b>\n\n"
        f"🎯 Referral Reward: "
        f"<b>{get_referral_reward():.2f} ETB</b>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "🔙 Admin Panel",
                    callback_data="admin_panel",
                )
            ]
        ]),
    )


# =========================================================
# ADMIN USERS / REFERRALS
# =========================================================

async def admin_users_callback(update, context):
    query = update.callback_query
    await query.answer()

    if not is_admin(query.from_user.id):
        return

    conn = db()

    users = conn.execute("""
        SELECT user_id, username, first_name,
               balance, referred_by,
               referral_paid, suspicious
        FROM users
        ORDER BY created_at DESC
        LIMIT 20
    """).fetchall()

    conn.close()

    text = "👥 <b>Recent Users</b>\n\n"

    buttons = []

    if not users:
        text += "No users yet."
    else:
        for u in users:
            name = (
                f"@{u['username']}"
                if u["username"]
                else u["first_name"] or str(u["user_id"])
            )

            text += (
                f"👤 <b>{escape(name)}</b>\n"
                f"ID: <code>{u['user_id']}</code>\n"
                f"Balance: <b>{u['balance']:.2f} ETB</b>\n\n"
            )

            buttons.append([
                InlineKeyboardButton(
                    f"👥 Referrals {u['user_id']}",
                    callback_data=f"admin_ref_{u['user_id']}",
                )
            ])

    buttons.append([
        InlineKeyboardButton(
            "🔙 Admin Panel",
            callback_data="admin_panel",
        )
    ])

    await query.edit_message_text(
        text,
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(buttons),
    )


async def admin_referrals_callback(update, context):
    query = update.callback_query
    await query.answer()

    if not is_admin(query.from_user.id):
        return

    try:
        user_id = int(query.data.split("_")[2])
    except Exception:
        return

    user = get_user(user_id)

    if not user:
        return

    referrals = get_referred_users(user_id)

    name = (
        f"@{user['username']}"
        if user["username"]
        else user["first_name"] or str(user_id)
    )

    text = (
        "👥 <b>Referral Details</b>\n\n"
        f"👤 User: <b>{escape(name)}</b>\n"
        f"🆔 ID: <code>{user_id}</code>\n"
        f"💰 Balance: <b>{user['balance']:.2f} ETB</b>\n"
        f"🎯 Successful Referrals: <b>{len([r for r in referrals if r['referral_paid']])}</b>\n\n"
    )

    if not referrals:
        text += "No referred users yet."
    else:
        text += "<b>Referred Users:</b>\n\n"

        for i, ref in enumerate(referrals, 1):
            if ref["username"]:
                ref_name = f"@{ref['username']}"
            else:
                ref_name = ref["first_name"] or "No Username"

            status = (
                "✅ Paid"
                if ref["referral_paid"]
                else "⏳ Pending"
            )

            text += (
                f"{i}. <b>{escape(ref_name)}</b>\n"
                f"   ID: <code>{ref['user_id']}</code>\n"
                f"   Status: {status}\n"
            )

            if ref["suspicious"]:
                text += "   🔐 Suspicious\n"

            text += "\n"

    await query.edit_message_text(
        text,
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "🔙 Users",
                    callback_data="admin_users",
                )
            ],
            [
                InlineKeyboardButton(
                    "🛠 Admin Panel",
                    callback_data="admin_panel",
                )
            ],
        ]),
    )


# =========================================================
# ADMIN REFERRAL REWARD
# =========================================================

async def admin_referral_reward_callback(update, context):
    query = update.callback_query
    await query.answer()

    if not is_admin(query.from_user.id):
        return

    reward = get_referral_reward()

    context.user_data["admin_changing_reward"] = True

    await query.edit_message_text(
        "💰 <b>Referral Reward Settings</b>\n\n"
        f"Current Reward: <b>{reward:.2f} ETB</b>\n\n"
        "Enter the new amount.\n"
        "Example: <code>1.00</code>\n"
        "Example: <code>0.50</code>\n"
        "Example: <code>1.50</code>\n\n"
        "🇪🇹 ይህ ለአዲስ successful referrals ብቻ ይተገበራል።\n"
        "Previously paid rewards will not change.",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "❌ Cancel",
                    callback_data="admin_panel",
                )
            ]
        ]),
    )


# =========================================================
# ADMIN WITHDRAWALS
# =========================================================

async def admin_withdrawals_callback(update, context):
    query = update.callback_query
    await query.answer()

    if not is_admin(query.from_user.id):
        return

    conn = db()

    withdrawals = conn.execute("""
        SELECT *
        FROM withdrawals
        WHERE status = 'pending'
        ORDER BY id ASC
        LIMIT 20
    """).fetchall()

    conn.close()

    if not withdrawals:
        await query.edit_message_text(
            "💸 <b>Pending Withdrawals</b>\n\n"
            "✅ No pending withdrawals.",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "🔙 Admin Panel",
                        callback_data="admin_panel",
                    )
                ]
            ]),
        )
        return

    buttons = []
    text = "💸 <b>Pending Withdrawals</b>\n\n"

    for w in withdrawals:
        text += (
            f"🆔 <b>#{w['id']}</b>\n"
            f"👤 User: <code>{w['user_id']}</code>\n"
            f"💰 Amount: <b>{w['amount']:.2f} ETB</b>\n"
            f"🏦 {escape(w['wallet_type'])}: "
            f"<code>{escape(w['wallet_number'])}</code>\n\n"
        )

        buttons.append([
            InlineKeyboardButton(
                f"🔎 Review #{w['id']}",
                callback_data=f"admin_wd_{w['id']}",
            )
        ])

    buttons.append([
        InlineKeyboardButton(
            "🔙 Admin Panel",
            callback_data="admin_panel",
        )
    ])

    await query.edit_message_text(
        text,
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(buttons),
    )


async def admin_withdrawal_review_callback(update, context):
    query = update.callback_query
    await query.answer()

    if not is_admin(query.from_user.id):
        return

    try:
        withdrawal_id = int(query.data.split("_")[2])
    except Exception:
        return

    conn = db()

    w = conn.execute(
        "SELECT * FROM withdrawals WHERE id = ?",
        (withdrawal_id,),
    ).fetchone()

    conn.close()

    if not w:
        await query.edit_message_text(
            "❌ Withdrawal not found.",
            reply_markup=back_keyboard(),
        )
        return

    text = (
        "💸 <b>Withdrawal Review</b>\n\n"
        f"🆔 Request: <code>#{w['id']}</code>\n"
        f"👤 User ID: <code>{w['user_id']}</code>\n"
        f"💰 Amount: <b>{w['amount']:.2f} ETB</b>\n"
        f"🏦 Method: <b>{escape(w['wallet_type'])}</b>\n"
        f"🔢 Wallet: <code>{escape(w['wallet_number'])}</code>\n"
        f"⏳ Status: <b>{escape(w['status'])}</b>"
    )

    buttons = []

    if w["status"] == "pending":
        buttons.append([
            InlineKeyboardButton(
                "✅ Approve",
                callback_data=f"wd_approve_{w['id']}",
            ),
            InlineKeyboardButton(
                "❌ Reject",
                callback_data=f"wd_reject_{w['id']}",
            ),
        ])

    buttons.append([
        InlineKeyboardButton(
            "🔙 Pending Withdrawals",
            callback_data="admin_withdrawals",
        )
    ])

    await query.edit_message_text(
        text,
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(buttons),
    )


# =========================================================
# APPROVE / REJECT WITHDRAWAL
# =========================================================

async def withdrawal_decision_callback(update, context):
    query = update.callback_query
    await query.answer()

    if not is_admin(query.from_user.id):
        return

    parts = query.data.split("_")

    if len(parts) != 3:
        return

    action = parts[1]

    try:
        withdrawal_id = int(parts[2])
    except Exception:
        return

    conn = db()

    w = conn.execute(
        "SELECT * FROM withdrawals WHERE id = ?",
        (withdrawal_id,),
    ).fetchone()

    if not w:
        conn.close()
        return

    if w["status"] != "pending":
        conn.close()

        await query.edit_message_text(
            "⚠️ This withdrawal has already been processed.",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "🔙 Withdrawals",
                        callback_data="admin_withdrawals",
                    )
                ]
            ]),
        )
        return

    if action == "approve":

        cur = conn.execute("""
            UPDATE withdrawals
            SET status = 'approved'
            WHERE id = ?
            AND status = 'pending'
        """, (withdrawal_id,))

        conn.commit()
        conn.close()

        if cur.rowcount != 1:
            return

        await query.edit_message_text(
            "✅ <b>Withdrawal Approved</b>\n\n"
            f"Request: <code>#{withdrawal_id}</code>\n"
            f"Amount: <b>{w['amount']:.2f} ETB</b>",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "🔙 Withdrawals",
                        callback_data="admin_withdrawals",
                    )
                ]
            ]),
        )

        try:
            await context.bot.send_message(
                chat_id=w["user_id"],
                text=(
                    "✅ <b>Withdrawal Approved</b>\n\n"
                    f"💰 Amount: <b>{w['amount']:.2f} ETB</b>\n"
                    f"🆔 Request: <code>#{withdrawal_id}</code>\n\n"
                    "🇪🇹 የWithdrawal ጥያቄህ Approved ተደርጓል።"
                ),
                parse_mode="HTML",
            )
        except Exception:
            pass

    elif action == "reject":

        cur = conn.execute("""
            UPDATE withdrawals
            SET status = 'rejected'
            WHERE id = ?
            AND status = 'pending'
        """, (withdrawal_id,))

        if cur.rowcount != 1:
            conn.rollback()
            conn.close()
            return

        # Return the amount to user's balance.
        conn.execute("""
            UPDATE users
            SET balance = balance + ?
            WHERE user_id = ?
        """, (
            w["amount"],
            w["user_id"],
        ))

        conn.commit()
        conn.close()

        await query.edit_message_text(
            "❌ <b>Withdrawal Rejected</b>\n\n"
            f"Request: <code>#{withdrawal_id}</code>\n"
            f"Amount returned: <b>{w['amount']:.2f} ETB</b>",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "🔙 Withdrawals",
                        callback_data="admin_withdrawals",
                    )
                ]
            ]),
        )

        try:
            await context.bot.send_message(
                chat_id=w["user_id"],
                text=(
                    "❌ <b>Withdrawal Rejected</b>\n\n"
                    f"💰 Amount Returned: "
                    f"<b>{w['amount']:.2f} ETB</b>\n"
                    f"🆔 Request: <code>#{withdrawal_id}</code>\n\n"
                    "🇪🇹 ገንዘቡ ወደ Balance ተመልሷል።"
                ),
                parse_mode="HTML",
            )
        except Exception:
            pass


# =========================================================
# ADMIN PROMOTION TASKS
# =========================================================

async def admin_tasks_callback(update, context):
    query = update.callback_query
    await query.answer()

    if not is_admin(query.from_user.id):
        return

    conn = db()

    tasks = conn.execute("""
        SELECT *
        FROM promotion_tasks
        ORDER BY id DESC
        LIMIT 20
    """).fetchall()

    conn.close()

    text = "📢 <b>Promotion Tasks</b>\n\n"

    buttons = []

    if not tasks:
        text += "No promotion tasks created yet."
    else:
        for task in tasks:
            text += (
                f"🎯 <b>#{task['id']}</b> "
                f"{escape(task['title'])}\n"
                f"💰 {task['reward']:.2f} ETB\n"
                f"📌 Status: {escape(task['status'])}\n\n"
            )

            buttons.append([
                InlineKeyboardButton(
                    f"👁 Review #{task['id']}",
                    callback_data=f"admin_task_{task['id']}",
                )
            ])

    buttons.append([
        InlineKeyboardButton(
            "➕ Create Task",
            callback_data="admin_create_task",
        )
    ])

    buttons.append([
        InlineKeyboardButton(
            "📸 Pending Proofs",
            callback_data="admin_proofs",
        )
    ])

    buttons.append([
        InlineKeyboardButton(
            "🔙 Admin Panel",
            callback_data="admin_panel",
        )
    ])

    await query.edit_message_text(
        text,
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(buttons),
    )


# =========================================================
# CREATE PROMOTION TASK
# =========================================================

async def admin_create_task_callback(update, context):
    query = update.callback_query
    await query.answer()

    if not is_admin(query.from_user.id):
        return

    context.user_data["creating_task_step"] = "title"

    await query.edit_message_text(
        "➕ <b>Create Promotion Task</b>\n\n"
        "Step 1/4\n\n"
        "Enter the Task Title.\n"
        "Example: <code>Telegram Channel Promotion</code>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "❌ Cancel",
                    callback_data="admin_tasks",
                )
            ]
        ]),
    )


# =========================================================
# ADMIN PROOFS
# =========================================================

async def admin_proofs_callback(update, context):
    query = update.callback_query
    await query.answer()

    if not is_admin(query.from_user.id):
        return

    conn = db()

    submissions = conn.execute("""
        SELECT
            ps.*,
            pt.title,
            pt.reward,
            pt.description
        FROM promotion_submissions ps
        JOIN promotion_tasks pt
        ON pt.id = ps.task_id
        WHERE ps.status = 'pending'
        ORDER BY ps.id ASC
        LIMIT 20
    """).fetchall()

    conn.close()

    text = "📸 <b>Pending Promotion Proofs</b>\n\n"

    buttons = []

    if not submissions:
        text += "✅ No pending proofs."
    else:
        for s in submissions:
            text += (
                f"🆔 Submission: <b>#{s['id']}</b>\n"
                f"👤 User: <code>{s['user_id']}</code>\n"
                f"🎯 Task: {escape(s['title'])}\n"
                f"💰 Reward: <b>{s['reward']:.2f} ETB</b>\n\n"
            )

            buttons.append([
                InlineKeyboardButton(
                    f"🔎 Review Proof #{s['id']}",
                    callback_data=f"admin_proof_{s['id']}",
                )
            ])

    buttons.append([
        InlineKeyboardButton(
            "🔙 Admin Tasks",
            callback_data="admin_tasks",
        )
    ])

    await query.edit_message_text(
        text,
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(buttons),
    )


async def admin_proof_review_callback(update, context):
    query = update.callback_query
    await query.answer()

    if not is_admin(query.from_user.id):
        return

    try:
        submission_id = int(query.data.split("_")[2])
    except Exception:
        return

    conn = db()

    s = conn.execute("""
        SELECT
            ps.*,
            pt.title,
            pt.reward
        FROM promotion_submissions ps
        JOIN promotion_tasks pt
        ON pt.id = ps.task_id
        WHERE ps.id = ?
    """, (submission_id,)).fetchone()

    conn.close()

    if not s:
        return

    text = (
        "📸 <b>Proof Review</b>\n\n"
        f"Submission: <code>#{s['id']}</code>\n"
        f"User: <code>{s['user_id']}</code>\n"
        f"Task: <b>{escape(s['title'])}</b>\n"
        f"Reward: <b>{s['reward']:.2f} ETB</b>\n"
        f"Status: <b>{escape(s['status'])}</b>\n\n"
        "The proof has been sent to Admin.\n"
        "Check the screenshot/photo above before deciding."
    )

    buttons = []

    if s["status"] == "pending":
        buttons.append([
            InlineKeyboardButton(
                "✅ Approve",
                callback_data=f"proof_approve_{s['id']}",
            ),
            InlineKeyboardButton(
                "❌ Reject",
                callback_data=f"proof_reject_{s['id']}",
            ),
        ])

    buttons.append([
        InlineKeyboardButton(
            "🔙 Pending Proofs",
            callback_data="admin_proofs",
        )
    ])

    await query.message.reply_photo(
        photo=s["proof_file_id"],
        caption=text,
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(buttons),
    )


# =========================================================
# PROOF APPROVE / REJECT
# =========================================================

async def proof_decision_callback(update, context):
    query = update.callback_query
    await query.answer()

    if not is_admin(query.from_user.id):
        return

    parts = query.data.split("_")

    if len(parts) != 3:
        return

    action = parts[1]

    try:
        submission_id = int(parts[2])
    except Exception:
        return

    conn = db()

    s = conn.execute("""
        SELECT
            ps.*,
            pt.reward,
            pt.title
        FROM promotion_submissions ps
        JOIN promotion_tasks pt
        ON pt.id = ps.task_id
        WHERE ps.id = ?
    """, (submission_id,)).fetchone()

    if not s:
        conn.close()
        return

    if s["status"] != "pending":
        conn.close()

        await query.message.reply_text(
            "⚠️ This proof has already been processed."
        )
        return

    if action == "approve":

        cur = conn.execute("""
            UPDATE promotion_submissions
            SET status = 'approved'
            WHERE id = ?
            AND status = 'pending'
        """, (submission_id,))

        if cur.rowcount != 1:
            conn.rollback()
            conn.close()
            return

        conn.execute("""
            UPDATE users
            SET balance = balance + ?
            WHERE user_id = ?
        """, (
            s["reward"],
            s["user_id"],
        ))

        conn.commit()
        conn.close()

        await query.message.reply_text(
            "✅ <b>Proof Approved</b>\n\n"
            f"💰 Reward Added: <b>{s['reward']:.2f} ETB</b>",
            parse_mode="HTML",
        )

        try:
            await context.bot.send_message(
                chat_id=s["user_id"],
                text=(
                    "🎉 <b>Promotion Task Approved!</b>\n\n"
                    f"🎯 Task: <b>{escape(s['title'])}</b>\n"
                    f"💰 Reward: <b>{s['reward']:.2f} ETB</b>\n\n"
                    "Your reward has been added to Balance."
                ),
                parse_mode="HTML",
            )
        except Exception:
            pass

    elif action == "reject":

        cur = conn.execute("""
            UPDATE promotion_submissions
            SET status = 'rejected'
            WHERE id = ?
            AND status = 'pending'
        """, (submission_id,))

        conn.commit()
        conn.close()

        if cur.rowcount != 1:
            return

        await query.message.reply_text(
            "❌ <b>Proof Rejected</b>\n\n"
            "No reward was added.",
            parse_mode="HTML",
        )

        try:
            await context.bot.send_message(
                chat_id=s["user_id"],
                text=(
                    "❌ <b>Promotion Proof Rejected</b>\n\n"
                    f"🎯 Task: <b>{escape(s['title'])}</b>\n\n"
                    "🇪🇹 Proofህ አልተፈቀደም። "
                    "ለሚቀጥለው task መመሪያውን በጥንቃቄ ተከተል።"
                ),
                parse_mode="HTML",
            )
        except Exception:
            pass


# =========================================================
# ADMIN PANEL CALLBACK
# =========================================================

async def admin_panel_callback(update, context):
    query = update.callback_query
    await query.answer()

    if not is_admin(query.from_user.id):
        return

    await query.edit_message_text(
        "🛠 <b>Global Cash Admin Panel</b>\n\n"
        "Choose an option 👇",
        parse_mode="HTML",
        reply_markup=admin_keyboard(),
    )


# =========================================================
# ADMIN TEXT INPUT
# =========================================================

async def admin_text_handler(update, context):
    user = update.effective_user

    if not is_admin(user.id):
        return

    text = (update.message.text or "").strip()

    # -------------------------------
    # CHANGE REFERRAL REWARD
    # -------------------------------

    if context.user_data.get("admin_changing_reward"):

        try:
            value = float(text)
        except ValueError:
            await update.message.reply_text(
                "❌ Enter a valid number.\n"
                "Example: <code>1.50</code>",
                parse_mode="HTML",
            )
            return

        if value < 0:
            await update.message.reply_text(
                "❌ Reward cannot be negative."
            )
            return

        if value > 1000:
            await update.message.reply_text(
                "❌ Please use a reasonable reward amount."
            )
            return

        set_setting(
            "referral_reward",
            value,
        )

        context.user_data.pop(
            "admin_changing_reward",
            None,
        )

        await update.message.reply_text(
            "✅ <b>Referral Reward Updated</b>\n\n"
            f"New Reward: <b>{value:.2f} ETB</b>\n\n"
            "This new rate will apply to future successful referrals.",
            parse_mode="HTML",
            reply_markup=admin_keyboard(),
        )
        return

    # -------------------------------
    # CREATE TASK
    # -------------------------------

    step = context.user_data.get(
        "creating_task_step"
    )

    if not step:
        return

    if step == "title":
        context.user_data["task_title"] = text
        context.user_data["creating_task_step"] = "description"

        await update.message.reply_text(
            "Step 2/4\n\n"
            "Enter the task description/instructions.",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "❌ Cancel",
                        callback_data="admin_tasks",
                    )
                ]
            ]),
        )
        return

    if step == "description":
        context.user_data["task_description"] = text
        context.user_data["creating_task_step"] = "reward"

        await update.message.reply_text(
            "Step 3/4\n\n"
            "Enter the reward in ETB.\n"
            "Example: <code>5</code>",
            parse_mode="HTML",
        )
        return

    if step == "reward":

        try:
            reward = float(text)
        except ValueError:
            await update.message.reply_text(
                "❌ Enter a valid reward amount."
            )
            return

        if reward <= 0:
            await update.message.reply_text(
                "❌ Reward must be greater than 0."
            )
            return

        context.user_data["task_reward"] = reward
        context.user_data["creating_task_step"] = "channel"

        await update.message.reply_text(
            "Step 4/4\n\n"
            "Enter the Telegram channel username.\n"
            "Example: <code>@ExampleChannel</code>\n\n"
            "If no channel is needed, type <code>none</code>.",
            parse_mode="HTML",
        )
        return

    if step == "channel":

        channel = text

        if channel.lower() == "none":
            channel = ""

        title = context.user_data.get("task_title", "")
        description = context.user_data.get(
            "task_description",
            "",
        )
        reward = context.user_data.get(
            "task_reward",
            0,
        )

        conn = db()

        conn.execute("""
            INSERT INTO promotion_tasks(
                title,
                description,
                reward,
                channel,
                required_posts,
                status,
                created_at
            )
            VALUES(?, ?, ?, ?, 1, 'active', ?)
        """, (
            title,
            description,
            reward,
            channel,
            datetime.utcnow().isoformat(),
        ))

        conn.commit()
        conn.close()

        for key in [
            "creating_task_step",
            "task_title",
            "task_description",
            "task_reward",
        ]:
            context.user_data.pop(key, None)

        await update.message.reply_text(
            "✅ <b>Promotion Task Created</b>\n\n"
            f"🎯 {escape(title)}\n"
            f"💰 Reward: <b>{reward:.2f} ETB</b>\n"
            f"📢 Channel: <b>{escape(channel or 'None')}</b>",
            parse_mode="HTML",
            reply_markup=admin_keyboard(),
        )


# =========================================================
# ADMIN NOTIFICATION - WITHDRAWAL
# =========================================================

async def notify_admin_withdrawal(
    context,
    withdrawal_id,
    user_id,
    amount,
    wallet,
):
    if ADMIN_ID == 0:
        return

    try:
        await context.bot.send_message(
            chat_id=ADMIN_ID,
            text=(
                "💳 <b>New Withdrawal</b>\n\n"
                f"🆔 Request: <code>#{withdrawal_id}</code>\n"
                f"👤 User ID: <code>{user_id}</code>\n"
                f"💰 Amount: <b>{amount:.2f} ETB</b>\n"
                f"🏦 Method: <b>{escape(wallet[0])}</b>\n"
                f"🔢 Wallet: <code>{escape(wallet[1])}</code>\n\n"
                "⏳ Status: <b>Pending</b>"
            ),
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "🔎 Review",
                        callback_data=f"admin_wd_{withdrawal_id}",
                    )
                ]
            ]),
        )
    except Exception as e:
        logger.error(
            "Admin withdrawal notification failed: %s",
            e,
        )


# =========================================================
# ADMIN NOTIFICATION - PROOF
# =========================================================

async def notify_admin_proof(
    context,
    submission_id,
    task,
    user,
    file_id,
):
    if ADMIN_ID == 0:
        return

    try:
        await context.bot.send_photo(
            chat_id=ADMIN_ID,
            photo=file_id,
            caption=(
                "📸 <b>New Promotion Proof</b>\n\n"
                f"🆔 Submission: <code>#{submission_id}</code>\n"
                f"👤 User ID: <code>{user.id}</code>\n"
                f"🎯 Task: <b>{escape(task['title'])}</b>\n"
                f"💰 Reward: <b>{task['reward']:.2f} ETB</b>\n\n"
                "⏳ Status: <b>Pending Review</b>"
            ),
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "🔎 Review",
                        callback_data=f"admin_proof_{submission_id}",
                    )
                ]
            ]),
        )
    except Exception as e:
        logger.error(
            "Admin proof notification failed: %s",
            e,
        )


# =========================================================
# CANCEL COMMAND
# =========================================================

async def cancel_command(update, context):
    context.user_data.clear()

    await update.message.reply_text(
        "❌ <b>Cancelled</b>\n\n"
        "🇪🇹 ሂደቱ ተሰርዟል።\n"
        "You are back to the main menu.",
        parse_mode="HTML",
        reply_markup=main_keyboard(),
    )


# =========================================================
# ERROR HANDLER
# =========================================================

async def error_handler(update, context):
    logger.error(
        "Exception while handling update:",
        exc_info=context.error,
    )


# =========================================================
# CALLBACK ROUTER
# =========================================================

async def callback_router(update, context):
    query = update.callback_query

    if not query:
        return

    data = query.data

    if data == "main_menu":
        await main_menu_callback(update, context)

    elif data == "verify":
        await verify_callback(update, context)

    elif data == "balance":
        await balance_callback(update, context)

    elif data == "referral":
        await referral_callback(update, context)

    elif data == "tasks":
        await tasks_callback(update, context)

    elif data.startswith("task_"):
        await task_details_callback(update, context)

    elif data == "wallet":
        await wallet_callback(update, context)

    elif data == "wallet_cbe":
        await wallet_cbe_callback(update, context)

    elif data == "wallet_telebirr":
        await wallet_telebirr_callback(update, context)

    elif data == "withdraw":
        await withdraw_callback(update, context)

    elif data == "cancel_withdraw":
        await cancel_withdraw_callback(update, context)

    elif data == "support":
        await support_callback(update, context)

    # ADMIN
    elif data == "admin_panel":
        await admin_panel_callback(update, context)

    elif data == "admin_stats":
        await admin_stats_callback(update, context)

    elif data == "admin_users":
        await admin_users_callback(update, context)

    elif data.startswith("admin_ref_"):
        await admin_referrals_callback(update, context)

    elif data == "admin_referral_reward":
        await admin_referral_reward_callback(update, context)

    elif data == "admin_withdrawals":
        await admin_withdrawals_callback(update, context)

    elif data.startswith("admin_wd_"):
        await admin_withdrawal_review_callback(update, context)

    elif data.startswith("wd_approve_") or data.startswith("wd_reject_"):
        await withdrawal_decision_callback(update, context)

    elif data == "admin_tasks":
        await admin_tasks_callback(update, context)

    elif data == "admin_create_task":
        await admin_create_task_callback(update, context)

    elif data == "admin_proofs":
        await admin_proofs_callback(update, context)

    elif data.startswith("admin_proof_"):
        await admin_proof_review_callback(update, context)

    elif data.startswith("proof_approve_") or data.startswith("proof_reject_"):
        await proof_decision_callback(update, context)


# =========================================================
# MAIN
# =========================================================

def main():
    if not BOT_TOKEN:
        raise RuntimeError(
            "BOT_TOKEN environment variable is missing."
        )

    if ADMIN_ID == 0:
        logger.warning(
            "ADMIN_ID is missing or invalid."
        )

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
        CommandHandler("cancel", cancel_command)
    )

    application.add_handler(
        CommandHandler("admin", admin_command)
    )

    # Callback buttons
    application.add_handler(
        CallbackQueryHandler(callback_router)
    )

    # Photo proof
    application.add_handler(
        MessageHandler(
            filters.PHOTO,
            photo_handler,
        )
    )

    # Text
    application.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            text_handler,
        )
    )

    application.add_error_handler(
        error_handler
    )

    logger.info(
        "%s started successfully.",
        BOT_NAME,
    )

    application.run_polling(
        allowed_updates=Update.ALL_TYPES
    )


if __name__ == "__main__":
    main()

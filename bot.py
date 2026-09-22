import os
import re
import sqlite3
import logging
import math
from contextlib import contextmanager
from datetime import datetime, timezone
from html import escape

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ChatMemberStatus,
)
from telegram.error import TelegramError, BadRequest
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
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    with db() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT DEFAULT '',
            first_name TEXT DEFAULT '',
            balance REAL DEFAULT 0,
            referred_by INTEGER DEFAULT NULL,
            referral_paid INTEGER DEFAULT 0,
            referral_status TEXT DEFAULT 'pending',
            joined_all INTEGER DEFAULT 0,
            suspicious INTEGER DEFAULT 0,
            is_banned INTEGER DEFAULT 0,
            wallet_type TEXT DEFAULT NULL,
            wallet_number TEXT DEFAULT NULL,
            created_at TEXT
        );

        CREATE TABLE IF NOT EXISTS referrals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            inviter_id INTEGER,
            invited_id INTEGER UNIQUE,
            reward REAL DEFAULT 0,
            status TEXT DEFAULT 'pending',
            created_at TEXT
        );

        CREATE TABLE IF NOT EXISTS withdrawals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            amount REAL,
            wallet_type TEXT,
            wallet_number TEXT,
            status TEXT DEFAULT 'pending',
            created_at TEXT,
            processed_at TEXT
        );

        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        );

        CREATE TABLE IF NOT EXISTS tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT,
            description TEXT,
            reward REAL,
            channel TEXT,
            link TEXT,
            created_at TEXT
        );

        CREATE TABLE IF NOT EXISTS user_tasks (
            user_id INTEGER,
            task_id INTEGER,
            paid INTEGER DEFAULT 0,
            created_at TEXT,
            PRIMARY KEY(user_id, task_id)
        );

        CREATE UNIQUE INDEX IF NOT EXISTS idx_wallet_unique
        ON users(wallet_type, wallet_number);
        """)

        conn.execute("""
            INSERT OR IGNORE INTO settings(key, value)
            VALUES('referral_reward', '2.00')
        """)


# =========================================================
# BASIC DB HELPERS
# =========================================================

def now():
    return datetime.now(timezone.utc).isoformat()


def get_user(user_id):
    with db() as conn:
        return conn.execute(
            "SELECT * FROM users WHERE user_id=?",
            (user_id,),
        ).fetchone()


def create_or_update_user(user, referred_by=None):
    existing = get_user(user.id)

    with db() as conn:
        if existing:
            conn.execute("""
                UPDATE users
                SET username=?, first_name=?
                WHERE user_id=?
            """, (
                user.username or "",
                user.first_name or "",
                user.id,
            ))
        else:
            conn.execute("""
                INSERT INTO users(
                    user_id,
                    username,
                    first_name,
                    referred_by,
                    created_at
                )
                VALUES(?,?,?,?,?)
            """, (
                user.id,
                user.username or "",
                user.first_name or "",
                referred_by,
                now(),
            ))

            if referred_by and referred_by != user.id:
                conn.execute("""
                    INSERT OR IGNORE INTO referrals(
                        inviter_id,
                        invited_id,
                        reward,
                        status,
                        created_at
                    )
                    VALUES(?,?,?,?,?)
                """, (
                    referred_by,
                    user.id,
                    0,
                    "pending",
                    now(),
                ))


def is_banned(user_id):
    user = get_user(user_id)
    return bool(user and user["is_banned"])


def is_suspicious(user_id):
    user = get_user(user_id)
    return bool(user and user["suspicious"])


def get_balance(user_id):
    user = get_user(user_id)
    return float(user["balance"]) if user else 0.0


def add_balance(user_id, amount):
    with db() as conn:
        conn.execute("""
            UPDATE users
            SET balance = balance + ?
            WHERE user_id=?
        """, (amount, user_id))


def get_setting(key, default=None):
    with db() as conn:
        row = conn.execute(
            "SELECT value FROM settings WHERE key=?",
            (key,),
        ).fetchone()

    return row["value"] if row else default


def set_setting(key, value):
    with db() as conn:
        conn.execute("""
            INSERT INTO settings(key,value)
            VALUES(?,?)
            ON CONFLICT(key)
            DO UPDATE SET value=excluded.value
        """, (key, str(value)))


def get_referral_reward():
    try:
        return float(get_setting("referral_reward", "2.00"))
    except Exception:
        return 2.0


def get_referral_count(user_id):
    with db() as conn:
        row = conn.execute("""
            SELECT COUNT(*) AS c
            FROM referrals
            WHERE inviter_id=? AND status='paid'
        """, (user_id,)).fetchone()

    return int(row["c"])


# =========================================================
# WALLET
# =========================================================

def get_wallet(user_id):
    user = get_user(user_id)

    if not user:
        return None, None

    return user["wallet_type"], user["wallet_number"]


def save_wallet(user_id, wallet_type, wallet_number):
    with db() as conn:
        exists = conn.execute("""
            SELECT user_id
            FROM users
            WHERE wallet_type=?
            AND wallet_number=?
            AND user_id != ?
        """, (
            wallet_type,
            wallet_number,
            user_id,
        )).fetchone()

        if exists:
            return False

        conn.execute("""
            UPDATE users
            SET wallet_type=?, wallet_number=?
            WHERE user_id=?
        """, (
            wallet_type,
            wallet_number,
            user_id,
        ))

    return True


# =========================================================
# REFERRAL
# =========================================================

def process_referral_reward(user_id):
    user = get_user(user_id)

    if not user:
        return False

    if user["referral_paid"]:
        return False

    if not user["referred_by"]:
        return False

    if user["suspicious"] or user["is_banned"]:
        return False

    inviter_id = int(user["referred_by"])

    inviter = get_user(inviter_id)

    if not inviter:
        return False

    if inviter["is_banned"] or inviter["suspicious"]:
        return False

    reward = get_referral_reward()

    with db() as conn:
        referral = conn.execute("""
            SELECT *
            FROM referrals
            WHERE invited_id=?
            LIMIT 1
        """, (user_id,)).fetchone()

        if referral and referral["status"] == "paid":
            conn.execute("""
                UPDATE users
                SET referral_paid=1,
                    referral_status='paid'
                WHERE user_id=?
            """, (user_id,))
            return False

        conn.execute("""
            UPDATE users
            SET balance=balance+?,
                referral_paid=1,
                referral_status='paid'
            WHERE user_id=?
        """, (
            reward,
            inviter_id,
        ))

        conn.execute("""
            UPDATE referrals
            SET reward=?,
                status='paid'
            WHERE invited_id=?
        """, (
            reward,
            user_id,
        ))

    return True


# =========================================================
# CHANNEL VERIFICATION
# =========================================================

async def missing_channels(bot, user_id):
    missing = []

    for channel, link in REQUIRED_CHANNELS:
        try:
            member = await bot.get_chat_member(
                chat_id=channel,
                user_id=user_id,
            )

            if member.status not in (
                ChatMemberStatus.MEMBER,
                ChatMemberStatus.ADMINISTRATOR,
                ChatMemberStatus.OWNER,
            ):
                missing.append((channel, link))

        except Exception:
            missing.append((channel, link))

    return missing


# =========================================================
# KEYBOARDS
# =========================================================

def channel_keyboard(missing):
    buttons = []

    for channel, link in missing:
        buttons.append([
            InlineKeyboardButton(
                f"📢 {channel}",
                url=link,
            )
        ])

    buttons.append([
        InlineKeyboardButton(
            "✅ Verify Membership",
            callback_data="verify",
        )
    ])

    return InlineKeyboardMarkup(buttons)


def main_keyboard():
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
            InlineKeyboardButton("💬 Support", callback_data="support"),
        ],
    ])


def back_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🔙 Back", callback_data="home")
        ]
    ])


def referral_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "🔗 My Referral Link",
                callback_data="share_ref",
            )
        ],
        [
            InlineKeyboardButton(
                "🔙 Back",
                callback_data="home",
            )
        ],
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


def admin_keyboard():
    return InlineKeyboardMarkup([
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
                "🔎 Referral Search",
                callback_data="admin_referral_lookup",
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
                "➕ Add Task",
                callback_data="admin_add_task",
            ),
        ],
        [
            InlineKeyboardButton(
                "📢 Broadcast",
                callback_data="admin_broadcast",
            ),
        ],
    ])


# =========================================================
# USER PAGES
# =========================================================

async def show_join_required(update, context, missing=None):
    if missing is None:
        missing = await missing_channels(
            context.bot,
            update.effective_user.id,
        )

    text = (
        "🔐 <b>Global Cash Bot</b>\n\n"
        "To use the bot, please join all required channels first.\n\n"
        "👇 Join the channels below, then press Verify."
    )

    if update.callback_query:
        await update.callback_query.edit_message_text(
            text,
            parse_mode="HTML",
            reply_markup=channel_keyboard(missing),
        )
    else:
        await update.message.reply_text(
            text,
            parse_mode="HTML",
            reply_markup=channel_keyboard(missing),
        )


async def show_home(update, context):
    user_id = update.effective_user.id

    if is_banned(user_id):
        text = (
            "🚫 <b>Account Restricted</b>\n\n"
            "Your account is currently restricted.\n"
            f"Support: {escape(SUPPORT_USERNAME)}"
        )

        if update.callback_query:
            await update.callback_query.edit_message_text(
                text,
                parse_mode="HTML",
            )
        else:
            await update.message.reply_text(
                text,
                parse_mode="HTML",
            )
        return

    missing = await missing_channels(
        context.bot,
        user_id,
    )

    if missing:
        await show_join_required(update, context, missing)
        return

    user = get_user(user_id)

    text = (
        "🎉 <b>Welcome to Global Cash Bot!</b>\n\n"
        f"👤 {escape(user['first_name'] or 'User')}\n"
        f"💰 Balance: <b>{user['balance']:.2f} ETB</b>\n\n"
        "Choose an option below 👇"
    )

    if update.callback_query:
        await update.callback_query.edit_message_text(
            text,
            parse_mode="HTML",
            reply_markup=main_keyboard(),
        )
    else:
        await update.message.reply_text(
            text,
            parse_mode="HTML",
            reply_markup=main_keyboard(),
        )


async def show_balance(query):
    balance = get_balance(query.from_user.id)

    await query.edit_message_text(
        "💰 <b>Your Balance</b>\n\n"
        f"💵 Available: <b>{balance:.2f} ETB</b>",
        parse_mode="HTML",
        reply_markup=back_keyboard(),
    )


async def show_referral(query):
    user_id = query.from_user.id
    count = get_referral_count(user_id)
    reward = get_referral_reward()

    link = f"https://t.me/{BOT_USERNAME}?start={user_id}"

    text = (
        "👥 <b>Referral Program</b>\n\n"
        f"👤 Successful referrals: <b>{count}</b>\n"
        f"💰 Reward per referral: <b>{reward:.2f} ETB</b>\n\n"
        "Invite friends using your referral link.\n"
        "The reward is paid after your invited user joins all "
        "required channels and verifies the bot.\n\n"
        f"🔗 <code>{escape(link)}</code>"
    )

    await query.edit_message_text(
        text,
        parse_mode="HTML",
        reply_markup=referral_keyboard(),
    )


async def show_tasks(query):
    with db() as conn:
        tasks = conn.execute("""
            SELECT *
            FROM tasks
            ORDER BY id DESC
        """).fetchall()

    if not tasks:
        await query.edit_message_text(
            "🎯 <b>Tasks</b>\n\n"
            "No tasks are available right now.",
            parse_mode="HTML",
            reply_markup=back_keyboard(),
        )
        return

    buttons = []

    for task in tasks:
        buttons.append([
            InlineKeyboardButton(
                f"🎯 {task['title']} — {task['reward']:.2f} ETB",
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
        "Complete a task and verify it to receive your reward.",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(buttons),
    )


async def show_task(query, task_id):
    with db() as conn:
        task = conn.execute(
            "SELECT * FROM tasks WHERE id=?",
            (task_id,),
        ).fetchone()

    if not task:
        await query.answer(
            "Task not found.",
            show_alert=True,
        )
        return

    text = (
        f"🎯 <b>{escape(task['title'])}</b>\n\n"
        f"{escape(task['description'] or '')}\n\n"
        f"💰 Reward: <b>{task['reward']:.2f} ETB</b>\n"
    )

    buttons = []

    if task["link"]:
        buttons.append([
            InlineKeyboardButton(
                "🔗 Open Task",
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
            "🔙 Back",
            callback_data="tasks",
        )
    ])

    await query.edit_message_text(
        text,
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(buttons),
    )


async def show_wallet(query):
    wallet_type, wallet_number = get_wallet(
        query.from_user.id
    )

    if wallet_type and wallet_number:
        text = (
            "👛 <b>Your Wallet</b>\n\n"
            f"Method: <b>{escape(wallet_type)}</b>\n"
            f"Number: <code>{escape(wallet_number)}</code>\n\n"
            "Choose another wallet method if you want to update it."
        )
    else:
        text = (
            "👛 <b>Wallet</b>\n\n"
            "Please select your withdrawal wallet."
        )

    await query.edit_message_text(
        text,
        parse_mode="HTML",
        reply_markup=wallet_keyboard(),
    )


async def show_withdraw(query):
    user = get_user(query.from_user.id)

    if user["is_banned"]:
        await query.answer(
            "Your account is restricted.",
            show_alert=True,
        )
        return

    if user["suspicious"]:
        await query.answer(
            "Withdrawal is temporarily restricted.",
            show_alert=True,
        )
        return

    wallet_type, wallet_number = get_wallet(
        query.from_user.id
    )

    if not wallet_type or not wallet_number:
        await query.edit_message_text(
            "👛 <b>Wallet Required</b>\n\n"
            "Please add your CBE or Telebirr wallet first.",
            parse_mode="HTML",
            reply_markup=wallet_keyboard(),
        )
        return

    balance = get_balance(query.from_user.id)

    text = (
        "💸 <b>Withdraw</b>\n\n"
        f"💰 Balance: <b>{balance:.2f} ETB</b>\n"
        f"📌 Minimum: <b>{MIN_WITHDRAWAL:.2f} ETB</b>\n\n"
        "Send the amount you want to withdraw."
    )

    await query.edit_message_text(
        text,
        parse_mode="HTML",
        reply_markup=back_keyboard(),
    )

    return True


async def show_support(query):
    await query.edit_message_text(
        "💬 <b>Support</b>\n\n"
        "If you need help, contact our support team.\n\n"
        f"👨‍💻 Support: {escape(SUPPORT_USERNAME)}",
        parse_mode="HTML",
        reply_markup=back_keyboard(),
    )


# =========================================================
# WALLET INPUT
# =========================================================

async def ask_wallet(query, wallet_type, context):
    context.user_data["wallet_type"] = wallet_type
    context.user_data["awaiting_wallet"] = True

    if wallet_type == "CBE":
        text = (
            "🏦 <b>CBE Wallet</b>\n\n"
            "Send your 13-digit CBE account number.\n\n"
            "Example:\n"
            "<code>1000123456789</code>"
        )
    else:
        text = (
            "📱 <b>Telebirr Wallet</b>\n\n"
            "Send your 10-digit Telebirr number.\n\n"
            "Example:\n"
            "<code>0912345678</code>"
        )

    await query.edit_message_text(
        text,
        parse_mode="HTML",
        reply_markup=back_keyboard(),
    )


def valid_cbe(number):
    return bool(re.fullmatch(r"1000\d{9}", number))


def valid_telebirr(number):
    return bool(re.fullmatch(r"(09|07)\d{8}", number))


# =========================================================
# WITHDRAWAL
# =========================================================

async def confirm_withdrawal_process(update, context, amount):
    user_id = update.effective_user.id

    if not math.isfinite(amount):
        return False, "Invalid amount."

    with db() as conn:
        user = conn.execute(
            "SELECT * FROM users WHERE user_id=?",
            (user_id,),
        ).fetchone()

        if not user:
            return False, "User not found."

        if user["is_banned"]:
            return False, "Your account is restricted."

        if user["suspicious"]:
            return False, "Withdrawal is restricted."

        if amount < MIN_WITHDRAWAL:
            return False, (
                f"Minimum withdrawal is "
                f"{MIN_WITHDRAWAL:.2f} ETB."
            )

        if amount > user["balance"]:
            return False, "Insufficient balance."

        if not user["wallet_type"] or not user["wallet_number"]:
            return False, "Please add your wallet first."

        conn.execute("""
            UPDATE users
            SET balance=balance-?
            WHERE user_id=?
        """, (
            amount,
            user_id,
        ))

        cursor = conn.execute("""
            INSERT INTO withdrawals(
                user_id,
                amount,
                wallet_type,
                wallet_number,
                status,
                created_at
            )
            VALUES(?,?,?,?,?,?)
        """, (
            user_id,
            amount,
            user["wallet_type"],
            user["wallet_number"],
            "pending",
            now(),
        ))

        withdrawal_id = cursor.lastrowid

    return True, withdrawal_id


async def notify_admin_withdrawal(
    context,
    withdrawal_id,
    user_id,
    amount,
):
    user = get_user(user_id)

    if not user:
        return

    username = (
        f"@{user['username']}"
        if user["username"]
        else "No username"
    )

    text = (
        "💸 <b>New Withdrawal Request</b>\n\n"
        f"🆔 Withdrawal: <code>#{withdrawal_id}</code>\n"
        f"👤 User ID: <code>{user_id}</code>\n"
        f"👤 Username: {escape(username)}\n"
        f"💰 Amount: <b>{amount:.2f} ETB</b>\n"
        f"👛 Wallet: <b>{escape(user['wallet_type'])}</b>\n"
        f"🔢 Number: <code>{escape(user['wallet_number'])}</code>\n"
    )

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "✅ Approve",
                callback_data=f"approve_{withdrawal_id}",
            ),
            InlineKeyboardButton(
                "❌ Reject",
                callback_data=f"reject_{withdrawal_id}",
            ),
        ],
        [
            InlineKeyboardButton(
                "🚫 Ban User",
                callback_data=f"ban_{user_id}",
            )
        ],
    ])

    await context.bot.send_message(
        chat_id=ADMIN_ID,
        text=text,
        parse_mode="HTML",
        reply_markup=keyboard,
    )


# =========================================================
# ADMIN
# =========================================================

def admin_only(user_id):
    return user_id == ADMIN_ID


async def show_admin(query):
    await query.edit_message_text(
        "🛠 <b>Global Cash Bot — Admin Panel</b>\n\n"
        "Choose an admin action:",
        parse_mode="HTML",
        reply_markup=admin_keyboard(),
    )


async def show_admin_stats(query):
    with db() as conn:
        users = conn.execute(
            "SELECT COUNT(*) AS c FROM users"
        ).fetchone()["c"]

        joined = conn.execute(
            "SELECT COUNT(*) AS c FROM users WHERE joined_all=1"
        ).fetchone()["c"]

        balance = conn.execute(
            "SELECT COALESCE(SUM(balance),0) AS s FROM users"
        ).fetchone()["s"]

        pending = conn.execute("""
            SELECT COUNT(*) AS c
            FROM withdrawals
            WHERE status='pending'
        """).fetchone()["c"]

        paid_refs = conn.execute("""
            SELECT COUNT(*) AS c
            FROM referrals
            WHERE status='paid'
        """).fetchone()["c"]

    text = (
        "📊 <b>Statistics</b>\n\n"
        f"👥 Total users: <b>{users}</b>\n"
        f"✅ Verified users: <b>{joined}</b>\n"
        f"👥 Paid referrals: <b>{paid_refs}</b>\n"
        f"💰 Total user balance: <b>{balance:.2f} ETB</b>\n"
        f"💸 Pending withdrawals: <b>{pending}</b>"
    )

    await query.edit_message_text(
        text,
        parse_mode="HTML",
        reply_markup=back_keyboard_admin(),
    )


def back_keyboard_admin():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "🔙 Admin Panel",
                callback_data="admin",
            )
        ]
    ])


async def start_change_reward(query, context):
    context.user_data.clear()
    context.user_data["admin_step"] = "reward"

    current = get_referral_reward()

    await query.edit_message_text(
        "💰 <b>Change Referral Reward</b>\n\n"
        f"Current reward: <b>{current:.2f} ETB</b>\n\n"
        "Send the new reward amount.\n"
        "Example: <code>2</code>",
        parse_mode="HTML",
        reply_markup=back_keyboard_admin(),
    )


async def show_admin_reward(query):
    reward = get_referral_reward()

    await query.edit_message_text(
        "💰 <b>Referral Reward</b>\n\n"
        f"Current reward: <b>{reward:.2f} ETB</b>\n\n"
        "You can change the reward below.",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "✏️ Change Reward",
                    callback_data="admin_change_reward",
                )
            ],
            [
                InlineKeyboardButton(
                    "🔙 Admin Panel",
                    callback_data="admin",
                )
            ],
        ]),
    )


async def show_admin_referrals(query):
    with db() as conn:
        rows = conn.execute("""
            SELECT
                u.user_id,
                u.username,
                u.first_name,
                COUNT(r.id) AS count
            FROM users u
            LEFT JOIN referrals r
                ON u.user_id = r.inviter_id
                AND r.status='paid'
            GROUP BY u.user_id
            ORDER BY count DESC
            LIMIT 10
        """).fetchall()

    lines = ["👥 <b>Top Referrers</b>\n"]

    for i, row in enumerate(rows, 1):
        name = row["username"] or row["first_name"] or "User"
        lines.append(
            f"{i}. {escape(name)} — "
            f"<b>{row['count']}</b> referrals\n"
            f"ID: <code>{row['user_id']}</code>"
        )

    await query.edit_message_text(
        "\n".join(lines),
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "🔎 Search User",
                    callback_data="admin_referral_lookup",
                )
            ],
            [
                InlineKeyboardButton(
                    "🔙 Admin Panel",
                    callback_data="admin",
                )
            ],
        ]),
    )


async def start_referral_lookup(query, context):
    context.user_data.clear()
    context.user_data["admin_step"] = "referral_lookup"

    await query.edit_message_text(
        "🔎 <b>Search User</b>\n\n"
        "Send a Telegram User ID or username.\n\n"
        "Example:\n"
        "<code>123456789</code>\n"
        "or\n"
        "<code>@username</code>",
        parse_mode="HTML",
        reply_markup=back_keyboard_admin(),
    )


async def search_referral_user(query, value):
    value = value.strip()

    with db() as conn:
        if value.startswith("@"):
            username = value[1:]
            user = conn.execute("""
                SELECT * FROM users
                WHERE LOWER(username)=LOWER(?)
                LIMIT 1
            """, (username,)).fetchone()
        elif value.isdigit():
            user = conn.execute("""
                SELECT * FROM users
                WHERE user_id=?
                LIMIT 1
            """, (int(value),)).fetchone()
        else:
            user = None

        if not user:
            await query.edit_message_text(
                "❌ User not found.",
                reply_markup=back_keyboard_admin(),
            )
            return

        referrals = conn.execute("""
            SELECT COUNT(*) AS c
            FROM referrals
            WHERE inviter_id=?
            AND status='paid'
        """, (user["user_id"],)).fetchone()["c"]

    name = user["username"] or user["first_name"] or "User"

    text = (
        "👤 <b>User Information</b>\n\n"
        f"Name: <b>{escape(name)}</b>\n"
        f"ID: <code>{user['user_id']}</code>\n"
        f"Balance: <b>{user['balance']:.2f} ETB</b>\n"
        f"Successful referrals: <b>{referrals}</b>\n"
        f"Verified: <b>{'Yes' if user['joined_all'] else 'No'}</b>\n"
        f"Banned: <b>{'Yes' if user['is_banned'] else 'No'}</b>\n"
        f"Suspicious: <b>{'Yes' if user['suspicious'] else 'No'}</b>"
    )

    await query.edit_message_text(
        text,
        parse_mode="HTML",
        reply_markup=back_keyboard_admin(),
    )


async def show_admin_withdrawals(query):
    with db() as conn:
        rows = conn.execute("""
            SELECT *
            FROM withdrawals
            WHERE status='pending'
            ORDER BY id DESC
            LIMIT 10
        """).fetchall()

    if not rows:
        text = "💸 <b>Pending Withdrawals</b>\n\nNo pending withdrawals."
        await query.edit_message_text(
            text,
            parse_mode="HTML",
            reply_markup=back_keyboard_admin(),
        )
        return

    buttons = []

    for row in rows:
        buttons.append([
            InlineKeyboardButton(
                f"#{row['id']} — {row['amount']:.2f} ETB",
                callback_data=f"withdraw_info_{row['id']}",
            )
        ])

    buttons.append([
        InlineKeyboardButton(
            "🔙 Admin Panel",
            callback_data="admin",
        )
    ])

    await query.edit_message_text(
        "💸 <b>Pending Withdrawals</b>\n\n"
        "Select a request:",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(buttons),
    )


async def show_admin_suspicious(query):
    with db() as conn:
        rows = conn.execute("""
            SELECT *
            FROM users
            WHERE suspicious=1 OR is_banned=1
            ORDER BY user_id DESC
            LIMIT 20
        """).fetchall()

    if not rows:
        await query.edit_message_text(
            "⚠️ <b>Suspicious Users</b>\n\n"
            "No suspicious users found.",
            parse_mode="HTML",
            reply_markup=back_keyboard_admin(),
        )
        return

    lines = ["⚠️ <b>Suspicious / Banned Users</b>\n"]

    for row in rows:
        name = row["username"] or row["first_name"] or "User"
        status = []

        if row["suspicious"]:
            status.append("Suspicious")

        if row["is_banned"]:
            status.append("Banned")

        lines.append(
            f"👤 {escape(name)}\n"
            f"ID: <code>{row['user_id']}</code>\n"
            f"Status: {', '.join(status)}\n"
        )

    await query.edit_message_text(
        "\n".join(lines),
        parse_mode="HTML",
        reply_markup=back_keyboard_admin(),
    )


async def start_add_task(query, context):
    context.user_data.clear()
    context.user_data["admin_step"] = "task_title"

    await query.edit_message_text(
        "➕ <b>Add Task</b>\n\n"
        "Step 1/4\n"
        "Send the task title.",
        parse_mode="HTML",
        reply_markup=back_keyboard_admin(),
    )


async def start_broadcast(query, context):
    context.user_data.clear()
    context.user_data["admin_step"] = "broadcast"

    await query.edit_message_text(
        "📢 <b>Broadcast</b>\n\n"
        "Send the message you want to broadcast to all users.",
        parse_mode="HTML",
        reply_markup=back_keyboard_admin(),
    )


# =========================================================
# WITHDRAWAL ADMIN ACTIONS
# =========================================================

async def approve_withdrawal(query, withdrawal_id):
    if not admin_only(query.from_user.id):
        return

    with db() as conn:
        row = conn.execute("""
            SELECT *
            FROM withdrawals
            WHERE id=?
        """, (withdrawal_id,)).fetchone()

        if not row:
            await query.answer(
                "Withdrawal not found.",
                show_alert=True,
            )
            return

        if row["status"] != "pending":
            await query.answer(
                "Already processed.",
                show_alert=True,
            )
            return

        conn.execute("""
            UPDATE withdrawals
            SET status='approved',
                processed_at=?
            WHERE id=?
        """, (
            now(),
            withdrawal_id,
        ))

    await query.edit_message_text(
        "✅ <b>Withdrawal Approved</b>\n\n"
        f"Withdrawal <code>#{withdrawal_id}</code> "
        "has been marked as approved.\n\n"
        "Please complete the actual payment manually.",
        parse_mode="HTML",
    )


async def reject_withdrawal(query, withdrawal_id):
    if not admin_only(query.from_user.id):
        return

    with db() as conn:
        row = conn.execute("""
            SELECT *
            FROM withdrawals
            WHERE id=?
        """, (withdrawal_id,)).fetchone()

        if not row:
            await query.answer(
                "Withdrawal not found.",
                show_alert=True,
            )
            return

        if row["status"] != "pending":
            await query.answer(
                "Already processed.",
                show_alert=True,
            )
            return

        conn.execute("""
            UPDATE withdrawals
            SET status='rejected',
                processed_at=?
            WHERE id=?
        """, (
            now(),
            withdrawal_id,
        ))

        conn.execute("""
            UPDATE users
            SET balance=balance+?
            WHERE user_id=?
        """, (
            row["amount"],
            row["user_id"],
        ))

    await query.edit_message_text(
        "❌ <b>Withdrawal Rejected</b>\n\n"
        f"Withdrawal <code>#{withdrawal_id}</code> rejected.\n"
        f"💰 {row['amount']:.2f} ETB has been returned to the user.",
        parse_mode="HTML",
    )


async def ban_user(query, user_id):
    if not admin_only(query.from_user.id):
        return

    with db() as conn:
        conn.execute("""
            UPDATE users
            SET is_banned=1,
                suspicious=1
            WHERE user_id=?
        """, (user_id,))

    await query.edit_message_text(
        "🚫 <b>User Banned</b>\n\n"
        f"User ID: <code>{user_id}</code>",
        parse_mode="HTML",
    )


# =========================================================
# TASK VERIFICATION
# =========================================================

async def verify_task(query, context, task_id):
    user_id = query.from_user.id

    with db() as conn:
        task = conn.execute(
            "SELECT * FROM tasks WHERE id=?",
            (task_id,),
        ).fetchone()

        if not task:
            await query.answer(
                "Task not found.",
                show_alert=True,
            )
            return

        done = conn.execute("""
            SELECT *
            FROM user_tasks
            WHERE user_id=? AND task_id=?
        """, (
            user_id,
            task_id,
        )).fetchone()

        if done:
            await query.answer(
                "You already completed this task.",
                show_alert=True,
            )
            return

    if task["channel"]:
        try:
            member = await context.bot.get_chat_member(
                task["channel"],
                user_id,
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
                "Could not verify membership.",
                show_alert=True,
            )
            return

    with db() as conn:
        conn.execute("""
            INSERT INTO user_tasks(
                user_id,
                task_id,
                paid,
                created_at
            )
            VALUES(?,?,?,?)
        """, (
            user_id,
            task_id,
            1,
            now(),
        ))

        conn.execute("""
            UPDATE users
            SET balance=balance+?
            WHERE user_id=?
        """, (
            task["reward"],
            user_id,
        ))

    await query.edit_message_text(
        "🎉 <b>Task Completed!</b>\n\n"
        f"💰 Reward: <b>{task['reward']:.2f} ETB</b>\n\n"
        "The reward has been added to your balance.",
        parse_mode="HTML",
        reply_markup=back_keyboard(),
    )


# =========================================================
# START
# =========================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user

    referred_by = None

    if context.args:
        try:
            ref_id = int(context.args[0])

            if ref_id != user.id:
                referred_by = ref_id
        except ValueError:
            pass

    create_or_update_user(
        user,
        referred_by,
    )

    if is_banned(user.id):
        await update.message.reply_text(
            "🚫 Your account is restricted."
        )
        return

    missing = await missing_channels(
        context.bot,
        user.id,
    )

    if missing:
        await show_join_required(
            update,
            context,
            missing,
        )
        return

    with db() as conn:
        conn.execute("""
            UPDATE users
            SET joined_all=1
            WHERE user_id=?
        """, (user.id,))

    paid = process_referral_reward(user.id)

    if paid:
        user = get_user(user.id)

        inviter_id = user["referred_by"]

        if inviter_id:
            try:
                await context.bot.send_message(
                    inviter_id,
                    "🎉 <b>Referral Reward!</b>\n\n"
                    "Your invited user completed verification.\n"
                    f"💰 <b>{get_referral_reward():.2f} ETB</b> "
                    "has been added to your balance.",
                    parse_mode="HTML",
                )
            except Exception:
                pass

    await show_home(update, context)


# =========================================================
# VERIFY USER
# =========================================================

async def verify_user(query, context):
    user_id = query.from_user.id

    if is_banned(user_id):
        await query.edit_message_text(
            "🚫 Your account is restricted."
        )
        return

    missing = await missing_channels(
        context.bot,
        user_id,
    )

    if missing:
        await query.edit_message_text(
            "❌ <b>Not Fully Verified</b>\n\n"
            "You still need to join these channels:",
            parse_mode="HTML",
            reply_markup=channel_keyboard(missing),
        )
        return

    with db() as conn:
        conn.execute("""
            UPDATE users
            SET joined_all=1
            WHERE user_id=?
        """, (user_id,))

    paid = process_referral_reward(user_id)

    if paid:
        user = get_user(user_id)
        inviter_id = user["referred_by"]

        if inviter_id:
            try:
                await context.bot.send_message(
                    inviter_id,
                    "🎉 <b>Referral Reward!</b>\n\n"
                    f"💰 <b>{get_referral_reward():.2f} ETB</b> "
                    "has been added to your balance.",
                    parse_mode="HTML",
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

async def callback_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.callback_query
    data = query.data

    await query.answer()

    if data == "home":
        await show_home(update, context)
        return

    if data == "verify":
        await verify_user(query, context)
        return

    if data == "balance":
        await show_balance(query)
        return

    if data == "referral":
        await show_referral(query)
        return

    if data == "tasks":
        await show_tasks(query)
        return

    if data == "withdraw":
        await show_withdraw(query)
        return

    if data == "wallet":
        await show_wallet(query)
        return

    if data == "support":
        await show_support(query)
        return

    if data == "share_ref":
        link = f"https://t.me/{BOT_USERNAME}?start={query.from_user.id}"

        share_url = (
            "https://t.me/share/url"
            f"?url={link}"
            "&text=Join%20Global%20Cash%20Bot%20and%20earn%20ETB!"
        )

        await query.edit_message_text(
            "🔗 <b>Your Referral Link</b>\n\n"
            f"<code>{escape(link)}</code>\n\n"
            "Share this link with your friends 👇",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "📤 Share Link",
                        url=share_url,
                    )
                ],
                [
                    InlineKeyboardButton(
                        "🔙 Back",
                        callback_data="referral",
                    )
                ],
            ]),
        )
        return

    if data == "wallet_cbe":
        await ask_wallet(
            query,
            "CBE",
            context,
        )
        return

    if data == "wallet_telebirr":
        await ask_wallet(
            query,
            "Telebirr",
            context,
        )
        return

    if data.startswith("task_") and not data.startswith("tasks"):
        try:
            task_id = int(data.split("_")[1])
            await show_task(query, task_id)
        except Exception:
            pass
        return

    if data.startswith("verify_task_"):
        try:
            task_id = int(data.split("_")[-1])
            await verify_task(
                query,
                context,
                task_id,
            )
        except Exception:
            await query.answer(
                "Invalid task.",
                show_alert=True,
            )
        return

    # ---------------- ADMIN ----------------

    if data == "admin":
        if not admin_only(query.from_user.id):
            await query.answer(
                "Admin only.",
                show_alert=True,
            )
            return

        await show_admin(query)
        return

    if data == "admin_stats":
        if not admin_only(query.from_user.id):
            return

        await show_admin_stats(query)
        return

    if data == "admin_reward":
        if not admin_only(query.from_user.id):
            return

        await show_admin_reward(query)
        return

    if data == "admin_change_reward":
        if not admin_only(query.from_user.id):
            return

        await start_change_reward(
            query,
            context,
        )
        return

    if data == "admin_referral_lookup":
        if not admin_only(query.from_user.id):
            return

        await start_referral_lookup(
            query,
            context,
        )
        return

    if data == "admin_withdrawals":
        if not admin_only(query.from_user.id):
            return

        await show_admin_withdrawals(query)
        return

    if data == "admin_suspicious":
        if not admin_only(query.from_user.id):
            return

        await show_admin_suspicious(query)
        return

    if data == "admin_add_task":
        if not admin_only(query.from_user.id):
            return

        await start_add_task(
            query,
            context,
        )
        return

    if data == "admin_broadcast":
        if not admin_only(query.from_user.id):
            return

        await start_broadcast(
            query,
            context,
        )
        return

    if data.startswith("approve_"):
        try:
            withdrawal_id = int(data.split("_")[1])
            await approve_withdrawal(
                query,
                withdrawal_id,
            )
        except Exception:
            pass
        return

    if data.startswith("reject_"):
        try:
            withdrawal_id = int(data.split("_")[1])
            await reject_withdrawal(
                query,
                withdrawal_id,
            )
        except Exception:
            pass
        return

    if data.startswith("ban_"):
        try:
            user_id = int(data.split("_")[1])
            await ban_user(
                query,
                user_id,
            )
        except Exception:
            pass
        return


# =========================================================
# MESSAGE HANDLER
# =========================================================

async def message_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    user = update.effective_user
    text = (update.message.text or "").strip()

    # ---------------- ADMIN FLOW ----------------

    if admin_only(user.id):
        admin_step = context.user_data.get("admin_step")

        if admin_step == "reward":
            try:
                reward = float(text)

                if not math.isfinite(reward) or reward < 0:
                    raise ValueError

                set_setting(
                    "referral_reward",
                    reward,
                )

                context.user_data.clear()

                await update.message.reply_text(
                    "✅ <b>Referral reward updated!</b>\n\n"
                    f"New reward: <b>{reward:.2f} ETB</b>",
                    parse_mode="HTML",
                    reply_markup=admin_keyboard(),
                )
            except ValueError:
                await update.message.reply_text(
                    "❌ Send a valid positive number.\n"
                    "Example: <code>2</code>",
                    parse_mode="HTML",
                )

            return

        if admin_step == "referral_lookup":
            context.user_data.clear()

            fake_query = await update.message.reply_text(
                "🔎 Searching..."
            )

            class DummyQuery:
                from_user = user

                async def edit_message_text(
                    self,
                    text,
                    **kwargs,
                ):
                    await fake_query.edit_text(
                        text,
                        **kwargs,
                    )

            await search_referral_user(
                DummyQuery(),
                text,
            )
            return

        if admin_step == "task_title":
            context.user_data["task_title"] = text
            context.user_data["admin_step"] = "task_description"

            await update.message.reply_text(
                "Step 2/4\n\n"
                "Send the task description."
            )
            return

        if admin_step == "task_description":
            context.user_data["task_description"] = text
            context.user_data["admin_step"] = "task_reward"

            await update.message.reply_text(
                "Step 3/4\n\n"
                "Send the reward amount.\n"
                "Example: 2"
            )
            return

        if admin_step == "task_reward":
            try:
                reward = float(text)

                if not math.isfinite(reward) or reward <= 0:
                    raise ValueError

                context.user_data["task_reward"] = reward
                context.user_data["admin_step"] = "task_link"

                await update.message.reply_text(
                    "Step 4/4\n\n"
                    "Send the task channel username or link.\n\n"
                    "Example:\n"
                    "@ExampleChannel\n\n"
                    "or send - if no channel is required."
                )
            except ValueError:
                await update.message.reply_text(
                    "❌ Invalid reward."
                )

            return

        if admin_step == "task_link":
            title = context.user_data["task_title"]
            description = context.user_data["task_description"]
            reward = context.user_data["task_reward"]

            link = "" if text == "-" else text

            channel = ""

            if text.startswith("@"):
                channel = text
                link = f"https://t.me/{text[1:]}"

            with db() as conn:
                conn.execute("""
                    INSERT INTO tasks(
                        title,
                        description,
                        reward,
                        channel,
                        link,
                        created_at
                    )
                    VALUES(?,?,?,?,?,?)
                """, (
                    title,
                    description,
                    reward,
                    channel,
                    link,
                    now(),
                ))

            context.user_data.clear()

            await update.message.reply_text(
                "✅ <b>Task Added Successfully!</b>\n\n"
                f"🎯 {escape(title)}\n"
                f"💰 Reward: <b>{reward:.2f} ETB</b>",
                parse_mode="HTML",
                reply_markup=admin_keyboard(),
            )
            return

        if admin_step == "broadcast":
            context.user_data.clear()

            with db() as conn:
                users = conn.execute(
                    "SELECT user_id FROM users WHERE is_banned=0"
                ).fetchall()

            sent = 0
            failed = 0

            for row in users:
                try:
                    await context.bot.send_message(
                        chat_id=row["user_id"],
                        text=text,
                    )
                    sent += 1
                except Exception:
                    failed += 1

            await update.message.reply_text(
                "📢 <b>Broadcast Finished</b>\n\n"
                f"✅ Sent: <b>{sent}</b>\n"
                f"❌ Failed: <b>{failed}</b>",
                parse_mode="HTML",
                reply_markup=admin_keyboard(),
            )
            return

    # ---------------- WALLET ----------------

    if context.user_data.get("awaiting_wallet"):
        wallet_type = context.user_data.get("wallet_type")

        if wallet_type == "CBE":
            if not valid_cbe(text):
                await update.message.reply_text(
                    "❌ Invalid CBE account.\n\n"
                    "CBE must be 13 digits and start with 1000.\n"
                    "Example: <code>1000123456789</code>",
                    parse_mode="HTML",
                )
                return

        elif wallet_type == "Telebirr":
            if not valid_telebirr(text):
                await update.message.reply_text(
                    "❌ Invalid Telebirr number.\n\n"
                    "Telebirr must be 10 digits and start with 09 or 07.\n"
                    "Example: <code>0912345678</code>",
                    parse_mode="HTML",
                )
                return

        saved = save_wallet(
            user.id,
            wallet_type,
            text,
        )

        if not saved:
            await update.message.reply_text(
                "⚠️ This wallet is already connected to another account."
            )
            return

        context.user_data.clear()

        await update.message.reply_text(
            "✅ <b>Wallet Saved Successfully!</b>\n\n"
            f"Method: <b>{escape(wallet_type)}</b>\n"
            f"Number: <code>{escape(text)}</code>",
            parse_mode="HTML",
            reply_markup=main_keyboard(),
        )
        return

    # ---------------- WITHDRAW AMOUNT ----------------

    if context.user_data.get("awaiting_withdraw"):
        try:
            amount = float(text)

            if not math.isfinite(amount):
                raise ValueError

        except ValueError:
            await update.message.reply_text(
                "❌ Please send a valid amount.\n"
                "Example: <code>30</code>",
                parse_mode="HTML",
            )
            return

        success, result = await confirm_withdrawal_process(
            update,
            context,
            amount,
        )

        if not success:
            await update.message.reply_text(
                f"❌ {result}"
            )
            return

        withdrawal_id = result

        context.user_data.clear()

        await update.message.reply_text(
            "✅ <b>Withdrawal Request Submitted</b>\n\n"
            f"💰 Amount: <b>{amount:.2f} ETB</b>\n"
            f"🆔 Request: <code>#{withdrawal_id}</code>\n\n"
            "Your request is now waiting for admin approval.",
            parse_mode="HTML",
            reply_markup=main_keyboard(),
        )

        try:
            await notify_admin_withdrawal(
                context,
                withdrawal_id,
                user.id,
                amount,
            )
        except Exception as e:
            logger.error(
                "Failed to notify admin: %s",
                e,
            )

        return

    # ---------------- FALLBACK ----------------

    if text == "/admin":
        if admin_only(user.id):
            await update.message.reply_text(
                "🛠 <b>Admin Panel</b>",
                parse_mode="HTML",
                reply_markup=admin_keyboard(),
            )
        return

    await update.message.reply_text(
        "Please choose an option:",
        reply_markup=main_keyboard(),
    )


# =========================================================
# COMMANDS
# =========================================================

async def admin_command(update, context):
    if not admin_only(update.effective_user.id):
        await update.message.reply_text(
            "⛔ Admin only."
        )
        return

    await update.message.reply_text(
        "🛠 <b>Global Cash Bot Admin Panel</b>",
        parse_mode="HTML",
        reply_markup=admin_keyboard(),
    )


async def cancel_command(update, context):
    context.user_data.clear()

    await update.message.reply_text(
        "❌ Cancelled.",
        reply_markup=main_keyboard(),
    )


# =========================================================
# ERROR HANDLER
# =========================================================

async def error_handler(update, context):
    logger.exception(
        "Exception while handling update:",
        exc_info=context.error,
    )


# =========================================================
# MAIN
# =========================================================

def main():
    if not BOT_TOKEN:
        raise RuntimeError(
            "BOT_TOKEN environment variable is missing."
        )

    if ADMIN_ID == 0:
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
        CommandHandler("start", start)
    )

    application.add_handler(
        CommandHandler("admin", admin_command)
    )

    application.add_handler(
        CommandHandler("cancel", cancel_command)
    )

    application.add_handler(
        CallbackQueryHandler(callback_handler)
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

    logger.info("Global Cash Bot started.")

    application.run_polling(
        drop_pending_updates=True
    )


if __name__ == "__main__":
    main()

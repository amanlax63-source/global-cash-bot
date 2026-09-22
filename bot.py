import os
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
# OLD USER SYSTEM + FIXED ADMIN SYSTEM
# ============================================================

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()

try:
    ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
except ValueError:
    ADMIN_ID = 0

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
# DATABASE
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


# ============================================================
# USER DATABASE FUNCTIONS
# ============================================================

def get_user(user_id):
    conn = db()

    try:
        return conn.execute(
            "SELECT * FROM users WHERE user_id=?",
            (user_id,)
        ).fetchone()
    finally:
        conn.close()


def create_or_update_user(tg_user, referred_by=None):

    existing = get_user(tg_user.id)

    with get_db_cursor() as cur:

        if existing is None:

            ref = None

            if referred_by and referred_by != tg_user.id:
                ref = referred_by

            cur.execute("""
                INSERT INTO users(
                    user_id,
                    username,
                    first_name,
                    referred_by,
                    created_at
                )
                VALUES(?,?,?,?,?)
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
                SET username=?,
                    first_name=?
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
            (1 if value else 0, user_id)
        )


def add_balance(user_id, amount):

    with get_db_cursor() as cur:
        cur.execute(
            "UPDATE users SET balance=balance+? WHERE user_id=?",
            (amount, user_id)
        )


def get_balance(user_id):

    row = get_user(user_id)

    if not row:
        return 0.0

    return float(row["balance"])


def get_wallet(user_id):

    row = get_user(user_id)

    if (
        not row
        or not row["wallet_type"]
        or not row["wallet_number"]
    ):
        return None

    return row["wallet_type"], row["wallet_number"]


def wallet_exists_for_other_user(wallet_type, wallet_number, user_id):

    conn = db()

    try:
        row = conn.execute("""
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

        return row is not None

    finally:
        conn.close()


def set_wallet(user_id, wallet_type, wallet_number):

    try:

        with get_db_cursor() as cur:

            cur.execute("""
                UPDATE users
                SET wallet_type=?,
                    wallet_number=?
                WHERE user_id=?
            """, (
                wallet_type,
                wallet_number,
                user_id,
            ))

        return True

    except sqlite3.IntegrityError:
        return False


# ============================================================
# SETTINGS
# ============================================================

def get_setting(key, default=None):

    conn = db()

    try:
        row = conn.execute(
            "SELECT value FROM settings WHERE key=?",
            (key,)
        ).fetchone()

        return row["value"] if row else default

    finally:
        conn.close()


def set_setting(key, value):

    with get_db_cursor() as cur:

        cur.execute("""
            INSERT INTO settings(key,value)
            VALUES(?,?)
            ON CONFLICT(key)
            DO UPDATE SET value=excluded.value
        """, (
            key,
            str(value),
        ))


def get_referral_reward():

    try:
        return float(
            get_setting("referral_reward", "2.00")
        )
    except Exception:
        return 2.0


def get_referral_count(user_id):

    conn = db()

    try:

        row = conn.execute("""
            SELECT COUNT(*) AS c
            FROM referrals
            WHERE referrer_id=?
              AND status='paid'
        """, (user_id,)).fetchone()

        return int(row["c"])

    finally:
        conn.close()


# ============================================================
# REFERRALS
# ============================================================

def process_referral_reward(referred_id):

    user = get_user(referred_id)

    if not user:
        return None

    if not user["referred_by"]:
        return None

    if user["referred_by"] == referred_id:
        return None

    if user["referral_paid"]:
        return None

    if user["suspicious"]:
        return None

    inviter = user["referred_by"]

    inviter_row = get_user(inviter)

    if not inviter_row:
        return None

    if inviter_row["suspicious"]:
        return None

    conn = db()

    try:

        conn.execute("BEGIN IMMEDIATE")

        existing = conn.execute("""
            SELECT id
            FROM referrals
            WHERE referred_id=?
        """, (referred_id,)).fetchone()

        if existing:

            conn.execute("""
                UPDATE users
                SET referral_paid=1,
                    referral_status='paid'
                WHERE user_id=?
            """, (referred_id,))

            conn.commit()

            return None

        reward = get_referral_reward()

        conn.execute("""
            UPDATE users
            SET balance=balance+?
            WHERE user_id=?
        """, (
            reward,
            inviter,
        ))

        conn.execute("""
            INSERT INTO referrals(
                referrer_id,
                referred_id,
                reward_amount,
                status,
                created_at
            )
            VALUES(?,?,?,?,?)
        """, (
            inviter,
            referred_id,
            reward,
            "paid",
            now(),
        ))

        conn.execute("""
            UPDATE users
            SET referral_paid=1,
                referral_status='paid'
            WHERE user_id=?
        """, (referred_id,))

        conn.commit()

        return {
            "inviter_id": inviter,
            "referred_id": referred_id,
            "amount": reward,
        }

    except Exception:

        conn.rollback()

        logger.exception(
            "Referral reward error"
        )

        return None

    finally:
        conn.close()


# ============================================================
# TASKS
# ============================================================

def get_active_tasks():

    conn = db()

    try:

        return conn.execute("""
            SELECT *
            FROM tasks
            WHERE active=1
            ORDER BY id DESC
        """).fetchall()

    finally:
        conn.close()


def get_task(task_id):

    conn = db()

    try:

        return conn.execute("""
            SELECT *
            FROM tasks
            WHERE id=?
        """, (task_id,)).fetchone()

    finally:
        conn.close()


def user_task_paid(user_id, task_id):

    conn = db()

    try:

        row = conn.execute("""
            SELECT paid
            FROM user_tasks
            WHERE user_id=?
              AND task_id=?
        """, (
            user_id,
            task_id,
        )).fetchone()

        return bool(row and row["paid"])

    finally:
        conn.close()


def save_user_task(
    user_id,
    task_id,
    verified,
    paid
):

    with get_db_cursor() as cur:

        cur.execute("""
            INSERT INTO user_tasks(
                user_id,
                task_id,
                verified,
                paid,
                paid_at
            )
            VALUES(?,?,?,?,?)
            ON CONFLICT(user_id,task_id)
            DO UPDATE SET
                verified=excluded.verified,
                paid=excluded.paid,
                paid_at=excluded.paid_at
        """, (
            user_id,
            task_id,
            1 if verified else 0,
            1 if paid else 0,
            now() if paid else None,
        ))


def create_task(
    title,
    description,
    username,
    url,
    reward
):

    with get_db_cursor() as cur:

        cur.execute("""
            INSERT INTO tasks(
                title,
                description,
                channel_username,
                channel_url,
                reward,
                active,
                created_at
            )
            VALUES(?,?,?,?,?,1,?)
        """, (
            title,
            description,
            username,
            url,
            reward,
            now(),
        ))

        return cur.lastrowid


# ============================================================
# CHANNEL VERIFICATION
# ============================================================

def is_channel_member_status(status):

    return status in (
        "member",
        "administrator",
        "creator",
    )


async def missing_channels(bot, user_id):

    missing = []

    for username, url in REQUIRED_CHANNELS:

        try:

            member = await bot.get_chat_member(
                chat_id=username,
                user_id=user_id,
            )

            if not is_channel_member_status(
                member.status
            ):
                missing.append(
                    (username, url)
                )

        except Exception:

            missing.append(
                (username, url)
            )

    return missing


# ============================================================
# SAFE MESSAGE EDIT
# ============================================================

async def safe_edit_message(
    query,
    text,
    reply_markup=None,
    parse_mode="HTML"
):

    try:

        await query.edit_message_text(
            text=text,
            reply_markup=reply_markup,
            parse_mode=parse_mode,
        )

    except BadRequest as e:

        if "Message is not modified" in str(e):
            return

        raise


# ============================================================
# USER KEYBOARDS
# ============================================================

def channel_keyboard(missing):

    buttons = []

    for username, url in missing:

        buttons.append([
            InlineKeyboardButton(
                "ðŸ“¢ Join Channel",
                url=url
            )
        ])

    buttons.append([
        InlineKeyboardButton(
            "Verify âœ…",
            callback_data="verify"
        )
    ])

    return InlineKeyboardMarkup(buttons)


def main_keyboard():

    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "Balance ðŸ’°",
                callback_data="balance"
            ),
            InlineKeyboardButton(
                "Referral ðŸ‘¥",
                callback_data="referral"
            ),
        ],
        [
            InlineKeyboardButton(
                "Tasks ðŸŽ¯",
                callback_data="tasks"
            ),
            InlineKeyboardButton(
                "Withdraw ðŸ’¸",
                callback_data="withdraw"
            ),
        ],
        [
            InlineKeyboardButton(
                "Wallet ðŸ‘›",
                callback_data="wallet"
            ),
            InlineKeyboardButton(
                "Support ðŸ“ž",
                callback_data="support"
            ),
        ],
    ])


def back_keyboard():

    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "Back ðŸ”™",
                callback_data="home"
            )
        ]
    ])


def referral_keyboard():

    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "Share Link ðŸ“¤",
                callback_data="share_ref"
            )
        ],
        [
            InlineKeyboardButton(
                "Back ðŸ”™",
                callback_data="home"
            )
        ],
    ])


def wallet_keyboard():

    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "CBE Bank ðŸ¦",
                callback_data="wallet_cbe"
            ),
            InlineKeyboardButton(
                "Telebirr ðŸ“±",
                callback_data="wallet_telebirr"
            ),
        ],
        [
            InlineKeyboardButton(
                "Back ðŸ”™",
                callback_data="home"
            )
        ],
    ])


# ============================================================
# ADMIN KEYBOARD
# ============================================================

def admin_keyboard():

    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "Statistics ðŸ“Š",
                callback_data="admin_stats"
            ),
            InlineKeyboardButton(
                "Referral Reward ðŸ’°",
                callback_data="admin_reward"
            ),
        ],
        [
            InlineKeyboardButton(
                "Referrals List ðŸ‘¥",
                callback_data="admin_referrals"
            ),
            InlineKeyboardButton(
                "Withdrawals ðŸ’¸",
                callback_data="admin_withdrawals"
            ),
        ],
        [
            InlineKeyboardButton(
                "Suspicious Users âš ï¸",
                callback_data="admin_suspicious"
            ),
            InlineKeyboardButton(
                "Add Task ðŸŽ¯",
                callback_data="admin_add_task"
            ),
        ],
    ])


# ============================================================
# USER PAGES
# ============================================================

async def show_home(query, user_id):

    await safe_edit_message(
        query,

        "ðŸ’° <b>Global Cash Bot</b> ðŸ‘‹\n\n"
        "Welcome back / áŠ¥áŠ•áŠ³áŠ• á‹°áˆ…áŠ“ áˆ˜áŒ¡!\n\n"
        "Please choose an option below to continue / "
        "áˆˆáˆ˜á‰€áŒ áˆ áŠ¨á‰³á‰½ áŠ«áˆ‰á‰µ áŠ áˆ›áˆ«áŒ®á‰½ á‹­áˆáˆ¨áŒ¡á¢",

        reply_markup=main_keyboard(),
    )


async def show_balance(query, user_id):

    balance = get_balance(user_id)

    text = (
        "ðŸ’° <b>Your Account Balance / á‹¨á‰€áˆª áˆ‚áˆ³á‰¥ áˆ˜áˆ¨áŒƒ</b>\n\n"
        f"ðŸ’µ Current Balance: <b>{balance:.2f} ETB</b>\n\n"
        "á‰°áŒ¨áˆ›áˆª Referral áŠ¥áŠ“ Tasks á‰ áˆ˜áˆµáˆ«á‰µ "
        "Account Balance áˆ›áˆ³á‹°áŒ á‹­á‰½áˆ‹áˆ‰!"
    )

    await safe_edit_message(
        query,
        text,
        reply_markup=back_keyboard()
    )


async def show_referral(query, user_id):

    count = get_referral_count(user_id)
    reward = get_referral_reward()

    link = (
        f"https://t.me/{BOT_USERNAME}?start={user_id}"
    )

    text = (
        "ðŸ‘¥ <b>Referral Program / á‹¨áˆªáˆáˆ«áˆ áˆ˜áˆ­áˆƒ áŒá‰¥áˆ­</b>\n\n"
        f"ðŸ‘¤ Total Invited: <b>{count} Users</b>\n"
        f"ðŸŽ Earn Per Refer: <b>{reward:.2f} ETB</b>\n\n"
        "Invite your friends and earn instant rewards! "
        "á‹¨áŠ¥áˆ­áˆµá‹ŽáŠ• áˆá‹© Link á‰ áˆ˜áŒ á‰€áˆ áŒ“á‹°áŠžá‰½á‹ŽáŠ• "
        "á‹­áŒ‹á‰¥á‹™ áŠ¥áŠ“ á‰°áŒ¨áˆ›áˆª áŒˆáŠ•á‹˜á‰¥ á‹­áˆ°á‰¥áˆµá‰¡á¢\n\n"
        "ðŸ”— <b>Your Invite Link / á‹¨áŠ¥áˆ­áˆµá‹Ž áˆŠáŠ•áŠ­á¦</b>\n"
        f"<code>{escape(link)}</code>"
    )

    await safe_edit_message(
        query,
        text,
        reply_markup=referral_keyboard()
    )


async def show_tasks(query, user_id):

    tasks = get_active_tasks()

    lines = [
        "ðŸŽ¯ <b>Available Tasks / á‹¨áˆšáˆ°áˆ© áˆµáˆ«á‹Žá‰½</b>",
        "",
        "Complete tasks to earn extra income! "
        "áˆµáˆ«á‹Žá‰½áŠ• á‰ áˆ˜áˆµáˆ«á‰µ á‰°áŒ¨áˆ›áˆª áŒˆáŠ•á‹˜á‰¥ á‹«áŒáŠ™á¢",
        "",
    ]

    buttons = []

    reward = get_referral_reward()

    lines.append(
        f"ðŸ‘¥ <b>Invite Friends</b> â€” "
        f"Earn <b>{reward:.2f} ETB</b> per referral.\n"
    )

    if tasks:

        for task in tasks:

            lines.append(
                f"ðŸŽ¯ <b>{escape(task['title'])}</b>\n"
                f"{escape(task['description'])}\n"
                f"ðŸ’° Reward: <b>{task['reward']:.2f} ETB</b>"
            )

            buttons.append([
                InlineKeyboardButton(
                    f"Task: {task['title']}",
                    callback_data=f"task_{task['id']}"
                )
            ])

    else:

        lines.append(
            "ðŸ“Œ More tasks coming soon! "
            "á‰°áŒ¨áˆ›áˆª áŠ á‹³á‹²áˆµ áˆµáˆ«á‹Žá‰½ á‰ á‰…áˆ­á‰¡ á‹­áŒ¨áˆ˜áˆ«áˆ‰á¢"
        )

    buttons.append([
        InlineKeyboardButton(
            "Back ðŸ”™",
            callback_data="home"
        )
    ])

    await safe_edit_message(
        query,
        "\n".join(lines),
        reply_markup=InlineKeyboardMarkup(buttons)
    )


async def show_task(query, user_id, task_id):

    task = get_task(task_id)

    if not task or not task["active"]:

        await query.answer(
            "Task not available / á‹­áˆ… áˆµáˆ« áŠ áˆáŠ• áŠ á‹­áŒˆáŠáˆá¢",
            show_alert=True
        )

        return

    paid = user_task_paid(
        user_id,
        task_id
    )

    buttons = [[
        InlineKeyboardButton(
            "Join Channel ðŸ“¢",
            url=task["channel_url"]
        )
    ]]

    if not paid:

        buttons.append([
            InlineKeyboardButton(
                "Verify Task âœ…",
                callback_data=f"verify_task_{task_id}"
            )
        ])

    buttons.append([
        InlineKeyboardButton(
            "Back ðŸ”™",
            callback_data="tasks"
        )
    ])

    status = (
        "âœ… <b>Status: Completed</b>\n\n"
        "á‹¨á‹šáˆ… áˆµáˆ« áŠ­áá‹« áŒˆá‰¥á‰¶áˆá‹Žá‰³áˆá¢"
        if paid
        else
        "â³ <b>Status: Pending</b>\n\n"
        "á‰»áŠ“áˆ‰áŠ• á‰°á‰€áˆ‹á‰…áˆˆá‹ Verify á‹¨áˆšáˆˆá‹áŠ• á‹­áŒ«áŠ‘á¢"
    )

    text = (
        f"ðŸŽ¯ <b>{escape(task['title'])}</b>\n\n"
        f"{escape(task['description'])}\n\n"
        f"ðŸ’° Reward: <b>{task['reward']:.2f} ETB</b>\n"
        f"ðŸ“¢ Channel: <b>{escape(task['channel_username'])}</b>\n\n"
        f"{status}"
    )

    await safe_edit_message(
        query,
        text,
        reply_markup=InlineKeyboardMarkup(buttons)
    )


async def show_wallet(query, user_id):

    wallet = get_wallet(user_id)

    if wallet:

        wallet_type, wallet_number = wallet

        text = (
            "ðŸ‘› <b>Wallet Information / á‹¨á‰°áˆ˜á‹˜áŒˆá‰  á‹ˆáˆŒá‰µ</b>\n\n"
            f"ðŸ’³ Payment Method: <b>{escape(wallet_type)}</b>\n"
            f"ðŸ”¢ Account Number: <code>{escape(wallet_number)}</code>\n\n"
            "á‹¨áŠ­áá‹« áˆ˜áŠ•áŒˆá‹µá‹ŽáŠ• áˆ˜á‰€á‹¨áˆ­ áŠ¨áˆáˆˆáŒ‰ "
            "áŠ¨á‰³á‰½ á‰£áˆ‰á‰µ á‰ á‰°áŠ–á‰½ á‹­áˆáˆ¨áŒ¡á¢"
        )

    else:

        text = (
            "ðŸ‘› <b>Connect Wallet / á‹ˆáˆŒá‰µ áˆ˜á‹áŒá‰¥</b>\n\n"
            "âš ï¸ No wallet connected yet! "
            "áŠ¥áˆµáŠ«áˆáŠ• áˆáŠ•áˆ áŠ á‹­áŠá‰µ á‹¨áŠ­áá‹« áˆ˜áŠ•áŒˆá‹µ "
            "áŠ áˆáˆ˜á‹˜áŒˆá‰¡áˆá¢\n\n"
            "Please select your payment method below / "
            "áŠ¥á‰£áŠ­á‹ŽáŠ• á‹¨áŠ­áá‹« áˆ˜áŠ•áŒˆá‹µá‹ŽáŠ• á‹­áˆáˆ¨áŒ¡á¦"
        )

    await safe_edit_message(
        query,
        text,
        reply_markup=wallet_keyboard()
    )


async def show_withdraw(query, user_id, context):

    user = get_user(user_id)

    if not user:
        return

    if user["suspicious"]:

        await safe_edit_message(
            query,

            f"âš ï¸ <b>Account Suspended / á‹¨á‹°áˆ…áŠ•áŠá‰µ áˆ›áˆ³áˆ°á‰¢á‹«</b>\n\n"
            f"Your account is under audit. "
            f"Support áˆˆáˆ›áŒáŠ˜á‰µá¦ {SUPPORT_USERNAME}",

            reply_markup=back_keyboard(),
        )

        return

    wallet = get_wallet(user_id)

    if not wallet:

        await safe_edit_message(
            query,

            "ðŸ’¸ <b>Withdrawal / áŒˆáŠ•á‹˜á‰¥ áˆ›á‹áŒ£á‰µ</b>\n\n"
            "âš ï¸ <b>Connect Wallet First!</b>\n\n"
            "áŒˆáŠ•á‹˜á‰¥ áˆˆáˆ›á‹áŒ£á‰µ á‰ áˆ˜áŒ€áˆ˜áˆªá‹« Wallet Connect "
            "áˆ›á‹µáˆ¨áŒ áŠ áˆˆá‰¥á‹Žá‰µá¢\n"
            "áŠ¥á‰£áŠ­á‹ŽáŠ• á‹¨ CBE á‹ˆá‹­áˆ Telebirr á‰áŒ¥áˆ­á‹ŽáŠ• "
            "áŠ áˆµá‰€á‹µáˆ˜á‹ á‹­áˆáˆ¨áŒ¡á¢",

            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "Connect Wallet ðŸ‘›",
                        callback_data="wallet"
                    )
                ],
                [
                    InlineKeyboardButton(
                        "Back ðŸ”™",
                        callback_data="home"
                    )
                ],
            ]),
        )

        return

    wallet_type, wallet_number = wallet

    balance = float(user["balance"])

    context.user_data["withdraw_step"] = "amount"

    await safe_edit_message(
        query,

        f"ðŸ’¸ <b>Withdraw Money / áŒˆáŠ•á‹˜á‰¥ áˆ›á‹áŒ£á‰µ</b>\n\n"
        f"ðŸ’° Available Balance: <b>{balance:.2f} ETB</b>\n"
        f"ðŸ’³ Method: <b>{escape(wallet_type)}</b> "
        f"(<code>{escape(wallet_number)}</code>)\n"
        f"ðŸ“Œ Minimum Withdrawal: <b>{MIN_WITHDRAWAL:.2f} ETB</b>\n\n"
        "ðŸ‘‰ <b>Enter Amount / áˆ›á‹áŒ£á‰µ á‹¨áˆšáˆáˆáŒ‰á‰µáŠ• "
        "áˆ˜áŒ áŠ• á‹«áˆµáŒˆá‰¡á¦</b>\n\n"
        "áˆáˆ³áˆŒ (Example):\n"
        "<code>50</code>",

        reply_markup=back_keyboard(),
    )


async def show_support(query, user_id):

    text = (
        "ðŸ“ž <b>Support & Digital Services / á‹µáŒ‹á áŠ¥áŠ“ áˆ›áˆµá‰³á‹ˆá‰‚á‹«</b>\n\n"
        "áŠ¥áŠá‹šáˆ…áŠ• áŠ áŒˆáˆáŒáˆŽá‰¶á‰½ áˆ›áŒáŠ˜á‰µ áŠ¨áˆáˆˆáŒ‰ "
        "á‰  Support áˆ˜áˆµáˆ˜áˆ«á‰½áŠ• DM á‹«á‹µáˆ­áŒ‰áˆáŠ•á¦\n\n"
        "ðŸš€ <b>Buy & Sell USDT "
        "(USDT áˆ˜áŒá‹›á‰µ áŠ¥áŠ“ áˆ˜áˆ¸áŒ¥ á‹¨áˆá‰µáˆáˆáŒ‰ DM áŠ á‹µáˆ­áŒ‰áŠ•)</b>\n"
        "ðŸ“¢ Telegram Channel Promotion\n"
        "ðŸ‘¥ Group Members & Growth\n"
        "ðŸ¢ Business & Product Promotion\n"
        "ðŸ“± App & Website Promotion\n\n"
        "Contact Admin / áˆˆáˆ›áŠáŒ‹áŒˆáˆ­á¦"
    )

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "Contact Support ðŸ’¬",
                url="https://t.me/AmanM_12"
            )
        ],
        [
            InlineKeyboardButton(
                "Back ðŸ”™",
                callback_data="home"
            )
        ],
    ])

    await safe_edit_message(
        query,
        text,
        reply_markup=keyboard
    )


# ============================================================
# JOIN REQUIRED
# ============================================================

async def show_join_required(
    update,
    context,
    missing
):

    text = (
        "ðŸ‘‹ <b>Welcome to Global Cash Bot!</b>\n\n"
        "To start using the bot, please join all required channels below.\n\n"
        "áˆáˆ‰áŠ•áˆ á‰»áŠ“áˆŽá‰½ áŠ¨á‰°á‰€áˆ‹á‰€áˆ‰ á‰ áŠ‹áˆ‹ "
        "<b>Verify âœ…</b> á‹¨áˆšáˆˆá‹áŠ• á‹­áŒ«áŠ‘á¢\n\n"
        f"ðŸ“Œ Remaining Channels: <b>{len(missing)}</b>"
    )

    if update.callback_query:

        await safe_edit_message(
            update.callback_query,
            text,
            reply_markup=channel_keyboard(missing)
        )

    else:

        await update.message.reply_text(
            text,
            reply_markup=channel_keyboard(missing),
            parse_mode="HTML"
        )


# ============================================================
# WALLET INPUT
# ============================================================

async def ask_wallet(
    query,
    user_id,
    wallet_type,
    context
):

    context.user_data["wallet_type"] = wallet_type
    context.user_data["wallet_step"] = "number"

    if wallet_type == "CBE":

        instruction = (
            "ðŸ¦ <b>CBE Bank Account / á‹¨á‰£áŠ•áŠ­ áˆ‚áˆ³á‰¥</b>\n\n"
            "Please enter your 13-digit CBE account number.\n"
            "á‹¨á‰£áŠ•áŠ­ á‰áŒ¥áˆ­á‹ŽáŠ• á‹«áˆµáŒˆá‰¡ "
            "(á‰  <b>1000</b> á‹¨áˆšáŒ€áˆáˆ­ 13 á‹²áŒ‚á‰µ á‰áŒ¥áˆ­)á¢\n\n"
            "Example:\n"
            "<code>1000123456789</code>"
        )

    else:

        instruction = (
            "ðŸ“± <b>Telebirr Account / á‰´áˆŒá‰¥áˆ­</b>\n\n"
            "Please enter your 10-digit Telebirr phone number.\n"
            "á‹¨á‰´áˆŒá‰¥áˆ­ áˆµáˆáŠ­ á‰áŒ¥áˆ­á‹ŽáŠ• á‹«áˆµáŒˆá‰¡ "
            "(á‰  <b>09</b> á‹ˆá‹­áˆ <b>07</b> á‹¨áˆšáŒ€áˆáˆ­)á¢\n\n"
            "Example:\n"
            "<code>0912345678</code>"
        )

    await safe_edit_message(
        query,
        instruction,
        reply_markup=back_keyboard()
    )


# ============================================================
# ADMIN
# ============================================================

def is_admin(user_id):

    return user_id == ADMIN_ID


async def show_admin(query):

    if not is_admin(query.from_user.id):

        await query.answer(
            "â›” Admin access only!",
            show_alert=True
        )

        return

    await safe_edit_message(
        query,

        "ðŸ›  <b>Admin Control Panel / á‹¨áŠ á‹µáˆšáŠ• á“áŠáˆ</b>\n\n"
        "Select an option:",

        reply_markup=admin_keyboard()
    )


async def show_admin_stats(query):

    if not is_admin(query.from_user.id):
        return

    conn = db()

    try:

        total_users = conn.execute(
            "SELECT COUNT(*) AS c FROM users"
        ).fetchone()["c"]

        verified_users = conn.execute(
            "SELECT COUNT(*) AS c FROM users WHERE joined_all=1"
        ).fetchone()["c"]

        total_balance = conn.execute(
            "SELECT COALESCE(SUM(balance),0) AS s FROM users"
        ).fetchone()["s"]

        successful_referrals = conn.execute(
            "SELECT COUNT(*) AS c FROM referrals WHERE status='paid'"
        ).fetchone()["c"]

        pending_withdrawals = conn.execute(
            "SELECT COUNT(*) AS c FROM withdrawals WHERE status='pending'"
        ).fetchone()["c"]

        suspicious_accounts = conn.execute(
            "SELECT COUNT(*) AS c FROM users WHERE suspicious=1"
        ).fetchone()["c"]

    finally:
        conn.close()

    text = (
        "ðŸ“Š <b>System Statistics</b>\n\n"
        f"ðŸ‘¥ Total Users: <b>{total_users}</b>\n"
        f"âœ… Verified Users: <b>{verified_users}</b>\n"
        f"ðŸ’° Total User Balance: <b>{float(total_balance):.2f} ETB</b>\n"
        f"ðŸ‘¥ Successful Referrals: <b>{successful_referrals}</b>\n"
        f"ðŸ’¸ Pending Withdrawals: <b>{pending_withdrawals}</b>\n"
        f"âš ï¸ Suspicious Accounts: <b>{suspicious_accounts}</b>"
    )

    await safe_edit_message(
        query,
        text,
        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "Back ðŸ”™",
                    callback_data="admin"
                )
            ]
        ])
    )


async def show_admin_reward(query):

    if not is_admin(query.from_user.id):
        return

    reward = get_referral_reward()

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "Change Reward âœï¸",
                callback_data="admin_change_reward"
            )
        ],
        [
            InlineKeyboardButton(
                "Back ðŸ”™",
                callback_data="admin"
            )
        ],
    ])

    await safe_edit_message(
        query,

        f"ðŸ’° <b>Referral Reward Settings</b>\n\n"
        f"Current Reward: <b>{reward:.2f} ETB</b>",

        reply_markup=keyboard
    )


async def show_admin_referrals(query):

    if not is_admin(query.from_user.id):
        return

    conn = db()

    try:

        rows = conn.execute("""
            SELECT referrer_id,
                   COUNT(*) AS count
            FROM referrals
            WHERE status='paid'
            GROUP BY referrer_id
            ORDER BY count DESC
            LIMIT 20
        """).fetchall()

    finally:
        conn.close()

    if not rows:

        text = (
            "ðŸ‘¥ <b>Top Referrers</b>\n\n"
            "No referral data available."
        )

    else:

        lines = [
            "ðŸ‘¥ <b>Top Referrers List</b>",
            ""
        ]

        for row in rows:

            lines.append(
                f"ðŸ‘¤ <code>{row['referrer_id']}</code> "
                f"â€” <b>{row['count']} Refers</b>"
            )

        text = "\n".join(lines)

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "Search User ðŸ”Ž",
                callback_data="admin_referral_lookup"
            )
        ],
        [
            InlineKeyboardButton(
                "Back ðŸ”™",
                callback_data="admin"
            )
        ],
    ])

    await safe_edit_message(
        query,
        text,
        reply_markup=keyboard
    )


async def admin_referral_lookup(
    query,
    context
):

    if not is_admin(query.from_user.id):
        return

    context.user_data.clear()
    context.user_data["admin_step"] = "referral_lookup"

    await safe_edit_message(
        query,

        "ðŸ”Ž <b>Search User Referrals</b>\n\n"
        "Enter User ID:\n\n"
        "Example:\n"
        "<code>123456789</code>",

        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "Cancel âŒ",
                    callback_data="admin"
                )
            ]
        ])
    )


async def show_referral_details_for_message(
    message,
    referrer_id
):

    conn = db()

    try:

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
            WHERE r.referrer_id=?
              AND r.status='paid'
            ORDER BY r.id DESC
            LIMIT 30
        """, (
            referrer_id,
        )).fetchall()

    finally:
        conn.close()

    if not rows:

        text = (
            "ðŸ‘¥ <b>Invited List</b>\n\n"
            f"Referrer ID: <code>{referrer_id}</code>\n\n"
            "No referrals found."
        )

    else:

        lines = [
            "ðŸ‘¥ <b>Invited Users List</b>",
            "",
            f"Referrer ID: <code>{referrer_id}</code>",
            f"Total: <b>{len(rows)}</b>",
            "",
        ]

        for index, row in enumerate(
            rows,
            start=1
        ):

            person = (
                f"@{row['username']}"
                if row["username"]
                else row["first_name"] or "No Name"
            )

            lines.append(
                f"{index}. ðŸ‘¤ <b>{escape(person)}</b>\n"
                f"   ðŸ†” <code>{row['referred_id']}</code>\n"
                f"   ðŸŽ Reward: "
                f"<b>{row['reward_amount']:.2f} ETB</b>"
            )

        text = "\n\n".join(lines)

    await message.reply_text(
        text,
        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "Admin Panel ðŸ› ",
                    callback_data="admin"
                )
            ],
            [
                InlineKeyboardButton(
                    "Referrals ðŸ‘¥",
                    callback_data="admin_referrals"
                )
            ],
        ]),
        parse_mode="HTML"
    )


async def show_admin_withdrawals(query):

    if not is_admin(query.from_user.id):
        return

    conn = db()

    try:

        rows = conn.execute("""
            SELECT *
            FROM withdrawals
            WHERE status='pending'
            ORDER BY id DESC
            LIMIT 20
        """).fetchall()

    finally:
        conn.close()

    if not rows:

        await safe_edit_message(
            query,

            "ðŸ’¸ <b>Pending Withdrawals</b>\n\n"
            "No pending withdrawal requests.",

            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "Back ðŸ”™",
                        callback_data="admin"
                    )
                ]
            ])
        )

        return

    buttons = []

    lines = [
        "ðŸ’¸ <b>Pending Withdrawal Requests</b>",
        ""
    ]

    for row in rows:

        lines.append(
            f"ðŸ†” <b>#{row['id']}</b> | "
            f"User ID: <code>{row['user_id']}</code>\n"
            f"ðŸ’° Amount: {row['amount']:.2f} ETB | "
            f"Method: {escape(row['wallet_type'])}\n"
            f"ðŸ”¢ Number: "
            f"<code>{escape(row['wallet_number'])}</code>\n"
        )

        buttons.append([
            InlineKeyboardButton(
                f"Approve #{row['id']} âœ…",
                callback_data=f"approve_wd_{row['id']}"
            ),
            InlineKeyboardButton(
                f"Reject #{row['id']} âŒ",
                callback_data=f"reject_wd_{row['id']}"
            ),
        ])

    buttons.append([
        InlineKeyboardButton(
            "Back ðŸ”™",
            callback_data="admin"
        )
    ])

    await safe_edit_message(
        query,
        "\n".join(lines),
        reply_markup=InlineKeyboardMarkup(buttons)
    )


async def show_admin_suspicious(query):

    if not is_admin(query.from_user.id):
        return

    conn = db()

    try:

        rows = conn.execute("""
            SELECT
                user_id,
                username,
                first_name,
                balance
            FROM users
            WHERE suspicious=1
            ORDER BY user_id DESC
            LIMIT 50
        """).fetchall()

    finally:
        conn.close()

    if not rows:

        text = (
            "âš ï¸ <b>Suspicious Accounts</b>\n\n"
            "No suspicious accounts found."
        )

    else:

        lines = [
            "âš ï¸ <b>Flagged/Suspicious Accounts</b>",
            ""
        ]

        for row in rows:

            name = (
                f"@{row['username']}"
                if row["username"]
                else row["first_name"] or "No Name"
            )

            lines.append(
                f"ðŸ‘¤ {escape(name)}\n"
                f"ðŸ†” <code>{row['user_id']}</code>\n"
                f"ðŸ’° {row['balance']:.2f} ETB\n"
            )

        text = "\n".join(lines)

    await safe_edit_message(
        query,
        text,
        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "Back ðŸ”™",
                    callback_data="admin"
                )
            ]
        ])
    )


async def start_add_task(
    query,
    context
):

    if not is_admin(query.from_user.id):
        return

    context.user_data.clear()
    context.user_data["admin_step"] = "task_title"

    await safe_edit_message(
        query,

        "ðŸŽ¯ <b>Add New Task</b>\n\n"
        "Step 1/4: Enter Task Title.\n\n"
        "Example:\n"
        "<code>Join Aman Income Lab Channel</code>",

        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "Cancel âŒ",
                    callback_data="admin"
                )
            ]
        ])
    )


# ============================================================
# START
# ============================================================

async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    user = update.effective_user

    referrer_id = None

    if context.args:

        try:
            referrer_id = int(
                context.args[0]
            )
        except ValueError:
            referrer_id = None

    create_or_update_user(
        user,
        referred_by=referrer_id
    )

    missing = await missing_channels(
        context.bot,
        user.id
    )

    if missing:

        set_joined_all(
            user.id,
            False
        )

        await show_join_required(
            update,
            context,
            missing
        )

        return

    set_joined_all(
        user.id,
        True
    )

    result = process_referral_reward(
        user.id
    )

    if result:

        try:

            await context.bot.send_message(
                chat_id=result["inviter_id"],

                text=(
                    "ðŸŽ‰ <b>New Referral Joined! / "
                    "áŠ á‹²áˆµ áˆ°á‹ á‰°á‰€áˆ‹á‰…áˆáˆ!</b>\n\n"
                    f"ðŸ‘¤ User ID: "
                    f"<code>{result['referred_id']}</code>\n"
                    f"ðŸŽ Earned Reward: "
                    f"<b>{result['amount']:.2f} ETB</b>"
                ),

                parse_mode="HTML"
            )

        except Exception:

            logger.exception(
                "Could not notify inviter."
            )

    await update.message.reply_text(

        "ðŸŽ‰ <b>Welcome to Global Cash Bot! / "
        "áŠ¥áŠ•áŠ³áŠ• á‹°áˆ…áŠ“ áˆ˜áŒ¡!</b>\n\n"
        "Your account is verified! "
        "áŠ áŠ«á‹áŠ•á‰µá‹Ž á‰ á‰µáŠ­áŠ­áˆ á‰°áˆ¨áŒ‹áŒáŒ§áˆá¢\n\n"
        "Choose an option below to start earning / "
        "áŠ¨á‰³á‰½ áŠ«áˆ‰á‰µ áŠ áˆ›áˆ«áŒ®á‰½ á‹­áˆáˆ¨áŒ¡á¦",

        reply_markup=main_keyboard(),
        parse_mode="HTML"
    )


# ============================================================
# VERIFY USER
# ============================================================

async def verify_user(
    query,
    context
):

    user_id = query.from_user.id

    missing = await missing_channels(
        context.bot,
        user_id
    )

    if missing:

        set_joined_all(
            user_id,
            False
        )

        text = (
            "âŒ <b>Verification Failed! / "
            "áŠ áˆá‰°áˆ¨áŒ‹áŒˆáŒ áˆ</b>\n\n"
            "You haven't joined all required channels.\n"
            f"ðŸ“Œ Remaining Channels: "
            f"<b>{len(missing)}</b>\n\n"
            "áŠ¥á‰£áŠ­á‹ŽáŠ• á‹¨á‰€áˆ©á‰µáŠ• á‰»áŠ“áˆŽá‰½ "
            "á‰°á‰€áˆ‹á‰…áˆˆá‹ á‹µáŒ‹áˆš Verify á‹¨áˆšáˆˆá‹áŠ• á‹­áŒ«áŠ‘á¢"
        )

        await safe_edit_message(
            query,
            text,
            reply_markup=channel_keyboard(missing)
        )

        return

    set_joined_all(
        user_id,
        True
    )

    result = process_referral_reward(
        user_id
    )

    if result:

        try:

            await context.bot.send_message(
                chat_id=result["inviter_id"],

                text=(
                    "ðŸŽ‰ <b>New Referral Joined! / "
                    "áŠ á‹²áˆµ áˆ°á‹ á‰°á‰€áˆ‹á‰…áˆáˆ!</b>\n\n"
                    f"ðŸ‘¤ User ID: "
                    f"<code>{result['referred_id']}</code>\n"
                    f"ðŸŽ Earned Reward: "
                    f"<b>{result['amount']:.2f} ETB</b>"
                ),

                parse_mode="HTML"
            )

        except Exception:

            logger.exception(
                "Could not notify inviter."
            )

    await safe_edit_message(

        query,

        "âœ… <b>Successfully Verified! / "
        "á‰ á‰µáŠ­áŠ­áˆ á‰°áˆ¨áŒ‹áŒáŒ§áˆ!</b>\n\n"
        "Welcome to <b>Global Cash Bot</b>! ðŸ’°",

        reply_markup=main_keyboard()
    )


# ============================================================
# VERIFY TASK
# ============================================================

async def verify_task(
    query,
    context,
    task_id
):

    user_id = query.from_user.id

    task = get_task(task_id)

    if not task or not task["active"]:

        await query.answer(
            "Task not available / á‹­áˆ… áˆµáˆ« áŠ á‹­áŒˆáŠáˆá¢",
            show_alert=True
        )

        return

    if user_task_paid(
        user_id,
        task_id
    ):

        await query.answer(
            "Task already completed / áŠ áˆµá‰€á‹µáˆ˜á‹ áˆ°áˆ­á‰°á‹á‰³áˆá¢",
            show_alert=True
        )

        return

    try:

        member = await context.bot.get_chat_member(
            chat_id=task["channel_username"],
            user_id=user_id
        )

        if not is_channel_member_status(
            member.status
        ):

            await query.answer(
                "âŒ Please join the channel first! "
                "áŒˆáŠ“ áŠ áˆá‰°á‰€áˆ‹á‰€áˆ‰áˆá¢",
                show_alert=True
            )

            return

    except Exception:

        await query.answer(
            "Verification failed! á‹µáŒ‹áˆš á‹­áˆžáŠ­áˆ©á¢",
            show_alert=True
        )

        return

    reward = float(
        task["reward"]
    )

    add_balance(
        user_id,
        reward
    )

    save_user_task(
        user_id,
        task_id,
        verified=True,
        paid=True
    )

    await query.answer(
        f"ðŸŽ‰ +{reward:.2f} ETB Added to your balance!",
        show_alert=True
    )

    await show_task(
        query,
        user_id,
        task_id
    )


# ============================================================
# WALLET INPUT
# ============================================================

async def save_wallet_from_input(
    update,
    context
):

    user = update.effective_user

    wallet_type = context.user_data.get(
        "wallet_type"
    )

    if not wallet_type:
        return False

    number = update.message.text.strip()

    valid = False

    if wallet_type == "CBE":

        valid = (
            number.isdigit()
            and len(number) == 13
            and number.startswith("1000")
        )

    elif wallet_type == "Telebirr":

        valid = (
            number.isdigit()
            and len(number) == 10
            and (
                number.startswith("09")
                or number.startswith("07")
            )
        )

    if not valid:

        if wallet_type == "CBE":

            msg = (
                "âŒ Invalid CBE Account!\n"
                "Must be 13 digits starting with 1000."
            )

        else:

            msg = (
                "âŒ Invalid Telebirr Number!\n"
                "Must be 10 digits starting with 09 or 07."
            )

        await update.message.reply_text(
            msg + "\n\náŠ¥á‰£áŠ­á‹ŽáŠ• á‹µáŒ‹áˆš á‹­áˆžáŠ­áˆ©á¢"
        )

        return True

    if wallet_exists_for_other_user(
        wallet_type,
        number,
        user.id
    ):

        await update.message.reply_text(
            "âš ï¸ <b>Wallet already connected to another account!</b>\n\n"
            "á‹­áˆ… á‰áŒ¥áˆ­ áŠ¨áˆŒáˆ‹ áŠ áŠ«á‹áŠ•á‰µ áŒ‹áˆ­ á‰°á‹«á‹­á‹Ÿáˆá¢",
            parse_mode="HTML"
        )

        return True

    saved = set_wallet(
        user.id,
        wallet_type,
        number
    )

    if not saved:

        await update.message.reply_text(
            "âš ï¸ Failed to save wallet.",
            parse_mode="HTML"
        )

        return True

    context.user_data.pop(
        "wallet_type",
        None
    )

    context.user_data.pop(
        "wallet_step",
        None
    )

    await update.message.reply_text(

        "âœ… <b>Wallet Connected Successfully! / "
        "á‹ˆáˆŒá‰µá‹Ž á‰°áˆ˜á‹áŒá‰§áˆ!</b>\n\n"
        f"ðŸ’³ Method: <b>{escape(wallet_type)}</b>\n"
        f"ðŸ”¢ Account: <code>{escape(number)}</code>\n\n"
        "Now you can withdraw your balance / "
        "áŠ áˆáŠ• áˆ›á‹áŒ£á‰µ á‹­á‰½áˆ‹áˆ‰á¢",

        reply_markup=main_keyboard(),
        parse_mode="HTML"
    )

    return True


# ============================================================
# WITHDRAW AMOUNT
# ============================================================

async def handle_withdraw_amount_input(
    update,
    context
):

    user = update.effective_user
    text = update.message.text.strip()

    try:

        amount = float(text)

    except ValueError:

        await update.message.reply_text(
            "âŒ Invalid input! "
            "Please enter a valid number.\n\n"
            "áŠ¥á‰£áŠ­á‹ŽáŠ• á‰µáŠ­áŠ­áˆˆáŠ› á‰áŒ¥áˆ­ á‹«áˆµáŒˆá‰¡ "
            "(áˆáˆ³áˆŒá¦ <code>50</code>)á¢",
            parse_mode="HTML"
        )

        return True

    balance = get_balance(
        user.id
    )

    if amount <= 0:

        await update.message.reply_text(
            "âŒ Amount must be greater than 0!"
        )

        return True

    if balance < MIN_WITHDRAWAL:

        await update.message.reply_text(

            f"âŒ <b>Insufficient Balance / "
            f"á‹¨á‰€áˆª áˆ‚áˆ³á‰¥ áˆ›áˆ³áˆ°á‰¢á‹«!</b>\n\n"
            f"Your Current Balance: "
            f"<b>{balance:.2f} ETB</b>\n"
            f"Minimum Withdrawal: "
            f"<b>{MIN_WITHDRAWAL:.2f} ETB</b>\n\n"
            "á‹­á‰…áˆ­á‰³! á‹«áˆŽá‰µ á‰€áˆª áˆ‚áˆ³á‰¥ "
            "áˆˆáˆ›á‹áŒ£á‰µ á‰ á‰‚ áŠ á‹­á‹°áˆˆáˆá¢",

            reply_markup=back_keyboard(),
            parse_mode="HTML"
        )

        context.user_data.pop(
            "withdraw_step",
            None
        )

        return True

    if amount < MIN_WITHDRAWAL:

        await update.message.reply_text(

            f"âŒ Minimum withdrawal is "
            f"<b>{MIN_WITHDRAWAL:.2f} ETB</b>.",

            reply_markup=back_keyboard(),
            parse_mode="HTML"
        )

        return True

    if amount > balance:

        await update.message.reply_text(

            f"âŒ <b>Insufficient Balance!</b>\n\n"
            f"Available: <b>{balance:.2f} ETB</b>",

            reply_markup=back_keyboard(),
            parse_mode="HTML"
        )

        return True

    wallet = get_wallet(
        user.id
    )

    if not wallet:

        await update.message.reply_text(
            "âŒ No wallet connected!",
            reply_markup=wallet_keyboard()
        )

        context.user_data.pop(
            "withdraw_step",
            None
        )

        return True

    wallet_type, wallet_number = wallet

    context.user_data[
        "pending_withdraw_amount"
    ] = amount

    context.user_data.pop(
        "withdraw_step",
        None
    )

    confirm_keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "Confirm ðŸ’¸",
                callback_data="confirm_withdrawal"
            )
        ],
        [
            InlineKeyboardButton(
                "Cancel âŒ",
                callback_data="home"
            )
        ],
    ])

    await update.message.reply_text(

        "ðŸ’¸ <b>Withdrawal Confirmation / "
        "áˆ›áˆ¨áŒ‹áŒˆáŒ«</b>\n\n"
        f"ðŸ’° Requested Amount: "
        f"<b>{amount:.2f} ETB</b>\n"
        f"ðŸ¦ Payment Method: "
        f"<b>{escape(wallet_type)}</b>\n"
        f"ðŸ”¢ Account Number: "
        f"<code>{escape(wallet_number)}</code>\n\n"
        "áˆˆáˆ˜á‰€áŒ áˆ <b>Confirm ðŸ’¸</b> "
        "á‹¨áˆšáˆˆá‹áŠ• á‹­áŒ«áŠ‘á¦",

        reply_markup=confirm_keyboard,
        parse_mode="HTML"
    )

    return True


# ============================================================
# CONFIRM WITHDRAWAL
# ============================================================

async def confirm_withdrawal_process(
    query,
    context
):

    user = query.from_user

    amount = context.user_data.get(
        "pending_withdraw_amount"
    )

    if not amount:

        await query.answer(
            "No pending request / áˆáŠ•áˆ áŒ¥á‹«á‰„ áŠ áˆá‰°áŒˆáŠ˜áˆá¢",
            show_alert=True
        )

        return

    wallet = get_wallet(
        user.id
    )

    if not wallet:

        await query.answer(
            "Wallet not found!",
            show_alert=True
        )

        return

    wallet_type, wallet_number = wallet

    conn = db()

    try:

        conn.execute(
            "BEGIN IMMEDIATE"
        )

        cur = conn.execute("""
            UPDATE users
            SET balance=balance-?
            WHERE user_id=?
              AND balance>=?
              AND suspicious=0
        """, (
            amount,
            user.id,
            amount,
        ))

        if cur.rowcount != 1:

            conn.rollback()

            await safe_edit_message(
                query,

                "âŒ Transaction Failed / "
                "á‹¨á‰€áˆª áˆ‚áˆ³á‰¥ áˆ›áŠáˆµ áŠ áŒ‹áŒ¥áˆŸáˆ!",

                reply_markup=back_keyboard()
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
            VALUES(?,?,?,?,?,?)
        """, (
            user.id,
            amount,
            wallet_type,
            wallet_number,
            "pending",
            now(),
        ))

        withdrawal_id = cur.lastrowid

        conn.commit()

    except Exception:

        conn.rollback()

        logger.exception(
            "Withdrawal creation failed."
        )

        await safe_edit_message(
            query,
            "âŒ Error creating withdrawal request.",
            reply_markup=back_keyboard()
        )

        return

    finally:
        conn.close()

    context.user_data.pop(
        "pending_withdraw_amount",
        None
    )

    await safe_edit_message(

        query,

        "âœ… <b>Withdrawal Requested Successfully!</b>\n\n"
        f"ðŸ†” Request ID: <b>#{withdrawal_id}</b>\n"
        f"ðŸ’° Amount: <b>{amount:.2f} ETB</b>\n"
        f"ðŸ¦ Method: <b>{escape(wallet_type)}</b>\n"
        f"ðŸ”¢ Account: <code>{escape(wallet_number)}</code>\n\n"
        "â³ Status: <b>Pending Admin Approval</b>\n\n"
        "áŒ¥á‹«á‰„á‹Ž áˆˆáŠ á‹µáˆšáŠ• á‰°áˆáŠ³áˆá¢",

        reply_markup=main_keyboard()
    )

    try:

        await context.bot.send_message(

            chat_id=ADMIN_ID,

            text=(
                "ðŸ’³ <b>New Withdrawal Request!</b>\n\n"
                f"ðŸ†” Request ID: <b>#{withdrawal_id}</b>\n"
                f"ðŸ‘¤ User ID: <code>{user.id}</code>\n"
                f"ðŸ’° Amount: <b>{amount:.2f} ETB</b>\n"
                f"ðŸ¦ Method: <b>{escape(wallet_type)}</b>\n"
                f"ðŸ”¢ Account: "
                f"<code>{escape(wallet_number)}</code>"
            ),

            parse_mode="HTML"
        )

    except Exception:

        logger.exception(
            "Could not notify admin."
        )


# ============================================================
# APPROVE WITHDRAWAL
# ============================================================

async def approve_withdrawal(
    query,
    withdrawal_id,
    context
):

    if not is_admin(
        query.from_user.id
    ):
        return

    conn = db()

    try:

        cur = conn.execute("""
            UPDATE withdrawals
            SET status='approved'
            WHERE id=?
              AND status='pending'
        """, (
            withdrawal_id,
        ))

        if cur.rowcount != 1:

            conn.rollback()

            await query.answer(
                "Already processed / á‰°áˆµá‰°áŠ“áŒá‹·áˆá¢",
                show_alert=True
            )

            return

        row = conn.execute("""
            SELECT *
            FROM withdrawals
            WHERE id=?
        """, (
            withdrawal_id,
        )).fetchone()

        conn.commit()

    finally:
        conn.close()

    await query.answer(
        "âœ… Approved!",
        show_alert=True
    )

    # IMPORTANT:
    # Approval does not mean actual bank/Telebirr transfer.
    # Admin must make the real payment separately.

    try:

        await context.bot.send_message(

            chat_id=row["user_id"],

            text=(
                "âœ… <b>Withdrawal Approved!</b>\n\n"
                f"ðŸ†” Request ID: <b>#{row['id']}</b>\n"
                f"ðŸ’° Amount: <b>{row['amount']:.2f} ETB</b>\n"
                f"ðŸ¦ Account: "
                f"<code>{escape(row['wallet_number'])}</code>\n\n"
                "á‹¨Withdrawal áŒ¥á‹«á‰„á‹Ž á‰ Admin á‰°áˆá‰…á‹·áˆá¢\n"
                "áŠ­áá‹«á‹ á‰ á‰°áŒá‰£áˆ­ áŠ¨á‰°áˆ‹áŠ¨ á‰ áŠ‹áˆ‹ "
                "á‹¨Payment áˆ›áˆ¨áŒ‹áŒˆáŒ« á‹­áˆ°áŒ£áˆá¢"
            ),

            parse_mode="HTML"
        )

    except Exception:

        logger.exception(
            "Could not notify user."
        )

    await show_admin_withdrawals(
        query
    )


# ============================================================
# REJECT WITHDRAWAL
# ============================================================

async def reject_withdrawal(
    query,
    withdrawal_id,
    context
):

    if not is_admin(
        query.from_user.id
    ):
        return

    conn = db()

    try:

        cur = conn.execute("""
            UPDATE withdrawals
            SET status='rejected'
            WHERE id=?
              AND status='pending'
        """, (
            withdrawal_id,
        ))

        if cur.rowcount != 1:

            conn.rollback()

            await query.answer(
                "Already processed!",
                show_alert=True
            )

            return

        row = conn.execute("""
            SELECT *
            FROM withdrawals
            WHERE id=?
        """, (
            withdrawal_id,
        )).fetchone()

        conn.execute("""
            UPDATE users
            SET balance=balance+?
            WHERE user_id=?
        """, (
            row["amount"],
            row["user_id"],
        ))

        conn.commit()

    finally:
        conn.close()

    await query.answer(
        "âŒ Rejected and refunded!",
        show_alert=True
    )

    try:

        await context.bot.send_message(

            chat_id=row["user_id"],

            text=(
                "âŒ <b>Withdrawal Rejected / "
                "áŠ áˆá‰°á‰€á‰ áˆˆáˆ!</b>\n\n"
                f"ðŸ†” Request ID: <b>#{row['id']}</b>\n"
                f"ðŸ’° Refunded Amount: "
                f"<b>{row['amount']:.2f} ETB</b>\n\n"
                "áŒˆáŠ•á‹˜á‰¡ á‹ˆá‹° á‰¦á‰µ áŠ áŠ«á‹áŠ•á‰µá‹Ž "
                "á‰°áˆ˜áˆáˆ·áˆá¢"
            ),

            parse_mode="HTML"
        )

    except Exception:

        logger.exception(
            "Could not notify user."
        )

    await show_admin_withdrawals(
        query
    )


# ============================================================
# CALLBACK HANDLER
# ============================================================

async def callback_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    query = update.callback_query

    data = query.data

    user = query.from_user

    # Answer callback exactly once by default.
    # Specific branches may answer again only when
    # they need an alert.
    try:
        await query.answer()
    except Exception:
        pass

    create_or_update_user(
        user
    )

    # --------------------------------------------------------
    # USER HOME
    # --------------------------------------------------------

    if data == "home":

        context.user_data.clear()

        missing = await missing_channels(
            context.bot,
            user.id
        )

        if missing:

            set_joined_all(
                user.id,
                False
            )

            await show_join_required(
                update,
                context,
                missing
            )

            return

        set_joined_all(
            user.id,
            True
        )

        await show_home(
            query,
            user.id
        )

        return

    # --------------------------------------------------------
    # VERIFY
    # --------------------------------------------------------

    if data == "verify":

        await verify_user(
            query,
            context
        )

        return

    # --------------------------------------------------------
    # USER MENU
    # --------------------------------------------------------

    if data == "balance":

        await show_balance(
            query,
            user.id
        )

        return

    if data == "referral":

        await show_referral(
            query,
            user.id
        )

        return

    if data == "tasks":

        await show_tasks(
            query,
            user.id
        )

        return

    if data == "withdraw":

        await show_withdraw(
            query,
            user.id,
            context
        )

        return

    if data == "confirm_withdrawal":

        await confirm_withdrawal_process(
            query,
            context
        )

        return

    if data == "wallet":

        await show_wallet(
            query,
            user.id
        )

        return

    if data == "support":

        await show_support(
            query,
            user.id
        )

        return

    # --------------------------------------------------------
    # REFERRAL SHARE
    # --------------------------------------------------------

    if data == "share_ref":

        link = (
            f"https://t.me/{BOT_USERNAME}?start={user.id}"
        )

        share_url = (
            "https://t.me/share/url"
            f"?url={link}"
        )

        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "ðŸ“¤ Share Referral Link",
                    url=share_url
                )
            ],
            [
                InlineKeyboardButton(
                    "Back ðŸ”™",
                    callback_data="referral"
                )
            ],
        ])

        await safe_edit_message(

            query,

            "ðŸ“¤ <b>Share Your Referral Link</b>\n\n"
            f"<code>{escape(link)}</code>\n\n"
            "áŠ¨á‰³á‰½ á‹«áˆˆá‹áŠ• Share á‰ áˆ˜áŒ«áŠ• "
            "áˆˆáŒ“á‹°áŠžá‰½á‹Ž á‹«áŒ‹áˆ©á¢",

            reply_markup=keyboard
        )

        return

    # --------------------------------------------------------
    # WALLET
    # --------------------------------------------------------

    if data == "wallet_cbe":

        await ask_wallet(
            query,
            user.id,
            "CBE",
            context
        )

        return

    if data == "wallet_telebirr":

        await ask_wallet(
            query,
            user.id,
            "Telebirr",
            context
        )

        return

    # --------------------------------------------------------
    # TASK
    # --------------------------------------------------------

    if data.startswith("verify_task_"):

        try:

            task_id = int(
                data.split("_", 2)[2]
            )

            await verify_task(
                query,
                context,
                task_id
            )

        except ValueError:
            pass

        return

    if data.startswith("task_"):

        try:

            task_id = int(
                data.split("_", 1)[1]
            )

            await show_task(
                query,
                user.id,
                task_id
            )

        except ValueError:
            pass

        return

    # ========================================================
    # ADMIN
    # ========================================================

    if data == "admin":

        await show_admin(
            query
        )

        return

    if data == "admin_stats":

        await show_admin_stats(
            query
        )

        return

    if data == "admin_reward":

        await show_admin_reward(
            query
        )

        return

    if data == "admin_change_reward":

        if not is_admin(user.id):
            return

        context.user_data.clear()

        context.user_data[
            "admin_step"
        ] = "reward"

        await safe_edit_message(

            query,

            f"ðŸ’° <b>Change Referral Reward</b>\n\n"
            f"Current: "
            f"<b>{get_referral_reward():.2f} ETB</b>\n\n"
            "Enter new reward amount:",

            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "Cancel âŒ",
                        callback_data="admin_reward"
                    )
                ]
            ])
        )

        return

    if data == "admin_referrals":

        await show_admin_referrals(
            query
        )

        return

    if data == "admin_referral_lookup":

        await admin_referral_lookup(
            query,
            context
        )

        return

    if data == "admin_withdrawals":

        await show_admin_withdrawals(
            query
        )

        return

    if data == "admin_suspicious":

        await show_admin_suspicious(
            query
        )

        return

    if data == "admin_add_task":

        await start_add_task(
            query,
            context
        )

        return

    # --------------------------------------------------------
    # APPROVE
    # --------------------------------------------------------

    if data.startswith("approve_wd_"):

        try:

            withdrawal_id = int(
                data.split("_")[-1]
            )

            await approve_withdrawal(
                query,
                withdrawal_id,
                context
            )

        except ValueError:
            pass

        return

    # --------------------------------------------------------
    # REJECT
    # --------------------------------------------------------

    if data.startswith("reject_wd_"):

        try:

            withdrawal_id = int(
                data.split("_")[-1]
            )

            await reject_withdrawal(
                query,
                withdrawal_id,
                context
            )

        except ValueError:
            pass

        return


# ============================================================
# MESSAGE HANDLER
# ============================================================

async def message_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    user = update.effective_user

    if not update.message:
        return

    text = update.message.text.strip()

    # ========================================================
    # ADMIN INPUTS
    # ========================================================

    if is_admin(user.id):

        admin_step = context.user_data.get(
            "admin_step"
        )

        # ----------------------------------------------------
        # CHANGE REFERRAL REWARD
        # ----------------------------------------------------

        if admin_step == "reward":

            try:

                amount = float(text)

                if amount <= 0:
                    raise ValueError

            except ValueError:

                await update.message.reply_text(
                    "âŒ Please enter a positive number!"
                )

                return

            set_setting(
                "referral_reward",
                f"{amount:.2f}"
            )

            context.user_data.pop(
                "admin_step",
                None
            )

            await update.message.reply_text(

                f"âœ… <b>Referral Reward Updated!</b>\n\n"
                f"New Reward: "
                f"<b>{amount:.2f} ETB</b>",

                reply_markup=InlineKeyboardMarkup([
                    [
                        InlineKeyboardButton(
                            "Referral Settings ðŸ’°",
                            callback_data="admin_reward"
                        )
                    ],
                    [
                        InlineKeyboardButton(
                            "Admin Panel ðŸ› ",
                            callback_data="admin"
                        )
                    ],
                ]),

                parse_mode="HTML"
            )

            return

        # ----------------------------------------------------
        # REFERRAL LOOKUP
        # ----------------------------------------------------

        if admin_step == "referral_lookup":

            try:

                referrer_id = int(text)

            except ValueError:

                await update.message.reply_text(
                    "âŒ Invalid User ID!"
                )

                return

            context.user_data.pop(
                "admin_step",
                None
            )

            await show_referral_details_for_message(
                update.message,
                referrer_id
            )

            return

        # ----------------------------------------------------
        # TASK TITLE
        # ----------------------------------------------------

        if admin_step == "task_title":

            context.user_data[
                "task_title"
            ] = text

            context.user_data[
                "admin_step"
            ] = "task_description"

            await update.message.reply_text(
                "ðŸŽ¯ <b>Add Task</b>\n\n"
                "Step 2/4: Enter task description.",
                parse_mode="HTML"
            )

            return

        # ----------------------------------------------------
        # TASK DESCRIPTION
        # ----------------------------------------------------

        if admin_step == "task_description":

            context.user_data[
                "task_description"
            ] = text

            context.user_data[
                "admin_step"
            ] = "task_channel"

            await update.message.reply_text(

                "ðŸŽ¯ <b>Add Task</b>\n\n"
                "Step 3/4: Enter Channel Username "
                "(e.g. <code>@MyChannel</code>).",

                parse_mode="HTML"
            )

            return

        # ----------------------------------------------------
        # TASK CHANNEL
        # ----------------------------------------------------

        if admin_step == "task_channel":

            username = text.strip()

            if not username.startswith("@"):
                username = "@" + username

            context.user_data[
                "task_channel"
            ] = username

            context.user_data[
                "task_url"
            ] = (
                f"https://t.me/"
                f"{username.lstrip('@')}"
            )

            context.user_data[
                "admin_step"
            ] = "task_reward"

            await update.message.reply_text(

                "ðŸŽ¯ <b>Add Task</b>\n\n"
                "Step 4/4: Enter reward amount in ETB.",

                parse_mode="HTML"
            )

            return

        # ----------------------------------------------------
        # TASK REWARD
        # ----------------------------------------------------

        if admin_step == "task_reward":

            try:

                reward = float(text)

                if reward <= 0:
                    raise ValueError

            except ValueError:

                await update.message.reply_text(
                    "âŒ Enter a valid reward amount!"
                )

                return

            title = context.user_data.get(
                "task_title",
                "Task"
            )

            description = context.user_data.get(
                "task_description",
                ""
            )

            channel = context.user_data.get(
                "task_channel",
                ""
            )

            url = context.user_data.get(
                "task_url",
                ""
            )

            task_id = create_task(
                title,
                description,
                channel,
                url,
                reward
            )

            context.user_data.clear()

            await update.message.reply_text(

                f"âœ… <b>Task Created Successfully!</b>\n\n"
                f"ðŸ†” Task ID: <b>{task_id}</b>\n"
                f"ðŸŽ¯ Title: <b>{escape(title)}</b>\n"
                f"ðŸ“¢ Channel: <b>{escape(channel)}</b>\n"
                f"ðŸ’° Reward: <b>{reward:.2f} ETB</b>",

                reply_markup=InlineKeyboardMarkup([
                    [
                        InlineKeyboardButton(
                            "Tasks ðŸŽ¯",
                            callback_data="tasks"
                        )
                    ],
                    [
                        InlineKeyboardButton(
                            "Admin Panel ðŸ› ",
                            callback_data="admin"
                        )
                    ],
                ]),

                parse_mode="HTML"
            )

            return

    # ========================================================
    # WALLET INPUT
    # ========================================================

    if context.user_data.get(
        "wallet_step"
    ) == "number":

        if await save_wallet_from_input(
            update,
            context
        ):
            return

    # ========================================================
    # WITHDRAW INPUT
    # ========================================================

    if context.user_data.get(
        "withdraw_step"
    ) == "amount":

        if await handle_withdraw_amount_input(
            update,
            context
        ):
            return

    # ========================================================
    # DEFAULT
    # ========================================================

    await update.message.reply_text(

        "Please use the buttons below / "
        "áŠ¥á‰£áŠ­á‹ŽáŠ• áŠ¨á‰³á‰½ á‹«áˆ‰á‰µáŠ• á‰ á‰°áŠ–á‰½ á‹­áŒ á‰€áˆ™á¦",

        reply_markup=main_keyboard()
    )


# ============================================================
# COMMANDS
# ============================================================

async def admin_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not is_admin(
        update.effective_user.id
    ):

        await update.message.reply_text(
            "â›” Admin access only!"
        )

        return

    context.user_data.clear()

    await update.message.reply_text(

        "ðŸ›  <b>Admin Control Panel / "
        "á‹¨áŠ á‹µáˆšáŠ• á“áŠáˆ</b>\n\n"
        "Select an option:",

        reply_markup=admin_keyboard(),
        parse_mode="HTML"
    )


async def cancel_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    context.user_data.clear()

    await update.message.reply_text(

        "âŒ Action cancelled / "
        "áŠ­áŠ•á‹áŠ‘ á‰°áˆ°áˆ­á‹Ÿáˆá¢",

        reply_markup=main_keyboard()
    )


# ============================================================
# ERROR HANDLER
# ============================================================

async def error_handler(
    update: object,
    context: ContextTypes.DEFAULT_TYPE
):

    logger.error(
        "Unexpected error occurred",
        exc_info=context.error
    )


# ============================================================
# MAIN
# ============================================================

def main():

    if not BOT_TOKEN:

        raise RuntimeError(
            "BOT_TOKEN is missing!"
        )

    if not ADMIN_ID:

        raise RuntimeError(
            "ADMIN_ID is missing!"
        )

    init_db()

    application = (
        Application
        .builder()
        .token(BOT_TOKEN)
        .build()
    )

    application.add_handler(
        CommandHandler(
            "start",
            start
        )
    )

    application.add_handler(
        CommandHandler(
            "admin",
            admin_command
        )
    )

    application.add_handler(
        CommandHandler(
            "cancel",
            cancel_command
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
            message_handler
        )
    )

    application.add_error_handler(
        error_handler
    )

    logger.info(
        "Global Cash Bot starting..."
    )

    application.run_polling(
        drop_pending_updates=True
    )


if __name__ == "__main__":
    main()

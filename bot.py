import os
import re
import sqlite3
import logging
import random
from datetime import datetime, timezone
from html import escape
from urllib.parse import quote

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
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

# ============================================================
# REQUIRED CHANNELS
# ============================================================

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


def now():
    return datetime.now(timezone.utc).isoformat()


def init_db():
    conn = db()
    cur = conn.cursor()

    # --------------------------------------------------------
    # USERS
    # --------------------------------------------------------

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

    # --------------------------------------------------------
    # REFERRALS
    # --------------------------------------------------------

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

    # --------------------------------------------------------
    # WITHDRAWALS
    # --------------------------------------------------------

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

    # --------------------------------------------------------
    # SETTINGS
    # --------------------------------------------------------

    cur.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
    """)

    # --------------------------------------------------------
    # TASKS
    # --------------------------------------------------------

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

    # --------------------------------------------------------
    # USER TASKS
    # --------------------------------------------------------

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

    # --------------------------------------------------------
    # UNIQUE WALLET
    # --------------------------------------------------------

    cur.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS idx_unique_wallet
        ON users(wallet_type, wallet_number)
        WHERE wallet_type IS NOT NULL
          AND wallet_number IS NOT NULL
          AND wallet_number != ''
    """)

    # --------------------------------------------------------
    # DEFAULT SETTINGS
    # --------------------------------------------------------

    # Random referral reward:
    # Minimum = 1 ETB
    # Maximum = 5 ETB
    cur.execute("""
        INSERT OR IGNORE INTO settings(key, value)
        VALUES('referral_min', '1.00')
    """)

    cur.execute("""
        INSERT OR IGNORE INTO settings(key, value)
        VALUES('referral_max', '5.00')
    """)

    conn.commit()
    conn.close()


# ============================================================
# USER FUNCTIONS
# ============================================================

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

    conn = db()

    if existing is None:

        ref = None

        if referred_by and referred_by != tg_user.id:
            ref = referred_by

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
            tg_user.id,
            tg_user.username,
            tg_user.first_name or "",
            ref,
            now(),
        ))

    else:

        # Do not overwrite existing referred_by.
        # This prevents users from changing their referrer
        # by repeatedly using different /start links.
        conn.execute("""
            UPDATE users
            SET username=?,
                first_name=?
            WHERE user_id=?
        """, (
            tg_user.username,
            tg_user.first_name or "",
            tg_user.id,
        ))

    conn.commit()
    conn.close()


def set_joined_all(user_id, value):
    conn = db()

    conn.execute(
        "UPDATE users SET joined_all=? WHERE user_id=?",
        (1 if value else 0, user_id),
    )

    conn.commit()
    conn.close()


def add_balance(user_id, amount):
    conn = db()

    conn.execute(
        "UPDATE users SET balance=balance+? WHERE user_id=?",
        (amount, user_id),
    )

    conn.commit()
    conn.close()


def get_balance(user_id):
    row = get_user(user_id)

    if not row:
        return 0.0

    return float(row["balance"])


def mark_suspicious(user_id, value=1):
    conn = db()

    conn.execute(
        "UPDATE users SET suspicious=? WHERE user_id=?",
        (1 if value else 0, user_id),
    )

    conn.commit()
    conn.close()


# ============================================================
# WALLET
# ============================================================

def get_wallet(user_id):
    row = get_user(user_id)

    if not row:
        return None

    if not row["wallet_type"] or not row["wallet_number"]:
        return None

    return row["wallet_type"], row["wallet_number"]


def wallet_exists_for_other_user(
    wallet_type,
    wallet_number,
    user_id,
):
    conn = db()

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

    conn.close()

    return row is not None


def set_wallet(
    user_id,
    wallet_type,
    wallet_number,
):
    conn = db()

    try:

        conn.execute("""
            UPDATE users
            SET wallet_type=?,
                wallet_number=?
            WHERE user_id=?
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


# ============================================================
# SETTINGS
# ============================================================

def get_setting(key, default=None):
    conn = db()

    row = conn.execute(
        "SELECT value FROM settings WHERE key=?",
        (key,),
    ).fetchone()

    conn.close()

    return row["value"] if row else default


def set_setting(key, value):
    conn = db()

    conn.execute("""
        INSERT INTO settings(key,value)
        VALUES(?,?)
        ON CONFLICT(key)
        DO UPDATE SET value=excluded.value
    """, (
        key,
        str(value),
    ))

    conn.commit()
    conn.close()


# ============================================================
# RANDOM REFERRAL REWARD
# ============================================================

def get_referral_min():
    try:
        value = float(
            get_setting(
                "referral_min",
                "1.00",
            )
        )

        return max(0.01, value)

    except Exception:
        return 1.0


def get_referral_max():
    try:
        value = float(
            get_setting(
                "referral_max",
                "5.00",
            )
        )

        return max(0.01, value)

    except Exception:
        return 5.0


def get_referral_range():
    minimum = get_referral_min()
    maximum = get_referral_max()

    if minimum > maximum:
        minimum, maximum = maximum, minimum

    return minimum, maximum


def generate_random_referral_reward():
    minimum, maximum = get_referral_range()

    # ETB precision = 2 decimal places
    cents_min = int(round(minimum * 100))
    cents_max = int(round(maximum * 100))

    if cents_min > cents_max:
        cents_min, cents_max = cents_max, cents_min

    reward_cents = random.randint(
        cents_min,
        cents_max,
    )

    return reward_cents / 100.0


# ============================================================
# REFERRALS
# ============================================================

def get_referral_count(user_id):
    conn = db()

    row = conn.execute("""
        SELECT COUNT(*) AS c
        FROM referrals
        WHERE referrer_id=?
          AND status='paid'
    """, (
        user_id,
    )).fetchone()

    conn.close()

    return int(row["c"])


def process_referral_reward(referred_id):

    user = get_user(referred_id)

    if not user:
        return None

    # --------------------------------------------------------
    # Referral owner
    # --------------------------------------------------------

    inviter = user["referred_by"]

    if not inviter:
        return None

    if inviter == referred_id:
        return None

    # --------------------------------------------------------
    # Already paid
    # --------------------------------------------------------

    if user["referral_paid"]:
        return None

    # --------------------------------------------------------
    # Suspicious referred user
    # --------------------------------------------------------

    if user["suspicious"]:
        return None

    inviter_row = get_user(inviter)

    if not inviter_row:
        return None

    if inviter_row["suspicious"]:
        return None

    # --------------------------------------------------------
    # IMPORTANT:
    # Referred user must be fully verified.
    # --------------------------------------------------------

    if not user["joined_all"]:
        return None

    conn = db()

    try:

        # ----------------------------------------------------
        # Check if referral already exists
        # ----------------------------------------------------

        existing = conn.execute("""
            SELECT id
            FROM referrals
            WHERE referred_id=?
        """, (
            referred_id,
        )).fetchone()

        if existing:

            conn.execute("""
                UPDATE users
                SET referral_paid=1,
                    referral_status='paid'
                WHERE user_id=?
            """, (
                referred_id,
            ))

            conn.commit()

            return None

        # ----------------------------------------------------
        # RANDOM REWARD
        # ----------------------------------------------------

        reward = generate_random_referral_reward()

        # ----------------------------------------------------
        # Add reward to inviter
        # ----------------------------------------------------

        cur = conn.execute("""
            UPDATE users
            SET balance=balance+?
            WHERE user_id=?
              AND suspicious=0
        """, (
            reward,
            inviter,
        ))

        if cur.rowcount != 1:
            conn.rollback()
            return None

        # ----------------------------------------------------
        # Save referral payment
        # ----------------------------------------------------

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

        # ----------------------------------------------------
        # Mark referred user as paid
        # ----------------------------------------------------

        conn.execute("""
            UPDATE users
            SET referral_paid=1,
                referral_status='paid'
            WHERE user_id=?
        """, (
            referred_id,
        ))

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

    rows = conn.execute("""
        SELECT *
        FROM tasks
        WHERE active=1
        ORDER BY id DESC
    """).fetchall()

    conn.close()

    return rows


def get_task(task_id):
    conn = db()

    row = conn.execute(
        "SELECT * FROM tasks WHERE id=?",
        (task_id,),
    ).fetchone()

    conn.close()

    return row


def user_task_paid(
    user_id,
    task_id,
):
    conn = db()

    row = conn.execute("""
        SELECT paid
        FROM user_tasks
        WHERE user_id=?
          AND task_id=?
    """, (
        user_id,
        task_id,
    )).fetchone()

    conn.close()

    return bool(
        row and row["paid"]
    )


def save_user_task(
    user_id,
    task_id,
    verified,
    paid,
):
    conn = db()

    conn.execute("""
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

    conn.commit()
    conn.close()


def create_task(
    title,
    description,
    username,
    url,
    reward,
):
    conn = db()

    cur = conn.execute("""
        INSERT INTO tasks(
            title,
            description,
            channel_username,
            channel_url,
            reward,
            active,
            created_at
        )
        VALUES(?,?,?,?,?,?,?)
    """, (
        title,
        description,
        username,
        url,
        reward,
        1,
        now(),
    ))

    task_id = cur.lastrowid

    conn.commit()
    conn.close()

    return task_id


# ============================================================
# CHANNEL VERIFICATION
# ============================================================

async def missing_channels(
    bot,
    user_id,
):
    missing = []

    for username, url in REQUIRED_CHANNELS:

        try:

            member = await bot.get_chat_member(
                chat_id=username,
                user_id=user_id,
            )

            if member.status not in (
                "member",
                "administrator",
                "creator",
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
# KEYBOARDS
# ============================================================

def channel_keyboard(missing):

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
            "✅ Verify",
            callback_data="verify",
        )
    ])

    return InlineKeyboardMarkup(buttons)


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


def balance_keyboard():

    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "💸 Withdraw",
                callback_data="withdraw",
            ),
            InlineKeyboardButton(
                "👥 Referral",
                callback_data="referral",
            ),
        ],
        [
            InlineKeyboardButton(
                "🔙 Back",
                callback_data="home",
            )
        ],
    ])


def referral_keyboard():

    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "📤 Share Referral Link",
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


def withdraw_keyboard():

    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "👛 Set / Change Wallet",
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
            ),
            InlineKeyboardButton(
                "👥 Users",
                callback_data="admin_users",
            ),
        ],
        [
            InlineKeyboardButton(
                "👥 Referrals",
                callback_data="admin_referrals",
            ),
            InlineKeyboardButton(
                "💸 Withdrawals",
                callback_data="admin_withdrawals",
            ),
        ],
        [
            InlineKeyboardButton(
                "💰 Referral Reward",
                callback_data="admin_reward",
            ),
            InlineKeyboardButton(
                "🔎 Check User",
                callback_data="admin_check_balance",
            ),
        ],
        [
            InlineKeyboardButton(
                "📢 Required Channels",
                callback_data="admin_channels",
            ),
            InlineKeyboardButton(
                "⚠️ Suspicious Users",
                callback_data="admin_suspicious",
            ),
        ],
        [
            InlineKeyboardButton(
                "🎯 Add Task",
                callback_data="admin_add_task",
            ),
            InlineKeyboardButton(
                "🧪 Test Balance",
                callback_data="admin_test_balance",
            ),
        ],
    ])


# ============================================================
# HOME
# ============================================================

async def show_home(query):

    await query.edit_message_text(
        "💰 <b>Global Cash Bot</b> 👋\n\n"
        "<b>Welcome back! / እንኳን ደህና መጡ!</b>\n\n"
        "Invite people, complete available tasks, "
        "and grow your ETB balance.\n"
        "ሰዎችን በመጋበዝ እና ያሉ ተግባራትን "
        "በመጨረስ ገቢዎን ያሳድጉ።\n\n"
        "Choose an option below to continue.\n"
        "ለመቀጠል ከታች ይምረጡ።",
        reply_markup=main_keyboard(),
        parse_mode="HTML",
    )


# ============================================================
# BALANCE
# ============================================================

async def show_balance(
    query,
    user_id,
):

    balance = get_balance(user_id)

    referral_count = get_referral_count(
        user_id
    )

    minimum, maximum = get_referral_range()

    await query.edit_message_text(
        "💰 <b>Your Balance / የእርስዎ ቀሪ ሂሳብ</b>\n\n"
        f"💵 <b>Available Balance:</b> {balance:.2f} ETB\n"
        f"👥 <b>Successful Referrals:</b> {referral_count}\n"
        f"🎁 <b>Referral Reward:</b> {minimum:.2f} - {maximum:.2f} ETB\n"
        f"📌 <b>Minimum Withdrawal:</b> {MIN_WITHDRAWAL:.2f} ETB\n\n"
        "Keep earning and withdraw when you reach the minimum.\n"
        "ቀሪ ሂሳብዎ ዝቅተኛውን መጠን ሲደርስ withdrawal መጠየቅ ይችላሉ።",
        reply_markup=balance_keyboard(),
        parse_mode="HTML",
    )


# ============================================================
# REFERRAL
# ============================================================

async def show_referral(
    query,
    user_id,
):

    count = get_referral_count(
        user_id
    )

    minimum, maximum = get_referral_range()

    link = (
        f"https://t.me/"
        f"{BOT_USERNAME}"
        f"?start={user_id}"
    )

    await query.edit_message_text(
        "👥 <b>Referral Center / የReferral ማዕከል</b>\n\n"
        f"👤 <b>Successful Referrals:</b> {count}\n"
        f"🎁 <b>Random Reward Range:</b> "
        f"{minimum:.2f} - {maximum:.2f} ETB\n\n"
        "Every successful referral receives a random reward "
        "from the current reward range.\n"
        "እያንዳንዱ successful referral ከተቀመጠው "
        "የreward range ውስጥ random reward ያስገኛል።\n\n"
        "Invite friends using your personal referral link.\n"
        "የግል Referral Linkዎን በመጠቀም ሰዎችን ይጋብዙ።\n\n"
        f"🔗 <b>Your Referral Link:</b>\n"
        f"<code>{escape(link)}</code>\n\n"
        "🎯 Reward is credited only after the referred user "
        "joins all required channels and completes verification.\n"
        "ተጋባዡ ሁሉንም channels ከጨረሰ እና Verify ካደረገ "
        "በኋላ ብቻ reward ይገባል።",
        reply_markup=referral_keyboard(),
        parse_mode="HTML",
    )


# ============================================================
# TASK LIST
# ============================================================

async def show_tasks(
    query,
    user_id,
):

    tasks = get_active_tasks()

    if not tasks:

        await query.edit_message_text(
            "🎯 <b>Tasks / ተግባራት</b>\n\n"
            "There are no active tasks right now.\n"
            "በአሁኑ ጊዜ active task የለም።\n\n"
            "Please check again later.\n"
            "እባክዎ ቆይተው እንደገና ይመልከቱ።",
            reply_markup=back_keyboard(),
            parse_mode="HTML",
        )

        return

    buttons = []

    for task in tasks:

        status = (
            "✅ Completed"
            if user_task_paid(
                user_id,
                task["id"],
            )
            else "🎯 Open"
        )

        buttons.append([
            InlineKeyboardButton(
                f"{status} • "
                f"{task['title'][:25]}",
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
        "🎯 <b>Available Tasks / ያሉ ተግባራት</b>\n\n"
        "Complete an available task and verify it "
        "to receive the reward.\n"
        "ተግባሩን ከጨረሱ በኋላ Verify ያድርጉ።",
        reply_markup=InlineKeyboardMarkup(
            buttons
        ),
        parse_mode="HTML",
    )


# ============================================================
# WALLET SCREEN
# ============================================================

async def show_wallet(
    query,
    user_id,
):

    wallet = get_wallet(user_id)

    if wallet:

        wallet_text = (
            f"Current Method: "
            f"<b>{escape(wallet[0])}</b>\n"
            f"Number: "
            f"<code>{escape(wallet[1])}</code>\n\n"
        )

    else:

        wallet_text = (
            "No wallet is saved yet. / "
            "እስካሁን wallet አልተቀመጠም።\n\n"
        )

    await query.edit_message_text(
        "👛 <b>Wallet / የክፍያ መረጃ</b>\n\n"
        + wallet_text
        + "Choose your payment method below.\n"
        "ከታች የክፍያ ዘዴዎን ይምረጡ።",
        reply_markup=wallet_keyboard(),
        parse_mode="HTML",
    )


# ============================================================
# WITHDRAW SCREEN
# ============================================================

async def show_withdraw(
    query,
    user_id,
    context,
):

    row = get_user(user_id)

    if not row:

        await query.edit_message_text(
            "❌ User not found.",
            reply_markup=back_keyboard(),
        )

        return

    if row["suspicious"]:

        await query.edit_message_text(
            "⚠️ <b>Security Review / የደህንነት ምርመራ</b>\n\n"
            "Your account is currently under security review.\n"
            "እባክዎ ለተጨማሪ መረጃ Support ያነጋግሩ።\n\n"
            f"📞 Contact: {SUPPORT_USERNAME}",
            reply_markup=back_keyboard(),
            parse_mode="HTML",
        )

        return

    balance = float(
        row["balance"]
    )

    if balance < MIN_WITHDRAWAL:

        await query.edit_message_text(
            "💸 <b>Withdraw Money / ገንዘብ ማውጣት</b>\n\n"
            f"💰 Available Balance: "
            f"<b>{balance:.2f} ETB</b>\n"
            f"📌 Minimum Withdrawal: "
            f"<b>{MIN_WITHDRAWAL:.2f} ETB</b>\n\n"
            "You can request a withdrawal after "
            "reaching the minimum.\n"
            "ቢያንስ 30 ETB ሲደርስ withdrawal መጠየቅ ይችላሉ።",
            reply_markup=withdraw_keyboard(),
            parse_mode="HTML",
        )

        return

    wallet = get_wallet(user_id)

    if not wallet:

        await query.edit_message_text(
            "👛 <b>Wallet Required / Wallet ያስፈልጋል</b>\n\n"
            "Please save a CBE or Telebirr wallet "
            "before requesting a withdrawal.\n"
            "Withdrawal ከመጠየቅዎ በፊት CBE ወይም "
            "Telebirr wallet ያስቀምጡ።",
            reply_markup=wallet_keyboard(),
            parse_mode="HTML",
        )

        return

    context.user_data["withdraw_mode"] = True

    await query.edit_message_text(
        "💸 <b>Withdraw Money / ገንዘብ ማውጣት</b>\n\n"
        f"💰 Available Balance: "
        f"<b>{balance:.2f} ETB</b>\n"
        f"💳 Method: "
        f"<b>{escape(wallet[0])}</b>\n"
        f"🔢 Wallet: "
        f"<code>{escape(wallet[1])}</code>\n"
        f"📌 Minimum Withdrawal: "
        f"<b>{MIN_WITHDRAWAL:.2f} ETB</b>\n\n"
        "Enter the amount you want to withdraw.\n"
        "ማውጣት የሚፈልጉትን amount ያስገቡ።\n\n"
        "Example: <code>50</code>\n\n"
        "Cancel: /cancel",
        parse_mode="HTML",
    )


# ============================================================
# SUPPORT
# ============================================================

async def show_support(query):

    await query.edit_message_text(
        "📞 <b>Support & Digital Services / ድጋፍ እና ማስታወቂያ</b>\n\n"
        "እነዚህን አገልግሎቶች ማግኘት ከፈለጉ "
        "በ Support መስመራችን DM ያድርጉልን፦\n\n"
        "🚀 <b>Buy & Sell USDT</b>\n"
        "📢 Telegram Channel Promotion\n"
        "👥 Group Members & Growth\n"
        "🏢 Business & Product Promotion\n"
        "📱 App & Website Promotion\n\n"
        "<b>Contact Admin / ለማነጋገር፦</b>\n\n"
        f"👉 {SUPPORT_USERNAME}",
        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "💬 Contact Admin",
                    url="https://t.me/AmanM_12",
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


# ============================================================
# START
# ============================================================

async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    user = update.effective_user

    referred_by = None

    if context.args:

        try:

            ref_id = int(
                context.args[0]
            )

            if ref_id != user.id:
                referred_by = ref_id

        except ValueError:
            pass

    create_or_update_user(
        user,
        referred_by,
    )

    missing = await missing_channels(
        context.bot,
        user.id,
    )

    if missing:

        set_joined_all(
            user.id,
            False,
        )

        await update.message.reply_text(
            "💎 <b>Welcome to Global Cash Bot</b>\n\n"
            "To continue, please verify all required channels below.\n\n"
            "ከታች ያሉትን ሁሉንም channels ከተቀላቀሉ "
            "በኋላ <b>Verify</b> ይጫኑ።",
            reply_markup=channel_keyboard(
                missing
            ),
            parse_mode="HTML",
        )

        return

    set_joined_all(
        user.id,
        True,
    )

    reward = process_referral_reward(
        user.id
    )

    if reward:

        try:

            await context.bot.send_message(
                chat_id=reward["inviter_id"],
                text=(
                    "🎉 <b>New Successful Referral!</b>\n\n"
                    f"👤 User ID: "
                    f"<code>{reward['referred_id']}</code>\n"
                    f"🎁 Random Reward: "
                    f"<b>{reward['amount']:.2f} ETB</b>\n\n"
                    "The reward has been added to your balance."
                ),
                parse_mode="HTML",
            )

        except Exception:

            logger.exception(
                "Could not notify inviter"
            )

    await update.message.reply_text(
        "💎 <b>Global Cash Bot</b>\n\n"
        "✅ <b>Verified</b>\n\n"
        "Welcome! Choose what you want to do below 👇",
        reply_markup=main_keyboard(),
        parse_mode="HTML",
    )


# ============================================================
# VERIFY CHANNELS
# ============================================================

async def verify_user(
    query,
    context,
):

    user = query.from_user

    missing = await missing_channels(
        context.bot,
        user.id,
    )

    if missing:

        set_joined_all(
            user.id,
            False,
        )

        names = "\n".join(
            f"• {escape(username)}"
            for username, _ in missing
        )

        await query.edit_message_text(
            "❌ <b>Verification Failed</b>\n\n"
            "You still need to join these channels:\n\n"
            f"{names}\n\n"
            "After joining them, press Verify again.",
            reply_markup=channel_keyboard(
                missing
            ),
            parse_mode="HTML",
        )

        return

    set_joined_all(
        user.id,
        True,
    )

    reward = process_referral_reward(
        user.id
    )

    if reward:

        try:

            await context.bot.send_message(
                chat_id=reward["inviter_id"],
                text=(
                    "🎉 <b>Successful Referral!</b>\n\n"
                    f"🎁 Random Reward: "
                    f"<b>{reward['amount']:.2f} ETB</b>\n"
                    "has been added to your balance."
                ),
                parse_mode="HTML",
            )

        except Exception:

            logger.exception(
                "Referral notification failed"
            )

    await query.edit_message_text(
        "✅ <b>Verified Successfully!</b>\n\n"
        "Welcome to Global Cash Bot.\n"
        "Choose an option below 👇",
        reply_markup=main_keyboard(),
        parse_mode="HTML",
    )


# ============================================================
# WALLET INPUT
# ============================================================

async def handle_wallet_number(
    update,
    context,
):

    user = update.effective_user

    text = (
        update.message.text or ""
    ).strip()

    wallet_type = context.user_data.get(
        "wallet_type"
    )

    if not wallet_type:
        return False

    # --------------------------------------------------------
    # CBE
    # --------------------------------------------------------

    if wallet_type == "CBE":

        if not re.fullmatch(
            r"1000\d{9}",
            text,
        ):

            await update.message.reply_text(
                "❌ <b>Invalid CBE Account</b>\n\n"
                "CBE must be exactly 13 digits "
                "and start with 1000.\n\n"
                "Example: "
                "<code>1000123456789</code>\n\n"
                "Try again or use /cancel.",
                parse_mode="HTML",
            )

            return True

    # --------------------------------------------------------
    # TELEBIRR
    # --------------------------------------------------------

    elif wallet_type == "Telebirr":

        if not re.fullmatch(
            r"(09|07)\d{8}",
            text,
        ):

            await update.message.reply_text(
                "❌ <b>Invalid Telebirr Number</b>\n\n"
                "Telebirr must be exactly 10 digits "
                "and start with 09 or 07.\n\n"
                "Example: "
                "<code>0912345678</code>\n\n"
                "Try again or use /cancel.",
                parse_mode="HTML",
            )

            return True

    # --------------------------------------------------------
    # DUPLICATE WALLET
    # --------------------------------------------------------

    if wallet_exists_for_other_user(
        wallet_type,
        text,
        user.id,
    ):

        mark_suspicious(
            user.id,
            1,
        )

        context.user_data.pop(
            "wallet_type",
            None,
        )

        await update.message.reply_text(
            "⚠️ <b>Wallet Security Check</b>\n\n"
            "This wallet is already registered "
            "to another account.\n"
            "Your account has been placed under "
            "security review.\n\n"
            f"📞 Contact: {SUPPORT_USERNAME}",
            parse_mode="HTML",
        )

        return True

    # --------------------------------------------------------
    # SAVE
    # --------------------------------------------------------

    if not set_wallet(
        user.id,
        wallet_type,
        text,
    ):

        await update.message.reply_text(
            "❌ Could not save the wallet. "
            "Please try again.",
        )

        return True

    context.user_data.pop(
        "wallet_type",
        None,
    )

    await update.message.reply_text(
        "✅ <b>Wallet Saved</b>\n\n"
        f"🏦 Method: "
        f"<b>{escape(wallet_type)}</b>\n"
        f"🔢 Number: "
        f"<code>{escape(text)}</code>\n\n"
        "This is now your active withdrawal wallet.",
        reply_markup=main_keyboard(),
        parse_mode="HTML",
    )

    return True


# ============================================================
# WITHDRAW INPUT
# ============================================================

async def handle_withdraw_amount(
    update,
    context,
):

    user = update.effective_user

    text = (
        update.message.text or ""
    ).strip()

    if not context.user_data.get(
        "withdraw_mode"
    ):
        return False

    try:

        amount = float(text)

    except ValueError:

        await update.message.reply_text(
            "❌ Please enter a valid number.\n\n"
            "Example: 30",
        )

        return True

    if amount <= 0:

        await update.message.reply_text(
            "❌ Amount must be greater than 0.",
        )

        return True

    if amount < MIN_WITHDRAWAL:

        await update.message.reply_text(
            f"❌ Minimum withdrawal is "
            f"{MIN_WITHDRAWAL:.0f} ETB.",
        )

        return True

    row = get_user(
        user.id
    )

    if not row:

        context.user_data.pop(
            "withdraw_mode",
            None,
        )

        await update.message.reply_text(
            "❌ User not found."
        )

        return True

    if row["suspicious"]:

        context.user_data.pop(
            "withdraw_mode",
            None,
        )

        await update.message.reply_text(
            "⚠️ Your account is under security review.\n"
            f"Contact: {SUPPORT_USERNAME}",
        )

        return True

    wallet = get_wallet(
        user.id
    )

    if not wallet:

        context.user_data.pop(
            "withdraw_mode",
            None,
        )

        await update.message.reply_text(
            "❌ No active wallet found.\n"
            "Please save a CBE or Telebirr wallet first.",
            reply_markup=main_keyboard(),
        )

        return True

    conn = db()

    try:

        # ----------------------------------------------------
        # Deduct balance atomically.
        # ----------------------------------------------------

        cur = conn.execute("""
            UPDATE users
            SET balance=balance-?
            WHERE user_id=?
              AND suspicious=0
              AND balance>=?
        """, (
            amount,
            user.id,
            amount,
        ))

        if cur.rowcount != 1:

            conn.rollback()

            await update.message.reply_text(
                "❌ Insufficient balance.",
                reply_markup=main_keyboard(),
            )

            context.user_data.pop(
                "withdraw_mode",
                None,
            )

            return True

        # ----------------------------------------------------
        # Create withdrawal
        # ----------------------------------------------------

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
            wallet[0],
            wallet[1],
            "pending",
            now(),
        ))

        withdrawal_id = cur.lastrowid

        conn.commit()

    except Exception:

        conn.rollback()

        logger.exception(
            "Withdrawal creation error"
        )

        await update.message.reply_text(
            "❌ Withdrawal could not be created. "
            "Please try again.",
            reply_markup=main_keyboard(),
        )

        context.user_data.pop(
            "withdraw_mode",
            None,
        )

        return True

    finally:

        conn.close()

    context.user_data.pop(
        "withdraw_mode",
        None,
    )

    await update.message.reply_text(
        "✅ <b>Withdrawal Submitted</b>\n\n"
        f"💰 Amount: "
        f"<b>{amount:.2f} ETB</b>\n"
        f"🏦 Method: "
        f"<b>{escape(wallet[0])}</b>\n"
        f"🔢 Wallet: "
        f"<code>{escape(wallet[1])}</code>\n"
        f"🆔 Request: "
        f"<code>#{withdrawal_id}</code>\n\n"
        "⏳ Status: <b>Pending</b>",
        reply_markup=main_keyboard(),
        parse_mode="HTML",
    )

    # --------------------------------------------------------
    # Notify admin
    # --------------------------------------------------------

    if ADMIN_ID:

        try:

            await context.bot.send_message(
                chat_id=ADMIN_ID,
                text=(
                    "💳 <b>New Withdrawal</b>\n\n"
                    f"🆔 Request: "
                    f"<code>#{withdrawal_id}</code>\n"
                    f"👤 User ID: "
                    f"<code>{user.id}</code>\n"
                    f"👤 Username: "
                    f"@{escape(user.username or 'N/A')}\n"
                    f"💰 Amount: "
                    f"<b>{amount:.2f} ETB</b>\n"
                    f"🏦 Method: "
                    f"<b>{escape(wallet[0])}</b>\n"
                    f"🔢 Wallet: "
                    f"<code>{escape(wallet[1])}</code>\n\n"
                    "⏳ Status: <b>Pending</b>"
                ),
                reply_markup=InlineKeyboardMarkup([
                    [
                        InlineKeyboardButton(
                            "✅ Approve",
                            callback_data=(
                                f"approve_wd_{withdrawal_id}"
                            ),
                        ),
                        InlineKeyboardButton(
                            "❌ Reject",
                            callback_data=(
                                f"reject_wd_{withdrawal_id}"
                            ),
                        ),
                    ]
                ]),
                parse_mode="HTML",
            )

        except Exception:

            logger.exception(
                "Admin withdrawal notification failed"
            )

    return True


# ============================================================
# ADMIN HELPERS
# ============================================================

def admin_only(user_id):
    return user_id == ADMIN_ID


def get_stats():

    conn = db()

    total = conn.execute(
        "SELECT COUNT(*) AS c FROM users"
    ).fetchone()["c"]

    verified = conn.execute(
        "SELECT COUNT(*) AS c "
        "FROM users WHERE joined_all=1"
    ).fetchone()["c"]

    balance = conn.execute(
        "SELECT COALESCE(SUM(balance),0) AS s "
        "FROM users"
    ).fetchone()["s"]

    referrals = conn.execute(
        "SELECT COUNT(*) AS c "
        "FROM referrals WHERE status='paid'"
    ).fetchone()["c"]

    pending = conn.execute(
        "SELECT COUNT(*) AS c "
        "FROM withdrawals WHERE status='pending'"
    ).fetchone()["c"]

    suspicious = conn.execute(
        "SELECT COUNT(*) AS c "
        "FROM users WHERE suspicious=1"
    ).fetchone()["c"]

    conn.close()

    return (
        total,
        verified,
        float(balance),
        referrals,
        pending,
        suspicious,
    )


def get_user_counts():

    conn = db()

    total = conn.execute(
        "SELECT COUNT(*) c FROM users"
    ).fetchone()["c"]

    active = conn.execute("""
        SELECT COUNT(*) c
        FROM users
        WHERE joined_all=1
          AND suspicious=0
    """).fetchone()["c"]

    verified = conn.execute("""
        SELECT COUNT(*) c
        FROM users
        WHERE joined_all=1
    """).fetchone()["c"]

    unverified = conn.execute("""
        SELECT COUNT(*) c
        FROM users
        WHERE joined_all=0
    """).fetchone()["c"]

    suspicious = conn.execute("""
        SELECT COUNT(*) c
        FROM users
        WHERE suspicious=1
    """).fetchone()["c"]

    conn.close()

    return (
        total,
        active,
        verified,
        unverified,
        suspicious,
    )


# ============================================================
# ADMIN USERS
# ============================================================

async def show_admin_users(query):

    conn = db()

    rows = conn.execute("""
        SELECT
            user_id,
            username,
            first_name,
            balance,
            joined_all,
            suspicious,
            created_at
        FROM users
        ORDER BY user_id DESC
        LIMIT 30
    """).fetchall()

    conn.close()

    lines = [
        "👥 <b>Recent Users</b>",
        "",
        "Showing the latest 30 users.",
        "",
    ]

    if not rows:

        lines.append(
            "No users found."
        )

    else:

        for r in rows:

            name = (
                f"@{r['username']}"
                if r["username"]
                else (
                    r["first_name"]
                    or "No username"
                )
            )

            status = (
                "Active"
                if r["joined_all"]
                else "Not verified"
            )

            if r["suspicious"]:
                status += " • Review"

            lines.append(
                f"👤 <b>{escape(name)}</b> | "
                f"<code>{r['user_id']}</code>\n"
                f"💰 {float(r['balance']):.2f} ETB • "
                f"{status}"
            )

    await query.edit_message_text(
        "\n\n".join(lines),
        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "🔙 Back to Admin",
                    callback_data="admin",
                )
            ]
        ]),
        parse_mode="HTML",
    )


# ============================================================
# ADMIN CHANNELS
# ============================================================

async def show_admin_channels(query):

    lines = [
        "📢 <b>Required Channels</b>",
        "",
        "These channels are required for account activation "
        "and referral rewards.",
        "",
    ]

    for i, (
        username,
        url,
    ) in enumerate(
        REQUIRED_CHANNELS,
        1,
    ):

        lines.append(
            f"<b>{i}. {escape(username)}</b>\n"
            f"🔗 {escape(url)}"
        )

    lines.extend([
        "",
        "ℹ️ Channel list is currently configured "
        "inside the bot settings.",
    ])

    await query.edit_message_text(
        "\n\n".join(lines),
        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "🔙 Back to Admin",
                    callback_data="admin",
                )
            ]
        ]),
        parse_mode="HTML",
    )


# ============================================================
# ADMIN CHECK USER
# ============================================================

async def start_check_balance(
    query,
    context,
):

    if not admin_only(
        query.from_user.id
    ):
        return

    context.user_data.clear()

    context.user_data[
        "admin_step"
    ] = "check_balance_user"

    await query.edit_message_text(
        "🔎 <b>Check User</b>\n\n"
        "Enter the Telegram User ID.\n"
        "የሚፈልጉትን User ID ያስገቡ።\n\n"
        "Example: "
        "<code>8727153413</code>",
        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "❌ Cancel",
                    callback_data="admin",
                )
            ]
        ]),
        parse_mode="HTML",
    )


async def handle_admin_check_balance(
    update,
    context,
):

    if (
        context.user_data.get(
            "admin_step"
        )
        != "check_balance_user"
    ):
        return False

    try:

        uid = int(
            (update.message.text or "").strip()
        )

        if uid <= 0:
            raise ValueError

    except Exception:

        await update.message.reply_text(
            "❌ Invalid User ID.\n"
            "Please enter a valid Telegram User ID.",
            parse_mode="HTML",
        )

        return True

    row = get_user(uid)

    if not row:

        await update.message.reply_text(
            "❌ User not found in the bot database.",
            reply_markup=admin_keyboard(),
        )

        context.user_data.clear()

        return True

    conn = db()

    referrals = conn.execute("""
        SELECT COUNT(*) c
        FROM referrals
        WHERE referrer_id=?
          AND status='paid'
    """, (
        uid,
    )).fetchone()["c"]

    pending = conn.execute("""
        SELECT COUNT(*) c
        FROM withdrawals
        WHERE user_id=?
          AND status='pending'
    """, (
        uid,
    )).fetchone()["c"]

    conn.close()

    context.user_data.clear()

    wallet = (
        f"{row['wallet_type']} "
        f"{row['wallet_number']}"
        if row["wallet_type"]
        and row["wallet_number"]
        else "Not set"
    )

    status = (
        "Active"
        if row["joined_all"]
        else "Not verified"
    )

    if row["suspicious"]:
        status += " • Security review"

    text = (
        f"🔎 <b>User Details</b>\n\n"
        f"👤 Name: "
        f"<b>{escape(row['first_name'] or 'No name')}</b>\n"
        f"🔖 Username: "
        f"<b>{escape('@' + row['username'] if row['username'] else 'No username')}</b>\n"
        f"🆔 User ID: <code>{uid}</code>\n"
        f"💰 Balance: "
        f"<b>{float(row['balance']):.2f} ETB</b>\n"
        f"👥 Direct Referrals: "
        f"<b>{referrals}</b>\n"
        f"💸 Pending Withdrawals: "
        f"<b>{pending}</b>\n"
        f"👛 Wallet: "
        f"<code>{escape(wallet)}</code>\n"
        f"📌 Status: "
        f"<b>{escape(status)}</b>\n"
        f"🎁 Referral Status: "
        f"<b>{escape(row['referral_status'] or 'none')}</b>"
    )

    await update.message.reply_text(
        text,
        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "👥 View Referrals",
                    callback_data=(
                        f"admin_user_referrals_{uid}"
                    ),
                )
            ],
            [
                InlineKeyboardButton(
                    "🔙 Back to Admin",
                    callback_data="admin",
                )
            ],
        ]),
        parse_mode="HTML",
    )

    return True


# ============================================================
# ADMIN USER REFERRALS
# ============================================================

async def show_admin_user_referrals(
    query,
    user_id,
):

    conn = db()

    user = conn.execute(
        "SELECT * FROM users WHERE user_id=?",
        (user_id,),
    ).fetchone()

    if user:

        rows = conn.execute("""
            SELECT
                u.user_id,
                u.username,
                u.first_name,
                u.balance,
                u.joined_all,
                u.suspicious,
                u.referral_status
            FROM users u
            WHERE u.referred_by=?
            ORDER BY u.created_at DESC
        """, (
            user_id,
        )).fetchall()

    else:

        rows = []

    conn.close()

    if not user:

        await query.edit_message_text(
            "❌ User not found.",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "🔙 Back to Admin",
                        callback_data="admin",
                    )
                ]
            ]),
        )

        return

    name = (
        f"@{user['username']}"
        if user["username"]
        else (
            user["first_name"]
            or "No name"
        )
    )

    lines = [
        "👥 <b>User Referral Network</b>",
        "",
        f"👤 Referrer: <b>{escape(name)}</b>",
        f"🆔 ID: <code>{user_id}</code>",
        f"💰 Balance: "
        f"<b>{float(user['balance']):.2f} ETB</b>",
        f"👥 Direct Referrals: "
        f"<b>{len(rows)}</b>",
        "",
    ]

    if not rows:

        lines.append(
            "No direct referrals found."
        )

    for i, r in enumerate(
        rows,
        1,
    ):

        n = (
            f"@{r['username']}"
            if r["username"]
            else (
                r["first_name"]
                or "No name"
            )
        )

        st = (
            "Verified"
            if r["joined_all"]
            else "Not verified"
        )

        if r["suspicious"]:
            st += " • Review"

        lines.append(
            f"<b>{i}. {escape(n)}</b> | "
            f"<code>{r['user_id']}</code>\n"
            f"💰 {float(r['balance']):.2f} ETB • {st}\n"
            f"🎁 Referral: "
            f"{escape(r['referral_status'] or 'none')}"
        )

    await query.edit_message_text(
        "\n\n".join(lines),
        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "🔙 Back to Admin",
                    callback_data="admin",
                )
            ]
        ]),
        parse_mode="HTML",
    )


# ============================================================
# ADMIN PANEL
# ============================================================

async def show_admin(query):

    total, active, verified, unverified, suspicious = (
        get_user_counts()
    )

    stats = get_stats()

    minimum, maximum = get_referral_range()

    await query.edit_message_text(
        "🛠 <b>Global Cash Admin Panel</b>\n\n"
        "Control users, referrals, balances, "
        "withdrawals, tasks and rewards.\n\n"
        f"👥 Users: <b>{total}</b> | "
        f"Active: <b>{active}</b>\n"
        f"💰 Total Balance: "
        f"<b>{stats[2]:.2f} ETB</b>\n"
        f"👥 Paid Referrals: "
        f"<b>{stats[3]}</b>\n"
        f"💸 Pending Withdrawals: "
        f"<b>{stats[4]}</b>\n"
        f"⚠️ Security Reviews: "
        f"<b>{suspicious}</b>\n"
        f"🎁 Referral Random Range: "
        f"<b>{minimum:.2f} - {maximum:.2f} ETB</b>",
        reply_markup=admin_keyboard(),
        parse_mode="HTML",
    )


# ============================================================
# ADMIN STATISTICS
# ============================================================

async def show_admin_stats(query):

    stats = get_stats()

    total, active, verified, unverified, suspicious = (
        get_user_counts()
    )

    minimum, maximum = get_referral_range()

    await query.edit_message_text(
        "📊 <b>Bot Statistics</b>\n\n"
        f"👥 Total Users: <b>{total}</b>\n"
        f"🟢 Active Users: <b>{active}</b>\n"
        f"✅ Verified Users: <b>{verified}</b>\n"
        f"⏳ Unverified Users: <b>{unverified}</b>\n"
        f"💰 Total User Balance: "
        f"<b>{stats[2]:.2f} ETB</b>\n"
        f"👥 Successful Referrals: "
        f"<b>{stats[3]}</b>\n"
        f"💸 Pending Withdrawals: "
        f"<b>{stats[4]}</b>\n"
        f"⚠️ Suspicious Accounts: "
        f"<b>{stats[5]}</b>\n"
        f"🎁 Referral Random Range: "
        f"<b>{minimum:.2f} - {maximum:.2f} ETB</b>",
        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "🔙 Back to Admin",
                    callback_data="admin",
                )
            ]
        ]),
        parse_mode="HTML",
    )


# ============================================================
# ADMIN RANDOM REWARD SETTINGS
# ============================================================

async def show_admin_reward(
    query,
    context,
):

    minimum, maximum = get_referral_range()

    context.user_data.clear()

    context.user_data[
        "admin_reward_step"
    ] = "min"

    await query.edit_message_text(
        "💰 <b>Referral Random Reward Settings</b>\n\n"
        f"Current Minimum: "
        f"<b>{minimum:.2f} ETB</b>\n"
        f"Current Maximum: "
        f"<b>{maximum:.2f} ETB</b>\n\n"
        "The bot will randomly choose a reward "
        "between the minimum and maximum.\n\n"
        "Step 1/2\n"
        "Enter the new minimum reward.\n\n"
        "Example: <code>1</code>\n"
        "Example: <code>2.50</code>\n\n"
        "Cancel: /cancel",
        parse_mode="HTML",
    )


async def handle_admin_reward(
    update,
    context,
):

    step = context.user_data.get(
        "admin_reward_step"
    )

    if not step:
        return False

    text = (
        update.message.text or ""
    ).strip()

    # --------------------------------------------------------
    # MINIMUM
    # --------------------------------------------------------

    if step == "min":

        try:

            amount = float(text)

        except ValueError:

            await update.message.reply_text(
                "❌ Enter a valid number.\n"
                "Example: 1.00"
            )

            return True

        if amount <= 0:

            await update.message.reply_text(
                "❌ Minimum reward must be greater than 0."
            )

            return True

        context.user_data[
            "new_referral_min"
        ] = amount

        context.user_data[
            "admin_reward_step"
        ] = "max"

        await update.message.reply_text(
            "💰 <b>Referral Random Reward Settings</b>\n\n"
            f"New Minimum: "
            f"<b>{amount:.2f} ETB</b>\n\n"
            "Step 2/2\n"
            "Enter the new maximum reward.\n\n"
            "Example: <code>5</code>\n"
            "Example: <code>10.50</code>\n\n"
            "Cancel: /cancel",
            parse_mode="HTML",
        )

        return True

    # --------------------------------------------------------
    # MAXIMUM
    # --------------------------------------------------------

    if step == "max":

        try:

            maximum = float(text)

        except ValueError:

            await update.message.reply_text(
                "❌ Enter a valid number.\n"
                "Example: 5.00"
            )

            return True

        minimum = float(
            context.user_data.get(
                "new_referral_min",
                0,
            )
        )

        if maximum <= 0:

            await update.message.reply_text(
                "❌ Maximum reward must be greater than 0."
            )

            return True

        if maximum < minimum:

            await update.message.reply_text(
                "❌ Maximum cannot be smaller "
                "than minimum.\n\n"
                f"Minimum: {minimum:.2f} ETB\n"
                f"Maximum entered: {maximum:.2f} ETB",
            )

            return True

        set_setting(
            "referral_min",
            f"{minimum:.2f}",
        )

        set_setting(
            "referral_max",
            f"{maximum:.2f}",
        )

        context.user_data.clear()

        await update.message.reply_text(
            "✅ <b>Referral Random Reward Updated</b>\n\n"
            f"🔽 Minimum: "
            f"<b>{minimum:.2f} ETB</b>\n"
            f"🔼 Maximum: "
            f"<b>{maximum:.2f} ETB</b>\n\n"
            "🎲 Every new successful referral "
            "will receive a random reward inside this range.\n\n"
            "Previously paid referrals are not changed.",
            reply_markup=admin_keyboard(),
            parse_mode="HTML",
        )

        return True

    return False


# ============================================================
# ADMIN REFERRALS
# ============================================================

async def show_admin_referrals(query):

    conn = db()

    rows = conn.execute("""
        SELECT
            r.referrer_id,
            r.referred_id,
            r.reward_amount,
            r.created_at,
            u.username,
            u.first_name
        FROM referrals r
        LEFT JOIN users u
          ON u.user_id=r.referred_id
        WHERE r.status='paid'
        ORDER BY r.id DESC
        LIMIT 30
    """).fetchall()

    conn.close()

    if not rows:

        text = (
            "👥 <b>Successful Referrals</b>\n\n"
            "No successful referrals yet."
        )

    else:

        lines = [
            "👥 <b>Successful Referrals</b>",
            "",
        ]

        for r in rows:

            name = (
                f"@{r['username']}"
                if r["username"]
                else (
                    r["first_name"]
                    or str(r["referred_id"])
                )
            )

            lines.append(
                f"👤 Referrer: "
                f"<code>{r['referrer_id']}</code>\n"
                f"   ↳ {escape(name)} | "
                f"ID <code>{r['referred_id']}</code>\n"
                f"   🎁 Random Reward: "
                f"<b>{r['reward_amount']:.2f} ETB</b>"
            )

        text = "\n\n".join(lines)

    await query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "🔙 Back to Admin",
                    callback_data="admin",
                )
            ]
        ]),
        parse_mode="HTML",
    )


# ============================================================
# ADMIN WITHDRAWALS
# ============================================================

async def show_admin_withdrawals(query):

    conn = db()

    rows = conn.execute("""
        SELECT *
        FROM withdrawals
        WHERE status='pending'
        ORDER BY id ASC
        LIMIT 20
    """).fetchall()

    conn.close()

    if not rows:

        await query.edit_message_text(
            "💸 <b>Pending Withdrawals</b>\n\n"
            "No pending withdrawals.",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "🔙 Back to Admin",
                        callback_data="admin",
                    )
                ]
            ]),
            parse_mode="HTML",
        )

        return

    buttons = []

    for r in rows:

        buttons.append([
            InlineKeyboardButton(
                f"#{r['id']} | "
                f"{r['amount']:.2f} ETB",
                callback_data=(
                    f"admin_wd_{r['id']}"
                ),
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
        "Select a request:",
        reply_markup=InlineKeyboardMarkup(
            buttons
        ),
        parse_mode="HTML",
    )


# ============================================================
# ADMIN WITHDRAWAL DETAIL
# ============================================================

async def show_admin_withdrawal_detail(
    query,
    withdrawal_id,
):

    conn = db()

    row = conn.execute("""
        SELECT
            w.*,
            u.username,
            u.first_name
        FROM withdrawals w
        LEFT JOIN users u
          ON u.user_id=w.user_id
        WHERE w.id=?
    """, (
        withdrawal_id,
    )).fetchone()

    conn.close()

    if not row:

        await query.edit_message_text(
            "❌ Withdrawal not found.",
            reply_markup=back_keyboard(),
        )

        return

    username = (
        f"@{row['username']}"
        if row["username"]
        else "N/A"
    )

    buttons = []

    if row["status"] == "pending":

        buttons.append([
            InlineKeyboardButton(
                "✅ Approve",
                callback_data=(
                    f"approve_wd_{withdrawal_id}"
                ),
            ),
            InlineKeyboardButton(
                "❌ Reject",
                callback_data=(
                    f"reject_wd_{withdrawal_id}"
                ),
            ),
        ])

    buttons.append([
        InlineKeyboardButton(
            "👥 View User Referrals",
            callback_data=(
                f"admin_user_referrals_{row['user_id']}"
            ),
        )
    ])

    buttons.append([
        InlineKeyboardButton(
            "🔙 Back",
            callback_data="admin_withdrawals",
        )
    ])

    await query.edit_message_text(
        "💳 <b>Withdrawal Details</b>\n\n"
        f"🆔 Request: "
        f"<code>#{row['id']}</code>\n"
        f"👤 User ID: "
        f"<code>{row['user_id']}</code>\n"
        f"👤 Username: "
        f"{escape(username)}\n"
        f"💰 Amount: "
        f"<b>{row['amount']:.2f} ETB</b>\n"
        f"🏦 Method: "
        f"<b>{escape(row['wallet_type'])}</b>\n"
        f"🔢 Wallet: "
        f"<code>{escape(row['wallet_number'])}</code>\n"
        f"📌 Status: "
        f"<b>{escape(row['status'])}</b>",
        reply_markup=InlineKeyboardMarkup(
            buttons
        ),
        parse_mode="HTML",
    )


# ============================================================
# APPROVE WITHDRAWAL
# ============================================================

async def approve_withdrawal(
    query,
    context,
    withdrawal_id,
):

    conn = db()

    try:

        row = conn.execute("""
            SELECT *
            FROM withdrawals
            WHERE id=?
        """, (
            withdrawal_id,
        )).fetchone()

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
                f"Already {row['status']}.",
                show_alert=True,
            )

            return

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
            conn.close()

            await query.answer(
                "Request was already processed.",
                show_alert=True,
            )

            return

        conn.commit()

    except Exception:

        conn.rollback()

        logger.exception(
            "Approve withdrawal error"
        )

        await query.answer(
            "Approval failed.",
            show_alert=True,
        )

        return

    finally:

        conn.close()

    await query.edit_message_text(
        "✅ <b>Withdrawal Approved</b>\n\n"
        f"Request: "
        f"<code>#{withdrawal_id}</code>\n"
        f"Amount: "
        f"<b>{row['amount']:.2f} ETB</b>\n"
        f"Wallet: "
        f"<code>{escape(row['wallet_number'])}</code>",
        parse_mode="HTML",
    )

    try:

        await context.bot.send_message(
            chat_id=row["user_id"],
            text=(
                "✅ <b>Withdrawal Approved</b>\n\n"
                f"💰 Amount: "
                f"<b>{row['amount']:.2f} ETB</b>\n"
                f"🏦 Method: "
                f"<b>{escape(row['wallet_type'])}</b>\n"
                f"🔢 Wallet: "
                f"<code>{escape(row['wallet_number'])}</code>\n\n"
                "Your withdrawal has been approved."
            ),
            parse_mode="HTML",
        )

    except Exception:

        logger.exception(
            "Could not notify withdrawal user"
        )


# ============================================================
# REJECT WITHDRAWAL
# ============================================================

async def reject_withdrawal(
    query,
    context,
    withdrawal_id,
):

    conn = db()

    try:

        row = conn.execute("""
            SELECT *
            FROM withdrawals
            WHERE id=?
        """, (
            withdrawal_id,
        )).fetchone()

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
                f"Already {row['status']}.",
                show_alert=True,
            )

            return

        # ----------------------------------------------------
        # Change status FIRST.
        # ----------------------------------------------------

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
            conn.close()

            await query.answer(
                "Request was already processed.",
                show_alert=True,
            )

            return

        # ----------------------------------------------------
        # Refund exactly once.
        # ----------------------------------------------------

        conn.execute("""
            UPDATE users
            SET balance=balance+?
            WHERE user_id=?
        """, (
            row["amount"],
            row["user_id"],
        ))

        conn.commit()

    except Exception:

        conn.rollback()

        logger.exception(
            "Reject withdrawal error"
        )

        await query.answer(
            "Rejection failed.",
            show_alert=True,
        )

        return

    finally:

        conn.close()

    await query.edit_message_text(
        "❌ <b>Withdrawal Rejected</b>\n\n"
        f"Request: "
        f"<code>#{withdrawal_id}</code>\n"
        f"Refunded: "
        f"<b>{row['amount']:.2f} ETB</b>",
        parse_mode="HTML",
    )

    try:

        await context.bot.send_message(
            chat_id=row["user_id"],
            text=(
                "❌ <b>Withdrawal Rejected</b>\n\n"
                f"💰 Refunded: "
                f"<b>{row['amount']:.2f} ETB</b>\n\n"
                "The amount has been returned to your balance.\n"
                f"For questions: {SUPPORT_USERNAME}"
            ),
            parse_mode="HTML",
        )

    except Exception:

        logger.exception(
            "Could not notify rejected user"
        )


# ============================================================
# ADMIN SUSPICIOUS USERS
# ============================================================

async def show_admin_suspicious(query):

    conn = db()

    rows = conn.execute("""
        SELECT
            user_id,
            username,
            first_name,
            balance
        FROM users
        WHERE suspicious=1
        ORDER BY user_id DESC
        LIMIT 30
    """).fetchall()

    conn.close()

    if not rows:

        text = (
            "⚠️ <b>Suspicious Accounts</b>\n\n"
            "None."
        )

    else:

        lines = [
            "⚠️ <b>Suspicious Accounts</b>",
            "",
        ]

        for r in rows:

            name = (
                f"@{r['username']}"
                if r["username"]
                else (
                    r["first_name"]
                    or "No name"
                )
            )

            lines.append(
                f"👤 {escape(name)}\n"
                f"ID: <code>{r['user_id']}</code>\n"
                f"Balance: "
                f"<b>{r['balance']:.2f} ETB</b>"
            )

        text = "\n\n".join(lines)

    await query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "🔙 Back to Admin",
                    callback_data="admin",
                )
            ]
        ]),
        parse_mode="HTML",
    )


# ============================================================
# ADMIN ADD TASK
# ============================================================

async def start_add_task(
    query,
    context,
):

    if not admin_only(
        query.from_user.id
    ):
        return

    context.user_data.clear()

    context.user_data[
        "task_step"
    ] = "title"

    await query.edit_message_text(
        "🎯 <b>Add New Task</b>\n\n"
        "Step 1/5\n"
        "Send the task title.\n\n"
        "Example: "
        "<code>Join Daily Money Channel</code>\n\n"
        "Cancel: /cancel",
        parse_mode="HTML",
    )


# ============================================================
# ADMIN TEST BALANCE
# ============================================================

async def start_test_balance(
    query,
    context,
):

    if not admin_only(
        query.from_user.id
    ):
        return

    context.user_data.clear()

    context.user_data[
        "admin_step"
    ] = "test_balance_user"

    await query.edit_message_text(
        "🧪 <b>Add Test Balance</b>\n\n"
        "ይህ feature ለሙከራ ብቻ ነው።\n\n"
        "Step 1/2: Enter User ID.\n\n"
        "Example:\n"
        "<code>8727153413</code>",
        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "❌ Cancel",
                    callback_data="admin",
                )
            ]
        ]),
        parse_mode="HTML",
    )


async def handle_admin_test_balance(
    update,
    context,
):

    step = context.user_data.get(
        "admin_step"
    )

    if step not in (
        "test_balance_user",
        "test_balance_amount",
    ):
        return False

    text = (
        update.message.text or ""
    ).strip()

    # --------------------------------------------------------
    # USER ID
    # --------------------------------------------------------

    if step == "test_balance_user":

        try:

            target_user_id = int(text)

            if target_user_id <= 0:
                raise ValueError

        except ValueError:

            await update.message.reply_text(
                "❌ Invalid User ID!\n\n"
                "እባክዎን ትክክለኛ Telegram User ID ያስገቡ።\n\n"
                "Example:\n"
                "<code>8727153413</code>",
                parse_mode="HTML",
            )

            return True

        target_user = get_user(
            target_user_id
        )

        if not target_user:

            await update.message.reply_text(
                "❌ <b>User Not Found!</b>\n\n"
                f"User ID "
                f"<code>{target_user_id}</code> "
                "በbot database ውስጥ አልተገኘም።\n\n"
                "ተጠቃሚው መጀመሪያ /start "
                "ብሎ Bot መጀመር አለበት።",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup([
                    [
                        InlineKeyboardButton(
                            "🛠 Admin Panel",
                            callback_data="admin",
                        )
                    ]
                ]),
            )

            return True

        context.user_data[
            "test_balance_user_id"
        ] = target_user_id

        context.user_data[
            "admin_step"
        ] = "test_balance_amount"

        await update.message.reply_text(
            "🧪 <b>Add Test Balance</b>\n\n"
            f"👤 User ID: "
            f"<code>{target_user_id}</code>\n"
            f"💰 Current Balance: "
            f"<b>{float(target_user['balance']):.2f} ETB</b>\n\n"
            "Step 2/2: Enter amount to add.\n\n"
            "ለምሳሌ፦\n"
            "<code>100</code>",
            parse_mode="HTML",
        )

        return True

    # --------------------------------------------------------
    # AMOUNT
    # --------------------------------------------------------

    if step == "test_balance_amount":

        try:

            amount = float(text)

            if amount <= 0:
                raise ValueError

        except ValueError:

            await update.message.reply_text(
                "❌ Invalid amount!\n\n"
                "ከ 0 በላይ የሆነ ቁጥር ያስገቡ።\n\n"
                "Example:\n"
                "<code>100</code>",
                parse_mode="HTML",
            )

            return True

        target_user_id = context.user_data.get(
            "test_balance_user_id"
        )

        if not target_user_id:

            context.user_data.clear()

            await update.message.reply_text(
                "❌ Test balance session expired.\n"
                "እባክዎን እንደገና ይጀምሩ።",
                reply_markup=admin_keyboard(),
            )

            return True

        target_user = get_user(
            target_user_id
        )

        if not target_user:

            context.user_data.clear()

            await update.message.reply_text(
                "❌ User not found!",
                reply_markup=admin_keyboard(),
            )

            return True

        old_balance = float(
            target_user["balance"]
        )

        add_balance(
            target_user_id,
            amount,
        )

        new_balance = (
            old_balance + amount
        )

        context.user_data.clear()

        await update.message.reply_text(
            "✅ <b>Test Balance Added Successfully!</b>\n\n"
            f"👤 User ID: "
            f"<code>{target_user_id}</code>\n"
            f"💰 Added: "
            f"<b>+{amount:.2f} ETB</b>\n"
            f"💵 Old Balance: "
            f"<b>{old_balance:.2f} ETB</b>\n"
            f"💵 New Balance: "
            f"<b>{new_balance:.2f} ETB</b>\n\n"
            "🧪 This is a bot database test balance.\n"
            "ይህ ለሙከራ ብቻ የተጨመረ "
            "የBot Balance ነው።\n"
            "በCBE ወይም Telebirr ገንዘብ አልተላከም።",
            reply_markup=admin_keyboard(),
            parse_mode="HTML",
        )

        try:

            await context.bot.send_message(
                chat_id=target_user_id,
                text=(
                    "🧪 <b>Test Balance Added</b>\n\n"
                    f"💰 Added: "
                    f"<b>+{amount:.2f} ETB</b>\n"
                    f"💵 New Balance: "
                    f"<b>{new_balance:.2f} ETB</b>\n\n"
                    "ይህ ለBot ሙከራ ብቻ "
                    "የተጨመረ Test Balance ነው።"
                ),
                parse_mode="HTML",
            )

        except Exception:

            logger.exception(
                "Could not notify test user."
            )

        return True

    return False


# ============================================================
# ADMIN TASK CREATION
# ============================================================

async def handle_admin_task_creation(
    update,
    context,
):

    step = context.user_data.get(
        "task_step"
    )

    if not step:
        return False

    text = (
        update.message.text or ""
    ).strip()

    # --------------------------------------------------------
    # TITLE
    # --------------------------------------------------------

    if step == "title":

        if not text:

            await update.message.reply_text(
                "❌ Task title cannot be empty."
            )

            return True

        context.user_data[
            "new_task_title"
        ] = text

        context.user_data[
            "task_step"
        ] = "description"

        await update.message.reply_text(
            "Step 2/5\n"
            "Send task description."
        )

        return True

    # --------------------------------------------------------
    # DESCRIPTION
    # --------------------------------------------------------

    if step == "description":

        if not text:

            await update.message.reply_text(
                "❌ Description cannot be empty."
            )

            return True

        context.user_data[
            "new_task_description"
        ] = text

        context.user_data[
            "task_step"
        ] = "username"

        await update.message.reply_text(
            "Step 3/5\n"
            "Send channel username.\n\n"
            "Example: @ExampleChannel"
        )

        return True

    # --------------------------------------------------------
    # USERNAME
    # --------------------------------------------------------

    if step == "username":

        if not re.fullmatch(
            r"@[A-Za-z0-9_]{5,}",
            text,
        ):

            await update.message.reply_text(
                "❌ Username must be a valid "
                "Telegram channel username "
                "starting with @."
            )

            return True

        context.user_data[
            "new_task_username"
        ] = text

        context.user_data[
            "task_step"
        ] = "url"

        await update.message.reply_text(
            "Step 4/5\n"
            "Send channel join URL.\n\n"
            "Example: "
            "https://t.me/ExampleChannel"
        )

        return True

    # --------------------------------------------------------
    # URL
    # --------------------------------------------------------

    if step == "url":

        if not text.startswith(
            "https://t.me/"
        ):

            await update.message.reply_text(
                "❌ Please send a valid "
                "Telegram URL starting with "
                "https://t.me/"
            )

            return True

        context.user_data[
            "new_task_url"
        ] = text

        context.user_data[
            "task_step"
        ] = "reward"

        await update.message.reply_text(
            "Step 5/5\n"
            "Send task reward in ETB.\n\n"
            "Example: 1.50"
        )

        return True

    # --------------------------------------------------------
    # REWARD
    # --------------------------------------------------------

    if step == "reward":

        try:

            reward = float(text)

        except ValueError:

            await update.message.reply_text(
                "❌ Enter a valid reward number."
            )

            return True

        if reward <= 0:

            await update.message.reply_text(
                "❌ Reward must be greater than 0."
            )

            return True

        task_id = create_task(
            context.user_data[
                "new_task_title"
            ],
            context.user_data[
                "new_task_description"
            ],
            context.user_data[
                "new_task_username"
            ],
            context.user_data[
                "new_task_url"
            ],
            reward,
        )

        context.user_data.clear()

        await update.message.reply_text(
            "✅ <b>Task Created</b>\n\n"
            f"🆔 Task ID: "
            f"<code>{task_id}</code>\n"
            f"🎁 Reward: "
            f"<b>{reward:.2f} ETB</b>\n\n"
            "This reward is the same for every "
            "user who successfully completes "
            "this task.",
            reply_markup=admin_keyboard(),
            parse_mode="HTML",
        )

        return True

    return False


# ============================================================
# TASK DETAIL
# ============================================================

async def show_task_detail(
    query,
    context,
    task_id,
):

    task = get_task(
        task_id
    )

    if not task or not task["active"]:

        await query.edit_message_text(
            "❌ Task not found or inactive.",
            reply_markup=back_keyboard(),
        )

        return

    user_id = query.from_user.id

    paid = user_task_paid(
        user_id,
        task_id,
    )

    if paid:

        buttons = [
            [
                InlineKeyboardButton(
                    "🔙 Back",
                    callback_data="tasks",
                )
            ]
        ]

    else:

        buttons = [
            [
                InlineKeyboardButton(
                    "📢 Open Channel",
                    url=task["channel_url"],
                )
            ],
            [
                InlineKeyboardButton(
                    "✅ Verify Task",
                    callback_data=(
                        f"verify_task_{task_id}"
                    ),
                )
            ],
            [
                InlineKeyboardButton(
                    "🔙 Back",
                    callback_data="tasks",
                )
            ],
        ]

    await query.edit_message_text(
        "🎯 <b>Task</b>\n\n"
        f"📌 <b>{escape(task['title'])}</b>\n\n"
        f"{escape(task['description'])}\n\n"
        f"🎁 Reward: "
        f"<b>{task['reward']:.2f} ETB</b>\n"
        f"📢 Channel: "
        f"<b>{escape(task['channel_username'])}</b>\n\n"
        + (
            "✅ You have already received this reward."
            if paid
            else
            "Join the channel and press Verify Task."
        ),
        reply_markup=InlineKeyboardMarkup(
            buttons
        ),
        parse_mode="HTML",
    )


# ============================================================
# TASK VERIFICATION
# ============================================================

async def verify_task(
    query,
    context,
    task_id,
):

    user_id = query.from_user.id

    task = get_task(
        task_id
    )

    if not task or not task["active"]:

        await query.answer(
            "Task is not available.",
            show_alert=True,
        )

        return

    # --------------------------------------------------------
    # Already paid
    # --------------------------------------------------------

    if user_task_paid(
        user_id,
        task_id,
    ):

        await query.answer(
            "You already received this reward.",
            show_alert=True,
        )

        return

    # --------------------------------------------------------
    # Verify Telegram membership
    # --------------------------------------------------------

    try:

        member = await context.bot.get_chat_member(
            chat_id=task["channel_username"],
            user_id=user_id,
        )

        if member.status not in (
            "member",
            "administrator",
            "creator",
        ):

            await query.answer(
                "❌ You have not joined the channel yet.",
                show_alert=True,
            )

            return

    except Exception:

        await query.answer(
            "❌ Could not verify membership. Try again.",
            show_alert=True,
        )

        return

    # --------------------------------------------------------
    # Atomic task payment.
    #
    # This prevents duplicate payment if two verify requests
    # arrive almost at the same time.
    # --------------------------------------------------------

    conn = db()

    try:

        # Insert unpaid record if it does not exist.
        conn.execute("""
            INSERT OR IGNORE INTO user_tasks(
                user_id,
                task_id,
                verified,
                paid,
                paid_at
            )
            VALUES(?,?,?,?,?)
        """, (
            user_id,
            task_id,
            1,
            0,
            None,
        ))

        # Pay only if paid is still 0.
        cur = conn.execute("""
            UPDATE user_tasks
            SET verified=1,
                paid=1,
                paid_at=?
            WHERE user_id=?
              AND task_id=?
              AND paid=0
        """, (
            now(),
            user_id,
            task_id,
        ))

        if cur.rowcount != 1:

            conn.rollback()

            await query.answer(
                "You already received this reward.",
                show_alert=True,
            )

            return

        # Add exact task reward.
        conn.execute("""
            UPDATE users
            SET balance=balance+?
            WHERE user_id=?
        """, (
            float(task["reward"]),
            user_id,
        ))

        conn.commit()

    except Exception:

        conn.rollback()

        logger.exception(
            "Task payment error"
        )

        await query.answer(
            "❌ Payment failed. Please try again.",
            show_alert=True,
        )

        return

    finally:

        conn.close()

    await query.edit_message_text(
        "🎉 <b>Task Completed!</b>\n\n"
        f"🎁 Reward: "
        f"<b>{task['reward']:.2f} ETB</b>\n"
        f"💰 New Balance: "
        f"<b>{get_balance(user_id):.2f} ETB</b>",
        reply_markup=back_keyboard(),
        parse_mode="HTML",
    )


# ============================================================
# CALLBACKS
# ============================================================

async def callbacks(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    query = update.callback_query

    await query.answer()

    user = update.effective_user

    data = query.data or ""

    create_or_update_user(
        user
    )

    # ========================================================
    # ADMIN PROTECTION
    # ========================================================

    if (
        data.startswith("admin")
        or data.startswith("approve_wd_")
        or data.startswith("reject_wd_")
    ):

        if not admin_only(
            user.id
        ):

            await query.answer(
                "⛔ Admin only.",
                show_alert=True,
            )

            return

    try:

        # ----------------------------------------------------
        # HOME
        # ----------------------------------------------------

        if data == "home":

            context.user_data.clear()

            await show_home(
                query
            )

            return

        # ----------------------------------------------------
        # VERIFY
        # ----------------------------------------------------

        if data == "verify":

            await verify_user(
                query,
                context,
            )

            return

        # ----------------------------------------------------
        # BALANCE
        # ----------------------------------------------------

        if data == "balance":

            await show_balance(
                query,
                user.id,
            )

            return

        # ----------------------------------------------------
        # REFERRAL
        # ----------------------------------------------------

        if data == "referral":

            await show_referral(
                query,
                user.id,
            )

            return

        # ----------------------------------------------------
        # SHARE REFERRAL
        # ----------------------------------------------------

        if data == "share_ref":

            link = (
                f"https://t.me/"
                f"{BOT_USERNAME}"
                f"?start={user.id}"
            )

            share_url = (
                "https://t.me/share/url?"
                f"url={quote(link, safe='')}"
                "&text="
                f"{quote('Join Global Cash Bot and start earning ETB!', safe='')}"
            )

            await query.edit_message_text(
                "📤 <b>Share Referral Link / "
                "የReferral ሊንክ ያጋሩ</b>\n\n"
                "Send your personal referral link "
                "directly to another person.\n"
                "የግል ሊንክዎን ለሌላ ሰው "
                "በቀጥታ ይላኩ።\n\n"
                f"🔗 <code>{escape(link)}</code>",
                reply_markup=InlineKeyboardMarkup([
                    [
                        InlineKeyboardButton(
                            "📤 Share Again",
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
                parse_mode="HTML",
            )

            return

        # ----------------------------------------------------
        # TASKS
        # ----------------------------------------------------

        if data == "tasks":

            await show_tasks(
                query,
                user.id,
            )

            return

        # ----------------------------------------------------
        # TASK DETAIL
        # ----------------------------------------------------

        if data.startswith("task_"):

            task_id = int(
                data.split(
                    "_",
                    1,
                )[1]
            )

            await show_task_detail(
                query,
                context,
                task_id,
            )

            return

        # ----------------------------------------------------
        # VERIFY TASK
        # ----------------------------------------------------

        if data.startswith(
            "verify_task_"
        ):

            task_id = int(
                data.split(
                    "_"
                )[-1]
            )

            await verify_task(
                query,
                context,
                task_id,
            )

            return

        # ----------------------------------------------------
        # WALLET
        # ----------------------------------------------------

        if data == "wallet":

            await show_wallet(
                query,
                user.id,
            )

            return

        # ----------------------------------------------------
        # CBE
        # ----------------------------------------------------

        if data == "wallet_cbe":

            context.user_data[
                "wallet_type"
            ] = "CBE"

            await query.edit_message_text(
                "🏦 <b>CBE Wallet</b>\n\n"
                "Send your 13-digit CBE account number.\n"
                "It must start with <b>1000</b>.\n\n"
                "Example: "
                "<code>1000123456789</code>\n\n"
                "Cancel: /cancel",
                parse_mode="HTML",
            )

            return

        # ----------------------------------------------------
        # TELEBIRR
        # ----------------------------------------------------

        if data == "wallet_telebirr":

            context.user_data[
                "wallet_type"
            ] = "Telebirr"

            await query.edit_message_text(
                "📱 <b>Telebirr Wallet</b>\n\n"
                "Send your 10-digit Telebirr number.\n"
                "It must start with <b>09</b> or <b>07</b>.\n\n"
                "Example: "
                "<code>0912345678</code>\n\n"
                "Cancel: /cancel",
                parse_mode="HTML",
            )

            return

        # ----------------------------------------------------
        # WITHDRAW
        # ----------------------------------------------------

        if data == "withdraw":

            await show_withdraw(
                query,
                user.id,
                context,
            )

            return

        # ----------------------------------------------------
        # SUPPORT
        # ----------------------------------------------------

        if data == "support":

            await show_support(
                query
            )

            return

        # ====================================================
        # ADMIN
        # ====================================================

        if data == "admin":

            await show_admin(
                query
            )

            return

        # ----------------------------------------------------
        # ADMIN STATS
        # ----------------------------------------------------

        if data == "admin_stats":

            await show_admin_stats(
                query
            )

            return

        # ----------------------------------------------------
        # ADMIN USERS
        # ----------------------------------------------------

        if data == "admin_users":

            await show_admin_users(
                query
            )

            return

        # ----------------------------------------------------
        # ADMIN CHANNELS
        # ----------------------------------------------------

        if data == "admin_channels":

            await show_admin_channels(
                query
            )

            return

        # ----------------------------------------------------
        # ADMIN CHECK BALANCE
        # ----------------------------------------------------

        if data == "admin_check_balance":

            await start_check_balance(
                query,
                context,
            )

            return

        # ----------------------------------------------------
        # ADMIN USER REFERRALS
        # ----------------------------------------------------

        if data.startswith(
            "admin_user_referrals_"
        ):

            uid = int(
                data.rsplit(
                    "_",
                    1,
                )[1]
            )

            await show_admin_user_referrals(
                query,
                uid,
            )

            return

        # ----------------------------------------------------
        # ADMIN REWARD
        # ----------------------------------------------------

        if data == "admin_reward":

            await show_admin_reward(
                query,
                context,
            )

            return

        # ----------------------------------------------------
        # ADMIN REFERRALS
        # ----------------------------------------------------

        if data == "admin_referrals":

            await show_admin_referrals(
                query
            )

            return

        # ----------------------------------------------------
        # ADMIN WITHDRAWALS
        # ----------------------------------------------------

        if data == "admin_withdrawals":

            await show_admin_withdrawals(
                query
            )

            return

        # ----------------------------------------------------
        # ADMIN WITHDRAWAL DETAIL
        # ----------------------------------------------------

        if data.startswith(
            "admin_wd_"
        ):

            withdrawal_id = int(
                data.split(
                    "_"
                )[-1]
            )

            await show_admin_withdrawal_detail(
                query,
                withdrawal_id,
            )

            return

        # ----------------------------------------------------
        # ADMIN SUSPICIOUS
        # ----------------------------------------------------

        if data == "admin_suspicious":

            await show_admin_suspicious(
                query
            )

            return

        # ----------------------------------------------------
        # ADMIN ADD TASK
        # ----------------------------------------------------

        if data == "admin_add_task":

            await start_add_task(
                query,
                context,
            )

            return

        # ----------------------------------------------------
        # ADMIN TEST BALANCE
        # ----------------------------------------------------

        if data == "admin_test_balance":

            await start_test_balance(
                query,
                context,
            )

            return

        # ----------------------------------------------------
        # APPROVE WITHDRAWAL
        # ----------------------------------------------------

        if data.startswith(
            "approve_wd_"
        ):

            withdrawal_id = int(
                data.split(
                    "_"
                )[-1]
            )

            await approve_withdrawal(
                query,
                context,
                withdrawal_id,
            )

            return

        # ----------------------------------------------------
        # REJECT WITHDRAWAL
        # ----------------------------------------------------

        if data.startswith(
            "reject_wd_"
        ):

            withdrawal_id = int(
                data.split(
                    "_"
                )[-1]
            )

            await reject_withdrawal(
                query,
                context,
                withdrawal_id,
            )

            return

    except Exception:

        logger.exception(
            "Callback error: %s",
            data,
        )

        try:

            await query.edit_message_text(
                "❌ <b>Something went wrong.</b>\n\n"
                "Please try again.",
                reply_markup=back_keyboard(),
                parse_mode="HTML",
            )

        except Exception:
            pass


# ============================================================
# ADMIN COMMAND
# ============================================================

async def admin_command(
    update,
    context,
):

    if not admin_only(
        update.effective_user.id
    ):

        await update.message.reply_text(
            "⛔ Admin only."
        )

        return

    await update.message.reply_text(
        "🛠 <b>Global Cash Admin Panel | "
        "የGlobal Cash አድሚን ፓነል</b>",
        reply_markup=admin_keyboard(),
        parse_mode="HTML",
    )


# ============================================================
# CANCEL
# ============================================================

async def cancel(
    update,
    context,
):

    context.user_data.clear()

    if admin_only(
        update.effective_user.id
    ):

        await update.message.reply_text(
            "❌ Cancelled.",
            reply_markup=admin_keyboard(),
        )

    else:

        await update.message.reply_text(
            "❌ Cancelled.",
            reply_markup=main_keyboard(),
        )


# ============================================================
# MESSAGE ROUTER
# ============================================================

async def message_router(
    update,
    context,
):

    if not update.message:
        return

    user = update.effective_user

    create_or_update_user(
        user
    )

    # ========================================================
    # ADMIN INPUTS
    # ========================================================

    if admin_only(
        user.id
    ):

        # Random referral reward
        if await handle_admin_reward(
            update,
            context,
        ):
            return

        # Task creation
        if await handle_admin_task_creation(
            update,
            context,
        ):
            return

        # Test balance
        if await handle_admin_test_balance(
            update,
            context,
        ):
            return

        # Check user
        if await handle_admin_check_balance(
            update,
            context,
        ):
            return

    # ========================================================
    # USER WALLET
    # ========================================================

    if await handle_wallet_number(
        update,
        context,
    ):
        return

    # ========================================================
    # WITHDRAWAL
    # ========================================================

    if await handle_withdraw_amount(
        update,
        context,
    ):
        return


# ============================================================
# ERROR HANDLER
# ============================================================

async def error_handler(
    update: object,
    context: ContextTypes.DEFAULT_TYPE,
):

    logger.exception(
        "Unhandled exception",
        exc_info=context.error,
    )


# ============================================================
# MAIN
# ============================================================

def main():

    if not BOT_TOKEN:

        raise RuntimeError(
            "BOT_TOKEN environment variable is missing."
        )

    if not ADMIN_ID:

        raise RuntimeError(
            "ADMIN_ID environment variable is missing."
        )

    # --------------------------------------------------------
    # Initialize database
    # --------------------------------------------------------

    init_db()

    # --------------------------------------------------------
    # Application
    # --------------------------------------------------------

    application = (
        Application.builder()
        .token(BOT_TOKEN)
        .build()
    )

    # --------------------------------------------------------
    # Commands
    # --------------------------------------------------------

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

    # --------------------------------------------------------
    # Callback buttons
    # --------------------------------------------------------

    application.add_handler(
        CallbackQueryHandler(
            callbacks
        )
    )

    # --------------------------------------------------------
    # Text messages
    # --------------------------------------------------------

    application.add_handler(
        MessageHandler(
            filters.TEXT
            & ~filters.COMMAND,
            message_router,
        )
    )

    # --------------------------------------------------------
    # Error handler
    # --------------------------------------------------------

    application.add_error_handler(
        error_handler
    )

    logger.info(
        "Global Cash Bot starting..."
    )

    # --------------------------------------------------------
    # Start polling
    # --------------------------------------------------------

    application.run_polling(
        allowed_updates=Update.ALL_TYPES
    )


# ============================================================
# RUN
# ============================================================

if __name__ == "__main__":
    main()

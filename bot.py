import logging
import sqlite3
from datetime import datetime
from decimal import Decimal, InvalidOperation

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
    KeyboardButton,
)
from telegram.constants import ChatMemberStatus
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

# ============================================================
# GLOBAL CASH BOT
# ============================================================

BOT_NAME = "Global Cash Bot"
BOT_USERNAME = "GloballCashh_Bot"

SUPPORT_USERNAME = "AmanM_12"

# ============================================================
# FIXED REFERRAL REWARD
# ============================================================

REFERRAL_REWARD = Decimal("2.00")

# ============================================================
# WITHDRAWAL SETTINGS
# ============================================================

MIN_WITHDRAW = Decimal("30.00")
CURRENCY = "ETB"

DB_FILE = "global_cash.db"

# ============================================================
# REQUIRED CHANNELS
# ============================================================

REQUIRED_CHANNELS = [
    ("@Sheger_tech1", "Sheger Tech"),
    ("@EthioVortex1", "Ethio Vortex"),
    ("@ethiocashflow", "Ethio Cash Flow"),
    ("@AmanIncomeLab", "Aman Income Lab"),
    ("@OnlineIncomeHub07", "Online Income Hub"),
    ("@Paymentprooff2", "Payment Proof"),
]

# ============================================================
# ADMIN
# ============================================================

ADMIN_ID = 0


# ============================================================
# LOGGING
# ============================================================

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)

logger = logging.getLogger(__name__)


# ============================================================
# DATABASE
# ============================================================

def db():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = db()
    cur = conn.cursor()

    # ---------------- USERS ----------------

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

    # ---------------- REFERRALS ----------------

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

    # ---------------- WITHDRAWALS ----------------

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

    # ---------------- TASKS ----------------

    cur.execute("""
        CREATE TABLE IF NOT EXISTS tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            description TEXT,
            channel_username TEXT NOT NULL,
            channel_url TEXT NOT NULL,
            reward REAL NOT NULL DEFAULT 0,
            active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL
        )
    """)

    # ---------------- USER TASKS ----------------

    cur.execute("""
        CREATE TABLE IF NOT EXISTS user_tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            task_id INTEGER NOT NULL,
            paid INTEGER NOT NULL DEFAULT 0,
            verified_at TEXT,
            UNIQUE(user_id, task_id)
        )
    """)

    # Migration for older database versions
    existing_columns = {
        row["name"]
        for row in cur.execute("PRAGMA table_info(users)").fetchall()
    }

    if "referral_status" not in existing_columns:
        cur.execute(
            "ALTER TABLE users ADD COLUMN referral_status TEXT NOT NULL DEFAULT 'none'"
        )

    if "suspicious" not in existing_columns:
        cur.execute(
            "ALTER TABLE users ADD COLUMN suspicious INTEGER NOT NULL DEFAULT 0"
        )

    if "joined_all" not in existing_columns:
        cur.execute(
            "ALTER TABLE users ADD COLUMN joined_all INTEGER NOT NULL DEFAULT 0"
        )

    if "wallet_type" not in existing_columns:
        cur.execute(
            "ALTER TABLE users ADD COLUMN wallet_type TEXT"
        )

    if "wallet_number" not in existing_columns:
        cur.execute(
            "ALTER TABLE users ADD COLUMN wallet_number TEXT"
        )

    # Prevent the same wallet from being assigned to multiple users.
    try:
        cur.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS
            unique_wallet_per_type
            ON users(wallet_type, wallet_number)
            WHERE wallet_type IS NOT NULL
            AND wallet_number IS NOT NULL
            AND wallet_number != ''
        """)
    except sqlite3.IntegrityError:
        logger.warning(
            "Could not create wallet unique index because old duplicate "
            "wallet data exists."
        )

    conn.commit()
    conn.close()


# ============================================================
# USER FUNCTIONS
# ============================================================

def create_user(user_id, username, first_name, referred_by=None):
    conn = db()
    cur = conn.cursor()

    now = datetime.utcnow().isoformat()

    cur.execute("""
        INSERT OR IGNORE INTO users
        (
            user_id,
            username,
            first_name,
            referred_by,
            created_at
        )
        VALUES (?, ?, ?, ?, ?)
    """, (
        user_id,
        username,
        first_name,
        referred_by,
        now,
    ))

    conn.commit()
    conn.close()


def get_user(user_id):
    conn = db()
    cur = conn.cursor()

    row = cur.execute(
        "SELECT * FROM users WHERE user_id = ?",
        (user_id,),
    ).fetchone()

    conn.close()
    return row


def update_user_info(user_id, username, first_name):
    conn = db()
    cur = conn.cursor()

    cur.execute("""
        UPDATE users
        SET username = ?, first_name = ?
        WHERE user_id = ?
    """, (
        username,
        first_name,
        user_id,
    ))

    conn.commit()
    conn.close()


def get_balance(user_id):
    row = get_user(user_id)

    if not row:
        return Decimal("0.00")

    return Decimal(str(row["balance"] or 0)).quantize(
        Decimal("0.01")
    )


def add_balance(user_id, amount):
    amount = Decimal(str(amount))

    conn = db()
    cur = conn.cursor()

    cur.execute("""
        UPDATE users
        SET balance = balance + ?
        WHERE user_id = ?
    """, (
        float(amount),
        user_id,
    ))

    conn.commit()
    conn.close()


def set_joined_all(user_id, value=True):
    conn = db()
    cur = conn.cursor()

    cur.execute("""
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
    conn = db()
    cur = conn.cursor()

    cur.execute("""
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

    return bool(row and row["suspicious"] == 1)


# ============================================================
# REFERRAL FUNCTIONS
# ============================================================

def get_successful_referral_count(user_id):
    conn = db()
    cur = conn.cursor()

    count = cur.execute("""
        SELECT COUNT(*)
        FROM referrals
        WHERE referrer_id = ?
        AND status = 'paid'
    """, (
        user_id,
    )).fetchone()[0]

    conn.close()

    return count


def get_successful_referrals(user_id):
    conn = db()
    cur = conn.cursor()

    rows = cur.execute("""
        SELECT
            r.id,
            r.referred_id,
            r.reward_amount,
            r.status,
            r.created_at,
            u.username,
            u.first_name
        FROM referrals r
        LEFT JOIN users u
            ON u.user_id = r.referred_id
        WHERE r.referrer_id = ?
        AND r.status = 'paid'
        ORDER BY r.id DESC
    """, (
        user_id,
    )).fetchall()

    conn.close()

    return rows


def process_referral_reward(referred_user_id):
    """
    Successful referral is paid only once.

    Fixed reward:
        2.00 ETB

    Historical rewards are stored in referrals table.
    """

    conn = db()
    cur = conn.cursor()

    referred = cur.execute("""
        SELECT *
        FROM users
        WHERE user_id = ?
    """, (
        referred_user_id,
    )).fetchone()

    if not referred:
        conn.close()
        return None

    # Already paid
    if referred["referral_paid"] == 1:
        conn.close()
        return None

    referrer_id = referred["referred_by"]

    if not referrer_id:
        cur.execute("""
            UPDATE users
            SET referral_status = 'none'
            WHERE user_id = ?
        """, (
            referred_user_id,
        ))

        conn.commit()
        conn.close()
        return None

    # Self referral
    if int(referrer_id) == int(referred_user_id):
        cur.execute("""
            UPDATE users
            SET referral_status = 'blocked',
                referral_paid = 0
            WHERE user_id = ?
        """, (
            referred_user_id,
        ))

        conn.commit()
        conn.close()
        return None

    # Referrer must exist
    referrer = cur.execute("""
        SELECT *
        FROM users
        WHERE user_id = ?
    """, (
        referrer_id,
    )).fetchone()

    if not referrer:
        conn.close()
        return None

    # Suspicious accounts don't receive referral rewards
    if referred["suspicious"] == 1 or referrer["suspicious"] == 1:
        cur.execute("""
            UPDATE users
            SET referral_status = 'blocked',
                referral_paid = 0
            WHERE user_id = ?
        """, (
            referred_user_id,
        ))

        conn.commit()
        conn.close()
        return None

    # Check if this referred user already exists in referral table
    existing = cur.execute("""
        SELECT id
        FROM referrals
        WHERE referred_id = ?
    """, (
        referred_user_id,
    )).fetchone()

    if existing:
        cur.execute("""
            UPDATE users
            SET referral_paid = 1,
                referral_status = 'paid'
            WHERE user_id = ?
        """, (
            referred_user_id,
        ))

        conn.commit()
        conn.close()
        return None

    # --------------------------------------------------------
    # FIXED REWARD
    # --------------------------------------------------------

    reward = REFERRAL_REWARD

    # Credit inviter
    cur.execute("""
        UPDATE users
        SET balance = balance + ?
        WHERE user_id = ?
    """, (
        float(reward),
        referrer_id,
    ))

    # Record exact amount paid
    cur.execute("""
        INSERT INTO referrals
        (
            referrer_id,
            referred_id,
            reward_amount,
            status,
            created_at
        )
        VALUES (?, ?, ?, 'paid', ?)
    """, (
        referrer_id,
        referred_user_id,
        float(reward),
        datetime.utcnow().isoformat(),
    ))

    # Mark referred user as paid
    cur.execute("""
        UPDATE users
        SET referral_paid = 1,
            referral_status = 'paid'
        WHERE user_id = ?
    """, (
        referred_user_id,
    ))

    conn.commit()
    conn.close()

    return {
        "referrer_id": referrer_id,
        "reward": reward,
    }


# ============================================================
# CHANNEL VERIFICATION
# ============================================================

async def check_channel_membership(bot, user_id, channel_username):
    try:
        member = await bot.get_chat_member(
            chat_id=channel_username,
            user_id=user_id,
        )

        return member.status in (
            ChatMemberStatus.MEMBER,
            ChatMemberStatus.ADMINISTRATOR,
            ChatMemberStatus.OWNER,
        )

    except Exception as e:
        logger.warning(
            "Channel check failed for %s / %s: %s",
            user_id,
            channel_username,
            e,
        )
        return False


async def get_missing_channels(bot, user_id):
    missing = []

    for username, title in REQUIRED_CHANNELS:
        joined = await check_channel_membership(
            bot,
            user_id,
            username,
        )

        if not joined:
            missing.append((username, title))

    return missing


# ============================================================
# KEYBOARDS
# ============================================================

def main_menu():
    keyboard = [
        [
            KeyboardButton("💰 Balance"),
            KeyboardButton("👥 Referral"),
        ],
        [
            KeyboardButton("🎯 Tasks"),
            KeyboardButton("💳 Withdraw"),
        ],
        [
            KeyboardButton("🏦 Wallet"),
            KeyboardButton("🆘 Support"),
        ],
    ]

    return ReplyKeyboardMarkup(
        keyboard,
        resize_keyboard=True,
    )


def back_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "🔙 Back",
                callback_data="back_main",
            )
        ]
    ])


def channel_keyboard(missing_channels):
    buttons = []

    for username, title in missing_channels:
        buttons.append([
            InlineKeyboardButton(
                f"📢 {title}",
                url=f"https://t.me/{username.lstrip('@')}",
            )
        ])

    buttons.append([
        InlineKeyboardButton(
            "✅ Verify",
            callback_data="verify_channels",
        )
    ])

    return InlineKeyboardMarkup(buttons)


# ============================================================
# START
# ============================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user

    username = user.username or ""
    first_name = user.first_name or "User"

    # --------------------------------------------------------
    # Referral parameter
    # --------------------------------------------------------

    referred_by = None

    if context.args:
        try:
            potential_referrer = int(context.args[0])

            if potential_referrer != user.id:
                referred_by = potential_referrer

        except ValueError:
            referred_by = None

    existing = get_user(user.id)

    if not existing:
        create_user(
            user_id=user.id,
            username=username,
            first_name=first_name,
            referred_by=referred_by,
        )

    else:
        update_user_info(
            user.id,
            username,
            first_name,
        )

    # --------------------------------------------------------
    # Channel verification
    # --------------------------------------------------------

    missing = await get_missing_channels(
        context.bot,
        user.id,
    )

    if missing:
        set_joined_all(user.id, False)

        text = (
            "🌍 <b>Welcome to Global Cash Bot!</b>\n\n"
            "💰 Earn ETB by completing available tasks "
            "and inviting new users.\n\n"
            "🇪🇹 መጀመሪያ ከታች ያሉትን Required Channels "
            "በሙሉ Follow/Join ያድርጉ።\n\n"
            "ከዚያ <b>✅ Verify</b> ይጫኑ።"
        )

        await update.message.reply_text(
            text,
            parse_mode="HTML",
            reply_markup=channel_keyboard(missing),
        )

        return

    # --------------------------------------------------------
    # Verified
    # --------------------------------------------------------

    set_joined_all(user.id, True)

    reward_info = process_referral_reward(user.id)

    if reward_info:
        try:
            await context.bot.send_message(
                chat_id=reward_info["referrer_id"],
                text=(
                    "🎉 <b>Successful Referral!</b>\n\n"
                    f"👤 A new user joined through your referral.\n\n"
                    f"💰 Reward Added: "
                    f"<b>{reward_info['reward']:.2f} ETB</b>\n\n"
                    "🇪🇹 ሪፈራልዎ ሙሉ verification ስላጠናቀቀ "
                    "rewardዎ ተጨምሯል።"
                ),
                parse_mode="HTML",
            )
        except Exception as e:
            logger.warning(
                "Could not notify referrer: %s",
                e,
            )

    await update.message.reply_text(
        (
            "✅ <b>Verified Successfully!</b>\n\n"
            f"🌍 Welcome to <b>{BOT_NAME}</b>.\n\n"
            "💰 Start earning by using the menu below."
        ),
        parse_mode="HTML",
        reply_markup=main_menu(),
    )


# ============================================================
# VERIFY
# ============================================================

async def verify_channels(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query

    await query.answer()

    user = update.effective_user

    missing = await get_missing_channels(
        context.bot,
        user.id,
    )

    if missing:
        set_joined_all(user.id, False)

        text = (
            "⚠️ <b>Verification Incomplete</b>\n\n"
            "ከታች ያሉትን channels ገና አልጨረሱም።\n\n"
            "Please join all required channels and try again."
        )

        await query.edit_message_text(
            text,
            parse_mode="HTML",
            reply_markup=channel_keyboard(missing),
        )

        return

    set_joined_all(user.id, True)

    reward_info = process_referral_reward(user.id)

    if reward_info:
        try:
            await context.bot.send_message(
                chat_id=reward_info["referrer_id"],
                text=(
                    "🎉 <b>Successful Referral!</b>\n\n"
                    "Your referred user has completed verification.\n\n"
                    f"💰 Reward: "
                    f"<b>{reward_info['reward']:.2f} ETB</b>\n\n"
                    "The reward has been added to your Balance."
                ),
                parse_mode="HTML",
            )
        except Exception as e:
            logger.warning(
                "Referral notification failed: %s",
                e,
            )

    await query.edit_message_text(
        (
            "✅ <b>Verified Successfully!</b>\n\n"
            "🇪🇹 አሁን ወደ Global Cash Bot መግባት ችለዋል።\n\n"
            "💰 Use the menu below to continue."
        ),
        parse_mode="HTML",
    )

    await context.bot.send_message(
        chat_id=user.id,
        text="🏠 <b>Main Menu</b>\n\nChoose an option:",
        parse_mode="HTML",
        reply_markup=main_menu(),
    )


# ============================================================
# BALANCE
# ============================================================

async def show_balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user

    balance = get_balance(user.id)

    text = (
        "💰 <b>Your Balance</b>\n\n"
        f"💵 Available Balance: <b>{balance:.2f} ETB</b>\n\n"
        f"💳 Minimum Withdrawal: <b>{MIN_WITHDRAW:.2f} ETB</b>\n\n"
        "🇪🇹 ያለዎትን Balance ከ30 ETB በላይ ሲሆን "
        "Withdrawal ማድረግ ይችላሉ።"
    )

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "💳 Withdraw",
                callback_data="open_withdraw",
            )
        ],
        [
            InlineKeyboardButton(
                "🔙 Back",
                callback_data="back_main",
            )
        ],
    ])

    await update.message.reply_text(
        text,
        parse_mode="HTML",
        reply_markup=keyboard,
    )


# ============================================================
# REFERRAL PAGE
# ============================================================

async def show_referral(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user

    count = get_successful_referral_count(user.id)

    # IMPORTANT:
    # This is the fixed reward and is not read from an editable setting.
    reward = REFERRAL_REWARD

    # IMPORTANT:
    # This URL must stay on ONE line.
    referral_link = (
        f"https://t.me/{BOT_USERNAME}?start={user.id}"
    )

    text = (
        "👥 <b>Referral Program</b>\n\n"
        f"🎯 Successful Referrals: <b>{count}</b>\n"
        f"💰 Reward: <b>{reward:.2f} ETB / referral</b>\n\n"
        "🇪🇹 ሰዎችን በ Referral Link ይጋብዙ።\n"
        "የሚጋብዙት ሰው Required Channels በሙሉ "
        "ከጨረሰ እና Verify ካደረገ በኋላ ብቻ "
        "የReferral Reward ይጨመራል።\n\n"
        "🔗 <b>Your Referral Link</b>\n"
        f"<code>{referral_link}</code>"
    )

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "📤 Share Link",
                url=(
                    "https://t.me/share/url?"
                    f"url={referral_link}"
                    "&text=Join%20Global%20Cash%20Bot%20and%20start%20earning!"
                ),
            )
        ],
        [
            InlineKeyboardButton(
                "🔙 Back",
                callback_data="back_main",
            )
        ],
    ])

    await update.message.reply_text(
        text,
        parse_mode="HTML",
        reply_markup=keyboard,
    )


# ============================================================
# TASKS
# ============================================================

async def show_tasks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user

    conn = db()
    cur = conn.cursor()

    tasks = cur.execute("""
        SELECT *
        FROM tasks
        WHERE active = 1
        ORDER BY id DESC
    """).fetchall()

    conn.close()

    buttons = []

    if tasks:
        for task in tasks:
            buttons.append([
                InlineKeyboardButton(
                    f"🎯 {task['title']}",
                    callback_data=f"task_view:{task['id']}",
                )
            ])

    text = (
        "🎯 <b>Tasks</b>\n\n"
        "🇪🇹 ከታች ያሉትን tasks በመጨረስ "
        "additional earning ማግኘት ይችላሉ።\n\n"
    )

    if not tasks:
        text += (
            "📌 <b>No active tasks yet.</b>\n\n"
            "More earning tasks will be added soon."
        )

    buttons.append([
        InlineKeyboardButton(
            "🔙 Back",
            callback_data="back_main",
        )
    ])

    await update.message.reply_text(
        text,
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(buttons),
    )


# ============================================================
# TASK VIEW
# ============================================================

async def task_view(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query

    await query.answer()

    try:
        task_id = int(query.data.split(":")[1])
    except Exception:
        return

    user = update.effective_user

    conn = db()
    cur = conn.cursor()

    task = cur.execute("""
        SELECT *
        FROM tasks
        WHERE id = ?
        AND active = 1
    """, (
        task_id,
    )).fetchone()

    completed = cur.execute("""
        SELECT *
        FROM user_tasks
        WHERE user_id = ?
        AND task_id = ?
    """, (
        user.id,
        task_id,
    )).fetchone()

    conn.close()

    if not task:
        await query.edit_message_text(
            "⚠️ This task is no longer available.",
            reply_markup=back_keyboard(),
        )
        return

    text = (
        f"🎯 <b>{task['title']}</b>\n\n"
        f"{task['description'] or ''}\n\n"
        f"💰 Reward: <b>{task['reward']:.2f} ETB</b>\n\n"
    )

    buttons = []

    if completed and completed["paid"] == 1:
        text += "✅ <b>Completed & Rewarded</b>"
    else:
        text += (
            "1️⃣ Join the required channel.\n"
            "2️⃣ Return and press Verify."
        )

        buttons.append([
            InlineKeyboardButton(
                "📢 Join Channel",
                url=task["channel_url"],
            )
        ])

        buttons.append([
            InlineKeyboardButton(
                "✅ Verify Task",
                callback_data=f"task_verify:{task_id}",
            )
        ])

    buttons.append([
        InlineKeyboardButton(
            "🔙 Back",
            callback_data="back_tasks",
        )
    ])

    await query.edit_message_text(
        text,
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(buttons),
    )


# ============================================================
# TASK VERIFY
# ============================================================

async def task_verify(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query

    await query.answer()

    try:
        task_id = int(query.data.split(":")[1])
    except Exception:
        return

    user = update.effective_user

    conn = db()
    cur = conn.cursor()

    task = cur.execute("""
        SELECT *
        FROM tasks
        WHERE id = ?
        AND active = 1
    """, (
        task_id,
    )).fetchone()

    if not task:
        conn.close()

        await query.edit_message_text(
            "⚠️ Task not found.",
            reply_markup=back_keyboard(),
        )

        return

    existing = cur.execute("""
        SELECT *
        FROM user_tasks
        WHERE user_id = ?
        AND task_id = ?
    """, (
        user.id,
        task_id,
    )).fetchone()

    if existing and existing["paid"] == 1:
        conn.close()

        await query.edit_message_text(
            "✅ <b>Task Already Completed</b>\n\n"
            "The reward was already added to your Balance.",
            parse_mode="HTML",
            reply_markup=back_keyboard(),
        )

        return

    # Check membership
    joined = await check_channel_membership(
        context.bot,
        user.id,
        task["channel_username"],
    )

    if not joined:
        conn.close()

        await query.edit_message_text(
            (
                "⚠️ <b>Verification Failed</b>\n\n"
                "Please join the channel first, then try Verify again."
            ),
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "📢 Join Channel",
                        url=task["channel_url"],
                    )
                ],
                [
                    InlineKeyboardButton(
                        "🔄 Verify Again",
                        callback_data=f"task_verify:{task_id}",
                    )
                ],
                [
                    InlineKeyboardButton(
                        "🔙 Back",
                        callback_data="back_tasks",
                    )
                ],
            ]),
        )

        return

    reward = Decimal(str(task["reward"]))

    if existing:
        cur.execute("""
            UPDATE user_tasks
            SET paid = 1,
                verified_at = ?
            WHERE user_id = ?
            AND task_id = ?
        """, (
            datetime.utcnow().isoformat(),
            user.id,
            task_id,
        ))
    else:
        cur.execute("""
            INSERT INTO user_tasks
            (
                user_id,
                task_id,
                paid,
                verified_at
            )
            VALUES (?, ?, 1, ?)
        """, (
            user.id,
            task_id,
            datetime.utcnow().isoformat(),
        ))

    cur.execute("""
        UPDATE users
        SET balance = balance + ?
        WHERE user_id = ?
    """, (
        float(reward),
        user.id,
    ))

    conn.commit()
    conn.close()

    await query.edit_message_text(
        (
            "🎉 <b>Task Completed!</b>\n\n"
            f"💰 Reward Added: <b>{reward:.2f} ETB</b>\n\n"
            "Your Balance has been updated successfully."
        ),
        parse_mode="HTML",
        reply_markup=back_keyboard(),
    )


# ============================================================
# WALLET
# ============================================================

async def show_wallet(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user

    row = get_user(user.id)

    if not row or not row["wallet_type"]:
        text = (
            "🏦 <b>Wallet</b>\n\n"
            "No withdrawal wallet has been added yet.\n\n"
            "🇪🇹 ከሁለቱ አንዱን ይምረጡ፦\n"
            "• CBE\n"
            "• Telebirr\n\n"
            "⚠️ One account can have only one active wallet."
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
                    callback_data="back_main",
                )
            ],
        ])

        await update.message.reply_text(
            text,
            parse_mode="HTML",
            reply_markup=keyboard,
        )

        return

    wallet_type = row["wallet_type"]
    wallet_number = row["wallet_number"]

    text = (
        "🏦 <b>Your Wallet</b>\n\n"
        f"Type: <b>{wallet_type}</b>\n"
        f"Account: <code>{wallet_number}</code>\n\n"
        "🔐 This wallet is locked to your account.\n"
        "🇪🇹 ሌላ wallet በቀጥታ መጨመር አይቻልም።\n\n"
        "If you need to change it, contact Admin/Support."
    )

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "💬 Contact Support",
                url=f"https://t.me/{SUPPORT_USERNAME}",
            )
        ],
        [
            InlineKeyboardButton(
                "🔙 Back",
                callback_data="back_main",
            )
        ],
    ])

    await update.message.reply_text(
        text,
        parse_mode="HTML",
        reply_markup=keyboard,
    )


# ============================================================
# WALLET SET
# ============================================================

async def wallet_cbe(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query

    await query.answer()

    user = update.effective_user
    row = get_user(user.id)

    if row and row["wallet_type"]:
        await query.edit_message_text(
            (
                "⚠️ <b>Wallet Already Set</b>\n\n"
                f"Current Wallet: <b>{row['wallet_type']}</b>\n"
                f"Account: <code>{row['wallet_number']}</code>\n\n"
                "You cannot add a second wallet directly."
            ),
            parse_mode="HTML",
            reply_markup=back_keyboard(),
        )
        return

    context.user_data["wallet_state"] = "cbe"

    await query.edit_message_text(
        (
            "🏦 <b>CBE Wallet</b>\n\n"
            "Please send your CBE account number.\n\n"
            "Format:\n"
            "• Exactly 13 digits\n"
            "• Must start with <b>1000</b>\n\n"
            "Example: <code>1000XXXXXXXXX</code>\n\n"
            "❌ Do not send password, PIN or OTP."
        ),
        parse_mode="HTML",
        reply_markup=back_keyboard(),
    )


async def wallet_telebirr(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query

    await query.answer()

    user = update.effective_user
    row = get_user(user.id)

    if row and row["wallet_type"]:
        await query.edit_message_text(
            (
                "⚠️ <b>Wallet Already Set</b>\n\n"
                f"Current Wallet: <b>{row['wallet_type']}</b>\n"
                f"Account: <code>{row['wallet_number']}</code>\n\n"
                "You cannot add a second wallet directly."
            ),
            parse_mode="HTML",
            reply_markup=back_keyboard(),
        )
        return

    context.user_data["wallet_state"] = "telebirr"

    await query.edit_message_text(
        (
            "📱 <b>Telebirr Wallet</b>\n\n"
            "Please send your Telebirr phone number.\n\n"
            "Format:\n"
            "• Exactly 10 digits\n"
            "• Must start with <b>09</b> or <b>07</b>\n\n"
            "Example: <code>09XXXXXXXX</code>\n\n"
            "❌ Do not send PIN or OTP."
        ),
        parse_mode="HTML",
        reply_markup=back_keyboard(),
    )


# ============================================================
# SAVE WALLET
# ============================================================

def wallet_exists_for_other_user(wallet_type, wallet_number, user_id):
    conn = db()
    cur = conn.cursor()

    row = cur.execute("""
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
    conn = db()
    cur = conn.cursor()

    try:
        cur.execute("""
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

    except sqlite3.IntegrityError:
        conn.rollback()
        conn.close()
        return False

    conn.close()

    return True


# ============================================================
# WITHDRAW
# ============================================================

async def show_withdraw(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user

    if is_suspicious(user.id):
        text = (
            "🚨 <b>Security Review</b>\n\n"
            "Your account is currently under security review.\n\n"
            "Withdrawal is temporarily unavailable.\n\n"
            "🇪🇹 ለተጨማሪ መረጃ Support ያነጋግሩ።"
        )

        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "💬 Contact Support",
                    url=f"https://t.me/{SUPPORT_USERNAME}",
                )
            ],
            [
                InlineKeyboardButton(
                    "🔙 Back",
                    callback_data="back_main",
                )
            ],
        ])

        await update.message.reply_text(
            text,
            parse_mode="HTML",
            reply_markup=keyboard,
        )

        return

    balance = get_balance(user.id)

    if balance < MIN_WITHDRAW:
        text = (
            "💳 <b>Withdrawal</b>\n\n"
            f"💰 Your Balance: <b>{balance:.2f} ETB</b>\n"
            f"Minimum Withdrawal: <b>{MIN_WITHDRAW:.2f} ETB</b>\n\n"
            "🇪🇹 30 ETB እስኪደርስ Balance ይሰብስቡ።"
        )

        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "👥 Referral",
                    callback_data="open_referral",
                )
            ],
            [
                InlineKeyboardButton(
                    "🔙 Back",
                    callback_data="back_main",
                )
            ],
        ])

        await update.message.reply_text(
            text,
            parse_mode="HTML",
            reply_markup=keyboard,
        )

        return

    row = get_user(user.id)

    if not row or not row["wallet_type"]:
        text = (
            "🏦 <b>Wallet Required</b>\n\n"
            "Please set your CBE or Telebirr wallet before requesting a withdrawal."
        )

        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "🏦 Set Wallet",
                    callback_data="open_wallet",
                )
            ],
            [
                InlineKeyboardButton(
                    "🔙 Back",
                    callback_data="back_main",
                )
            ],
        ])

        await update.message.reply_text(
            text,
            parse_mode="HTML",
            reply_markup=keyboard,
        )

        return

    context.user_data["withdraw_state"] = True

    await update.message.reply_text(
        (
            "💳 <b>New Withdrawal</b>\n\n"
            f"💰 Available: <b>{balance:.2f} ETB</b>\n"
            f"📉 Minimum: <b>{MIN_WITHDRAW:.2f} ETB</b>\n\n"
            f"🏦 Wallet: <b>{row['wallet_type']}</b>\n"
            f"🔢 Account: <code>{row['wallet_number']}</code>\n\n"
            "Enter the amount you want to withdraw.\n"
            "Example: <code>30</code>"
        ),
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


# ============================================================
# PROCESS WITHDRAW AMOUNT
# ============================================================

async def process_withdraw_amount(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    user = update.effective_user

    if not context.user_data.get("withdraw_state"):
        return

    raw = update.message.text.strip()

    try:
        amount = Decimal(raw).quantize(Decimal("0.01"))
    except InvalidOperation:
        await update.message.reply_text(
            "❌ Invalid amount.\n\nPlease enter a valid number.",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "❌ Cancel",
                        callback_data="cancel_withdraw",
                    )
                ]
            ]),
        )
        return

    if amount < MIN_WITHDRAW:
        await update.message.reply_text(
            f"❌ Minimum withdrawal is {MIN_WITHDRAW:.2f} ETB.",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "❌ Cancel",
                        callback_data="cancel_withdraw",
                    )
                ]
            ]),
        )
        return

    balance = get_balance(user.id)

    if amount > balance:
        await update.message.reply_text(
            (
                "❌ <b>Insufficient Balance</b>\n\n"
                f"Available: <b>{balance:.2f} ETB</b>"
            ),
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
        return

    if is_suspicious(user.id):
        context.user_data.pop("withdraw_state", None)

        await update.message.reply_text(
            "🚨 Your account is under Security Review.",
            reply_markup=main_menu(),
        )

        return

    row = get_user(user.id)

    if not row or not row["wallet_type"]:
        context.user_data.pop("withdraw_state", None)

        await update.message.reply_text(
            "🏦 Please set your Wallet first.",
            reply_markup=main_menu(),
        )

        return

    # --------------------------------------------------------
    # Atomic balance deduction
    # --------------------------------------------------------

    conn = db()
    cur = conn.cursor()

    cur.execute("""
        UPDATE users
        SET balance = balance - ?
        WHERE user_id = ?
        AND balance >= ?
    """, (
        float(amount),
        user.id,
        float(amount),
    ))

    if cur.rowcount != 1:
        conn.rollback()
        conn.close()

        await update.message.reply_text(
            "❌ Withdrawal could not be processed. Please try again.",
            reply_markup=main_menu(),
        )

        return

    cur.execute("""
        INSERT INTO withdrawals
        (
            user_id,
            amount,
            wallet_type,
            wallet_number,
            status,
            created_at
        )
        VALUES (?, ?, ?, ?, 'pending', ?)
    """, (
        user.id,
        float(amount),
        row["wallet_type"],
        row["wallet_number"],
        datetime.utcnow().isoformat(),
    ))

    withdrawal_id = cur.lastrowid

    conn.commit()
    conn.close()

    context.user_data.pop("withdraw_state", None)

    # --------------------------------------------------------
    # USER CONFIRMATION
    # --------------------------------------------------------

    await update.message.reply_text(
        (
            "✅ <b>Withdrawal Request Submitted</b>\n\n"
            f"🆔 Request ID: <code>#{withdrawal_id}</code>\n"
            f"💰 Amount: <b>{amount:.2f} ETB</b>\n"
            f"🏦 Method: <b>{row['wallet_type']}</b>\n"
            f"🔢 Account: <code>{row['wallet_number']}</code>\n\n"
            "⏳ Status: <b>Pending Review</b>\n\n"
            "🇪🇹 Admin ካረጋገጠ በኋላ ክፍያው ይፈጸማል።"
        ),
        parse_mode="HTML",
        reply_markup=main_menu(),
    )

    # --------------------------------------------------------
    # ADMIN NOTIFICATION
    # --------------------------------------------------------

    try:
        admin_keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "👥 View Referrals",
                    callback_data=f"admin_referrals:{user.id}",
                )
            ],
            [
                InlineKeyboardButton(
                    "✅ Approve",
                    callback_data=f"approve_withdraw:{withdrawal_id}",
                ),
                InlineKeyboardButton(
                    "❌ Reject",
                    callback_data=f"reject_withdraw:{withdrawal_id}",
                ),
            ],
        ])

        await context.bot.send_message(
            chat_id=ADMIN_ID,
            text=(
                "💳 <b>New Withdrawal</b>\n\n"
                f"🆔 Request: <code>#{withdrawal_id}</code>\n"
                f"👤 User ID: <code>{user.id}</code>\n"
                f"👤 Username: @{user.username if user.username else 'NoUsername'}\n\n"
                f"💰 Amount: <b>{amount:.2f} ETB</b>\n"
                f"🏦 Method: <b>{row['wallet_type']}</b>\n"
                f"🔢 Wallet: <code>{row['wallet_number']}</code>\n\n"
                f"🎯 Successful Referrals: "
                f"<b>{get_successful_referral_count(user.id)}</b>\n\n"
                "⏳ Status: <b>Pending</b>"
            ),
            parse_mode="HTML",
            reply_markup=admin_keyboard,
        )

    except Exception as e:
        logger.warning(
            "Admin notification failed: %s",
            e,
        )


# ============================================================
# SUPPORT
# ============================================================

async def show_support(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "🆘 <b>Global Cash Support</b>\n\n"
        "📢 <b>Advertising & Telegram Promotion</b>\n\n"
        "የTelegram Channel ወይም Group ማስታወቂያ ከፈለጉ፦\n\n"
        "📌 <b>Channel Promotion</b>\n"
        "የTelegram Channel ማስታወቂያ እና Promotion ከፈለጉ ያነጋግሩን።\n\n"
        "📈 <b>Channel Growth</b>\n"
        "Channel Members እና Reach ማሳደግ ከፈለጉ ያነጋግሩን።\n\n"
        "📣 <b>Group Advertising</b>\n"
        "Telegram Group ወይም Channel ማስተዋወቅ ከፈለጉ ያነጋግሩን።\n\n"
        "💼 <b>Business Promotion</b>\n"
        "Business, Product ወይም Service በTelegram ላይ ለማስተዋወቅ ያነጋግሩን።\n\n"
        "📱 <b>App & Website Promotion</b>\n"
        "App, Website ወይም Online Service ለPromotion ካስፈለገዎት ያነጋግሩን።\n\n"
        "🎯 <b>Custom Promotion</b>\n"
        "ሌላ የPromotion አገልግሎት ከፈለጉም ምን እንደሚፈልጉ ይንገሩን።\n\n"
        f"👤 <b>Support:</b> @{SUPPORT_USERNAME}\n\n"
        "💰 ለዋጋ እና ለተጨማሪ መረጃ "
        "ከታች ያለውን <b>Contact Support</b> ይጫኑ።"
    )

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "💬 Contact Support",
                url=f"https://t.me/{SUPPORT_USERNAME}",
            )
        ],
        [
            InlineKeyboardButton(
                "🔙 Back",
                callback_data="back_main",
            )
        ],
    ])

    await update.message.reply_text(
        text,
        parse_mode="HTML",
        reply_markup=keyboard,
    )


# ============================================================
# ADMIN CHECK
# ============================================================

def is_admin(user_id):
    return int(user_id) == int(ADMIN_ID)


# ============================================================
# ADMIN PANEL
# ============================================================

async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user

    if not is_admin(user.id):
        await update.message.reply_text(
            "⛔ Access denied."
        )
        return

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "📊 Statistics",
                callback_data="admin_stats",
            )
        ],
        [
            InlineKeyboardButton(
                "💳 Pending Withdrawals",
                callback_data="admin_pending",
            )
        ],
        [
            InlineKeyboardButton(
                "👥 Successful Referrals",
                callback_data="admin_referral_search",
            )
        ],
        [
            InlineKeyboardButton(
                "🚨 Suspicious Accounts",
                callback_data="admin_suspicious",
            )
        ],
        [
            InlineKeyboardButton(
                "📢 Tasks / Promotions",
                callback_data="admin_tasks",
            )
        ],
        [
            InlineKeyboardButton(
                "🔙 Close",
                callback_data="admin_close",
            )
        ],
    ])

    await update.message.reply_text(
        "👑 <b>Global Cash Admin Panel</b>\n\nSelect an option:",
        parse_mode="HTML",
        reply_markup=keyboard,
    )


# ============================================================
# ADMIN STATISTICS
# ============================================================

async def admin_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query

    await query.answer()

    if not is_admin(update.effective_user.id):
        return

    conn = db()
    cur = conn.cursor()

    total_users = cur.execute(
        "SELECT COUNT(*) FROM users"
    ).fetchone()[0]

    verified_users = cur.execute(
        "SELECT COUNT(*) FROM users WHERE joined_all = 1"
    ).fetchone()[0]

    total_balance = cur.execute(
        "SELECT COALESCE(SUM(balance), 0) FROM users"
    ).fetchone()[0]

    successful_referrals = cur.execute(
        "SELECT COUNT(*) FROM referrals WHERE status = 'paid'"
    ).fetchone()[0]

    pending_withdrawals = cur.execute(
        "SELECT COUNT(*) FROM withdrawals WHERE status = 'pending'"
    ).fetchone()[0]

    suspicious_accounts = cur.execute(
        "SELECT COUNT(*) FROM users WHERE suspicious = 1"
    ).fetchone()[0]

    conn.close()

    text = (
        "📊 <b>Bot Statistics</b>\n\n"
        f"👥 Total Users: <b>{total_users}</b>\n"
        f"✅ Verified Users: <b>{verified_users}</b>\n"
        f"💰 Total Balance: <b>{total_balance:.2f} ETB</b>\n"
        f"🎯 Successful Referrals: <b>{successful_referrals}</b>\n"
        f"💳 Pending Withdrawals: <b>{pending_withdrawals}</b>\n"
        f"🚨 Suspicious Accounts: <b>{suspicious_accounts}</b>\n\n"
        f"💵 Fixed Referral Reward: <b>{REFERRAL_REWARD:.2f} ETB</b>\n"
        f"📉 Minimum Withdrawal: <b>{MIN_WITHDRAW:.2f} ETB</b>"
    )

    await query.edit_message_text(
        text,
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


# ============================================================
# ADMIN PENDING WITHDRAWALS
# ============================================================

async def admin_pending(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query

    await query.answer()

    if not is_admin(update.effective_user.id):
        return

    conn = db()
    cur = conn.cursor()

    rows = cur.execute("""
        SELECT *
        FROM withdrawals
        WHERE status = 'pending'
        ORDER BY id ASC
        LIMIT 20
    """).fetchall()

    conn.close()

    if not rows:
        await query.edit_message_text(
            "💳 <b>Pending Withdrawals</b>\n\n"
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

    for row in rows:
        buttons.append([
            InlineKeyboardButton(
                f"#{row['id']} • {row['amount']:.2f} ETB",
                callback_data=f"admin_withdraw:{row['id']}",
            )
        ])

    buttons.append([
        InlineKeyboardButton(
            "🔙 Admin Panel",
            callback_data="admin_panel",
        )
    ])

    await query.edit_message_text(
        "💳 <b>Pending Withdrawals</b>\n\n"
        "Select a request:",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(buttons),
    )


# ============================================================
# ADMIN WITHDRAW DETAIL
# ============================================================

async def admin_withdraw_detail(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.callback_query

    await query.answer()

    if not is_admin(update.effective_user.id):
        return

    try:
        withdrawal_id = int(query.data.split(":")[1])
    except Exception:
        return

    conn = db()
    cur = conn.cursor()

    row = cur.execute("""
        SELECT
            w.*,
            u.username,
            u.first_name
        FROM withdrawals w
        LEFT JOIN users u
            ON u.user_id = w.user_id
        WHERE w.id = ?
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

    status = row["status"]

    text = (
        "💳 <b>Withdrawal Details</b>\n\n"
        f"🆔 Request: <code>#{row['id']}</code>\n"
        f"👤 User ID: <code>{row['user_id']}</code>\n"
        f"👤 Username: @{row['username'] or 'NoUsername'}\n"
        f"👤 Name: {row['first_name'] or 'Unknown'}\n\n"
        f"💰 Amount: <b>{row['amount']:.2f} ETB</b>\n"
        f"🏦 Method: <b>{row['wallet_type']}</b>\n"
        f"🔢 Wallet: <code>{row['wallet_number']}</code>\n\n"
        f"🎯 Successful Referrals: "
        f"<b>{get_successful_referral_count(row['user_id'])}</b>\n\n"
        f"📌 Status: <b>{status.upper()}</b>"
    )

    buttons = [
        [
            InlineKeyboardButton(
                "👥 View Successful Referrals",
                callback_data=f"admin_referrals:{row['user_id']}",
            )
        ]
    ]

    if status == "pending":
        buttons.append([
            InlineKeyboardButton(
                "✅ Approve",
                callback_data=f"approve_withdraw:{row['id']}",
            ),
            InlineKeyboardButton(
                "❌ Reject",
                callback_data=f"reject_withdraw:{row['id']}",
            ),
        ])

    buttons.append([
        InlineKeyboardButton(
            "🔙 Pending Withdrawals",
            callback_data="admin_pending",
        )
    ])

    await query.edit_message_text(
        text,
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(buttons),
    )


# ============================================================
# ADMIN APPROVE
# ============================================================

async def approve_withdraw(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.callback_query

    await query.answer()

    if not is_admin(update.effective_user.id):
        return

    try:
        withdrawal_id = int(query.data.split(":")[1])
    except Exception:
        return

    conn = db()
    cur = conn.cursor()

    row = cur.execute("""
        SELECT *
        FROM withdrawals
        WHERE id = ?
        AND status = 'pending'
    """, (
        withdrawal_id,
    )).fetchone()

    if not row:
        conn.close()

        await query.answer(
            "Already processed or not found.",
            show_alert=True,
        )

        return

    cur.execute("""
        UPDATE withdrawals
        SET status = 'approved',
            processed_at = ?
        WHERE id = ?
        AND status = 'pending'
    """, (
        datetime.utcnow().isoformat(),
        withdrawal_id,
    ))

    conn.commit()
    conn.close()

    try:
        await context.bot.send_message(
            chat_id=row["user_id"],
            text=(
                "✅ <b>Withdrawal Approved</b>\n\n"
                f"🆔 Request: <code>#{withdrawal_id}</code>\n"
                f"💰 Amount: <b>{row['amount']:.2f} ETB</b>\n"
                f"🏦 Method: <b>{row['wallet_type']}</b>\n\n"
                "Your withdrawal has been approved by Admin."
            ),
            parse_mode="HTML",
        )
    except Exception as e:
        logger.warning(
            "Could not notify user after approval: %s",
            e,
        )

    await query.edit_message_text(
        (
            "✅ <b>Withdrawal Approved</b>\n\n"
            f"Request: <code>#{withdrawal_id}</code>\n"
            f"Amount: <b>{row['amount']:.2f} ETB</b>"
        ),
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "🔙 Pending Withdrawals",
                    callback_data="admin_pending",
                )
            ]
        ]),
    )


# ============================================================
# ADMIN REJECT
# ============================================================

async def reject_withdraw(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.callback_query

    await query.answer()

    if not is_admin(update.effective_user.id):
        return

    try:
        withdrawal_id = int(query.data.split(":")[1])
    except Exception:
        return

    conn = db()
    cur = conn.cursor()

    row = cur.execute("""
        SELECT *
        FROM withdrawals
        WHERE id = ?
        AND status = 'pending'
    """, (
        withdrawal_id,
    )).fetchone()

    if not row:
        conn.close()

        await query.answer(
            "Already processed or not found.",
            show_alert=True,
        )

        return

    # Mark rejected
    cur.execute("""
        UPDATE withdrawals
        SET status = 'rejected',
            processed_at = ?
        WHERE id = ?
        AND status = 'pending'
    """, (
        datetime.utcnow().isoformat(),
        withdrawal_id,
    ))

    # Return the deducted money
    cur.execute("""
        UPDATE users
        SET balance = balance + ?
        WHERE user_id = ?
    """, (
        row["amount"],
        row["user_id"],
    ))

    conn.commit()
    conn.close()

    try:
        await context.bot.send_message(
            chat_id=row["user_id"],
            text=(
                "❌ <b>Withdrawal Rejected</b>\n\n"
                f"🆔 Request: <code>#{withdrawal_id}</code>\n"
                f"💰 Amount Returned: <b>{row['amount']:.2f} ETB</b>\n\n"
                "The amount has been returned to your Balance.\n\n"
                "🇪🇹 ለተጨማሪ መረጃ Support ያነጋግሩ።"
            ),
            parse_mode="HTML",
        )
    except Exception as e:
        logger.warning(
            "Could not notify user after rejection: %s",
            e,
        )

    await query.edit_message_text(
        (
            "❌ <b>Withdrawal Rejected</b>\n\n"
            f"Request: <code>#{withdrawal_id}</code>\n"
            f"Returned: <b>{row['amount']:.2f} ETB</b>"
        ),
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "🔙 Pending Withdrawals",
                    callback_data="admin_pending",
                )
            ]
        ]),
    )


# ============================================================
# ADMIN REFERRALS SEARCH
# ============================================================

async def admin_referral_search(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.callback_query

    await query.answer()

    if not is_admin(update.effective_user.id):
        return

    context.user_data["admin_referral_search"] = True

    await query.edit_message_text(
        (
            "👥 <b>Successful Referrals</b>\n\n"
            "Send the User ID of the referrer.\n\n"
            "Example:\n"
            "<code>123456789</code>\n\n"
            "Only successful/paid referrals will be displayed."
        ),
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


# ============================================================
# ADMIN SHOW REFERRALS
# ============================================================

async def show_admin_referrals(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    user_id: int,
):
    if not is_admin(update.effective_user.id):
        return

    user = get_user(user_id)

    if not user:
        await update.message.reply_text(
            "❌ User not found."
        )
        return

    referrals = get_successful_referrals(user_id)

    username = user["username"] or "NoUsername"

    text = (
        "👥 <b>Successful Referral Details</b>\n\n"
        f"👤 Referrer: @{username}\n"
        f"🆔 User ID: <code>{user_id}</code>\n"
        f"🎯 Successful Referrals: <b>{len(referrals)}</b>\n\n"
    )

    if not referrals:
        text += "No successful referrals yet."

    else:
        for index, ref in enumerate(referrals, start=1):
            referred_username = ref["username"]

            if referred_username:
                referred_name = f"@{referred_username}"
            else:
                referred_name = (
                    f"ID {ref['referred_id']}"
                )

            text += (
                f"<b>{index}.</b> {referred_name}\n"
                f"   🆔 <code>{ref['referred_id']}</code>\n"
                f"   💰 Reward: "
                f"<b>{ref['reward_amount']:.2f} ETB</b>\n"
                f"   📅 {ref['created_at'][:19]}\n\n"
            )

            # Telegram message limit protection
            if len(text) > 3500:
                text += "\n...more referrals are stored in the database."
                break

    await update.message.reply_text(
        text,
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


# ============================================================
# ADMIN REFERRALS FROM BUTTON
# ============================================================

async def admin_referrals_button(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.callback_query

    await query.answer()

    if not is_admin(update.effective_user.id):
        return

    try:
        user_id = int(query.data.split(":")[1])
    except Exception:
        return

    user = get_user(user_id)

    if not user:
        await query.edit_message_text(
            "❌ User not found.",
            reply_markup=back_keyboard(),
        )
        return

    referrals = get_successful_referrals(user_id)

    username = user["username"] or "NoUsername"

    text = (
        "👥 <b>Successful Referrals</b>\n\n"
        f"👤 Referrer: @{username}\n"
        f"🆔 User ID: <code>{user_id}</code>\n"
        f"🎯 Count: <b>{len(referrals)}</b>\n\n"
    )

    if not referrals:
        text += "No successful referrals."

    else:
        for index, ref in enumerate(referrals, start=1):
            referred_name = (
                f"@{ref['username']}"
                if ref["username"]
                else f"ID {ref['referred_id']}"
            )

            text += (
                f"<b>{index}.</b> {referred_name}\n"
                f"🆔 <code>{ref['referred_id']}</code>\n"
                f"💰 {ref['reward_amount']:.2f} ETB\n\n"
            )

            if len(text) > 3500:
                text += "...more stored in database."
                break

    await query.edit_message_text(
        text,
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "🔙 Withdrawal",
                    callback_data="admin_pending",
                )
            ]
        ]),
    )


# ============================================================
# ADMIN SUSPICIOUS
# ============================================================

async def admin_suspicious(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.callback_query

    await query.answer()

    if not is_admin(update.effective_user.id):
        return

    conn = db()
    cur = conn.cursor()

    rows = cur.execute("""
        SELECT user_id, username, first_name
        FROM users
        WHERE suspicious = 1
        ORDER BY user_id DESC
        LIMIT 30
    """).fetchall()

    conn.close()

    text = "🚨 <b>Suspicious Accounts</b>\n\n"

    if not rows:
        text += "✅ No suspicious accounts."

    else:
        for index, row in enumerate(rows, start=1):
            username = (
                f"@{row['username']}"
                if row["username"]
                else "NoUsername"
            )

            text += (
                f"{index}. {username}\n"
                f"🆔 <code>{row['user_id']}</code>\n\n"
            )

    await query.edit_message_text(
        text,
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


# ============================================================
# ADMIN TASKS
# ============================================================

async def admin_tasks(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.callback_query

    await query.answer()

    if not is_admin(update.effective_user.id):
        return

    conn = db()
    cur = conn.cursor()

    tasks = cur.execute("""
        SELECT *
        FROM tasks
        ORDER BY id DESC
        LIMIT 20
    """).fetchall()

    conn.close()

    text = (
        "📢 <b>Tasks / Promotions</b>\n\n"
        "Dynamic task system is available.\n\n"
    )

    if tasks:
        for task in tasks:
            status = "🟢 Active" if task["active"] else "🔴 Off"

            text += (
                f"#{task['id']} <b>{task['title']}</b>\n"
                f"💰 {task['reward']:.2f} ETB\n"
                f"{status}\n\n"
            )
    else:
        text += "No tasks added yet.\n\n"

    text += (
        "To add a new promotion/task, use the admin task creation flow."
    )

    await query.edit_message_text(
        text,
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "➕ Add Task",
                    callback_data="admin_add_task",
                )
            ],
            [
                InlineKeyboardButton(
                    "🔙 Admin Panel",
                    callback_data="admin_panel",
                )
            ],
        ]),
    )


# ============================================================
# ADMIN ADD TASK
# ============================================================

async def admin_add_task(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.callback_query

    await query.answer()

    if not is_admin(update.effective_user.id):
        return

    context.user_data["admin_task_state"] = "title"

    await query.edit_message_text(
        (
            "➕ <b>Add New Task</b>\n\n"
            "Step 1/4\n\n"
            "Send the Task Title.\n\n"
            "Example:\n"
            "<code>Join Payment Proof Channel</code>"
        ),
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


# ============================================================
# ADMIN TEXT INPUT
# ============================================================

async def handle_admin_task_input(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    user = update.effective_user

    if not is_admin(user.id):
        return

    state = context.user_data.get("admin_task_state")

    if not state:
        return

    text = update.message.text.strip()

    if state == "title":
        context.user_data["new_task_title"] = text
        context.user_data["admin_task_state"] = "description"

        await update.message.reply_text(
            (
                "Step 2/4\n\n"
                "Send a short Task Description.\n\n"
                "Example:\n"
                "Join the channel and verify your membership."
            )
        )

        return

    if state == "description":
        context.user_data["new_task_description"] = text
        context.user_data["admin_task_state"] = "username"

        await update.message.reply_text(
            (
                "Step 3/4\n\n"
                "Send the Channel Username.\n\n"
                "Example:\n"
                "<code>@ExampleChannel</code>"
            ),
            parse_mode="HTML",
        )

        return

    if state == "username":
        channel_username = text

        if not channel_username.startswith("@"):
            channel_username = "@" + channel_username

        context.user_data["new_task_channel"] = channel_username
        context.user_data["admin_task_state"] = "reward"

        await update.message.reply_text(
            (
                "Step 4/4\n\n"
                "Send the Task Reward in ETB.\n\n"
                "Example:\n"
                "<code>1.50</code>"
            ),
            parse_mode="HTML",
        )

        return

    if state == "reward":
        try:
            reward = Decimal(text).quantize(
                Decimal("0.01")
            )

            if reward < 0:
                raise InvalidOperation

        except InvalidOperation:
            await update.message.reply_text(
                "❌ Invalid reward. Please send a valid number."
            )
            return

        title = context.user_data.get("new_task_title")
        description = context.user_data.get("new_task_description")
        channel_username = context.user_data.get("new_task_channel")

        channel_url = (
            f"https://t.me/{channel_username.lstrip('@')}"
        )

        conn = db()
        cur = conn.cursor()

        cur.execute("""
            INSERT INTO tasks
            (
                title,
                description,
                channel_username,
                channel_url,
                reward,
                active,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, 1, ?)
        """, (
            title,
            description,
            channel_username,
            channel_url,
            float(reward),
            datetime.utcnow().isoformat(),
        ))

        conn.commit()
        conn.close()

        context.user_data.pop("admin_task_state", None)
        context.user_data.pop("new_task_title", None)
        context.user_data.pop("new_task_description", None)
        context.user_data.pop("new_task_channel", None)

        await update.message.reply_text(
            (
                "✅ <b>Task Created Successfully</b>\n\n"
                f"🎯 Title: <b>{title}</b>\n"
                f"📢 Channel: <b>{channel_username}</b>\n"
                f"💰 Reward: <b>{reward:.2f} ETB</b>\n\n"
                "The task is now active."
            ),
            parse_mode="HTML",
        )

        return


# ============================================================
# CALLBACK HANDLER
# ============================================================

async def callbacks(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.callback_query

    data = query.data

    if data == "verify_channels":
        await verify_channels(update, context)
        return

    if data == "back_main":
        await query.answer()

        await query.edit_message_text(
            "🏠 <b>Main Menu</b>\n\nChoose an option below:",
            parse_mode="HTML",
        )

        await context.bot.send_message(
            chat_id=update.effective_user.id,
            text="🏠 Main Menu",
            reply_markup=main_menu(),
        )

        return

    if data == "open_withdraw":
        await query.answer()

        await query.edit_message_text(
            (
                "💳 <b>Withdrawal</b>\n\n"
                "Please use the Withdraw button from the Main Menu."
            ),
            parse_mode="HTML",
            reply_markup=back_keyboard(),
        )

        return

    if data == "open_referral":
        await query.answer()

        user = update.effective_user
        count = get_successful_referral_count(user.id)

        referral_link = (
            f"https://t.me/{BOT_USERNAME}?start={user.id}"
        )

        await query.edit_message_text(
            (
                "👥 <b>Referral Program</b>\n\n"
                f"🎯 Successful Referrals: <b>{count}</b>\n"
                f"💰 Reward: <b>{REFERRAL_REWARD:.2f} ETB</b>\n\n"
                f"🔗 <code>{referral_link}</code>"
            ),
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "📤 Share Link",
                        url=(
                            "https://t.me/share/url?"
                            f"url={referral_link}"
                        ),
                    )
                ],
                [
                    InlineKeyboardButton(
                        "🔙 Back",
                        callback_data="back_main",
                    )
                ],
            ]),
        )

        return

    if data == "open_wallet":
        await query.answer()

        await query.edit_message_text(
            (
                "🏦 <b>Choose Wallet</b>\n\n"
                "Select one:"
            ),
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
                        callback_data="back_main",
                    )
                ],
            ]),
        )

        return

    if data == "wallet_cbe":
        await wallet_cbe(update, context)
        return

    if data == "wallet_telebirr":
        await wallet_telebirr(update, context)
        return

    if data == "cancel_withdraw":
        await query.answer()

        context.user_data.pop("withdraw_state", None)

        await query.edit_message_text(
            "❌ <b>Withdrawal Cancelled</b>",
            parse_mode="HTML",
            reply_markup=back_keyboard(),
        )

        return

    if data == "back_tasks":
        await query.answer()

        await query.edit_message_text(
            "🎯 <b>Tasks</b>\n\nChoose a task:",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "🔙 Back",
                        callback_data="back_main",
                    )
                ]
            ]),
        )

        return

    if data.startswith("task_view:"):
        await task_view(update, context)
        return

    if data.startswith("task_verify:"):
        await task_verify(update, context)
        return

    # ========================================================
    # ADMIN CALLBACKS
    # ========================================================

    if data == "admin_panel":
        await query.answer()

        if not is_admin(update.effective_user.id):
            return

        await query.edit_message_text(
            "👑 <b>Admin Panel</b>\n\nSelect an option:",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "📊 Statistics",
                        callback_data="admin_stats",
                    )
                ],
                [
                    InlineKeyboardButton(
                        "💳 Pending Withdrawals",
                        callback_data="admin_pending",
                    )
                ],
                [
                    InlineKeyboardButton(
                        "👥 Successful Referrals",
                        callback_data="admin_referral_search",
                    )
                ],
                [
                    InlineKeyboardButton(
                        "🚨 Suspicious Accounts",
                        callback_data="admin_suspicious",
                    )
                ],
                [
                    InlineKeyboardButton(
                        "📢 Tasks / Promotions",
                        callback_data="admin_tasks",
                    )
                ],
            ]),
        )

        return

    if data == "admin_stats":
        await admin_stats(update, context)
        return

    if data == "admin_pending":
        await admin_pending(update, context)
        return

    if data.startswith("admin_withdraw:"):
        await admin_withdraw_detail(update, context)
        return

    if data.startswith("approve_withdraw:"):
        await approve_withdraw(update, context)
        return

    if data.startswith("reject_withdraw:"):
        await reject_withdraw(update, context)
        return

    if data == "admin_referral_search":
        await admin_referral_search(update, context)
        return

    if data.startswith("admin_referrals:"):
        await admin_referrals_button(update, context)
        return

    if data == "admin_suspicious":
        await admin_suspicious(update, context)
        return

    if data == "admin_tasks":
        await admin_tasks(update, context)
        return

    if data == "admin_add_task":
        await admin_add_task(update, context)
        return

    if data == "admin_close":
        await query.answer()
        await query.edit_message_text(
            "👑 Admin Panel closed."
        )
        return


# ============================================================
# TEXT HANDLER
# ============================================================

async def handle_text(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    user = update.effective_user

    # --------------------------------------------------------
    # Admin task creation
    # --------------------------------------------------------

    if is_admin(user.id):
        if context.user_data.get("admin_task_state"):
            await handle_admin_task_input(
                update,
                context,
            )
            return

        if context.user_data.get("admin_referral_search"):
            raw = update.message.text.strip()

            try:
                target_id = int(raw)
            except ValueError:
                await update.message.reply_text(
                    "❌ Please send a valid numeric User ID."
                )
                return

            context.user_data.pop(
                "admin_referral_search",
                None,
            )

            await show_admin_referrals(
                update,
                context,
                target_id,
            )

            return

    # --------------------------------------------------------
    # Wallet input
    # --------------------------------------------------------

    wallet_state = context.user_data.get("wallet_state")

    if wallet_state:
        value = update.message.text.strip()

        if wallet_state == "cbe":
            if not (
                value.isdigit()
                and len(value) == 13
                and value.startswith("1000")
            ):
                await update.message.reply_text(
                    (
                        "❌ <b>Invalid CBE Account</b>\n\n"
                        "CBE must be exactly 13 digits "
                        "and start with 1000.\n\n"
                        "Example: <code>1000XXXXXXXXX</code>"
                    ),
                    parse_mode="HTML",
                )
                return

            wallet_type = "CBE"

        else:
            if not (
                value.isdigit()
                and len(value) == 10
                and (
                    value.startswith("09")
                    or value.startswith("07")
                )
            ):
                await update.message.reply_text(
                    (
                        "❌ <b>Invalid Telebirr Number</b>\n\n"
                        "Telebirr must be exactly 10 digits "
                        "and start with 09 or 07.\n\n"
                        "Example: <code>09XXXXXXXX</code>"
                    ),
                    parse_mode="HTML",
                )
                return

            wallet_type = "Telebirr"

        # Same wallet used by another account
        if wallet_exists_for_other_user(
            wallet_type,
            value,
            user.id,
        ):
            mark_suspicious(user.id, True)

            context.user_data.pop("wallet_state", None)

            await update.message.reply_text(
                (
                    "🚨 <b>Wallet Security Alert</b>\n\n"
                    "This wallet is already linked to another account.\n\n"
                    "Your account has been sent for Security Review.\n\n"
                    "🇪🇹 ለተጨማሪ መረጃ Support ያነጋግሩ።"
                ),
                parse_mode="HTML",
                reply_markup=main_menu(),
            )

            return

        saved = save_wallet(
            user.id,
            wallet_type,
            value,
        )

        if not saved:
            mark_suspicious(user.id, True)

            context.user_data.pop("wallet_state", None)

            await update.message.reply_text(
                "🚨 Wallet could not be saved. Your account is under review.",
                reply_markup=main_menu(),
            )

            return

        context.user_data.pop("wallet_state", None)

        await update.message.reply_text(
            (
                "✅ <b>Wallet Saved Successfully</b>\n\n"
                f"🏦 Type: <b>{wallet_type}</b>\n"
                f"🔢 Account: <code>{value}</code>\n\n"
                "🔐 This wallet is now linked to your account."
            ),
            parse_mode="HTML",
            reply_markup=main_menu(),
        )

        return

    # --------------------------------------------------------
    # Withdrawal input
    # --------------------------------------------------------

    if context.user_data.get("withdraw_state"):
        await process_withdraw_amount(
            update,
            context,
        )
        return

    # --------------------------------------------------------
    # Normal menu
    # --------------------------------------------------------

    text = update.message.text

    if text == "💰 Balance":
        await show_balance(update, context)

    elif text == "👥 Referral":
        await show_referral(update, context)

    elif text == "🎯 Tasks":
        await show_tasks(update, context)

    elif text == "💳 Withdraw":
        await show_withdraw(update, context)

    elif text == "🏦 Wallet":
        await show_wallet(update, context)

    elif text == "🆘 Support":
        await show_support(update, context)

    else:
        await update.message.reply_text(
            (
                "👋 <b>Global Cash Bot</b>\n\n"
                "Please use the menu buttons below."
            ),
            parse_mode="HTML",
            reply_markup=main_menu(),
        )


# ============================================================
# CANCEL COMMAND
# ============================================================

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()

    await update.message.reply_text(
        "❌ Cancelled.",
        reply_markup=main_menu(),
    )


# ============================================================
# ERROR HANDLER
# ============================================================

async def error_handler(
    update: object,
    context: ContextTypes.DEFAULT_TYPE,
):
    logger.exception(
        "Unhandled exception:",
        exc_info=context.error,
    )


# ============================================================
# MAIN
# ============================================================

def main():
    global ADMIN_ID

    # --------------------------------------------------------
    # IMPORTANT:
    # Replace 123456789 with your REAL Telegram numeric Admin ID
    # --------------------------------------------------------

    ADMIN_ID = 123456789

    # --------------------------------------------------------
    # Replace this with your BotFather token.
    # DO NOT publish your token on GitHub.
    # --------------------------------------------------------

    BOT_TOKEN = "PUT_YOUR_BOT_TOKEN_HERE"

    if not BOT_TOKEN or BOT_TOKEN == "PUT_YOUR_BOT_TOKEN_HERE":
        raise RuntimeError(
            "BOT_TOKEN is not configured."
        )

    if ADMIN_ID == 123456789:
        raise RuntimeError(
            "ADMIN_ID is not configured."
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
        CommandHandler("admin", admin_command)
    )

    application.add_handler(
        CommandHandler("cancel", cancel)
    )

    # Callback buttons
    application.add_handler(
        CallbackQueryHandler(callbacks)
    )

    # Text
    application.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            handle_text,
        )
    )

    # Error handler
    application.add_error_handler(
        error_handler
    )

    logger.info(
        "%s is starting...",
        BOT_NAME,
    )

    application.run_polling(
        allowed_updates=Update.ALL_TYPES
    )


if __name__ == "__main__":
    main()

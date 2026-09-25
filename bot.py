import os
import re
import html
import sqlite3
import random
import logging
from datetime import datetime, timedelta

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
)

from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    ConversationHandler,
    filters,
)


# ============================================================
# FALCON WORLD
# Telegram Earning Bot
# ============================================================


# ============================================================
# CONFIGURATION
# ============================================================

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()

# Example:
# ADMIN_IDS=123456789,987654321
ADMIN_IDS = [
    int(x.strip())
    for x in os.getenv("ADMIN_IDS", "").split(",")
    if x.strip().isdigit()
]

BOT_NAME = "Falcon World"

DB_NAME = os.getenv("DB_NAME", "falcon_world.db")

DAILY_BONUS = 0.50
REFERRAL_REWARD = 2.00
MIN_WITHDRAW = 30.00


# ============================================================
# REQUIRED CHANNELS
# ============================================================

MANDATORY_CHANNELS = [
    {
        "username": "@ethiocashflow",
        "name": "Ethio Cash Flow",
        "url": "https://t.me/ethiocashflow",
    },
    {
        "username": "@Sheger_tech1",
        "name": "Sheger Tech",
        "url": "https://t.me/Sheger_tech1",
    },
    {
        "username": "@EthioVortex1",
        "name": "Ethio Vortex",
        "url": "https://t.me/EthioVortex1",
    },
    {
        "username": "@AmanIncomeLab",
        "name": "Aman Income Lab",
        "url": "https://t.me/AmanIncomeLab",
    },
    {
        "username": "@OnlineIncomeHub07",
        "name": "Online Income Hub",
        "url": "https://t.me/OnlineIncomeHub07",
    },
    {
        "username": "@Paymentprooff2",
        "name": "Payment Proof",
        "url": "https://t.me/Paymentprooff2",
    },
]


# ============================================================
# CONVERSATION STATES
# ============================================================

(
    CAPTCHA,
    WALLET_INPUT,
    TASK_PROOF,
    WITHDRAW_CONFIRM,
) = range(4)


# ============================================================
# LOGGING
# ============================================================

logging.basicConfig(
    format="%(asctime)s | %(name)s | %(levelname)s | %(message)s",
    level=logging.INFO,
)

logger = logging.getLogger("FalconWorld")


# ============================================================
# DATABASE
# ============================================================

def db():
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():

    conn = db()
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT DEFAULT '',
            full_name TEXT DEFAULT '',
            balance REAL DEFAULT 0,
            referred_by INTEGER,
            cbe_account TEXT DEFAULT '',
            telebirr_account TEXT DEFAULT '',
            last_daily_bonus TEXT DEFAULT '',
            is_banned INTEGER DEFAULT 0,
            suspicious INTEGER DEFAULT 0,
            referral_rewarded INTEGER DEFAULT 0,
            verified INTEGER DEFAULT 0,
            joined_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS referrals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            referrer_id INTEGER NOT NULL,
            referred_id INTEGER UNIQUE NOT NULL,
            reward_amount REAL NOT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS withdrawals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            amount REAL NOT NULL,
            method TEXT NOT NULL,
            account_number TEXT NOT NULL,
            status TEXT DEFAULT 'PENDING',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            link TEXT DEFAULT '',
            reward REAL DEFAULT 0,
            is_active INTEGER DEFAULT 1,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS task_submissions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            proof_text TEXT DEFAULT '',
            proof_file_id TEXT DEFAULT '',
            status TEXT DEFAULT 'PENDING',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
    """)

    # --------------------------------------------------------
    # Migration for old database
    # --------------------------------------------------------

    existing_columns = {
        row["name"]
        for row in cursor.execute("PRAGMA table_info(users)").fetchall()
    }

    migrations = {
        "referral_rewarded": "INTEGER DEFAULT 0",
        "verified": "INTEGER DEFAULT 0",
    }

    for column, definition in migrations.items():
        if column not in existing_columns:
            cursor.execute(
                f"ALTER TABLE users ADD COLUMN {column} {definition}"
            )

    # --------------------------------------------------------
    # Default settings
    # --------------------------------------------------------

    defaults = {
        "daily_bonus": "0.50",
        "referral_reward": "2.00",
        "min_withdraw": "30.00",
    }

    for key, value in defaults.items():
        cursor.execute(
            """
            INSERT OR IGNORE INTO settings (key, value)
            VALUES (?, ?)
            """,
            (key, value),
        )

    conn.commit()
    conn.close()


init_db()


# ============================================================
# SETTINGS
# ============================================================

def get_setting(key, default=None):

    conn = db()

    row = conn.execute(
        "SELECT value FROM settings WHERE key = ?",
        (key,),
    ).fetchone()

    conn.close()

    if not row:
        return default

    try:
        return float(row["value"])
    except Exception:
        return row["value"]


def set_setting(key, value):

    conn = db()

    conn.execute(
        """
        INSERT OR REPLACE INTO settings (key, value)
        VALUES (?, ?)
        """,
        (key, str(value)),
    )

    conn.commit()
    conn.close()


# ============================================================
# USER FUNCTIONS
# ============================================================

def get_user(user_id):

    conn = db()

    user = conn.execute(
        "SELECT * FROM users WHERE user_id = ?",
        (user_id,),
    ).fetchone()

    conn.close()

    return user


def create_user(
    user_id,
    username="",
    full_name="",
    referred_by=None,
):

    conn = db()

    existing = conn.execute(
        "SELECT user_id FROM users WHERE user_id = ?",
        (user_id,),
    ).fetchone()

    if existing:
        conn.execute(
            """
            UPDATE users
            SET username = ?, full_name = ?
            WHERE user_id = ?
            """,
            (
                username or "",
                full_name or "",
                user_id,
            ),
        )

        conn.commit()
        conn.close()

        return False

    # Prevent self-referral
    if referred_by == user_id:
        referred_by = None

    conn.execute(
        """
        INSERT INTO users (
            user_id,
            username,
            full_name,
            referred_by
        )
        VALUES (?, ?, ?, ?)
        """,
        (
            user_id,
            username or "",
            full_name or "",
            referred_by,
        ),
    )

    conn.commit()
    conn.close()

    return True


def is_banned(user_id):

    user = get_user(user_id)

    return bool(user and user["is_banned"] == 1)


# ============================================================
# CHANNEL MEMBERSHIP
# ============================================================

async def check_channel_membership(
    user_id,
    context,
):

    missing = []

    for channel in MANDATORY_CHANNELS:

        try:

            member = await context.bot.get_chat_member(
                chat_id=channel["username"],
                user_id=user_id,
            )

            if member.status in (
                "left",
                "kicked",
            ):
                missing.append(channel)

        except Exception as e:

            logger.warning(
                "Membership check failed for %s: %s",
                channel["username"],
                e,
            )

            missing.append(channel)

    return missing


# ============================================================
# MAIN KEYBOARD
# ============================================================

def main_keyboard():

    keyboard = [
        ["💰 Balance", "🎁 Daily Bonus"],
        ["👥 Invite Friends", "📋 Tasks"],
        ["💳 Wallet Settings", "🔻 Withdraw"],
        ["📊 Statistics", "❓ Help"],
    ]

    return ReplyKeyboardMarkup(
        keyboard,
        resize_keyboard=True,
    )


# ============================================================
# CHANNEL KEYBOARD
# ============================================================

def channel_keyboard(missing_channels):

    buttons = []

    for channel in missing_channels:

        buttons.append(
            [
                InlineKeyboardButton(
                    f"📢 Join {channel['name']}",
                    url=channel["url"],
                )
            ]
        )

    buttons.append(
        [
            InlineKeyboardButton(
                "✅ Verify Joining",
                callback_data="verify_membership",
            )
        ]
    )

    return InlineKeyboardMarkup(buttons)


# ============================================================
# START
# ============================================================

async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    user = update.effective_user

    if not user:
        return ConversationHandler.END

    if is_banned(user.id):

        await update.message.reply_text(
            "⛔ <b>Your account has been suspended.</b>",
            parse_mode="HTML",
        )

        return ConversationHandler.END

    # --------------------------------------------------------
    # Referral
    # --------------------------------------------------------

    referred_by = None

    if context.args:

        referral_code = context.args[0].strip()

        if referral_code.startswith("ref_"):
            referral_code = referral_code[4:]

        if referral_code.isdigit():

            candidate = int(referral_code)

            if candidate != user.id:
                referred_by = candidate

    create_user(
        user.id,
        user.username,
        user.full_name,
        referred_by,
    )

    # --------------------------------------------------------
    # CAPTCHA
    # --------------------------------------------------------

    num1 = random.randint(1, 9)
    num2 = random.randint(1, 9)

    context.user_data["captcha_answer"] = num1 + num2

    await update.message.reply_text(
        "🤖 <b>Falcon World Verification</b>\n\n"
        "Before you continue, solve this simple verification:\n\n"
        f"🔢 <b>{num1} + {num2} = ?</b>\n\n"
        "Send only the answer.",
        parse_mode="HTML",
    )

    return CAPTCHA


# ============================================================
# CAPTCHA
# ============================================================

async def verify_captcha(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    if not update.message:
        return ConversationHandler.END

    answer = update.message.text.strip()

    correct = str(
        context.user_data.get(
            "captcha_answer",
            "",
        )
    )

    if answer != correct:

        await update.message.reply_text(
            "❌ Incorrect answer.\n\n"
            "Please use /start to try again."
        )

        return ConversationHandler.END

    context.user_data.pop(
        "captcha_answer",
        None,
    )

    await show_channel_verification(
        update,
        context,
    )

    return ConversationHandler.END


# ============================================================
# CHANNEL VERIFICATION SCREEN
# ============================================================

async def show_channel_verification(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    user_id = update.effective_user.id

    missing = await check_channel_membership(
        user_id,
        context,
    )

    if missing:

        text = (
            "🦅 <b>WELCOME TO FALCON WORLD</b>\n\n"
            "Before using the bot, please join all "
            "required channels below.\n\n"
            "After joining them all, press "
            "<b>Verify Joining</b>."
        )

        if update.callback_query:

            await update.callback_query.edit_message_text(
                text,
                reply_markup=channel_keyboard(missing),
                parse_mode="HTML",
            )

        else:

            await update.message.reply_text(
                text,
                reply_markup=channel_keyboard(missing),
                parse_mode="HTML",
            )

        return

    # All channels joined
    await complete_verification(
        user_id,
        context,
    )

    if update.callback_query:

        await update.callback_query.edit_message_text(
            "✅ <b>Verification successful!</b>\n\n"
            "Welcome to Falcon World 🦅",
            parse_mode="HTML",
        )

        await context.bot.send_message(
            chat_id=user_id,
            text=welcome_text(
                update.effective_user.first_name
            ),
            reply_markup=main_keyboard(),
            parse_mode="HTML",
        )

    else:

        await update.message.reply_text(
            welcome_text(
                update.effective_user.first_name
            ),
            reply_markup=main_keyboard(),
            parse_mode="HTML",
        )


# ============================================================
# WELCOME
# ============================================================

def welcome_text(first_name):

    safe_name = html.escape(
        first_name or "Friend"
    )

    return (
        f"🦅 <b>WELCOME TO FALCON WORLD</b>\n\n"
        f"👋 Hello {safe_name}!\n\n"
        "💰 Earn & Complete Tasks\n"
        "🎁 Daily Rewards\n"
        "👥 Referral Rewards\n"
        "🚀 New Opportunities\n\n"
        "📢 <b>Ads & Promotions:</b> Contact Admin\n"
        "💱 <b>USDT Exchange:</b> Buy & Sell\n\n"
        "🚀 Open Falcon World from the Menu below."
    )


# ============================================================
# COMPLETE VERIFICATION
# ============================================================

async def complete_verification(
    user_id,
    context,
):

    conn = db()

    user = conn.execute(
        """
        SELECT referred_by, referral_rewarded
        FROM users
        WHERE user_id = ?
        """,
        (user_id,),
    ).fetchone()

    if not user:
        conn.close()
        return

    # Mark user verified
    conn.execute(
        """
        UPDATE users
        SET verified = 1
        WHERE user_id = ?
        """,
        (user_id,),
    )

    conn.commit()

    referred_by = user["referred_by"]
    already_rewarded = user["referral_rewarded"]

    # --------------------------------------------------------
    # Referral reward only after verification
    # --------------------------------------------------------

    if referred_by and not already_rewarded:

        # Referrer must exist
        referrer = conn.execute(
            """
            SELECT user_id
            FROM users
            WHERE user_id = ?
            """,
            (referred_by,),
        ).fetchone()

        if referrer and referred_by != user_id:

            # Make absolutely sure this referral was not paid
            existing = conn.execute(
                """
                SELECT id
                FROM referrals
                WHERE referred_id = ?
                """,
                (user_id,),
            ).fetchone()

            if not existing:

                reward = get_setting(
                    "referral_reward",
                    REFERRAL_REWARD,
                )

                conn.execute(
                    """
                    UPDATE users
                    SET balance = balance + ?
                    WHERE user_id = ?
                    """,
                    (
                        reward,
                        referred_by,
                    ),
                )

                conn.execute(
                    """
                    INSERT INTO referrals (
                        referrer_id,
                        referred_id,
                        reward_amount
                    )
                    VALUES (?, ?, ?)
                    """,
                    (
                        referred_by,
                        user_id,
                        reward,
                    ),
                )

                conn.execute(
                    """
                    UPDATE users
                    SET referral_rewarded = 1
                    WHERE user_id = ?
                    """,
                    (user_id,),
                )

                conn.commit()

                try:

                    await context.bot.send_message(
                        chat_id=referred_by,
                        text=(
                            "🎉 <b>Referral Reward!</b>\n\n"
                            "A new user joined through your link "
                            "and completed verification.\n\n"
                            f"💰 Reward: <b>+{reward:.2f} ETB</b>"
                        ),
                        parse_mode="HTML",
                    )

                except Exception as e:

                    logger.warning(
                        "Could not notify referrer: %s",
                        e,
                    )

    conn.close()


# ============================================================
# VERIFY CALLBACK
# ============================================================

async def callback_verify_membership(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    query = update.callback_query

    await query.answer()

    user_id = query.from_user.id

    if is_banned(user_id):

        await query.answer(
            "⛔ Your account is suspended.",
            show_alert=True,
        )

        return

    missing = await check_channel_membership(
        user_id,
        context,
    )

    if missing:

        names = "\n".join(
            f"• {x['name']}"
            for x in missing
        )

        await query.answer(
            "❌ You still have channels to join.",
            show_alert=True,
        )

        try:

            await query.edit_message_text(
                "⚠️ <b>Some required channels are still missing.</b>\n\n"
                f"{names}\n\n"
                "Join them and press Verify again.",
                reply_markup=channel_keyboard(missing),
                parse_mode="HTML",
            )

        except Exception:
            pass

        return

    await complete_verification(
        user_id,
        context,
    )

    await query.edit_message_text(
        "✅ <b>Verification successful!</b>\n\n"
        "Welcome to Falcon World 🦅",
        parse_mode="HTML",
    )

    await context.bot.send_message(
        chat_id=user_id,
        text=welcome_text(
            query.from_user.first_name
        ),
        reply_markup=main_keyboard(),
        parse_mode="HTML",
    )


# ============================================================
# BALANCE
# ============================================================

async def handle_balance(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    user = get_user(
        update.effective_user.id
    )

    if not user:
        return

    cbe = user["cbe_account"] or "Not Set"
    telebirr = user["telebirr_account"] or "Not Set"

    text = (
        "💰 <b>MY BALANCE</b>\n\n"
        f"🆔 User ID: <code>{user['user_id']}</code>\n"
        f"💵 Balance: <b>{user['balance']:.2f} ETB</b>\n\n"
        "💳 <b>Wallets</b>\n"
        f"🏦 CBE: <code>{html.escape(cbe)}</code>\n"
        f"📱 Telebirr: <code>{html.escape(telebirr)}</code>"
    )

    await update.message.reply_text(
        text,
        parse_mode="HTML",
    )


# ============================================================
# DAILY BONUS
# ============================================================

async def handle_daily_bonus(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    user_id = update.effective_user.id

    if is_banned(user_id):
        return

    conn = db()

    user = conn.execute(
        """
        SELECT last_daily_bonus, balance
        FROM users
        WHERE user_id = ?
        """,
        (user_id,),
    ).fetchone()

    if not user:
        conn.close()
        return

    now = datetime.now()

    if user["last_daily_bonus"]:

        try:

            last_claim = datetime.fromisoformat(
                user["last_daily_bonus"]
            )

            next_claim = (
                last_claim +
                timedelta(hours=24)
            )

            if now < next_claim:

                remaining = (
                    next_claim - now
                )

                hours = int(
                    remaining.total_seconds()
                    // 3600
                )

                minutes = int(
                    (
                        remaining.total_seconds()
                        % 3600
                    )
                    // 60
                )

                await update.message.reply_text(
                    "⏳ <b>Daily Bonus Already Claimed</b>\n\n"
                    f"Come back in <b>{hours}h "
                    f"{minutes}m</b>.",
                    parse_mode="HTML",
                )

                conn.close()
                return

        except Exception:
            pass

    bonus = get_setting(
        "daily_bonus",
        DAILY_BONUS,
    )

    conn.execute(
        """
        UPDATE users
        SET balance = balance + ?,
            last_daily_bonus = ?
        WHERE user_id = ?
        """,
        (
            bonus,
            now.isoformat(),
            user_id,
        ),
    )

    conn.commit()

    new_balance = conn.execute(
        """
        SELECT balance
        FROM users
        WHERE user_id = ?
        """,
        (user_id,),
    ).fetchone()["balance"]

    conn.close()

    await update.message.reply_text(
        "🎁 <b>DAILY BONUS CLAIMED!</b>\n\n"
        f"💰 Reward: <b>+{bonus:.2f} ETB</b>\n"
        f"💵 New Balance: <b>{new_balance:.2f} ETB</b>",
        parse_mode="HTML",
    )


# ============================================================
# REFERRAL
# ============================================================

async def handle_invite(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    user_id = update.effective_user.id

    bot_info = await context.bot.get_me()

    conn = db()

    count = conn.execute(
        """
        SELECT COUNT(*)
        FROM referrals
        WHERE referrer_id = ?
        """,
        (user_id,),
    ).fetchone()[0]

    total_earned = conn.execute(
        """
        SELECT COALESCE(SUM(reward_amount), 0)
        FROM referrals
        WHERE referrer_id = ?
        """,
        (user_id,),
    ).fetchone()[0]

    conn.close()

    reward = get_setting(
        "referral_reward",
        REFERRAL_REWARD,
    )

    link = (
        f"https://t.me/"
        f"{bot_info.username}"
        f"?start=ref_{user_id}"
    )

    text = (
        "👥 <b>INVITE FRIENDS</b>\n\n"
        f"💰 Earn <b>{reward:.2f} ETB</b> "
        "for every referred user who completes "
        "all required channels.\n\n"
        "🔗 <b>Your Referral Link:</b>\n"
        f"<code>{link}</code>\n\n"
        f"👥 Invited: <b>{count}</b>\n"
        f"💵 Referral Earnings: <b>{total_earned:.2f} ETB</b>"
    )

    buttons = [
        [
            InlineKeyboardButton(
                "📤 Share Referral Link",
                url=(
                    "https://t.me/share/url"
                    f"?url={link}"
                ),
            )
        ]
    ]

    await update.message.reply_text(
        text,
        reply_markup=InlineKeyboardMarkup(buttons),
        parse_mode="HTML",
    )


# ============================================================
# WALLET SETTINGS
# ============================================================

async def start_wallet_setup(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    buttons = [
        [
            InlineKeyboardButton(
                "🏦 CBE Account",
                callback_data="set_wallet_cbe",
            )
        ],
        [
            InlineKeyboardButton(
                "📱 Telebirr",
                callback_data="set_wallet_telebirr",
            )
        ],
    ]

    await update.message.reply_text(
        "💳 <b>Wallet Settings</b>\n\n"
        "Select the wallet you want to save:",
        reply_markup=InlineKeyboardMarkup(buttons),
        parse_mode="HTML",
    )


# ============================================================
# WALLET CHOICE
# ============================================================

async def handle_wallet_choice(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    query = update.callback_query

    await query.answer()

    context.user_data["wallet_choice"] = query.data

    if query.data == "set_wallet_cbe":

        text = (
            "🏦 <b>CBE Account</b>\n\n"
            "Send your 13-digit CBE account number.\n\n"
            "Example:\n"
            "<code>1000XXXXXXXXX</code>"
        )

    else:

        text = (
            "📱 <b>Telebirr</b>\n\n"
            "Send your 10-digit Telebirr number.\n\n"
            "Example:\n"
            "<code>0912345678</code>"
        )

    await query.edit_message_text(
        text,
        parse_mode="HTML",
    )

    return WALLET_INPUT


# ============================================================
# SAVE WALLET
# ============================================================

async def save_wallet_input(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    user_id = update.effective_user.id

    value = update.message.text.strip()

    choice = context.user_data.get(
        "wallet_choice"
    )

    conn = db()

    # --------------------------------------------------------
    # CBE
    # --------------------------------------------------------

    if choice == "set_wallet_cbe":

        if not re.fullmatch(
            r"1000\d{9}",
            value,
        ):

            await update.message.reply_text(
                "❌ Invalid CBE account.\n\n"
                "It must be exactly 13 digits "
                "and start with 1000."
            )

            conn.close()

            return WALLET_INPUT

        duplicate = conn.execute(
            """
            SELECT user_id
            FROM users
            WHERE cbe_account = ?
            AND user_id != ?
            """,
            (
                value,
                user_id,
            ),
        ).fetchall()

        if duplicate:

            conn.execute(
                """
                UPDATE users
                SET suspicious = 1
                WHERE user_id = ?
                """,
                (user_id,),
            )

            for row in duplicate:

                conn.execute(
                    """
                    UPDATE users
                    SET suspicious = 1
                    WHERE user_id = ?
                    """,
                    (row["user_id"],),
                )

            conn.commit()

            await notify_admins(
                context,
                (
                    "🚨 <b>DUPLICATE CBE WALLET</b>\n\n"
                    f"User ID: <code>{user_id}</code>\n"
                    f"CBE: <code>{value}</code>\n"
                    f"Existing Users: "
                    f"<code>{[x['user_id'] for x in duplicate]}</code>"
                ),
            )

        conn.execute(
            """
            UPDATE users
            SET cbe_account = ?
            WHERE user_id = ?
            """,
            (
                value,
                user_id,
            ),
        )

        conn.commit()
        conn.close()

        context.user_data.pop(
            "wallet_choice",
            None,
        )

        await update.message.reply_text(
            "✅ <b>CBE account saved successfully.</b>",
            reply_markup=main_keyboard(),
            parse_mode="HTML",
        )

        return ConversationHandler.END

    # --------------------------------------------------------
    # TELEBIRR
    # --------------------------------------------------------

    if choice == "set_wallet_telebirr":

        if not re.fullmatch(
            r"(09|07)\d{8}",
            value,
        ):

            await update.message.reply_text(
                "❌ Invalid Telebirr number.\n\n"
                "It must be exactly 10 digits "
                "and start with 09 or 07."
            )

            conn.close()

            return WALLET_INPUT

        duplicate = conn.execute(
            """
            SELECT user_id
            FROM users
            WHERE telebirr_account = ?
            AND user_id != ?
            """,
            (
                value,
                user_id,
            ),
        ).fetchall()

        if duplicate:

            conn.execute(
                """
                UPDATE users
                SET suspicious = 1
                WHERE user_id = ?
                """,
                (user_id,),
            )

            for row in duplicate:

                conn.execute(
                    """
                    UPDATE users
                    SET suspicious = 1
                    WHERE user_id = ?
                    """,
                    (row["user_id"],),
                )

            conn.commit()

            await notify_admins(
                context,
                (
                    "🚨 <b>DUPLICATE TELEBIRR</b>\n\n"
                    f"User ID: <code>{user_id}</code>\n"
                    f"Telebirr: <code>{value}</code>\n"
                    f"Existing Users: "
                    f"<code>{[x['user_id'] for x in duplicate]}</code>"
                ),
            )

        conn.execute(
            """
            UPDATE users
            SET telebirr_account = ?
            WHERE user_id = ?
            """,
            (
                value,
                user_id,
            ),
        )

        conn.commit()
        conn.close()

        context.user_data.pop(
            "wallet_choice",
            None,
        )

        await update.message.reply_text(
            "✅ <b>Telebirr saved successfully.</b>",
            reply_markup=main_keyboard(),
            parse_mode="HTML",
        )

        return ConversationHandler.END

    conn.close()

    await update.message.reply_text(
        "❌ Wallet setup error. Please try again."
    )

    return ConversationHandler.END


# ============================================================
# WITHDRAW
# ============================================================

async def handle_withdraw_request(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    user_id = update.effective_user.id

    user = get_user(user_id)

    if not user:
        return

    minimum = get_setting(
        "min_withdraw",
        MIN_WITHDRAW,
    )

    balance = float(user["balance"])

    if balance < minimum:

        await update.message.reply_text(
            "🔻 <b>WITHDRAW</b>\n\n"
            f"Minimum withdrawal: <b>{minimum:.2f} ETB</b>\n"
            f"Your balance: <b>{balance:.2f} ETB</b>",
            parse_mode="HTML",
        )

        return

    buttons = []

    if user["cbe_account"]:

        buttons.append(
            [
                InlineKeyboardButton(
                    f"🏦 CBE • {user['cbe_account']}",
                    callback_data="withdraw_cbe",
                )
            ]
        )

    if user["telebirr_account"]:

        buttons.append(
            [
                InlineKeyboardButton(
                    f"📱 Telebirr • {user['telebirr_account']}",
                    callback_data="withdraw_telebirr",
                )
            ]
        )

    if not buttons:

        await update.message.reply_text(
            "⚠️ <b>No withdrawal wallet found.</b>\n\n"
            "Please open <b>💳 Wallet Settings</b> "
            "and add CBE or Telebirr first.",
            parse_mode="HTML",
        )

        return

    await update.message.reply_text(
        "🔻 <b>WITHDRAWAL</b>\n\n"
        f"Available balance: <b>{balance:.2f} ETB</b>\n\n"
        "Select your withdrawal method:",
        reply_markup=InlineKeyboardMarkup(buttons),
        parse_mode="HTML",
    )


# ============================================================
# WITHDRAW SELECTION
# ============================================================

async def process_withdrawal_selection(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    query = update.callback_query

    await query.answer()

    user_id = query.from_user.id

    if is_banned(user_id):
        return

    user = get_user(user_id)

    if not user:
        return

    method = (
        "CBE"
        if query.data == "withdraw_cbe"
        else "Telebirr"
    )

    account = (
        user["cbe_account"]
        if method == "CBE"
        else user["telebirr_account"]
    )

    balance = float(user["balance"])

    minimum = get_setting(
        "min_withdraw",
        MIN_WITHDRAW,
    )

    if balance < minimum:

        await query.edit_message_text(
            "❌ Insufficient balance."
        )

        return

    # Store pending withdrawal information
    context.user_data["withdraw_method"] = method
    context.user_data["withdraw_account"] = account
    context.user_data["withdraw_amount"] = balance

    buttons = [
        [
            InlineKeyboardButton(
                "✅ Confirm Withdrawal",
                callback_data="confirm_withdraw",
            )
        ],
        [
            InlineKeyboardButton(
                "❌ Cancel",
                callback_data="cancel_withdraw",
            )
        ],
    ]

    await query.edit_message_text(
        "🔻 <b>CONFIRM WITHDRAWAL</b>\n\n"
        f"💰 Amount: <b>{balance:.2f} ETB</b>\n"
        f"💳 Method: <b>{method}</b>\n"
        f"🏦 Account: <code>{account}</code>\n\n"
        "Please confirm your request.",
        reply_markup=InlineKeyboardMarkup(buttons),
        parse_mode="HTML",
    )


# ============================================================
# CONFIRM WITHDRAW
# ============================================================

async def confirm_withdraw(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    query = update.callback_query

    await query.answer()

    user_id = query.from_user.id

    method = context.user_data.get(
        "withdraw_method"
    )

    account = context.user_data.get(
        "withdraw_account"
    )

    amount = context.user_data.get(
        "withdraw_amount"
    )

    if not method or not account or not amount:

        await query.edit_message_text(
            "❌ Withdrawal session expired. "
            "Please try again."
        )

        return

    conn = db()

    user = conn.execute(
        """
        SELECT *
        FROM users
        WHERE user_id = ?
        """,
        (user_id,),
    ).fetchone()

    if not user:

        conn.close()

        await query.edit_message_text(
            "❌ User not found."
        )

        return

    # Recheck balance
    if float(user["balance"]) < float(amount):

        conn.close()

        await query.edit_message_text(
            "❌ Your balance has changed. "
            "Please try again."
        )

        return

    # Deduct balance
    conn.execute(
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

    conn.execute(
        """
        INSERT INTO withdrawals (
            user_id,
            amount,
            method,
            account_number
        )
        VALUES (?, ?, ?, ?)
        """,
        (
            user_id,
            amount,
            method,
            account,
        ),
    )

    withdrawal_id = conn.execute(
        "SELECT last_insert_rowid()"
    ).fetchone()[0]

    conn.commit()
    conn.close()

    # Notify admins
    suspicious = user["suspicious"] == 1

    warning = (
        "\n🚨 <b>MULTI-ACCOUNT FLAG</b>\n"
        if suspicious
        else ""
    )

    username = (
        f"@{html.escape(user['username'])}"
        if user["username"]
        else "N/A"
    )

    admin_text = (
        f"🚨 <b>NEW WITHDRAWAL #{withdrawal_id}</b>\n"
        f"{warning}\n"
        "👤 <b>User</b>\n"
        f"Name: {html.escape(user['full_name'])}\n"
        f"Username: {username}\n"
        f"ID: <code>{user_id}</code>\n\n"
        "💰 <b>Payout</b>\n"
        f"Amount: <b>{amount:.2f} ETB</b>\n"
        f"Method: <b>{method}</b>\n"
        f"Account: <code>{account}</code>"
    )

    buttons = [
        [
            InlineKeyboardButton(
                "✅ Approve",
                callback_data=f"adm_appr_{withdrawal_id}",
            ),
            InlineKeyboardButton(
                "❌ Reject & Refund",
                callback_data=f"adm_rej_{withdrawal_id}",
            ),
        ],
        [
            InlineKeyboardButton(
                "⛔ Ban User",
                callback_data=f"adm_ban_{user_id}",
            )
        ],
    ]

    for admin_id in ADMIN_IDS:

        try:

            await context.bot.send_message(
                chat_id=admin_id,
                text=admin_text,
                reply_markup=InlineKeyboardMarkup(buttons),
                parse_mode="HTML",
            )

        except Exception as e:

            logger.warning(
                "Admin notification failed: %s",
                e,
            )

    context.user_data.clear()

    await query.edit_message_text(
        "✅ <b>Withdrawal Submitted</b>\n\n"
        f"💰 Amount: <b>{amount:.2f} ETB</b>\n"
        f"💳 Method: <b>{method}</b>\n\n"
        "⏳ Your request is waiting for Admin verification.",
        parse_mode="HTML",
    )


# ============================================================
# CANCEL WITHDRAW
# ============================================================

async def cancel_withdraw(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    query = update.callback_query

    await query.answer()

    context.user_data.pop(
        "withdraw_method",
        None,
    )

    context.user_data.pop(
        "withdraw_account",
        None,
    )

    context.user_data.pop(
        "withdraw_amount",
        None,
    )

    await query.edit_message_text(
        "❌ Withdrawal cancelled."
    )


# ============================================================
# TASK LIST
# ============================================================

async def handle_tasks_list(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    user_id = update.effective_user.id

    conn = db()

    tasks = conn.execute(
        """
        SELECT
            t.id,
            t.title,
            t.link,
            t.reward
        FROM tasks t
        WHERE t.is_active = 1
        AND NOT EXISTS (
            SELECT 1
            FROM task_submissions ts
            WHERE ts.task_id = t.id
            AND ts.user_id = ?
            AND ts.status IN ('PENDING', 'APPROVED')
        )
        ORDER BY t.id DESC
        """,
        (user_id,),
    ).fetchall()

    conn.close()

    if not tasks:

        await update.message.reply_text(
            "📋 <b>No tasks available.</b>\n\n"
            "Please check again later.",
            parse_mode="HTML",
        )

        return

    buttons = []

    for task in tasks:

        buttons.append(
            [
                InlineKeyboardButton(
                    f"👉 {task['title']} "
                    f"(+{task['reward']:.2f} ETB)",
                    callback_data=f"dotask_{task['id']}",
                )
            ]
        )

    await update.message.reply_text(
        "📋 <b>AVAILABLE TASKS</b>\n\n"
        "Select a task below:",
        reply_markup=InlineKeyboardMarkup(buttons),
        parse_mode="HTML",
    )


# ============================================================
# TASK CLICK
# ============================================================

async def process_task_click(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    query = update.callback_query

    await query.answer()

    try:

        task_id = int(
            query.data.split("_")[1]
        )

    except Exception:

        await query.edit_message_text(
            "❌ Invalid task."
        )

        return ConversationHandler.END

    user_id = query.from_user.id

    conn = db()

    task = conn.execute(
        """
        SELECT *
        FROM tasks
        WHERE id = ?
        AND is_active = 1
        """,
        (task_id,),
    ).fetchone()

    existing = conn.execute(
        """
        SELECT id
        FROM task_submissions
        WHERE task_id = ?
        AND user_id = ?
        AND status IN ('PENDING', 'APPROVED')
        """,
        (
            task_id,
            user_id,
        ),
    ).fetchone()

    conn.close()

    if not task:

        await query.edit_message_text(
            "❌ Task no longer exists."
        )

        return ConversationHandler.END

    if existing:

        await query.edit_message_text(
            "⚠️ You already submitted this task."
        )

        return ConversationHandler.END

    context.user_data["current_task_id"] = task_id

    text = (
        "📋 <b>TASK DETAILS</b>\n\n"
        f"📌 Task: <b>{html.escape(task['title'])}</b>\n"
        f"💰 Reward: <b>{task['reward']:.2f} ETB</b>\n\n"
        f"🔗 Link:\n{html.escape(task['link'])}\n\n"
        "📝 Complete the task and send your proof.\n\n"
        "You can send:\n"
        "• Screenshot\n"
        "• Username\n"
        "• Text proof"
    )

    await query.edit_message_text(
        text,
        parse_mode="HTML",
    )

    return TASK_PROOF


# ============================================================
# TASK PROOF
# ============================================================

async def handle_task_proof_submission(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    user_id = update.effective_user.id

    task_id = context.user_data.get(
        "current_task_id"
    )

    if not task_id:

        await update.message.reply_text(
            "❌ Task session expired. "
            "Please select the task again."
        )

        return ConversationHandler.END

    proof_text = ""
    proof_file_id = ""

    if update.message.photo:

        proof_file_id = (
            update.message.photo[-1].file_id
        )

        proof_text = (
            update.message.caption
            or "Screenshot proof"
        )

    elif update.message.text:

        proof_text = (
            update.message.text.strip()
        )

    else:

        await update.message.reply_text(
            "❌ Please send text or a screenshot."
        )

        return TASK_PROOF

    conn = db()

    task = conn.execute(
        """
        SELECT title, reward
        FROM tasks
        WHERE id = ?
        """,
        (task_id,),
    ).fetchone()

    if not task:

        conn.close()

        await update.message.reply_text(
            "❌ Task not found."
        )

        return ConversationHandler.END

    # Prevent duplicate submissions
    existing = conn.execute(
        """
        SELECT id
        FROM task_submissions
        WHERE task_id = ?
        AND user_id = ?
        AND status IN ('PENDING', 'APPROVED')
        """,
        (
            task_id,
            user_id,
        ),
    ).fetchone()

    if existing:

        conn.close()

        await update.message.reply_text(
            "⚠️ You already submitted this task."
        )

        return ConversationHandler.END

    cursor = conn.execute(
        """
        INSERT INTO task_submissions (
            task_id,
            user_id,
            proof_text,
            proof_file_id
        )
        VALUES (?, ?, ?, ?)
        """,
        (
            task_id,
            user_id,
            proof_text,
            proof_file_id,
        ),
    )

    submission_id = cursor.lastrowid

    conn.commit()
    conn.close()

    # --------------------------------------------------------
    # Admin notification
    # --------------------------------------------------------

    user = get_user(user_id)

    username = (
        f"@{html.escape(user['username'])}"
        if user and user["username"]
        else "N/A"
    )

    admin_text = (
        f"📥 <b>NEW TASK PROOF #{submission_id}</b>\n\n"
        f"👤 User: <code>{user_id}</code>\n"
        f"Username: {username}\n"
        f"📌 Task: <b>{html.escape(task['title'])}</b>\n"
        f"💰 Reward: <b>{task['reward']:.2f} ETB</b>\n\n"
        f"📝 Proof:\n{html.escape(proof_text)}"
    )

    buttons = [
        [
            InlineKeyboardButton(
                "✅ Approve",
                callback_data=f"tappr_{submission_id}",
            ),
            InlineKeyboardButton(
                "❌ Reject",
                callback_data=f"trej_{submission_id}",
            ),
        ]
    ]

    for admin_id in ADMIN_IDS:

        try:

            if proof_file_id:

                await context.bot.send_photo(
                    chat_id=admin_id,
                    photo=proof_file_id,
                    caption=admin_text,
                    reply_markup=InlineKeyboardMarkup(buttons),
                    parse_mode="HTML",
                )

            else:

                await context.bot.send_message(
                    chat_id=admin_id,
                    text=admin_text,
                    reply_markup=InlineKeyboardMarkup(buttons),
                    parse_mode="HTML",
                )

        except Exception as e:

            logger.warning(
                "Task proof admin notification failed: %s",
                e,
            )

    context.user_data.pop(
        "current_task_id",
        None,
    )

    await update.message.reply_text(
        "✅ <b>Proof Submitted!</b>\n\n"
        "Your submission has been sent to Admin "
        "for review.",
        reply_markup=main_keyboard(),
        parse_mode="HTML",
    )

    return ConversationHandler.END


# ============================================================
# STATISTICS
# ============================================================

async def handle_stats(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    conn = db()

    total_users = conn.execute(
        "SELECT COUNT(*) FROM users"
    ).fetchone()[0]

    verified_users = conn.execute(
        """
        SELECT COUNT(*)
        FROM users
        WHERE verified = 1
        """
    ).fetchone()[0]

    total_paid = conn.execute(
        """
        SELECT COALESCE(SUM(amount), 0)
        FROM withdrawals
        WHERE status = 'APPROVED'
        """
    ).fetchone()[0]

    conn.close()

    await update.message.reply_text(
        "📊 <b>FALCON WORLD STATISTICS</b>\n\n"
        f"👥 Members: <b>{total_users}</b>\n"
        f"✅ Verified: <b>{verified_users}</b>\n"
        f"💸 Total Paid: <b>{total_paid:.2f} ETB</b>\n"
        "⚡ Status: <b>Online</b>",
        parse_mode="HTML",
    )


# ============================================================
# HELP
# ============================================================

async def handle_help(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    await update.message.reply_text(
        "❓ <b>FALCON WORLD HELP</b>\n\n"
        "💰 <b>Balance</b>\n"
        "Check your current balance and wallets.\n\n"
        "🎁 <b>Daily Bonus</b>\n"
        "Claim 0.50 ETB every 24 hours.\n\n"
        "👥 <b>Invite Friends</b>\n"
        "Earn 2 ETB when your referred user "
        "completes verification.\n\n"
        "📋 <b>Tasks</b>\n"
        "Complete available tasks and submit proof.\n\n"
        "💳 <b>Wallet Settings</b>\n"
        "Save your CBE or Telebirr account.\n\n"
        "🔻 <b>Withdraw</b>\n"
        f"Minimum withdrawal: {MIN_WITHDRAW:.2f} ETB.\n\n"
        "📢 <b>Ads & Promotions</b>\n"
        "Contact Admin for advertising and promotions.",
        parse_mode="HTML",
    )


# ============================================================
# ADMIN NOTIFICATION
# ============================================================

async def notify_admins(
    context,
    text,
):

    for admin_id in ADMIN_IDS:

        try:

            await context.bot.send_message(
                chat_id=admin_id,
                text=text,
                parse_mode="HTML",
            )

        except Exception as e:

            logger.warning(
                "Admin notification error: %s",
                e,
            )


# ============================================================
# ADMIN DASHBOARD
# ============================================================

async def admin_dashboard(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    if update.effective_user.id not in ADMIN_IDS:
        return

    conn = db()

    total_users = conn.execute(
        "SELECT COUNT(*) FROM users"
    ).fetchone()[0]

    verified = conn.execute(
        """
        SELECT COUNT(*)
        FROM users
        WHERE verified = 1
        """
    ).fetchone()[0]

    total_balance = conn.execute(
        """
        SELECT COALESCE(SUM(balance), 0)
        FROM users
        """
    ).fetchone()[0]

    pending = conn.execute(
        """
        SELECT COUNT(*)
        FROM withdrawals
        WHERE status = 'PENDING'
        """
    ).fetchone()[0]

    suspicious = conn.execute(
        """
        SELECT COUNT(*)
        FROM users
        WHERE suspicious = 1
        """
    ).fetchone()[0]

    pending_tasks = conn.execute(
        """
        SELECT COUNT(*)
        FROM task_submissions
        WHERE status = 'PENDING'
        """
    ).fetchone()[0]

    conn.close()

    text = (
        "⚙️ <b>FALCON WORLD ADMIN PANEL</b>\n\n"
        f"👥 Total Users: <b>{total_users}</b>\n"
        f"✅ Verified: <b>{verified}</b>\n"
        f"💰 User Balance: <b>{total_balance:.2f} ETB</b>\n"
        f"⏳ Pending Withdrawals: <b>{pending}</b>\n"
        f"📋 Pending Tasks: <b>{pending_tasks}</b>\n"
        f"🚨 Suspicious Users: <b>{suspicious}</b>\n\n"
        "<b>Commands</b>\n"
        "/addtask Title | Link | Reward\n"
        "/checkuser USER_ID\n"
        "/ban USER_ID\n"
        "/unban USER_ID\n"
        "/users\n"
        "/settings"
    )

    await update.message.reply_text(
        text,
        parse_mode="HTML",
    )


# ============================================================
# ADMIN USERS
# ============================================================

async def admin_users(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    if update.effective_user.id not in ADMIN_IDS:
        return

    conn = db()

    total = conn.execute(
        "SELECT COUNT(*) FROM users"
    ).fetchone()[0]

    verified = conn.execute(
        """
        SELECT COUNT(*)
        FROM users
        WHERE verified = 1
        """
    ).fetchone()[0]

    banned = conn.execute(
        """
        SELECT COUNT(*)
        FROM users
        WHERE is_banned = 1
        """
    ).fetchone()[0]

    conn.close()

    await update.message.reply_text(
        "👥 <b>USERS</b>\n\n"
        f"Total: <b>{total}</b>\n"
        f"Verified: <b>{verified}</b>\n"
        f"Banned: <b>{banned}</b>",
        parse_mode="HTML",
    )


# ============================================================
# ADMIN CHECK USER
# ============================================================

async def admin_check_user(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    if update.effective_user.id not in ADMIN_IDS:
        return

    if not context.args:

        await update.message.reply_text(
            "Usage:\n"
            "<code>/checkuser USER_ID</code>",
            parse_mode="HTML",
        )

        return

    try:
        target_id = int(context.args[0])
    except Exception:

        await update.message.reply_text(
            "❌ Invalid User ID."
        )

        return

    user = get_user(target_id)

    if not user:

        await update.message.reply_text(
            "❌ User not found."
        )

        return

    conn = db()

    referrals = conn.execute(
        """
        SELECT COUNT(*)
        FROM referrals
        WHERE referrer_id = ?
        """,
        (target_id,),
    ).fetchone()[0]

    withdrawals = conn.execute(
        """
        SELECT COUNT(*)
        FROM withdrawals
        WHERE user_id = ?
        """,
        (target_id,),
    ).fetchone()[0]

    conn.close()

    username = (
        f"@{html.escape(user['username'])}"
        if user["username"]
        else "N/A"
    )

    text = (
        "👤 <b>USER AUDIT</b>\n\n"
        f"🆔 ID: <code>{user['user_id']}</code>\n"
        f"👤 Name: {html.escape(user['full_name'])}\n"
        f"Username: {username}\n"
        f"💰 Balance: <b>{user['balance']:.2f} ETB</b>\n"
        f"👥 Referrals: <b>{referrals}</b>\n"
        f"🔻 Withdrawals: <b>{withdrawals}</b>\n"
        f"🏦 CBE: <code>{user['cbe_account'] or 'None'}</code>\n"
        f"📱 Telebirr: <code>{user['telebirr_account'] or 'None'}</code>\n"
        f"🚨 Suspicious: <b>{user['suspicious']}</b>\n"
        f"⛔ Banned: <b>{user['is_banned']}</b>\n"
        f"✅ Verified: <b>{user['verified']}</b>"
    )

    await update.message.reply_text(
        text,
        parse_mode="HTML",
    )


# ============================================================
# ADMIN BAN
# ============================================================

async def admin_ban(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    if update.effective_user.id not in ADMIN_IDS:
        return

    if not context.args:
        await update.message.reply_text(
            "Usage: /ban USER_ID"
        )
        return

    try:
        user_id = int(context.args[0])
    except Exception:
        await update.message.reply_text(
            "❌ Invalid User ID."
        )
        return

    conn = db()

    conn.execute(
        """
        UPDATE users
        SET is_banned = 1
        WHERE user_id = ?
        """,
        (user_id,),
    )

    conn.commit()
    conn.close()

    await update.message.reply_text(
        f"⛔ User <code>{user_id}</code> banned.",
        parse_mode="HTML",
    )


# ============================================================
# ADMIN UNBAN
# ============================================================

async def admin_unban(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    if update.effective_user.id not in ADMIN_IDS:
        return

    if not context.args:
        await update.message.reply_text(
            "Usage: /unban USER_ID"
        )
        return

    try:
        user_id = int(context.args[0])
    except Exception:
        await update.message.reply_text(
            "❌ Invalid User ID."
        )
        return

    conn = db()

    conn.execute(
        """
        UPDATE users
        SET is_banned = 0
        WHERE user_id = ?
        """,
        (user_id,),
    )

    conn.commit()
    conn.close()

    await update.message.reply_text(
        f"✅ User <code>{user_id}</code> unbanned.",
        parse_mode="HTML",
    )


# ============================================================
# ADMIN ADD TASK
# ============================================================

async def admin_add_task(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    if update.effective_user.id not in ADMIN_IDS:
        return

    try:

        raw = update.message.text.split(
            " ",
            1,
        )[1]

        title, link, reward = [
            x.strip()
            for x in raw.split("|")
        ]

        reward = float(reward)

        if reward <= 0:
            raise ValueError

    except Exception:

        await update.message.reply_text(
            "❌ <b>Wrong format</b>\n\n"
            "<code>/addtask Join Channel | https://t.me/example | 2</code>",
            parse_mode="HTML",
        )

        return

    conn = db()

    conn.execute(
        """
        INSERT INTO tasks (
            title,
            link,
            reward
        )
        VALUES (?, ?, ?)
        """,
        (
            title,
            link,
            reward,
        ),
    )

    conn.commit()
    conn.close()

    await update.message.reply_text(
        "✅ <b>Task Created</b>\n\n"
        f"📌 {html.escape(title)}\n"
        f"💰 {reward:.2f} ETB\n"
        f"🔗 {html.escape(link)}",
        parse_mode="HTML",
    )


# ============================================================
# ADMIN SETTINGS
# ============================================================

async def admin_settings(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    if update.effective_user.id not in ADMIN_IDS:
        return

    daily = get_setting(
        "daily_bonus",
        DAILY_BONUS,
    )

    referral = get_setting(
        "referral_reward",
        REFERRAL_REWARD,
    )

    minimum = get_setting(
        "min_withdraw",
        MIN_WITHDRAW,
    )

    await update.message.reply_text(
        "⚙️ <b>BOT SETTINGS</b>\n\n"
        f"🎁 Daily Bonus: <b>{daily:.2f} ETB</b>\n"
        f"👥 Referral Reward: <b>{referral:.2f} ETB</b>\n"
        f"🔻 Minimum Withdrawal: <b>{minimum:.2f} ETB</b>",
        parse_mode="HTML",
    )


# ============================================================
# ADMIN CALLBACKS
# ============================================================

async def handle_admin_callbacks(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    query = update.callback_query

    # VERY IMPORTANT:
    # Only configured admins may execute admin callbacks.
    if query.from_user.id not in ADMIN_IDS:

        await query.answer(
            "⛔ Admin only.",
            show_alert=True,
        )

        return

    await query.answer()

    data = query.data

    conn = db()

    try:

        # ----------------------------------------------------
        # APPROVE WITHDRAWAL
        # ----------------------------------------------------

        if data.startswith("adm_appr_"):

            withdrawal_id = int(
                data.split("_")[2]
            )

            row = conn.execute(
                """
                SELECT user_id, amount, status
                FROM withdrawals
                WHERE id = ?
                """,
                (withdrawal_id,),
            ).fetchone()

            if not row:

                await query.edit_message_text(
                    "❌ Withdrawal not found."
                )

                return

            if row["status"] != "PENDING":

                await query.answer(
                    "Already processed.",
                    show_alert=True,
                )

                return

            conn.execute(
                """
                UPDATE withdrawals
                SET status = 'APPROVED'
                WHERE id = ?
                """,
                (withdrawal_id,),
            )

            conn.commit()

            await query.edit_message_text(
                query.message.text +
                "\n\n✅ <b>APPROVED</b>",
                parse_mode="HTML",
            )

            try:

                await context.bot.send_message(
                    chat_id=row["user_id"],
                    text=(
                        "🎉 <b>Withdrawal Approved!</b>\n\n"
                        f"💰 Amount: <b>{row['amount']:.2f} ETB</b>\n"
                        "✅ Your payout has been approved."
                    ),
                    parse_mode="HTML",
                )

            except Exception:
                pass

        # ----------------------------------------------------
        # REJECT WITH REFUND
        # ----------------------------------------------------

        elif data.startswith("adm_rej_"):

            withdrawal_id = int(
                data.split("_")[2]
            )

            row = conn.execute(
                """
                SELECT user_id, amount, status
                FROM withdrawals
                WHERE id = ?
                """,
                (withdrawal_id,),
            ).fetchone()

            if not row:

                await query.edit_message_text(
                    "❌ Withdrawal not found."
                )

                return

            if row["status"] != "PENDING":

                await query.answer(
                    "Already processed.",
                    show_alert=True,
                )

                return

            conn.execute(
                """
                UPDATE withdrawals
                SET status = 'REJECTED'
                WHERE id = ?
                """,
                (withdrawal_id,),
            )

            conn.execute(
                """
                UPDATE users
                SET balance = balance + ?
                WHERE user_id = ?
                """,
                (
                    row["amount"],
                    row["user_id"],
                ),
            )

            conn.commit()

            await query.edit_message_text(
                query.message.text +
                "\n\n❌ <b>REJECTED & REFUNDED</b>",
                parse_mode="HTML",
            )

            try:

                await context.bot.send_message(
                    chat_id=row["user_id"],
                    text=(
                        "❌ <b>Withdrawal Rejected</b>\n\n"
                        f"💰 {row['amount']:.2f} ETB "
                        "has been returned to your balance."
                    ),
                    parse_mode="HTML",
                )

            except Exception:
                pass

        # ----------------------------------------------------
        # BAN USER
        # ----------------------------------------------------

        elif data.startswith("adm_ban_"):

            user_id = int(
                data.split("_")[2]
            )

            conn.execute(
                """
                UPDATE users
                SET is_banned = 1
                WHERE user_id = ?
                """,
                (user_id,),
            )

            conn.commit()

            await query.edit_message_text(
                query.message.text +
                f"\n\n⛔ <b>USER {user_id} BANNED</b>",
                parse_mode="HTML",
            )

            try:

                await context.bot.send_message(
                    chat_id=user_id,
                    text=(
                        "⛔ <b>Your Falcon World account "
                        "has been suspended.</b>"
                    ),
                    parse_mode="HTML",
                )

            except Exception:
                pass

        # ----------------------------------------------------
        # APPROVE TASK
        # ----------------------------------------------------

        elif data.startswith("tappr_"):

            submission_id = int(
                data.split("_")[1]
            )

            row = conn.execute(
                """
                SELECT
                    ts.user_id,
                    ts.status,
                    t.reward,
                    t.title
                FROM task_submissions ts
                JOIN tasks t
                    ON ts.task_id = t.id
                WHERE ts.id = ?
                """,
                (submission_id,),
            ).fetchone()

            if not row:

                await query.edit_message_text(
                    "❌ Submission not found."
                )

                return

            if row["status"] != "PENDING":

                await query.answer(
                    "Already processed.",
                    show_alert=True,
                )

                return

            conn.execute(
                """
                UPDATE task_submissions
                SET status = 'APPROVED'
                WHERE id = ?
                """,
                (submission_id,),
            )

            conn.execute(
                """
                UPDATE users
                SET balance = balance + ?
                WHERE user_id = ?
                """,
                (
                    row["reward"],
                    row["user_id"],
                ),
            )

            conn.commit()

            await query.edit_message_text(
                query.message.text +
                "\n\n✅ <b>APPROVED & REWARDED</b>",
                parse_mode="HTML",
            )

            try:

                await context.bot.send_message(
                    chat_id=row["user_id"],
                    text=(
                        "🎉 <b>Task Approved!</b>\n\n"
                        f"📌 {html.escape(row['title'])}\n"
                        f"💰 <b>+{row['reward']:.2f} ETB</b>"
                    ),
                    parse_mode="HTML",
                )

            except Exception:
                pass

        # ----------------------------------------------------
        # REJECT TASK
        # ----------------------------------------------------

        elif data.startswith("trej_"):

            submission_id = int(
                data.split("_")[1]
            )

            row = conn.execute(
                """
                SELECT user_id, status
                FROM task_submissions
                WHERE id = ?
                """,
                (submission_id,),
            ).fetchone()

            if not row:

                await query.edit_message_text(
                    "❌ Submission not found."
                )

                return

            if row["status"] != "PENDING":

                await query.answer(
                    "Already processed.",
                    show_alert=True,
                )

                return

            conn.execute(
                """
                UPDATE task_submissions
                SET status = 'REJECTED'
                WHERE id = ?
                """,
                (submission_id,),
            )

            conn.commit()

            await query.edit_message_text(
                query.message.text +
                "\n\n❌ <b>REJECTED</b>",
                parse_mode="HTML",
            )

            try:

                await context.bot.send_message(
                    chat_id=row["user_id"],
                    text=(
                        "❌ <b>Task Rejected</b>\n\n"
                        "Your submitted proof was not approved."
                    ),
                    parse_mode="HTML",
                )

            except Exception:
                pass

    except Exception as e:

        logger.exception(
            "Admin callback error"
        )

        try:

            await query.answer(
                "An error occurred.",
                show_alert=True,
            )

        except Exception:
            pass

    finally:

        conn.close()


# ============================================================
# UNKNOWN TEXT
# ============================================================

async def unknown_text(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    if not update.message:
        return

    await update.message.reply_text(
        "🦅 <b>Falcon World</b>\n\n"
        "Please use the menu buttons below.",
        reply_markup=main_keyboard(),
        parse_mode="HTML",
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

    if not BOT_TOKEN:

        raise RuntimeError(
            "BOT_TOKEN environment variable is missing."
        )

    if not ADMIN_IDS:

        raise RuntimeError(
            "ADMIN_IDS environment variable is missing."
        )

    application = (
        Application
        .builder()
        .token(BOT_TOKEN)
        .build()
    )

    # --------------------------------------------------------
    # START + CAPTCHA
    # --------------------------------------------------------

    captcha_conversation = ConversationHandler(
        entry_points=[
            CommandHandler(
                "start",
                start,
            )
        ],

        states={
            CAPTCHA: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    verify_captcha,
                )
            ]
        },

        fallbacks=[],

        allow_reentry=True,
    )

    # --------------------------------------------------------
    # WALLET
    # --------------------------------------------------------

    wallet_conversation = ConversationHandler(
        entry_points=[
            MessageHandler(
                filters.Regex(
                    r"^💳 Wallet Settings$"
                ),
                start_wallet_setup,
            )
        ],

        states={
            WALLET_INPUT: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    save_wallet_input,
                )
            ]
        },

        fallbacks=[],
    )

    # --------------------------------------------------------
    # TASK PROOF
    # --------------------------------------------------------

    task_conversation = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(
                process_task_click,
                pattern=r"^dotask_\d+$",
            )
        ],

        states={
            TASK_PROOF: [
                MessageHandler(
                    filters.TEXT | filters.PHOTO,
                    handle_task_proof_submission,
                )
            ]
        },

        fallbacks=[],
    )

    # --------------------------------------------------------
    # Add conversations FIRST
    # --------------------------------------------------------

    application.add_handler(
        captcha_conversation
    )

    application.add_handler(
        wallet_conversation
    )

    application.add_handler(
        task_conversation
    )

    # --------------------------------------------------------
    # Main Menu
    # --------------------------------------------------------

    application.add_handler(
        MessageHandler(
            filters.Regex(r"^💰 Balance$"),
            handle_balance,
        )
    )

    application.add_handler(
        MessageHandler(
            filters.Regex(r"^🎁 Daily Bonus$"),
            handle_daily_bonus,
        )
    )

    application.add_handler(
        MessageHandler(
            filters.Regex(r"^👥 Invite Friends$"),
            handle_invite,
        )
    )

    application.add_handler(
        MessageHandler(
            filters.Regex(r"^📋 Tasks$"),
            handle_tasks_list,
        )
    )

    application.add_handler(
        MessageHandler(
            filters.Regex(r"^🔻 Withdraw$"),
            handle_withdraw_request,
        )
    )

    application.add_handler(
        MessageHandler(
            filters.Regex(r"^📊 Statistics$"),
            handle_stats,
        )
    )

    application.add_handler(
        MessageHandler(
            filters.Regex(r"^❓ Help$"),
            handle_help,
        )
    )

    # --------------------------------------------------------
    # Membership
    # --------------------------------------------------------

    application.add_handler(
        CallbackQueryHandler(
            callback_verify_membership,
            pattern=r"^verify_membership$",
        )
    )

    # --------------------------------------------------------
    # Wallet callbacks
    # --------------------------------------------------------

    application.add_handler(
        CallbackQueryHandler(
            handle_wallet_choice,
            pattern=r"^set_wallet_(cbe|telebirr)$",
        )
    )

    # --------------------------------------------------------
    # Withdrawal callbacks
    # --------------------------------------------------------

    application.add_handler(
        CallbackQueryHandler(
            process_withdrawal_selection,
            pattern=r"^withdraw_(cbe|telebirr)$",
        )
    )

    application.add_handler(
        CallbackQueryHandler(
            confirm_withdraw,
            pattern=r"^confirm_withdraw$",
        )
    )

    application.add_handler(
        CallbackQueryHandler(
            cancel_withdraw,
            pattern=r"^cancel_withdraw$",
        )
    )

    # --------------------------------------------------------
    # Admin callbacks
    # --------------------------------------------------------

    application.add_handler(
        CallbackQueryHandler(
            handle_admin_callbacks,
            pattern=r"^(adm_appr_|adm_rej_|adm_ban_|tappr_|trej_)",
        )
    )

    # --------------------------------------------------------
    # Admin commands
    # --------------------------------------------------------

    application.add_handler(
        CommandHandler(
            "admin",
            admin_dashboard,
        )
    )

    application.add_handler(
        CommandHandler(
            "users",
            admin_users,
        )
    )

    application.add_handler(
        CommandHandler(
            "checkuser",
            admin_check_user,
        )
    )

    application.add_handler(
        CommandHandler(
            "addtask",
            admin_add_task,
        )
    )

    application.add_handler(
        CommandHandler(
            "ban",
            admin_ban,
        )
    )

    application.add_handler(
        CommandHandler(
            "unban",
            admin_unban,
        )
    )

    application.add_handler(
        CommandHandler(
            "settings",
            admin_settings,
        )
    )

    # --------------------------------------------------------
    # Unknown text
    # --------------------------------------------------------

    application.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            unknown_text,
        )
    )

    # --------------------------------------------------------
    # Error handler
    # --------------------------------------------------------

    application.add_error_handler(
        error_handler
    )

    logger.info(
        "🦅 Falcon World is starting..."
    )

    application.run_polling(
        drop_pending_updates=True
    )


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":
    main()

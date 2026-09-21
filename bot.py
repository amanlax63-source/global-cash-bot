import os
import sqlite3
import logging
from typing import Optional

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ChatMemberStatus
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

# =========================================================
# GLOBAL CASH BOT
# =========================================================

BOT_NAME = "Global Cash Bot"
BOT_USERNAME = "GloballCashh_Bot"
SUPPORT_USERNAME = "AmanM_12"

REQUIRED_CHANNELS = [
    ("@Sheger_tech1", "Sheger Tech"),
    ("@EthioVortex1", "Ethio Vortex"),
    ("@ethiocashflow", "Ethio Cash Flow"),
    ("@AmanIncomeLab", "Aman Income Lab"),
    ("@OnlineIncomeHub07", "Online Income Hub"),
    ("@Paymentprooff2", "Payment Proof"),
]

REFERRAL_REWARD = 2.0
MIN_WITHDRAW = 30.0
CURRENCY = "ETB"

DB_FILE = "global_cash.db"

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)

logger = logging.getLogger(__name__)


# =========================================================
# DATABASE
# =========================================================

def db():
    return sqlite3.connect(DB_FILE)


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
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS withdrawals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            amount REAL,
            wallet_type TEXT,
            wallet_number TEXT,
            status TEXT DEFAULT 'pending',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # -----------------------------------------------------
    # Database migration
    # -----------------------------------------------------

    existing_columns = set()

    cur.execute("PRAGMA table_info(users)")

    for row in cur.fetchall():
        existing_columns.add(row[1])

    if "referral_status" not in existing_columns:
        cur.execute("""
            ALTER TABLE users
            ADD COLUMN referral_status TEXT DEFAULT 'pending'
        """)

    if "suspicious" not in existing_columns:
        cur.execute("""
            ALTER TABLE users
            ADD COLUMN suspicious INTEGER DEFAULT 0
        """)

    conn.commit()

    # -----------------------------------------------------
    # Unique wallet protection
    # -----------------------------------------------------

    try:
        cur.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS idx_unique_wallet
            ON users(wallet_type, wallet_number)
            WHERE wallet_type IS NOT NULL
              AND wallet_number IS NOT NULL
              AND wallet_number != ''
        """)

        conn.commit()

    except sqlite3.IntegrityError:

        logger.warning(
            "Duplicate wallets already exist in old database. "
            "Manual wallet protection will still work."
        )

    conn.close()


# =========================================================
# USER FUNCTIONS
# =========================================================

def get_user(user_id: int):
    conn = db()
    cur = conn.cursor()

    cur.execute("""
        SELECT *
        FROM users
        WHERE user_id = ?
    """, (user_id,))

    user = cur.fetchone()

    conn.close()

    return user


def create_user(
    user_id: int,
    username: str,
    first_name: str,
    referred_by: Optional[int] = None
):
    conn = db()
    cur = conn.cursor()

    cur.execute("""
        INSERT OR IGNORE INTO users
        (
            user_id,
            username,
            first_name,
            referred_by
        )
        VALUES (?, ?, ?, ?)
    """, (
        user_id,
        username,
        first_name,
        referred_by
    ))

    conn.commit()
    conn.close()


def update_user_info(
    user_id: int,
    username: str,
    first_name: str
):
    conn = db()
    cur = conn.cursor()

    cur.execute("""
        UPDATE users
        SET username = ?,
            first_name = ?
        WHERE user_id = ?
    """, (
        username,
        first_name,
        user_id
    ))

    conn.commit()
    conn.close()


def get_balance(user_id: int) -> float:
    conn = db()
    cur = conn.cursor()

    cur.execute("""
        SELECT balance
        FROM users
        WHERE user_id = ?
    """, (user_id,))

    row = cur.fetchone()

    conn.close()

    if not row:
        return 0.0

    return float(row[0])


def add_balance(
    user_id: int,
    amount: float
):
    conn = db()
    cur = conn.cursor()

    cur.execute("""
        UPDATE users
        SET balance = balance + ?
        WHERE user_id = ?
    """, (
        amount,
        user_id
    ))

    conn.commit()
    conn.close()


def set_joined_all(
    user_id: int,
    value: int
):
    conn = db()
    cur = conn.cursor()

    cur.execute("""
        UPDATE users
        SET joined_all = ?
        WHERE user_id = ?
    """, (
        value,
        user_id
    ))

    conn.commit()
    conn.close()


def mark_suspicious(user_id: int):
    conn = db()
    cur = conn.cursor()

    cur.execute("""
        UPDATE users
        SET suspicious = 1
        WHERE user_id = ?
    """, (user_id,))

    conn.commit()
    conn.close()


def is_suspicious(user_id: int) -> bool:
    conn = db()
    cur = conn.cursor()

    cur.execute("""
        SELECT suspicious
        FROM users
        WHERE user_id = ?
    """, (user_id,))

    row = cur.fetchone()

    conn.close()

    return bool(row and row[0])


# =========================================================
# WALLET
# =========================================================

def get_wallet(user_id: int):
    conn = db()
    cur = conn.cursor()

    cur.execute("""
        SELECT wallet_type,
               wallet_number
        FROM users
        WHERE user_id = ?
    """, (user_id,))

    row = cur.fetchone()

    conn.close()

    return row


def wallet_exists_for_other_user(
    wallet_type: str,
    wallet_number: str,
    user_id: int
) -> bool:

    conn = db()
    cur = conn.cursor()

    cur.execute("""
        SELECT user_id
        FROM users
        WHERE wallet_type = ?
          AND wallet_number = ?
          AND user_id != ?
        LIMIT 1
    """, (
        wallet_type,
        wallet_number,
        user_id
    ))

    row = cur.fetchone()

    conn.close()

    return row is not None


def set_wallet(
    user_id: int,
    wallet_type: str,
    wallet_number: str
) -> bool:

    if wallet_exists_for_other_user(
        wallet_type,
        wallet_number,
        user_id
    ):
        mark_suspicious(user_id)
        return False

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
            user_id
        ))

        conn.commit()

        return True

    except sqlite3.IntegrityError:

        conn.rollback()

        mark_suspicious(user_id)

        return False

    finally:

        conn.close()


# =========================================================
# REFERRAL
# =========================================================

def get_referral_count(user_id: int) -> int:

    conn = db()
    cur = conn.cursor()

    cur.execute("""
        SELECT COUNT(*)
        FROM users
        WHERE referred_by = ?
          AND referral_paid = 1
    """, (user_id,))

    count = cur.fetchone()[0]

    conn.close()

    return count


def process_referral_reward(user_id: int):

    conn = db()
    cur = conn.cursor()

    cur.execute("""
        SELECT referred_by,
               referral_paid,
               referral_status,
               suspicious
        FROM users
        WHERE user_id = ?
    """, (user_id,))

    row = cur.fetchone()

    if not row:
        conn.close()
        return None

    referred_by = row[0]
    referral_paid = row[1]
    referral_status = row[2]
    suspicious = row[3]

    # Already paid
    if referral_paid:
        conn.close()
        return None

    # No referrer
    if not referred_by:
        conn.close()
        return None

    # Self referral
    if int(referred_by) == int(user_id):

        cur.execute("""
            UPDATE users
            SET suspicious = 1,
                referral_status = 'blocked'
            WHERE user_id = ?
        """, (user_id,))

        conn.commit()
        conn.close()

        return None

    # Suspicious referred account
    if suspicious:

        cur.execute("""
            UPDATE users
            SET referral_status = 'blocked'
            WHERE user_id = ?
        """, (user_id,))

        conn.commit()
        conn.close()

        return None

    # Check inviter
    cur.execute("""
        SELECT user_id,
               suspicious
        FROM users
        WHERE user_id = ?
    """, (referred_by,))

    inviter = cur.fetchone()

    if not inviter:
        conn.close()
        return None

    # Suspicious inviter
    if inviter[1]:

        cur.execute("""
            UPDATE users
            SET referral_status = 'blocked'
            WHERE user_id = ?
        """, (user_id,))

        conn.commit()
        conn.close()

        return None

    # Give reward
    cur.execute("""
        UPDATE users
        SET balance = balance + ?
        WHERE user_id = ?
    """, (
        REFERRAL_REWARD,
        referred_by
    ))

    # Mark referral paid
    cur.execute("""
        UPDATE users
        SET referral_paid = 1,
            referral_status = 'paid'
        WHERE user_id = ?
    """, (user_id,))

    conn.commit()
    conn.close()

    return referred_by


# =========================================================
# WITHDRAWAL
# =========================================================

def create_withdrawal(
    user_id: int,
    amount: float,
    wallet_type: str,
    wallet_number: str
):

    conn = db()
    cur = conn.cursor()

    cur.execute("""
        INSERT INTO withdrawals
        (
            user_id,
            amount,
            wallet_type,
            wallet_number,
            status
        )
        VALUES (?, ?, ?, ?, 'pending')
    """, (
        user_id,
        amount,
        wallet_type,
        wallet_number
    ))

    cur.execute("""
        UPDATE users
        SET balance = balance - ?
        WHERE user_id = ?
    """, (
        amount,
        user_id
    ))

    conn.commit()
    conn.close()


# =========================================================
# CHANNEL VERIFICATION
# =========================================================

async def check_channel_membership(
    context: ContextTypes.DEFAULT_TYPE,
    user_id: int,
    channel: str
) -> bool:

    try:

        member = await context.bot.get_chat_member(
            chat_id=channel,
            user_id=user_id
        )

        return member.status in [
            ChatMemberStatus.MEMBER,
            ChatMemberStatus.ADMINISTRATOR,
            ChatMemberStatus.OWNER,
        ]

    except Exception as e:

        logger.warning(
            "Could not check %s for %s: %s",
            channel,
            user_id,
            e
        )

        return False


async def get_missing_channels(
    context: ContextTypes.DEFAULT_TYPE,
    user_id: int
):

    missing = []

    for username, name in REQUIRED_CHANNELS:

        joined = await check_channel_membership(
            context,
            user_id,
            username
        )

        if not joined:
            missing.append(
                (username, name)
            )

    return missing


def channel_keyboard(missing_channels):

    buttons = []

    for username, name in missing_channels:

        buttons.append([
            InlineKeyboardButton(
                f"📢 {name}",
                url=f"https://t.me/{username.replace('@', '')}"
            )
        ])

    buttons.append([
        InlineKeyboardButton(
            "✅ Verify",
            callback_data="verify"
        )
    ])

    return InlineKeyboardMarkup(buttons)


# =========================================================
# MAIN MENU
# =========================================================

def main_keyboard():

    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "💰 Balance",
                callback_data="balance"
            ),
            InlineKeyboardButton(
                "👥 Referral",
                callback_data="referral"
            ),
        ],
        [
            InlineKeyboardButton(
                "📋 Tasks",
                callback_data="tasks"
            ),
            InlineKeyboardButton(
                "💳 Withdraw",
                callback_data="withdraw"
            ),
        ],
        [
            InlineKeyboardButton(
                "👛 Wallet",
                callback_data="wallet"
            ),
            InlineKeyboardButton(
                "🆘 Support",
                callback_data="support"
            ),
        ],
    ])


async def send_main_menu(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    text = (
        f"🎉 <b>{BOT_NAME}</b>\n\n"
        "✅ <b>Account Verified</b>\n\n"
        "Global Cash မှာ earning መጀመር ይችላሉ።\n"
        "Referral እና available tasks በመጠቀም "
        "balance ያሳድጉ።\n\n"
        "👇 <b>ከታች የሚፈልጉትን ይምረጡ።</b>"
    )

    if update.callback_query:

        await update.callback_query.edit_message_text(
            text,
            reply_markup=main_keyboard(),
            parse_mode="HTML"
        )

    else:

        await update.message.reply_text(
            text,
            reply_markup=main_keyboard(),
            parse_mode="HTML"
        )


# =========================================================
# START
# =========================================================

async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    user = update.effective_user

    referred_by = None

    # -----------------------------------------------------
    # Referral link
    # -----------------------------------------------------

    if context.args:

        try:

            ref_id = int(
                context.args[0]
            )

            if ref_id != user.id:
                referred_by = ref_id

        except ValueError:

            pass

    # -----------------------------------------------------
    # Existing user protection
    # -----------------------------------------------------

    existing_user = get_user(
        user.id
    )

    if existing_user:

        referred_by = existing_user[4]

    # -----------------------------------------------------
    # Create/update user
    # -----------------------------------------------------

    create_user(
        user.id,
        user.username or "",
        user.first_name or "",
        referred_by
    )

    update_user_info(
        user.id,
        user.username or "",
        user.first_name or ""
    )

    # -----------------------------------------------------
    # Channel verification
    # -----------------------------------------------------

    missing = await get_missing_channels(
        context,
        user.id
    )

    if missing:

        text = (
            f"👋 <b>Welcome to {BOT_NAME}</b>\n\n"
            "💰 Earning system ለመጀመር "
            "ከታች ያሉትን channels በሙሉ Join ያድርጉ።\n\n"
            "📌 ሁሉንም ከጨረሱ በኋላ "
            "<b>Verify</b> ይጫኑ።"
        )

        await update.message.reply_text(
            text,
            reply_markup=channel_keyboard(missing),
            parse_mode="HTML"
        )

        return

    set_joined_all(
        user.id,
        1
    )

    await send_main_menu(
        update,
        context
    )


# =========================================================
# VERIFY
# =========================================================

async def verify(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    query = update.callback_query

    await query.answer()

    user_id = query.from_user.id

    missing = await get_missing_channels(
        context,
        user_id
    )

    if missing:

        names = "\n".join(
            f"❌ {name}"
            for _, name in missing
        )

        text = (
            "⚠️ <b>Verification Failed</b>\n\n"
            "እነዚህ channels ገና አልተጠናቀቁም፦\n\n"
            f"{names}\n\n"
            "Join ካደረጉ በኋላ "
            "<b>Verify</b> እንደገና ይጫኑ።"
        )

        await query.edit_message_text(
            text,
            reply_markup=channel_keyboard(missing),
            parse_mode="HTML"
        )

        return

    # -----------------------------------------------------
    # Verified
    # -----------------------------------------------------

    set_joined_all(
        user_id,
        1
    )

    inviter = process_referral_reward(
        user_id
    )

    # -----------------------------------------------------
    # Referral notification
    # -----------------------------------------------------

    if inviter:

        try:

            await context.bot.send_message(
                chat_id=inviter,
                text=(
                    "🎉 <b>Referral Reward!</b>\n\n"
                    "የጋበዙት user verification ጨርሷል።\n\n"
                    f"💰 <b>+{REFERRAL_REWARD:.2f} ETB</b>\n"
                    "ወደ balance ተጨምሯል።"
                ),
                parse_mode="HTML"
            )

        except Exception as e:

            logger.warning(
                "Referral notification failed: %s",
                e
            )

    await query.edit_message_text(
        "✅ <b>Verified Successfully!</b>\n\n"
        "🎉 እንኳን ወደ Global Cash Bot በደህና መጡ።",
        parse_mode="HTML"
    )

    await context.bot.send_message(
        chat_id=user_id,
        text="👇 <b>Main Menu</b>",
        reply_markup=main_keyboard(),
        parse_mode="HTML"
    )


# =========================================================
# BALANCE
# =========================================================

async def show_balance(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    query = update.callback_query

    await query.answer()

    balance = get_balance(
        query.from_user.id
    )

    text = (
        "💰 <b>Your Balance</b>\n\n"
        f"💵 Available: <b>{balance:.2f} ETB</b>\n\n"
        f"📌 Minimum Withdrawal: "
        f"<b>{MIN_WITHDRAW:.2f} ETB</b>\n\n"
        "Referral እና available activities በመጠቀም "
        "balance ማሳደግ ይችላሉ።"
    )

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "💳 Withdraw",
                callback_data="withdraw"
            )
        ],
        [
            InlineKeyboardButton(
                "🔙 Back",
                callback_data="home"
            )
        ],
    ])

    await query.edit_message_text(
        text,
        reply_markup=keyboard,
        parse_mode="HTML"
    )


# =========================================================
# REFERRAL
# =========================================================

async def show_referral(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    query = update.callback_query

    await query.answer()

    user_id = query.from_user.id

    count = get_referral_count(
        user_id
    )

    me = await context.bot.get_me()

    referral_link = (
        f"https://t.me/{me.username}"
        f"?start={user_id}"
    )

    text = (
        "👥 <b>Referral Program</b>\n\n"
        f"👤 Successful Referrals: <b>{count}</b>\n"
        f"💰 እያንዳንዱ successful referral: "
        f"<b>{REFERRAL_REWARD:.2f} ETB</b>\n\n"
        "🔗 <b>Your Referral Link</b>\n"
        f"<code>{referral_link}</code>\n\n"
        "📌 የጋበዙት user channels በሙሉ Join "
        "አድርጎ Verify ካደረገ በኋላ reward ይጨመራል።"
    )

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "📤 Share Link",
                url=(
                    "https://t.me/share/url"
                    f"?url={referral_link}"
                    "&text=Join%20Global%20Cash%20Bot"
                )
            )
        ],
        [
            InlineKeyboardButton(
                "🔙 Back",
                callback_data="home"
            )
        ],
    ])

    await query.edit_message_text(
        text,
        reply_markup=keyboard,
        parse_mode="HTML"
    )


# =========================================================
# TASKS
# =========================================================

async def show_tasks(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    query = update.callback_query

    await query.answer()

    text = (
        "📋 <b>Tasks</b>\n\n"
        "🎯 <b>Available Task</b>\n\n"
        "👥 Friends ይጋብዙ እና "
        f"በእያንዳንዱ successful referral "
        f"<b>{REFERRAL_REWARD:.2f} ETB</b> ያግኙ።\n\n"
        "🚀 More earning tasks will be added soon."
    )

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "👥 Referral",
                callback_data="referral"
            )
        ],
        [
            InlineKeyboardButton(
                "🔙 Back",
                callback_data="home"
            )
        ],
    ])

    await query.edit_message_text(
        text,
        reply_markup=keyboard,
        parse_mode="HTML"
    )


# =========================================================
# WALLET
# =========================================================

async def show_wallet(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    query = update.callback_query

    await query.answer()

    wallet = get_wallet(
        query.from_user.id
    )

    if wallet and wallet[0] and wallet[1]:

        text = (
            "👛 <b>Your Wallet</b>\n\n"
            f"💳 Type: <b>{wallet[0]}</b>\n"
            f"🔢 Number: <code>{wallet[1]}</code>\n\n"
            "Wallet ለመቀየር ከታች ያለውን "
            "payment method ይምረጡ።"
        )

    else:

        text = (
            "👛 <b>Wallet</b>\n\n"
            "❌ እስካሁን wallet አልተመዘገበም።\n\n"
            "👇 Payment method ይምረጡ።"
        )

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "🏦 CBE",
                callback_data="wallet_cbe"
            ),
            InlineKeyboardButton(
                "📱 Telebirr",
                callback_data="wallet_telebirr"
            ),
        ],
        [
            InlineKeyboardButton(
                "🔙 Back",
                callback_data="home"
            )
        ],
    ])

    await query.edit_message_text(
        text,
        reply_markup=keyboard,
        parse_mode="HTML"
    )


# =========================================================
# CBE WALLET
# =========================================================

async def wallet_cbe(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    query = update.callback_query

    await query.answer()

    context.user_data.clear()
    context.user_data["wallet_setup"] = "CBE"

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "🔙 Back",
                callback_data="wallet"
            )
        ]
    ])

    await query.edit_message_text(
        "🏦 <b>CBE Wallet</b>\n\n"
        "የCBE account number ያስገቡ።\n\n"
        "📌 13 digits መሆን አለበት።\n"
        "📌 <b>1000</b> በሚል መጀመር አለበት።\n\n"
        "ካልፈለጉ <b>Back</b> ይጫኑ።",
        reply_markup=keyboard,
        parse_mode="HTML"
    )


# =========================================================
# TELEBIRR WALLET
# =========================================================

async def wallet_telebirr(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    query = update.callback_query

    await query.answer()

    context.user_data.clear()
    context.user_data["wallet_setup"] = "Telebirr"

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "🔙 Back",
                callback_data="wallet"
            )
        ]
    ])

    await query.edit_message_text(
        "📱 <b>Telebirr Wallet</b>\n\n"
        "የTelebirr phone number ያስገቡ።\n\n"
        "📌 10 digits መሆን አለበት።\n"
        "📌 <b>09</b> ወይም <b>07</b> በሚል መጀመር አለበት።\n\n"
        "ካልፈለጉ <b>Back</b> ይጫኑ።",
        reply_markup=keyboard,
        parse_mode="HTML"
    )


# =========================================================
# WITHDRAW
# =========================================================

async def withdraw(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    query = update.callback_query

    await query.answer()

    user_id = query.from_user.id

    # -----------------------------------------------------
    # Security check
    # -----------------------------------------------------

    if is_suspicious(user_id):

        await query.edit_message_text(
            "🛡️ <b>Security Review</b>\n\n"
            "የእርስዎ account በsecurity review ላይ ነው።\n\n"
            "Withdrawal ለጊዜው unavailable ነው።",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "🆘 Support",
                        callback_data="support"
                    )
                ],
                [
                    InlineKeyboardButton(
                        "🔙 Back",
                        callback_data="home"
                    )
                ],
            ]),
            parse_mode="HTML"
        )

        return

    balance = get_balance(
        user_id
    )

    wallet = get_wallet(
        user_id
    )

    # -----------------------------------------------------
    # Minimum balance
    # -----------------------------------------------------

    if balance < MIN_WITHDRAW:

        await query.edit_message_text(
            "❌ <b>Insufficient Balance</b>\n\n"
            f"💰 Your Balance: <b>{balance:.2f} ETB</b>\n"
            f"💳 Minimum Withdrawal: "
            f"<b>{MIN_WITHDRAW:.2f} ETB</b>\n\n"
            "ተጨማሪ earning ካደረጉ በኋላ "
            "እንደገና ይሞክሩ።",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "👥 Referral",
                        callback_data="referral"
                    )
                ],
                [
                    InlineKeyboardButton(
                        "🔙 Back",
                        callback_data="home"
                    )
                ],
            ]),
            parse_mode="HTML"
        )

        return

    # -----------------------------------------------------
    # Wallet required
    # -----------------------------------------------------

    if not wallet or not wallet[0] or not wallet[1]:

        await query.edit_message_text(
            "👛 <b>Wallet Required</b>\n\n"
            "Withdrawal ለማድረግ መጀመሪያ "
            "CBE ወይም Telebirr wallet ያስቀምጡ።",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "👛 Set Wallet",
                        callback_data="wallet"
                    )
                ],
                [
                    InlineKeyboardButton(
                        "🔙 Back",
                        callback_data="home"
                    )
                ],
            ]),
            parse_mode="HTML"
        )

        return

    context.user_data.clear()
    context.user_data["withdraw_amount"] = True

    await query.edit_message_text(
        "💳 <b>Withdrawal</b>\n\n"
        f"💰 Available: <b>{balance:.2f} ETB</b>\n"
        f"📌 Minimum: <b>{MIN_WITHDRAW:.2f} ETB</b>\n\n"
        "የሚያወጡትን amount በETB ያስገቡ።\n\n"
        "ካልፈለጉ <b>Cancel</b> ይጫኑ።",
        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "❌ Cancel",
                    callback_data="cancel_withdraw"
                )
            ]
        ]),
        parse_mode="HTML"
    )


# =========================================================
# SUPPORT
# =========================================================

async def support(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    query = update.callback_query

    await query.answer()

    text = (
        "🆘 <b>Global Cash Support</b>\n\n"

        "📢 <b>Advertising & Telegram Promotion</b>\n\n"

        "የTelegram Channel ወይም Group "
        "ማስታወቂያ ከፈለጉ፦\n\n"

        "📌 <b>Channel Promotion</b>\n"
        "የTelegram Channel ማስታወቂያ እና "
        "Promotion ከፈለጉ ያነጋግሩን።\n\n"

        "📈 <b>Channel Growth</b>\n"
        "የTelegram Channel አባላትን ማሳደግ "
        "እና Reach ማሻሻል ከፈለጉ ያነጋግሩን።\n\n"

        "📣 <b>Group Advertising</b>\n"
        "Telegram Group ወይም Channel "
        "ማስተዋወቅ ከፈለጉ ያነጋግሩን።\n\n"

        "💼 <b>Business Promotion</b>\n"
        "Business፣ Product ወይም Service "
        "በTelegram ላይ ለማስተዋወቅ ያነጋግሩን።\n\n"

        "📱 <b>App & Website Promotion</b>\n"
        "App፣ Website ወይም Online Service "
        "ለማስተዋወቅ የPromotion አማራጮች አሉ።\n\n"

        "🎯 <b>Custom Promotion</b>\n"
        "ሌላ የPromotion አገልግሎት ከፈለጉም "
        "ምን እንደሚፈልጉ ይንገሩን።\n\n"

        f"👤 <b>Support:</b> @{SUPPORT_USERNAME}\n\n"

        "💰 <b>ለዋጋ እና ለተጨማሪ መረጃ "
        "ከታች ያለውን Contact Support ይጫኑ።</b>"
    )

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "💬 Contact Support",
                url=f"https://t.me/{SUPPORT_USERNAME}"
            )
        ],
        [
            InlineKeyboardButton(
                "🔙 Back",
                callback_data="home"
            )
        ],
    ])

    await query.edit_message_text(
        text,
        reply_markup=keyboard,
        parse_mode="HTML"
    )


# =========================================================
# CANCEL COMMAND
# =========================================================

async def cancel_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    context.user_data.clear()

    await update.message.reply_text(
        "❌ <b>Cancelled</b>\n\n"
        "👇 Main Menu",
        reply_markup=main_keyboard(),
        parse_mode="HTML"
    )


# =========================================================
# TEXT HANDLER
# =========================================================

async def text_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    user = update.effective_user

    text = update.message.text.strip()

    # =====================================================
    # WALLET SETUP
    # =====================================================

    wallet_setup = context.user_data.get(
        "wallet_setup"
    )

    if wallet_setup:

        # -------------------------------------------------
        # CBE
        # -------------------------------------------------

        if wallet_setup == "CBE":

            if (
                len(text) != 13
                or not text.isdigit()
                or not text.startswith("1000")
            ):

                await update.message.reply_text(
                    "❌ <b>Invalid CBE Account</b>\n\n"
                    "13 digits መሆን አለበት።\n"
                    "<b>1000</b> በሚል መጀመር አለበት።",
                    parse_mode="HTML"
                )

                return

        # -------------------------------------------------
        # Telebirr
        # -------------------------------------------------

        elif wallet_setup == "Telebirr":

            if (
                len(text) != 10
                or not text.isdigit()
                or not (
                    text.startswith("09")
                    or text.startswith("07")
                )
            ):

                await update.message.reply_text(
                    "❌ <b>Invalid Telebirr Number</b>\n\n"
                    "10 digits መሆን አለበት።\n"
                    "<b>09</b> ወይም <b>07</b> በሚል መጀመር አለበት።",
                    parse_mode="HTML"
                )

                return

        # -------------------------------------------------
        # Save wallet
        # -------------------------------------------------

        saved = set_wallet(
            user.id,
            wallet_setup,
            text
        )

        if not saved:

            context.user_data.clear()

            await update.message.reply_text(
                "🛡️ <b>Security Review</b>\n\n"
                "ይህ wallet ከሌላ account ጋር "
                "ተመዝግቦ ተገኝቷል።\n\n"
                "For security reasons, your account "
                "has been placed under review.",
                reply_markup=main_keyboard(),
                parse_mode="HTML"
            )

            return

        saved_type = wallet_setup

        context.user_data.clear()

        await update.message.reply_text(
            f"✅ <b>{saved_type} Wallet Saved</b>\n\n"
            "Wallet በትክክል ተመዝግቧል።\n\n"
            "👇 Main Menu",
            reply_markup=main_keyboard(),
            parse_mode="HTML"
        )

        return

    # =====================================================
    # WITHDRAW AMOUNT
    # =====================================================

    if context.user_data.get(
        "withdraw_amount"
    ):

        try:

            amount = float(text)

        except ValueError:

            await update.message.reply_text(
                "❌ <b>Invalid Amount</b>\n\n"
                "እባክዎ valid number ያስገቡ።",
                parse_mode="HTML"
            )

            return

        # -------------------------------------------------
        # Invalid amount
        # -------------------------------------------------

        if amount <= 0:

            await update.message.reply_text(
                "❌ Amount ከ0 በላይ መሆን አለበት።",
                parse_mode="HTML"
            )

            return

        balance = get_balance(
            user.id
        )

        # -------------------------------------------------
        # Minimum withdrawal
        # -------------------------------------------------

        if amount < MIN_WITHDRAW:

            await update.message.reply_text(
                f"❌ Minimum withdrawal is "
                f"<b>{MIN_WITHDRAW:.2f} ETB</b>.",
                parse_mode="HTML"
            )

            return

        # -------------------------------------------------
        # Balance check
        # -------------------------------------------------

        if amount > balance:

            await update.message.reply_text(
                "❌ <b>Insufficient Balance</b>\n\n"
                f"Your balance: <b>{balance:.2f} ETB</b>",
                parse_mode="HTML"
            )

            return

        # -------------------------------------------------
        # Security check
        # -------------------------------------------------

        if is_suspicious(user.id):

            context.user_data.clear()

            await update.message.reply_text(
                "🛡️ <b>Security Review</b>\n\n"
                "Withdrawal ለጊዜው unavailable ነው።\n"
                "Your account is under review.",
                reply_markup=main_keyboard(),
                parse_mode="HTML"
            )

            return

        # -------------------------------------------------
        # Wallet check
        # -------------------------------------------------

        wallet = get_wallet(
            user.id
        )

        if not wallet or not wallet[0] or not wallet[1]:

            context.user_data.clear()

            await update.message.reply_text(
                "❌ Wallet not found.\n\n"
                "እባክዎ መጀመሪያ wallet ያስቀምጡ።",
                reply_markup=main_keyboard(),
                parse_mode="HTML"
            )

            return

        # -------------------------------------------------
        # Create withdrawal
        # -------------------------------------------------

        create_withdrawal(
            user.id,
            amount,
            wallet[0],
            wallet[1]
        )

        context.user_data.clear()

        await update.message.reply_text(
            "✅ <b>Withdrawal Request Submitted</b>\n\n"
            f"💰 Amount: <b>{amount:.2f} ETB</b>\n"
            f"🏦 Method: <b>{wallet[0]}</b>\n"
            f"🔢 Wallet: <code>{wallet[1]}</code>\n\n"
            "⏳ Status: <b>Pending</b>\n"
            "ጥያቄዎ ለreview ተልኳል።",
            reply_markup=main_keyboard(),
            parse_mode="HTML"
        )

        # -------------------------------------------------
        # Admin notification
        # -------------------------------------------------

        admin_id = os.getenv(
            "ADMIN_ID"
        )

        if admin_id:

            try:

                await context.bot.send_message(
                    chat_id=int(admin_id),
                    text=(
                        "💳 <b>New Withdrawal</b>\n\n"
                        f"👤 User ID: <code>{user.id}</code>\n"
                        f"💰 Amount: <b>{amount:.2f} ETB</b>\n"
                        f"🏦 Method: <b>{wallet[0]}</b>\n"
                        f"🔢 Wallet: <code>{wallet[1]}</code>\n\n"
                        "⏳ Status: <b>Pending</b>"
                    ),
                    parse_mode="HTML"
                )

            except Exception as e:

                logger.warning(
                    "Admin notification failed: %s",
                    e
                )

        return

    # =====================================================
    # DEFAULT TEXT
    # =====================================================

    await update.message.reply_text(
        "👇 <b>እባክዎ ከMenu ውስጥ የሚፈልጉትን ይምረጡ።</b>",
        reply_markup=main_keyboard(),
        parse_mode="HTML"
    )


# =========================================================
# CALLBACKS
# =========================================================

async def callbacks(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    query = update.callback_query

    data = query.data

    # -----------------------------------------------------
    # Verify
    # -----------------------------------------------------

    if data == "verify":

        await verify(
            update,
            context
        )

    # -----------------------------------------------------
    # Home
    # -----------------------------------------------------

    elif data == "home":

        await query.answer()

        context.user_data.clear()

        await send_main_menu(
            update,
            context
        )

    # -----------------------------------------------------
    # Balance
    # -----------------------------------------------------

    elif data == "balance":

        await show_balance(
            update,
            context
        )

    # -----------------------------------------------------
    # Referral
    # -----------------------------------------------------

    elif data == "referral":

        await show_referral(
            update,
            context
        )

    # -----------------------------------------------------
    # Tasks
    # -----------------------------------------------------

    elif data == "tasks":

        await show_tasks(
            update,
            context
        )

    # -----------------------------------------------------
    # Withdraw
    # -----------------------------------------------------

    elif data == "withdraw":

        await withdraw(
            update,
            context
        )

    # -----------------------------------------------------
    # Wallet
    # -----------------------------------------------------

    elif data == "wallet":

        context.user_data.clear()

        await show_wallet(
            update,
            context
        )

    # -----------------------------------------------------
    # CBE
    # -----------------------------------------------------

    elif data == "wallet_cbe":

        await wallet_cbe(
            update,
            context
        )

    # -----------------------------------------------------
    # Telebirr
    # -----------------------------------------------------

    elif data == "wallet_telebirr":

        await wallet_telebirr(
            update,
            context
        )

    # -----------------------------------------------------
    # Support
    # -----------------------------------------------------

    elif data == "support":

        context.user_data.clear()

        await support(
            update,
            context
        )

    # -----------------------------------------------------
    # Cancel withdrawal
    # -----------------------------------------------------

    elif data == "cancel_withdraw":

        await query.answer()

        context.user_data.clear()

        await query.edit_message_text(
            "❌ <b>Withdrawal Cancelled</b>\n\n"
            "👇 Main Menu",
            reply_markup=main_keyboard(),
            parse_mode="HTML"
        )

    # -----------------------------------------------------
    # Unknown callback
    # -----------------------------------------------------

    else:

        await query.answer()


# =========================================================
# ERROR HANDLER
# =========================================================

async def error_handler(
    update: object,
    context: ContextTypes.DEFAULT_TYPE
):

    logger.error(
        "Exception while handling update:",
        exc_info=context.error
    )


# =========================================================
# MAIN
# =========================================================

def main():

    init_db()

    token = os.getenv(
        "BOT_TOKEN"
    )

    if not token:

        raise RuntimeError(
            "BOT_TOKEN environment variable is missing."
        )

    application = (
        Application.builder()
        .token(token)
        .build()
    )

    # -----------------------------------------------------
    # Commands
    # -----------------------------------------------------

    application.add_handler(
        CommandHandler(
            "start",
            start
        )
    )

    application.add_handler(
        CommandHandler(
            "cancel",
            cancel_command
        )
    )

    # -----------------------------------------------------
    # Buttons
    # -----------------------------------------------------

    application.add_handler(
        CallbackQueryHandler(
            callbacks
        )
    )

    # -----------------------------------------------------
    # Text
    # -----------------------------------------------------

    application.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            text_handler
        )
    )

    # -----------------------------------------------------
    # Error handler
    # -----------------------------------------------------

    application.add_error_handler(
        error_handler
    )

    logger.info(
        "%s is starting...",
        BOT_NAME
    )

    application.run_polling(
        allowed_updates=Update.ALL_TYPES
    )


# =========================================================
# RUN
# =========================================================

if __name__ == "__main__":
    main()

import os
import sqlite3
import logging
from typing import Optional

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

# =========================================================
# REQUIRED CHANNELS
# =========================================================

REQUIRED_CHANNELS = [
    ("@Sheger_tech1", "Sheger Tech"),
    ("@EthioVortex1", "Ethio Vortex"),
    ("@ethiocashflow", "Ethio Cash Flow"),
    ("@AmanIncomeLab", "Aman Income Lab"),
    ("@OnlineIncomeHub07", "Online Income Hub"),
    ("@Paymentprooff2", "Payment Proof"),
]

# =========================================================
# BOT SETTINGS
# =========================================================

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
            joined_all INTEGER DEFAULT 0,
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

    # New anti-abuse columns.
    # Safe for existing databases.
    columns = [
        ("referral_status", "TEXT DEFAULT 'none'"),
        ("suspicious", "INTEGER DEFAULT 0"),
    ]

    for column_name, column_type in columns:
        try:
            cur.execute(
                f"ALTER TABLE users ADD COLUMN {column_name} {column_type}"
            )
        except sqlite3.OperationalError:
            pass

    # Unique wallet protection.
    # If an old database already contains duplicate wallets,
    # the index may fail; manual checking below still protects new wallets.
    try:
        cur.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS
            idx_unique_wallet
            ON users(wallet_type, wallet_number)
            WHERE wallet_type IS NOT NULL
            AND wallet_number IS NOT NULL
            AND wallet_number != ''
        """)
    except sqlite3.IntegrityError:
        logger.warning(
            "Duplicate old wallets detected. "
            "Manual wallet checking remains active."
        )
    except sqlite3.OperationalError:
        pass

    conn.commit()
    conn.close()


def get_user(user_id: int):
    conn = db()
    cur = conn.cursor()

    cur.execute(
        "SELECT * FROM users WHERE user_id = ?",
        (user_id,)
    )

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

    # Do not allow changing the original referrer later.
    cur.execute("""
        INSERT OR IGNORE INTO users
        (user_id, username, first_name, referred_by)
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
        SET username = ?, first_name = ?
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

    cur.execute(
        "SELECT balance FROM users WHERE user_id = ?",
        (user_id,)
    )

    row = cur.fetchone()
    conn.close()

    if not row:
        return 0.0

    return float(row[0])


def add_balance(user_id: int, amount: float):
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


def set_joined_all(user_id: int, value: int):
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

def wallet_exists(
    wallet_type: str,
    wallet_number: str,
    except_user_id: Optional[int] = None
) -> bool:

    conn = db()
    cur = conn.cursor()

    if except_user_id is not None:
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
            except_user_id
        ))
    else:
        cur.execute("""
            SELECT user_id
            FROM users
            WHERE wallet_type = ?
            AND wallet_number = ?
            LIMIT 1
        """, (
            wallet_type,
            wallet_number
        ))

    row = cur.fetchone()
    conn.close()

    return row is not None


def set_wallet(
    user_id: int,
    wallet_type: str,
    wallet_number: str
) -> bool:

    if wallet_exists(
        wallet_type,
        wallet_number,
        except_user_id=user_id
    ):
        return False

    conn = db()
    cur = conn.cursor()

    try:
        cur.execute("""
            UPDATE users
            SET wallet_type = ?, wallet_number = ?
            WHERE user_id = ?
        """, (
            wallet_type,
            wallet_number,
            user_id
        ))

        conn.commit()

    except sqlite3.IntegrityError:
        conn.rollback()
        conn.close()
        return False

    conn.close()

    # Wallet is now saved.
    # If a referral was waiting for wallet verification,
    # process it.
    process_pending_referral(user_id)

    return True


def get_wallet(user_id: int):
    conn = db()
    cur = conn.cursor()

    cur.execute("""
        SELECT wallet_type, wallet_number
        FROM users
        WHERE user_id = ?
    """, (user_id,))

    row = cur.fetchone()
    conn.close()

    return row


# =========================================================
# REFERRAL SYSTEM
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


def set_referral_status(
    user_id: int,
    status: str
):
    conn = db()
    cur = conn.cursor()

    cur.execute("""
        UPDATE users
        SET referral_status = ?
        WHERE user_id = ?
    """, (
        status,
        user_id
    ))

    conn.commit()
    conn.close()


def process_referral_reward(
    user_id: int
):
    """
    Referral reward is paid only when:
    1. The new user completed verification.
    2. The referral is valid.
    3. The new user's wallet is saved.
    4. The wallet is not already used by another account.
    5. The inviter is not the same account.
    6. The referral has not already been paid.
    """

    conn = db()
    cur = conn.cursor()

    cur.execute("""
        SELECT
            referred_by,
            referral_paid,
            joined_all,
            wallet_type,
            wallet_number
        FROM users
        WHERE user_id = ?
    """, (user_id,))

    row = cur.fetchone()

    if not row:
        conn.close()
        return None

    (
        referred_by,
        referral_paid,
        joined_all,
        wallet_type,
        wallet_number
    ) = row

    if not referred_by:
        conn.close()
        return None

    if referred_by == user_id:
        conn.close()
        mark_suspicious(user_id)
        return None

    if referral_paid:
        conn.close()
        return None

    if not joined_all:
        conn.close()
        return None

    # Wallet is required before referral reward.
    if not wallet_type or not wallet_number:
        conn.close()
        set_referral_status(user_id, "pending_wallet")
        return None

    # Make sure the same wallet is not linked
    # to another Telegram account.
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

    wallet_owner = cur.fetchone()

    if wallet_owner:
        conn.close()
        mark_suspicious(user_id)
        set_referral_status(user_id, "blocked_duplicate_wallet")
        return None

    # Check inviter exists.
    cur.execute("""
        SELECT user_id
        FROM users
        WHERE user_id = ?
    """, (referred_by,))

    inviter_exists = cur.fetchone()

    if not inviter_exists:
        conn.close()
        return None

    # Pay inviter.
    cur.execute("""
        UPDATE users
        SET balance = balance + ?
        WHERE user_id = ?
    """, (
        REFERRAL_REWARD,
        referred_by
    ))

    # Mark referral paid.
    cur.execute("""
        UPDATE users
        SET referral_paid = 1,
            referral_status = 'paid'
        WHERE user_id = ?
    """, (user_id,))

    conn.commit()
    conn.close()

    return referred_by


def process_pending_referral(user_id: int):
    """
    Called after a wallet is successfully saved.
    """

    inviter = process_referral_reward(user_id)

    return inviter


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
        (user_id, amount, wallet_type, wallet_number)
        VALUES (?, ?, ?, ?)
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
            missing.append((username, name))

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
# START
# =========================================================

async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    user = update.effective_user

    referred_by = None

    if context.args:
        try:
            ref_id = int(context.args[0])

            if ref_id != user.id:
                referred_by = ref_id

        except ValueError:
            pass

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

    missing = await get_missing_channels(
        context,
        user.id
    )

    if missing:

        text = (
            f"👋 <b>Welcome to {BOT_NAME}</b>\n\n"
            "💰 የማግኘት ስርዓቱን ለመጀመር "
            "ከታች ያሉትን channels በሙሉ Join ያድርጉ።\n\n"
            "👇 ሁሉንም channels ከጨረሱ በኋላ "
            "<b>Verify</b> ይጫኑ።"
        )

        await update.message.reply_text(
            text,
            reply_markup=channel_keyboard(missing),
            parse_mode="HTML"
        )

        return

    set_joined_all(user.id, 1)

    await send_main_menu(update, context)


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
            )
        ],
        [
            InlineKeyboardButton(
                "📋 Tasks",
                callback_data="tasks"
            ),
            InlineKeyboardButton(
                "💳 Withdraw",
                callback_data="withdraw"
            )
        ],
        [
            InlineKeyboardButton(
                "👛 Wallet",
                callback_data="wallet"
            ),
            InlineKeyboardButton(
                "🆘 Support",
                callback_data="support"
            )
        ],
    ])


async def send_main_menu(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    text = (
        f"🎉 <b>Welcome to {BOT_NAME}</b>\n\n"
        "✅ <b>Verified Account</b>\n"
        "💰 Earn ETB through Referrals & Tasks.\n\n"
        "👇 <b>Choose your option:</b>"
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
            "እነዚህ channels ገና Join አላደረጉም፦\n\n"
            f"{names}\n\n"
            "ከJoin በኋላ <b>Verify</b> እንደገና ይጫኑ።"
        )

        await query.edit_message_text(
            text,
            reply_markup=channel_keyboard(missing),
            parse_mode="HTML"
        )

        return

    set_joined_all(user_id, 1)

    # Try referral reward.
    # If wallet is not saved yet, it becomes pending.
    inviter = process_referral_reward(user_id)

    if inviter:

        try:
            await context.bot.send_message(
                chat_id=inviter,
                text=(
                    "🎉 <b>Referral Reward!</b>\n\n"
                    "እርስዎ የጋበዙት user verification አጠናቋል።\n\n"
                    f"💰 <b>+{REFERRAL_REWARD:.2f} ETB</b>\n"
                    "በBalance ላይ ተጨምሯል።"
                ),
                parse_mode="HTML"
            )
        except Exception:
            pass

    await query.edit_message_text(
        "✅ <b>Verified Successfully!</b>\n\n"
        "🎉 Welcome to Global Cash Bot.\n\n"
        "💡 Referral reward ለመክፈል የተጠቃሚው wallet "
        "እንዲሁም ይመረጣል።",
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

    balance = get_balance(query.from_user.id)

    text = (
        "💰 <b>Your Balance</b>\n\n"
        "💵 Available Balance\n"
        f"<b>{balance:.2f} ETB</b>\n\n"
        f"🎯 Minimum Withdraw: <b>{MIN_WITHDRAW:.2f} ETB</b>\n\n"
        "Earn more through Referrals & Tasks."
    )

    await query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "💳 Withdraw",
                    callback_data="withdraw"
                )
            ],
            [
                InlineKeyboardButton(
                    "👥 Earn with Referral",
                    callback_data="referral"
                )
            ],
            [
                InlineKeyboardButton(
                    "🔙 Back",
                    callback_data="home"
                )
            ]
        ]),
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

    count = get_referral_count(user_id)

    me = await context.bot.get_me()

    referral_link = (
        f"https://t.me/{me.username}?start={user_id}"
    )

    text = (
        "👥 <b>Referral Program</b>\n\n"
        "🤝 Friends invite አድርገው ETB ያግኙ!\n\n"
        f"👤 Successful Referrals: <b>{count}</b>\n"
        f"💰 Per Verified Referral: "
        f"<b>{REFERRAL_REWARD:.2f} ETB</b>\n\n"
        "🔗 <b>Your Referral Link</b>\n"
        f"<code>{referral_link}</code>\n\n"
        "📌 Reward የሚከፈለው ከverification በኋላ "
        "እና wallet በትክክል ከተረጋገጠ ብቻ ነው።"
    )

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "📤 Share Referral Link",
                url=(
                    "https://t.me/share/url"
                    f"?url={referral_link}"
                    "&text=Join%20Global%20Cash%20Bot"
                )
            )
        ],
        [
            InlineKeyboardButton(
                "💰 My Balance",
                callback_data="balance"
            )
        ],
        [
            InlineKeyboardButton(
                "🔙 Back",
                callback_data="home"
            )
        ]
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
        "📋 <b>Tasks Center</b>\n\n"
        "🚀 New earning tasks are coming soon.\n\n"
        "👥 <b>Available Now</b>\n"
        "Invite friends and earn "
        f"<b>{REFERRAL_REWARD:.2f} ETB</b> "
        "for each eligible referral.\n\n"
        "🔥 Keep checking for new tasks."
    )

    await query.edit_message_text(
        text,
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
            ]
        ]),
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

    wallet = get_wallet(query.from_user.id)

    if wallet and wallet[0] and wallet[1]:

        text = (
            "👛 <b>Your Wallet</b>\n\n"
            f"💳 Method: <b>{wallet[0]}</b>\n"
            f"🔢 Number: <code>{wallet[1]}</code>\n\n"
            "✅ Wallet saved successfully."
        )

    else:

        text = (
            "👛 <b>Your Wallet</b>\n\n"
            "⚠️ No wallet saved yet.\n\n"
            "Choose your payment method:"
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
            )
        ],
        [
            InlineKeyboardButton(
                "🔙 Back",
                callback_data="home"
            )
        ]
    ])

    await query.edit_message_text(
        text,
        reply_markup=keyboard,
        parse_mode="HTML"
    )


# =========================================================
# WALLET SETUP
# =========================================================

async def wallet_cbe(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    query = update.callback_query
    await query.answer()

    context.user_data["wallet_setup"] = "CBE"

    await query.edit_message_text(
        "🏦 <b>CBE Wallet</b>\n\n"
        "የCBE account number ያስገቡ።\n\n"
        "📌 13 digits መሆን እና <b>1000</b> በሚል "
        "መጀመር አለበት።\n\n"
        "❌ Cancel: /cancel",
        parse_mode="HTML"
    )


async def wallet_telebirr(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    query = update.callback_query
    await query.answer()

    context.user_data["wallet_setup"] = "Telebirr"

    await query.edit_message_text(
        "📱 <b>Telebirr Wallet</b>\n\n"
        "የTelebirr phone number ያስገቡ።\n\n"
        "📌 10 digits እና <b>09</b> ወይም <b>07</b> "
        "በሚል መጀመር አለበት።\n\n"
        "❌ Cancel: /cancel",
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

    balance = get_balance(query.from_user.id)
    wallet = get_wallet(query.from_user.id)

    if balance < MIN_WITHDRAW:

        await query.edit_message_text(
            "💳 <b>Withdraw</b>\n\n"
            "⚠️ እስካሁን ለWithdrawal በቂ Balance የለዎትም።\n\n"
            f"💰 Your Balance: <b>{balance:.2f} ETB</b>\n"
            f"🎯 Minimum: <b>{MIN_WITHDRAW:.2f} ETB</b>\n\n"
            "💡 Keep earning and reach the minimum.",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "👥 Referral & Earn",
                        callback_data="referral"
                    )
                ],
                [
                    InlineKeyboardButton(
                        "🔙 Back",
                        callback_data="home"
                    )
                ]
            ]),
            parse_mode="HTML"
        )

        return

    if not wallet or not wallet[0] or not wallet[1]:

        await query.edit_message_text(
            "👛 <b>Wallet Required</b>\n\n"
            "Withdrawal ከማድረግዎ በፊት "
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
                ]
            ]),
            parse_mode="HTML"
        )

        return

    context.user_data["withdraw_amount"] = True

    await query.edit_message_text(
        "💳 <b>Withdrawal</b>\n\n"
        f"💰 Available: <b>{balance:.2f} ETB</b>\n"
        f"🎯 Minimum: <b>{MIN_WITHDRAW:.2f} ETB</b>\n\n"
        "የሚያወጡትን amount በETB ያስገቡ።\n\n"
        "❌ Cancel: /cancel",
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
        "ለሚፈልጉት አገልግሎት ከታች ይምረጡ 👇"
    )

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "📢 Advertising & Promotion",
                callback_data="support_ads"
            )
        ],
        [
            InlineKeyboardButton(
                "📈 Telegram Growth",
                callback_data="support_growth"
            )
        ],
        [
            InlineKeyboardButton(
                "💼 Business Promotion",
                callback_data="support_business"
            )
        ],
        [
            InlineKeyboardButton(
                "📱 App / Website Promotion",
                callback_data="support_app"
            )
        ],
        [
            InlineKeyboardButton(
                "💬 General Support",
                callback_data="support_general"
            )
        ],
        [
            InlineKeyboardButton(
                "🔙 Back",
                callback_data="home"
            )
        ]
    ])

    await query.edit_message_text(
        text,
        reply_markup=keyboard,
        parse_mode="HTML"
    )


async def support_category(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    category: str
):

    query = update.callback_query
    await query.answer()

    descriptions = {
        "ads": (
            "📢 <b>Advertising & Promotion</b>\n\n"
            "Channel • Group • Bot • Business Promotion\n\n"
            "ማስታወቂያ ለመስራት ያነጋግሩን።"
        ),
        "growth": (
            "📈 <b>Telegram Growth</b>\n\n"
            "Members • Channel Growth • Promotion\n\n"
            "የTelegram እድገት እና Promotion አገልግሎት።"
        ),
        "business": (
            "💼 <b>Business Promotion</b>\n\n"
            "Product • Service • Brand Promotion\n\n"
            "የንግድዎን እና Service ማስተዋወቅ።"
        ),
        "app": (
            "📱 <b>App / Website Promotion</b>\n\n"
            "App • Website • Online Service\n\n"
            "Online project ማስተዋወቅ ከፈለጉ ያነጋግሩን።"
        ),
        "general": (
            "💬 <b>General Support</b>\n\n"
            "ለBot ችግር፣ ጥያቄ ወይም ሌላ እገዛ ካስፈለገዎት ያነጋግሩን።"
        ),
    }

    text = (
        descriptions.get(
            category,
            "💬 <b>Support</b>"
        )
        + f"\n\n📩 <b>@{SUPPORT_USERNAME}</b>"
    )

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "💬 Contact @AmanM_12",
                url=f"https://t.me/{SUPPORT_USERNAME}"
            )
        ],
        [
            InlineKeyboardButton(
                "🔙 Support Menu",
                callback_data="support"
            )
        ],
        [
            InlineKeyboardButton(
                "🏠 Main Menu",
                callback_data="home"
            )
        ]
    ])

    await query.edit_message_text(
        text,
        reply_markup=keyboard,
        parse_mode="HTML"
    )


# =========================================================
# /CANCEL
# =========================================================

async def cancel(
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
# TEXT MESSAGE HANDLER
# =========================================================

async def text_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    user = update.effective_user
    text = update.message.text.strip()

    # -----------------------------------------------------
    # WALLET SETUP
    # -----------------------------------------------------

    wallet_setup = context.user_data.get("wallet_setup")

    if wallet_setup:

        if wallet_setup == "CBE":

            if (
                len(text) != 13
                or not text.isdigit()
                or not text.startswith("1000")
            ):

                await update.message.reply_text(
                    "❌ <b>Invalid CBE account number.</b>\n\n"
                    "13 digits መሆን እና 1000 በሚል "
                    "መጀመር አለበት።",
                    parse_mode="HTML"
                )

                return

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
                    "❌ <b>Invalid Telebirr number.</b>\n\n"
                    "10 digits እና 09 ወይም 07 በሚል "
                    "መጀመር አለበት።",
                    parse_mode="HTML"
                )

                return

        # Anti-duplicate-wallet check.
        if wallet_exists(
            wallet_setup,
            text,
            except_user_id=user.id
        ):

            mark_suspicious(user.id)

            await update.message.reply_text(
                "⚠️ <b>Wallet Already Registered</b>\n\n"
                "ይህ wallet ቀድሞ በሌላ account ተመዝግቧል።\n\n"
                "አንድ CBE/Telebirr wallet በአንድ verified "
                "account ብቻ መጠቀም ይቻላል።",
                parse_mode="HTML"
            )

            return

        saved = set_wallet(
            user.id,
            wallet_setup,
            text
        )

        if not saved:

            await update.message.reply_text(
                "⚠️ Wallet ማስቀመጥ አልተቻለም።\n"
                "እባክዎ ሌላ valid wallet ይጠቀሙ።"
            )

            return

        context.user_data.clear()

        await update.message.reply_text(
            f"✅ <b>{wallet_setup} Wallet Saved!</b>\n\n"
            "🔐 Wallet security check completed.\n"
            "👇 Main Menu",
            reply_markup=main_keyboard(),
            parse_mode="HTML"
        )

        # If this user had a pending referral,
        # the reward may now be released.
        inviter = process_referral_reward(user.id)

        if inviter:

            try:
                await context.bot.send_message(
                    chat_id=inviter,
                    text=(
                        "🎉 <b>Referral Reward Unlocked!</b>\n\n"
                        "የጋበዙት user verification እና wallet "
                        "setup አጠናቋል።\n\n"
                        f"💰 <b>+{REFERRAL_REWARD:.2f} ETB</b>"
                    ),
                    parse_mode="HTML"
                )
            except Exception:
                pass

        return

    # -----------------------------------------------------
    # WITHDRAW AMOUNT
    # -----------------------------------------------------

    if context.user_data.get("withdraw_amount"):

        try:
            amount = float(text)
        except ValueError:

            await update.message.reply_text(
                "❌ Please enter a valid number."
            )

            return

        balance = get_balance(user.id)

        if amount < MIN_WITHDRAW:

            await update.message.reply_text(
                f"❌ Minimum withdrawal is "
                f"<b>{MIN_WITHDRAW:.2f} ETB</b>.",
                parse_mode="HTML"
            )

            return

        if amount > balance:

            await update.message.reply_text(
                "❌ <b>Insufficient Balance</b>\n\n"
                f"Your balance: <b>{balance:.2f} ETB</b>",
                parse_mode="HTML"
            )

            return

        wallet = get_wallet(user.id)

        if not wallet or not wallet[0] or not wallet[1]:

            context.user_data.clear()

            await update.message.reply_text(
                "❌ Wallet not found.\n"
                "Please set your wallet first.",
                reply_markup=main_keyboard()
            )

            return

        # Suspicious accounts should not automatically withdraw.
        if is_suspicious(user.id):

            context.user_data.clear()

            await update.message.reply_text(
                "⚠️ <b>Security Review</b>\n\n"
                "Your account is currently under security review.\n\n"
                "📩 Please contact Support: "
                f"@{SUPPORT_USERNAME}",
                reply_markup=main_keyboard(),
                parse_mode="HTML"
            )

            return

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
            f"💳 Method: <b>{wallet[0]}</b>\n"
            f"🔢 Wallet: <code>{wallet[1]}</code>\n\n"
            "⏳ Status: <b>Pending</b>\n"
            "📩 Your request has been sent for review.",
            reply_markup=main_keyboard(),
            parse_mode="HTML"
        )

        # Notify admin.
        admin_id = os.getenv("ADMIN_ID")

        if admin_id:

            try:
                await context.bot.send_message(
                    chat_id=int(admin_id),
                    text=(
                        "💳 <b>New Withdrawal</b>\n\n"
                        f"👤 User ID: <code>{user.id}</code>\n"
                        f"💰 Amount: <b>{amount

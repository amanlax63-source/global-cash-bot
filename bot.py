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

# Telegram support
SUPPORT_USERNAME = "AmanM_12"

# ---------------------------------------------------------
# REQUIRED CHANNELS
# ---------------------------------------------------------

REQUIRED_CHANNELS = [
    ("@Sheger_tech1", "Sheger Tech"),
    ("@EthioVortex1", "Ethio Vortex"),
    ("@ethiocashflow", "Ethio Cash Flow"),
    ("@AmanIncomeLab", "Aman Income Lab"),
    ("@OnlineIncomeHub07", "Online Income Hub"),
    ("@Paymentprooff2", "Payment Proof"),
]

# ---------------------------------------------------------
# BOT SETTINGS
# ---------------------------------------------------------

REFERRAL_REWARD = 2.0          # ETB
MIN_WITHDRAW = 10.0            # ETB
CURRENCY = "ETB"

# Database file
DB_FILE = "global_cash.db"

# Logging
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


def set_wallet(
    user_id: int,
    wallet_type: str,
    wallet_number: str
):
    conn = db()
    cur = conn.cursor()

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
    conn.close()


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
    """
    Pays the inviter only after the new user has passed
    all required-channel verification.
    """

    conn = db()
    cur = conn.cursor()

    cur.execute("""
        SELECT referred_by, referral_paid
        FROM users
        WHERE user_id = ?
    """, (user_id,))

    row = cur.fetchone()

    if not row:
        conn.close()
        return None

    referred_by, referral_paid = row

    if not referred_by or referral_paid:
        conn.close()
        return None

    # Pay inviter
    cur.execute("""
        UPDATE users
        SET balance = balance + ?
        WHERE user_id = ?
    """, (
        REFERRAL_REWARD,
        referred_by
    ))

    # Mark referral as paid
    cur.execute("""
        UPDATE users
        SET referral_paid = 1
        WHERE user_id = ?
    """, (user_id,))

    conn.commit()
    conn.close()

    return referred_by


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

    # Deduct balance immediately
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

    # /start referral
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
        "✅ Your account is verified.\n\n"
        "💰 Earn ETB through available activities "
        "and referrals.\n\n"
        "👇 Choose an option:"
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
            "⚠️ <b>Verification failed</b>\n\n"
            "እነዚህ channels ገና Join አላደረጉም፦\n\n"
            f"{names}\n\n"
            "ከJoin በኋላ Verify እንደገና ይጫኑ።"
        )

        await query.edit_message_text(
            text,
            reply_markup=channel_keyboard(missing),
            parse_mode="HTML"
        )

        return

    set_joined_all(user_id, 1)

    # Referral reward
    inviter = process_referral_reward(user_id)

    if inviter:

        try:
            await context.bot.send_message(
                chat_id=inviter,
                text=(
                    "🎉 <b>Referral Reward!</b>\n\n"
                    f"Someone you referred completed verification.\n"
                    f"💰 +{REFERRAL_REWARD:.2f} ETB added to your balance."
                ),
                parse_mode="HTML"
            )
        except Exception:
            pass

    await query.edit_message_text(
        "✅ <b>Verified successfully!</b>\n\n"
        "🎉 Welcome to Global Cash Bot.",
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
        f"💵 Balance: <b>{balance:.2f} ETB</b>\n\n"
        f"Minimum withdrawal: {MIN_WITHDRAW:.2f} ETB"
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
        f"👤 Successful referrals: <b>{count}</b>\n"
        f"💰 Reward per verified referral: "
        f"<b>{REFERRAL_REWARD:.2f} ETB</b>\n\n"
        "📎 <b>Your referral link:</b>\n"
        f"<code>{referral_link}</code>\n\n"
        "⚠️ Reward is added only after the referred user "
        "joins all required channels and passes verification."
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
        "📋 <b>Tasks</b>\n\n"
        "🔹 More earning tasks will be added here.\n\n"
        "📌 Current available earning method:\n"
        "👥 Invite friends and earn referral rewards "
        "after successful verification."
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
            f"💳 Type: <b>{wallet[0]}</b>\n"
            f"🔢 Number: <code>{wallet[1]}</code>"
        )

    else:

        text = (
            "👛 <b>Your Wallet</b>\n\n"
            "❌ No wallet saved yet.\n\n"
            "Choose your wallet type:"
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
        "📌 13 digits መሆን አለበት እና 1000 በሚል መጀመር አለበት።\n\n"
        "🔙 Cancel ለማድረግ /cancel ይጻፉ።",
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
        "📌 10 digits እና 09 ወይም 07 በሚል መጀመር አለበት።\n\n"
        "🔙 Cancel ለማድረግ /cancel ይጻፉ።",
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
            "❌ <b>Insufficient Balance</b>\n\n"
            f"💰 Your balance: {balance:.2f} ETB\n"
            f"💳 Minimum withdrawal: {MIN_WITHDRAW:.2f} ETB",
            reply_markup=InlineKeyboardMarkup([
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
            "Withdrawal ከማድረግዎ በፊት wallet ያስቀምጡ።",
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
        f"📌 Minimum: <b>{MIN_WITHDRAW:.2f} ETB</b>\n\n"
        "የሚያወጡትን amount በETB ያስገቡ።\n\n"
        "ለመሰረዝ /cancel ይጻፉ።",
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
        "🆘 <b>Support</b>\n\n"
        "በbot ላይ ችግር ካጋጠመዎት ወይም "
        "ለማስታወቂያ / promotion ማነጋገር ከፈለጉ፦\n\n"
        f"👤 Support: @{SUPPORT_USERNAME}\n\n"
        "💬 Please contact our support admin."
    )

    await query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup([
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
            ]
        ]),
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
    # CANCEL
    # -----------------------------------------------------

    if text.lower() == "/cancel":

        context.user_data.clear()

        await update.message.reply_text(
            "❌ Cancelled.\n\n"
            "👇 Main Menu",
            reply_markup=main_keyboard()
        )

        return

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
                    "❌ Invalid CBE account number.\n\n"
                    "13 digits መሆን እና 1000 በሚል መጀመር አለበት።"
                )

                return

        elif wallet_setup == "Telebirr":

            if (
                len(text) != 10
                or not text.isdigit()
                or not (text.startswith("09") or text.startswith("07"))
            ):

                await update.message.reply_text(
                    "❌ Invalid Telebirr number.\n\n"
                    "10 digits እና 09 ወይም 07 በሚል መጀመር አለበት።"
                )

                return

        set_wallet(
            user.id,
            wallet_setup,
            text
        )

        context.user_data.clear()

        await update.message.reply_text(
            f"✅ <b>{wallet_setup} wallet saved successfully.</b>\n\n"
            "👇 Main Menu",
            reply_markup=main_keyboard(),
            parse_mode="HTML"
        )

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
                f"{MIN_WITHDRAW:.2f} ETB."
            )

            return

        if amount > balance:

            await update.message.reply_text(
                f"❌ Insufficient balance.\n\n"
                f"Your balance: {balance:.2f} ETB"
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
            "⏳ Your request is now pending.",
            reply_markup=main_keyboard(),
            parse_mode="HTML"
        )

        # Notify admin if ADMIN_ID exists
        admin_id = os.getenv("ADMIN_ID")

        if admin_id:

            try:
                await context.bot.send_message(
                    chat_id=int(admin_id),
                    text=(
                        "💳 <b>New Withdrawal</b>\n\n"
                        f"👤 User ID: <code>{user.id}</code>\n"
                        f"💰 Amount: <b>{amount:.2f} ETB</b>\n"
                        f"🏦 Method: <b>{wallet[0]}</b>\n"
                        f"🔢 Wallet: <code>{wallet[1]}</code>"
                    ),
                    parse_mode="HTML"
                )
            except Exception as e:
                logger.warning(
                    "Admin notification failed: %s",
                    e
                )

        return

    # -----------------------------------------------------
    # DEFAULT
    # -----------------------------------------------------

    await update.message.reply_text(
        "👇 <b>Please choose an option from the menu.</b>",
        reply_markup=main_keyboard(),
        parse_mode="HTML"
    )


# =========================================================
# CALLBACK ROUTER
# =========================================================

async def callbacks(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    query = update.callback_query
    data = query.data

    if data == "verify":
        await verify(update, context)

    elif data == "home":
        await query.answer()
        await send_main_menu(update, context)

    elif data == "balance":
        await show_balance(update, context)

    elif data == "referral":
        await show_referral(update, context)

    elif data == "tasks":
        await show_tasks(update, context)

    elif data == "withdraw":
        await withdraw(update, context)

    elif data == "wallet":
        await show_wallet(update, context)

    elif data == "wallet_cbe":
        await wallet_cbe(update, context)

    elif data == "wallet_telebirr":
        await wallet_telebirr(update, context)

    elif data == "support":
        await support(update, context)


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

    token = os.getenv("BOT_TOKEN")

    if not token:

        raise RuntimeError(
            "BOT_TOKEN environment variable is missing."
        )

    application = (
        Application.builder()
        .token(token)
        .build()
    )

    # Commands
    application.add_handler(
        CommandHandler("start", start)
    )

    # Callback buttons
    application.add_handler(
        CallbackQueryHandler(callbacks)
    )

    # Text messages
    application.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            text_handler
        )
    )

    # Errors
    application.add_error_handler(
        error_handler
    )

    logger.info(
        "%s is starting...",
        BOT_NAME
    )

    # Telegram long polling
    application.run_polling(
        allowed_updates=Update.ALL_TYPES
    )


if __name__ == "__main__":
    main()

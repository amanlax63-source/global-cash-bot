import os
import re
import sqlite3
import logging
from datetime import datetime, timezone
from html import escape

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

    conn.commit()
    conn.close()


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
                user_id, username, first_name, referred_by,
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
        conn.execute("""
            UPDATE users
            SET username=?, first_name=?
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
    return float(row["balance"]) if row else 0.0


def mark_suspicious(user_id, value=1):
    conn = db()
    conn.execute(
        "UPDATE users SET suspicious=? WHERE user_id=?",
        (1 if value else 0, user_id),
    )
    conn.commit()
    conn.close()


def get_wallet(user_id):
    row = get_user(user_id)
    if not row:
        return None
    if not row["wallet_type"] or not row["wallet_number"]:
        return None
    return row["wallet_type"], row["wallet_number"]


def wallet_exists_for_other_user(wallet_type, wallet_number, user_id):
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


def set_wallet(user_id, wallet_type, wallet_number):
    conn = db()
    try:
        conn.execute("""
            UPDATE users
            SET wallet_type=?, wallet_number=?
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
        ON CONFLICT(key) DO UPDATE SET value=excluded.value
    """, (key, str(value)))
    conn.commit()
    conn.close()


def get_referral_reward():
    try:
        return float(get_setting("referral_reward", "2.00"))
    except Exception:
        return 2.0


def get_referral_count(user_id):
    conn = db()
    row = conn.execute("""
        SELECT COUNT(*) AS c
        FROM referrals
        WHERE referrer_id=? AND status='paid'
    """, (user_id,)).fetchone()
    conn.close()
    return int(row["c"])


def process_referral_reward(referred_id):
    user = get_user(referred_id)
    if not user:
        return None

    inviter = user["referred_by"]
    if not inviter or inviter == referred_id:
        return None

    if user["referral_paid"]:
        return None

    if user["suspicious"]:
        return None

    inviter_row = get_user(inviter)
    if not inviter_row or inviter_row["suspicious"]:
        return None

    conn = db()
    try:
        existing = conn.execute(
            "SELECT id FROM referrals WHERE referred_id=?",
            (referred_id,),
        ).fetchone()

        if existing:
            conn.execute("""
                UPDATE users
                SET referral_paid=1, referral_status='paid'
                WHERE user_id=?
            """, (referred_id,))
            conn.commit()
            return None

        reward = get_referral_reward()

        conn.execute("""
            UPDATE users
            SET balance=balance+?
            WHERE user_id=?
        """, (reward, inviter))

        conn.execute("""
            INSERT INTO referrals(
                referrer_id, referred_id,
                reward_amount, status, created_at
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
            SET referral_paid=1, referral_status='paid'
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
        logger.exception("Referral reward error")
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


def user_task_paid(user_id, task_id):
    conn = db()
    row = conn.execute("""
        SELECT paid
        FROM user_tasks
        WHERE user_id=? AND task_id=?
    """, (user_id, task_id)).fetchone()
    conn.close()
    return bool(row and row["paid"])


def save_user_task(user_id, task_id, verified, paid):
    conn = db()
    conn.execute("""
        INSERT INTO user_tasks(
            user_id, task_id, verified, paid, paid_at
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


def create_task(title, description, username, url, reward):
    conn = db()
    cur = conn.execute("""
        INSERT INTO tasks(
            title, description,
            channel_username, channel_url,
            reward, active, created_at
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

async def missing_channels(bot, user_id):
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
                missing.append((username, url))

        except Exception:
            missing.append((username, url))

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
            "✅ Verify | አረጋግጥ",
            callback_data="verify",
        )
    ])

    return InlineKeyboardMarkup(buttons)


def main_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("💰 Balance | ቀሪ", callback_data="balance"),
            InlineKeyboardButton("👥 Referral | ሪፈራል", callback_data="referral"),
        ],
        [
            InlineKeyboardButton("🎯 Tasks | ተግባር", callback_data="tasks"),
            InlineKeyboardButton("💸 Withdraw | ማውጣት", callback_data="withdraw"),
        ],
        [
            InlineKeyboardButton("👛 Wallet | ዋሌት", callback_data="wallet"),
            InlineKeyboardButton("📞 Support | ድጋፍ", callback_data="support"),
        ],
    ])


def back_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔙 Back | ተመለስ", callback_data="home")]
    ])


def balance_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("💸 Withdraw | ማውጣት", callback_data="withdraw"),
            InlineKeyboardButton("👥 Referral | ሪፈራል", callback_data="referral"),
        ],
        [InlineKeyboardButton("🔙 Back | ተመለስ", callback_data="home")],
    ])


def referral_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📤 Share Link | አጋራ", callback_data="share_ref")],
        [InlineKeyboardButton("🔙 Back | ተመለስ", callback_data="home")],
    ])


def wallet_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🏦 CBE | ንግድ ባንክ", callback_data="wallet_cbe"),
            InlineKeyboardButton("📱 Telebirr | ቴሌብር", callback_data="wallet_telebirr"),
        ],
        [InlineKeyboardButton("🔙 Back | ተመለስ", callback_data="home")],
    ])


def withdraw_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("👛 Set / Change Wallet | ዋሌት ቀይር", callback_data="wallet")],
        [InlineKeyboardButton("🔙 Back | ተመለስ", callback_data="home")],
    ])


def admin_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📊 Statistics | ስታቲስቲክስ", callback_data="admin_stats"),
            InlineKeyboardButton("💰 Reward | ሽልማት", callback_data="admin_reward"),
        ],
        [
            InlineKeyboardButton("👥 Referrals | ሪፈራሎች", callback_data="admin_referrals"),
            InlineKeyboardButton("💸 Withdrawals | ማውጫዎች", callback_data="admin_withdrawals"),
        ],
        [
            InlineKeyboardButton("⚠️ Suspicious | ጥርጣሬ", callback_data="admin_suspicious"),
            InlineKeyboardButton("🎯 Add Task | ተግባር ጨምር", callback_data="admin_add_task"),
        ],
        [
            InlineKeyboardButton("🧪 Test Balance | የሙከራ ቀሪ", callback_data="admin_test_balance"),
        ],
    ])


# ============================================================
# TEXT SCREENS
# ============================================================

async def show_home(query):
    await query.edit_message_text(
        "💎 <b>Global Cash Bot</b>\n\n"
        "Welcome! እዚህ በTasks እና Referral በመስራት "
        "balance መሰብሰብ ይችላሉ።\n"
        "You can earn ETB by completing tasks and referrals.\n\n"
        "Choose an option below | ከታች ያለውን ይምረጡ 👇",
        reply_markup=main_keyboard(),
        parse_mode="HTML",
    )


async def show_balance(query, user_id):
    balance = get_balance(user_id)

    await query.edit_message_text(
        "💰 <b>Your Balance | የእርስዎ ቀሪ ሂሳብ</b>\n\n"
        f"💵 Balance | ቀሪ ሂሳብ: <b>{balance:.2f} ETB</b>\n"
        f"📌 Minimum Withdrawal | ዝቅተኛ ማውጫ: <b>{MIN_WITHDRAWAL:.2f} ETB</b>\n\n"
        "Keep earning and withdraw when you reach the minimum. | ቀሪ ሂሳብዎ ዝቅተኛውን መጠን ሲደርስ ማውጣት ይችላሉ።",
        reply_markup=balance_keyboard(),
        parse_mode="HTML",
    )


async def show_referral(query, user_id):
    count = get_referral_count(user_id)
    reward = get_referral_reward()

    link = f"https://t.me/{BOT_USERNAME}?start={user_id}"

    await query.edit_message_text(
        "👥 <b>Referral Program | የሪፈራል ፕሮግራም</b>\n\n"
        f"✅ Successful Referrals: <b>{count}</b>\n"
        f"🎁 Current Reward: <b>{reward:.2f} ETB</b> / successful referral\n\n"
        "Invite your friends using your personal link.\n"
        "The reward is paid after the referred user completes verification.\n\n"
        f"🔗 <code>{escape(link)}</code>",
        reply_markup=referral_keyboard(),
        parse_mode="HTML",
    )


async def show_tasks(query, user_id):
    tasks = get_active_tasks()

    if not tasks:
        await query.edit_message_text(
            "🎯 <b>Tasks</b>\n\n"
            "No active tasks right now.\n\n"
            "More earning tasks will be added soon.",
            reply_markup=back_keyboard(),
            parse_mode="HTML",
        )
        return

    buttons = []
    for task in tasks:
        status = "✅ Paid" if user_task_paid(user_id, task["id"]) else "🎯 Open"
        buttons.append([
            InlineKeyboardButton(
                f"{status} | {task['title'][:25]}",
                callback_data=f"task_{task['id']}",
            )
        ])

    buttons.append([
        InlineKeyboardButton("🔙 Back | ተመለስ", callback_data="home")
    ])

    await query.edit_message_text(
        "🎯 <b>Available Tasks | ያሉ ተግባራት</b>\n\n"
        "Complete a task and verify it to receive the reward. | ተግባሩን ጨርሰው Verify በማድረግ ሽልማቱን ይቀበሉ።",
        reply_markup=InlineKeyboardMarkup(buttons),
        parse_mode="HTML",
    )


async def show_wallet(query, user_id):
    wallet = get_wallet(user_id)

    if wallet:
        wallet_text = (
            f"Current Wallet: <b>{escape(wallet[0])}</b>\n"
            f"Number: <code>{escape(wallet[1])}</code>\n\n"
        )
    else:
        wallet_text = "No wallet saved yet.\n\n"

    await query.edit_message_text(
        "👛 <b>Wallet | ዋሌት</b>\n\n"
        + wallet_text +
        "Choose a method below. Saving another method will make it your active wallet.",
        reply_markup=wallet_keyboard(),
        parse_mode="HTML",
    )


async def show_withdraw(query, user_id, context):
    row = get_user(user_id)

    if not row:
        await query.edit_message_text(
            "❌ User not found.",
            reply_markup=back_keyboard(),
        )
        return

    if row["suspicious"]:
        await query.edit_message_text(
            "⚠️ <b>Security Review</b>\n\n"
            "Your account is currently under security review.\n\n"
            f"📞 Contact: {SUPPORT_USERNAME}",
            reply_markup=back_keyboard(),
            parse_mode="HTML",
        )
        return

    balance = float(row["balance"])

    if balance < MIN_WITHDRAWAL:
        await query.edit_message_text(
            "💸 <b>Withdraw | ገንዘብ አውጣ</b>\n\n"
            f"💰 Your Balance: <b>{balance:.2f} ETB</b>\n"
            f"📌 Minimum: <b>{MIN_WITHDRAWAL:.2f} ETB</b>\n\n"
            "ቢያንስ 30 ETB ሲደርስ withdrawal ማድረግ ይችላሉ።",
            reply_markup=withdraw_keyboard(),
            parse_mode="HTML",
        )
        return

    wallet = get_wallet(user_id)

    if not wallet:
        await query.edit_message_text(
            "👛 <b>Wallet Required</b>\n\n"
            "Withdrawal ከማድረግዎ በፊት CBE ወይም Telebirr "
            "wallet ያስቀምጡ።",
            reply_markup=wallet_keyboard(),
            parse_mode="HTML",
        )
        return

    # IMPORTANT:
    # context is explicitly passed here.
    # This fixes the previous Withdraw button bug.
    context.user_data["withdraw_mode"] = True

    await query.edit_message_text(
        "💸 <b>Withdraw | ገንዘብ አውጣ</b>\n\n"
        f"💰 Available Balance: <b>{balance:.2f} ETB</b>\n"
        f"🏦 Method: <b>{escape(wallet[0])}</b>\n"
        f"🔢 Wallet: <code>{escape(wallet[1])}</code>\n\n"
        "የሚወጣውን amount በETB ቁጥር ያስገቡ።\n\n"
        "Example: <code>30</code>\n\n"
        "Cancel: /cancel",
        parse_mode="HTML",
    )


async def show_support(query):
    await query.edit_message_text(
        "📢 <b>Advertising & Telegram Promotion | ማስታወቂያ እና Telegram Promotion</b>\n\n"
        "We provide promotion services for:\n\n"
        "📣 Channel Promotion\n"
        "📈 Channel Growth\n"
        "👥 Group Advertising\n"
        "🏢 Business Promotion\n"
        "📱 App & Website Promotion\n"
        "✨ Custom Promotion\n\n"
        f"📞 Contact Support: {SUPPORT_USERNAME}\n\n"
        "For price and more information, contact support.",
        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "💬 Contact Support",
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
# /START
# ============================================================

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

    create_or_update_user(user, referred_by)

    missing = await missing_channels(
        context.bot,
        user.id,
    )

    if missing:
        set_joined_all(user.id, False)

        await update.message.reply_text(
            "💎 <b>Welcome to Global Cash Bot</b>\n\n"
            "To continue, please verify all required channels below.\n\n"
            "ከታች ያሉትን ሁሉንም channels ከተቀላቀሉ "
            "በኋላ <b>Verify</b> ይጫኑ።",
            reply_markup=channel_keyboard(missing),
            parse_mode="HTML",
        )
        return

    set_joined_all(user.id, True)

    reward = process_referral_reward(user.id)

    if reward:
        try:
            await context.bot.send_message(
                chat_id=reward["inviter_id"],
                text=(
                    "🎉 <b>New Successful Referral!</b>\n\n"
                    f"👤 User ID: <code>{reward['referred_id']}</code>\n"
                    f"🎁 Reward: <b>{reward['amount']:.2f} ETB</b>\n\n"
                    "The reward has been added to your balance."
                ),
                parse_mode="HTML",
            )
        except Exception:
            logger.exception("Could not notify inviter")

    await update.message.reply_text(
        "💎 <b>Global Cash Bot</b>\n\n"
        "✅ <b>Verified</b>\n\n"
        "Welcome! Choose what you want to do below 👇",
        reply_markup=main_keyboard(),
        parse_mode="HTML",
    )


# ============================================================
# VERIFY
# ============================================================

async def verify_user(query, context):
    user = query.from_user

    missing = await missing_channels(
        context.bot,
        user.id,
    )

    if missing:
        set_joined_all(user.id, False)

        names = "\n".join(
            f"• {escape(username)}"
            for username, _ in missing
        )

        await query.edit_message_text(
            "❌ <b>Verification Failed</b>\n\n"
            "You still need to join these channels:\n\n"
            f"{names}\n\n"
            "After joining them, press Verify again.",
            reply_markup=channel_keyboard(missing),
            parse_mode="HTML",
        )
        return

    set_joined_all(user.id, True)

    reward = process_referral_reward(user.id)

    if reward:
        try:
            await context.bot.send_message(
                chat_id=reward["inviter_id"],
                text=(
                    "🎉 <b>Successful Referral!</b>\n\n"
                    f"🎁 Reward: <b>{reward['amount']:.2f} ETB</b>\n"
                    "has been added to your balance."
                ),
                parse_mode="HTML",
            )
        except Exception:
            logger.exception("Referral notification failed")

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

async def handle_wallet_number(update, context):
    user = update.effective_user
    text = (update.message.text or "").strip()

    wallet_type = context.user_data.get("wallet_type")

    if not wallet_type:
        return False

    if wallet_type == "CBE":
        if not re.fullmatch(r"1000\d{9}", text):
            await update.message.reply_text(
                "❌ <b>Invalid CBE Account</b>\n\n"
                "CBE must be exactly 13 digits and start with 1000.\n\n"
                "Example: <code>1000123456789</code>\n\n"
                "Try again or use /cancel.",
                parse_mode="HTML",
            )
            return True

    elif wallet_type == "Telebirr":
        if not re.fullmatch(r"(09|07)\d{8}", text):
            await update.message.reply_text(
                "❌ <b>Invalid Telebirr Number</b>\n\n"
                "Telebirr must be exactly 10 digits and start with 09 or 07.\n\n"
                "Example: <code>0912345678</code>\n\n"
                "Try again or use /cancel.",
                parse_mode="HTML",
            )
            return True

    if wallet_exists_for_other_user(
        wallet_type,
        text,
        user.id,
    ):
        mark_suspicious(user.id, 1)
        context.user_data.pop("wallet_type", None)

        await update.message.reply_text(
            "⚠️ <b>Wallet Security Check</b>\n\n"
            "This wallet is already registered to another account.\n"
            "Your account has been placed under security review.\n\n"
            f"📞 Contact: {SUPPORT_USERNAME}",
            parse_mode="HTML",
        )
        return True

    if not set_wallet(user.id, wallet_type, text):
        await update.message.reply_text(
            "❌ Could not save the wallet. Please try again.",
        )
        return True

    context.user_data.pop("wallet_type", None)

    await update.message.reply_text(
        "✅ <b>Wallet Saved</b>\n\n"
        f"🏦 Method: <b>{escape(wallet_type)}</b>\n"
        f"🔢 Number: <code>{escape(text)}</code>\n\n"
        "This is now your active withdrawal wallet.",
        reply_markup=main_keyboard(),
        parse_mode="HTML",
    )
    return True


# ============================================================
# WITHDRAW INPUT
# ============================================================

async def handle_withdraw_amount(update, context):
    user = update.effective_user
    text = (update.message.text or "").strip()

    if not context.user_data.get("withdraw_mode"):
        return False

    try:
        amount = float(text)
    except ValueError:
        await update.message.reply_text(
            "❌ Please enter a valid number.\n\n"
            "Example: 30",
        )
        return True

    if amount < MIN_WITHDRAWAL:
        await update.message.reply_text(
            f"❌ Minimum withdrawal is {MIN_WITHDRAWAL:.0f} ETB.",
        )
        return True

    if amount <= 0:
        await update.message.reply_text(
            "❌ Amount must be greater than 0.",
        )
        return True

    row = get_user(user.id)

    if not row:
        context.user_data.pop("withdraw_mode", None)
        await update.message.reply_text("❌ User not found.")
        return True

    if row["suspicious"]:
        context.user_data.pop("withdraw_mode", None)
        await update.message.reply_text(
            "⚠️ Your account is under security review.\n"
            f"Contact: {SUPPORT_USERNAME}",
        )
        return True

    wallet = get_wallet(user.id)

    if not wallet:
        context.user_data.pop("withdraw_mode", None)
        await update.message.reply_text(
            "❌ No active wallet found.\n"
            "Please save a CBE or Telebirr wallet first.",
            reply_markup=main_keyboard(),
        )
        return True

    conn = db()

    try:
        # Deduct only if enough balance exists.
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
            context.user_data.pop("withdraw_mode", None)
            return True

        cur = conn.execute("""
            INSERT INTO withdrawals(
                user_id, amount,
                wallet_type, wallet_number,
                status, created_at
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
        logger.exception("Withdrawal creation error")
        await update.message.reply_text(
            "❌ Withdrawal could not be created. Please try again.",
            reply_markup=main_keyboard(),
        )
        context.user_data.pop("withdraw_mode", None)
        return True

    finally:
        conn.close()

    context.user_data.pop("withdraw_mode", None)

    await update.message.reply_text(
        "✅ <b>Withdrawal Submitted</b>\n\n"
        f"💰 Amount: <b>{amount:.2f} ETB</b>\n"
        f"🏦 Method: <b>{escape(wallet[0])}</b>\n"
        f"🔢 Wallet: <code>{escape(wallet[1])}</code>\n"
        f"🆔 Request: <code>#{withdrawal_id}</code>\n\n"
        "⏳ Status: <b>Pending</b>",
        reply_markup=main_keyboard(),
        parse_mode="HTML",
    )

    # Notify admin.
    if ADMIN_ID:
        try:
            await context.bot.send_message(
                chat_id=ADMIN_ID,
                text=(
                    "💳 <b>New Withdrawal</b>\n\n"
                    f"🆔 Request: <code>#{withdrawal_id}</code>\n"
                    f"👤 User ID: <code>{user.id}</code>\n"
                    f"👤 Username: @{escape(user.username or 'N/A')}\n"
                    f"💰 Amount: <b>{amount:.2f} ETB</b>\n"
                    f"🏦 Method: <b>{escape(wallet[0])}</b>\n"
                    f"🔢 Wallet: <code>{escape(wallet[1])}</code>\n\n"
                    "⏳ Status: <b>Pending</b>"
                ),
                reply_markup=InlineKeyboardMarkup([
                    [
                        InlineKeyboardButton(
                            "✅ Approve",
                            callback_data=f"approve_wd_{withdrawal_id}",
                        ),
                        InlineKeyboardButton(
                            "❌ Reject",
                            callback_data=f"reject_wd_{withdrawal_id}",
                        ),
                    ]
                ]),
                parse_mode="HTML",
            )
        except Exception:
            logger.exception("Admin withdrawal notification failed")

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
        "SELECT COUNT(*) AS c FROM users WHERE joined_all=1"
    ).fetchone()["c"]

    balance = conn.execute(
        "SELECT COALESCE(SUM(balance),0) AS s FROM users"
    ).fetchone()["s"]

    referrals = conn.execute(
        "SELECT COUNT(*) AS c FROM referrals WHERE status='paid'"
    ).fetchone()["c"]

    pending = conn.execute(
        "SELECT COUNT(*) AS c FROM withdrawals WHERE status='pending'"
    ).fetchone()["c"]

    suspicious = conn.execute(
        "SELECT COUNT(*) AS c FROM users WHERE suspicious=1"
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


async def show_admin(query):
    await query.edit_message_text(
        "🛠 <b>Admin Panel | የአድሚን ፓነል</b>\n\n"
        "Choose an option | አማራጭ ይምረጡ:",
        reply_markup=admin_keyboard(),
        parse_mode="HTML",
    )


async def show_admin_stats(query):
    stats = get_stats()

    await query.edit_message_text(
        "📊 <b>Bot Statistics</b>\n\n"
        f"👥 Total Users: <b>{stats[0]}</b>\n"
        f"✅ Verified Users: <b>{stats[1]}</b>\n"
        f"💰 Total Balance: <b>{stats[2]:.2f} ETB</b>\n"
        f"🎁 Successful Referrals: <b>{stats[3]}</b>\n"
        f"💸 Pending Withdrawals: <b>{stats[4]}</b>\n"
        f"⚠️ Suspicious Accounts: <b>{stats[5]}</b>\n\n"
        f"🎁 Referral Reward: <b>{get_referral_reward():.2f} ETB</b>",
        reply_markup=back_keyboard(),
        parse_mode="HTML",
    )


async def show_admin_reward(query, context):
    context.user_data["admin_mode"] = "reward"

    await query.edit_message_text(
        "💰 <b>Referral Reward Settings</b>\n\n"
        f"Current Reward: <b>{get_referral_reward():.2f} ETB</b>\n\n"
        "Enter the new amount.\n\n"
        "Example: <code>2.00</code>\n"
        "Example: <code>1.50</code>\n\n"
        "This applies only to new successful referrals.\n"
        "Previously paid rewards will not change.\n\n"
        "Cancel: /cancel",
        parse_mode="HTML",
    )


async def handle_admin_reward(update, context):
    if context.user_data.get("admin_mode") != "reward":
        return False

    try:
        amount = float((update.message.text or "").strip())
    except ValueError:
        await update.message.reply_text(
            "❌ Enter a valid number. Example: 2.00"
        )
        return True

    if amount <= 0:
        await update.message.reply_text(
            "❌ Reward must be greater than 0."
        )
        return True

    set_setting("referral_reward", f"{amount:.2f}")
    context.user_data.pop("admin_mode", None)

    await update.message.reply_text(
        "✅ <b>Referral Reward Updated</b>\n\n"
        f"New Reward: <b>{amount:.2f} ETB</b>\n\n"
        "This amount will be used for new successful referrals.",
        reply_markup=admin_keyboard(),
        parse_mode="HTML",
    )
    return True


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
        text = "👥 <b>Successful Referrals</b>\n\nNo successful referrals yet."
    else:
        lines = ["👥 <b>Successful Referrals</b>\n"]
        for r in rows:
            name = (
                f"@{r['username']}"
                if r["username"]
                else (r["first_name"] or str(r["referred_id"]))
            )
            lines.append(
                f"👤 Referrer: <code>{r['referrer_id']}</code>\n"
                f"   ↳ {escape(name)} | ID <code>{r['referred_id']}</code>\n"
                f"   🎁 {r['reward_amount']:.2f} ETB\n"
            )
        text = "\n".join(lines)

    await query.edit_message_text(
        text,
        reply_markup=back_keyboard(),
        parse_mode="HTML",
    )


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
            reply_markup=back_keyboard(),
            parse_mode="HTML",
        )
        return

    buttons = []
    for r in rows:
        buttons.append([
            InlineKeyboardButton(
                f"#{r['id']} | {r['amount']:.2f} ETB",
                callback_data=f"admin_wd_{r['id']}",
            )
        ])

    buttons.append([
        InlineKeyboardButton("🔙 Back | ተመለስ", callback_data="admin")
    ])

    await query.edit_message_text(
        "💸 <b>Pending Withdrawals</b>\n\n"
        "Select a request:",
        reply_markup=InlineKeyboardMarkup(buttons),
        parse_mode="HTML",
    )


async def show_admin_withdrawal_detail(query, withdrawal_id):
    conn = db()
    row = conn.execute("""
        SELECT w.*, u.username, u.first_name
        FROM withdrawals w
        LEFT JOIN users u ON u.user_id=w.user_id
        WHERE w.id=?
    """, (withdrawal_id,)).fetchone()
    conn.close()

    if not row:
        await query.edit_message_text(
            "❌ Withdrawal not found.",
            reply_markup=back_keyboard(),
        )
        return

    username = f"@{row['username']}" if row["username"] else "N/A"

    buttons = []

    if row["status"] == "pending":
        buttons.append([
            InlineKeyboardButton(
                "✅ Approve",
                callback_data=f"approve_wd_{withdrawal_id}",
            ),
            InlineKeyboardButton(
                "❌ Reject",
                callback_data=f"reject_wd_{withdrawal_id}",
            ),
        ])

    buttons.append([
        InlineKeyboardButton(
            "🔙 Back",
            callback_data="admin_withdrawals",
        )
    ])

    await query.edit_message_text(
        "💳 <b>Withdrawal Details</b>\n\n"
        f"🆔 Request: <code>#{row['id']}</code>\n"
        f"👤 User ID: <code>{row['user_id']}</code>\n"
        f"👤 Username: {escape(username)}\n"
        f"💰 Amount: <b>{row['amount']:.2f} ETB</b>\n"
        f"🏦 Method: <b>{escape(row['wallet_type'])}</b>\n"
        f"🔢 Wallet: <code>{escape(row['wallet_number'])}</code>\n"
        f"📌 Status: <b>{escape(row['status'])}</b>",
        reply_markup=InlineKeyboardMarkup(buttons),
        parse_mode="HTML",
    )


async def approve_withdrawal(query, context, withdrawal_id):
    conn = db()

    try:
        row = conn.execute("""
            SELECT *
            FROM withdrawals
            WHERE id=?
        """, (withdrawal_id,)).fetchone()

        if not row:
            conn.close()
            await query.answer("Withdrawal not found.", show_alert=True)
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
            WHERE id=? AND status='pending'
        """, (withdrawal_id,))

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
        logger.exception("Approve withdrawal error")
        await query.answer("Approval failed.", show_alert=True)
        return

    finally:
        conn.close()

    await query.edit_message_text(
        "✅ <b>Withdrawal Approved</b>\n\n"
        f"Request: <code>#{withdrawal_id}</code>\n"
        f"Amount: <b>{row['amount']:.2f} ETB</b>\n"
        f"Wallet: <code>{escape(row['wallet_number'])}</code>",
        parse_mode="HTML",
    )

    try:
        await context.bot.send_message(
            chat_id=row["user_id"],
            text=(
                "✅ <b>Withdrawal Approved</b>\n\n"
                f"💰 Amount: <b>{row['amount']:.2f} ETB</b>\n"
                f"🏦 Method: <b>{escape(row['wallet_type'])}</b>\n"
                f"🔢 Wallet: <code>{escape(row['wallet_number'])}</code>\n\n"
                "Your withdrawal has been approved."
            ),
            parse_mode="HTML",
        )
    except Exception:
        logger.exception("Could not notify withdrawal user")


async def reject_withdrawal(query, context, withdrawal_id):
    conn = db()

    try:
        row = conn.execute("""
            SELECT *
            FROM withdrawals
            WHERE id=?
        """, (withdrawal_id,)).fetchone()

        if not row:
            conn.close()
            await query.answer("Withdrawal not found.", show_alert=True)
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
            SET status='rejected'
            WHERE id=? AND status='pending'
        """, (withdrawal_id,))

        if cur.rowcount != 1:
            conn.rollback()
            conn.close()
            await query.answer(
                "Request was already processed.",
                show_alert=True,
            )
            return

        # Refund exactly once because the status changed from pending
        # to rejected in this same transaction.
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
        logger.exception("Reject withdrawal error")
        await query.answer("Rejection failed.", show_alert=True)
        return

    finally:
        conn.close()

    await query.edit_message_text(
        "❌ <b>Withdrawal Rejected</b>\n\n"
        f"Request: <code>#{withdrawal_id}</code>\n"
        f"Refunded: <b>{row['amount']:.2f} ETB</b>",
        parse_mode="HTML",
    )

    try:
        await context.bot.send_message(
            chat_id=row["user_id"],
            text=(
                "❌ <b>Withdrawal Rejected</b>\n\n"
                f"💰 Refunded: <b>{row['amount']:.2f} ETB</b>\n\n"
                "The amount has been returned to your balance.\n"
                f"For questions: {SUPPORT_USERNAME}"
            ),
            parse_mode="HTML",
        )
    except Exception:
        logger.exception("Could not notify rejected user")


async def show_admin_suspicious(query):
    conn = db()
    rows = conn.execute("""
        SELECT user_id, username, first_name, balance
        FROM users
        WHERE suspicious=1
        ORDER BY user_id DESC
        LIMIT 30
    """).fetchall()
    conn.close()

    if not rows:
        text = "⚠️ <b>Suspicious Accounts</b>\n\nNone."
    else:
        lines = ["⚠️ <b>Suspicious Accounts</b>\n"]
        for r in rows:
            name = (
                f"@{r['username']}"
                if r["username"]
                else (r["first_name"] or "No name")
            )
            lines.append(
                f"👤 {escape(name)}\n"
                f"ID: <code>{r['user_id']}</code>\n"
                f"Balance: <b>{r['balance']:.2f} ETB</b>\n"
            )
        text = "\n".join(lines)

    await query.edit_message_text(
        text,
        reply_markup=back_keyboard(),
        parse_mode="HTML",
    )


# ============================================================
# ADMIN TASK CREATION
# ============================================================

async def start_add_task(query, context):
    context.user_data["task_step"] = "title"

    await query.edit_message_text(
        "🎯 <b>Add New Task</b>\n\n"
        "Step 1/5\n"
        "Send the task title.\n\n"
        "Example: <code>Join Daily Money Channel</code>\n\n"
        "Cancel: /cancel",
        parse_mode="HTML",
    )


async def start_test_balance(query, context):
    if not admin_only(query.from_user.id):
        return

    context.user_data.clear()
    context.user_data["admin_step"] = "test_balance_user"

    await query.edit_message_text(
        "🧪 <b>Add Test Balance</b>\n\n"
        "ይህ feature ለሙከራ ብቻ ነው።\n\n"
        "Step 1/2: Enter User ID.\n\n"
        "Example:\n"
        "<code>8727153413</code>",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("Cancel ❌", callback_data="admin")]
        ]),
        parse_mode="HTML",
    )


async def handle_admin_test_balance(update, context):
    step = context.user_data.get("admin_step")
    if step not in ("test_balance_user", "test_balance_amount"):
        return False

    text = (update.message.text or "").strip()

    if step == "test_balance_user":
        try:
            target_user_id = int(text)
            if target_user_id <= 0:
                raise ValueError
        except ValueError:
            await update.message.reply_text(
                "❌ Invalid User ID!\n\n"
                "እባክዎን ትክክለኛ Telegram User ID ያስገቡ።\n\n"
                "Example:\n<code>8727153413</code>",
                parse_mode="HTML",
            )
            return True

        target_user = get_user(target_user_id)
        if not target_user:
            await update.message.reply_text(
                "❌ <b>User Not Found!</b>\n\n"
                f"User ID <code>{target_user_id}</code> በbot database ውስጥ አልተገኘም።\n\n"
                "ተጠቃሚው መጀመሪያ /start ብሎ Bot መጀመር አለበት።",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("Admin Panel 🛠", callback_data="admin")]
                ]),
            )
            return True

        context.user_data["test_balance_user_id"] = target_user_id
        context.user_data["admin_step"] = "test_balance_amount"

        await update.message.reply_text(
            "🧪 <b>Add Test Balance</b>\n\n"
            f"👤 User ID: <code>{target_user_id}</code>\n"
            f"💰 Current Balance: <b>{float(target_user['balance']):.2f} ETB</b>\n\n"
            "Step 2/2: Enter amount to add.\n\n"
            "ለምሳሌ፦\n<code>100</code>",
            parse_mode="HTML",
        )
        return True

    if step == "test_balance_amount":
        try:
            amount = float(text)
            if amount <= 0:
                raise ValueError
        except ValueError:
            await update.message.reply_text(
                "❌ Invalid amount!\n\n"
                "ከ 0 በላይ የሆነ ቁጥር ያስገቡ።\n\n"
                "Example:\n<code>100</code>",
                parse_mode="HTML",
            )
            return True

        target_user_id = context.user_data.get("test_balance_user_id")
        if not target_user_id:
            context.user_data.clear()
            await update.message.reply_text(
                "❌ Test balance session expired.\nእባክዎን እንደገና ይጀምሩ።",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("Admin Panel 🛠", callback_data="admin")]
                ]),
            )
            return True

        target_user = get_user(target_user_id)
        if not target_user:
            context.user_data.clear()
            await update.message.reply_text(
                "❌ User not found!",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("Admin Panel 🛠", callback_data="admin")]
                ]),
            )
            return True

        old_balance = float(target_user["balance"])
        add_balance(target_user_id, amount)
        new_balance = old_balance + amount
        context.user_data.clear()

        await update.message.reply_text(
            "✅ <b>Test Balance Added Successfully!</b>\n\n"
            f"👤 User ID: <code>{target_user_id}</code>\n"
            f"💰 Added: <b>+{amount:.2f} ETB</b>\n"
            f"💵 Old Balance: <b>{old_balance:.2f} ETB</b>\n"
            f"💵 New Balance: <b>{new_balance:.2f} ETB</b>\n\n"
            "🧪 This is a bot database test balance.\n"
            "ይህ ለሙከራ ብቻ የተጨመረ የBot Balance ነው።\n"
            "በCBE ወይም Telebirr ገንዘብ አልተላከም።",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("Admin Panel 🛠", callback_data="admin")]
            ]),
            parse_mode="HTML",
        )

        try:
            await context.bot.send_message(
                chat_id=target_user_id,
                text=(
                    "🧪 <b>Test Balance Added</b>\n\n"
                    f"💰 Added: <b>+{amount:.2f} ETB</b>\n"
                    f"💵 New Balance: <b>{new_balance:.2f} ETB</b>\n\n"
                    "ይህ ለBot ሙከራ ብቻ የተጨመረ Test Balance ነው።"
                ),
                parse_mode="HTML",
            )
        except Exception:
            logger.exception("Could not notify test user.")

        return True

    return False


async def handle_admin_task_creation(update, context):
    step = context.user_data.get("task_step")

    if not step:
        return False

    text = (update.message.text or "").strip()

    if step == "title":
        context.user_data["new_task_title"] = text
        context.user_data["task_step"] = "description"

        await update.message.reply_text(
            "Step 2/5\nSend task description."
        )
        return True

    if step == "description":
        context.user_data["new_task_description"] = text
        context.user_data["task_step"] = "username"

        await update.message.reply_text(
            "Step 3/5\nSend channel username.\n\n"
            "Example: @ExampleChannel"
        )
        return True

    if step == "username":
        if not text.startswith("@"):
            await update.message.reply_text(
                "❌ Username must start with @."
            )
            return True

        context.user_data["new_task_username"] = text
        context.user_data["task_step"] = "url"

        await update.message.reply_text(
            "Step 4/5\nSend channel join URL.\n\n"
            "Example: https://t.me/ExampleChannel"
        )
        return True

    if step == "url":
        if not text.startswith("https://t.me/"):
            await update.message.reply_text(
                "❌ Please send a valid Telegram URL."
            )
            return True

        context.user_data["new_task_url"] = text
        context.user_data["task_step"] = "reward"

        await update.message.reply_text(
            "Step 5/5\nSend task reward in ETB.\n\n"
            "Example: 1.50"
        )
        return True

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
            context.user_data["new_task_title"],
            context.user_data["new_task_description"],
            context.user_data["new_task_username"],
            context.user_data["new_task_url"],
            reward,
        )

        for key in [
            "task_step",
            "new_task_title",
            "new_task_description",
            "new_task_username",
            "new_task_url",
        ]:
            context.user_data.pop(key, None)

        await update.message.reply_text(
            "✅ <b>Task Created</b>\n\n"
            f"🆔 Task ID: <code>{task_id}</code>\n"
            f"🎁 Reward: <b>{reward:.2f} ETB</b>",
            reply_markup=admin_keyboard(),
            parse_mode="HTML",
        )
        return True

    return False


# ============================================================
# TASK VERIFICATION
# ============================================================

async def show_task_detail(query, context, task_id):
    task = get_task(task_id)

    if not task or not task["active"]:
        await query.edit_message_text(
            "❌ Task not found or inactive.",
            reply_markup=back_keyboard(),
        )
        return

    user_id = query.from_user.id
    paid = user_task_paid(user_id, task_id)

    if paid:
        buttons = [
            [InlineKeyboardButton("🔙 Back | ተመለስ", callback_data="tasks")]
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
                    callback_data=f"verify_task_{task_id}",
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
        f"🎁 Reward: <b>{task['reward']:.2f} ETB</b>\n"
        f"📢 Channel: <b>{escape(task['channel_username'])}</b>\n\n"
        + (
            "✅ You have already received this reward."
            if paid
            else "Join the channel and press Verify Task."
        ),
        reply_markup=InlineKeyboardMarkup(buttons),
        parse_mode="HTML",
    )


async def verify_task(query, context, task_id):
    user_id = query.from_user.id
    task = get_task(task_id)

    if not task or not task["active"]:
        await query.answer("Task is not available.", show_alert=True)
        return

    if user_task_paid(user_id, task_id):
        await query.answer(
            "You already received this reward.",
            show_alert=True,
        )
        return

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

    # Pay only once.
    if user_task_paid(user_id, task_id):
        await query.answer("Already paid.", show_alert=True)
        return

    add_balance(user_id, float(task["reward"]))
    save_user_task(user_id, task_id, True, True)

    await query.edit_message_text(
        "🎉 <b>Task Completed!</b>\n\n"
        f"🎁 Reward: <b>{task['reward']:.2f} ETB</b>\n"
        f"💰 New Balance: <b>{get_balance(user_id):.2f} ETB</b>",
        reply_markup=back_keyboard(),
        parse_mode="HTML",
    )


# ============================================================
# CALLBACKS
# ============================================================

async def callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user = update.effective_user
    data = query.data or ""

    create_or_update_user(user)

    # Admin-only callback protection.
    if (
        data.startswith("admin")
        or data.startswith("approve_wd_")
        or data.startswith("reject_wd_")
    ):
        if not admin_only(user.id):
            await query.answer(
                "⛔ Admin only.",
                show_alert=True,
            )
            return

    try:
        if data == "home":
            context.user_data.clear()
            await show_home(query)
            return

        if data == "verify":
            await verify_user(query, context)
            return

        if data == "balance":
            await show_balance(query, user.id)
            return

        if data == "referral":
            await show_referral(query, user.id)
            return

        if data == "share_ref":
            link = f"https://t.me/{BOT_USERNAME}?start={user.id}"

            await query.edit_message_text(
                "📤 <b>Share Your Referral Link</b>\n\n"
                f"<code>{escape(link)}</code>\n\n"
                "Copy the link and share it with your friends.",
                reply_markup=referral_keyboard(),
                parse_mode="HTML",
            )
            return

        if data == "tasks":
            await show_tasks(query, user.id)
            return

        if data.startswith("task_"):
            task_id = int(data.split("_", 1)[1])
            await show_task_detail(query, context, task_id)
            return

        if data.startswith("verify_task_"):
            task_id = int(data.split("_")[-1])
            await verify_task(query, context, task_id)
            return

        if data == "wallet":
            await show_wallet(query, user.id)
            return

        if data == "wallet_cbe":
            context.user_data["wallet_type"] = "CBE"

            await query.edit_message_text(
                "🏦 <b>CBE Wallet</b>\n\n"
                "Send your 13-digit CBE account number.\n"
                "It must start with <b>1000</b>.\n\n"
                "Example: <code>1000123456789</code>\n\n"
                "Cancel: /cancel",
                parse_mode="HTML",
            )
            return

        if data == "wallet_telebirr":
            context.user_data["wallet_type"] = "Telebirr"

            await query.edit_message_text(
                "📱 <b>Telebirr Wallet</b>\n\n"
                "Send your 10-digit Telebirr number.\n"
                "It must start with <b>09</b> or <b>07</b>.\n\n"
                "Example: <code>0912345678</code>\n\n"
                "Cancel: /cancel",
                parse_mode="HTML",
            )
            return

        # ====================================================
        # IMPORTANT FIX:
        # show_withdraw now receives context.
        # ====================================================
        if data == "withdraw":
            await show_withdraw(query, user.id, context)
            return

        if data == "support":
            await show_support(query)
            return

        # ---------------- ADMIN ----------------

        if data == "admin":
            await show_admin(query)
            return

        if data == "admin_stats":
            await show_admin_stats(query)
            return

        if data == "admin_reward":
            await show_admin_reward(query, context)
            return

        if data == "admin_referrals":
            await show_admin_referrals(query)
            return

        if data == "admin_withdrawals":
            await show_admin_withdrawals(query)
            return

        if data.startswith("admin_wd_"):
            withdrawal_id = int(data.split("_")[-1])
            await show_admin_withdrawal_detail(
                query,
                withdrawal_id,
            )
            return

        if data == "admin_suspicious":
            await show_admin_suspicious(query)
            return

        if data == "admin_add_task":
            await start_add_task(query, context)
            return

        if data == "admin_test_balance":
            await start_test_balance(query, context)
            return

        if data.startswith("approve_wd_"):
            withdrawal_id = int(data.split("_")[-1])
            await approve_withdrawal(
                query,
                context,
                withdrawal_id,
            )
            return

        if data.startswith("reject_wd_"):
            withdrawal_id = int(data.split("_")[-1])
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

async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not admin_only(update.effective_user.id):
        await update.message.reply_text("⛔ Admin only.")
        return

    await update.message.reply_text(
        "🛠 <b>Global Cash Admin Panel | የGlobal Cash አድሚን ፓነል</b>",
        reply_markup=admin_keyboard(),
        parse_mode="HTML",
    )


# ============================================================
# CANCEL
# ============================================================

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()

    if admin_only(update.effective_user.id):
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

async def message_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return

    user = update.effective_user
    create_or_update_user(user)

    # Admin reward input.
    if admin_only(user.id):
        if await handle_admin_reward(update, context):
            return

        if await handle_admin_task_creation(update, context):
            return

        if await handle_admin_test_balance(update, context):
            return

    # Wallet input.
    if await handle_wallet_number(update, context):
        return

    # Withdrawal amount input.
    if await handle_withdraw_amount(update, context):
        return


# ============================================================
# ERROR HANDLER
# ============================================================

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
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
        CommandHandler("cancel", cancel)
    )

    application.add_handler(
        CallbackQueryHandler(callbacks)
    )

    application.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            message_router,
        )
    )

    application.add_error_handler(error_handler)

    logger.info("Global Cash Bot starting...")
    application.run_polling(
        allowed_updates=Update.ALL_TYPES
    )


if __name__ == "__main__":
    main()

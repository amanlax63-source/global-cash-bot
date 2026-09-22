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
from telegram.constants import ChatMemberStatus
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# ============================================================
# CONFIGURATION
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
# DATABASE SETUP
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
                joined_all INTEGER NOT NULL DEFAULT 0,
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
                channel_url TEXT NOT NULL,
                reward REAL NOT NULL,
                active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL
            )
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS task_proofs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                task_id INTEGER NOT NULL,
                photo_id TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                created_at TEXT NOT NULL
            )
        """)

        cur.execute("""
            INSERT OR IGNORE INTO settings(key, value)
            VALUES('referral_reward', '2.00')
        """)

def is_admin(user_id):
    return user_id == ADMIN_ID


# ============================================================
# DATA HELPERS
# ============================================================

def get_user(user_id):
    conn = db()
    row = conn.execute("SELECT * FROM users WHERE user_id=?", (user_id,)).fetchone()
    conn.close()
    return row

def create_or_update_user(tg_user, referred_by=None):
    existing = get_user(tg_user.id)
    with get_db_cursor() as cur:
        if existing is None:
            ref = referred_by if (referred_by and referred_by != tg_user.id) else None
            cur.execute("""
                INSERT INTO users(user_id, username, first_name, referred_by, created_at)
                VALUES(?,?,?,?,?)
            """, (tg_user.id, tg_user.username, tg_user.first_name or "", ref, now()))
        else:
            cur.execute("""
                UPDATE users SET username=?, first_name=? WHERE user_id=?
            """, (tg_user.username, tg_user.first_name or "", tg_user.id))

def get_all_user_ids():
    conn = db()
    rows = conn.execute("SELECT user_id FROM users").fetchall()
    conn.close()
    return [r["user_id"] for r in rows]

def get_balance(user_id):
    row = get_user(user_id)
    return float(row["balance"]) if row else 0.0

def get_wallet(user_id):
    row = get_user(user_id)
    if not row or not row["wallet_type"] or not row["wallet_number"]:
        return None
    return row["wallet_type"], row["wallet_number"]

def set_wallet(user_id, wallet_type, wallet_number):
    with get_db_cursor() as cur:
        cur.execute("""
            UPDATE users SET wallet_type=?, wallet_number=? WHERE user_id=?
        """, (wallet_type, wallet_number, user_id))

def get_referral_reward():
    conn = db()
    row = conn.execute("SELECT value FROM settings WHERE key='referral_reward'").fetchone()
    conn.close()
    return float(row["value"]) if row else 2.0

def set_referral_reward(new_reward):
    with get_db_cursor() as cur:
        cur.execute("UPDATE settings SET value=? WHERE key='referral_reward'", (str(new_reward),))

def get_referral_count(user_id):
    conn = db()
    row = conn.execute("SELECT COUNT(*) AS c FROM referrals WHERE referrer_id=? AND status='paid'", (user_id,)).fetchone()
    conn.close()
    return int(row["c"])

def get_user_referrals_list(user_id):
    conn = db()
    rows = conn.execute("""
        SELECT u.user_id, u.username, u.first_name, r.created_at
        FROM referrals r
        JOIN users u ON r.referred_id = u.user_id
        WHERE r.referrer_id = ? AND r.status='paid'
        ORDER BY r.id DESC
    """, (user_id,)).fetchall()
    conn.close()
    return rows

def process_referral_reward(referred_id):
    user = get_user(referred_id)
    if not user or not user["referred_by"] or user["referral_paid"]:
        return None

    inviter = user["referred_by"]
    reward = get_referral_reward()

    with get_db_cursor() as cur:
        cur.execute("UPDATE users SET balance=balance+? WHERE user_id=?", (reward, inviter))
        cur.execute("""
            INSERT INTO referrals(referrer_id, referred_id, reward_amount, status, created_at)
            VALUES(?,?,?,?,?)
        """, (inviter, referred_id, reward, "paid", now()))
        cur.execute("UPDATE users SET referral_paid=1 WHERE user_id=?", (referred_id,))

    return {"inviter_id": inviter, "referred_id": referred_id, "amount": reward}


# ============================================================
# KEYBOARDS
# ============================================================

def main_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Balance 💰", callback_data="balance"), InlineKeyboardButton("Referral 👥", callback_data="referral")],
        [InlineKeyboardButton("Tasks 🎯", callback_data="tasks"), InlineKeyboardButton("Withdraw 💸", callback_data="withdraw")],
        [InlineKeyboardButton("Wallet 👛", callback_data="wallet"), InlineKeyboardButton("Support 📞", callback_data="support")],
    ])

def back_keyboard():
    return InlineKeyboardMarkup([[InlineKeyboardButton("Back 🔙", callback_data="home")]])

def wallet_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("CBE Bank 🏦", callback_data="wallet_cbe"), InlineKeyboardButton("Telebirr 📱", callback_data="wallet_telebirr")],
        [InlineKeyboardButton("Back 🔙", callback_data="home")],
    ])

def admin_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Statistics 📊", callback_data="admin_stats"), InlineKeyboardButton("Withdrawals 💸", callback_data="admin_withdrawals")],
        [InlineKeyboardButton("Add Task 🎯", callback_data="admin_add_task"), InlineKeyboardButton("Verify Task Proofs 📥", callback_data="admin_proofs")],
        [InlineKeyboardButton("Top Referrers 👥", callback_data="admin_referrals"), InlineKeyboardButton("Set Referral Reward 💰", callback_data="admin_reward")],
    ])


# ============================================================
# USER HANDLERS
# ============================================================

async def missing_channels(bot, user_id):
    missing = []
    for username, url in REQUIRED_CHANNELS:
        try:
            member = await bot.get_chat_member(chat_id=username, user_id=user_id)
            if member.status not in (ChatMemberStatus.MEMBER, ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER):
                missing.append((username, url))
        except Exception:
            missing.append((username, url))
    return missing

async def show_home(update, context):
    text = "💰 <b>Global Cash Bot</b> 👋\n\nWelcome back! እባክዎን ከታች ካሉት አማራጮች ይምረጡ፦"
    if update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=main_keyboard(), parse_mode="HTML")
    else:
        await update.message.reply_text(text, reply_markup=main_keyboard(), parse_mode="HTML")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    referrer_id = int(context.args[0]) if (context.args and context.args[0].isdigit()) else None

    create_or_update_user(user, referred_by=referrer_id)
    missing = await missing_channels(context.bot, user.id)

    if missing:
        buttons = [[InlineKeyboardButton("📢 Join Channel", url=url)] for _, url in missing]
        buttons.append([InlineKeyboardButton("Verify ✅", callback_data="verify_channels")])
        await update.message.reply_text(
            "👋 <b>Welcome to Global Cash Bot!</b>\n\nTo use this bot, please join all required channels below:\nሁሉንም ቻናሎች ከተቀላቀሉ በኋላ <b>Verify ✅</b> የሚለውን ይጫኑ።",
            reply_markup=InlineKeyboardMarkup(buttons),
            parse_mode="HTML",
        )
        return

    process_referral_reward(user.id)
    await show_home(update, context)

async def verify_channels_callback(query, context):
    user_id = query.from_user.id
    missing = await missing_channels(context.bot, user_id)

    if missing:
        await query.answer("❌ ገና ሁሉንም ቻናሎች አልተቀላቀሉም!", show_alert=True)
        return

    process_referral_reward(user_id)
    await query.answer("✅ በትክክል ተረጋግጧል!")
    await show_home(query, context)


# ============================================================
# USER TASKS & PROOF HANDLER
# ============================================================

async def show_tasks(query, user_id):
    conn = db()
    tasks = conn.execute("SELECT * FROM tasks WHERE active=1 ORDER BY id DESC").fetchall()
    conn.close()

    if not tasks:
        await query.edit_message_text("🎯 <b>Tasks / የሚሰሩ ስራዎች</b>\n\nበአሁኑ ሰአት ምንም አዲስ Task የለም። አዲስ Task ሲኖር መልእክት ይደርስዎታል።", reply_markup=back_keyboard(), parse_mode="HTML")
        return

    buttons = []
    lines = ["🎯 <b>Available Tasks / የሚሰሩ ስራዎች</b>\n"]
    for t in tasks:
        lines.append(f"📌 <b>{escape(t['title'])}</b>\n💰 Reward: <b>{t['reward']:.2f} ETB</b>\n")
        buttons.append([InlineKeyboardButton(f"Start Task: {t['title']}", callback_data=f"usertask_{t['id']}")])

    buttons.append([InlineKeyboardButton("Back 🔙", callback_data="home")])
    await query.edit_message_text("\n".join(lines), reply_markup=InlineKeyboardMarkup(buttons), parse_mode="HTML")

async def show_user_task_detail(query, user_id, task_id, context):
    conn = db()
    task = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
    conn.close()

    if not task:
        await query.answer("Task not found!")
        return

    context.user_data["active_task_id"] = task_id

    text = (
        f"🎯 <b>{escape(task['title'])}</b>\n\n"
        f"📝 <b>መመሪያ፦</b> {escape(task['description'])}\n\n"
        f"🔗 <b>Channel Link:</b> {task['channel_url']}\n"
        f"💰 <b>Reward:</b> {task['reward']:.2f} ETB\n\n"
        "👉 <b>እንዴት መስራት ይቻላል?</b>\n"
        "1. ከላይ ያለውን Link ነክተው ቻናሉን ይቀላቀሉ (Join ያድርጉ)።\n"
        "2. Join ማድረጋችሁን የሚያሳይ <b>Screenshot (ስክሪንሹት)</b> ያንሱ።\n"
        "3. የስክሪንሹቱን ፎቶ ቀጥታ ለዚህ ቦት ይላኩ!"
    )
    buttons = [
        [InlineKeyboardButton("Open Channel 📢", url=task["channel_url"])],
        [InlineKeyboardButton("Back 🔙", callback_data="tasks")]
    ]
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(buttons), parse_mode="HTML")

async def handle_photo_proof(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    photo = update.message.photo[-1]

    active_task_id = context.user_data.get("active_task_id")

    if not active_task_id:
        await update.message.reply_text("⚠️ እባክዎን በመጀመሪያ ከ <b>Tasks 🎯</b> ገጽ ላይ መስራት የሚፈልጉትን Task ይምረጡ!", parse_mode="HTML")
        return

    conn = db()
    task = conn.execute("SELECT * FROM tasks WHERE id=?", (active_task_id,)).fetchone()
    
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO task_proofs (user_id, task_id, photo_id, status, created_at)
        VALUES (?, ?, ?, 'pending', ?)
    """, (user.id, active_task_id, photo.file_id, now()))
    proof_id = cur.lastrowid
    conn.commit()
    conn.close()

    context.user_data.pop("active_task_id", None)

    await update.message.reply_text(
        "✅ <b>Screenshot Successfully Sent!</b>\n\n"
        "የላኩት ስክሪንሹት ለአድሚን ተልኳል። አድሚኑ መርምሮ ሲያፀድቀው ክፍያው ቀጥታ ወደ ሂሳብዎ ይገባል!",
        reply_markup=main_keyboard(),
        parse_mode="HTML"
    )

    try:
        admin_buttons = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("Approve Task ✅", callback_data=f"approve_proof_{proof_id}"),
                InlineKeyboardButton("Reject Task ❌", callback_data=f"reject_proof_{proof_id}")
            ]
        ])
        await context.bot.send_photo(
            chat_id=ADMIN_ID,
            photo=photo.file_id,
            caption=(
                f"📥 <b>New Task Proof Submitted!</b>\n\n"
                f"👤 User: {user.first_name} (@{user.username or 'N/A'})\n"
                f"🆔 User ID: <code>{user.id}</code>\n"
                f"🎯 Task: <b>{escape(task['title'])}</b>\n"
                f"💰 Reward: <b>{task['reward']:.2f} ETB</b>"
            ),
            reply_markup=admin_buttons,
            parse_mode="HTML"
        )
    except Exception as e:
        logger.error(f"Failed to send proof to admin: {e}")


# ============================================================
# WITHDRAWAL PROCESS
# ============================================================

async def show_withdraw_start(query, user_id, context):
    wallet = get_wallet(user_id)
    if not wallet:
        await query.edit_message_text(
            "⚠️ <b>Connect Wallet First!</b>\n\nገንዘብ ለማውጣት በመጀመሪያ የክፍያ አካውንትዎን (CBE ወይም Telebirr) መመዝገብ አለብዎት።",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("Connect Wallet 👛", callback_data="wallet")],
                [InlineKeyboardButton("Back 🔙", callback_data="home")]
            ]),
            parse_mode="HTML"
        )
        return

    context.user_data["withdraw_step"] = "amount"
    balance = get_balance(user_id)
    await query.edit_message_text(
        f"💸 <b>Withdraw Money / ገንዘብ ማውጣት</b>\n\n"
        f"💰 Current Balance: <b>{balance:.2f} ETB</b>\n"
        f"📌 Minimum Withdrawal: <b>{MIN_WITHDRAWAL:.2f} ETB</b>\n\n"
        "👉 <b>ማውጣት የሚፈልጉትን መጠን ያስገቡ (በቁጥር)፦</b>",
        reply_markup=back_keyboard(),
        parse_mode="HTML"
    )

async def handle_withdraw_amount_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    text = update.message.text.strip()

    try:
        amount = float(text)
    except ValueError:
        await update.message.reply_text("❌ እባክዎን ትክክለኛ ቁጥር ያስገቡ!")
        return

    balance = get_balance(user.id)
    if amount > balance or amount < MIN_WITHDRAWAL:
        await update.message.reply_text(
            f"❌ <b>የቀሪ ሂሳብ ማሳሰቢያ!</b>\n\n"
            f"ያሎት balance <b>{balance:.2f} ETB</b> ነው። ዝቅተኛው ማውጫ <b>{MIN_WITHDRAWAL:.2f} ETB</b> ነው።\n"
            "እባክዎን ተጨማሪ ሰዎችን በ Refer በመጋበዝ Balance ያሳድጉ!",
            reply_markup=back_keyboard(),
            parse_mode="HTML"
        )
        context.user_data.pop("withdraw_step", None)
        return

    wallet_type, wallet_number = get_wallet(user.id)
    context.user_data["pending_withdraw_amount"] = amount
    context.user_data.pop("withdraw_step", None)

    await update.message.reply_text(
        f"💸 <b>Withdrawal Confirmation</b>\n\n"
        f"💰 Request Amount: <b>{amount:.2f} ETB</b>\n"
        f"💳 Method: <b>{wallet_type}</b>\n"
        f"🔢 Account: <code>{wallet_number}</code>\n\n"
        "ለመቀጠል Confirm የሚለውን ይጫኑ፦",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("Confirm 💸", callback_data="confirm_withdraw_final")],
            [InlineKeyboardButton("Cancel ❌", callback_data="home")]
        ]),
        parse_mode="HTML"
    )

async def process_withdraw_final(query, context):
    user = query.from_user
    amount = context.user_data.get("pending_withdraw_amount")

    if not amount:
        await query.answer("No pending request!")
        return

    wallet = get_wallet(user.id)
    wallet_type, wallet_number = wallet

    conn = db()
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("UPDATE users SET balance = balance - ? WHERE user_id=?", (amount, user.id))
        cur = conn.execute("""
            INSERT INTO withdrawals(user_id, amount, wallet_type, wallet_number, status, created_at)
            VALUES(?,?,?,?,?,?)
        """, (user.id, amount, wallet_type, wallet_number, "pending", now()))
        wd_id = cur.lastrowid
        conn.commit()
    except Exception:
        conn.rollback()
        await query.edit_message_text("❌ Error processing request!")
        return
    finally:
        conn.close()

    context.user_data.pop("pending_withdraw_amount", None)

    await query.edit_message_text(
        "✅ <b>የማውጣት ጥያቄዎ ለአድሚን ተልኳል!</b>\n\nአድሚኑ መርምሮ ሲያፀድቀው ገንዘቡ ገቢ ይደረጋል።",
        reply_markup=main_keyboard(),
        parse_mode="HTML"
    )

    ref_list = get_user_referrals_list(user.id)
    ref_text_lines = []
    for idx, r in enumerate(ref_list[:15], 1):
        uname = f"@{r['username']}" if r['username'] else r['first_name']
        ref_text_lines.append(f"{idx}. {escape(uname)} (<code>{r['user_id']}</code>)")

    ref_details = "\n".join(ref_text_lines) if ref_text_lines else "No Referrals found."

    try:
        admin_text = (
            f"🚨 <b>NEW WITHDRAWAL REQUEST #{wd_id}</b>\n\n"
            f"👤 <b>User:</b> {user.first_name} (@{user.username or 'N/A'})\n"
            f"🆔 <b>User ID:</b> <code>{user.id}</code>\n"
            f"💰 <b>Amount Requested:</b> <b>{amount:.2f} ETB</b>\n"
            f"💳 <b>Method:</b> {wallet_type}\n"
            f"🔢 <b>Account Number:</b> <code>{wallet_number}</code>\n\n"
            f"👥 <b>Total Referrals Invited ({len(ref_list)}):</b>\n{ref_details}"
        )
        admin_btns = InlineKeyboardMarkup([
            [
                InlineKeyboardButton(f"Approve #{wd_id} ✅", callback_data=f"adm_app_wd_{wd_id}"),
                InlineKeyboardButton(f"Reject #{wd_id} ❌", callback_data=f"adm_rej_wd_{wd_id}")
            ],
            [InlineKeyboardButton("Check Full Referrals 🔎", callback_data=f"adm_check_ref_{user.id}")]
        ])
        await context.bot.send_message(chat_id=ADMIN_ID, text=admin_text, reply_markup=admin_btns, parse_mode="HTML")
    except Exception as e:
        logger.error(f"Could not notify admin: {e}")


# ============================================================
# FULL WORKING ADMIN PANEL
# ============================================================

async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("⛔ You are not an admin!")
        return
    context.user_data.clear()
    await update.message.reply_text(
        "🛠 <b>Welcome Admin Control Panel</b>\n\nከታች ካሉት አማራጮች ለመምረጥ ይጫኑ፦",
        reply_markup=admin_keyboard(),
        parse_mode="HTML"
    )

async def admin_callback_handler(query, context, data):
    if not is_admin(query.from_user.id):
        await query.answer("⛔ Admin Only!", show_alert=True)
        return

    # 1. STATISTICS BUTTON
    if data == "admin_stats":
        conn = db()
        u_count = conn.execute("SELECT COUNT(*) AS c FROM users").fetchone()["c"]
        w_pending = conn.execute("SELECT COUNT(*) AS c FROM withdrawals WHERE status='pending'").fetchone()["c"]
        p_pending = conn.execute("SELECT COUNT(*) AS c FROM task_proofs WHERE status='pending'").fetchone()["c"]
        conn.close()

        text = (
            f"📊 <b>System Statistics</b>\n\n"
            f"👥 Total Users: <b>{u_count}</b>\n"
            f"💸 Pending Withdrawals: <b>{w_pending}</b>\n"
            f"📥 Pending Task Proofs: <b>{p_pending}</b>"
        )
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Back 🔙", callback_data="admin_home")]]), parse_mode="HTML")

    elif data == "admin_home":
        await query.edit_message_text("🛠 <b>Admin Control Panel</b>", reply_markup=admin_keyboard(), parse_mode="HTML")

    # 2. WITHDRAWALS BUTTON
    elif data == "admin_withdrawals":
        conn = db()
        rows = conn.execute("SELECT * FROM withdrawals WHERE status='pending' ORDER BY id DESC").fetchall()
        conn.close()

        if not rows:
            await query.edit_message_text("💸 <b>No Pending Withdrawals!</b>", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Back 🔙", callback_data="admin_home")]]), parse_mode="HTML")
            return

        for r in rows:
            text = f"🆔 Request #{r['id']}\n👤 User ID: <code>{r['user_id']}</code>\n💰 Amount: {r['amount']:.2f} ETB\n💳 {r['wallet_type']}: <code>{r['wallet_number']}</code>"
            btns = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("Approve ✅", callback_data=f"adm_app_wd_{r['id']}"),
                    InlineKeyboardButton("Reject ❌", callback_data=f"adm_rej_wd_{r['id']}")
                ],
                [InlineKeyboardButton("Check Referrals 🔎", callback_data=f"adm_check_ref_{r['user_id']}")]
            ])
            await context.bot.send_message(chat_id=ADMIN_ID, text=text, reply_markup=btns, parse_mode="HTML")

    # 3. ADD TASK BUTTON
    elif data == "admin_add_task":
        context.user_data["admin_step"] = "task_title"
        await query.edit_message_text(
            "🎯 <b>Add New Task (Step 1/4)</b>\n\nየታስኩን ርዕስ (Title) ያስገቡ፦\nምሳሌ፦ <code>Join Channel & Earn</code>",
            parse_mode="HTML"
        )

    # 4. VERIFY TASK PROOFS BUTTON
    elif data == "admin_proofs":
        conn = db()
        proofs = conn.execute("SELECT * FROM task_proofs WHERE status='pending' ORDER BY id ASC LIMIT 5").fetchall()
        conn.close()

        if not proofs:
            await query.edit_message_text("📥 <b>No Pending Task Proofs!</b>", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Back 🔙", callback_data="admin_home")]]), parse_mode="HTML")
            return

        await query.edit_message_text(f"📥 Found {len(proofs)} pending proofs. Sending below...", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Back 🔙", callback_data="admin_home")]]))

        for p in proofs:
            conn = db()
            task = conn.execute("SELECT * FROM tasks WHERE id=?", (p["task_id"],)).fetchone()
            user = conn.execute("SELECT * FROM users WHERE user_id=?", (p["user_id"],)).fetchone()
            conn.close()

            uname = f"@{user['username']}" if user and user['username'] else "N/A"
            btns = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("Approve Task ✅", callback_data=f"approve_proof_{p['id']}"),
                    InlineKeyboardButton("Reject Task ❌", callback_data=f"reject_proof_{p['id']}")
                ]
            ])
            try:
                await context.bot.send_photo(
                    chat_id=ADMIN_ID,
                    photo=p["photo_id"],
                    caption=f"📥 Proof #{p['id']}\n👤 User: {uname} (<code>{p['user_id']}</code>)\n🎯 Task: {task['title'] if task else 'N/A'}",
                    reply_markup=btns,
                    parse_mode="HTML"
                )
            except Exception as e:
                logger.error(f"Error displaying proof #{p['id']}: {e}")

    # 5. TOP REFERRERS BUTTON
    elif data == "admin_referrals":
        conn = db()
        top_users = conn.execute("""
            SELECT referrer_id, COUNT(*) as ref_count 
            FROM referrals 
            WHERE status='paid' 
            GROUP BY referrer_id 
            ORDER BY ref_count DESC 
            LIMIT 10
        """).fetchall()
        conn.close()

        if not top_users:
            await query.edit_message_text("👥 <b>No Referrals Found Yet!</b>", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Back 🔙", callback_data="admin_home")]]), parse_mode="HTML")
            return

        lines = ["👥 <b>Top 10 Referrers:</b>\n"]
        for idx, u in enumerate(top_users, 1):
            user_info = get_user(u["referrer_id"])
            uname = f"@{user_info['username']}" if user_info and user_info['username'] else f"User {u['referrer_id']}"
            lines.append(f"{idx}. {escape(uname)} — <b>{u['ref_count']} Invites</b>")

        await query.edit_message_text("\n".join(lines), reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Back 🔙", callback_data="admin_home")]), parse_mode="HTML")

    # 6. SET REFERRAL REWARD BUTTON
    elif data == "admin_reward":
        context.user_data["admin_step"] = "set_reward"
        curr = get_referral_reward()
        await query.edit_message_text(
            f"💰 <b>Set Referral Reward</b>\n\nCurrent Reward Per Invite: <b>{curr:.2f} ETB</b>\n\nአዲስ የሪፈራል ክፍያ መጠን በቁጥር ያስገቡ (ምሳሌ፦ 3.00)፦",
            parse_mode="HTML"
        )

    # APPROVE/REJECT WITHDRAW ACTIONS
    elif data.startswith("adm_app_wd_"):
        wd_id = int(data.split("_")[-1])
        conn = db()
        conn.execute("UPDATE withdrawals SET status='approved' WHERE id=?", (wd_id,))
        row = conn.execute("SELECT * FROM withdrawals WHERE id=?", (wd_id,)).fetchone()
        conn.commit()
        conn.close()

        await query.answer("✅ Withdrawal Approved!")
        await query.edit_message_text(f"✅ <b>Request #{wd_id} Approved!</b>", parse_mode="HTML")

        try:
            await context.bot.send_message(chat_id=row["user_id"], text=f"🎉 <b>የወጣው ገንዘብ ገቢ ሆኗል!</b>\n\nAmount: <b>{row['amount']:.2f} ETB</b>", parse_mode="HTML")
        except Exception:
            pass

    elif data.startswith("adm_rej_wd_"):
        wd_id = int(data.split("_")[-1])
        conn = db()
        row = conn.execute("SELECT * FROM withdrawals WHERE id=?", (wd_id,)).fetchone()
        conn.execute("UPDATE withdrawals SET status='rejected' WHERE id=?", (wd_id,))
        conn.execute("UPDATE users SET balance = balance + ? WHERE user_id=?", (row["amount"], row["user_id"]))
        conn.commit()
        conn.close()

        await query.answer("❌ Rejected and Refunded!")
        await query.edit_message_text(f"❌ <b>Request #{wd_id} Rejected & Refunded!</b>", parse_mode="HTML")

        try:
            await context.bot.send_message(chat_id=row["user_id"], text=f"❌ <b>የገንዘብ ማውጣት ጥያቄዎ ውድቅ ሆኗል።</b>\n\nተመልሶ ወደ Balance ገብቷል።", parse_mode="HTML")
        except Exception:
            pass

    elif data.startswith("adm_check_ref_"):
        target_uid = int(data.split("_")[-1])
        refs = get_user_referrals_list(target_uid)
        lines = [f"👥 <b>Full Referrals for User ID:</b> <code>{target_uid}</code>\n"]
        for idx, r in enumerate(refs, 1):
            un = f"@{r['username']}" if r['username'] else r['first_name']
            lines.append(f"{idx}. {escape(un)} | ID: <code>{r['user_id']}</code>")

        txt = "\n".join(lines) if refs else "No referrals found!"
        await context.bot.send_message(chat_id=ADMIN_ID, text=txt, parse_mode="HTML")

    # APPROVE/REJECT PROOF ACTIONS
    elif data.startswith("approve_proof_"):
        proof_id = int(data.split("_")[-1])
        conn = db()
        proof = conn.execute("SELECT * FROM task_proofs WHERE id=?", (proof_id,)).fetchone()
        
        if proof and proof["status"] == "pending":
            conn.execute("UPDATE task_proofs SET status='approved' WHERE id=?", (proof_id,))
            task = conn.execute("SELECT * FROM tasks WHERE id=?", (proof["task_id"],)).fetchone()
            reward = float(task["reward"])
            conn.execute("UPDATE users SET balance = balance + ? WHERE user_id=?", (reward, proof["user_id"]))
            conn.commit()

            await query.edit_message_caption(caption="✅ <b>Proof Approved & Reward Added!</b>", parse_mode="HTML")
            try:
                await context.bot.send_message(chat_id=proof["user_id"], text=f"🎉 <b>Task Approved!</b>\n\n+<b>{reward:.2f} ETB</b> ወደ ሂሳብዎ ተጨምሯል!", parse_mode="HTML")
            except Exception:
                pass
        conn.close()

    elif data.startswith("reject_proof_"):
        proof_id = int(data.split("_")[-1])
        conn = db()
        proof = conn.execute("SELECT * FROM task_proofs WHERE id=?", (proof_id,)).fetchone()
        
        if proof and proof["status"] == "pending":
            conn.execute("UPDATE task_proofs SET status='rejected' WHERE id=?", (proof_id,))
            conn.commit()

            await query.edit_message_caption(caption="❌ <b>Proof Rejected!</b>", parse_mode="HTML")
            try:
                await context.bot.send_message(chat_id=proof["user_id"], text="❌ <b>የላኩት የታስክ ስክሪንሹት ውድቅ ሆኗል!</b>\n\nእባክዎን ቻናሉን በትክክል Join ማድረጋችሁን አረጋግጠው ድጋሚ ይላኩ።", parse_mode="HTML")
            except Exception:
                pass
        conn.close()


# ============================================================
# BROADCAST FUNCTION
# ============================================================

async def broadcast_new_task(context, task_title, task_url, reward):
    users = get_all_user_ids()
    count = 0
    msg = (
        f"🚨 <b>NEW TASK AVAILABLE! / አዲስ ስራ ወጥቷል!</b>\n\n"
        f"📌 <b>Title:</b> {escape(task_title)}\n"
        f"💰 <b>Reward:</b> {reward:.2f} ETB\n\n"
        "አሁኑኑ ስራውን ሰርተው ስክሪንሹት በመላክ ብር ያግኙ! 👇"
    )
    btns = InlineKeyboardMarkup([[InlineKeyboardButton("Start Task 🎯", callback_data="tasks")]])

    for uid in users:
        try:
            await context.bot.send_message(chat_id=uid, text=msg, reply_markup=btns, parse_mode="HTML")
            count += 1
        except Exception:
            pass
    logger.info(f"Broadcasted task to {count} users.")


# ============================================================
# MAIN CALLBACK & MESSAGE ROUTER
# ============================================================

async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    user = query.from_user

    if data.startswith("adm_") or data.startswith("admin_") or data.startswith("approve_proof_") or data.startswith("reject_proof_"):
        await admin_callback_handler(query, context, data)
        return

    if data == "home":
        await show_home(query, context)
        return

    if data == "verify_channels":
        await verify_channels_callback(query, context)
        return

    if data == "balance":
        b = get_balance(user.id)
        await query.edit_message_text(f"💰 <b>Current Balance:</b> <b>{b:.2f} ETB</b>", reply_markup=back_keyboard(), parse_mode="HTML")
        return

    if data == "referral":
        count = get_referral_count(user.id)
        reward = get_referral_reward()
        link = f"https://t.me/{BOT_USERNAME}?start={user.id}"
        txt = (
            f"👥 <b>Referral Program</b>\n\n"
            f"👤 Total Invited: <b>{count}</b>\n"
            f"🎁 Earn Per Refer: <b>{reward:.2f} ETB</b>\n\n"
            f"🔗 <b>Your Link:</b>\n<code>{link}</code>"
        )
        await query.edit_message_text(txt, reply_markup=back_keyboard(), parse_mode="HTML")
        return

    if data == "wallet":
        w = get_wallet(user.id)
        txt = f"👛 Wallet: <b>{w[0]} - {w[1]}</b>" if w else "👛 Wallet Not Connected!"
        await query.edit_message_text(txt, reply_markup=wallet_keyboard(), parse_mode="HTML")
        return

    if data == "wallet_cbe":
        context.user_data["wallet_type"] = "CBE"
        context.user_data["wallet_step"] = "number"
        await query.edit_message_text("🏦 የ 13 ዲጂት CBE ሂሳብ ቁጥርዎን ያስገቡ፦", reply_markup=back_keyboard())
        return

    if data == "wallet_telebirr":
        context.user_data["wallet_type"] = "Telebirr"
        context.user_data["wallet_step"] = "number"
        await query.edit_message_text("📱 የቴሌብር ስልክ ቁጥርዎን ያስገቡ (09.../07...)፦", reply_markup=back_keyboard())
        return

    if data == "tasks":
        await show_tasks(query, user.id)
        return

    if data.startswith("usertask_"):
        tid = int(data.split("_")[-1])
        await show_user_task_detail(query, user.id, tid, context)
        return

    if data == "withdraw":
        await show_withdraw_start(query, user.id, context)
        return

    if data == "confirm_withdraw_final":
        await process_withdraw_final(query, context)
        return

    if data == "support":
        txt = f"📞 Support ለማግኘትና USDT ለመግዛት/ለመሸጥ፦ {SUPPORT_USERNAME} DM ያድርጉ።"
        await query.edit_message_text(txt, reply_markup=back_keyboard())
        return


async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    text = update.message.text.strip() if update.message.text else ""

    # ADMIN INPUT STEPS
    if is_admin(user.id) and "admin_step" in context.user_data:
        step = context.user_data["admin_step"]

        if step == "set_reward":
            try:
                new_r = float(text)
                set_referral_reward(new_r)
                context.user_data.clear()
                await update.message.reply_text(f"✅ <b>Referral Reward Updated to {new_r:.2f} ETB!</b>", parse_mode="HTML")
            except ValueError:
                await update.message.reply_text("❌ እባክዎን ትክክለኛ ቁጥር ያስገቡ!")
            return

        if step == "task_title":
            context.user_data["new_task_title"] = text
            context.user_data["admin_step"] = "task_desc"
            await update.message.reply_text("🎯 <b>Step 2/4:</b> የታስኩን ማብራሪያ (Description) ያስገቡ፦", parse_mode="HTML")
            return

        if step == "task_desc":
            context.user_data["new_task_desc"] = text
            context.user_data["admin_step"] = "task_url"
            await update.message.reply_text("🎯 <b>Step 3/4:</b> የቻናሉን Link (URL) ያስገቡ (ምሳሌ፦ https://t.me/example)፦", parse_mode="HTML")
            return

        if step == "task_url":
            context.user_data["new_task_url"] = text
            context.user_data["admin_step"] = "task_reward"
            await update.message.reply_text("🎯 <b>Step 4/4:</b> ክፍያውን ያስገቡ (በ ETB)፦", parse_mode="HTML")
            return

        if step == "task_reward":
            try:
                reward = float(text)
            except ValueError:
                await update.message.reply_text("❌ እባክዎን ትክክለኛ ቁጥር ያስገቡ!")
                return

            title = context.user_data["new_task_title"]
            desc = context.user_data["new_task_desc"]
            url = context.user_data["new_task_url"]

            with get_db_cursor() as cur:
                cur.execute("""
                    INSERT INTO tasks (title, description, channel_url, reward, active, created_at)
                    VALUES (?, ?, ?, ?, 1, ?)
                """, (title, desc, url, reward, now()))

            context.user_data.clear()

            await update.message.reply_text("✅ <b>Task Created & Broadcasted to all members!</b>", parse_mode="HTML")
            await broadcast_new_task(context, title, url, reward)
            return

    # WALLET INPUT STEP
    if context.user_data.get("wallet_step") == "number":
        w_type = context.user_data.get("wallet_type")
        set_wallet(user.id, w_type, text)
        context.user_data.pop("wallet_step", None)
        await update.message.reply_text(f"✅ <b>{w_type} Wallet Saved!</b>", reply_markup=main_keyboard(), parse_mode="HTML")
        return

    # WITHDRAW INPUT STEP
    if context.user_data.get("withdraw_step") == "amount":
        await handle_withdraw_amount_text(update, context)
        return

    await update.message.reply_text("እባክዎን ከታች ያሉትን በተኖች ይጠቀሙ፦", reply_markup=main_keyboard())


# ============================================================
# MAIN APPLICATION STARTUP
# ============================================================

def main():
    if not BOT_TOKEN or not ADMIN_ID:
        raise RuntimeError("BOT_TOKEN or ADMIN_ID missing!")

    init_db()
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("admin", admin_command))
    app.add_handler(CallbackQueryHandler(callback_handler))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo_proof))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler))

    logger.info("Bot started successfully...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()

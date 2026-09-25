import logging
import sqlite3
import random
import re
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    ConversationHandler,
    filters,
)

# ================= Configuration =================
BOT_TOKEN = "YOUR_BOT_TOKEN_HERE"  # Replace with your Telegram Bot Token
ADMIN_IDS = [123456789]  # Replace with your Telegram Admin User ID(s)
MANDATORY_CHANNELS = ["@YourChannel1", "@YourChannel2"]  # Replace with your required channels

DB_NAME = "global_cash.db"

# Conversation States
CAPTCHA, WALLET_TYPE, WALLET_INPUT, TASK_PROOF, EDIT_SETTING_VAL = range(5)

# Logging Setup
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# ================= Database Initialization =================
def init_db():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()

    # Users Table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            full_name TEXT,
            balance REAL DEFAULT 0.0,
            referred_by INTEGER,
            cbe_account TEXT,
            telebirr_account TEXT,
            last_daily_bonus TIMESTAMP,
            is_banned INTEGER DEFAULT 0,
            suspicious INTEGER DEFAULT 0,
            joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Referrals Table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS referrals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            referrer_id INTEGER,
            referred_id INTEGER,
            reward_amount REAL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Withdrawals Table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS withdrawals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            amount REAL,
            method TEXT,
            account_number TEXT,
            status TEXT DEFAULT 'PENDING',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Tasks Table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT,
            link TEXT,
            reward REAL,
            is_active INTEGER DEFAULT 1
        )
    """)

    # Task Submissions Table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS task_submissions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id INTEGER,
            user_id INTEGER,
            proof_text TEXT,
            status TEXT DEFAULT 'PENDING',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Settings Table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    """)

    # Default Settings
    cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('min_ref_reward', '1.0')")
    cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('max_ref_reward', '3.0')")
    cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('min_withdraw', '30.0')")
    cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('daily_bonus', '0.50')")

    conn.commit()
    conn.close()

init_db()

# ================= Helper Functions =================
def get_setting(key, default=None):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT value FROM settings WHERE key = ?", (key,))
    row = cursor.fetchone()
    conn.close()
    return float(row[0]) if row else default

def set_setting(key, value):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, str(value)))
    conn.commit()
    conn.close()

def get_user(user_id):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
    user = cursor.fetchone()
    conn.close()
    return user

async def check_channel_membership(user_id, context):
    for ch in MANDATORY_CHANNELS:
        try:
            member = await context.bot.get_chat_member(chat_id=ch, user_id=user_id)
            if member.status in ["left", "kicked"]:
                return False
        except Exception:
            return False
    return True

# ================= Keyboards =================
def main_keyboard():
    keyboard = [
        ["💰 Balance", "🎁 Daily Bonus"],
        ["👥 Invite Friends", "📋 Tasks"],
        ["💳 Wallet Settings", "🔻 Withdraw"],
        ["📊 Statistics", "❓ Help"]
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

# ================= Handlers: Start & Captcha =================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    args = context.args

    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT is_banned FROM users WHERE user_id = ?", (user.id,))
    row = cursor.fetchone()

    if row and row[0] == 1:
        await update.message.reply_text("⛔ Your account has been suspended.")
        conn.close()
        return ConversationHandler.END

    referrer_id = None
    if args and args[0].isdigit():
        ref_candidate = int(args[0])
        if ref_candidate != user.id:
            referrer_id = ref_candidate

    if not row:
        cursor.execute(
            "INSERT INTO users (user_id, username, full_name, referred_by) VALUES (?, ?, ?, ?)",
            (user.id, user.username, user.full_name, referrer_id),
        )
        conn.commit()

    conn.close()

    # Generate Captcha Challenge
    num1, num2 = random.randint(1, 9), random.randint(1, 9)
    context.user_data["captcha_ans"] = num1 + num2

    await update.message.reply_text(
        f"🤖 **Anti-Bot Verification**\n\nPlease solve the math problem to continue:\n\n👉 **{num1} + {num2} = ?**",
        parse_mode="Markdown"
    )
    return CAPTCHA

async def verify_captcha(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_ans = update.message.text.strip()
    correct_ans = str(context.user_data.get("captcha_ans"))

    if user_ans == correct_ans:
        await check_channels_and_proceed(update, context)
        return ConversationHandler.END
    else:
        await update.message.reply_text("❌ Incorrect answer! Please start again by sending /start.")
        return ConversationHandler.END

async def check_channels_and_proceed(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    is_member = await check_channel_membership(user_id, context)

    if not is_member:
        buttons = []
        for ch in MANDATORY_CHANNELS:
            buttons.append([InlineKeyboardButton(f"Join {ch}", url=f"https://t.me/{ch.replace('@', '')}")])
        buttons.append([InlineKeyboardButton("✅ Verify Joining", callback_data="verify_membership")])

        reply_markup = InlineKeyboardMarkup(buttons)
        await update.message.reply_text(
            "⚠️ **Must Join Required Channels**\n\nTo use this bot and claim rewards, you must join all our required channels below:",
            reply_markup=reply_markup,
            parse_mode="Markdown"
        )
    else:
        await process_referral_reward(user_id, context)
        await update.message.reply_text(
            f"👋 Welcome {update.effective_user.first_name}!\n\nYour account is verified. Use the menu below to earn money!",
            reply_markup=main_keyboard()
        )

async def process_referral_reward(user_id, context):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()

    cursor.execute("SELECT referred_by FROM users WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()

    if row and row[0]:
        referrer_id = row[0]
        cursor.execute("SELECT id FROM referrals WHERE referred_id = ?", (user_id,))
        already_rewarded = cursor.fetchone()

        if not already_rewarded:
            min_r = get_setting("min_ref_reward", 1.0)
            max_r = get_setting("max_ref_reward", 3.0)
            reward = round(random.uniform(min_r, max_r), 2)

            cursor.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (reward, referrer_id))
            cursor.execute("INSERT INTO referrals (referrer_id, referred_id, reward_amount) VALUES (?, ?, ?)",
                           (referrer_id, user_id, reward))
            conn.commit()

            try:
                await context.bot.send_message(
                    chat_id=referrer_id,
                    text=f"🎉 **New Referral Bonus!**\n\nSomeone joined using your referral link! You earned **+{reward:.2f} ETB**.",
                    parse_mode="Markdown"
                )
            except Exception:
                pass

    conn.close()

async def callback_verify_membership(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id
    is_member = await check_channel_membership(user_id, context)

    if is_member:
        await process_referral_reward(user_id, context)
        await query.edit_message_text("✅ Verification successful! Welcome to Global Cash Bot.")
        await context.bot.send_message(chat_id=user_id, text="Main Menu:", reply_markup=main_keyboard())
    else:
        await query.answer("❌ You have not joined all required channels yet!", show_alert=True)

# ================= Menu Feature Handlers =================
async def handle_balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user = get_user(user_id)

    if not user:
        return

    text = (
        f"👤 **Account Overview**\n\n"
        f"🆔 User ID: `{user[0]}`\n"
        f"💵 Balance: **{user[3]:.2f} ETB**\n\n"
        f"💳 **Linked Wallets:**\n"
        f"• CBE Account: `{user[5] or 'Not Set'}`\n"
        f"• Telebirr: `{user[6] or 'Not Set'}`"
    )
    await update.message.reply_text(text, parse_mode="Markdown")

async def handle_daily_bonus(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()

    cursor.execute("SELECT last_daily_bonus, balance FROM users WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()

    bonus_amount = get_setting("daily_bonus", 0.50)
    now = datetime.now()

    if row and row[0]:
        last_claim = datetime.strptime(row[0], "%Y-%m-%d %H:%M:%S.%f") if "." in row[0] else datetime.strptime(row[0], "%Y-%m-%d %H:%M:%S")
        next_claim = last_claim + timedelta(hours=24)

        if now < next_claim:
            time_left = next_claim - now
            hours, remainder = divmod(int(time_left.total_seconds()), 3600)
            minutes, _ = divmod(remainder, 60)
            await update.message.reply_text(f"⏳ You already claimed your daily bonus.\n\nPlease wait **{hours}h {minutes}m** before claiming again.")
            conn.close()
            return

    cursor.execute("UPDATE users SET balance = balance + ?, last_daily_bonus = ? WHERE user_id = ?", (bonus_amount, now, user_id))
    conn.commit()
    conn.close()

    await update.message.reply_text(f"🎁 **Daily Bonus Claimed!**\n\nYou received **+{bonus_amount:.2f} ETB** into your balance.", parse_mode="Markdown")

async def handle_invite(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    bot_info = await context.bot.get_me()

    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM referrals WHERE referrer_id = ?", (user_id,))
    ref_count = cursor.fetchone()[0]
    conn.close()

    min_r = get_setting("min_ref_reward", 1.0)
    max_r = get_setting("max_ref_reward", 3.0)

    link = f"https://t.me/{bot_info.username}?start={user_id}"

    text = (
        f"👥 **Invite Friends & Earn**\n\n"
        f"Share your referral link with friends and earn between **{min_r:.2f} ETB** and **{max_r:.2f} ETB** per user!\n\n"
        f"🔗 Your Link:\n`{link}`\n\n"
        f"📊 Total Invited: **{ref_count} users**"
    )
    await update.message.reply_text(text, parse_mode="Markdown")

async def handle_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()

    cursor.execute("SELECT COUNT(*) FROM users")
    total_users = cursor.fetchone()[0]

    cursor.execute("SELECT SUM(amount) FROM withdrawals WHERE status = 'APPROVED'")
    total_paid = cursor.fetchone()[0] or 0.0

    conn.close()

    text = (
        f"📊 **Global Bot Statistics**\n\n"
        f"👥 Total Members: **{total_users}**\n"
        f"💸 Total Paid Out: **{total_paid:.2f} ETB**\n"
        f"⚡ Status: **Online & Active**"
    )
    await update.message.reply_text(text, parse_mode="Markdown")

async def handle_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        f"❓ **Help & Information**\n\n"
        f"• **Balance**: Check your current earnings and wallets.\n"
        f"• **Daily Bonus**: Claim 0.50 ETB every 24 hours.\n"
        f"• **Invite Friends**: Earn money for every active user you invite.\n"
        f"• **Tasks**: Complete social tasks and submit proof to earn extra income.\n"
        f"• **Withdraw**: Cash out your earnings directly to CBE or Telebirr."
    )
    await update.message.reply_text(text, parse_mode="Markdown")

# ================= Wallet Management =================
async def start_wallet_setup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    buttons = [
        [InlineKeyboardButton("🏦 CBE Account", callback_data="set_wallet_cbe")],
        [InlineKeyboardButton("📱 Telebirr Account", callback_data="set_wallet_telebirr")]
    ]
    await update.message.reply_text("Select the payment method you want to configure:", reply_markup=InlineKeyboardMarkup(buttons))

async def handle_wallet_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    choice = query.data
    context.user_data["wallet_choice"] = choice

    if choice == "set_wallet_cbe":
        await query.edit_message_text("Send your **13-digit Commercial Bank of Ethiopia (CBE)** account number (starts with 1000):")
    else:
        await query.edit_message_text("Send your **10-digit Telebirr** phone number (e.g., 0912345678 or 0712345678):")

    return WALLET_INPUT

async def save_wallet_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text.strip()
    choice = context.user_data.get("wallet_choice")

    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()

    if choice == "set_wallet_cbe":
        if not (re.match(r"^1000\d{9}$", text)):
            await update.message.reply_text("❌ Invalid CBE Account number. Must be 13 digits starting with 1000. Try again:")
            conn.close()
            return WALLET_INPUT

        # Duplicate Wallet Multi-Account Audit
        cursor.execute("SELECT user_id FROM users WHERE cbe_account = ? AND user_id != ?", (text, user_id))
        dup = cursor.fetchall()
        if dup:
            cursor.execute("UPDATE users SET suspicious = 1 WHERE user_id = ?", (user_id,))
            for d in dup:
                cursor.execute("UPDATE users SET suspicious = 1 WHERE user_id = ?", (d[0],))
            conn.commit()

            for admin_id in ADMIN_IDS:
                try:
                    await context.bot.send_message(
                        admin_id,
                        f"🚨 **DUPLICATE WALLET ALERT!**\n\nCBE Account `{text}` was saved by User ID `{user_id}`. Matches existing User IDs: {[d[0] for d in dup]}.",
                        parse_mode="Markdown"
                    )
                except Exception:
                    pass

        cursor.execute("UPDATE users SET cbe_account = ? WHERE user_id = ?", (text, user_id))
        conn.commit()
        await update.message.reply_text("✅ CBE Account saved successfully!", reply_markup=main_keyboard())

    elif choice == "set_wallet_telebirr":
        if not (re.match(r"^(09|07)\d{8}$", text)):
            await update.message.reply_text("❌ Invalid Telebirr number. Must start with 09 or 07 and be 10 digits. Try again:")
            conn.close()
            return WALLET_INPUT

        cursor.execute("SELECT user_id FROM users WHERE telebirr_account = ? AND user_id != ?", (text, user_id))
        dup = cursor.fetchall()
        if dup:
            cursor.execute("UPDATE users SET suspicious = 1 WHERE user_id = ?", (user_id,))
            for d in dup:
                cursor.execute("UPDATE users SET suspicious = 1 WHERE user_id = ?", (d[0],))
            conn.commit()

            for admin_id in ADMIN_IDS:
                try:
                    await context.bot.send_message(
                        admin_id,
                        f"🚨 **DUPLICATE WALLET ALERT!**\n\nTelebirr Number `{text}` saved by User ID `{user_id}`. Matches existing User IDs: {[d[0] for d in dup]}.",
                        parse_mode="Markdown"
                    )
                except Exception:
                    pass

        cursor.execute("UPDATE users SET telebirr_account = ? WHERE user_id = ?", (text, user_id))
        conn.commit()
        await update.message.reply_text("✅ Telebirr account saved successfully!", reply_markup=main_keyboard())

    conn.close()
    return ConversationHandler.END

# ================= Withdrawal Logic =================
async def handle_withdraw_request(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user = get_user(user_id)

    min_withdraw = get_setting("min_withdraw", 30.0)

    if user[3] < min_withdraw:
        await update.message.reply_text(f"❌ Minimum withdrawal amount is **{min_withdraw:.2f} ETB**.\nYour current balance is **{user[3]:.2f} ETB**.", parse_mode="Markdown")
        return

    if not user[5] and not user[6]:
        await update.message.reply_text("⚠️ You have not set up any withdrawal wallet! Go to **💳 Wallet Settings** first.")
        return

    buttons = []
    if user[5]:
        buttons.append([InlineKeyboardButton(f"🏦 CBE ({user[5]})", callback_data="withdraw_cbe")])
    if user[6]:
        buttons.append([InlineKeyboardButton(f"📱 Telebirr ({user[6]})", callback_data="withdraw_telebirr")])

    await update.message.reply_text("Select payment method for withdrawal:", reply_markup=InlineKeyboardMarkup(buttons))

async def process_withdrawal_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id
    method_choice = query.data

    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT balance, cbe_account, telebirr_account, username, full_name, suspicious FROM users WHERE user_id = ?", (user_id,))
    u = cursor.fetchone()

    balance, cbe, tele, username, full_name, suspicious = u[0], u[1], u[2], u[3], u[4], u[5]
    min_w = get_setting("min_withdraw", 30.0)

    if balance < min_w:
        await query.edit_message_text("❌ Insufficient balance.")
        conn.close()
        return

    method = "CBE" if method_choice == "withdraw_cbe" else "Telebirr"
    acc = cbe if method == "CBE" else tele

    cursor.execute("UPDATE users SET balance = balance - ? WHERE user_id = ?", (balance, user_id))
    cursor.execute("INSERT INTO withdrawals (user_id, amount, method, account_number) VALUES (?, ?, ?, ?)",
                   (user_id, balance, method, acc))
    withdraw_id = cursor.lastrowid
    conn.commit()

    # Get Referrals List for Admin Fraud Check
    cursor.execute("""
        SELECT u.user_id, u.username, u.full_name 
        FROM referrals r 
        JOIN users u ON r.referred_id = u.user_id 
        WHERE r.referrer_id = ?
    """, (user_id,))
    invited_users = cursor.fetchall()
    conn.close()

    ref_details = ""
    for idx, inv in enumerate(invited_users, start=1):
        uname = f"@{inv[1]}" if inv[1] else "No Username"
        ref_details += f"{idx}. {inv[2]} ({uname}) - ID: `{inv[0]}`\n"

    if not ref_details:
        ref_details = "None (No referrals invited)\n"

    susp_warning = "⚠️ **FLAGGED AS SUSPICIOUS (MULTI-ACCOUNT RISK)**\n" if suspicious == 1 else ""

    admin_msg = (
        f"🚨 **NEW WITHDRAWAL REQUEST #{withdraw_id}**\n{susp_warning}\n"
        f"👤 **User Info:**\n"
        f"• Name: {full_name}\n"
        f"• Username: @{username if username else 'N/A'}\n"
        f"• User ID: `{user_id}`\n\n"
        f"💰 **Payout Details:**\n"
        f"• Amount: **{balance:.2f} ETB**\n"
        f"• Method: **{method}**\n"
        f"• Account: `{acc}`\n\n"
        f"👥 **Referral Breakdown (Total Invited: {len(invited_users)}):**\n"
        f"{ref_details}"
    )

    btn = [
        [InlineKeyboardButton("✅ Approve Payout", callback_data=f"adm_appr_{withdraw_id}")],
        [InlineKeyboardButton("❌ Reject & Refund", callback_data=f"adm_rej_{withdraw_id}")],
        [InlineKeyboardButton("⛔ Ban User", callback_data=f"adm_ban_{user_id}")]
    ]

    for admin_id in ADMIN_IDS:
        try:
            await context.bot.send_message(admin_id, admin_msg, reply_markup=InlineKeyboardMarkup(btn), parse_mode="Markdown")
        except Exception:
            pass

    await query.edit_message_text("✅ Your withdrawal request has been submitted to Admin for verification. You will be notified once processed!")

# ================= Task Management =================
async def handle_tasks_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()

    cursor.execute("""
        SELECT t.id, t.title, t.link, t.reward 
        FROM tasks t 
        LEFT JOIN task_submissions ts ON t.id = ts.task_id AND ts.user_id = ?
        WHERE t.is_active = 1 AND ts.id IS NULL
    """, (user_id,))
    available_tasks = cursor.fetchall()
    conn.close()

    if not available_tasks:
        await update.message.reply_text("📋 No tasks available at the moment. Check back later!")
        return

    buttons = []
    for t in available_tasks:
        buttons.append([InlineKeyboardButton(f"👉 {t[1]} (+{t[3]:.2f} ETB)", callback_data=f"dotask_{t[0]}")])

    await update.message.reply_text("📋 **Available Tasks:**\nChoose a task below to complete:", reply_markup=InlineKeyboardMarkup(buttons), parse_mode="Markdown")

async def process_task_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    task_id = int(query.data.split("_")[1])
    context.user_data["current_task_id"] = task_id

    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT title, link, reward FROM tasks WHERE id = ?", (task_id,))
    task = cursor.fetchone()
    conn.close()

    if not task:
        await query.edit_message_text("Task no longer exists.")
        return

    text = (
        f"📌 **Task:** {task[0]}\n"
        f"💰 **Reward:** {task[2]:.2f} ETB\n\n"
        f"🔗 **Link:** {task[1]}\n\n"
        f"👉 Instructions: Join/Complete the task link above and reply with your proof (Username, Screenshot, or Text Proof)."
    )
    await query.edit_message_text(text, parse_mode="Markdown")
    return TASK_PROOF

async def handle_task_proof_submission(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    task_id = context.user_data.get("current_task_id")
    proof_text = update.message.text or "Photo/File Proof Attached"

    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("INSERT INTO task_submissions (task_id, user_id, proof_text) VALUES (?, ?, ?)", (task_id, user_id, proof_text))
    sub_id = cursor.lastrowid
    
    cursor.execute("SELECT title, reward FROM tasks WHERE id = ?", (task_id,))
    task = cursor.fetchone()
    conn.close()

    admin_msg = (
        f"📥 **NEW TASK PROOF SUBMISSION #{sub_id}**\n\n"
        f"👤 User ID: `{user_id}`\n"
        f"📌 Task: {task[0]}\n"
        f"💰 Reward: {task[1]:.2f} ETB\n"
        f"📝 Proof Submitted:\n{proof_text}"
    )
    btn = [
        [InlineKeyboardButton("✅ Approve Task", callback_data=f"tappr_{sub_id}")],
        [InlineKeyboardButton("❌ Reject Task", callback_data=f"trej_{sub_id}")]
    ]

    for admin_id in ADMIN_IDS:
        try:
            await context.bot.send_message(admin_id, admin_msg, reply_markup=InlineKeyboardMarkup(btn), parse_mode="Markdown")
        except Exception:
            pass

    await update.message.reply_text("✅ Proof submitted successfully! Admin will review it shortly.", reply_markup=main_keyboard())
    return ConversationHandler.END

# ================= Admin Panel & Actions =================
async def admin_dashboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        return

    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()

    cursor.execute("SELECT COUNT(*) FROM users")
    total_users = cursor.fetchone()[0]

    cursor.execute("SELECT SUM(balance) FROM users")
    total_balance = cursor.fetchone()[0] or 0.0

    cursor.execute("SELECT COUNT(*) FROM withdrawals WHERE status = 'PENDING'")
    pending_withdraws = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM users WHERE suspicious = 1")
    suspicious_users = cursor.fetchone()[0]

    conn.close()

    msg = (
        f"⚙️ **ADMIN DASHBOARD**\n\n"
        f"👥 Total Users: **{total_users}**\n"
        f"💰 User Total Balance: **{total_balance:.2f} ETB**\n"
        f"⏳ Pending Withdrawals: **{pending_withdraws}**\n"
        f"🚨 Suspicious Accounts: **{suspicious_users}**\n\n"
        f"**Admin Commands:**\n"
        f"• `/addtask Title | Link | Reward` - Add new task\n"
        f"• `/checkuser USER_ID` - View user breakdown\n"
        f"• `/ban USER_ID` - Ban user\n"
        f"• `/unban USER_ID` - Unban user"
    )
    await update.message.reply_text(msg, parse_mode="Markdown")

async def admin_check_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        return

    try:
        target_id = int(context.args[0])
        user = get_user(target_id)

        if not user:
            await update.message.reply_text("❌ User not found.")
            return

        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM referrals WHERE referrer_id = ?", (target_id,))
        ref_count = cursor.fetchone()[0]
        conn.close()

        msg = (
            f"👤 **User Audit #{user[0]}**\n\n"
            f"• Full Name: {user[2]}\n"
            f"• Username: @{user[1] if user[1] else 'N/A'}\n"
            f"• Balance: **{user[3]:.2f} ETB**\n"
            f"• Referrals Count: **{ref_count}**\n"
            f"• CBE Account: `{user[5] or 'Not Set'}`\n"
            f"• Telebirr: `{user[6] or 'Not Set'}`\n"
            f"• Suspicious Flag: `{user[9]}`\n"
            f"• Banned Status: `{user[8]}`"
        )
        await update.message.reply_text(msg, parse_mode="Markdown")
    except Exception:
        await update.message.reply_text("❌ Usage: `/checkuser USER_ID`", parse_mode="Markdown")

async def handle_admin_callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    data = query.data
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()

    if data.startswith("adm_appr_"):
        wid = int(data.split("_")[2])
        cursor.execute("SELECT user_id, amount FROM withdrawals WHERE id = ? AND status = 'PENDING'", (wid,))
        row = cursor.fetchone()
        if row:
            cursor.execute("UPDATE withdrawals SET status = 'APPROVED' WHERE id = ?", (wid,))
            conn.commit()
            await query.edit_message_text(query.message.text + "\n\n✅ **STATUS: APPROVED**")
            try:
                await context.bot.send_message(row[0], f"🎉 Your withdrawal request of **{row[1]:.2f} ETB** was APPROVED and processed!")
            except Exception:
                pass

    elif data.startswith("adm_rej_"):
        wid = int(data.split("_")[2])
        cursor.execute("SELECT user_id, amount FROM withdrawals WHERE id = ? AND status = 'PENDING'", (wid,))
        row = cursor.fetchone()
        if row:
            cursor.execute("UPDATE withdrawals SET status = 'REJECTED' WHERE id = ?", (wid,))
            cursor.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (row[1], row[0]))
            conn.commit()
            await query.edit_message_text(query.message.text + "\n\n❌ **STATUS: REJECTED & REFUNDED**")
            try:
                await context.bot.send_message(row[0], f"❌ Your withdrawal request of **{row[1]:.2f} ETB** was rejected. The funds have been returned to your balance.")
            except Exception:
                pass

    elif data.startswith("adm_ban_"):
        uid = int(data.split("_")[2])
        cursor.execute("UPDATE users SET is_banned = 1 WHERE user_id = ?", (uid,))
        conn.commit()
        await query.edit_message_text(query.message.text + f"\n\n⛔ **USER `{uid}` BANNED!**")

    elif data.startswith("tappr_"):
        sub_id = int(data.split("_")[1])
        cursor.execute("""
            SELECT ts.user_id, t.reward, ts.status 
            FROM task_submissions ts 
            JOIN tasks t ON ts.task_id = t.id 
            WHERE ts.id = ?
        """, (sub_id,))
        row = cursor.fetchone()
        if row and row[2] == 'PENDING':
            cursor.execute("UPDATE task_submissions SET status = 'APPROVED' WHERE id = ?", (sub_id,))
            cursor.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (row[1], row[0]))
            conn.commit()
            await query.edit_message_text(query.message.text + "\n\n✅ **TASK APPROVED & REWARD GRANTED**")
            try:
                await context.bot.send_message(row[0], f"🎉 Your task submission was approved! **+{row[1]:.2f} ETB** added to your balance.")
            except Exception:
                pass

    elif data.startswith("trej_"):
        sub_id = int(data.split("_")[1])
        cursor.execute("UPDATE task_submissions SET status = 'REJECTED' WHERE id = ?", (sub_id,))
        conn.commit()
        await query.edit_message_text(query.message.text + "\n\n❌ **TASK PROOF REJECTED**")

    conn.close()

async def admin_add_task(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        return

    try:
        raw_text = update.message.text.split(" ", 1)[1]
        title, link, reward = [x.strip() for x in raw_text.split("|")]
        reward = float(reward)

        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute("INSERT INTO tasks (title, link, reward) VALUES (?, ?, ?)", (title, link, reward))
        conn.commit()
        conn.close()

        await update.message.reply_text(f"✅ **Task Created Successfully!**\n\n📌 Title: {title}\n🔗 Link: {link}\n💰 Reward: {reward:.2f} ETB")
    except Exception:
        await update.message.reply_text("❌ Format error! Use:\n`/addtask Join Channel | https://t.me/example | 2.0`", parse_mode="Markdown")

# ================= Main App Runner =================
def main():
    app = Application.builder().token(BOT_TOKEN).build()

    # Captcha Handler
    captcha_conv = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            CAPTCHA: [MessageHandler(filters.TEXT & ~filters.COMMAND, verify_captcha)]
        },
        fallbacks=[]
    )

    # Wallet Setup Handler
    wallet_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^💳 Wallet Settings$"), start_wallet_setup)],
        states={
            WALLET_INPUT: [MessageHandler(filters.TEXT & ~filters.COMMAND, save_wallet_input)]
        },
        fallbacks=[]
    )

    # Task Submission Handler
    task_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(process_task_click, pattern="^dotask_")],
        states={
            TASK_PROOF: [MessageHandler(filters.TEXT | filters.PHOTO, handle_task_proof_submission)]
        },
        fallbacks=[]
    )

    app.add_handler(captcha_conv)
    app.add_handler(wallet_conv)
    app.add_handler(task_conv)

    # Core Buttons
    app.add_handler(MessageHandler(filters.Regex("^💰 Balance$"), handle_balance))
    app.add_handler(MessageHandler(filters.Regex("^🎁 Daily Bonus$"), handle_daily_bonus))
    app.add_handler(MessageHandler(filters.Regex("^👥 Invite Friends$"), handle_invite))
    app.add_handler(MessageHandler(filters.Regex("^🔻 Withdraw$"), handle_withdraw_request))
    app.add_handler(MessageHandler(filters.Regex("^📋 Tasks$"), handle_tasks_list))
    app.add_handler(MessageHandler(filters.Regex("^📊 Statistics$"), handle_stats))
    app.add_handler(MessageHandler(filters.Regex("^❓ Help$"), handle_help))

    # Callbacks
    app.add_handler(CallbackQueryHandler(callback_verify_membership, pattern="^verify_membership$"))
    app.add_handler(CallbackQueryHandler(handle_wallet_choice, pattern="^set_wallet_"))
    app.add_handler(CallbackQueryHandler(process_withdrawal_selection, pattern="^withdraw_"))
    app.add_handler(CallbackQueryHandler(handle_admin_callbacks, pattern="^(adm_|tappr_|trej_)"))

    # Admin Commands
    app.add_handler(CommandHandler("admin", admin_dashboard))
    app.add_handler(CommandHandler("checkuser", admin_check_user))
    app.add_handler(CommandHandler("addtask", admin_add_task))

    print("Bot is running...")
    app.run_polling()

if __name__ == "__main__":
    main()

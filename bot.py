import os
import sqlite3
import logging
from datetime import datetime, timezone
from html import escape

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ChatMemberStatus
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, ContextTypes, filters

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
BOT_USERNAME = "GloballCashh_Bot"
SUPPORT = "https://t.me/AmanM_12"
MIN_WITHDRAWAL = 30.0
DB_FILE = "global_cash.db"

CHANNELS = [
    ("@Sheger_tech1", "https://t.me/Sheger_tech1"),
    ("@EthioVortex1", "https://t.me/EthioVortex1"),
    ("@ethiocashflow", "https://t.me/ethiocashflow"),
    ("@AmanIncomeLab", "https://t.me/AmanIncomeLab"),
    ("@OnlineIncomeHub07", "https://t.me/OnlineIncomeHub07"),
    ("@Paymentprooff2", "https://t.me/Paymentprooff2"),
]

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger("global_cash")


def db():
    c = sqlite3.connect(DB_FILE, timeout=30)
    c.row_factory = sqlite3.Row
    return c


def now():
    return datetime.now(timezone.utc).isoformat()


def init_db():
    c = db()
    c.executescript("""
    CREATE TABLE IF NOT EXISTS users(
        user_id INTEGER PRIMARY KEY,
        username TEXT, first_name TEXT,
        balance REAL NOT NULL DEFAULT 0,
        referred_by INTEGER,
        referral_paid INTEGER NOT NULL DEFAULT 0,
        joined_all INTEGER NOT NULL DEFAULT 0,
        suspicious INTEGER NOT NULL DEFAULT 0,
        wallet_type TEXT, wallet_number TEXT,
        created_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS referrals(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        referrer_id INTEGER NOT NULL,
        referred_id INTEGER NOT NULL UNIQUE,
        reward_amount REAL NOT NULL,
        status TEXT NOT NULL DEFAULT 'paid',
        created_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS withdrawals(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        amount REAL NOT NULL,
        wallet_type TEXT NOT NULL,
        wallet_number TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending',
        created_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS settings(
        key TEXT PRIMARY KEY, value TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS tasks(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT NOT NULL, description TEXT NOT NULL,
        channel_username TEXT NOT NULL, channel_url TEXT NOT NULL,
        reward REAL NOT NULL, active INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS user_tasks(
        user_id INTEGER NOT NULL, task_id INTEGER NOT NULL,
        verified INTEGER NOT NULL DEFAULT 0,
        paid INTEGER NOT NULL DEFAULT 0, paid_at TEXT,
        PRIMARY KEY(user_id,task_id)
    );
    CREATE UNIQUE INDEX IF NOT EXISTS wallet_one_account
    ON users(wallet_type,wallet_number)
    WHERE wallet_type IS NOT NULL AND wallet_number IS NOT NULL AND wallet_number!='';
    INSERT OR IGNORE INTO settings(key,value) VALUES('referral_reward','2.00');
    """)
    c.commit()
    c.close()


def user(uid):
    c = db()
    r = c.execute("SELECT * FROM users WHERE user_id=?", (uid,)).fetchone()
    c.close()
    return r


def ensure_user(tg, ref=None):
    r = user(tg.id)
    c = db()
    if not r:
        if ref == tg.id:
            ref = None
        c.execute("""INSERT INTO users(user_id,username,first_name,referred_by,created_at)
                     VALUES(?,?,?,?,?)""",
                  (tg.id, tg.username, tg.first_name or "", ref, now()))
    else:
        c.execute("UPDATE users SET username=?,first_name=? WHERE user_id=?",
                  (tg.username, tg.first_name or "", tg.id))
    c.commit()
    c.close()


def setting(k, default=None):
    c = db()
    r = c.execute("SELECT value FROM settings WHERE key=?", (k,)).fetchone()
    c.close()
    return r["value"] if r else default


def set_setting(k, v):
    c = db()
    c.execute("""INSERT INTO settings(key,value) VALUES(?,?)
                 ON CONFLICT(key) DO UPDATE SET value=excluded.value""", (k, str(v)))
    c.commit()
    c.close()


def reward():
    try:
        return float(setting("referral_reward", "2.00"))
    except Exception:
        return 2.0


def wallet(uid):
    r = user(uid)
    if r and r["wallet_type"] and r["wallet_number"]:
        return r["wallet_type"], r["wallet_number"]
    return None


def wallet_used_by_other(kind, number, uid):
    c = db()
    r = c.execute("""SELECT user_id FROM users
                     WHERE wallet_type=? AND wallet_number=? AND user_id!=?""",
                  (kind, number, uid)).fetchone()
    c.close()
    return r is not None


async def missing(bot, uid):
    out = []
    for name, url in CHANNELS:
        try:
            m = await bot.get_chat_member(name, uid)
            if m.status not in (ChatMemberStatus.MEMBER, ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER):
                out.append((name, url))
        except Exception:
            out.append((name, url))
    return out


def channels_kb(items):
    b = [[InlineKeyboardButton("📢 " + n, url=u)] for n, u in items]
    b.append([InlineKeyboardButton("✅ Verify", callback_data="verify")])
    return InlineKeyboardMarkup(b)


def main_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("💰 Balance", callback_data="balance"),
         InlineKeyboardButton("👥 Referral", callback_data="referral")],
        [InlineKeyboardButton("🎯 Tasks", callback_data="tasks"),
         InlineKeyboardButton("💸 Withdraw", callback_data="withdraw")],
        [InlineKeyboardButton("👛 Wallet", callback_data="wallet"),
         InlineKeyboardButton("📞 Support", callback_data="support")]
    ])


def back_kb(target="home"):
    return InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data=target)]])


def admin_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📊 Statistics", callback_data="a_stats"),
         InlineKeyboardButton("💰 Reward", callback_data="a_reward")],
        [InlineKeyboardButton("👥 Referrals", callback_data="a_refs"),
         InlineKeyboardButton("💸 Withdrawals", callback_data="a_wd")],
        [InlineKeyboardButton("⚠️ Suspicious", callback_data="a_sus"),
         InlineKeyboardButton("🎯 Add Task", callback_data="a_task")]
    ])


def is_admin(uid):
    return uid == ADMIN_ID


def referral_count(uid):
    c = db()
    n = c.execute("SELECT COUNT(*) n FROM referrals WHERE referrer_id=? AND status='paid'", (uid,)).fetchone()["n"]
    c.close()
    return n


def process_referral(uid):
    r = user(uid)
    if not r or not r["referred_by"] or r["referral_paid"] or r["suspicious"]:
        return None
    inv = user(r["referred_by"])
    if not inv or inv["suspicious"] or inv["user_id"] == uid:
        return None
    c = db()
    try:
        if c.execute("SELECT id FROM referrals WHERE referred_id=?", (uid,)).fetchone():
            c.execute("UPDATE users SET referral_paid=1 WHERE user_id=?", (uid,))
            c.commit()
            return None
        amt = reward()
        c.execute("UPDATE users SET balance=balance+? WHERE user_id=?", (amt, inv["user_id"]))
        c.execute("""INSERT INTO referrals(referrer_id,referred_id,reward_amount,status,created_at)
                     VALUES(?,?,?,?,?)""", (inv["user_id"], uid, amt, "paid", now()))
        c.execute("UPDATE users SET referral_paid=1 WHERE user_id=?", (uid,))
        c.commit()
        return inv["user_id"], amt
    except Exception:
        c.rollback()
        log.exception("referral")
        return None
    finally:
        c.close()


async def notify_referrer(ctx, result, uid):
    if not result:
        return
    inv, amt = result
    try:
        await ctx.bot.send_message(inv,
            "🎉 <b>New Successful Referral!</b>\n\n"
            f"👤 User ID: <code>{uid}</code>\n"
            f"🎁 Reward: <b>{amt:.2f} ETB</b>",
            parse_mode="HTML")
    except Exception:
        log.exception("notify referrer")


async def start(update, ctx):
    u = update.effective_user
    ref = None
    if ctx.args:
        try:
            ref = int(ctx.args[0])
        except ValueError:
            pass
    ensure_user(u, ref)
    miss = await missing(ctx.bot, u.id)
    if miss:
        c = db(); c.execute("UPDATE users SET joined_all=0 WHERE user_id=?", (u.id,)); c.commit(); c.close()
        await update.message.reply_text(
            "👋 <b>Welcome to Global Cash Bot!</b>\n\n"
            "Join all required channels, then press Verify.\n"
            f"📌 Remaining: <b>{len(miss)}</b>",
            reply_markup=channels_kb(miss), parse_mode="HTML")
        return
    c = db(); c.execute("UPDATE users SET joined_all=1 WHERE user_id=?", (u.id,)); c.commit(); c.close()
    await notify_referrer(ctx, process_referral(u.id), u.id)
    await update.message.reply_text(
        "🎉 <b>Welcome to Global Cash Bot!</b>\n\n"
        "✅ Verification complete.\n\nChoose an option:",
        reply_markup=main_kb(), parse_mode="HTML")


async def verify(query, ctx):
    uid = query.from_user.id
    miss = await missing(ctx.bot, uid)
    if miss:
        await query.edit_message_text(
            "❌ <b>Verification Failed</b>\n\n"
            f"📌 Remaining: <b>{len(miss)}</b>\n\nJoin them and press Verify again.",
            reply_markup=channels_kb(miss), parse_mode="HTML")
        return
    c = db(); c.execute("UPDATE users SET joined_all=1 WHERE user_id=?", (uid,)); c.commit(); c.close()
    await notify_referrer(ctx, process_referral(uid), uid)
    await query.edit_message_text(
        "✅ <b>Verified Successfully!</b>\n\nAll required channels are verified.",
        reply_markup=main_kb(), parse_mode="HTML")


async def page_balance(q, uid):
    r = user(uid)
    await q.edit_message_text(
        "💰 <b>Your Balance</b>\n\n"
        f"💵 Balance: <b>{r['balance']:.2f} ETB</b>\n"
        f"💸 Minimum Withdrawal: <b>{MIN_WITHDRAWAL:.2f} ETB</b>",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("💸 Withdraw", callback_data="withdraw"),
             InlineKeyboardButton("👥 Referral", callback_data="referral")],
            [InlineKeyboardButton("🔙 Back", callback_data="home")]
        ]), parse_mode="HTML")


async def page_referral(q, uid):
    link = f"https://t.me/{BOT_USERNAME}?start={uid}"
    await q.edit_message_text(
        "👥 <b>Referral Program</b>\n\n"
        f"👤 Successful Referrals: <b>{referral_count(uid)}</b>\n"
        f"🎁 Current Reward: <b>{reward():.2f} ETB</b>\n\n"
        "Invite friends. A referral is paid after the referred user "
        "joins all required channels and completes verification.\n\n"
        f"🔗 <code>{escape(link)}</code>",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("📤 Share Link", callback_data="share")],
            [InlineKeyboardButton("🔙 Back", callback_data="home")]
        ]), parse_mode="HTML")


async def page_wallet(q, uid):
    w = wallet(uid)
    text = "👛 <b>Wallet</b>\n\n"
    if w:
        text += f"💳 Method: <b>{escape(w[0])}</b>\n🔢 <code>{escape(w[1])}</code>\n\n"
    text += "Choose CBE or Telebirr. You can switch anytime."
    await q.edit_message_text(text, reply_markup=InlineKeyboardMarkup([
        [InlineKeyboardButton("🏦 CBE", callback_data="cbe"),
         InlineKeyboardButton("📱 Telebirr", callback_data="telebirr")],
        [InlineKeyboardButton("🔙 Back", callback_data="home")]
    ]), parse_mode="HTML")


async def page_support(q):
    await q.edit_message_text(
        "📞 <b>Support & Services</b>\n\n"
        "📢 Channel Promotion\n"
        "🚀 Channel Growth\n"
        "👥 Group Advertising\n"
        "🏢 Business Promotion\n"
        "💱 USDT Buy & Sell\n"
        "📱 App & Website Promotion\n\n"
        "For price and more information, contact Support.",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("💬 Contact Support", url=SUPPORT)],
            [InlineKeyboardButton("🔙 Back", callback_data="home")]
        ]), parse_mode="HTML")


async def page_tasks(q, uid):
    c = db()
    rows = c.execute("SELECT * FROM tasks WHERE active=1 ORDER BY id DESC").fetchall()
    c.close()
    lines = ["🎯 <b>Tasks</b>", "", f"👥 Referral: <b>{reward():.2f} ETB</b> per successful referral.", ""]
    b = []
    for t in rows:
        lines.append(f"🎯 <b>{escape(t['title'])}</b>\n{escape(t['description'])}\n💰 <b>{t['reward']:.2f} ETB</b>")
        b.append([InlineKeyboardButton("🎯 " + t["title"], callback_data=f"task:{t['id']}")])
    if not rows:
        lines.append("📌 More earning tasks will be added soon.")
    b.append([InlineKeyboardButton("🔙 Back", callback_data="home")])
    await q.edit_message_text("\n".join(lines), reply_markup=InlineKeyboardMarkup(b), parse_mode="HTML")


async def page_withdraw(q, uid, ctx):
    r = user(uid)
    if r["suspicious"]:
        await q.edit_message_text("⚠️ <b>Security Review</b>\n\nContact Support.", reply_markup=back_kb(), parse_mode="HTML")
        return
    if r["balance"] < MIN_WITHDRAWAL:
        await q.edit_message_text(
            "💸 <b>Withdraw</b>\n\n"
            f"💰 Balance: <b>{r['balance']:.2f} ETB</b>\n"
            f"📌 Minimum: <b>{MIN_WITHDRAWAL:.2f} ETB</b>\n\n"
            "You need more balance.",
            reply_markup=back_kb(), parse_mode="HTML")
        return
    w = wallet(uid)
    if not w:
        await q.edit_message_text(
            "💸 <b>Withdraw</b>\n\n⚠️ Set your wallet first.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("👛 Set Wallet", callback_data="wallet")],
                [InlineKeyboardButton("🔙 Back", callback_data="home")]
            ]), parse_mode="HTML")
        return
    ctx.user_data.clear()
    ctx.user_data["wd_step"] = "amount"
    await q.edit_message_text(
        "💸 <b>Withdraw</b>\n\n"
        f"💰 Available: <b>{r['balance']:.2f} ETB</b>\n"
        f"🏦 {escape(w[0])}: <code>{escape(w[1])}</code>\n\n"
        "✍️ Enter the amount you want to withdraw.\n"
        f"Minimum: <b>{MIN_WITHDRAWAL:.2f} ETB</b>",
        reply_markup=back_kb(), parse_mode="HTML")


async def amount_received(update, ctx):
    try:
        amt = float(update.message.text.strip())
    except ValueError:
        await update.message.reply_text("❌ Enter a valid amount. Example: <code>50</code>", parse_mode="HTML")
        return
    r = user(update.effective_user.id)
    if amt < MIN_WITHDRAWAL:
        await update.message.reply_text(f"❌ Minimum withdrawal is <b>{MIN_WITHDRAWAL:.2f} ETB</b>.", parse_mode="HTML")
        return
    if amt > r["balance"]:
        await update.message.reply_text(f"❌ Insufficient balance.\nAvailable: <b>{r['balance']:.2f} ETB</b>", parse_mode="HTML")
        return
    w = wallet(update.effective_user.id)
    ctx.user_data["wd_amount"] = amt
    ctx.user_data["wd_step"] = "confirm"
    await update.message.reply_text(
        "💸 <b>Confirm Withdrawal</b>\n\n"
        f"💰 Amount: <b>{amt:.2f} ETB</b>\n"
        f"🏦 Method: <b>{escape(w[0])}</b>\n"
        f"🔢 Wallet: <code>{escape(w[1])}</code>\n\n"
        "Press Withdraw to submit the request.",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("💸 Withdraw", callback_data="wd_confirm")],
            [InlineKeyboardButton("✏️ Change Amount", callback_data="withdraw")],
            [InlineKeyboardButton("❌ Cancel", callback_data="home")]
        ]), parse_mode="HTML")


async def confirm_wd(q, ctx):
    uid = q.from_user.id
    amt = ctx.user_data.get("wd_amount")
    if amt is None:
        ctx.user_data.clear()
        await q.edit_message_text("❌ Session expired. Please start again.", reply_markup=main_kb())
        return
    r = user(uid)
    w = wallet(uid)
    if not w or r["suspicious"] or r["balance"] < amt:
        ctx.user_data.clear()
        await q.edit_message_text("❌ Withdrawal could not be processed.", reply_markup=back_kb())
        return
    c = db()
    try:
        cur = c.execute("""UPDATE users SET balance=balance-?
                           WHERE user_id=? AND balance>=? AND suspicious=0""",
                        (amt, uid, amt))
        if cur.rowcount != 1:
            c.rollback()
            await q.edit_message_text("❌ Insufficient balance or account under review.", reply_markup=back_kb(), parse_mode="HTML")
            return
        cur = c.execute("""INSERT INTO withdrawals(user_id,amount,wallet_type,wallet_number,status,created_at)
                           VALUES(?,?,?,?,?,?)""",
                        (uid, amt, w[0], w[1], "pending", now()))
        wid = cur.lastrowid
        c.commit()
    except Exception:
        c.rollback()
        log.exception("withdraw")
        await q.edit_message_text("❌ Something went wrong. Please try again.", reply_markup=back_kb())
        return
    finally:
        c.close()
    ctx.user_data.clear()
    await q.edit_message_text(
        "⏳ <b>Withdrawal Processing</b>\n\n"
        f"🆔 Request: <b>#{wid}</b>\n"
        f"💰 Amount: <b>{amt:.2f} ETB</b>\n"
        f"🏦 Method: <b>{escape(w[0])}</b>\n"
        f"🔢 Wallet: <code>{escape(w[1])}</code>\n\n"
        "📌 Status: <b>Pending</b>\n\n"
        "Your request has been sent to Admin for review.",
        reply_markup=back_kb(), parse_mode="HTML")
    try:
        await ctx.bot.send_message(ADMIN_ID,
            "💳 <b>New Withdrawal Request</b>\n\n"
            f"🆔 #{wid}\n👤 User: <code>{uid}</code>\n"
            f"💰 Amount: <b>{amt:.2f} ETB</b>\n"
            f"🏦 {escape(w[0])}: <code>{escape(w[1])}</code>\n\n"
            "⏳ <b>Pending</b>",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("✅ Approve", callback_data=f"approve:{wid}"),
                InlineKeyboardButton("❌ Reject", callback_data=f"reject:{wid}")
            ]]), parse_mode="HTML")
    except Exception:
        log.exception("admin notify")


async def wallet_input(update, ctx):
    uid = update.effective_user.id
    kind = ctx.user_data.get("wallet_kind")
    number = update.message.text.strip()
    if kind == "CBE":
        ok = number.isdigit() and len(number) == 13 and number.startswith("1000")
    else:
        ok = number.isdigit() and len(number) == 10 and (number.startswith("09") or number.startswith("07"))
    if not ok:
        await update.message.reply_text(
            "❌ Invalid wallet.\n\n" +
            ("CBE: 13 digits, starts with 1000." if kind == "CBE" else "Telebirr: 10 digits, starts with 09 or 07."))
        return
    if wallet_used_by_other(kind, number, uid):
        await update.message.reply_text("⚠️ This wallet is already linked to another account.")
        return
    c = db()
    try:
        c.execute("UPDATE users SET wallet_type=?,wallet_number=? WHERE user_id=?", (kind, number, uid))
        c.commit()
    except sqlite3.IntegrityError:
        c.rollback()
        await update.message.reply_text("⚠️ This wallet is already linked to another account.")
        return
    finally:
        c.close()
    ctx.user_data.clear()
    await update.message.reply_text(
        "✅ <b>Wallet Saved!</b>\n\n"
        f"💳 {escape(kind)}\n🔢 <code>{escape(number)}</code>",
        reply_markup=main_kb(), parse_mode="HTML")


async def admin_stats(q):
    c = db()
    a = c.execute("SELECT COUNT(*) n FROM users").fetchone()["n"]
    b = c.execute("SELECT COUNT(*) n FROM users WHERE joined_all=1").fetchone()["n"]
    bal = c.execute("SELECT COALESCE(SUM(balance),0) n FROM users").fetchone()["n"]
    ref = c.execute("SELECT COUNT(*) n FROM referrals WHERE status='paid'").fetchone()["n"]
    wd = c.execute("SELECT COUNT(*) n FROM withdrawals WHERE status='pending'").fetchone()["n"]
    sus = c.execute("SELECT COUNT(*) n FROM users WHERE suspicious=1").fetchone()["n"]
    c.close()
    await q.edit_message_text(
        "📊 <b>Statistics</b>\n\n"
        f"👥 Users: <b>{a}</b>\n✅ Verified: <b>{b}</b>\n"
        f"💰 Total Balance: <b>{bal:.2f} ETB</b>\n"
        f"👥 Successful Referrals: <b>{ref}</b>\n"
        f"💸 Pending Withdrawals: <b>{wd}</b>\n"
        f"⚠️ Suspicious: <b>{sus}</b>",
        reply_markup=back_kb("admin"), parse_mode="HTML")


async def admin_wd(q):
    c = db()
    rows = c.execute("SELECT * FROM withdrawals WHERE status='pending' ORDER BY id DESC LIMIT 30").fetchall()
    c.close()
    if not rows:
        await q.edit_message_text("💸 <b>Pending Withdrawals</b>\n\nNo pending requests.", reply_markup=back_kb("admin"), parse_mode="HTML")
        return
    lines = ["💸 <b>Pending Withdrawals</b>", ""]
    buttons = []
    for r in rows:
        lines.append(
            f"🆔 <b>#{r['id']}</b> | User <code>{r['user_id']}</code>\n"
            f"💰 <b>{r['amount']:.2f} ETB</b> | {escape(r['wallet_type'])}\n"
            f"🔢 <code>{escape(r['wallet_number'])}</code>")
        buttons.append([
            InlineKeyboardButton(f"#{r['id']} ✅ Approve", callback_data=f"approve:{r['id']}"),
            InlineKeyboardButton(f"#{r['id']} ❌ Reject", callback_data=f"reject:{r['id']}")
        ])
    buttons.append([InlineKeyboardButton("🔙 Back", callback_data="admin")])
    await q.edit_message_text("\n\n".join(lines), reply_markup=InlineKeyboardMarkup(buttons), parse_mode="HTML")


async def process_wd(q, ctx, wid, approve):
    if not is_admin(q.from_user.id):
        return
    c = db()
    cur = c.execute("UPDATE withdrawals SET status=? WHERE id=? AND status='pending'",
                    ("approved" if approve else "rejected", wid))
    if cur.rowcount != 1:
        c.rollback(); c.close()
        await q.answer("Already processed.", show_alert=True)
        return
    r = c.execute("SELECT * FROM withdrawals WHERE id=?", (wid,)).fetchone()
    if not approve:
        c.execute("UPDATE users SET balance=balance+? WHERE user_id=?", (r["amount"], r["user_id"]))
    c.commit(); c.close()
    await q.answer("Approved." if approve else "Rejected and refunded.", show_alert=True)
    try:
        if approve:
            msg = f"✅ <b>Withdrawal Approved</b>\n\nRequest #{wid}\n💰 <b>{r['amount']:.2f} ETB</b>"
        else:
            msg = f"❌ <b>Withdrawal Rejected</b>\n\nRequest #{wid}\n💰 <b>{r['amount']:.2f} ETB</b>\n\nThe amount was returned to your balance."
        await ctx.bot.send_message(r["user_id"], msg, parse_mode="HTML")
    except Exception:
        log.exception("user wd notification")
    await admin_wd(q)


async def callback(update, ctx):
    q = update.callback_query
    await q.answer()
    d = q.data
    uid = q.from_user.id
    ensure_user(q.from_user)

    if d == "home":
        ctx.user_data.clear()
        miss = await missing(ctx.bot, uid)
        if miss:
            await q.edit_message_text("👋 Please join all required channels.", reply_markup=channels_kb(miss))
        else:
            await q.edit_message_text("💰 <b>Global Cash Bot</b>\n\nChoose an option:", reply_markup=main_kb(), parse_mode="HTML")
        return
    if d == "verify":
        await verify(q, ctx); return
    if d == "balance":
        await page_balance(q, uid); return
    if d == "referral":
        await page_referral(q, uid); return
    if d == "tasks":
        await page_tasks(q, uid); return
    if d == "wallet":
        await page_wallet(q, uid); return
    if d == "support":
        await page_support(q); return
    if d == "withdraw":
        await page_withdraw(q, uid, ctx); return
    if d == "share":
        link = f"https://t.me/{BOT_USERNAME}?start={uid}"
        await ctx.bot.send_message(uid, f"📤 <b>Your Referral Link</b>\n\n<code>{escape(link)}</code>", parse_mode="HTML")
        return
    if d in ("cbe", "telebirr"):
        ctx.user_data.clear()
        ctx.user_data["wallet_kind"] = "CBE" if d == "cbe" else "Telebirr"
        ctx.user_data["wallet_step"] = "number"
        text = ("🏦 <b>CBE</b>\n\nSend 13 digits starting with 1000."
                if d == "cbe" else
                "📱 <b>Telebirr</b>\n\nSend 10 digits starting with 09 or 07.")
        await q.edit_message_text(text, reply_markup=back_kb(), parse_mode="HTML"); return
    if d == "wd_confirm":
        await confirm_wd(q, ctx); return
    if d.startswith("task:"):
        tid = int(d.split(":")[1])
        c = db(); t = c.execute("SELECT * FROM tasks WHERE id=? AND active=1", (tid,)).fetchone(); c.close()
        if not t:
            await q.answer("Task unavailable.", show_alert=True); return
        await q.edit_message_text(
            f"🎯 <b>{escape(t['title'])}</b>\n\n{escape(t['description'])}\n\n💰 <b>{t['reward']:.2f} ETB</b>",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("📢 Join Channel", url=t["channel_url"])],
                [InlineKeyboardButton("🔙 Back", callback_data="tasks")]
            ]), parse_mode="HTML"); return

    # Admin
    if d == "admin":
        if is_admin(uid):
            await q.edit_message_text("🛠 <b>Admin Panel</b>\n\nChoose an option:", reply_markup=admin_kb(), parse_mode="HTML")
        return
    if not is_admin(uid):
        return
    if d == "a_stats":
        await admin_stats(q); return
    if d == "a_reward":
        ctx.user_data["admin_step"] = "reward"
        await q.edit_message_text(f"💰 <b>Referral Reward</b>\n\nCurrent: <b>{reward():.2f} ETB</b>\n\nSend new amount.", reply_markup=back_kb("admin"), parse_mode="HTML"); return
    if d == "a_wd":
        await admin_wd(q); return
    if d == "a_sus":
        c = db(); rows = c.execute("SELECT user_id,username,balance FROM users WHERE suspicious=1").fetchall(); c.close()
        text = "⚠️ <b>Suspicious Accounts</b>\n\n" + ("\n".join(f"👤 <code>{r['user_id']}</code> @{r['username'] or '-'} | {r['balance']:.2f} ETB" for r in rows) if rows else "No suspicious accounts.")
        await q.edit_message_text(text, reply_markup=back_kb("admin"), parse_mode="HTML"); return
    if d == "a_refs":
        c = db(); rows = c.execute("SELECT referrer_id,COUNT(*) n FROM referrals WHERE status='paid' GROUP BY referrer_id ORDER BY n DESC LIMIT 20").fetchall(); c.close()
        text = "👥 <b>Successful Referrals</b>\n\n" + ("\n".join(f"👤 <code>{r['referrer_id']}</code> — <b>{r['n']}</b>" for r in rows) if rows else "No successful referrals.")
        await q.edit_message_text(text, reply_markup=back_kb("admin"), parse_mode="HTML"); return
    if d == "a_task":
        ctx.user_data["admin_step"] = "task_title"
        await q.edit_message_text("🎯 <b>Add Task</b>\n\nSend task title.", reply_markup=back_kb("admin"), parse_mode="HTML"); return
    if d.startswith("approve:"):
        await process_wd(q, ctx, int(d.split(":")[1]), True); return
    if d.startswith("reject:"):
        await process_wd(q, ctx, int(d.split(":")[1]), False); return


async def messages(update, ctx):
    if not update.message:
        return
    uid = update.effective_user.id
    text = update.message.text.strip()

    if is_admin(uid):
        s = ctx.user_data.get("admin_step")
        if s == "reward":
            try:
                x = float(text)
                if x <= 0: raise ValueError
            except ValueError:
                await update.message.reply_text("❌ Send a valid positive amount.")
                return
            set_setting("referral_reward", f"{x:.2f}")
            ctx.user_data.clear()
            await update.message.reply_text(f"✅ Reward updated to <b>{x:.2f} ETB</b>.", parse_mode="HTML")
            return
        if s == "task_title":
            ctx.user_data["task_title"] = text; ctx.user_data["admin_step"] = "task_desc"
            await update.message.reply_text("Step 2/4: Send task description."); return
        if s == "task_desc":
            ctx.user_data["task_desc"] = text; ctx.user_data["admin_step"] = "task_channel"
            await update.message.reply_text("Step 3/4: Send channel username, e.g. @MyChannel"); return
        if s == "task_channel":
            ch = text if text.startswith("@") else "@"+text
            ctx.user_data["task_channel"] = ch
            ctx.user_data["task_url"] = f"https://t.me/{ch[1:]}"
            ctx.user_data["admin_step"] = "task_reward"
            await update.message.reply_text("Step 4/4: Send reward in ETB."); return
        if s == "task_reward":
            try:
                x = float(text)
                if x <= 0: raise ValueError
            except ValueError:
                await update.message.reply_text("❌ Send a valid positive reward."); return
            c = db()
            c.execute("""INSERT INTO tasks(title,description,channel_username,channel_url,reward,created_at)
                         VALUES(?,?,?,?,?,?)""",
                      (ctx.user_data["task_title"],ctx.user_data["task_desc"],
                       ctx.user_data["task_channel"],ctx.user_data["task_url"],x,now()))
            c.commit(); c.close(); ctx.user_data.clear()
            await update.message.reply_text("✅ Task created.", reply_markup=main_kb()); return

    if ctx.user_data.get("wallet_step") == "number":
        await wallet_input(update, ctx); return
    if ctx.user_data.get("wd_step") == "amount":
        await amount_received(update, ctx); return
    if ctx.user_data.get("wd_step") == "confirm":
        await update.message.reply_text("👇 Use the buttons above to confirm or change the withdrawal.")
        return
    await update.message.reply_text("Please use the buttons.", reply_markup=main_kb())


async def admin_cmd(update, ctx):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("⛔ Admin only."); return
    ctx.user_data.clear()
    await update.message.reply_text("🛠 <b>Admin Panel</b>\n\nChoose an option:", reply_markup=admin_kb(), parse_mode="HTML")


async def cancel(update, ctx):
    ctx.user_data.clear()
    await update.message.reply_text("❌ Cancelled.", reply_markup=main_kb())


async def errors(update, ctx):
    log.error("Unhandled error: %s", ctx.error, exc_info=ctx.error)


def main():
    if not BOT_TOKEN or not ADMIN_ID:
        raise RuntimeError("BOT_TOKEN and ADMIN_ID are required.")
    init_db()
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("admin", admin_cmd))
    app.add_handler(CommandHandler("cancel", cancel))
    app.add_handler(CallbackQueryHandler(callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, messages))
    app.add_error_handler(errors)
    log.info("Global Cash Bot starting...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()

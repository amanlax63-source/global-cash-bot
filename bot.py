import logging
import asyncio
from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import CommandStart, Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage

# ----------------- CONFIGURATION -----------------
BOT_TOKEN = "YOUR_BOT_TOKEN_HERE"  # የቦትህን Token እዚህ አስገባ
ADMIN_ID = 123456789  # ያንተን የTelegram Numeric ID እዚህ አስገባ
# --------------------------------------------------

logging.basicConfig(level=logging.INFO)
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

# ----------------- IN-MEMORY DATABASE -----------------
# ለቀላልነት መረጃዎች በእንደዚህ አይነት Struct ይመዘገባሉ (ለትልቅ ቦት Database/SQLite መጠቀም ይመረጣል)
users_db = {}
# users_db structure:
# user_id: {
#    "username": str,
#    "balance": float,
#    "referred_by": int or None,
#    "referrals": list of user_ids
# }

withdrawals_db = {} # withdrawal_id: {details}
tasks_db = [] # list of active tasks

withdrawal_counter = 1

# ----------------- FSM STATES FOR ADMIN -----------------
class AdminStates(StatesGroup):
    waiting_for_task_details = State()
    waiting_for_broadcast_msg = State()

# ----------------- HELPER FUNCTIONS -----------------
def get_user_data(user_id, username="Unknown"):
    if user_id not in users_db:
        users_db[user_id] = {
            "username": username,
            "balance": 0.0,
            "referred_by": None,
            "referrals": []
        }
    else:
        users_db[user_id]["username"] = username
    return users_db[user_id]

# ----------------- USER COMMANDS -----------------
@dp.message(CommandStart())
async def start_cmd(message: types.Message):
    user_id = message.from_user.id
    username = message.from_user.username or message.from_user.first_name
    
    # Referral Link Check
    args = message.text.split()
    is_new = user_id not in users_db
    user = get_user_data(user_id, username)

    if is_new and len(args) > 1:
        try:
            referrer_id = int(args[1])
            if referrer_id != user_id and referrer_id in users_db:
                user["referred_by"] = referrer_id
                users_db[referrer_id]["referrals"].append(user_id)
                users_db[referrer_id]["balance"] += 5.0  # ለምሳሌ 5 ብር/ነጥብ ለሪፈር
                await bot.send_message(referrer_id, f"🎉 አዲስ ሰው ተጋብዟል! 5 ብር ገቢ ሆኖልዎታል። (User: @{username})")
        except ValueError:
            pass

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💰 Balance / ባላንስ", callback_data="check_balance")],
        [InlineKeyboardButton(text="👥 Refer & Earn / ጋብዝ", callback_data="get_referral")],
        [InlineKeyboardButton(text="📋 Tasks / ታስኮች", callback_data="view_tasks")],
        [InlineKeyboardButton(text="💸 Withdraw / ወጪ አድርግ", callback_data="user_withdraw")]
    ])

    await message.answer(f"ሰላም {message.from_user.first_name}! ወደ ማግኛ ቦት እንኳን ደህና መጡ።", reply_markup=keyboard)

@dp.callback_query(F.data == "check_balance")
async def process_balance(callback: CallbackQuery):
    user = get_user_data(callback.from_user.id)
    await callback.message.answer(f"💳 ያንተ ባላንስ: {user['balance']} ETB\n👥 አጠቃላይ የጋበዝካቸው: {len(user['referrals'])} ሰዎች")
    await callback.answer()

@dp.callback_query(F.data == "get_referral")
async def process_referral(callback: CallbackQuery):
    bot_info = await bot.get_me()
    ref_link = f"https://t.me/{bot_info.username}?start={callback.from_user.id}"
    await callback.message.answer(f"🔗 የእርሶ የመጋበዣ ሊንክ:\n{ref_link}\n\nበእያንዳንዱ ለሚጋብዙት ሰው አበል ያገኛሉ!")
    await callback.answer()

@dp.callback_query(F.data == "view_tasks")
async def process_view_tasks(callback: CallbackQuery):
    if not tasks_db:
        await callback.message.answer("አሁን ላይ ምንም ክፍት ታስክ የለም።")
    else:
        text = "📋 **የሚሰሩ ታስኮች list:**\n\n"
        for idx, task in enumerate(tasks_db, 1):
            text += f"{idx}. {task['title']} - ሽልማት: {task['reward']} ETB\n🔗 Link: {task['link']}\n\n"
        await callback.message.answer(text, parse_mode="Markdown")
    await callback.answer()

# ----------------- USER WITHDRAWAL -----------------
@dp.callback_query(F.data == "user_withdraw")
async def process_user_withdraw(callback: CallbackQuery):
    global withdrawal_counter
    user_id = callback.from_user.id
    user = get_user_data(user_id)

    if user["balance"] < 10.0:  # Minimum balance check
        await callback.message.answer("⚠️ ወጪ ለማድረግ በትንሹ 10 ETB ሊኖርዎት ይገባል።")
        await callback.answer()
        return

    req_id = withdrawal_counter
    withdrawal_counter += 1

    withdrawals_db[req_id] = {
        "user_id": user_id,
        "amount": user["balance"],
        "status": "Pending"
    }

    # ለአድሚን የሚላክ መረጃ (Anti-Multi-Account checkን ጨምሮ)
    referred_list = user["referrals"]
    ref_details_text = ""
    
    if referred_list:
        for ref_id in referred_list:
            ref_user = users_db.get(ref_id, {})
            ref_username = ref_user.get("username", "No Username")
            ref_details_text += f"   • ID: `{ref_id}` | Username: @{ref_username}\n"
    else:
        ref_details_text = "   • ምንም ያመጣው ሰው የለም (0 referrals)\n"

    admin_msg = (
        f"🚨 **አዲስ የሕሳብ ወጪ ጥያቄ (Withdraw Request #{req_id})** 🚨\n\n"
        f"👤 **ተጠቃሚ (Mr. A):** {callback.from_user.full_name}\n"
        f"🆔 **User ID:** `{user_id}`\n"
        f"📛 **Username:** @{callback.from_user.username or 'የለውም'}\n"
        f"💵 **የሚወጣው መጠን:** {user['balance']} ETB\n\n"
        f"📊 **የጋበዛቸው ሰዎች ዝርዝር ({len(referred_list)} ተጠቃሚዎች):**\n"
        f"{ref_details_text}\n"
        f"⚠️ *ማስታወሻ:* ከላይ የተዘረዘሩትን አካውንቶች በማየት Multi-account / Fake መሆናቸውን ያረጋግጡ!"
    )

    admin_keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Approve (ክፈል)", callback_data=f"app_{req_id}"),
            InlineKeyboardButton(text="❌ Reject (ሰርዝ)", callback_data=f"rej_{req_id}")
        ]
    ])

    # ለአድሚኑ መላክ
    await bot.send_message(chat_id=ADMIN_ID, text=admin_msg, reply_markup=admin_keyboard, parse_mode="Markdown")
    await callback.message.answer("✅ የክፍያ ጥያቄዎ ለአድሚን ተልኳል። ግምገማው እንደተጠናቀቀ ይላክልዎታል።")
    await callback.answer()

# ----------------- ADMIN CONTROLS & ACTIONS -----------------

# 1. Admin Dashboard
@dp.message(Command("admin"))
async def admin_dashboard(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return

    total_users = len(users_db)
    total_balance = sum(u["balance"] for u in users_db.values())
    pending_withdrawals = len([w for w in withdrawals_db.values() if w["status"] == "Pending"])

    text = (
        f"👑 **አድሚን ዳሽቦርድ (Admin Dashboard)**\n\n"
        f"👥 አጠቃላይ ተጠቃሚዎች: **{total_users}**\n"
        f"💰 በአሁኑ ሰዓት በስርዓቱ ያሉ ነጥቦች: **{total_balance} ETB**\n"
        f"⏳ በመጠባበቅ ላይ ያሉ Withdraws: **{pending_withdrawals}**\n\n"
        f"**የአድሚን ትዕዛዞች:**\n"
        f"/addtask - አዲስ ታስክ ለመጨመር\n"
        f"/broadcast - ለሁሉም ተጠቃሚዎች መልዕክት ለመላክ\n"
    )
    await message.answer(text, parse_mode="Markdown")

# 2. Approve/Reject Withdrawal Callback
@dp.callback_query(F.data.startswith("app_"))
async def approve_withdrawal(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        return
    
    req_id = int(callback.data.split("_")[1])
    req = withdrawals_db.get(req_id)

    if req and req["status"] == "Pending":
        req["status"] = "Approved"
        user_id = req["user_id"]
        amount = req["amount"]
        
        # የተጠቃሚውን ባላንስ ዜሮ ማድረግ
        users_db[user_id]["balance"] -= amount

        await bot.send_message(chat_id=user_id, text=f"🎉 እንኳን ደስ አለዎት! የ {amount} ETB ክፍያ ጥያቄዎ ጸድቋል!")
        await callback.message.edit_text(callback.message.text + "\n\n✅ **STATUS: APPROVED (ተከፍሏል)**", parse_mode="Markdown")
    await callback.answer()

@dp.callback_query(F.data.startswith("rej_"))
async def reject_withdrawal(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        return

    req_id = int(callback.data.split("_")[1])
    req = withdrawals_db.get(req_id)

    if req and req["status"] == "Pending":
        req["status"] = "Rejected"
        user_id = req["user_id"]

        await bot.send_message(chat_id=user_id, text="❌ የክፍያ ጥያቄዎ ውድቅ ተደርጓል (በሕግ ፍቃድ መጣስ ወይም Fake አካውንት በመጠቀም ምክንያት)።")
        await callback.message.edit_text(callback.message.text + "\n\n❌ **STATUS: REJECTED (ተሰርዟል)**", parse_mode="Markdown")
    await callback.answer()

# 3. Add Task Feature
@dp.message(Command("addtask"))
async def cmd_add_task(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return
    
    await message.answer("እባክዎን የTask ዝርዝር በሚከተለው ፎርማት ይላኩ:\n`Title | Link | Reward`\n\nምሳሌ:\n`Join Channel | https://t.me/example | 2.5`", parse_mode="Markdown")
    await state.set_state(AdminStates.waiting_for_task_details)

@dp.message(AdminStates.waiting_for_task_details)
async def process_add_task(message: types.Message, state: FSMContext):
    try:
        parts = message.text.split("|")
        title = parts[0].strip()
        link = parts[1].strip()
        reward = float(parts[2].strip())

        tasks_db.append({
            "title": title,
            "link": link,
            "reward": reward
        })

        await message.answer("✅ ታስኩ በተሳካ ሁኔታ ተጨምሯል!")
    except Exception:
        await message.answer("⚠️ ስህተት አለ! እባክዎን ፎርማቱን ጠብቀው እንደገና ይሞክሩ።")
    finally:
        await state.clear()

# 4. Broadcast Command (ለቀረቡ ፕሮሞሽኖች)
@dp.message(Command("broadcast"))
async def cmd_broadcast(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return
    
    await message.answer("ለቀረቡ ፕሮሞሽኖች ለሁሉም ዩዘሮች የሚላከውን ጽሁፍ/መልዕክት አስገባ:")
    await state.set_state(AdminStates.waiting_for_broadcast_msg)

@dp.message(AdminStates.waiting_for_broadcast_msg)
async def process_broadcast(message: types.Message, state: FSMContext):
    success_count = 0
    for uid in users_db.keys():
        try:
            await bot.send_message(chat_id=uid, text=message.text)
            success_count += 1
        except Exception:
            pass  # ቦቱን የዘጋ ወይም Block ያደረገ ተጠቃሚ ካለ ያልፈዋል
            
    await message.answer(f"📢 መልዕክቱ በድምሩ ለ {success_count} ተጠቃሚዎች ተልኳል!")
    await state.clear()

# ----------------- MAIN RUNNER -----------------
async def main():
    print("Bot is running...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())

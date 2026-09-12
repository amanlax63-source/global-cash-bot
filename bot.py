import os
from flask import Flask, request
import requests

TOKEN = os.environ.get("BOT_TOKEN")
ADMIN_ID = 7783622296
SUPPORT = "@AmanM_12"

CHANNELS = [
    ("Sheger Tech", "@Sheger_tech1"),
    ("Ethio Cash Flow", "@ethiocashflow"),
    ("Ethio Vortex", "@EthioVortex1"),
    ("Aman Money Lab", "@AmanMoneyLab07"),
    ("Aman Payment Proof", "@AmanPaymentProof"),
]

app = Flask(__name__)


# =========================
# TELEGRAM API
# =========================

def tg(method, data):
    url = f"https://api.telegram.org/bot{TOKEN}/{method}"

    try:
        response = requests.post(
            url,
            json=data,
            timeout=20
        )
        return response.json()
    except Exception as e:
        print("Telegram API ERROR:", e)
        return {"ok": False}


# =========================
# WEBHOOK
# =========================

RENDER_URL = os.environ.get("RENDER_EXTERNAL_URL")


def set_webhook():
    if not TOKEN:
        print("ERROR: BOT_TOKEN is missing")
        return

    if not RENDER_URL:
        print("ERROR: RENDER_EXTERNAL_URL is missing")
        return

    webhook_url = f"{RENDER_URL}/webhook"

    result = tg("setWebhook", {
        "url": webhook_url
    })

    print("WEBHOOK URL:", webhook_url)
    print("WEBHOOK RESULT:", result)


# =========================
# MEMBERSHIP CHECK
# =========================

def check_membership(user_id):
    for name, channel in CHANNELS:

        result = tg("getChatMember", {
            "chat_id": channel,
            "user_id": user_id
        })

        if not result.get("ok"):
            print("Membership check failed:", name, result)
            return False

        status = result["result"]["status"]

        if status not in [
            "member",
            "administrator",
            "creator"
        ]:
            return False

    return True


# =========================
# JOIN MESSAGE
# =========================

def join_message(chat_id):

    buttons = []

    for name, channel in CHANNELS:
        buttons.append([
            {
                "text": f"📢 Join {name}",
                "url": f"https://t.me/{channel.replace('@', '')}"
            }
        ])

    buttons.append([
        {
            "text": "✅ Verify Membership",
            "callback_data": "verify"
        }
    ])

    tg("sendMessage", {
        "chat_id": chat_id,
        "text": (
            "🔐 <b>GLOBAL CASH BOT</b>\n\n"
            "Welcome! 👋\n\n"
            "Before using the bot, please join all required channels below.\n\n"
            "ከዚያ <b>Verify Membership</b> የሚለውን ይጫኑ።"
        ),
        "parse_mode": "HTML",
        "reply_markup": {
            "inline_keyboard": buttons
        }
    })


# =========================
# MAIN MENU
# =========================

def main_menu(chat_id):

    keyboard = [
        [
            {
                "text": "💰 Balance",
                "callback_data": "balance"
            },
            {
                "text": "👥 Referral",
                "callback_data": "referral"
            }
        ],
        [
            {
                "text": "🎯 Tasks",
                "callback_data": "tasks"
            },
            {
                "text": "👛 Wallet",
                "callback_data": "wallet"
            }
        ],
        [
            {
                "text": "💸 Withdraw",
                "callback_data": "withdraw"
            },
            {
                "text": "🎁 Bonus",
                "callback_data": "bonus"
            }
        ],
        [
            {
                "text": "🆘 Support",
                "callback_data": "support"
            }
        ]
    ]

    tg("sendMessage", {
        "chat_id": chat_id,
        "text": (
            "🏠 <b>GLOBAL CASH BOT</b>\n\n"
            "Welcome! 🎉\n\n"
            "Choose an option below 👇"
        ),
        "parse_mode": "HTML",
        "reply_markup": {
            "inline_keyboard": keyboard
        }
    })


# =========================
# START
# =========================

def start(chat_id):

    if check_membership(chat_id):

        tg("sendMessage", {
            "chat_id": chat_id,
            "text": (
                "🎉 <b>WELCOME TO GLOBAL CASH BOT</b>\n\n"
                "✅ Account verified successfully!\n\n"
                "💰 Earn • Refer • Complete Tasks • Withdraw"
            ),
            "parse_mode": "HTML"
        })

        main_menu(chat_id)

    else:
        join_message(chat_id)


# =========================
# SIMPLE PAGE
# =========================

def simple_page(chat_id, text):

    tg("sendMessage", {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "reply_markup": {
            "inline_keyboard": [
                [
                    {
                        "text": "🔙 Back",
                        "callback_data": "menu"
                    }
                ]
            ]
        }
    })


# =========================
# HOME
# =========================

@app.route("/", methods=["GET"])
def home():
    return "Global Cash Bot is running! 🚀"


# =========================
# WEBHOOK
# =========================

@app.route("/webhook", methods=["POST"])
def webhook():

    update = request.get_json(silent=True)

    if not update:
        return "OK"

    try:

        # =====================
        # MESSAGE
        # =====================

        if "message" in update:

            message = update["message"]

            chat_id = message["chat"]["id"]

            text = message.get("text", "")

            if text.startswith("/start"):
                start(chat_id)


        # =====================
        # CALLBACK QUERY
        # =====================

        elif "callback_query" in update:

            query = update["callback_query"]

            chat_id = query["message"]["chat"]["id"]

            data = query["data"]

            tg("answerCallbackQuery", {
                "callback_query_id": query["id"]
            })


            # =====================
            # VERIFY
            # =====================

            if data == "verify":

                if check_membership(chat_id):

                    tg("sendMessage", {
                        "chat_id": chat_id,
                        "text": (
                            "✅ <b>Verified Successfully!</b>\n\n"
                            "🎉 Welcome to Global Cash Bot!"
                        ),
                        "parse_mode": "HTML"
                    })

                    main_menu(chat_id)

                else:

                    tg("sendMessage", {
                        "chat_id": chat_id,
                        "text": (
                            "❌ <b>Verification failed.</b>\n\n"
                            "እባክዎ 5ቱንም required channels "
                            "ይቀላቀሉ፣ ከዚያ Verify "
                            "እንደገና ይጫኑ።"
                        ),
                        "parse_mode": "HTML"
                    })


            # =====================
            # BALANCE
            # =====================

            elif data == "balance":

                tg("sendMessage", {
                    "chat_id": chat_id,
                    "text": (
                        "💰 <b>Balance</b>\n\n"
                        "💵 Balance: <b>0 Birr</b>\n"
                        "📊 Total Earned: <b>0 Birr</b>"
                    ),
                    "parse_mode": "HTML",
                    "reply_markup": {
                        "inline_keyboard": [
                            [
                                {
                                    "text": "🔙 Back",
                                    "callback_data": "menu"
                                }
                            ]
                        ]
                    }
                })


            # =====================
            # REFERRAL
            # =====================

            elif data == "referral":

                tg("sendMessage", {
                    "chat_id": chat_id,
                    "text": (
                        "👥 <b>Referral</b>\n\n"
                        "🎁 Referral Reward: <b>2 Birr</b>\n\n"
                        "👤 Referral Count: <b>0</b>\n\n"
                        "🔗 <b>Your Referral Link:</b>\n"
                        f"https://t.me/Globalcashh_bot"
                        f"?start=ref_{chat_id}\n\n"
                        "💡 የሚጋብዙት ሰው 5ቱንም "
                        "required channels ከተቀላቀለ በኋላ "
                        "2 Birr ይጨመርልዎታል።"
                    ),
                    "parse_mode": "HTML",
                    "reply_markup": {
                        "inline_keyboard": [
                            [
                                {
                                    "text": "🔙 Back",
                                    "callback_data": "menu"
                                }
                            ]
                        ]
                    }
                })


            # =====================
            # TASKS
            # =====================

            elif data == "tasks":

                simple_page(
                    chat_id,
                    "🎯 <b>Tasks</b>\n\n"
                    "No tasks available yet."
                )


            # =====================
            # WALLET
            # =====================

            elif data == "wallet":

                simple_page(
                    chat_id,
                    "👛 <b>Wallet</b>\n\n"
                    "📱 Telebirr\n"
                    "🏦 CBE"
                )


            # =====================
            # WITHDRAW
            # =====================

            elif data == "withdraw":

                simple_page(
                    chat_id,
                    "💸 <b>Withdraw</b>\n\n"
                    "📱 Telebirr\n"
                    "🏦 CBE"
                )


            # =====================
            # BONUS
            # =====================

            elif data == "bonus":

                simple_page(
                    chat_id,
                    "🎁 <b>Daily Bonus</b>\n\n"
                    "Coming soon."
                )


            # =====================
            # SUPPORT
            # =====================

            elif data == "support":

                simple_page(
                    chat_id,
                    f"🆘 <b>Support</b>\n\n"
                    f"Contact: {SUPPORT}"
                )


            # =====================
            # BACK
            # =====================

            elif data == "menu":

                main_menu(chat_id)


    except Exception as e:

        print("WEBHOOK ERROR:", e)


    return "OK"


# =========================
# START SERVER
# =========================

if __name__ == "__main__":

    # Set Telegram webhook
    set_webhook()

    port = int(
        os.environ.get("PORT", 10000)
    )

    app.run(
        host="0.0.0.0",
        port=port
    )

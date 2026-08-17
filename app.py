from flask import Flask, request
import requests
import os

app = Flask(__name__)

TOKEN = os.getenv("BOT_TOKEN")

@app.route("/")
def home():
    return "Bot is running"

@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.json

    if "message" in data:
        chat_id = data["message"]["chat"]["id"]
        text = data["message"].get("text", "")

        if text == "/start":
            msg = (
                "สวัสดีครับ 🤖\n\n"
                "ระบบพร้อมใช้งานแล้ว\n\n"
                "ลองพิมพ์\n"
                "/cover Mahjong Ways"
            )
        elif text.startswith("/cover"):
            game = text.replace("/cover", "").strip()
            msg = f"รับคำสั่งแล้ว 🎮\nเกม: {game}"
        else:
            msg = "พิมพ์ /start เพื่อเริ่มใช้งาน"

        requests.post(
            f"https://api.telegram.org/bot{TOKEN}/sendMessage",
            json={
                "chat_id": chat_id,
                "text": msg
            }
        )

    return "ok"

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))

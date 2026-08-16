import os
import requests

TOKEN = os.getenv("BOT_TOKEN")
API = f"https://api.telegram.org/bot{TOKEN}"


def send_message(chat_id, text):
    requests.post(
        f"{API}/sendMessage",
        json={
            "chat_id": chat_id,
            "text": text
        }
    )


def main():
    offset = 0

    print("Bot started...")

    while True:
        try:
            response = requests.get(
                f"{API}/getUpdates",
                params={
                    "offset": offset,
                    "timeout": 30
                }
            ).json()

            for update in response.get("result", []):
                offset = update["update_id"] + 1

                message = update.get("message")
                if not message:
                    continue

                chat_id = message["chat"]["id"]
                text = message.get("text", "")

                if text == "/start":
                    send_message(
                        chat_id,
                        "สวัสดีครับ 🤖\n\n"
                        "ระบบบอทพร้อมใช้งานแล้ว\n"
                        "พิมพ์ /cover ตามด้วยชื่อเกม\n\n"
                        "ตัวอย่าง:\n"
                        "/cover Mahjong Ways"
                    )

                elif text.startswith("/cover"):
                    game = text.replace("/cover", "", 1).strip()

                    if not game:
                        send_message(
                            chat_id,
                            "กรุณาระบุชื่อเกมครับ\n\n"
                            "ตัวอย่าง:\n"
                            "/cover Mahjong Ways"
                        )
                    else:
                        send_message(
                            chat_id,
                            f"รับคำสั่งแล้วครับ 🎮\n\n"
                            f"เกม: {game}\n\n"
                            "ขั้นต่อไปจะเชื่อมคลังรูปเกมและ AI ให้ครับ"
                        )

                else:
                    send_message(
                        chat_id,
                        "พิมพ์ /start เพื่อเริ่มใช้งานครับ"
                    )

        except Exception as e:
            print("Error:", e)


if __name__ == "__main__":
    main()

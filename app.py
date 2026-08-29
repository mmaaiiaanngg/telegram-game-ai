from flask import Flask, request
import requests
import os
import random
from google.oauth2 import service_account
from googleapiclient.discovery import build

app = Flask(__name__)

TOKEN = os.getenv("BOT_TOKEN")
DRIVE_FOLDER_ID = os.getenv("DRIVE_FOLDER_ID")
GOOGLE_KEY_FILE = "/etc/secrets/google-service-account.json"


def send_message(chat_id, text):
    requests.post(
        f"https://api.telegram.org/bot{TOKEN}/sendMessage",
        json={
            "chat_id": chat_id,
            "text": text
        },
        timeout=20
    )


def get_drive_files():
    credentials = service_account.Credentials.from_service_account_file(
        GOOGLE_KEY_FILE,
        scopes=["https://www.googleapis.com/auth/drive"]
    )

    drive = build(
        "drive",
        "v3",
        credentials=credentials,
        cache_discovery=False
    )

    result = drive.files().list(
        q=f"'{DRIVE_FOLDER_ID}' in parents and trashed = false",
        fields="files(id,name,mimeType)",
        pageSize=20
    ).execute()

    return result.get("files", [])
def download_drive_file(file_id):
    credentials = service_account.Credentials.from_service_account_file(
        GOOGLE_KEY_FILE,
        scopes=["https://www.googleapis.com/auth/drive.readonly"]
    )

    drive = build(
        "drive",
        "v3",
        credentials=credentials,
        cache_discovery=False
    )

    return drive.files().get_media(fileId=file_id).execute()
def get_drive_service():
    credentials = service_account.Credentials.from_service_account_file(
        GOOGLE_KEY_FILE,
        scopes=["https://www.googleapis.com/auth/drive"]
    )

    return build(
        "drive",
        "v3",
        credentials=credentials,
        cache_discovery=False
    )
@app.route("/")
def home():
    return "Bot is running"


@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.get_json(silent=True) or {}

    if "message" in data:
        chat_id = data["message"]["chat"]["id"]
        text = data["message"].get("text", "")

        if text == "/start":
            msg = (
                "สวัสดีครับ 🤖\n\n"
                "ระบบพร้อมใช้งานแล้ว\n\n"
                "ลองพิมพ์\n"
                "/drive\n"
                "/cover Mahjong Ways"
            )

        elif text == "/drive":
            try:
                files = get_drive_files()

                if not files:
                    msg = "✅ เชื่อม Google Drive สำเร็จ แต่โฟลเดอร์ยังไม่มีไฟล์"
                else:
                    names = [f"• {file['name']}" for file in files]
                    msg = "✅ พบไฟล์ใน Google Drive:\n\n" + "\n".join(names)

            except Exception as e:
                print("Drive error:", e)
                msg = "❌ ยังอ่าน Google Drive ไม่สำเร็จ กรุณาตรวจสอบ Logs"

        elif text.startswith("/cover"):
            game = text.replace("/cover", "").strip()
    
        if game:
            try:
                files = get_drive_files()
                game_key = game.lower().replace(" ", "-")
    
                matched_files = [
                    f for f in files
                    if game_key in f["name"].lower()
                    and f.get("mimeType", "").startswith("image/")
                ]
    
                if matched_files:
                    image_file = random.choice(matched_files)
                    image_bytes = download_drive_file(image_file["id"])
    
                    requests.post(
                        f"https://api.telegram.org/bot{TOKEN}/sendPhoto",
                        data={
                            "chat_id": chat_id,
                            "caption": f"🎮 {game}\n📁 {image_file['name']}"
                        },
                        files={
                            "photo": (image_file["name"], image_bytes)
                        },
                        timeout=30
                    )
    
                    return "ok"
    
                else:
                    msg = f"❌ ไม่พบรูปเกม {game} ใน Google Drive"
    
            except Exception as e:
                print("Cover error:", e)
                msg = "❌ ดึงรูปจาก Google Drive ไม่สำเร็จ กรุณาตรวจสอบ Logs"
    
            else:
                msg = "กรุณาระบุชื่อเกม เช่น\n/cover Mahjong Ways"

        else:
            msg = "พิมพ์ /start เพื่อเริ่มใช้งาน"

        send_message(chat_id, msg)

    return "ok"


@app.route("/setup-webhook")
def setup_webhook():
    webhook_url = "https://telegram-game-ai-1.onrender.com/webhook"

    response = requests.get(
        f"https://api.telegram.org/bot{TOKEN}/setWebhook",
        params={"url": webhook_url},
        timeout=20
    )

    return response.json()
if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 10000))
    )



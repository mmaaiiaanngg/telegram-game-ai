from flask import Flask, request
import requests
import os
import random
import zipfile
import tempfile
import mimetypes
import threading
import shutil
import io

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload


app = Flask(__name__)


# =========================
# CONFIG
# =========================

TOKEN = os.getenv("BOT_TOKEN")
DRIVE_FOLDER_ID = os.getenv("DRIVE_FOLDER_ID")

GOOGLE_KEY_FILE = "/etc/secrets/google-service-account.json"

DROPBOX_SHARED_URL = (
    "https://www.dropbox.com/scl/fo/"
    "vaw97cwctz6rkbzp47u8q/h"
    "?rlkey=pseqzodk25k3oxswdaox5a5gr&dl=1"
)

WEBHOOK_URL = "https://telegram-game-ai-1.onrender.com/webhook"

IMPORT_LOCK = threading.Lock()


# =========================
# TELEGRAM
# =========================

def send_message(chat_id, text):
    try:
        requests.post(
            f"https://api.telegram.org/bot{TOKEN}/sendMessage",
            json={
                "chat_id": chat_id,
                "text": text
            },
            timeout=20
        )
    except Exception as e:
        print("Telegram send_message error:", e)


def send_photo(chat_id, filename, image_bytes, caption=""):
    try:
        response = requests.post(
            f"https://api.telegram.org/bot{TOKEN}/sendPhoto",
            data={
                "chat_id": chat_id,
                "caption": caption
            },
            files={
                "photo": (filename, image_bytes)
            },
            timeout=60
        )

        response.raise_for_status()
        return True

    except Exception as e:
        print("Telegram send_photo error:", e)
        return False


# =========================
# GOOGLE DRIVE
# =========================

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


def list_drive_children(parent_id):
    drive = get_drive_service()

    files = []
    page_token = None

    while True:
        result = drive.files().list(
            q=f"'{parent_id}' in parents and trashed = false",
            fields="nextPageToken, files(id,name,mimeType)",
            pageSize=1000,
            pageToken=page_token
        ).execute()

        files.extend(result.get("files", []))

        page_token = result.get("nextPageToken")

        if not page_token:
            break

    return files


def get_drive_files():
    return list_drive_children(DRIVE_FOLDER_ID)


def download_drive_file(file_id):
    drive = get_drive_service()

    return drive.files().get_media(
        fileId=file_id
    ).execute()


def normalize_name(text):
    return (
        text.lower()
        .replace(" ", "")
        .replace("-", "")
        .replace("_", "")
    )


def find_game_folder(game_name):
    items = get_drive_files()

    game_key = normalize_name(game_name)

    for item in items:
        if item.get("mimeType") == "application/vnd.google-apps.folder":
            if normalize_name(item["name"]) == game_key:
                return item

    return None


def get_or_create_game_folder(drive, game_name, folder_cache):
    key = normalize_name(game_name)

    if key in folder_cache:
        return folder_cache[key]

    folder = drive.files().create(
        body={
            "name": game_name,
            "mimeType": "application/vnd.google-apps.folder",
            "parents": [DRIVE_FOLDER_ID]
        },
        fields="id,name"
    ).execute()

    folder_cache[key] = folder["id"]

    print("Created folder:", game_name)

    return folder["id"]


# =========================
# DROPBOX
# =========================

def download_dropbox_zip():
    response = requests.get(
        DROPBOX_SHARED_URL,
        stream=True,
        timeout=300,
        headers={
            "User-Agent": "Mozilla/5.0"
        }
    )

    response.raise_for_status()

    temp_file = tempfile.NamedTemporaryFile(
        delete=False,
        suffix=".zip"
    )

    for chunk in response.iter_content(
        chunk_size=1024 * 1024
    ):
        if chunk:
            temp_file.write(chunk)

    temp_file.close()

    return temp_file.name


# =========================
# IMPORT DROPBOX -> DRIVE
# =========================

def import_zip_to_drive(zip_path):
    drive = get_drive_service()

    root_items = list_drive_children(DRIVE_FOLDER_ID)

    folder_cache = {}

    for item in root_items:
        if item.get("mimeType") == "application/vnd.google-apps.folder":
            folder_cache[normalize_name(item["name"])] = item["id"]

    uploaded = 0
    skipped = 0

    existing_files_cache = {}

    with zipfile.ZipFile(zip_path, "r") as zip_ref:

        for member in zip_ref.infolist():

            if member.is_dir():
                continue

            path = member.filename.replace("\\", "/")
            parts = [
                p for p in path.split("/")
                if p and p != "__MACOSX"
            ]

            if len(parts) < 2:
                continue

            # Dropbox ZIP อาจมีโฟลเดอร์ PG Soft ครอบอยู่ด้านนอก
            if normalize_name(parts[0]) == normalize_name("PG Soft"):
                parts = parts[1:]

            if len(parts) < 2:
                continue

            game_name = parts[0]
            filename = parts[-1]

            if filename.startswith("."):
                continue

            mime_type, _ = mimetypes.guess_type(filename)

            if not mime_type or not mime_type.startswith("image/"):
                continue

            folder_id = get_or_create_game_folder(
                drive,
                game_name,
                folder_cache
            )

            if folder_id not in existing_files_cache:
                existing_items = list_drive_children(folder_id)

                existing_files_cache[folder_id] = {
                    item["name"]
                    for item in existing_items
                }

            if filename in existing_files_cache[folder_id]:
                skipped += 1
                continue

            image_data = zip_ref.read(member)

            media = MediaIoBaseUpload(
                io.BytesIO(image_data),
                mimetype=mime_type,
                resumable=False
            )

            drive.files().create(
                body={
                    "name": filename,
                    "parents": [folder_id]
                },
                media_body=media,
                fields="id"
            ).execute()

            existing_files_cache[folder_id].add(filename)

            uploaded += 1

            print(
                f"Uploaded: {game_name} / {filename}"
            )

    return uploaded, skipped


def run_import(chat_id):
    if not IMPORT_LOCK.acquire(blocking=False):
        send_message(
            chat_id,
            "⚠️ ตอนนี้กำลังนำเข้ารูปอยู่ กรุณารอให้งานเดิมเสร็จก่อน"
        )
        return

    zip_path = None

    try:
        send_message(
            chat_id,
            "📥 เริ่มดาวน์โหลด PG Soft จาก Dropbox แล้ว..."
        )

        zip_path = download_dropbox_zip()

        send_message(
            chat_id,
            "📦 ดาวน์โหลดเสร็จแล้ว กำลังแยกเกมและอัปโหลดเข้า Google Drive..."
        )

        uploaded, skipped = import_zip_to_drive(zip_path)

        send_message(
            chat_id,
            (
                "✅ นำเข้ารูปเรียบร้อยแล้ว\n\n"
                f"📤 อัปโหลดใหม่: {uploaded} ไฟล์\n"
                f"⏭️ ไฟล์ที่มีอยู่แล้ว: {skipped} ไฟล์"
            )
        )

    except zipfile.BadZipFile:
        print("Import error: Dropbox response is not a ZIP")

        send_message(
            chat_id,
            "❌ Dropbox ไม่ได้ส่งไฟล์ ZIP กลับมา กรุณาตรวจสอบ Logs"
        )

    except Exception as e:
        print("Import error:", repr(e))

        send_message(
            chat_id,
            "❌ นำเข้ารูปไม่สำเร็จ กรุณาตรวจสอบ Render Logs"
        )

    finally:
        if zip_path and os.path.exists(zip_path):
            try:
                os.remove(zip_path)
            except Exception:
                pass

        IMPORT_LOCK.release()


# =========================
# COVER
# =========================

def find_cover_images(game_name):
    game_folder = find_game_folder(game_name)

    # ถ้ามีโฟลเดอร์เกมแล้ว ใช้รูปในโฟลเดอร์นั้น
    if game_folder:
        files = list_drive_children(game_folder["id"])

        images = [
            f for f in files
            if f.get("mimeType", "").startswith("image/")
        ]

        if images:
            return images

    # รองรับรูปเก่าที่เคยวางรวมไว้ใน telegram-game-images
    files = get_drive_files()

    game_key = normalize_name(game_name)

    images = [
        f for f in files
        if f.get("mimeType", "").startswith("image/")
        and game_key in normalize_name(f["name"])
    ]

    return images


# =========================
# ROUTES
# =========================

@app.route("/")
def home():
    return "Bot is running"


@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.get_json(silent=True) or {}

    if "message" not in data:
        return "ok"

    message = data["message"]

    chat_id = message["chat"]["id"]
    text = message.get("text", "").strip()

    if text == "/start":
        msg = (
            "🤖 สวัสดีครับ\n"
            "ระบบพร้อมใช้งานแล้ว\n\n"
            "คำสั่งที่ใช้ได้\n"
            "/drive\n"
            "/cover Mahjong Ways\n"
            "/import"
        )

        send_message(chat_id, msg)
        return "ok"


    elif text == "/drive":
        try:
            files = get_drive_files()

            if not files:
                msg = (
                    "✅ เชื่อม Google Drive สำเร็จ "
                    "แต่โฟลเดอร์ยังไม่มีไฟล์"
                )

            else:
                names = [
                    f"• {file['name']}"
                    for file in files[:50]
                ]

                msg = (
                    "✅ พบไฟล์/โฟลเดอร์ใน Google Drive:\n\n"
                    + "\n".join(names)
                )

            send_message(chat_id, msg)

        except Exception as e:
            print("Drive error:", e)

            send_message(
                chat_id,
                "❌ เชื่อม Google Drive ไม่สำเร็จ กรุณาตรวจสอบ Logs"
            )

        return "ok"


    elif text == "/import":
        send_message(
            chat_id,
            "⏳ รับคำสั่งแล้ว กำลังเริ่มนำเข้ารูป..."
        )

        threading.Thread(
            target=run_import,
            args=(chat_id,),
            daemon=True
        ).start()

        return "ok"


    elif text.startswith("/cover"):
        game = text.replace("/cover", "", 1).strip()

        if not game:
            send_message(
                chat_id,
                "กรุณาระบุชื่อเกม เช่น\n/cover Mahjong Ways"
            )
            return "ok"

        try:
            matched_files = find_cover_images(game)

            if not matched_files:
                send_message(
                    chat_id,
                    f"❌ ไม่พบรูปเกม {game} ใน Google Drive"
                )
                return "ok"

            image_file = random.choice(matched_files)

            image_bytes = download_drive_file(
                image_file["id"]
            )

            success = send_photo(
                chat_id,
                image_file["name"],
                image_bytes,
                (
                    f"🎮 {game}\n"
                    f"📁 {image_file['name']}"
                )
            )

            if not success:
                send_message(
                    chat_id,
                    "❌ ส่งรูปไม่สำเร็จ กรุณาตรวจสอบ Logs"
                )

        except Exception as e:
            print("Cover error:", e)

            send_message(
                chat_id,
                "❌ ดึงรูปจาก Google Drive ไม่สำเร็จ กรุณาตรวจสอบ Logs"
            )

        return "ok"


    else:
        send_message(
            chat_id,
            "พิมพ์ /start เพื่อดูคำสั่ง"
        )

        return "ok"


@app.route("/setup-webhook")
def setup_webhook():
    response = requests.get(
        f"https://api.telegram.org/bot{TOKEN}/setWebhook",
        params={
            "url": WEBHOOK_URL
        },
        timeout=20
    )

    return response.json()


# =========================
# START
# =========================

if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 10000))
    )

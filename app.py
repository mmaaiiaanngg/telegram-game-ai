from flask import Flask, request
import requests
import os
import random
import mimetypes
import threading
import io

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload


app = Flask(__name__)


# ==================================================
# CONFIG
# ==================================================

TOKEN = os.getenv("BOT_TOKEN")
DRIVE_FOLDER_ID = os.getenv("DRIVE_FOLDER_ID")
DROPBOX_ACCESS_TOKEN = os.getenv("DROPBOX_ACCESS_TOKEN")

GOOGLE_KEY_FILE = "/etc/secrets/google-service-account.json"

WEBHOOK_URL = "https://telegram-game-ai-1.onrender.com/webhook"

# โฟลเดอร์ต้นทางใน Dropbox
DROPBOX_SOURCE_FOLDER = "/PG"

IMPORT_LOCK = threading.Lock()


# ==================================================
# TELEGRAM
# ==================================================

def send_message(chat_id, text):
    try:
        response = requests.post(
            f"https://api.telegram.org/bot{TOKEN}/sendMessage",
            json={
                "chat_id": chat_id,
                "text": text
            },
            timeout=30
        )

        response.raise_for_status()

    except Exception as e:
        print("Telegram send_message error:", repr(e))


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
        print("Telegram send_photo error:", repr(e))
        return False


# ==================================================
# GOOGLE DRIVE
# ==================================================

def get_drive_service():
    credentials = service_account.Credentials.from_service_account_file(
        GOOGLE_KEY_FILE,
        scopes=[
            "https://www.googleapis.com/auth/drive"
        ]
    )

    return build(
        "drive",
        "v3",
        credentials=credentials,
        cache_discovery=False
    )


def list_drive_children(parent_id):
    drive = get_drive_service()

    results = []
    page_token = None

    while True:
        response = drive.files().list(
            q=f"'{parent_id}' in parents and trashed = false",
            fields="nextPageToken,files(id,name,mimeType)",
            pageSize=1000,
            pageToken=page_token
        ).execute()

        results.extend(
            response.get("files", [])
        )

        page_token = response.get("nextPageToken")

        if not page_token:
            break

    return results


def get_drive_files():
    return list_drive_children(
        DRIVE_FOLDER_ID
    )


def download_drive_file(file_id):
    drive = get_drive_service()

    return drive.files().get_media(
        fileId=file_id
    ).execute()


def normalize_name(text):
    return (
        str(text)
        .lower()
        .strip()
        .replace(" ", "")
        .replace("-", "")
        .replace("_", "")
    )


def get_or_create_game_folder(
    drive,
    game_name,
    folder_cache
):
    game_key = normalize_name(game_name)

    if game_key in folder_cache:
        return folder_cache[game_key]

    folder = drive.files().create(
        body={
            "name": game_name,
            "mimeType": "application/vnd.google-apps.folder",
            "parents": [DRIVE_FOLDER_ID]
        },
        fields="id,name"
    ).execute()

    folder_cache[game_key] = folder["id"]

    print(
        "Created Google Drive folder:",
        game_name
    )

    return folder["id"]


def find_game_folder(game_name):
    game_key = normalize_name(game_name)

    for item in get_drive_files():

        if (
            item.get("mimeType")
            == "application/vnd.google-apps.folder"
        ):
            if normalize_name(item["name"]) == game_key:
                return item

    return None


# ==================================================
# DROPBOX API
# ==================================================

def dropbox_headers():
    return {
        "Authorization":
            f"Bearer {DROPBOX_ACCESS_TOKEN}",
        "Content-Type":
            "application/json"
    }


def test_dropbox_connection():
    response = requests.post(
        "https://api.dropboxapi.com/2/users/get_current_account",
        headers={
            "Authorization":
                f"Bearer {DROPBOX_ACCESS_TOKEN}"
        },
        timeout=30
    )

    response.raise_for_status()

    return response.json()


def list_dropbox_files():
    """
    อ่านไฟล์ทั้งหมดภายใน /PG แบบ recursive
    """

    url = (
        "https://api.dropboxapi.com/"
        "2/files/list_folder"
    )

    response = requests.post(
        url,
        headers=dropbox_headers(),
        json={
            "path": DROPBOX_SOURCE_FOLDER,
            "recursive": True,
            "include_deleted": False,
            "include_non_downloadable_files": False
        },
        timeout=60
    )

    response.raise_for_status()

    data = response.json()

    entries = data.get(
        "entries",
        []
    )

    while data.get("has_more"):

        cursor = data["cursor"]

        response = requests.post(
            "https://api.dropboxapi.com/2/files/list_folder/continue",
            headers=dropbox_headers(),
            json={
                "cursor": cursor
            },
            timeout=60
        )

        response.raise_for_status()

        data = response.json()

        entries.extend(
            data.get("entries", [])
        )

    return entries

def list_dropbox_root():
    response = requests.post(
        "https://api.dropboxapi.com/2/files/list_folder",
        headers=dropbox_headers(),
        json={
            "path": "",
            "recursive": False,
            "include_deleted": False,
            "include_non_downloadable_files": False
        },
        timeout=60
    )

    response.raise_for_status()

    return response.json().get("entries", [])
    
def download_dropbox_file(path):
    response = requests.post(
        "https://content.dropboxapi.com/2/files/download",
        headers={
            "Authorization":
                f"Bearer {DROPBOX_ACCESS_TOKEN}",
            "Dropbox-API-Arg":
                '{"path": "' +
                path.replace('"', '\\"') +
                '"}'
        },
        timeout=120
    )

    response.raise_for_status()

    return response.content


# ==================================================
# IMPORT DROPBOX -> GOOGLE DRIVE
# ==================================================

def get_game_name_from_dropbox_path(path_display):
    """
    ตัวอย่าง:
    /PG/Mahjong Ways/image.jpg

    คืนค่า:
    Mahjong Ways
    """

    clean_path = (
        path_display
        .replace("\\", "/")
        .strip("/")
    )

    parts = clean_path.split("/")

    if len(parts) < 3:
        return None

    # parts[0] = PG
    # parts[1] = ชื่อเกม
    return parts[1]


def import_dropbox_to_drive(chat_id):
    if not DROPBOX_ACCESS_TOKEN:
        send_message(
            chat_id,
            "❌ ไม่พบ DROPBOX_ACCESS_TOKEN ใน Render"
        )
        return

    if not IMPORT_LOCK.acquire(
        blocking=False
    ):
        send_message(
            chat_id,
            "⚠️ ตอนนี้มีงาน Import กำลังทำงานอยู่ กรุณารอก่อน"
        )
        return

    try:
        send_message(
            chat_id,
            "🔗 กำลังเชื่อมต่อ Dropbox..."
        )

        account = test_dropbox_connection()

        print(
            "Dropbox connected:",
            account.get(
                "name",
                {}
            ).get(
                "display_name",
                ""
            )
        )

        send_message(
            chat_id,
            "✅ เชื่อม Dropbox สำเร็จ\n📂 กำลังอ่านโฟลเดอร์ PG..."
        )

        entries = list_dropbox_files()

        dropbox_files = [
            item
            for item in entries
            if item.get(".tag") == "file"
        ]

        if not dropbox_files:
            send_message(
                chat_id,
                "❌ ไม่พบไฟล์ใน Dropbox /PG"
            )
            return

        send_message(
            chat_id,
            (
                "📋 พบไฟล์ใน Dropbox "
                f"{len(dropbox_files)} ไฟล์\n"
                "⏳ กำลังแยกเกมและนำเข้า Google Drive..."
            )
        )

        drive = get_drive_service()

        # --------------------------
        # Cache โฟลเดอร์ Google Drive
        # --------------------------

        root_items = list_drive_children(
            DRIVE_FOLDER_ID
        )

        folder_cache = {}

        for item in root_items:

            if (
                item.get("mimeType")
                == "application/vnd.google-apps.folder"
            ):
                folder_cache[
                    normalize_name(
                        item["name"]
                    )
                ] = item["id"]

        # --------------------------
        # Cache ไฟล์ที่มีอยู่แล้ว
        # --------------------------

        existing_files_cache = {}

        uploaded = 0
        skipped = 0
        ignored = 0
        failed = 0

        for index, item in enumerate(
            dropbox_files,
            start=1
        ):

            try:
                path_display = item.get(
                    "path_display",
                    ""
                )

                filename = item.get(
                    "name",
                    ""
                )

                game_name = (
                    get_game_name_from_dropbox_path(
                        path_display
                    )
                )

                if not game_name:
                    ignored += 1
                    continue

                mime_type, _ = (
                    mimetypes.guess_type(
                        filename
                    )
                )

                # เอาเฉพาะไฟล์รูป
                if (
                    not mime_type
                    or not mime_type.startswith(
                        "image/"
                    )
                ):
                    ignored += 1
                    continue

                folder_id = (
                    get_or_create_game_folder(
                        drive,
                        game_name,
                        folder_cache
                    )
                )

                # โหลดรายชื่อไฟล์เดิมในโฟลเดอร์
                if (
                    folder_id
                    not in existing_files_cache
                ):

                    existing_items = (
                        list_drive_children(
                            folder_id
                        )
                    )

                    existing_files_cache[
                        folder_id
                    ] = {
                        old_file["name"]
                        for old_file
                        in existing_items
                        if old_file.get(
                            "mimeType",
                            ""
                        ).startswith(
                            "image/"
                        )
                    }

                # ถ้ามีไฟล์ชื่อเดียวกันแล้ว ให้ข้าม
                if (
                    filename
                    in existing_files_cache[
                        folder_id
                    ]
                ):
                    skipped += 1
                    continue

                # ดาวน์โหลดจาก Dropbox
                image_bytes = (
                    download_dropbox_file(
                        item["path_lower"]
                    )
                )

                media = MediaIoBaseUpload(
                    io.BytesIO(
                        image_bytes
                    ),
                    mimetype=mime_type,
                    resumable=False
                )

                # อัปโหลด Google Drive
                drive.files().create(
                    body={
                        "name": filename,
                        "parents": [
                            folder_id
                        ]
                    },
                    media_body=media,
                    fields="id"
                ).execute()

                existing_files_cache[
                    folder_id
                ].add(
                    filename
                )

                uploaded += 1

                print(
                    "Uploaded:",
                    game_name,
                    "/",
                    filename
                )

                # แจ้งความคืบหน้าทุก 50 รูป
                if uploaded > 0 and uploaded % 50 == 0:

                    send_message(
                        chat_id,
                        (
                            "⏳ กำลังนำเข้า...\n"
                            f"✅ อัปโหลดแล้ว {uploaded} รูป"
                        )
                    )

            except Exception as file_error:
                failed += 1

                print(
                    "File import error:",
                    item.get(
                        "path_display"
                    ),
                    repr(
                        file_error
                    )
                )

        send_message(
            chat_id,
            (
                "✅ นำเข้า Dropbox → Google Drive เสร็จแล้ว\n\n"
                f"📤 อัปโหลดใหม่: {uploaded} รูป\n"
                f"⏭️ มีอยู่แล้ว: {skipped} รูป\n"
                f"🚫 ไม่ใช่รูป/ข้าม: {ignored} ไฟล์\n"
                f"❌ ล้มเหลว: {failed} ไฟล์"
            )
        )

    except requests.HTTPError as e:

        print(
            "Dropbox HTTP error:",
            repr(e)
        )

        if e.response is not None:
            print(
                "Dropbox response:",
                e.response.text
            )

        send_message(
            chat_id,
            "❌ Dropbox API มีปัญหา กรุณาตรวจสอบ Render Logs"
        )

    except Exception as e:

        print(
            "Import error:",
            repr(e)
        )

        send_message(
            chat_id,
            "❌ Import ไม่สำเร็จ กรุณาตรวจสอบ Render Logs"
        )

    finally:
        IMPORT_LOCK.release()


# ==================================================
# COVER
# ==================================================


def find_cover_images(game_name):
    game_folder = find_game_folder(
        game_name
    )

    # แบบใหม่: รูปอยู่ในโฟลเดอร์เกม
    if game_folder:

        files = list_drive_children(
            game_folder["id"]
        )

        images = [
            file
            for file in files
            if file.get(
                "mimeType",
                ""
            ).startswith(
                "image/"
            )
        ]

        if images:
            return images

    # รองรับโครงสร้างเก่า
    files = get_drive_files()

    game_key = normalize_name(
        game_name
    )

    images = [
        file
        for file in files
        if (
            file.get(
                "mimeType",
                ""
            ).startswith(
                "image/"
            )
            and game_key
            in normalize_name(
                file["name"]
            )
        )
    ]

    return images


# ==================================================
# ROUTES
# ==================================================

@app.route("/")
def home():
    return "Bot is running"


@app.route(
    "/webhook",
    methods=["POST"]
)
def webhook():

    data = (
        request.get_json(
            silent=True
        )
        or {}
    )

    if "message" not in data:
        return "ok"

    message = data["message"]

    chat_id = (
        message["chat"]["id"]
    )

    text = (
        message
        .get(
            "text",
            ""
        )
        .strip()
    )

    # --------------------------
    # START
    # --------------------------

    if text == "/start":

        send_message(
            chat_id,
            (
                "🤖 สวัสดีครับ\n"
                "ระบบพร้อมใช้งานแล้ว\n\n"
                "คำสั่งที่ใช้ได้\n"
                "/drive\n"
                "/cover Mahjong Ways\n"
                "/import"
            )
        )

        return "ok"

    # --------------------------
    # DRIVE
    # --------------------------

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
                    for file
                    in files[:100]
                ]

                msg = (
                    "✅ พบไฟล์/โฟลเดอร์ใน Google Drive:\n\n"
                    + "\n".join(
                        names
                    )
                )

            send_message(
                chat_id,
                msg
            )

        except Exception as e:

            print(
                "Drive error:",
                repr(e)
            )

            send_message(
                chat_id,
                "❌ เชื่อม Google Drive ไม่สำเร็จ กรุณาตรวจสอบ Logs"
            )

        return "ok"

    # --------------------------
    # IMPORT
    # --------------------------

    elif text == "/import":

        send_message(
            chat_id,
            "⏳ รับคำสั่งแล้ว กำลังเริ่ม Import Dropbox..."
        )

        threading.Thread(
            target=import_dropbox_to_drive,
            args=(chat_id,),
            daemon=True
        ).start()

        return "ok"

        elif text == "/dropbox":
        try:
            entries = list_dropbox_root()

            if not entries:
                msg = "📂 Dropbox root ว่าง"
            else:
                names = [
                    f"• {item.get('name', '(ไม่มีชื่อ)')} | {item.get('.tag', '')}"
                    for item in entries
                ]

                msg = (
                    "📂 Dropbox root:\n\n"
                    + "\n".join(names)
                )

            send_message(chat_id, msg)

        except Exception as e:
            print("Dropbox root error:", repr(e))
            send_message(
                chat_id,
                "❌ อ่าน Dropbox root ไม่สำเร็จ กรุณาตรวจสอบ Logs"
            )

        return "ok"

    # --------------------------
    # COVER
    # --------------------------

    elif text.startswith("/cover"):

        game = (
            text.replace(
                "/cover",
                "",
                1
            )
            .strip()
        )

        if not game:

            send_message(
                chat_id,
                "กรุณาระบุชื่อเกม เช่น\n/cover Mahjong Ways"
            )

            return "ok"

        try:

            matched_files = (
                find_cover_images(
                    game
                )
            )

            if not matched_files:

                send_message(
                    chat_id,
                    f"❌ ไม่พบรูปเกม {game} ใน Google Drive"
                )

                return "ok"

            image_file = (
                random.choice(
                    matched_files
                )
            )

            image_bytes = (
                download_drive_file(
                    image_file["id"]
                )
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

            print(
                "Cover error:",
                repr(e)
            )

            send_message(
                chat_id,
                "❌ ดึงรูปจาก Google Drive ไม่สำเร็จ กรุณาตรวจสอบ Logs"
            )

        return "ok"

    # --------------------------
    # OTHER
    # --------------------------

    else:

        send_message(
            chat_id,
            "พิมพ์ /start เพื่อดูคำสั่ง"
        )

        return "ok"


# ==================================================
# SET WEBHOOK
# ==================================================

@app.route("/setup-webhook")
def setup_webhook():

    response = requests.get(
        f"https://api.telegram.org/bot{TOKEN}/setWebhook",
        params={
            "url": WEBHOOK_URL
        },
        timeout=30
    )

    return response.json()


# ==================================================
# START SERVER
# ==================================================

if __name__ == "__main__":

    app.run(
        host="0.0.0.0",
        port=int(
            os.environ.get(
                "PORT",
                10000
            )
        )
    )

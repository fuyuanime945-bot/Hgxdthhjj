import telebot
from telebot import types
import json
import os
import base64
import time
import threading
import re

# ================= CONFIG =================

TOKEN = "8772869279:AAFPJXR3lWQtCqTiP535o1zn3kVLj3n7cE0"

ADMIN_ID = 8758830915

CHANNEL_USERNAME = "@Burmese_Anime"
CHANNEL_LINK = "https://t.me/Burmese_Anime"

DB_FILE = "files.json"
USERS_DB_FILE = "users.json"

bot = telebot.TeleBot(
    TOKEN,
    threaded=True,
    num_threads=8
)

lock = threading.RLock()


# ================= DATABASE =================

def load_json(path, default):
    if not os.path.exists(path):
        return default

    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


files_db = load_json(DB_FILE, {})
users_db = set(load_json(USERS_DB_FILE, []))


def save_json(path, data):
    with lock:
        temp = path + ".tmp"

        with open(temp, "w", encoding="utf-8") as f:
            json.dump(
                data,
                f,
                indent=2,
                ensure_ascii=False
            )

        os.replace(temp, path)


def save_files():
    save_json(DB_FILE, files_db)


def save_users():
    save_json(USERS_DB_FILE, list(users_db))


# ================= HELPERS =================

def normalize(text):
    if not text:
        return ""

    text = str(text).lower()

    text = re.sub(
        r"[\s_\-]+",
        "",
        text
    )

    return text


def clean_filename(name):
    if not name:
        return "Unknown"

    name = str(name)

    name = re.sub(
        r"\.(mp4|mkv|avi|mov|webm|mp3|m4a|flac|zip|rar)$",
        "",
        name,
        flags=re.I
    )

    return name.strip()


def anime_name(name):
    name = clean_filename(name)

    patterns = [
        r"[\s._\-]+s\d{1,2}e\d{1,4}.*$",
        r"[\s._\-]+episode[\s._\-]*\d+.*$",
        r"[\s._\-]+ep[\s._\-]*\d+.*$",
        r"[\s._\-]+\d{1,4}$"
    ]

    for pattern in patterns:
        name = re.sub(
            pattern,
            "",
            name,
            flags=re.I
        )

    return name.strip() or "Unknown Anime"


def encode(text):
    return base64.urlsafe_b64encode(
        text.encode("utf-8")
    ).decode().rstrip("=")


def decode(text):
    try:
        text += "=" * (-len(text) % 4)

        return base64.urlsafe_b64decode(
            text
        ).decode("utf-8")

    except Exception:
        return None


def admin(user_id):
    return user_id == ADMIN_ID


def add_user(user_id):
    if user_id not in users_db:
        users_db.add(user_id)

        try:
            save_users()
        except Exception:
            pass


def send(chat_id, text, **kwargs):
    try:
        return bot.send_message(
            chat_id,
            text,
            **kwargs
        )
    except Exception as e:
        print("Send Error:", e)


# ================= CHANNEL =================

def is_joined(user_id):
    try:
        member = bot.get_chat_member(
            CHANNEL_USERNAME,
            user_id
        )

        return member.status in (
            "creator",
            "administrator",
            "member"
        )

    except Exception as e:
        print("Join Check:", e)
        return False


def join_message(chat_id):

    markup = types.InlineKeyboardMarkup()

    markup.add(
        types.InlineKeyboardButton(
            "📢 Channel Join",
            url=CHANNEL_LINK
        )
    )

    markup.add(
        types.InlineKeyboardButton(
            "✅ Join ပြီးပြီ",
            callback_data="check_join"
        )
    )

    send(
        chat_id,
        """
❌ <b>Channel ကို အရင် Join ပေးပါ</b>

🎬 Anime Bot အသုံးပြုရန်
Burmese Anime Channel ကို Join ထားပေးပါဗျာ။
""",
        reply_markup=markup,
        parse_mode="HTML"
    )


@bot.callback_query_handler(
    func=lambda call: call.data == "check_join"
)
def check_join(call):

    if is_joined(call.from_user.id):

        bot.answer_callback_query(
            call.id,
            "✅ အောင်မြင်ပါတယ်"
        )

        try:
            bot.edit_message_text(
                """
✅ <b>Join အောင်မြင်ပါတယ်</b>

🎬 Bot ကို စတင်အသုံးပြုနိုင်ပါပြီ။

/start ကိုနှိပ်ပါ။
""",
                call.message.chat.id,
                call.message.message_id,
                parse_mode="HTML"
            )
        except Exception:
            pass

    else:

        bot.answer_callback_query(
            call.id,
            "❌ Channel ကို အရင် Join ပေးပါ",
            show_alert=True
        )


# ================= SEARCH =================

def search_files(keyword):

    keyword = normalize(keyword)

    if not keyword:
        return []

    results = []

    with lock:
        for file_id, data in files_db.items():

            name = normalize(
                data.get("name", "")
            )

            anime = normalize(
                data.get("anime", "")
            )

            if (
                keyword in name
                or keyword in anime
            ):
                item = dict(data)
                item["_id"] = file_id
                results.append(item)

    return results


# ================= SEND FILE =================

def send_files(chat_id, results):

    sent = 0
    failed = 0

    for data in results:

        try:

            file_id = data.get("file_id")
            file_type = data.get("type")
            caption = data.get("caption", "")

            if file_type == "document":

                bot.send_document(
                    chat_id,
                    file_id,
                    caption=caption
                )

            elif file_type == "video":

                bot.send_video(
                    chat_id,
                    file_id,
                    caption=caption,
                    supports_streaming=True
                )

            elif file_type == "audio":

                bot.send_audio(
                    chat_id,
                    file_id,
                    caption=caption
                )

            else:
                failed += 1
                continue

            sent += 1

        except Exception as e:
            failed += 1
            print("File Error:", e)

    if failed:
        text = (
            f"✅ <b>{sent}</b> File ပို့ပြီးပါပြီ\n"
            f"❌ <b>{failed}</b> File ပို့မရပါ"
        )
    else:
        text = (
            f"✅ <b>{sent}</b> File ပို့ပြီးပါပြီ"
        )

    send(
        chat_id,
        text,
        parse_mode="HTML"
    )


# ================= START =================

@bot.message_handler(commands=["start"])
def start(message):

    user_id = message.from_user.id

    add_user(user_id)

    if not is_joined(user_id):

        join_message(
            message.chat.id
        )

        return

    args = message.text.split(
        maxsplit=1
    )

    if len(args) > 1:

        keyword = decode(args[1])

        if not keyword:

            return send(
                message.chat.id,
                "❌ Link မမှန်ပါ"
            )

        results = search_files(
            keyword
        )

        if not results:

            return send(
                message.chat.id,
                "❌ Anime မတွေ့ပါ"
            )

        send_files(
            message.chat.id,
            results
        )

        return

    markup = types.InlineKeyboardMarkup()

    markup.add(
        types.InlineKeyboardButton(
            "📢 Burmese Anime",
            url=CHANNEL_LINK
        )
    )

    send(
        message.chat.id,
        """
🎬 <b>Burmese Anime</b>

Anime ကြည့်ရန်
အောက်က <b>ကြည့်ရန်</b> Link ကိုနှိပ်ပါ။

❤️ Burmese Anime
""",
        reply_markup=markup,
        parse_mode="HTML"
    )


# ================= HELP =================

@bot.message_handler(commands=["help"])
def help_command(message):

    add_user(
        message.from_user.id
    )

    send(
        message.chat.id,
        """
ℹ️ <b>အသုံးပြုနည်း</b>

🔗 Anime ရှိတဲ့ <b>ကြည့်ရန်</b> ကိုနှိပ်ပါ။

📢 Channel Join ထားရန် လိုအပ်ပါတယ်။
""",
        parse_mode="HTML"
    )


# ================= ADMIN UPLOAD =================

@bot.message_handler(
    content_types=[
        "document",
        "video",
        "audio"
    ]
)
def upload_file(message):

    if not admin(
        message.from_user.id
    ):
        return

    file_key = str(
        message.message_id
    )

    caption = (
        message.caption or ""
    )

    if message.document:

        file_id = message.document.file_id

        file_name = (
            message.document.file_name
            or "Unknown Anime"
        )

        file_type = "document"

    elif message.video:

        file_id = message.video.file_id

        file_name = (
            message.caption
            or "Anime Video"
        )

        file_type = "video"

    elif message.audio:

        file_id = message.audio.file_id

        file_name = (
            message.audio.file_name
            or message.caption
            or "Anime Audio"
        )

        file_type = "audio"

    else:
        return

    file_name = clean_filename(
        file_name
    )

    title = anime_name(
        file_name
    )

    files_db[file_key] = {
        "file_id": file_id,
        "name": file_name,
        "anime": title,
        "type": file_type,
        "caption": caption,
        "uploaded_at": int(time.time())
    }

    save_files()

    try:
        username = bot.get_me().username
    except Exception:
        username = ""

    encoded = encode(
        normalize(title)
    )

    link = (
        f"https://t.me/{username}"
        f"?start={encoded}"
    )

    markup = types.InlineKeyboardMarkup()

    markup.add(
        types.InlineKeyboardButton(
            "🎬 ကြည့်ရန်",
            url=link
        )
    )

    send(
        message.chat.id,
        f"""
✅ <b>သိမ်းပြီးပါပြီ</b>

🎬 <b>{title}</b>

📄 <code>{file_name}</code>

🆔 <code>{file_key}</code>
""",
        reply_markup=markup,
        parse_mode="HTML"
    )


# ================= ADMIN SEARCH =================

@bot.message_handler(commands=["search"])
def search_command(message):

    if not admin(
        message.from_user.id
    ):
        return

    args = message.text.split(
        maxsplit=1
    )

    if len(args) < 2:

        return send(
            message.chat.id,
            "Usage: /search Anime Name"
        )

    results = search_files(
        args[1]
    )

    if not results:

        return send(
            message.chat.id,
            "❌ File မတွေ့ပါ"
        )

    text = [
        "🔎 <b>Search Results</b>",
        ""
    ]

    for item in results[:30]:

        text.append(
            f"🆔 <code>{item['_id']}</code>"
        )

        text.append(
            f"🎬 {item.get('name', 'Unknown')}"
        )

        text.append("")

    send(
        message.chat.id,
        "\n".join(text),
        parse_mode="HTML"
    )


# ================= ADMIN FILES =================

@bot.message_handler(commands=["files"])
def files_command(message):

    if not admin(
        message.from_user.id
    ):
        return

    if not files_db:

        return send(
            message.chat.id,
            "📂 File မရှိသေးပါ"
        )

    text = [
        "📂 <b>Files</b>",
        ""
    ]

    for file_id, data in list(
        files_db.items()
    )[-30:]:

        text.append(
            f"🆔 <code>{file_id}</code>"
        )

        text.append(
            f"🎬 {data.get('name', 'Unknown')}"
        )

        text.append("")

    send(
        message.chat.id,
        "\n".join(text),
        parse_mode="HTML"
    )


# ================= ADMIN DELETE =================

@bot.message_handler(commands=["delete"])
def delete_file(message):

    if not admin(
        message.from_user.id
    ):
        return

    args = message.text.split()

    if len(args) < 2:

        return send(
            message.chat.id,
            "Usage: /delete ID"
        )

    file_id = args[1]

    if file_id not in files_db:

        return send(
            message.chat.id,
            "❌ File မတွေ့ပါ"
        )

    name = files_db[
        file_id
    ].get(
        "name",
        "Unknown"
    )

    del files_db[file_id]

    save_files()

    send(
        message.chat.id,
        f"""
✅ <b>ဖျက်ပြီးပါပြီ</b>

🎬 {name}
🆔 <code>{file_id}</code>
""",
        parse_mode="HTML"
    )


# ================= DELETE NAME =================

@bot.message_handler(commands=["delname"])
def delete_name(message):

    if not admin(
        message.from_user.id
    ):
        return

    args = message.text.split(
        maxsplit=1
    )

    if len(args) < 2:

        return send(
            message.chat.id,
            "Usage: /delname Anime Name"
        )

    keyword = normalize(
        args[1]
    )

    deleted = 0

    with lock:

        for file_id, data in list(
            files_db.items()
        ):

            name = normalize(
                data.get("name", "")
            )

            anime = normalize(
                data.get("anime", "")
            )

            if (
                keyword in name
                or keyword in anime
            ):

                del files_db[file_id]
                deleted += 1

    save_files()

    send(
        message.chat.id,
        f"✅ <b>{deleted}</b> File ဖျက်ပြီးပါပြီ",
        parse_mode="HTML"
    )


# ================= DELETE ALL =================

@bot.message_handler(commands=["deleteall"])
def delete_all(message):

    if not admin(
        message.from_user.id
    ):
        return

    count = len(files_db)

    files_db.clear()

    save_files()

    send(
        message.chat.id,
        f"🗑 <b>{count}</b> File ဖျက်ပြီးပါပြီ",
        parse_mode="HTML"
    )


# ================= BACKUP =================

@bot.message_handler(commands=["backup"])
def backup(message):

    if not admin(
        message.from_user.id
    ):
        return

    filename = (
        f"backup_{int(time.time())}.json"
    )

    try:

        with open(
            filename,
            "w",
            encoding="utf-8"
        ) as f:

            json.dump(
                files_db,
                f,
                indent=2,
                ensure_ascii=False
            )

        with open(
            filename,
            "rb"
        ) as f:

            bot.send_document(
                message.chat.id,
                f,
                caption="📦 Backup"
            )

    except Exception as e:

        print("Backup Error:", e)

        send(
            message.chat.id,
            "❌ Backup မအောင်မြင်ပါ"
        )

    finally:

        try:
            os.remove(filename)
        except Exception:
            pass


# ================= STATS =================

@bot.message_handler(commands=["stats"])
def stats(message):

    if not admin(
        message.from_user.id
    ):
        return

    documents = sum(
        1 for x in files_db.values()
        if x.get("type") == "document"
    )

    videos = sum(
        1 for x in files_db.values()
        if x.get("type") == "video"
    )

    audios = sum(
        1 for x in files_db.values()
        if x.get("type") == "audio"
    )

    send(
        message.chat.id,
        f"""
📊 <b>BOT STATS</b>

👥 Users: <b>{len(users_db)}</b>

🎬 Total: <b>{len(files_db)}</b>

📄 Documents: <b>{documents}</b>
🎥 Videos: <b>{videos}</b>
🎵 Audios: <b>{audios}</b>
""",
        parse_mode="HTML"
    )


# ================= RELOAD =================

@bot.message_handler(commands=["reload"])
def reload_db(message):

    global files_db
    global users_db

    if not admin(
        message.from_user.id
    ):
        return

    files_db = load_json(
        DB_FILE,
        {}
    )

    users_db = set(
        load_json(
            USERS_DB_FILE,
            []
        )
    )

    send(
        message.chat.id,
        "♻️ Database Reloaded"
    )


# ================= RUN =================

print("🤖 Burmese Anime Bot Running...")

while True:

    try:

        bot.infinity_polling(
            skip_pending=True,
            timeout=60,
            long_polling_timeout=60
        )

    except KeyboardInterrupt:

        break

    except Exception as e:

        print("Bot Error:", e)
        time.sleep(5)
import telebot
from telebot import types
import json
import os
import base64
import time
import threading
import re
import html
from collections import deque

TOKEN = "8772869279:AAG_ZeYuQKmQkc0z4lHVmqQ8vtjt3qlY_iU"

ADMIN_ID = 8758830915

CHANNEL_USERNAME = "@Burmese_Anime"
CHANNEL_LINK = "https://t.me/Burmese_Anime"

DB_FILE = "files.json"
USERS_DB_FILE = "users.json"

bot = telebot.TeleBot(
    TOKEN,
    threaded=True,
    num_threads=16
)

lock = threading.RLock()

BOT_USERNAME = ""

QUEUE_WORKERS = 8
MAX_RETRIES = 3

MIN_SEND_INTERVAL = 0.035
RATE_BACKOFF = 1.3

file_queue = deque()
queue_lock = threading.Lock()
queue_event = threading.Event()

rate_lock = threading.Lock()
next_send_time = 0.0

active_jobs = 0
active_jobs_lock = threading.Lock()


def load_json(path, default):
    if not os.path.exists(path):
        return default

    try:
        with open(
            path,
            "r",
            encoding="utf-8"
        ) as f:
            return json.load(f)

    except Exception as e:
        print(
            "Load Error:",
            e
        )
        return default


def save_json(path, data):
    with lock:

        temp = path + ".tmp"

        try:

            with open(
                temp,
                "w",
                encoding="utf-8"
            ) as f:

                json.dump(
                    data,
                    f,
                    ensure_ascii=False,
                    separators=(",", ":")
                )

            os.replace(
                temp,
                path
            )

            return True

        except Exception as e:

            print(
                "Save Error:",
                e
            )

            try:
                if os.path.exists(temp):
                    os.remove(temp)
            except:
                pass

            return False


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


def save_files():
    return save_json(
        DB_FILE,
        files_db
    )


def save_users():
    return save_json(
        USERS_DB_FILE,
        list(users_db)
    )


def get_bot_username():

    global BOT_USERNAME

    if BOT_USERNAME:
        return BOT_USERNAME

    try:

        BOT_USERNAME = (
            bot.get_me().username
            or ""
        )

    except Exception as e:

        print(
            "Username Error:",
            e
        )

    return BOT_USERNAME


def normalize(text):

    if not text:
        return ""

    return re.sub(
        r"[\s_\-]+",
        "",
        str(text).lower()
    )


def get_episode_sort_key(data):
    """
    Sort anime files by season/episode so files are sent in order:
    S01E01 -> S01E02 -> ... -> S02E01
    Also supports: EP01, Ep 1, Episode 01, E01, and trailing episode numbers.
    """
    name = str(data.get("name", "") or "")
    anime = str(data.get("anime", "") or "")
    value = f"{name} {anime}"

    # Season + Episode: S01E02 / S1 E2 / Season 1 Episode 2
    match = re.search(
        r'(?:s(?:eason)?[\s._-]*(\d+)[\s._-]*e(?:pisode)?[\s._-]*(\d+))',
        value,
        flags=re.I
    )
    if match:
        return (int(match.group(1)), int(match.group(2)), value.lower())

    # Episode only: EP01 / E01 / Episode 01 / Ep 1
    match = re.search(
        r'(?:e(?:p(?:isode)?)?)[\s._-]*(\d+)\b',
        value,
        flags=re.I
    )
    if match:
        return (0, int(match.group(1)), value.lower())

    # "Episode 12" can be missed by the compact pattern above
    match = re.search(
        r'\bepisode[\s._-]*(\d+)\b',
        value,
        flags=re.I
    )
    if match:
        return (0, int(match.group(1)), value.lower())

    # Fallback for filenames ending in a number
    match = re.search(r'(?:^|[\s._-])(\d{1,4})(?:\D*)$', name)
    if match:
        return (0, int(match.group(1)), value.lower())

    # Unknown episode numbers go after numbered episodes
    return (9999, 9999, value.lower())


def sort_episode_files(results):
    """Return files in episode order without changing the database order."""
    return sorted(results, key=get_episode_sort_key)


def clean_filename(name):

    if not name:
        return "Unknown"

    name = str(name).strip()

    name = re.sub(
        r"\.(mp4|mkv|avi|mov|webm|mp3|m4a|flac|zip|rar)$",
        "",
        name,
        flags=re.I
    )

    return (
        name.strip()
        or "Unknown"
    )


def anime_name(name):

    name = clean_filename(name)

    patterns = (
        r"[\s._\-]+s\d{1,2}e\d{1,4}.*$",
        r"[\s._\-]+season[\s._\-]*\d+.*$",
        r"[\s._\-]+episode[\s._\-]*\d+.*$",
        r"[\s._\-]+ep[\s._\-]*\d+.*$",
        r"[\s._\-]+\d{1,4}$"
    )

    for pattern in patterns:

        name = re.sub(
            pattern,
            "",
            name,
            flags=re.I
        )

    return (
        name.strip()
        or "Unknown Anime"
    )


def encode(text):

    return base64.urlsafe_b64encode(
        text.encode("utf-8")
    ).decode().rstrip("=")


def decode(text):

    try:

        text += "=" * (
            -len(text) % 4
        )

        return base64.urlsafe_b64decode(
            text
        ).decode("utf-8")

    except:

        return None


def safe(text):

    return html.escape(
        str(text or "")
    )


def is_admin(user_id):

    return user_id == ADMIN_ID


def add_user(user_id):

    if not user_id:
        return

    with lock:

        if user_id in users_db:
            return

        users_db.add(user_id)

    save_users()


def get_retry_after(error):

    result = getattr(
        error,
        "result_json",
        None
    )

    if not isinstance(
        result,
        dict
    ):
        return None

    parameters = result.get(
        "parameters",
        {}
    )

    if not isinstance(
        parameters,
        dict
    ):
        return None

    retry = parameters.get(
        "retry_after"
    )

    try:

        return max(
            1,
            int(retry)
        )

    except:

        return None


def rate_wait():

    global next_send_time

    with rate_lock:

        now = time.monotonic()

        wait = (
            next_send_time
            - now
        )

        if wait > 0:
            time.sleep(wait)

        next_send_time = (
            time.monotonic()
            + MIN_SEND_INTERVAL
        )


def safe_send_message(
    chat_id,
    text,
    **kwargs
):

    delay = 0.3

    for attempt in range(
        MAX_RETRIES
    ):

        try:

            rate_wait()

            return bot.send_message(
                chat_id,
                text,
                **kwargs
            )

        except Exception as e:

            retry_after = (
                get_retry_after(e)
            )

            if retry_after:

                print(
                    f"429 Message: wait {retry_after}s"
                )

                time.sleep(
                    retry_after
                )

                continue

            print(
                "Message Error:",
                e
            )

            if attempt + 1 < MAX_RETRIES:

                time.sleep(
                    delay
                )

                delay *= RATE_BACKOFF

    return None


def send_file_once(
    chat_id,
    data
):

    file_id = data.get(
        "file_id"
    )

    file_type = data.get(
        "type"
    )

    caption = data.get(
        "caption",
        ""
    )

    if not file_id:
        return False

    delay = 0.3

    for attempt in range(
        MAX_RETRIES
    ):

        try:

            rate_wait()

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

                return False

            return True

        except Exception as e:

            retry_after = (
                get_retry_after(e)
            )

            if retry_after:

                print(
                    f"429 File: wait {retry_after}s"
                )

                time.sleep(
                    retry_after
                )

                continue

            print(
                f"File Error [{attempt + 1}/{MAX_RETRIES}]:",
                e
            )

            if attempt + 1 < MAX_RETRIES:

                time.sleep(
                    delay
                )

                delay *= RATE_BACKOFF

    return False


def file_queue_worker():

    global active_jobs

    while True:

        queue_event.wait()

        while True:

            with queue_lock:

                if not file_queue:

                    queue_event.clear()
                    break

                job = file_queue.popleft()

            chat_id = job["chat_id"]
            data = job["data"]
            job_state = job["state"]

            with active_jobs_lock:
                active_jobs += 1

            success = send_file_once(
                chat_id,
                data
            )

            with active_jobs_lock:
                active_jobs -= 1

            if success:
                job_state["sent"] += 1
            else:
                job_state["failed"] += 1

            job_state["remaining"] -= 1

            if job_state["remaining"] == 0:

                sent = job_state["sent"]
                failed = job_state["failed"]
                total = job_state["total"]

                status_message_id = (
                    job_state.get(
                        "status_message_id"
                    )
                )

                if status_message_id:

                    try:

                        bot.delete_message(
                            chat_id,
                            status_message_id
                        )

                    except Exception as e:

                        print(
                            "Status Delete Error:",
                            e
                        )

                # Final result
                if failed:

                    safe_send_message(
                        chat_id,
                        f"""
📦 <b>ပို့ခြင်းပြီးပါပြီ</b>

🎬 Total: <b>{total}</b>
✅ Sent: <b>{sent}</b>
❌ Failed: <b>{failed}</b>
""",
                        parse_mode="HTML"
                    )

                else:

                    safe_send_message(
                        chat_id,
                        f"""
✅ <b>အားလုံးပို့ပြီးပါပြီ</b>

🎬 Total Files: <b>{total}</b>
📦 Sent: <b>{sent}</b>
""",
                        parse_mode="HTML"
                    )


def start_queue_workers():

    for _ in range(
        QUEUE_WORKERS
    ):

        thread = threading.Thread(
            target=file_queue_worker,
            daemon=True
        )

        thread.start()


def queue_files(chat_id, results):

    if not results:
        return

    state = {
        "remaining": len(results),
        "sent": 0,
        "failed": 0,
        "total": len(results),
        "status_message_id": None
    }

    status = safe_send_message(
        chat_id,
        f"""
🔎 <b>ခဏစောင့်ပါ...</b>

🎬 Anime File <b>{len(results)}</b> ခု ရှာဖွေနေသည်...
📦 File များကို ပြင်ဆင်နေပါသည်
""",
        parse_mode="HTML"
    )

    if status:
        state["status_message_id"] = status.message_id

    with queue_lock:

        for data in results:

            file_queue.append({
                "chat_id": chat_id,
                "data": data,
                "state": state
            })

        queue_event.set()


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

    except:

        return False


def join_message(chat_id):

    markup = types.InlineKeyboardMarkup()

    markup.row(
        types.InlineKeyboardButton(
            "📢 Channel Join",
            url=CHANNEL_LINK
        )
    )

    markup.row(
        types.InlineKeyboardButton(
            "✅ Join ပြီးပြီ",
            callback_data="check_join"
        )
    )

    safe_send_message(
        chat_id,
        """
❌ <b>Channel ကို အရင် Join ပေးပါ</b>

🎬 Anime Bot အသုံးပြုရန်
<b>Burmese Anime</b> Channel ကို Join ထားပေးပါ

Join ပြီးရင် <b>✅ Join ပြီးပြီ</b> ကိုနှိပ်ပါ
""",
        reply_markup=markup,
        parse_mode="HTML"
    )


@bot.callback_query_handler(
    func=lambda call:
        call.data == "check_join"
)
def check_join(call):

    user_id = call.from_user.id

    if is_joined(user_id):

        add_user(user_id)

        try:

            bot.answer_callback_query(
                call.id,
                "✅ Join အောင်မြင်ပါတယ်"
            )

        except:
            pass

        try:

            bot.edit_message_text(
                """
✅ <b>Join အောင်မြင်ပါတယ်</b>

🎬 Bot ကို စတင်အသုံးပြုနိုင်ပါပြီ

/start ကိုနှိပ်ပါ
""",
                call.message.chat.id,
                call.message.message_id,
                parse_mode="HTML"
            )

        except:
            pass

    else:

        try:

            bot.answer_callback_query(
                call.id,
                "❌ Channel ကို အရင် Join ပေးပါ",
                show_alert=True
            )

        except:
            pass


def search_files(keyword):

    keyword = normalize(
        keyword
    )

    if not keyword:
        return []

    results = []

    with lock:

        data_list = list(
            files_db.items()
        )

    for file_id, data in data_list:

        name = normalize(
            data.get(
                "name",
                ""
            )
        )

        anime = normalize(
            data.get(
                "anime",
                ""
            )
        )

        if (
            keyword in name
            or keyword in anime
        ):

            item = data.copy()
            item["_id"] = file_id

            results.append(item)

    # Always return episodes in proper order: 1, 2, 3 ... 10, 11
    return sort_episode_files(results)


@bot.message_handler(
    commands=["start"]
)
def start(message):

    user_id = message.from_user.id
    chat_id = message.chat.id

    add_user(
        user_id
    )

    if not is_joined(
        user_id
    ):

        join_message(
            chat_id
        )

        return

    args = message.text.split(
        maxsplit=1
    )

    if len(args) > 1:

        keyword = decode(
            args[1]
        )

        if not keyword:

            safe_send_message(
                chat_id,
                "❌ Link မမှန်ပါ"
            )

            return

        results = search_files(
            keyword
        )

        if not results:

            safe_send_message(
                chat_id,
                "❌ Anime မတွေ့ပါ"
            )

            return

        queue_files(
            chat_id,
            results
        )

        return

    markup = types.InlineKeyboardMarkup()

    markup.row(
        types.InlineKeyboardButton(
            "📢 Burmese Anime",
            url=CHANNEL_LINK
        )
    )

    safe_send_message(
        chat_id,
        """
🎬 <b>Burmese Anime</b>

Anime ကြည့်ရန်
Channel ထဲက <b>🎬 ကြည့်ရန်</b> Link ကိုနှိပ်ပါ

❤️ Burmese Anime
""",
        reply_markup=markup,
        parse_mode="HTML"
    )


@bot.message_handler(
    commands=["help"]
)
def help_command(message):

    user_id = message.from_user.id

    add_user(
        user_id
    )

    if not is_joined(
        user_id
    ):

        join_message(
            message.chat.id
        )

        return

    safe_send_message(
        message.chat.id,
        """
ℹ️ <b>အသုံးပြုနည်း</b>

🎬 Channel ထဲက
<b>🎬 ကြည့်ရန်</b> Button ကိုနှိပ်ပါ

📢 Channel Join ထားရန် လိုအပ်ပါတယ်
""",
        parse_mode="HTML"
    )


@bot.message_handler(
    content_types=[
        "document",
        "video",
        "audio"
    ]
)
def upload_file(message):

    if not is_admin(
        message.from_user.id
    ):
        return

    file_key = str(
        message.message_id
    )

    caption = (
        message.caption
        or ""
    )

    if message.document:

        file_id = (
            message.document.file_id
        )

        file_name = (
            message.document.file_name
            or "Unknown Anime"
        )

        file_type = "document"

    elif message.video:

        file_id = (
            message.video.file_id
        )

        file_name = (
            message.caption
            or "Anime Video"
        )

        file_type = "video"

    elif message.audio:

        file_id = (
            message.audio.file_id
        )

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

    with lock:

        for old_id, old_data in files_db.items():

            if old_data.get(
                "file_id"
            ) == file_id:

                safe_send_message(
                    message.chat.id,
                    f"""
⚠️ <b>ဒီ File ရှိပြီးသားပါ</b>

🎬 {safe(old_data.get("anime"))}

🆔 <code>{safe(old_id)}</code>
""",
                    parse_mode="HTML"
                )

                return

        files_db[file_key] = {
            "file_id": file_id,
            "name": file_name,
            "anime": title,
            "type": file_type,
            "caption": caption,
            "uploaded_at": int(
                time.time()
            )
        }

    if not save_files():

        safe_send_message(
            message.chat.id,
            "❌ Database Save မအောင်မြင်ပါ"
        )

        return

    username = get_bot_username()

    if not username:

        safe_send_message(
            message.chat.id,
            "❌ Bot Username မရပါ"
        )

        return

    link = (
        f"https://t.me/"
        f"{username}"
        f"?start={encode(normalize(title))}"
    )

    markup = types.InlineKeyboardMarkup()

    markup.row(
        types.InlineKeyboardButton(
            "🎬 ကြည့်ရန်",
            url=link
        )
    )

    safe_send_message(
        message.chat.id,
        f"""
✅ <b>သိမ်းပြီးပါပြီ</b>

🎬 <b>{safe(title)}</b>

📄 <code>{safe(file_name)}</code>

🆔 <code>{file_key}</code>
""",
        reply_markup=markup,
        parse_mode="HTML"
    )


@bot.message_handler(
    commands=["search"]
)
def search_command(message):

    if not is_admin(
        message.from_user.id
    ):
        return

    args = message.text.split(
        maxsplit=1
    )

    if len(args) < 2:

        safe_send_message(
            message.chat.id,
            "Usage: /search Anime Name"
        )

        return

    results = search_files(
        args[1]
    )
    results = sort_episode_files(results)

    if not results:

        safe_send_message(
            message.chat.id,
            "❌ File မတွေ့ပါ"
        )

        return

    text = [
        "🔎 <b>Search Results</b>",
        ""
    ]

    for item in results[:50]:

        text.append(
            f"🆔 <code>{safe(item['_id'])}</code>"
        )

        text.append(
            f"🎬 {safe(item.get('name'))}"
        )

        text.append("")

    safe_send_message(
        message.chat.id,
        "\n".join(text),
        parse_mode="HTML"
    )


@bot.message_handler(
    commands=["files"]
)
def files_command(message):

    if not is_admin(
        message.from_user.id
    ):
        return

    with lock:

        recent = list(
            files_db.items()
        )[-50:]

    if not recent:

        safe_send_message(
            message.chat.id,
            "📂 File မရှိသေးပါ"
        )

        return

    text = [
        "📂 <b>Recent Files</b>",
        ""
    ]

    for file_id, data in recent:

        text.append(
            f"🆔 <code>{safe(file_id)}</code>"
        )

        text.append(
            f"🎬 {safe(data.get('name'))}"
        )

        text.append("")

    safe_send_message(
        message.chat.id,
        "\n".join(text),
        parse_mode="HTML"
    )


@bot.message_handler(
    commands=["delete"]
)
def delete_file(message):

    if not is_admin(
        message.from_user.id
    ):
        return

    args = message.text.split()

    if len(args) < 2:

        safe_send_message(
            message.chat.id,
            "Usage: /delete ID"
        )

        return

    file_id = args[1]

    with lock:

        if file_id not in files_db:

            safe_send_message(
                message.chat.id,
                "❌ File မတွေ့ပါ"
            )

            return

        name = files_db[
            file_id
        ].get(
            "name",
            "Unknown"
        )

        del files_db[
            file_id
        ]

    save_files()

    safe_send_message(
        message.chat.id,
        f"""
✅ <b>ဖျက်ပြီးပါပြီ</b>

🎬 {safe(name)}
🆔 <code>{safe(file_id)}</code>
""",
        parse_mode="HTML"
    )


@bot.message_handler(
    commands=["delname"]
)
def delete_name(message):

    if not is_admin(
        message.from_user.id
    ):
        return

    args = message.text.split(
        maxsplit=1
    )

    if len(args) < 2:

        safe_send_message(
            message.chat.id,
            "Usage: /delname Anime Name"
        )

        return

    keyword = normalize(
        args[1]
    )

    deleted = 0

    with lock:

        for file_id, data in list(
            files_db.items()
        ):

            name = normalize(
                data.get(
                    "name",
                    ""
                )
            )

            anime = normalize(
                data.get(
                    "anime",
                    ""
                )
            )

            if (
                keyword == name
                or keyword == anime
                or keyword in name
                or keyword in anime
            ):

                del files_db[
                    file_id
                ]

                deleted += 1

    save_files()

    safe_send_message(
        message.chat.id,
        f"🗑 <b>{deleted}</b> File ဖျက်ပြီးပါပြီ",
        parse_mode="HTML"
    )


@bot.message_handler(
    commands=["deleteall"]
)
def delete_all(message):

    if not is_admin(
        message.from_user.id
    ):
        return

    with lock:

        count = len(
            files_db
        )

        files_db.clear()

    save_files()

    safe_send_message(
        message.chat.id,
        f"🗑 <b>{count}</b> File ဖျက်ပြီးပါပြီ",
        parse_mode="HTML"
    )


@bot.message_handler(
    commands=["backup"]
)
def backup(message):

    if not is_admin(
        message.from_user.id
    ):
        return

    filename = (
        f"backup_{int(time.time())}.json"
    )

    try:

        with lock:
            data = files_db.copy()

        with open(
            filename,
            "w",
            encoding="utf-8"
        ) as f:

            json.dump(
                data,
                f,
                ensure_ascii=False,
                indent=2
            )

        with open(
            filename,
            "rb"
        ) as f:

            bot.send_document(
                message.chat.id,
                f,
                caption=(
                    f"📦 Backup\n"
                    f"Files: {len(data)}"
                )
            )

    except Exception as e:

        print(
            "Backup Error:",
            e
        )

        safe_send_message(
            message.chat.id,
            "❌ Backup မအောင်မြင်ပါ"
        )

    finally:

        try:
            os.remove(filename)
        except:
            pass


@bot.message_handler(
    commands=["stats"]
)
def stats(message):

    if not is_admin(
        message.from_user.id
    ):
        return

    with lock:

        total = len(
            files_db
        )

        users = len(
            users_db
        )

        documents = sum(
            x.get("type") == "document"
            for x in files_db.values()
        )

        videos = sum(
            x.get("type") == "video"
            for x in files_db.values()
        )

        audios = sum(
            x.get("type") == "audio"
            for x in files_db.values()
        )

    safe_send_message(
        message.chat.id,
        f"""
📊 <b>BOT STATS</b>

👥 Users: <b>{users}</b>
🎬 Total Files: <b>{total}</b>

📄 Documents: <b>{documents}</b>
🎥 Videos: <b>{videos}</b>
🎵 Audios: <b>{audios}</b>
""",
        parse_mode="HTML"
    )


@bot.message_handler(
    commands=["users"]
)
def users_command(message):

    if not is_admin(
        message.from_user.id
    ):
        return

    with lock:
        total = len(
            users_db
        )

    safe_send_message(
        message.chat.id,
        f"👥 Total Users: <b>{total}</b>",
        parse_mode="HTML"
    )


@bot.message_handler(
    commands=["queue"]
)
def queue_status(message):

    if not is_admin(
        message.from_user.id
    ):
        return

    with queue_lock:
        waiting = len(
            file_queue
        )

    with active_jobs_lock:
        active = active_jobs

    safe_send_message(
        message.chat.id,
        f"""
📤 <b>FILE QUEUE</b>

⏳ Waiting: <b>{waiting}</b>
⚡ Active: <b>{active}</b>
🧵 Workers: <b>{QUEUE_WORKERS}</b>
""",
        parse_mode="HTML"
    )


@bot.message_handler(
    commands=["broadcast"]
)
def broadcast_command(message):

    if not is_admin(
        message.from_user.id
    ):
        return

    args = message.text.split(
        maxsplit=1
    )

    if len(args) < 2:

        safe_send_message(
            message.chat.id,
            "Usage: /broadcast Message"
        )

        return

    text = args[1]

    with lock:
        target_users = list(
            users_db
        )

    success = 0
    failed = 0

    for user_id in target_users:

        if safe_send_message(
            user_id,
            text
        ):

            success += 1

        else:

            failed += 1

    safe_send_message(
        message.chat.id,
        f"""
📢 <b>Broadcast Complete</b>

✅ Sent: <b>{success}</b>
❌ Failed: <b>{failed}</b>
""",
        parse_mode="HTML"
    )


@bot.message_handler(
    commands=["reload"]
)
def reload_db(message):

    global files_db
    global users_db

    if not is_admin(
        message.from_user.id
    ):
        return

    new_files = load_json(
        DB_FILE,
        {}
    )

    new_users = set(
        load_json(
            USERS_DB_FILE,
            []
        )
    )

    with lock:

        files_db = new_files
        users_db = new_users

    safe_send_message(
        message.chat.id,
        f"""
♻️ <b>Database Reloaded</b>

🎬 Files: <b>{len(files_db)}</b>
👥 Users: <b>{len(users_db)}</b>
""",
        parse_mode="HTML"
    )


@bot.message_handler(
    commands=["admin"]
)
def admin_help(message):

    if not is_admin(
        message.from_user.id
    ):
        return

    safe_send_message(
        message.chat.id,
        """
🛠 <b>ADMIN</b>

/stats
/users
/queue
/search Anime
/files

/delete ID
/delname Anime
/deleteall

/backup
/broadcast Message
/reload
""",
        parse_mode="HTML"
    )


start_queue_workers()

print(
    "🤖 Burmese Anime Bot V5 Running..."
)

while True:

    try:

        bot.infinity_polling(
            skip_pending=True,
            timeout=20,
            long_polling_timeout=20,
            allowed_updates=[
                "message",
                "callback_query"
            ]
        )

    except KeyboardInterrupt:

        print(
            "🛑 Bot Stopped"
        )

        break

    except Exception as e:

        print(
            "Bot Error:",
            e
        )

        time.sleep(2)

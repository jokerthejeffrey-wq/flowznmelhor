import os
import json
import time
import hashlib
import secrets
import zipfile
from io import BytesIO
from functools import wraps

import requests
from flask import (
    Flask,
    render_template_string,
    request,
    redirect,
    url_for,
    session,
    flash,
    send_file,
    abort,
    jsonify
)
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename


app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", secrets.token_hex(64))

DISCORD_BOT_TOKEN = os.environ.get("DISCORD_BOT_TOKEN", "").strip()
DISCORD_DB_CHANNEL_ID = os.environ.get("DISCORD_DB_CHANNEL_ID", "").strip()
DISCORD_API = "https://discord.com/api/v10"

MAX_FILE_SIZE = int(os.environ.get("MAX_FILE_SIZE", str(8 * 1024 * 1024)))
MAX_DB_SIZE = int(os.environ.get("MAX_DB_SIZE", str(7 * 1024 * 1024)))

POST_COOLDOWN_SECONDS = int(os.environ.get("POST_COOLDOWN_SECONDS", "30"))
COMMENT_COOLDOWN_SECONDS = int(os.environ.get("COMMENT_COOLDOWN_SECONDS", "8"))

app.config["MAX_CONTENT_LENGTH"] = MAX_FILE_SIZE

ALLOWED_EXTENSIONS = {".zip", ".mp3"}
ALLOWED_PFP_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif"}

DANGEROUS_ZIP_EXTENSIONS = {
    ".exe", ".bat", ".cmd", ".scr", ".ps1", ".vbs", ".js", ".jar",
    ".msi", ".dll", ".com", ".pif", ".lnk", ".reg", ".hta", ".apk",
    ".sh", ".command", ".app"
}

CACHE_SECONDS = 2
CACHE = {"time": 0, "store": None}

CREDITS = {
    "OWNERS": ["DJ TUTTER", "DJ LIRA DA ZL"],
    "MEMBERS": [
        "DJ FRG 011",
        "DJ PLT 011",
        "DJ RGLX",
        "DJ RDC",
        "DJ SABA 7",
        "DJ RE7 013",
        "RSFI",
        "DJ RDC"
    ],
    "WEBSITE MADE BY": ["DJ SABA 7"]
}


def now_ms():
    return int(time.time() * 1000)


def blank_db():
    return {
        "version": 6,
        "users": {},
        "topics": {},
        "comments": {},
        "files": {},
        "dms": {},
        "created_at": now_ms(),
        "updated_at": now_ms()
    }


def normalize_db(db):
    if not isinstance(db, dict):
        db = blank_db()

    clean = blank_db()
    clean.update(db)

    for key in ["users", "topics", "comments", "files", "dms"]:
        if not isinstance(clean.get(key), dict):
            clean[key] = {}

    clean["version"] = 6

    if "updated_at" not in clean:
        clean["updated_at"] = now_ms()

    if "created_at" not in clean:
        clean["created_at"] = now_ms()

    return clean


def require_discord_config():
    if not DISCORD_BOT_TOKEN:
        raise RuntimeError("Missing DISCORD_BOT_TOKEN in Render Environment.")

    if not DISCORD_DB_CHANNEL_ID:
        raise RuntimeError("Missing DISCORD_DB_CHANNEL_ID in Render Environment.")


def discord_request(method, endpoint, **kwargs):
    require_discord_config()

    url = endpoint if endpoint.startswith("http") else f"{DISCORD_API}{endpoint}"

    headers = kwargs.pop("headers", {})
    headers["Authorization"] = f"Bot {DISCORD_BOT_TOKEN}"

    for _ in range(5):
        response = requests.request(
            method,
            url,
            headers=headers,
            timeout=45,
            **kwargs
        )

        if response.status_code == 429:
            try:
                retry_after = float(response.json().get("retry_after", 1))
            except Exception:
                retry_after = 1

            time.sleep(retry_after)
            continue

        if not (200 <= response.status_code < 300):
            raise RuntimeError(f"Discord API error {response.status_code}: {response.text[:700]}")

        return response

    raise RuntimeError("Discord API rate-limit retry failed.")


def clear_cache():
    CACHE["time"] = 0
    CACHE["store"] = None


def fetch_discord_messages():
    all_messages = []
    before = None

    for _ in range(60):
        params = {"limit": 100}

        if before:
            params["before"] = before

        response = discord_request(
            "GET",
            f"/channels/{DISCORD_DB_CHANNEL_ID}/messages",
            params=params
        )

        messages = response.json()

        if not messages:
            break

        all_messages.extend(messages)
        before = messages[-1]["id"]

        if len(messages) < 100:
            break

    return all_messages


def post_discord_text(content):
    response = discord_request(
        "POST",
        f"/channels/{DISCORD_DB_CHANNEL_ID}/messages",
        headers={"Content-Type": "application/json"},
        json={"content": content}
    )

    clear_cache()
    return response.json()


def post_discord_attachment(content, filename, file_bytes, content_type):
    payload = {"content": content}

    data = {
        "payload_json": json.dumps(payload)
    }

    files = {
        "files[0]": (
            filename,
            BytesIO(file_bytes),
            content_type or "application/octet-stream"
        )
    }

    response = discord_request(
        "POST",
        f"/channels/{DISCORD_DB_CHANNEL_ID}/messages",
        data=data,
        files=files
    )

    clear_cache()
    return response.json()


def load_store(force=False):
    now = time.time()

    if not force and CACHE["store"] is not None and now - CACHE["time"] < CACHE_SECONDS:
        return CACHE["store"]

    messages = fetch_discord_messages()

    db = blank_db()
    file_urls = {}
    pfp_urls = {}
    snapshot_loaded = False

    for message in messages:
        content = message.get("content", "") or ""
        attachments = message.get("attachments", []) or []

        if content.startswith("SWFILE|"):
            file_id = content.split("|", 1)[1].strip()

            if file_id and file_id not in file_urls and attachments:
                attachment = attachments[0]
                file_urls[file_id] = {
                    "url": attachment.get("url", ""),
                    "filename": attachment.get("filename", ""),
                    "size": attachment.get("size", 0)
                }

        elif content.startswith("SWPFP|"):
            pfp_id = content.split("|", 1)[1].strip()

            if pfp_id and pfp_id not in pfp_urls and attachments:
                attachment = attachments[0]
                pfp_urls[pfp_id] = {
                    "url": attachment.get("url", ""),
                    "filename": attachment.get("filename", ""),
                    "size": attachment.get("size", 0)
                }

        elif content.startswith("SWDBSNAP|") and not snapshot_loaded and attachments:
            try:
                db_url = attachments[0].get("url", "")
                response = requests.get(db_url, timeout=45)
                response.raise_for_status()
                db = normalize_db(response.json())
                snapshot_loaded = True
            except Exception:
                pass

    store = {
        "db": normalize_db(db),
        "file_urls": file_urls,
        "pfp_urls": pfp_urls,
        "message_count": len(messages),
        "snapshot_loaded": snapshot_loaded
    }

    CACHE["time"] = now
    CACHE["store"] = store
    return store


def save_db(db):
    db = normalize_db(db)
    db["updated_at"] = now_ms()

    raw = json.dumps(db, separators=(",", ":"), ensure_ascii=False).encode("utf-8")

    if len(raw) > MAX_DB_SIZE:
        raise ValueError("Discord DB snapshot is too large. Delete old data or raise MAX_DB_SIZE.")

    post_discord_attachment(
        content=f"SWDBSNAP|v6|{int(time.time())}",
        filename="smartweb-db.json",
        file_bytes=raw,
        content_type="application/json"
    )


def save_uploaded_file_to_discord(file_id, filename, file_bytes, content_type):
    post_discord_attachment(
        content=f"SWFILE|{file_id}",
        filename=filename,
        file_bytes=file_bytes,
        content_type=content_type or "application/octet-stream"
    )


def save_profile_picture_to_discord(pfp_id, filename, file_bytes, content_type):
    post_discord_attachment(
        content=f"SWPFP|{pfp_id}",
        filename=filename,
        file_bytes=file_bytes,
        content_type=content_type or "application/octet-stream"
    )


def user_id_from_email(email):
    return hashlib.sha256(email.strip().lower().encode("utf-8")).hexdigest()


def allowed_file(filename):
    return os.path.splitext(filename.lower())[1] in ALLOWED_EXTENSIONS


def allowed_pfp(filename):
    return os.path.splitext(filename.lower())[1] in ALLOWED_PFP_EXTENSIONS


def size_text(size):
    try:
        size = int(size)
    except Exception:
        size = 0

    if size >= 1024 * 1024:
        return f"{size / (1024 * 1024):.1f} MB"

    return f"{size / 1024:.1f} KB"


def cooldown_left(user, key, seconds):
    last = int(user.get(key, 0))
    now = int(time.time())
    left = seconds - (now - last)
    return max(0, left)


def looks_like_mp3(file_bytes):
    if len(file_bytes) < 4:
        return False

    if file_bytes[:3] == b"ID3":
        return True

    if file_bytes[0] == 0xFF and (file_bytes[1] & 0xE0) == 0xE0:
        return True

    return False


def looks_like_image(filename, file_bytes):
    ext = os.path.splitext(filename.lower())[1]

    if ext == ".png":
        return file_bytes.startswith(b"\x89PNG\r\n\x1a\n")

    if ext in [".jpg", ".jpeg"]:
        return file_bytes.startswith(b"\xff\xd8\xff")

    if ext == ".gif":
        return file_bytes.startswith(b"GIF87a") or file_bytes.startswith(b"GIF89a")

    if ext == ".webp":
        return len(file_bytes) > 12 and file_bytes[:4] == b"RIFF" and file_bytes[8:12] == b"WEBP"

    return False


def scan_uploaded_file(filename, file_bytes):
    ext = os.path.splitext(filename.lower())[1]

    if ext == ".mp3":
        if not looks_like_mp3(file_bytes):
            return False, "This does not look like a real MP3 file."

        return True, "MP3 passed basic safety check."

    if ext == ".zip":
        bio = BytesIO(file_bytes)

        if not zipfile.is_zipfile(bio):
            return False, "This does not look like a real ZIP file."

        total_uncompressed = 0
        max_uncompressed = 80 * 1024 * 1024
        max_files = 200

        try:
            with zipfile.ZipFile(BytesIO(file_bytes)) as z:
                infos = z.infolist()

                if len(infos) > max_files:
                    return False, "ZIP has too many files."

                for info in infos:
                    name = info.filename.replace("\\", "/")
                    lower_name = name.lower()

                    if name.startswith("/") or ".." in name.split("/"):
                        return False, "ZIP contains unsafe file paths."

                    inner_ext = os.path.splitext(lower_name)[1]

                    if inner_ext in DANGEROUS_ZIP_EXTENSIONS:
                        return False, f"ZIP contains a blocked dangerous file type: {inner_ext}"

                    total_uncompressed += int(info.file_size)

                    if total_uncompressed > max_uncompressed:
                        return False, "ZIP is too large when extracted."

                    if info.compress_size > 0:
                        ratio = info.file_size / max(info.compress_size, 1)

                        if ratio > 120:
                            return False, "ZIP looks like a zip bomb."

        except Exception:
            return False, "ZIP could not be scanned."

        return True, "ZIP passed basic safety check."

    return False, "Only ZIP and MP3 files are allowed."


def scan_profile_picture(filename, file_bytes):
    if not looks_like_image(filename, file_bytes):
        return False, "This does not look like a real image file."

    return True, "Image passed basic safety check."


def current_email():
    return session.get("email")


def current_user_id():
    email = current_email()

    if not email:
        return None

    return user_id_from_email(email)


def current_user():
    uid = current_user_id()

    if not uid:
        return None

    return load_store()["db"]["users"].get(uid)


def username_from_id(uid, db=None, fallback="unknown"):
    if not uid:
        return fallback

    if db is None:
        db = load_store()["db"]

    user = db["users"].get(uid)

    if not user:
        return fallback

    return user.get("username", fallback)


def pfp_url_from_user(user, store=None):
    if not user:
        return ""

    pfp_id = user.get("pfp_id", "")

    if not pfp_id:
        return ""

    if store is None:
        store = load_store()

    if pfp_id not in store["pfp_urls"]:
        return ""

    return url_for("profile_picture", user_id=user.get("id", ""), v=user.get("pfp_updated", 0))


def valid_username(username):
    username = username.strip()

    if len(username) < 3 or len(username) > 20:
        return False

    allowed = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_."

    return all(c in allowed for c in username)


def login_required(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        if not current_email():
            flash("Please login first.", "error")
            return redirect(url_for("home", view="login"))

        try:
            user = current_user()
        except Exception as e:
            flash(f"Discord database error: {e}", "error")
            return redirect(url_for("home", view="login"))

        if not user:
            session.clear()
            flash("Account not found. Please login again.", "error")
            return redirect(url_for("home", view="login"))

        return func(*args, **kwargs)

    return wrapper


def go(view="dashboard", item_id=None):
    if item_id:
        return redirect(url_for("home", view=view, id=item_id))

    return redirect(url_for("home", view=view))


def public_file(file_data, db=None):
    name = file_data.get("original_name", "file")
    ext = os.path.splitext(name.lower())[1]
    file_id = file_data.get("id", "")

    return {
        "id": file_id,
        "name": name,
        "size": size_text(file_data.get("size", 0)),
        "author": username_from_id(file_data.get("author_id", ""), db, file_data.get("author", "unknown")),
        "author_id": file_data.get("author_id", ""),
        "created": int(file_data.get("created", 0)),
        "is_audio": ext == ".mp3",
        "stream_url": url_for("stream_file", file_id=file_id) if ext == ".mp3" else ""
    }


def public_topic(topic_data, db=None):
    if db is None:
        db = load_store()["db"]

    topic_id = topic_data.get("id", "")
    author_id = topic_data.get("author_id", "")
    viewer_id = current_user_id()

    topic_comments = [
        c for c in db["comments"].values()
        if c.get("topic_id") == topic_id
    ]

    topic_comments.sort(key=lambda c: int(c.get("created", 0)))

    return {
        "id": topic_id,
        "title": topic_data.get("title", ""),
        "body": topic_data.get("body", ""),
        "author": username_from_id(author_id, db, topic_data.get("author", "unknown")),
        "author_id": author_id,
        "created": int(topic_data.get("created", 0)),
        "can_delete": viewer_id == author_id,
        "comments": [
            {
                "id": c.get("id", ""),
                "body": c.get("body", ""),
                "author": username_from_id(c.get("author_id", ""), db, c.get("author", "unknown")),
                "author_id": c.get("author_id", ""),
                "created": int(c.get("created", 0)),
            }
            for c in topic_comments
        ],
    }


def public_user(user, db, store, viewer_id):
    uid = user.get("id", "")
    username = user.get("username", "unknown")

    return {
        "id": uid,
        "username": username,
        "email": user.get("email", "") if uid == viewer_id else "",
        "about": user.get("about", ""),
        "joined": int(user.get("created", 0)),
        "pfp_url": pfp_url_from_user(user, store),
        "topic_count": len([t for t in db["topics"].values() if t.get("author_id") == uid]),
        "comment_count": len([c for c in db["comments"].values() if c.get("author_id") == uid]),
        "file_count": len([f for f in db["files"].values() if f.get("author_id") == uid])
    }


def public_dm_messages(db, current_id):
    messages = []

    for dm in db["dms"].values():
        if dm.get("from") == current_id or dm.get("to") == current_id:
            messages.append({
                "id": dm.get("id", ""),
                "from": dm.get("from", ""),
                "to": dm.get("to", ""),
                "body": dm.get("body", ""),
                "created": int(dm.get("created", 0))
            })

    messages.sort(key=lambda x: x["created"])
    return messages


def public_notifications(db, current_id):
    user = db["users"].get(current_id, {})
    last_seen = int(user.get("last_seen_notifications", 0))
    items = []

    for dm in db["dms"].values():
        created = int(dm.get("created", 0))

        if dm.get("to") == current_id and dm.get("from") != current_id:
            sender = db["users"].get(dm.get("from", ""), {})

            items.append({
                "id": dm.get("id", ""),
                "type": "dm",
                "title": f"New message from {sender.get('username', 'unknown')}",
                "body": dm.get("body", "")[:160],
                "from_id": dm.get("from", ""),
                "target_id": dm.get("from", ""),
                "created": created,
                "unread": created > last_seen
            })

    my_topic_ids = {
        topic_id
        for topic_id, topic in db["topics"].items()
        if topic.get("author_id") == current_id
    }

    for comment in db["comments"].values():
        created = int(comment.get("created", 0))
        commenter_id = comment.get("author_id", "")

        if comment.get("topic_id") in my_topic_ids and commenter_id != current_id:
            commenter = db["users"].get(commenter_id, {})
            topic = db["topics"].get(comment.get("topic_id", ""), {})

            items.append({
                "id": comment.get("id", ""),
                "type": "comment",
                "title": f"{commenter.get('username', 'unknown')} commented on your post",
                "body": topic.get("title", "discussion"),
                "from_id": commenter_id,
                "target_id": comment.get("topic_id", ""),
                "created": created,
                "unread": created > last_seen
            })

    for topic in db["topics"].values():
        created = int(topic.get("created", 0))

        if topic.get("author_id") != current_id:
            author = db["users"].get(topic.get("author_id", ""), {})

            items.append({
                "id": topic.get("id", ""),
                "type": "topic",
                "title": f"{author.get('username', 'unknown')} made a new post",
                "body": topic.get("title", "")[:160],
                "from_id": topic.get("author_id", ""),
                "target_id": topic.get("id", ""),
                "created": created,
                "unread": created > last_seen
            })

    for file_data in db["files"].values():
        created = int(file_data.get("created", 0))

        if file_data.get("author_id") != current_id:
            author = db["users"].get(file_data.get("author_id", ""), {})

            items.append({
                "id": file_data.get("id", ""),
                "type": "file",
                "title": f"{author.get('username', 'unknown')} uploaded a file",
                "body": file_data.get("original_name", "file")[:160],
                "from_id": file_data.get("author_id", ""),
                "target_id": file_data.get("id", ""),
                "created": created,
                "unread": created > last_seen
            })

    items.sort(key=lambda x: x["created"], reverse=True)
    return items[:50]


def build_client_state(db, store, user):
    current_id = user.get("id", "")

    files = [public_file(file_data, db) for file_data in db["files"].values()]
    files.sort(key=lambda x: x["created"], reverse=True)

    topics = [public_topic(topic_data, db) for topic_data in db["topics"].values()]
    topics.sort(key=lambda x: x["created"], reverse=True)

    public_users = [
        public_user(user_data, db, store, current_id)
        for user_data in db["users"].values()
    ]
    public_users.sort(key=lambda x: x["username"].lower())

    notifications = public_notifications(db, current_id)
    unread_count = len([n for n in notifications if n.get("unread")])

    return {
        "files": files,
        "topics": topics,
        "users": public_users,
        "dm_messages": public_dm_messages(db, current_id),
        "notifications": notifications,
        "notification_count": unread_count,
        "db_updated_at": int(db.get("updated_at", 0)),
        "username": user.get("username", user.get("email", "")),
        "pfp_url": pfp_url_from_user(user, store)
    }


HTML = """
<!DOCTYPE html>
<html>
<head>
<title>FlowZNmelhor</title>
<meta name="viewport" content="width=device-width, initial-scale=1">

<style>
*{
    box-sizing:border-box;
    border-radius:0!important;
}

:root{
    --white:#ffffff;
    --text:#f7fbff;
    --muted:rgba(245,250,255,.72);
    --soft:rgba(255,255,255,.52);
    --blue:#a7ecff;
    --blue2:#63d3ff;
    --dark:#06101d;
    --glass:rgba(9,20,31,.32);
    --glass2:rgba(255,255,255,.055);
    --line:rgba(255,255,255,.20);
    --line2:rgba(255,255,255,.10);
    --red:#ff5468;
}

html,body{
    margin:0;
    min-height:100vh;
}

body{
    color:var(--text);
    font-family:Arial,Helvetica,sans-serif;
    background:
        radial-gradient(circle at 15% 10%, rgba(121,219,255,.18), transparent 28%),
        radial-gradient(circle at 82% 82%, rgba(56,92,190,.18), transparent 35%),
        linear-gradient(135deg, #030910 0%, #08131d 42%, #040810 100%);
    overflow:hidden;
}

body::before{
    content:"FLOWZNMELHOR   PRODUCER ROOM   DISCORD DATABASE   PRIVATE FILES   DIRECT MESSAGES   LIVE UPDATES   ";
    position:fixed;
    inset:0;
    white-space:pre-wrap;
    word-spacing:26px;
    letter-spacing:10px;
    line-height:82px;
    font-size:12px;
    font-weight:900;
    color:rgba(255,255,255,.035);
    background-image:
        linear-gradient(rgba(255,255,255,.035) 1px, transparent 1px),
        linear-gradient(90deg, rgba(255,255,255,.035) 1px, transparent 1px);
    background-size:24px 24px;
    pointer-events:none;
    z-index:0;
}

body::after{
    content:"";
    position:fixed;
    width:520px;
    height:520px;
    right:-180px;
    top:-200px;
    background:rgba(167,236,255,.12);
    filter:blur(55px);
    pointer-events:none;
    z-index:0;
}

.app{
    position:relative;
    z-index:1;
    height:100vh;
    display:flex;
    padding:16px;
    gap:16px;
}

.login-only{
    height:100vh;
    display:flex;
    justify-content:center;
    align-items:center;
    padding:20px;
    position:relative;
    z-index:2;
}

.login-shell{
    width:390px;
    background:rgba(7,18,29,.38);
    border:1px solid rgba(255,255,255,.22);
    backdrop-filter:blur(22px) saturate(150%);
    -webkit-backdrop-filter:blur(22px) saturate(150%);
    box-shadow:
        0 30px 90px rgba(0,0,0,.48),
        inset 0 1px 0 rgba(255,255,255,.12);
    padding:32px;
}

.login-logo{
    font-size:12px;
    letter-spacing:3px;
    font-weight:900;
    color:white;
    margin-bottom:28px;
    text-shadow:0 2px 14px rgba(0,0,0,.55);
}

.login-logo::after{
    content:"";
    display:block;
    width:110px;
    height:2px;
    background:var(--blue);
    margin-top:12px;
    box-shadow:0 0 18px rgba(167,236,255,.55);
}

.login-title{
    font-size:34px;
    font-weight:900;
    color:white;
    margin-bottom:8px;
    letter-spacing:-1px;
    text-shadow:0 2px 18px rgba(0,0,0,.65);
}

.login-sub{
    color:rgba(255,255,255,.76);
    font-size:14px;
    line-height:1.6;
    margin-bottom:26px;
    text-shadow:0 1px 10px rgba(0,0,0,.55);
}

.login-input-wrap{
    position:relative;
    margin-bottom:14px;
}

.login-input-wrap input{
    width:100%;
    background:rgba(255,255,255,.045);
    border:1px solid rgba(255,255,255,.18);
    color:white;
    outline:none;
    padding:13px 14px;
    font-size:14px;
    font-weight:700;
    backdrop-filter:blur(16px);
}

.login-input-wrap input::placeholder{
    color:rgba(255,255,255,.56);
    font-weight:500;
}

.btn{
    display:inline-flex;
    align-items:center;
    justify-content:center;
    width:100%;
    height:44px;
    border:1px solid rgba(255,255,255,.22);
    cursor:pointer;
    transition:.16s ease;
    font-weight:900;
    font-size:14px;
    text-decoration:none;
}

.btn-dark{
    background:rgba(7,24,39,.48);
    color:white;
    backdrop-filter:blur(14px);
}

.btn-dark:hover{
    background:rgba(16,43,67,.64);
    border-color:var(--blue);
    transform:translateY(-1px);
    box-shadow:0 0 20px rgba(167,236,255,.12);
}

.btn-white{
    background:rgba(255,255,255,.90);
    color:#06101d;
    border-color:white;
}

.btn-white:hover{
    background:white;
    transform:translateY(-1px);
}

.login-line{
    height:1px;
    background:rgba(255,255,255,.14);
    margin:18px 0 14px;
}

.switch-text{
    color:rgba(255,255,255,.74);
    text-align:center;
    font-size:13px;
    margin-top:18px;
}

.switch-text a{
    color:var(--blue);
    font-weight:900;
    text-decoration:none;
}

.alert-box,.success-box{
    padding:12px 14px;
    margin-bottom:18px;
    font-size:14px;
    backdrop-filter:blur(18px);
}

.alert-box{
    background:rgba(80,0,0,.38);
    color:#ffdede;
    border:1px solid rgba(255,90,90,.40);
    border-left:3px solid #ff5757;
}

.success-box{
    background:rgba(0,70,30,.32);
    color:#d6ffe1;
    border:1px solid rgba(67,232,139,.38);
    border-left:3px solid #43e88b;
}

.side{
    width:260px;
    padding:28px 20px;
    border:1px solid var(--line);
    background:rgba(7,18,29,.36);
    backdrop-filter:blur(24px) saturate(155%);
    -webkit-backdrop-filter:blur(24px) saturate(155%);
    box-shadow:
        0 22px 60px rgba(0,0,0,.38),
        inset 0 1px 0 rgba(255,255,255,.10);
    display:flex;
    flex-direction:column;
}

.title{
    font-size:12px;
    letter-spacing:3px;
    color:white;
    margin-bottom:30px;
    font-weight:900;
    text-shadow:0 2px 12px rgba(0,0,0,.55);
}

.title::after{
    content:"";
    display:block;
    width:110px;
    height:2px;
    background:var(--blue);
    margin-top:12px;
    box-shadow:0 0 18px rgba(167,236,255,.55);
}

.user-mini{
    background:rgba(255,255,255,.055);
    border:1px solid rgba(255,255,255,.15);
    padding:12px;
    margin-bottom:24px;
    display:flex;
    gap:12px;
    align-items:center;
    box-shadow:inset 0 1px 0 rgba(255,255,255,.07);
}

.pfp-box{
    width:46px;
    height:46px;
    background:rgba(255,255,255,.06);
    border:1px solid rgba(255,255,255,.18);
    display:flex;
    align-items:center;
    justify-content:center;
    overflow:hidden;
    flex:0 0 auto;
    color:white;
    font-weight:900;
    font-size:18px;
    text-shadow:0 2px 10px rgba(0,0,0,.45);
}

.pfp-box img{
    width:100%;
    height:100%;
    object-fit:cover;
    display:block;
}

.user-mini-name{
    color:white;
    font-weight:900;
    font-size:14px;
    text-shadow:0 2px 12px rgba(0,0,0,.55);
}

.user-mini-mail{
    color:var(--muted);
    font-size:12px;
    margin-top:4px;
    word-break:break-all;
}

.menu-main{
    flex:1;
}

.menu-bottom{
    border-top:1px solid var(--line2);
    padding-top:18px;
}

.item{
    cursor:pointer;
    user-select:none;
    transition:.16s ease;
    line-height:2.35;
    color:rgba(255,255,255,.72);
    padding:2px 10px;
    margin-bottom:4px;
    font-size:14px;
    letter-spacing:.4px;
    text-transform:uppercase;
    font-weight:900;
    text-shadow:0 2px 12px rgba(0,0,0,.50);
}

.item:hover,.item.active{
    color:white;
    background:rgba(255,255,255,.10);
    transform:translateX(4px);
    box-shadow:inset 2px 0 0 var(--blue);
}

.clicked{
    animation:clickFade .35s ease;
}

@keyframes clickFade{
    0%{opacity:1; transform:scale(1)}
    40%{opacity:.55; transform:scale(.97)}
    100%{opacity:1; transform:scale(1)}
}

.content{
    flex:1;
    padding:42px;
    overflow-y:auto;
    transition:opacity .22s ease,transform .22s ease;
    border:1px solid var(--line);
    background:rgba(7,18,29,.30);
    backdrop-filter:blur(24px) saturate(160%);
    -webkit-backdrop-filter:blur(24px) saturate(160%);
    box-shadow:
        0 22px 65px rgba(0,0,0,.36),
        inset 0 1px 0 rgba(255,255,255,.10);
}

.content.fade{
    opacity:0;
    transform:translateY(8px);
}

.content::-webkit-scrollbar{width:10px}
.content::-webkit-scrollbar-track{background:rgba(255,255,255,.035)}
.content::-webkit-scrollbar-thumb{background:rgba(255,255,255,.22)}

.page-title{
    font-size:34px;
    font-weight:900;
    margin-bottom:10px;
    color:white;
    letter-spacing:-1px;
    text-shadow:0 2px 18px rgba(0,0,0,.65);
}

.page-sub{
    color:var(--muted);
    font-size:14px;
    line-height:1.7;
    margin-bottom:28px;
    text-shadow:0 1px 10px rgba(0,0,0,.55);
}

.grid{
    display:grid;
    grid-template-columns:repeat(3,minmax(0,1fr));
    gap:16px;
    margin-bottom:28px;
}

.card{
    background:rgba(255,255,255,.055);
    border:1px solid rgba(255,255,255,.15);
    padding:18px;
    transition:.16s ease;
    backdrop-filter:blur(18px);
    -webkit-backdrop-filter:blur(18px);
    box-shadow:inset 0 1px 0 rgba(255,255,255,.06);
}

.card:hover{
    background:rgba(255,255,255,.085);
    border-color:rgba(167,236,255,.42);
    transform:translateY(-1px);
}

.card-label{
    color:var(--muted);
    font-size:12px;
    letter-spacing:1.5px;
    text-transform:uppercase;
    margin-bottom:10px;
}

.card-number{
    color:white;
    font-size:30px;
    font-weight:900;
    text-shadow:0 2px 14px rgba(0,0,0,.6);
}

.card-text{
    color:var(--muted);
    font-size:14px;
    line-height:1.6;
    margin-top:8px;
}

.line{
    border-left:1px solid rgba(255,255,255,.22);
    padding-left:24px;
    max-width:980px;
}

input,textarea{
    background:rgba(255,255,255,.045);
    color:white;
    border:1px solid rgba(255,255,255,.18);
    padding:13px 14px;
    outline:none;
    font-size:14px;
    backdrop-filter:blur(16px);
}

input::placeholder,textarea::placeholder{
    color:rgba(255,255,255,.55);
}

input:focus,textarea:focus{
    border-color:var(--blue);
    background:rgba(255,255,255,.065);
}

textarea{
    width:100%;
    min-height:96px;
    resize:vertical;
    margin-top:10px;
}

.search-bar{
    width:330px;
    margin-bottom:26px;
}

.file-row,.topic-row,.credit-row,.comment-row,.user-row,.dm-row{
    margin-bottom:16px;
    padding:16px 18px;
    background:rgba(255,255,255,.052);
    border:1px solid rgba(255,255,255,.15);
    transition:.16s ease;
    backdrop-filter:blur(18px);
    -webkit-backdrop-filter:blur(18px);
    box-shadow:inset 0 1px 0 rgba(255,255,255,.06);
}

.file-row:hover,.topic-row:hover,.credit-row:hover,.comment-row:hover,.user-row:hover,.dm-row:hover{
    background:rgba(255,255,255,.085);
    border-color:rgba(167,236,255,.42);
    transform:translateY(-1px);
}

.file-title,.credit-name,.topic-title{
    font-size:15px;
    color:white;
    margin-bottom:7px;
    font-weight:900;
    text-shadow:0 2px 12px rgba(0,0,0,.55);
}

.meta,.topic-meta,.comment-meta,.small{
    color:var(--muted);
    font-size:14px;
    line-height:1.7;
    text-shadow:0 1px 9px rgba(0,0,0,.50);
}

.body-text{
    white-space:pre-wrap;
}

.file-link,.topic-open,.fake-link,.name-link{
    color:var(--blue);
    text-decoration:none;
    font-size:14px;
    word-break:break-all;
    transition:.16s ease;
    cursor:pointer;
    font-weight:900;
    text-shadow:0 1px 10px rgba(0,0,0,.60);
}

.file-link:hover,.topic-open:hover,.fake-link:hover,.name-link:hover{
    color:white;
    padding-left:5px;
}

.form-box{
    margin-top:32px;
    border-left:1px solid rgba(255,255,255,.22);
    padding-left:24px;
    max-width:740px;
}

button,.file-button{
    display:inline-flex;
    align-items:center;
    justify-content:center;
    gap:10px;
    background:rgba(7,24,39,.46);
    color:white;
    border:1px solid rgba(255,255,255,.20);
    padding:12px 18px;
    cursor:pointer;
    transition:.16s ease;
    font-size:14px;
    text-decoration:none;
    font-weight:900;
    backdrop-filter:blur(16px);
    -webkit-backdrop-filter:blur(16px);
    text-shadow:0 2px 10px rgba(0,0,0,.50);
}

button:hover,.file-button:hover{
    background:rgba(16,43,67,.58);
    border-color:var(--blue);
    transform:translateY(-1px);
    box-shadow:0 0 20px rgba(167,236,255,.10);
}

.primary-btn{
    background:rgba(255,255,255,.90);
    color:#06101d;
    border-color:white;
    text-shadow:none;
}

.primary-btn:hover{
    background:white;
    color:#000;
}

.danger-btn{
    background:rgba(80,0,0,.34);
    border-color:rgba(255,90,90,.42);
    color:#ffdede;
}

.danger-btn:hover{
    background:rgba(120,0,0,.50);
    border-color:#ff5757;
}

.account-box{
    width:430px;
    background:rgba(255,255,255,.052);
    border:1px solid rgba(255,255,255,.15);
    padding:28px;
    box-shadow:
        0 20px 55px rgba(0,0,0,.28),
        inset 0 1px 0 rgba(255,255,255,.07);
    backdrop-filter:blur(20px);
    -webkit-backdrop-filter:blur(20px);
}

.login-card-title{
    font-size:25px;
    color:white;
    margin-bottom:8px;
    font-weight:900;
    letter-spacing:-.5px;
    text-shadow:0 2px 14px rgba(0,0,0,.60);
}

.login-card-sub{
    color:var(--muted);
    font-size:14px;
    margin-bottom:24px;
    word-break:break-all;
}

.login-btn{
    width:100%;
    height:46px;
    margin-bottom:12px;
}

button.login-btn,
a.login-btn{
    display:flex;
    align-items:center;
    justify-content:center;
    background:rgba(7,24,39,.46);
    color:white;
    border:1px solid rgba(255,255,255,.20);
    cursor:pointer;
    transition:.16s ease;
    font-size:14px;
    font-weight:900;
    text-decoration:none;
    backdrop-filter:blur(16px);
    -webkit-backdrop-filter:blur(16px);
    text-shadow:0 2px 10px rgba(0,0,0,.50);
}

button.login-btn:hover,
a.login-btn:hover{
    background:rgba(16,43,67,.58);
    border-color:var(--blue);
    transform:translateY(-1px);
    box-shadow:0 0 20px rgba(167,236,255,.10);
    color:white;
}

button.login-btn.primary-btn{
    background:rgba(255,255,255,.90);
    color:#06101d;
    border-color:white;
    text-shadow:none;
}

button.login-btn.primary-btn:hover{
    background:white;
    color:#000;
}

.login-input{
    width:100%;
    margin-bottom:12px;
}

.selected-file{
    color:var(--muted);
    font-size:14px;
    margin-left:10px;
}

.comment-row{
    border-left:1px solid rgba(167,236,255,.28);
}

.account-section{
    margin-top:28px;
    padding-top:22px;
    border-top:1px solid rgba(255,255,255,.15);
}

.account-pfp,.profile-head{
    display:flex;
    align-items:center;
    gap:14px;
    margin-bottom:22px;
}

.account-pfp .pfp-box,.profile-head .pfp-box{
    width:64px;
    height:64px;
    font-size:24px;
}

.credit-heading{
    color:white;
    font-size:13px;
    letter-spacing:2.4px;
    margin:26px 0 16px;
    font-weight:900;
}

.credit-heading:first-child{
    margin-top:0;
}

.credit-divider{
    border-top:1px solid rgba(255,255,255,.18);
    width:280px;
    margin:24px 0;
}

.dm-message{
    max-width:70%;
    margin-bottom:12px;
    padding:12px 14px;
    border:1px solid rgba(255,255,255,.13);
    background:rgba(255,255,255,.052);
    backdrop-filter:blur(16px);
}

.dm-message.me{
    margin-left:auto;
    background:rgba(167,236,255,.10);
    border-color:rgba(167,236,255,.24);
}

.dm-message.them{
    margin-right:auto;
    background:rgba(255,255,255,.045);
}

.audio-player{
    margin-top:14px;
    padding:10px 0 0 0;
    border:none;
    background:transparent;
    backdrop-filter:none;
    -webkit-backdrop-filter:none;
}

.audio-controls{
    display:flex;
    align-items:center;
    gap:10px;
}

.audio-controls .audio-btn{
    width:auto;
    height:auto;
    min-width:0;
    min-height:0;
    padding:0 2px;
    border:none!important;
    outline:none!important;
    background:transparent!important;
    box-shadow:none!important;
    color:white;
    font-size:16px;
    line-height:1;
    text-shadow:0 0 12px rgba(167,236,255,.60);
    backdrop-filter:none!important;
    -webkit-backdrop-filter:none!important;
}

.audio-controls .audio-btn:hover{
    background:transparent!important;
    border:none!important;
    transform:scale(1.13);
    box-shadow:none!important;
    color:var(--blue);
}

.audio-range{
    flex:1;
    height:3px;
    padding:0;
    cursor:pointer;
    accent-color:var(--blue);
    background:transparent;
    border:none;
}

.audio-time{
    min-width:82px;
    text-align:right;
    color:var(--muted);
    font-size:12px;
    font-weight:900;
}

.live-dot{
    display:inline-block;
    width:7px;
    height:7px;
    background:#43e88b;
    margin-left:8px;
    box-shadow:0 0 12px rgba(67,232,139,.85);
}

@media(max-width:900px){
    body{
        overflow:auto;
    }

    .app{
        height:auto;
        min-height:100vh;
        flex-direction:column;
    }

    .side{
        width:100%;
    }

    .content{
        padding:28px;
    }

    .grid{
        grid-template-columns:1fr;
    }

    .account-box,.search-bar{
        width:100%;
    }
}
</style>
</head>

<body>

{% if not user_email %}

<div class="login-only">
    <div class="login-shell">
        <div class="login-logo">FLOWZNMELHOR</div>

        {% with messages = get_flashed_messages(with_categories=true) %}
            {% if messages %}
                {% set category = messages[0][0] %}
                {% set message = messages[0][1] %}
                {% if category == "success" %}
                    <div class="success-box">{{ message }}</div>
                {% else %}
                    <div class="alert-box">{{ message }}</div>
                {% endif %}
            {% endif %}
        {% endwith %}

        {% if auth_mode == "register" %}
            <div class="login-title">Create account</div>
            <div class="login-sub">Join the producer room. Upload beats, ZIP packs, patterns and discussions.</div>

            <form action="/register" method="POST">
                <div class="login-input-wrap">
                    <input name="username" placeholder="Username" required>
                </div>

                <div class="login-input-wrap">
                    <input name="email" type="email" placeholder="Email" required>
                </div>

                <div class="login-input-wrap">
                    <input name="password" type="password" placeholder="Password" required>
                </div>

                <button class="btn btn-white" type="submit">Create Account</button>
            </form>

            <div class="login-line"></div>
            <button class="btn btn-dark" onclick="location.href='/?view=login'">Back to Login</button>

            <div class="switch-text">
                Already have an account? <a href="/?view=login">Login</a>
            </div>
        {% else %}
            <div class="login-title">Login</div>
            <div class="login-sub">Private producer space for files, discussion, DMs, credits and account settings.</div>

            <form action="/login" method="POST">
                <div class="login-input-wrap">
                    <input name="email" type="email" placeholder="Email" required>
                </div>

                <div class="login-input-wrap">
                    <input name="password" type="password" placeholder="Password" required>
                </div>

                <button class="btn btn-dark" type="submit">Login</button>
            </form>

            <div class="login-line"></div>
            <button class="btn btn-white" onclick="location.href='/?view=register'">Create Account</button>

            <div class="switch-text">
                New producer? <a href="/?view=register">Register</a>
            </div>
        {% endif %}
    </div>
</div>

{% else %}

<div class="app">
    <div class="side">
        <div class="title">FLOWZNMELHOR</div>

        <div class="user-mini" onclick="showAccount(document.getElementById('menuAccount'))" style="cursor:pointer;">
            <div class="pfp-box">
                {% if pfp_url %}
                    <img src="{{ pfp_url }}" alt="pfp">
                {% else %}
                    {{ username[0]|upper }}
                {% endif %}
            </div>
            <div>
                <div class="user-mini-name">{{ username }}</div>
                <div class="user-mini-mail">{{ user_email }}</div>
            </div>
        </div>

        <div class="menu-main">
            <div class="item" id="menuDashboard" onclick="showDashboard(this)">home</div>
            <div class="item" id="menuFiles" onclick="showFiles(this)">files</div>
            <div class="item" id="menuDiscussion" onclick="showDiscussion(this)">discussion</div>
            <div class="item" id="menuDMs" onclick="showDMList(this)">messages</div>
            <div class="item" id="menuUsers" onclick="showUsers(this)">profiles</div>
            <div class="item" id="menuNotifications" onclick="showNotifications(this)">
                notifications <span id="notificationBadge" style="color:#9fe7ff">{{ notification_count }}</span>
            </div>
        </div>

        <div class="menu-bottom">
            <div class="item" id="menuAccount" onclick="showAccount(this)">account</div>
            <div class="item" id="menuCredits" onclick="showCredits(this)">credits</div>
        </div>
    </div>

    <div class="content" id="content">
        {% with messages = get_flashed_messages(with_categories=true) %}
            {% if messages %}
                {% set category = messages[0][0] %}
                {% set message = messages[0][1] %}

                {% if category == "success" %}
                    <div class="success-box">{{ message }}</div>
                {% else %}
                    <div class="alert-box">{{ message }}</div>
                {% endif %}
            {% endif %}
        {% endwith %}

        <div class="page-title">loading</div>
        <div class="small">opening your producer space.</div>
    </div>
</div>

{% endif %}

<script>
let files = {{ files|tojson }};
const credits = {{ credits|tojson }};
let discussions = {{ topics|tojson }};
let users = {{ users|tojson }};
let dmMessages = {{ dm_messages|tojson }};
let notifications = {{ notifications|tojson }};
let notificationCount = {{ notification_count|tojson }};
let dbUpdatedAt = {{ db_updated_at|tojson }};

const userEmail = {{ user_email|tojson }};
const username = {{ username|tojson }};
const currentUserId = {{ current_user_id|tojson }};
const pfpUrl = {{ pfp_url|tojson }};
const startView = {{ start_view|tojson }};
const startTopicId = {{ start_item_id|tojson }};
const maxFileMb = {{ max_file_mb|tojson }};

const POST_COOLDOWN_TEXT = "{{ post_cooldown }} seconds";
const COMMENT_COOLDOWN_TEXT = "{{ comment_cooldown }} seconds";

let currentView = startView || "dashboard";
let currentViewId = startTopicId || "";

function clickEffect(el){
    if(!el) return;
    el.classList.remove("clicked");
    void el.offsetWidth;
    el.classList.add("clicked");
}

document.addEventListener("click", function(e){
    const target = e.target.closest("button, .file-button, .item, .fake-link, .topic-open, .name-link");
    if(target) clickEffect(target);
});

function fadeChange(html){
    const content=document.getElementById("content");
    if(!content) return;

    content.classList.add("fade");

    setTimeout(()=>{
        content.innerHTML=html;
        content.classList.remove("fade");
        bindFileInput();
        bindPfpInput();
        bindAudioPlayers();
    },160);
}

function bindFileInput(){
    const input=document.getElementById("fileInput");
    const name=document.getElementById("fileName");

    if(!input || !name) return;

    input.addEventListener("change",()=>{
        name.textContent=input.files.length ? input.files[0].name : "no file selected";
    });
}

function bindPfpInput(){
    const input=document.getElementById("pfpInput");
    const name=document.getElementById("pfpName");

    if(!input || !name) return;

    input.addEventListener("change",()=>{
        name.textContent=input.files.length ? input.files[0].name : "no profile picture selected";
    });
}

function clearActive(){
    document.querySelectorAll(".item").forEach(i=>i.classList.remove("active"));
}

function setUrl(view, id=null){
    currentView = view;
    currentViewId = id || "";

    if(id){
        window.history.replaceState(null, "", "/?view=" + encodeURIComponent(view) + "&id=" + encodeURIComponent(id));
    }else{
        window.history.replaceState(null, "", "/?view=" + encodeURIComponent(view));
    }
}

function userById(id){
    return users.find(u=>u.id===id);
}

function smallPfp(user){
    if(user && user.pfp_url){
        return `<div class="pfp-box"><img src="${escapeAttr(user.pfp_url)}" alt="pfp"></div>`;
    }

    const letter = user && user.username ? user.username.charAt(0).toUpperCase() : "?";
    return `<div class="pfp-box">${escapeHtml(letter)}</div>`;
}

function pfpHtml(){
    if(pfpUrl){
        return `<div class="pfp-box"><img src="${escapeAttr(pfpUrl)}" alt="pfp"></div>`;
    }

    return `<div class="pfp-box">${escapeHtml(username.charAt(0).toUpperCase())}</div>`;
}

function nameLink(userId, fallback){
    const u = userById(userId);
    const name = u ? u.username : fallback;
    return `<span class="name-link" onclick="openProfile('${escapeAttr(userId)}')">${escapeHtml(name)}</span>`;
}

function formatAudioTime(seconds){
    if(!Number.isFinite(seconds)) return "0:00";

    seconds = Math.floor(seconds);
    const m = Math.floor(seconds / 60);
    const s = seconds % 60;

    return m + ":" + String(s).padStart(2, "0");
}

function audioPlayerHtml(file){
    if(!file.is_audio) return "";

    return `
        <div class="audio-player" data-audio-player>
            <audio preload="metadata" src="${escapeAttr(file.stream_url)}"></audio>

            <div class="audio-controls">
                <button type="button" class="audio-btn" data-play>▶</button>
                <button type="button" class="audio-btn" data-pause>⏸</button>
                <button type="button" class="audio-btn" data-stop>■</button>

                <input class="audio-range" type="range" value="0" min="0" max="100" step="0.1">

                <div class="audio-time" data-time>0:00 / 0:00</div>
            </div>
        </div>
    `;
}

function bindAudioPlayers(){
    document.querySelectorAll("[data-audio-player]").forEach(player=>{
        if(player.dataset.bound === "1") return;

        player.dataset.bound = "1";

        const audio = player.querySelector("audio");
        const playBtn = player.querySelector("[data-play]");
        const pauseBtn = player.querySelector("[data-pause]");
        const stopBtn = player.querySelector("[data-stop]");
        const range = player.querySelector(".audio-range");
        const timeText = player.querySelector("[data-time]");

        function update(){
            const duration = audio.duration || 0;
            const current = audio.currentTime || 0;
            const percent = duration > 0 ? (current / duration) * 100 : 0;

            range.value = percent;
            timeText.textContent = `${formatAudioTime(current)} / ${formatAudioTime(duration)}`;
        }

        playBtn.addEventListener("click", ()=>{
            document.querySelectorAll("audio").forEach(a=>{
                if(a !== audio) a.pause();
            });

            audio.play().catch(()=>{});
        });

        pauseBtn.addEventListener("click", ()=>{
            audio.pause();
        });

        stopBtn.addEventListener("click", ()=>{
            audio.pause();
            audio.currentTime = 0;
            update();
        });

        range.addEventListener("input", ()=>{
            if(audio.duration){
                audio.currentTime = (Number(range.value) / 100) * audio.duration;
            }
        });

        audio.addEventListener("loadedmetadata", update);
        audio.addEventListener("timeupdate", update);
        audio.addEventListener("ended", update);

        update();
    });
}

function showDashboard(button){
    clickEffect(button);
    clearActive();
    if(button) button.classList.add("active");

    setUrl("dashboard");

    const totalComments = discussions.reduce((sum, t)=>sum + t.comments.length, 0);
    const recentTopics = discussions.slice(0,3);
    const recentFiles = files.slice(0,3);

    let html=`
        <div class="page-title">producer room <span class="live-dot"></span></div>
        <div class="page-sub">Upload ZIP packs, share MP3 previews, start discussions, and build a private funk producer space.</div>

        <div class="grid">
            <div class="card">
                <div class="card-label">uploaded files</div>
                <div class="card-number">${files.length}</div>
                <div class="card-text">ZIP packs and MP3 previews shared by members.</div>
            </div>

            <div class="card">
                <div class="card-label">topics</div>
                <div class="card-number">${discussions.length}</div>
                <div class="card-text">Producer questions, beat feedback and ideas.</div>
            </div>

            <div class="card">
                <div class="card-label">messages</div>
                <div class="card-number">${dmMessages.length}</div>
                <div class="card-text">Direct messages between members.</div>
            </div>
        </div>

        <div class="line">
            <div class="topic-title">recent activity</div>
            <br>
    `;

    if(recentTopics.length === 0 && recentFiles.length === 0){
        html += `<div class="small">No activity yet. Upload a file or start the first discussion.</div>`;
    }

    recentFiles.forEach(file=>{
        html += `
            <div class="file-row">
                <div class="file-title">${escapeHtml(file.name)}</div>
                <div class="meta">new file · ${escapeHtml(file.size)} · by ${nameLink(file.author_id, file.author)}</div>
                <a class="file-link" href="/download/${encodeURIComponent(file.id)}" target="_blank">download</a>
                ${audioPlayerHtml(file)}
            </div>
        `;
    });

    recentTopics.forEach(topic=>{
        html += `
            <div class="topic-row">
                <div class="topic-title">${escapeHtml(topic.title)}</div>
                <div class="topic-meta">by ${nameLink(topic.author_id, topic.author)} · ${topic.comments.length} comments</div>
                <div class="topic-open" onclick="openTopic('${topic.id}')">open discussion</div>
            </div>
        `;
    });

    html += `
        </div>

        <div class="form-box">
            <div class="topic-title">quick actions</div>
            <p class="small">Upload a pack, start a discussion, open profiles, or send direct messages.</p>
            <button onclick="showFiles(document.getElementById('menuFiles'))">upload file</button>
            <button onclick="showDiscussion(document.getElementById('menuDiscussion'))">start discussion</button>
        </div>
    `;

    fadeChange(html);
}

function showFiles(button){
    clickEffect(button);
    clearActive();
    if(button) button.classList.add("active");

    setUrl("files");

    let html=`
        <div class="page-title">files</div>
        <div class="page-sub">Upload ZIP packs or MP3 previews. MP3 files can be played directly here.</div>

        <input id="searchInput" class="search-bar" placeholder="search files" oninput="filterFiles()">

        <div class="line">
    `;

    if(files.length===0){
        html+=`<div class="small">no files yet.</div>`;
    }

    files.forEach(file=>{
        html+=`
            <div class="file-row" data-name="${escapeAttr((file.name + ' ' + file.author).toLowerCase())}">
                <div class="file-title">${escapeHtml(file.name)}</div>
                <div class="meta">${escapeHtml(file.size)} · by ${nameLink(file.author_id, file.author)}</div>
                <a class="file-link" href="/download/${encodeURIComponent(file.id)}" target="_blank">download</a>
                ${audioPlayerHtml(file)}
            </div>
        `;
    });

    html+=`</div>`;

    html+=`
        <div class="form-box">
            <form action="/upload" method="POST" enctype="multipart/form-data">
                <input id="fileInput" type="file" name="uploadfile" accept=".zip,.mp3" required hidden>
                <label for="fileInput" class="file-button">select zip or mp3</label>
                <span id="fileName" class="selected-file">no file selected</span>
                <br><br>
                <button class="primary-btn" type="submit">upload file</button>
            </form>
            <p class="small">allowed: zip and mp3 only. maximum size: ${maxFileMb} MB. files are checked with basic no-api safety scan.</p>
        </div>
    `;

    fadeChange(html);
}

function filterFiles(){
    const input=document.getElementById("searchInput");
    if(!input) return;

    const search=input.value.toLowerCase();

    document.querySelectorAll(".file-row").forEach(row=>{
        const name=row.getAttribute("data-name");
        row.style.display=name.includes(search) ? "block" : "none";
    });
}

function showDiscussion(button){
    clickEffect(button);
    clearActive();
    if(button) button.classList.add("active");

    setUrl("discussion");

    let html=`
        <div class="page-title">discussion</div>
        <div class="page-sub">Ask for feedback, share FL Studio tricks, post beat ideas, or start producer challenges.</div>

        <input id="discussionSearchInput" class="search-bar" placeholder="search discussions" oninput="filterDiscussions()">

        <div class="line">
    `;

    if(discussions.length===0){
        html+=`<div class="small">no topics yet.</div>`;
    }

    discussions.forEach(topic=>{
        const searchable=(topic.title + " " + topic.author + " " + topic.body).toLowerCase();

        html+=`
            <div class="topic-row" data-name="${escapeAttr(searchable)}">
                <div class="topic-title">${escapeHtml(topic.title)}</div>
                <div class="topic-meta">by ${nameLink(topic.author_id, topic.author)} · ${topic.comments.length} comments</div>
                <div class="small">${escapeHtml(topic.body).slice(0,140)}${topic.body.length > 140 ? "..." : ""}</div>
                <br>
                <div class="topic-open" onclick="openTopic('${topic.id}')">open discussion</div>
            </div>
        `;
    });

    html+=`</div>`;

    html+=`
        <div class="form-box">
            <form action="/topic" method="POST">
                <input name="title" placeholder="topic title" required style="width:100%;">
                <textarea name="body" placeholder="write topic text" required></textarea>
                <br><br>
                <button class="primary-btn" type="submit">add topic</button>
            </form>
            <p class="small">post cooldown: ${POST_COOLDOWN_TEXT}</p>
        </div>
    `;

    fadeChange(html);
}

function filterDiscussions(){
    const input=document.getElementById("discussionSearchInput");
    if(!input) return;

    const search=input.value.toLowerCase();

    document.querySelectorAll(".topic-row").forEach(row=>{
        const name=row.getAttribute("data-name");
        row.style.display=name.includes(search) ? "block" : "none";
    });
}

function openTopic(topicId){
    const topic=discussions.find(t=>t.id===topicId);
    if(!topic) return;

    setUrl("topic", topicId);

    clearActive();
    const discussionButton=document.getElementById("menuDiscussion");
    if(discussionButton) discussionButton.classList.add("active");

    let html=`
        <div class="page-title">${escapeHtml(topic.title)}</div>
        <div class="page-sub">by ${nameLink(topic.author_id, topic.author)}</div>

        <button onclick="showDiscussion(document.getElementById('menuDiscussion'))">back to discussion</button>
    `;

    if(topic.can_delete){
        html += `
            <form action="/delete-topic/${topic.id}" method="POST" style="display:inline-block;margin-left:12px;" onsubmit="return confirm('Delete this post?')">
                <button class="danger-btn" type="submit">delete post</button>
            </form>
        `;
    }

    html += `
        <br><br>

        <div class="line">
            <div class="small body-text">${escapeHtml(topic.body)}</div>
            <br><br>
            <div class="topic-title">comments</div>
    `;

    if(topic.comments.length===0){
        html+=`<div class="small">no comments yet.</div>`;
    }

    topic.comments.forEach(comment=>{
        html+=`
            <div class="comment-row">
                <div class="comment-meta">${nameLink(comment.author_id, comment.author)}</div>
                <div class="small body-text">${escapeHtml(comment.body)}</div>
            </div>
        `;
    });

    html+=`</div>`;

    html+=`
        <div class="form-box">
            <form action="/comment/${topic.id}" method="POST">
                <textarea name="body" placeholder="write comment" required></textarea>
                <br><br>
                <button class="primary-btn" type="submit">add comment</button>
            </form>
            <p class="small">comment cooldown: ${COMMENT_COOLDOWN_TEXT}</p>
        </div>
    `;

    fadeChange(html);
}

function showUsers(button){
    clickEffect(button);
    clearActive();
    if(button) button.classList.add("active");

    setUrl("profiles");

    let html=`
        <div class="page-title">profiles</div>
        <div class="page-sub">Click a person to view their profile or send a direct message.</div>
        <input id="userSearchInput" class="search-bar" placeholder="search profiles" oninput="filterUsers()">
        <div class="line">
    `;

    users.forEach(u=>{
        html += `
            <div class="user-row" data-name="${escapeAttr(u.username.toLowerCase())}">
                <div style="display:flex;align-items:center;gap:14px;">
                    ${smallPfp(u)}
                    <div>
                        <div class="topic-title">${escapeHtml(u.username)}</div>
                        <div class="meta">${u.topic_count} posts · ${u.file_count} files · ${u.comment_count} comments</div>
                        <div class="topic-open" onclick="openProfile('${u.id}')">view profile</div>
                    </div>
                </div>
            </div>
        `;
    });

    html += `</div>`;
    fadeChange(html);
}

function filterUsers(){
    const input=document.getElementById("userSearchInput");
    if(!input) return;

    const search=input.value.toLowerCase();

    document.querySelectorAll(".user-row").forEach(row=>{
        const name=row.getAttribute("data-name");
        row.style.display=name.includes(search) ? "block" : "none";
    });
}

function openProfile(userId){
    const u = userById(userId);
    if(!u) return;

    setUrl("profile", userId);
    clearActive();

    let actionButtons = `<button onclick="openDm('${u.id}')">direct message</button>`;

    if(u.id === currentUserId){
        actionButtons = `<button onclick="showAccount(document.getElementById('menuAccount'))">edit account</button>`;
    }

    let html=`
        <div class="page-title">profile</div>
        <div class="page-sub">Member profile and activity.</div>

        <div class="line">
            <div class="profile-head">
                ${smallPfp(u)}
                <div>
                    <div class="login-card-title">${escapeHtml(u.username)}</div>
                    <div class="login-card-sub">${u.id === currentUserId ? escapeHtml(u.email) : "member profile"}</div>
                </div>
            </div>

            <div class="grid">
                <div class="card">
                    <div class="card-label">posts</div>
                    <div class="card-number">${u.topic_count}</div>
                </div>
                <div class="card">
                    <div class="card-label">files</div>
                    <div class="card-number">${u.file_count}</div>
                </div>
                <div class="card">
                    <div class="card-label">comments</div>
                    <div class="card-number">${u.comment_count}</div>
                </div>
            </div>

            <div class="topic-title">about</div>
            <div class="small body-text">${escapeHtml(u.about || "No about text yet.")}</div>
            <br>
            ${actionButtons}
            <button onclick="showUsers(document.getElementById('menuUsers'))">all profiles</button>
        </div>
    `;

    fadeChange(html);
}

function showDMList(button){
    clickEffect(button);
    clearActive();
    if(button) button.classList.add("active");

    setUrl("messages");

    const partners = {};

    dmMessages.forEach(m=>{
        const other = m.from === currentUserId ? m.to : m.from;
        if(!partners[other] || m.created > partners[other].created){
            partners[other] = m;
        }
    });

    let html=`
        <div class="page-title">messages</div>
        <div class="page-sub">Direct messages with other members.</div>
        <div class="line">
    `;

    const ids = Object.keys(partners);

    if(ids.length === 0){
        html += `<div class="small">No messages yet. Open a profile and start a direct message.</div>`;
    }

    ids.forEach(id=>{
        const u = userById(id);
        if(!u) return;

        const latest = partners[id];

        html += `
            <div class="dm-row" onclick="openDm('${u.id}')" style="cursor:pointer;">
                <div style="display:flex;align-items:center;gap:14px;">
                    ${smallPfp(u)}
                    <div>
                        <div class="topic-title">${escapeHtml(u.username)}</div>
                        <div class="small">${escapeHtml(latest.body).slice(0,100)}</div>
                    </div>
                </div>
            </div>
        `;
    });

    html += `
        </div>
        <div class="form-box">
            <button onclick="showUsers(document.getElementById('menuUsers'))">find people</button>
        </div>
    `;

    fadeChange(html);
}

function openDm(otherId){
    const u = userById(otherId);
    if(!u) return;

    setUrl("dm", otherId);
    clearActive();

    const dmButton=document.getElementById("menuDMs");
    if(dmButton) dmButton.classList.add("active");

    const convo = dmMessages.filter(m=>{
        return (m.from === currentUserId && m.to === otherId) || (m.from === otherId && m.to === currentUserId);
    });

    let html=`
        <div class="page-title">direct message</div>
        <div class="page-sub">Chatting with ${nameLink(u.id, u.username)}</div>

        <button onclick="showDMList(document.getElementById('menuDMs'))">back to messages</button>
        <button onclick="openProfile('${u.id}')">view profile</button>
        <br><br>

        <div class="line">
    `;

    if(convo.length === 0){
        html += `<div class="small">No messages yet. Send the first one.</div><br>`;
    }

    convo.forEach(m=>{
        const mine = m.from === currentUserId;
        html += `
            <div class="dm-message ${mine ? "me" : "them"}">
                <div class="comment-meta">${mine ? "you" : escapeHtml(u.username)}</div>
                <div class="small body-text">${escapeHtml(m.body)}</div>
            </div>
        `;
    });

    html += `
        </div>

        <div class="form-box">
            <form action="/send-dm/${u.id}" method="POST">
                <textarea name="body" placeholder="write message" required></textarea>
                <br><br>
                <button class="primary-btn" type="submit">send message</button>
            </form>
        </div>
    `;

    fadeChange(html);
}

function showAccount(button){
    clickEffect(button);
    clearActive();
    if(button) button.classList.add("active");

    setUrl("account");

    const me = userById(currentUserId);

    let html=`
        <div class="page-title">account</div>
        <div class="page-sub">Edit your profile picture, username, about text, password or logout.</div>

        <div class="line">
            <div class="account-box">
                <div class="account-pfp">
                    ${pfpHtml()}
                    <div>
                        <div class="login-card-title">${escapeHtml(username)}</div>
                        <div class="login-card-sub">${escapeHtml(userEmail)}</div>
                    </div>
                </div>

                <div class="account-section">
                    <div class="topic-title">change profile picture</div>
                    <br>
                    <form action="/change-pfp" method="POST" enctype="multipart/form-data">
                        <input id="pfpInput" type="file" name="pfp" accept=".png,.jpg,.jpeg,.webp,.gif" required hidden>
                        <label for="pfpInput" class="file-button">select profile picture</label>
                        <span id="pfpName" class="selected-file">no profile picture selected</span>
                        <br><br>
                        <button class="login-btn primary-btn" type="submit">save profile picture</button>
                    </form>
                    <div class="small">allowed: png, jpg, jpeg, webp, gif. maximum size: 3 MB.</div>
                </div>

                <div class="account-section">
                    <div class="topic-title">about me</div>
                    <br>
                    <form action="/change-about" method="POST">
                        <textarea name="about" placeholder="write something about yourself">${escapeHtml(me ? (me.about || "") : "")}</textarea>
                        <br><br>
                        <button class="login-btn primary-btn" type="submit">save about</button>
                    </form>
                </div>

                <div class="account-section">
                    <div class="topic-title">change username</div>
                    <br>
                    <form action="/change-username" method="POST">
                        <input class="login-input" name="new_username" placeholder="new username" required>
                        <button class="login-btn primary-btn" type="submit">save username</button>
                    </form>
                </div>

                <div class="account-section">
                    <div class="topic-title">change password</div>
                    <br>
                    <form action="/change-password" method="POST">
                        <input class="login-input" name="old_password" type="password" placeholder="old password" required>
                        <input class="login-input" name="new_password" type="password" placeholder="new password" required>
                        <button class="login-btn primary-btn" type="submit">save password</button>
                    </form>
                </div>

                <div class="account-section">
                    <a class="login-btn" href="/logout">logout</a>
                </div>
            </div>
        </div>
    `;

    fadeChange(html);
}

function showNotifications(button){
    clickEffect(button);
    clearActive();
    if(button) button.classList.add("active");

    setUrl("notifications");

    let html=`
        <div class="page-title">notifications</div>
        <div class="page-sub">Recent messages, comments, posts and file uploads.</div>
        <div class="line">
    `;

    if(notifications.length === 0){
        html += `<div class="small">No notifications yet.</div>`;
    }

    notifications.forEach(n=>{
        let action = "";

        if(n.type === "dm"){
            action = `<div class="topic-open" onclick="openDm('${n.target_id}')">open message</div>`;
        }else if(n.type === "comment"){
            action = `<div class="topic-open" onclick="openTopic('${n.target_id}')">open discussion</div>`;
        }else if(n.type === "topic"){
            action = `<div class="topic-open" onclick="openTopic('${n.target_id}')">open post</div>`;
        }else if(n.type === "file"){
            action = `<div class="topic-open" onclick="showFiles(document.getElementById('menuFiles'))">open files</div>`;
        }

        html += `
            <div class="topic-row">
                <div class="topic-title">${n.unread ? "● " : ""}${escapeHtml(n.title)}</div>
                <div class="small">${escapeHtml(n.body)}</div>
                <br>
                ${action}
            </div>
        `;
    });

    html += `</div>`;

    fadeChange(html);

    fetch("/notifications-read", {method:"POST"}).then(()=>{
        notificationCount = 0;
        updateNotificationBadge();
        notifications = notifications.map(n => ({...n, unread:false}));
    }).catch(()=>{});
}

function showCredits(button){
    clickEffect(button);
    clearActive();
    if(button) button.classList.add("active");

    setUrl("credits");

    let html=`
        <div class="page-title">credits</div>
        <div class="page-sub">People behind the site.</div>

        <div class="line">
            <div class="credit-heading">OWNERS</div>
    `;

    credits["OWNERS"].forEach(person=>{
        html+=`
            <div if(button) button.classList.add("active");

    setUrl("credits");

    let html=`
        <div class="credit-row">
                <div class="credit-name">${escapeHtml(person)}</div>
            </div>
        `;
    });

    html+=`
        <div class="credit-divider"></div>
        <div class="credit-heading">MEMBERS</div>
    `;

    credits["MEMBERS"].forEach(person=>{
        html+=`
            <div class="credit-row">
                <div class="credit-name">${escapeHtml(person)}</div>
            </div>
        `;
    });

    html+=`
        <div class="credit-divider"></div>
        <div class="credit-heading">WEBSITE MADE BY</div>
    `;

    credits["WEBSITE MADE BY"].forEach(person=>{
        html+=`
            <div class="credit-row">
                <div class="credit-name">${escapeHtml(person)}</div>
            </div>
        `;
    });

    html+=`</div>`;
    fadeChange(html);
}

function updateNotificationBadge(){
    const badge = document.getElementById("notificationBadge");

    if(!badge) return;

    badge.textContent = notificationCount;

    if(notificationCount > 0){
        badge.style.color = "#9fe7ff";
        badge.style.textShadow = "0 0 12px rgba(167,236,255,.8)";
    }else{
        badge.style.color = "#9fe7ff";
        badge.style.textShadow = "none";
    }
}

function isUserTyping(){
    const el = document.activeElement;

    if(!el) return false;

    const tag = el.tagName.toLowerCase();

    return tag === "input" || tag === "textarea";
}

function rerenderCurrentView(){
    if(isUserTyping()) return;

    if(currentView === "files"){
        showFiles(document.getElementById("menuFiles"));
    }else if(currentView === "discussion"){
        showDiscussion(document.getElementById("menuDiscussion"));
    }else if(currentView === "topic" && currentViewId){
        openTopic(currentViewId);
    }else if(currentView === "profile" && currentViewId){
        openProfile(currentViewId);
    }else if(currentView === "dm" && currentViewId){
        openDm(currentViewId);
    }else if(currentView === "messages"){
        showDMList(document.getElementById("menuDMs"));
    }else if(currentView === "profiles"){
        showUsers(document.getElementById("menuUsers"));
    }else if(currentView === "account"){
        showAccount(document.getElementById("menuAccount"));
    }else if(currentView === "credits"){
        showCredits(document.getElementById("menuCredits"));
    }else if(currentView === "notifications"){
        showNotifications(document.getElementById("menuNotifications"));
    }else{
        showDashboard(document.getElementById("menuDashboard"));
    }
}

async function checkForUpdates(){
    if(!userEmail) return;

    try{
        const res = await fetch("/live-state?t=" + Date.now(), {
            cache: "no-store"
        });

        const data = await res.json();

        if(!data.ok){
            return;
        }

        if(data.db_updated_at !== dbUpdatedAt){
            files = data.files;
            discussions = data.topics;
            users = data.users;
            dmMessages = data.dm_messages;
            notifications = data.notifications;
            notificationCount = data.notification_count;
            dbUpdatedAt = data.db_updated_at;

            updateNotificationBadge();
            rerenderCurrentView();
        }

    }catch(e){
        console.log("live update failed", e);
    }
}

setInterval(checkForUpdates, 3000);

function escapeHtml(text){
    return String(text)
        .replaceAll("&","&amp;")
        .replaceAll("<","&lt;")
        .replaceAll(">","&gt;")
        .replaceAll('"',"&quot;")
        .replaceAll("'","&#039;");
}

function escapeAttr(text){
    return escapeHtml(text).replaceAll('"',"&quot;");
}

window.addEventListener("load", ()=>{
    if(!userEmail) return;

    updateNotificationBadge();

    setTimeout(()=>{
        if(startView === "files"){
            showFiles(document.getElementById("menuFiles"));
        }else if(startView === "discussion"){
            showDiscussion(document.getElementById("menuDiscussion"));
        }else if(startView === "topic" && startTopicId){
            openTopic(startTopicId);
        }else if(startView === "profile" && startTopicId){
            openProfile(startTopicId);
        }else if(startView === "dm" && startTopicId){
            openDm(startTopicId);
        }else if(startView === "messages"){
            showDMList(document.getElementById("menuDMs"));
        }else if(startView === "profiles"){
            showUsers(document.getElementById("menuUsers"));
        }else if(startView === "account"){
            showAccount(document.getElementById("menuAccount"));
        }else if(startView === "credits"){
            showCredits(document.getElementById("menuCredits"));
        }else if(startView === "notifications"){
            showNotifications(document.getElementById("menuNotifications"));
        }else{
            showDashboard(document.getElementById("menuDashboard"));
        }
    },100);
});
</script>

</body>
</html>
"""


@app.route("/")
def home():
    logged_in = bool(current_email())
    requested_view = request.args.get("view", "")
    requested_id = request.args.get("id", "")

    if not logged_in:
        auth_mode = "register" if requested_view == "register" else "login"
        requested_view = "login"
        requested_id = ""

        return render_template_string(
            HTML,
            files=[],
            topics=[],
            users=[],
            dm_messages=[],
            notifications=[],
            notification_count=0,
            db_updated_at=0,
            credits=CREDITS,
            user_email=None,
            username="",
            current_user_id="",
            pfp_url="",
            start_view=requested_view,
            start_item_id=requested_id,
            auth_mode=auth_mode,
            max_file_mb=MAX_FILE_SIZE // (1024 * 1024),
            post_cooldown=POST_COOLDOWN_SECONDS,
            comment_cooldown=COMMENT_COOLDOWN_SECONDS
        )

    auth_mode = ""

    if requested_view in ["login", "register", ""]:
        requested_view = "dashboard"

    allowed_views = {
        "dashboard", "files", "discussion", "topic", "account", "credits",
        "notifications", "profile", "profiles", "dm", "messages"
    }

    if requested_view not in allowed_views:
        requested_view = "dashboard"

    try:
        store = load_store()
        db = store["db"]
        user = db["users"].get(current_user_id())
    except Exception as e:
        flash(f"Discord database error: {e}", "error")
        db = blank_db()
        store = {"pfp_urls": {}, "file_urls": {}}
        user = {
            "id": current_user_id(),
            "email": current_email(),
            "username": current_email(),
            "about": ""
        }

    if not user:
        session.clear()
        flash("Account not found. Please login again.", "error")
        return redirect(url_for("home", view="login"))

    state = build_client_state(db, store, user)

    return render_template_string(
        HTML,
        files=state["files"],
        topics=state["topics"],
        users=state["users"],
        dm_messages=state["dm_messages"],
        notifications=state["notifications"],
        notification_count=state["notification_count"],
        db_updated_at=state["db_updated_at"],
        credits=CREDITS,
        user_email=user.get("email", ""),
        username=user.get("username", user.get("email", "")),
        current_user_id=user.get("id", ""),
        pfp_url=pfp_url_from_user(user, store),
        start_view=requested_view,
        start_item_id=requested_id,
        auth_mode=auth_mode,
        max_file_mb=MAX_FILE_SIZE // (1024 * 1024),
        post_cooldown=POST_COOLDOWN_SECONDS,
        comment_cooldown=COMMENT_COOLDOWN_SECONDS
    )


@app.route("/register", methods=["POST"])
def register():
    try:
        store = load_store()
        db = store["db"]
    except Exception as e:
        flash(f"Discord database error: {e}", "error")
        return redirect(url_for("home", view="register"))

    username = request.form.get("username", "").strip()
    email = request.form.get("email", "").strip().lower()
    password = request.form.get("password", "")

    if not valid_username(username):
        flash("Username must be 3-20 characters and only use letters, numbers, underscore, or dot.", "error")
        return redirect(url_for("home", view="register"))

    if "@" not in email or "." not in email:
        flash("Invalid email address.", "error")
        return redirect(url_for("home", view="register"))

    if len(password) < 6:
        flash("Password must be at least 6 characters.", "error")
        return redirect(url_for("home", view="register"))

    user_id = user_id_from_email(email)

    if user_id in db["users"]:
        flash("Account already exists. Please login instead.", "error")
        return redirect(url_for("home", view="login"))

    for existing_user in db["users"].values():
        if existing_user.get("username", "").strip().lower() == username.lower():
            flash("Username already exists. Choose another one.", "error")
            return redirect(url_for("home", view="register"))

    user = {
        "id": user_id,
        "username": username,
        "email": email,
        "password_hash": generate_password_hash(password),
        "pfp_id": "",
        "pfp_updated": 0,
        "about": "",
        "last_seen_notifications": int(time.time()),
        "last_topic_at": 0,
        "last_comment_at": 0,
        "created": int(time.time())
    }

    db["users"][user_id] = user

    try:
        save_db(db)
    except Exception as e:
        flash(f"Could not save user to Discord DB: {e}", "error")
        return redirect(url_for("home", view="register"))

    session["email"] = email

    flash("Account created successfully.", "success")
    return go("dashboard")


@app.route("/login", methods=["POST"])
def login():
    try:
        db = load_store()["db"]
    except Exception as e:
        flash(f"Discord database error: {e}", "error")
        return redirect(url_for("home", view="login"))

    email = request.form.get("email", "").strip().lower()
    password = request.form.get("password", "")

    user = db["users"].get(user_id_from_email(email))

    if not user:
        flash("Account not found. Please create an account first.", "error")
        return redirect(url_for("home", view="login"))

    if not check_password_hash(user.get("password_hash", ""), password):
        flash("Wrong password. Please try again.", "error")
        return redirect(url_for("home", view="login"))

    session["email"] = email

    flash("Logged in successfully.", "success")
    return go("dashboard")


@app.route("/logout", methods=["GET", "POST"])
def logout():
    session.clear()
    response = redirect(url_for("home", view="login"))
    response.set_cookie("session", "", expires=0)
    return response


@app.route("/change-username", methods=["POST"])
@login_required
def change_username():
    store = load_store()
    db = store["db"]
    user = db["users"].get(current_user_id())

    if not user:
        session.clear()
        flash("Account not found.", "error")
        return redirect(url_for("home", view="login"))

    new_username = request.form.get("new_username", "").strip()

    if not valid_username(new_username):
        flash("Username must be 3-20 characters and only use letters, numbers, underscore, or dot.", "error")
        return go("account")

    for existing_user in db["users"].values():
        same_username = existing_user.get("username", "").strip().lower() == new_username.lower()
        different_account = existing_user.get("id") != user.get("id")

        if same_username and different_account:
            flash("Username already exists. Choose another one.", "error")
            return go("account")

    user["username"] = new_username
    user["updated_at"] = int(time.time())
    db["users"][user["id"]] = user

    try:
        save_db(db)
    except Exception as e:
        flash(f"Could not update username: {e}", "error")
        return go("account")

    flash("Username changed successfully.", "success")
    return go("account")


@app.route("/change-about", methods=["POST"])
@login_required
def change_about():
    store = load_store()
    db = store["db"]
    user = db["users"].get(current_user_id())

    if not user:
        session.clear()
        flash("Account not found.", "error")
        return redirect(url_for("home", view="login"))

    about = request.form.get("about", "").strip()

    user["about"] = about[:500]
    user["updated_at"] = int(time.time())
    db["users"][user["id"]] = user

    try:
        save_db(db)
    except Exception as e:
        flash(f"Could not update about text: {e}", "error")
        return go("account")

    flash("About text changed successfully.", "success")
    return go("account")


@app.route("/change-password", methods=["POST"])
@login_required
def change_password():
    store = load_store()
    db = store["db"]
    user = db["users"].get(current_user_id())

    if not user:
        session.clear()
        flash("Account not found.", "error")
        return redirect(url_for("home", view="login"))

    old_password = request.form.get("old_password", "")
    new_password = request.form.get("new_password", "")

    if not check_password_hash(user.get("password_hash", ""), old_password):
        flash("Old password is wrong.", "error")
        return go("account")

    if len(new_password) < 6:
        flash("New password must be at least 6 characters.", "error")
        return go("account")

    user["password_hash"] = generate_password_hash(new_password)
    user["updated_at"] = int(time.time())
    db["users"][user["id"]] = user

    try:
        save_db(db)
    except Exception as e:
        flash(f"Could not update password: {e}", "error")
        return go("account")

    flash("Password changed successfully.", "success")
    return go("account")


@app.route("/change-pfp", methods=["POST"])
@login_required
def change_pfp():
    store = load_store()
    db = store["db"]
    user = db["users"].get(current_user_id())

    if not user:
        session.clear()
        flash("Account not found.", "error")
        return redirect(url_for("home", view="login"))

    if "pfp" not in request.files:
        flash("No profile picture selected.", "error")
        return go("account")

    uploaded = request.files["pfp"]

    if uploaded.filename == "":
        flash("No profile picture selected.", "error")
        return go("account")

    if not allowed_pfp(uploaded.filename):
        flash("Only PNG, JPG, JPEG, WEBP and GIF images are allowed.", "error")
        return go("account")

    original_name = secure_filename(uploaded.filename)
    file_bytes = uploaded.read()
    size = len(file_bytes)

    if size <= 0:
        flash("Empty image is not allowed.", "error")
        return go("account")

    if size > 3 * 1024 * 1024:
        flash("Profile picture is too large. Maximum size is 3 MB.", "error")
        return go("account")

    safe, reason = scan_profile_picture(original_name, file_bytes)

    if not safe:
        flash(f"Profile picture blocked: {reason}", "error")
        return go("account")

    pfp_id = secrets.token_hex(12)

    try:
        save_profile_picture_to_discord(
            pfp_id=pfp_id,
            filename=original_name,
            file_bytes=file_bytes,
            content_type=uploaded.content_type
        )
    except Exception as e:
        flash(f"Could not upload profile picture to Discord: {e}", "error")
        return go("account")

    user["pfp_id"] = pfp_id
    user["pfp_name"] = original_name
    user["pfp_updated"] = int(time.time())
    db["users"][user["id"]] = user

    try:
        save_db(db)
    except Exception as e:
        flash(f"Profile picture uploaded, but DB update failed: {e}", "error")
        return go("account")

    flash("Profile picture changed successfully.", "success")
    return go("account")


@app.route("/upload", methods=["POST"])
@login_required
def upload():
    store = load_store()
    db = store["db"]
    user = db["users"].get(current_user_id())

    if not user:
        session.clear()
        flash("Account not found.", "error")
        return redirect(url_for("home", view="login"))

    if "uploadfile" not in request.files:
        flash("No file selected.", "error")
        return go("files")

    uploaded = request.files["uploadfile"]

    if uploaded.filename == "":
        flash("No file selected.", "error")
        return go("files")

    if not allowed_file(uploaded.filename):
        flash("Only ZIP and MP3 files are allowed.", "error")
        return go("files")

    original_name = secure_filename(uploaded.filename)
    file_bytes = uploaded.read()
    size = len(file_bytes)

    if size <= 0:
        flash("Empty file is not allowed.", "error")
        return go("files")

    if size > MAX_FILE_SIZE:
        flash(f"File too large. Max size is {MAX_FILE_SIZE // (1024 * 1024)} MB.", "error")
        return go("files")

    safe, reason = scan_uploaded_file(original_name, file_bytes)

    if not safe:
        flash(f"Upload blocked: {reason}", "error")
        return go("files")

    file_id = secrets.token_hex(12)

    try:
        save_uploaded_file_to_discord(
            file_id=file_id,
            filename=original_name,
            file_bytes=file_bytes,
            content_type=uploaded.content_type
        )
    except Exception as e:
        flash(f"Could not upload file to Discord: {e}", "error")
        return go("files")

    metadata = {
        "id": file_id,
        "original_name": original_name,
        "size": size,
        "content_type": uploaded.content_type or "application/octet-stream",
        "author": user.get("username"),
        "author_id": user.get("id"),
        "created": int(time.time())
    }

    db["files"][file_id] = metadata

    try:
        save_db(db)
    except Exception as e:
        flash(f"File uploaded, but DB update failed: {e}", "error")
        return go("files")

    flash("File uploaded successfully. Basic safety check passed.", "success")
    return go("files")


@app.route("/download/<file_id>")
@login_required
def download(file_id):
    try:
        store = load_store(force=True)
        db = store["db"]
        file_urls = store["file_urls"]
    except Exception as e:
        flash(f"Discord database error: {e}", "error")
        return go("files")

    file_data = db["files"].get(file_id)

    if not file_data:
        flash("File not found.", "error")
        return go("files")

    file_url = file_urls.get(file_id, {}).get("url")

    if not file_url:
        flash("Discord file attachment not found.", "error")
        return go("files")

    try:
        response = requests.get(file_url, timeout=60)
        response.raise_for_status()
    except Exception as e:
        flash(f"Could not download Discord attachment: {e}", "error")
        return go("files")

    return send_file(
        BytesIO(response.content),
        as_attachment=True,
        download_name=file_data.get("original_name", "download"),
        mimetype=file_data.get("content_type", "application/octet-stream")
    )


@app.route("/stream/<file_id>")
@login_required
def stream_file(file_id):
    try:
        store = load_store(force=True)
        db = store["db"]
        file_urls = store["file_urls"]
    except Exception:
        abort(404)

    file_data = db["files"].get(file_id)

    if not file_data:
        abort(404)

    if not file_data.get("original_name", "").lower().endswith(".mp3"):
        abort(404)

    file_url = file_urls.get(file_id, {}).get("url")

    if not file_url:
        abort(404)

    try:
        response = requests.get(file_url, timeout=60)
        response.raise_for_status()
    except Exception:
        abort(404)

    return send_file(
        BytesIO(response.content),
        mimetype="audio/mpeg",
        as_attachment=False,
        download_name=file_data.get("original_name", "audio.mp3")
    )


@app.route("/pfp/<user_id>")
@login_required
def profile_picture(user_id):
    try:
        store = load_store(force=True)
        db = store["db"]
        pfp_urls = store["pfp_urls"]
    except Exception:
        abort(404)

    user = db["users"].get(user_id)

    if not user:
        abort(404)

    pfp_id = user.get("pfp_id", "")

    if not pfp_id:
        abort(404)

    pfp_url = pfp_urls.get(pfp_id, {}).get("url")

    if not pfp_url:
        abort(404)

    try:
        response = requests.get(pfp_url, timeout=60)
        response.raise_for_status()
    except Exception:
        abort(404)

    content_type = response.headers.get("Content-Type", "image/png")

    return send_file(BytesIO(response.content), mimetype=content_type)


@app.route("/topic", methods=["POST"])
@login_required
def add_topic():
    store = load_store()
    db = store["db"]
    user = db["users"].get(current_user_id())

    if not user:
        session.clear()
        flash("Account not found.", "error")
        return redirect(url_for("home", view="login"))

    left = cooldown_left(user, "last_topic_at", POST_COOLDOWN_SECONDS)

    if left > 0:
        flash(f"Slow down. You can make another post in {left} seconds.", "error")
        return go("discussion")

    title = request.form.get("title", "").strip()
    body = request.form.get("body", "").strip()

    if not title or not body:
        flash("Topic title and text are required.", "error")
        return go("discussion")

    topic_id = secrets.token_hex(12)

    topic = {
        "id": topic_id,
        "title": title[:120],
        "body": body[:1200],
        "author": user.get("username"),
        "author_id": user.get("id"),
        "created": int(time.time())
    }

    db["topics"][topic_id] = topic
    user["last_topic_at"] = int(time.time())
    db["users"][user["id"]] = user

    try:
        save_db(db)
    except Exception as e:
        flash(f"Could not save topic to Discord DB: {e}", "error")
        return go("discussion")

    flash("Topic added.", "success")
    return go("topic", topic_id)


@app.route("/comment/<topic_id>", methods=["POST"])
@login_required
def add_comment(topic_id):
    store = load_store()
    db = store["db"]
    user = db["users"].get(current_user_id())

    if not user:
        session.clear()
        flash("Account not found.", "error")
        return redirect(url_for("home", view="login"))

    if topic_id not in db["topics"]:
        flash("Topic not found.", "error")
        return go("discussion")

    left = cooldown_left(user, "last_comment_at", COMMENT_COOLDOWN_SECONDS)

    if left > 0:
        flash(f"Slow down. You can comment again in {left} seconds.", "error")
        return go("topic", topic_id)

    body = request.form.get("body", "").strip()

    if not body:
        flash("Comment cannot be empty.", "error")
        return go("topic", topic_id)

    comment_id = secrets.token_hex(12)

    comment = {
        "id": comment_id,
        "topic_id": topic_id,
        "body": body[:900],
        "author": user.get("username"),
        "author_id": user.get("id"),
        "created": int(time.time())
    }

    db["comments"][comment_id] = comment
    user["last_comment_at"] = int(time.time())
    db["users"][user["id"]] = user

    try:
        save_db(db)
    except Exception as e:
        flash(f"Could not save comment to Discord DB: {e}", "error")
        return go("topic", topic_id)

    flash("Comment added.", "success")
    return go("topic", topic_id)


@app.route("/delete-topic/<topic_id>", methods=["POST"])
@login_required
def delete_topic_route(topic_id):
    store = load_store()
    db = store["db"]
    user = db["users"].get(current_user_id())
    topic = db["topics"].get(topic_id)

    if not user:
        session.clear()
        flash("Account not found.", "error")
        return redirect(url_for("home", view="login"))

    if not topic:
        flash("Topic not found.", "error")
        return go("discussion")

    if topic.get("author_id") != user.get("id"):
        flash("You can only delete your own posts.", "error")
        return go("topic", topic_id)

    db["topics"].pop(topic_id, None)

    for comment_id in list(db["comments"].keys()):
        if db["comments"][comment_id].get("topic_id") == topic_id:
            db["comments"].pop(comment_id, None)

    try:
        save_db(db)
    except Exception as e:
        flash(f"Could not delete topic: {e}", "error")
        return go("topic", topic_id)

    flash("Post deleted.", "success")
    return go("discussion")


@app.route("/send-dm/<target_id>", methods=["POST"])
@login_required
def send_dm(target_id):
    store = load_store()
    db = store["db"]

    sender = db["users"].get(current_user_id())
    target = db["users"].get(target_id)

    if not sender:
        session.clear()
        flash("Account not found.", "error")
        return redirect(url_for("home", view="login"))

    if not target:
        flash("User not found.", "error")
        return go("messages")

    if sender.get("id") == target.get("id"):
        flash("You cannot message yourself.", "error")
        return go("profile", target_id)

    body = request.form.get("body", "").strip()

    if not body:
        flash("Message cannot be empty.", "error")
        return go("dm", target_id)

    dm_id = secrets.token_hex(12)

    db["dms"][dm_id] = {
        "id": dm_id,
        "from": sender.get("id"),
        "to": target.get("id"),
        "body": body[:1000],
        "created": int(time.time()),
        "read": False
    }

    try:
        save_db(db)
    except Exception as e:
        flash(f"Could not send message: {e}", "error")
        return go("dm", target_id)

    flash("Message sent.", "success")
    return go("dm", target_id)


@app.route("/live-state")
@login_required
def live_state():
    try:
        store = load_store(force=True)
        db = store["db"]
        user = db["users"].get(current_user_id())

        if not user:
            return jsonify({"ok": False, "error": "Account not found"}), 401

        state = build_client_state(db, store, user)
        state["ok"] = True
        return jsonify(state)

    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/notifications-read", methods=["POST"])
@login_required
def notifications_read():
    try:
        store = load_store(force=True)
        db = store["db"]
        user = db["users"].get(current_user_id())

        if not user:
            return jsonify({"ok": False, "error": "Account not found"}), 401

        user["last_seen_notifications"] = int(time.time())
        user["updated_at"] = int(time.time())
        db["users"][user["id"]] = user

        save_db(db)

        return jsonify({"ok": True})

    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/discord-test")
def discord_test():
    try:
        store = load_store(force=True)
        db = store["db"]

        post_discord_text(f"SWTEST|website connected|{int(time.time())}")

        return (
            "DISCORD DATABASE WORKS<br>"
            f"Messages scanned: {store['message_count']}<br>"
            f"Snapshot loaded: {store['snapshot_loaded']}<br>"
            f"Users: {len(db['users'])}<br>"
            f"Topics: {len(db['topics'])}<br>"
            f"Comments: {len(db['comments'])}<br>"
            f"Files: {len(db['files'])}<br>"
            f"DMs: {len(db['dms'])}<br>"
            "Test message sent."
        )

    except Exception as e:
        return f"DISCORD DATABASE ERROR: {e}"


@app.errorhandler(413)
def too_large(error):
    flash(f"File too large. Max size is {MAX_FILE_SIZE // (1024 * 1024)} MB.", "error")
    return go("files")


@app.errorhandler(404)
def not_found(error):
    return redirect(url_for("home"))


if __name__ == "__main__":
    app.run(debug=True)

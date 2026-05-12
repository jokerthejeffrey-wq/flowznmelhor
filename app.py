import os
import re
import json
import time
import hashlib
import secrets
import zipfile
from io import BytesIO
from functools import wraps

import requests
import dns.resolver
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
)
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename


app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", secrets.token_hex(64))

DISCORD_BOT_TOKEN = os.environ.get("DISCORD_BOT_TOKEN", "").strip()
DISCORD_DB_CHANNEL_ID = os.environ.get("DISCORD_DB_CHANNEL_ID", "").strip()
DISCORD_API = "https://discord.com/api/v10"

MAX_FILE_SIZE = int(os.environ.get("MAX_FILE_SIZE", str(8 * 1024 * 1024 * 1024)))
MAX_DB_SIZE = int(os.environ.get("MAX_DB_SIZE", str(7 * 1024 * 1024)))

POST_COOLDOWN_SECONDS = int(os.environ.get("POST_COOLDOWN_SECONDS", "25"))
COMMENT_COOLDOWN_SECONDS = int(os.environ.get("COMMENT_COOLDOWN_SECONDS", "6"))

app.config["MAX_CONTENT_LENGTH"] = MAX_FILE_SIZE

AUDIO_EXTENSIONS = {".mp3", ".wav", ".ogg", ".m4a", ".aac", ".flac"}
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
VIDEO_EXTENSIONS = {".mp4", ".mov"}

FL_STUDIO_EXTENSIONS = {
    ".flp",
    ".fst",
    ".mid",
    ".midi",
    ".sf2",
    ".sfz",
    ".dwp",
    ".fxp",
    ".fxb",
    ".nmsv",
}

MOBILE_FRIENDLY_EXTENSIONS = {
    ".txt",
    ".pdf",
}

ALLOWED_EXTENSIONS = (
    {".zip"}
    | AUDIO_EXTENSIONS
    | IMAGE_EXTENSIONS
    | VIDEO_EXTENSIONS
    | FL_STUDIO_EXTENSIONS
    | MOBILE_FRIENDLY_EXTENSIONS
)

ALLOWED_PFP_EXTENSIONS = IMAGE_EXTENSIONS

DANGEROUS_ZIP_EXTENSIONS = {
    ".exe",
    ".bat",
    ".cmd",
    ".scr",
    ".ps1",
    ".vbs",
    ".js",
    ".jar",
    ".msi",
    ".dll",
    ".com",
    ".pif",
    ".lnk",
    ".reg",
    ".hta",
    ".apk",
    ".sh",
    ".command",
    ".app",
    ".vb",
    ".wsf",
    ".cpl",
}

BLOCKED_RAW_HEADERS = [
    b"MZ",
    b"\x7fELF",
    b"#!/bin/sh",
    b"#!/bin/bash",
    b"<script",
    b"<!doctype html",
    b"<html",
]

MIME_TYPES = {
    ".mp3": "audio/mpeg",
    ".wav": "audio/wav",
    ".ogg": "audio/ogg",
    ".m4a": "audio/mp4",
    ".aac": "audio/aac",
    ".flac": "audio/flac",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".mp4": "video/mp4",
    ".mov": "video/quicktime",
    ".pdf": "application/pdf",
    ".txt": "text/plain",
    ".zip": "application/zip",
}

EMAIL_REGEX = re.compile(
    r"^(?=.{6,254}$)(?=.{1,64}@)[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@"
    r"[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+$"
)

BLOCKED_EMAIL_DOMAINS = {
    "example.com",
    "example.org",
    "example.net",
    "test.com",
    "fake.com",
    "mailinator.com",
    "10minutemail.com",
    "10minutemail.net",
    "10minutemail.org",
    "guerrillamail.com",
    "guerrillamail.net",
    "guerrillamail.org",
    "temp-mail.org",
    "tempmail.com",
    "tempmail.net",
    "tempmailo.com",
    "throwawaymail.com",
    "yopmail.com",
    "sharklasers.com",
    "getnada.com",
    "trashmail.com",
    "dispostable.com",
    "maildrop.cc",
    "moakt.com",
    "emailondeck.com",
    "mintemail.com",
    "mytemp.email",
    "tempail.com",
    "fakeinbox.com",
    "spamgourmet.com",
    "burnermail.io",
    "mail.tm",
    "inboxkitten.com",
    "tempmail.plus",
    "tmail.io",
    "fakemail.net",
    "fakemailgenerator.com",
    "dropmail.me",
    "33mail.com",
    "mvrht.com",
    "mailnesia.com",
    "mailcatch.com",
    "mailforspam.com",
    "spambog.com",
    "trash-mail.com",
    "tempm.com",
    "temporary-mail.net",
    "tempmailaddress.com",
    "mohmal.com",
    "emailfake.com",
    "fexpost.com",
    "fexbox.org",
    "fextemp.com",
    "tmpmail.org",
    "tmpmail.net",
    "minuteinbox.com",
    "emailtemporanea.com",
    "tempinbox.com",
    "instant-email.org",
    "spam4.me",
    "inboxbear.com",
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
        "DJ RDC",
    ],
    "WEBSITE MADE BY": ["DJ SABA 7"],
}


def now_ms():
    return int(time.time() * 1000)


def blank_db():
    return {
        "version": 11,
        "users": {},
        "topics": {},
        "comments": {},
        "files": {},
        "dms": {},
        "created_at": now_ms(),
        "updated_at": now_ms(),
    }


def normalize_db(db):
    if not isinstance(db, dict):
        db = blank_db()

    clean = blank_db()
    clean.update(db)

    for key in ["users", "topics", "comments", "files", "dms"]:
        if not isinstance(clean.get(key), dict):
            clean[key] = {}

    clean["version"] = 11

    if "updated_at" not in clean:
        clean["updated_at"] = now_ms()

    if "created_at" not in clean:
        clean["created_at"] = now_ms()

    # Remove old verification garbage from old database snapshots.
    for user in clean["users"].values():
        if isinstance(user, dict):
            user.pop("email_verified", None)
            user.pop("email_verified_at", None)
            user.pop("verification_code", None)
            user.pop("code_hash", None)
            user.pop("pending_register", None)

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
            **kwargs,
        )

        if response.status_code == 429:
            try:
                retry_after = float(response.json().get("retry_after", 1))
            except Exception:
                retry_after = 1

            time.sleep(retry_after)
            continue

        if not (200 <= response.status_code < 300):
            raise RuntimeError(
                f"Discord API error {response.status_code}: {response.text[:700]}"
            )

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
            params=params,
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
        json={"content": content},
    )

    clear_cache()
    return response.json()


def post_discord_attachment(content, filename, file_bytes, content_type):
    payload = {"content": content}
    data = {"payload_json": json.dumps(payload)}

    files = {
        "files[0]": (
            filename,
            BytesIO(file_bytes),
            content_type or "application/octet-stream",
        )
    }

    response = discord_request(
        "POST",
        f"/channels/{DISCORD_DB_CHANNEL_ID}/messages",
        data=data,
        files=files,
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
                    "size": attachment.get("size", 0),
                }

        elif content.startswith("SWPFP|"):
            pfp_id = content.split("|", 1)[1].strip()

            if pfp_id and pfp_id not in pfp_urls and attachments:
                attachment = attachments[0]
                pfp_urls[pfp_id] = {
                    "url": attachment.get("url", ""),
                    "filename": attachment.get("filename", ""),
                    "size": attachment.get("size", 0),
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
        "snapshot_loaded": snapshot_loaded,
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
        content=f"SWDBSNAP|v11|{int(time.time())}",
        filename="smartweb-db.json",
        file_bytes=raw,
        content_type="application/json",
    )


def save_uploaded_file_to_discord(file_id, filename, file_bytes, content_type):
    post_discord_attachment(
        content=f"SWFILE|{file_id}",
        filename=filename,
        file_bytes=file_bytes,
        content_type=content_type or "application/octet-stream",
    )


def save_profile_picture_to_discord(pfp_id, filename, file_bytes, content_type):
    post_discord_attachment(
        content=f"SWPFP|{pfp_id}",
        filename=filename,
        file_bytes=file_bytes,
        content_type=content_type or "application/octet-stream",
    )


def normalize_email(email):
    return (email or "").strip().lower()


def email_domain_is_blocked(domain):
    domain = domain.lower().strip()

    for blocked in BLOCKED_EMAIL_DOMAINS:
        if domain == blocked or domain.endswith("." + blocked):
            return True

    return False


def email_domain_format_ok(domain):
    if not domain or "." not in domain:
        return False

    if len(domain) > 253:
        return False

    labels = domain.split(".")

    for label in labels:
        if not label:
            return False

        if len(label) > 63:
            return False

        if label.startswith("-") or label.endswith("-"):
            return False

        if not re.fullmatch(r"[a-z0-9-]+", label):
            return False

    tld = labels[-1]

    if len(tld) < 2 or len(tld) > 24:
        return False

    if not re.fullmatch(r"[a-z]+", tld):
        return False

    return True


def email_domain_has_mx(domain):
    try:
        resolver = dns.resolver.Resolver()
        resolver.timeout = 3
        resolver.lifetime = 3

        answers = resolver.resolve(domain, "MX")
        return len(answers) > 0

    except Exception:
        return False


def validate_real_email(email):
    email = normalize_email(email)

    if not EMAIL_REGEX.fullmatch(email):
        return False, "Invalid email format."

    local, domain = email.rsplit("@", 1)

    if ".." in local:
        return False, "Invalid email format."

    if local.startswith(".") or local.endswith("."):
        return False, "Invalid email format."

    if email_domain_is_blocked(domain):
        return False, "Temporary or fake email domains are not allowed."

    if not email_domain_format_ok(domain):
        return False, "Invalid email domain."

    if not email_domain_has_mx(domain):
        return False, "This email domain cannot receive mail."

    return True, "Email accepted."


def user_id_from_email(email):
    return hashlib.sha256(email.strip().lower().encode("utf-8")).hexdigest()


def file_ext(filename):
    return os.path.splitext(filename.lower())[1]

def sha256_bytes(file_bytes):
    return hashlib.sha256(file_bytes).hexdigest()


def wav_audio_hash(file_bytes):
    """
    Basic WAV audio-content hash.
    It ignores some headers/metadata by hashing the data chunk only.
    """
    try:
        marker = b"data"
        index = file_bytes.find(marker)

        if index == -1:
            return ""

        size_start = index + 4
        data_start = index + 8

        if len(file_bytes) < data_start:
            return ""

        data_size = int.from_bytes(file_bytes[size_start:data_start], "little", signed=False)
        audio_data = file_bytes[data_start:data_start + data_size]

        if not audio_data:
            return ""

        return hashlib.sha256(audio_data).hexdigest()

    except Exception:
        return ""


def duplicate_file_check(db, original_name, file_bytes):
    new_name = original_name.strip().lower()
    new_file_hash = sha256_bytes(file_bytes)
    new_ext = file_ext(original_name)
    new_audio_hash = ""

    if new_ext == ".wav":
        new_audio_hash = wav_audio_hash(file_bytes)

    for old_file in db["files"].values():
        old_name = old_file.get("original_name", "").strip().lower()

        if old_name == new_name:
            return False, "A file with this same name already exists."

        if old_file.get("file_hash") == new_file_hash:
            return False, "This exact same file was already uploaded."

        if new_audio_hash and old_file.get("audio_hash") == new_audio_hash:
            return False, "This same WAV audio was already uploaded."

    return True, "No duplicate found."


def allowed_file(filename):
    return file_ext(filename) in ALLOWED_EXTENSIONS


def allowed_pfp(filename):
    return file_ext(filename) in ALLOWED_PFP_EXTENSIONS


def size_text(size):
    try:
        size = int(size)
    except Exception:
        size = 0

    if size >= 1024 * 1024:
        return f"{size / (1024 * 1024):.1f} MB"

    return f"{size / 1024:.1f} KB"


def file_category(filename):
    ext = file_ext(filename)

    if ext == ".zip":
        return "ZIP pack"

    if ext in AUDIO_EXTENSIONS:
        return "Audio"

    if ext in IMAGE_EXTENSIONS:
        return "Image"

    if ext in VIDEO_EXTENSIONS:
        return "Video"

    if ext in FL_STUDIO_EXTENSIONS:
        return "FL Studio"

    if ext in MOBILE_FRIENDLY_EXTENSIONS:
        return "Mobile"

    return "File"


def mime_for_name(filename):
    return MIME_TYPES.get(file_ext(filename), "application/octet-stream")


def cooldown_left(user, key, seconds):
    last = int(user.get(key, 0))
    now = int(time.time())
    left = seconds - (now - last)
    return max(0, left)


def blocked_raw_header(file_bytes):
    head = file_bytes[:80].lower()

    for bad in BLOCKED_RAW_HEADERS:
        if head.startswith(bad.lower()):
            return True

    return False


def looks_like_mp3(file_bytes):
    if len(file_bytes) < 4:
        return False

    if file_bytes[:3] == b"ID3":
        return True

    if file_bytes[0] == 0xFF and (file_bytes[1] & 0xE0) == 0xE0:
        return True

    return False


def looks_like_wav(file_bytes):
    return len(file_bytes) > 12 and file_bytes[:4] == b"RIFF" and file_bytes[8:12] == b"WAVE"


def looks_like_ogg(file_bytes):
    return file_bytes.startswith(b"OggS")


def looks_like_flac(file_bytes):
    return file_bytes.startswith(b"fLaC")


def looks_like_m4a_or_aac(file_bytes):
    return b"ftyp" in file_bytes[:16] or file_bytes[:2] in [b"\xff\xf1", b"\xff\xf9"]


def looks_like_image(filename, file_bytes):
    ext = file_ext(filename)

    if ext == ".png":
        return file_bytes.startswith(b"\x89PNG\r\n\x1a\n")

    if ext in [".jpg", ".jpeg"]:
        return file_bytes.startswith(b"\xff\xd8\xff")

    if ext == ".gif":
        return file_bytes.startswith(b"GIF87a") or file_bytes.startswith(b"GIF89a")

    if ext == ".webp":
        return len(file_bytes) > 12 and file_bytes[:4] == b"RIFF" and file_bytes[8:12] == b"WEBP"

    return False


def looks_like_video(filename, file_bytes):
    ext = file_ext(filename)

    if ext in [".mp4", ".mov"]:
        return b"ftyp" in file_bytes[:24]

    return False


def scan_zip_file(file_bytes):
    if not zipfile.is_zipfile(BytesIO(file_bytes)):
        return False, "This does not look like a real ZIP file."

    total_uncompressed = 0
    max_uncompressed = 100 * 1024 * 1024
    max_files = 250

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
                    return False, f"ZIP contains blocked file type: {inner_ext}"

                total_uncompressed += int(info.file_size)

                if total_uncompressed > max_uncompressed:
                    return False, "ZIP is too large when extracted."

                if info.compress_size > 0:
                    ratio = info.file_size / max(info.compress_size, 1)

                    if ratio > 120:
                        return False, "ZIP looks like a zip bomb."

    except Exception:
        return False, "ZIP could not be scanned."

    return True, "ZIP accepted."


def scan_uploaded_file(filename, file_bytes):
    ext = file_ext(filename)

    if ext not in ALLOWED_EXTENSIONS:
        return False, "This file type is not allowed."

    if ext != ".zip" and blocked_raw_header(file_bytes):
        return False, "This file looks like an executable or script."

    if ext == ".zip":
        return scan_zip_file(file_bytes)

    if ext == ".mp3":
        if not looks_like_mp3(file_bytes):
            return False, "This does not look like a real MP3 file."
        return True, "MP3 accepted."

    if ext == ".wav":
        if not looks_like_wav(file_bytes):
            return False, "This does not look like a real WAV file."
        return True, "WAV accepted."

    if ext == ".ogg":
        if not looks_like_ogg(file_bytes):
            return False, "This does not look like a real OGG file."
        return True, "OGG accepted."

    if ext == ".flac":
        if not looks_like_flac(file_bytes):
            return False, "This does not look like a real FLAC file."
        return True, "FLAC accepted."

    if ext in {".m4a", ".aac"}:
        if not looks_like_m4a_or_aac(file_bytes):
            return False, "This does not look like a real mobile audio file."
        return True, "Mobile audio accepted."

    if ext in IMAGE_EXTENSIONS:
        if not looks_like_image(filename, file_bytes):
            return False, "This does not look like a real image file."
        return True, "Image accepted."

    if ext in VIDEO_EXTENSIONS:
        if not looks_like_video(filename, file_bytes):
            return False, "This does not look like a real video file."
        return True, "Video accepted."

    if ext in FL_STUDIO_EXTENSIONS:
        return True, "FL Studio file accepted."

    if ext in MOBILE_FRIENDLY_EXTENSIONS:
        return True, "Mobile-friendly file accepted."

    return True, "File accepted."


def scan_profile_picture(filename, file_bytes):
    if not looks_like_image(filename, file_bytes):
        return False, "This does not look like a real image file."

    return True, "Image accepted."


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
    if db is None:
        db = load_store()["db"]

    name = file_data.get("original_name", "file")
    ext = file_ext(name)
    file_id = file_data.get("id", "")

    file_comments = [
        c for c in db["comments"].values()
        if c.get("file_id") == file_id
    ]

    file_comments.sort(key=lambda c: int(c.get("created", 0)))

    return {
        "id": file_id,
        "name": name,
        "size": size_text(file_data.get("size", 0)),
        "category": file_category(name),
        "author": username_from_id(
            file_data.get("author_id", ""),
            db,
            file_data.get("author", "unknown"),
        ),
        "author_id": file_data.get("author_id", ""),
        "created": int(file_data.get("created", 0)),
        "is_audio": ext in AUDIO_EXTENSIONS,
        "is_image": ext in IMAGE_EXTENSIONS,
        "is_video": ext in VIDEO_EXTENSIONS,
        "is_fl": ext in FL_STUDIO_EXTENSIONS,
        "media_url": url_for("media_file", file_id=file_id),
        "comments": [
            {
                "id": c.get("id", ""),
                "body": c.get("body", ""),
                "author": username_from_id(
                    c.get("author_id", ""),
                    db,
                    c.get("author", "unknown"),
                ),
                "author_id": c.get("author_id", ""),
                "created": int(c.get("created", 0)),
            }
            for c in file_comments
        ],
    }

    return {
        "id": file_id,
        "name": name,
        "size": size_text(file_data.get("size", 0)),
        "category": file_category(name),
        "author": username_from_id(
            file_data.get("author_id", ""),
            db,
            file_data.get("author", "unknown"),
        ),
        "author_id": file_data.get("author_id", ""),
        "created": int(file_data.get("created", 0)),
        "is_audio": ext in AUDIO_EXTENSIONS,
        "is_image": ext in IMAGE_EXTENSIONS,
        "is_video": ext in VIDEO_EXTENSIONS,
        "is_fl": ext in FL_STUDIO_EXTENSIONS,
        "media_url": url_for("media_file", file_id=file_id),
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
                "author": username_from_id(
                    c.get("author_id", ""),
                    db,
                    c.get("author", "unknown"),
                ),
                "author_id": c.get("author_id", ""),
                "created": int(c.get("created", 0)),
            }
            for c in topic_comments
        ],
    }


def public_user(user, db, store, viewer_id):
    uid = user.get("id", "")

    return {
        "id": uid,
        "username": user.get("username", "unknown"),
        "email": user.get("email", "") if uid == viewer_id else "",
        "about": user.get("about", ""),
        "joined": int(user.get("created", 0)),
        "pfp_url": pfp_url_from_user(user, store),
        "topic_count": len([t for t in db["topics"].values() if t.get("author_id") == uid]),
        "comment_count": len([c for c in db["comments"].values() if c.get("author_id") == uid]),
        "file_count": len([f for f in db["files"].values() if f.get("author_id") == uid]),
    }


def public_dm_messages(db, current_id):
    messages = []

    for dm in db["dms"].values():
        if dm.get("from") == current_id or dm.get("to") == current_id:
            messages.append(
                {
                    "id": dm.get("id", ""),
                    "from": dm.get("from", ""),
                    "to": dm.get("to", ""),
                    "body": dm.get("body", ""),
                    "created": int(dm.get("created", 0)),
                }
            )

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

            items.append(
                {
                    "id": dm.get("id", ""),
                    "type": "dm",
                    "title": f"New message from {sender.get('username', 'unknown')}",
                    "body": dm.get("body", "")[:160],
                    "from_id": dm.get("from", ""),
                    "target_id": dm.get("from", ""),
                    "created": created,
                    "unread": created > last_seen,
                }
            )

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

            items.append(
                {
                    "id": comment.get("id", ""),
                    "type": "comment",
                    "title": f"{commenter.get('username', 'unknown')} commented on your post",
                    "body": topic.get("title", "discussion"),
                    "from_id": commenter_id,
                    "target_id": comment.get("topic_id", ""),
                    "created": created,
                    "unread": created > last_seen,
                }
            )

    for topic in db["topics"].values():
        created = int(topic.get("created", 0))

        if topic.get("author_id") != current_id:
            author = db["users"].get(topic.get("author_id", ""), {})

            items.append(
                {
                    "id": topic.get("id", ""),
                    "type": "topic",
                    "title": f"{author.get('username', 'unknown')} made a new post",
                    "body": topic.get("title", "")[:160],
                    "from_id": topic.get("author_id", ""),
                    "target_id": topic.get("id", ""),
                    "created": created,
                    "unread": created > last_seen,
                }
            )

    for file_data in db["files"].values():
        created = int(file_data.get("created", 0))

        if file_data.get("author_id") != current_id:
            author = db["users"].get(file_data.get("author_id", ""), {})

            items.append(
                {
                    "id": file_data.get("id", ""),
                    "type": "file",
                    "title": f"{author.get('username', 'unknown')} uploaded {file_category(file_data.get('original_name', 'file'))}",
                    "body": file_data.get("original_name", "file")[:160],
                    "from_id": file_data.get("author_id", ""),
                    "target_id": file_data.get("id", ""),
                    "created": created,
                    "unread": created > last_seen,
                }
            )

    items.sort(key=lambda x: x["created"], reverse=True)
    return items[:50]


def build_state(db, store, user):
    current_id = user.get("id", "")

    files = [public_file(file_data, db) for file_data in db["files"].values()]
    files.sort(key=lambda x: x["created"], reverse=True)

    topics = [public_topic(topic_data, db) for topic_data in db["topics"].values()]
    topics.sort(key=lambda x: x["created"], reverse=True)

    users = [
        public_user(user_data, db, store, current_id)
        for user_data in db["users"].values()
    ]
    users.sort(key=lambda x: x["username"].lower())

    notifications = public_notifications(db, current_id)
    unread_count = len([n for n in notifications if n.get("unread")])

    return {
        "files": files,
        "topics": topics,
        "users": users,
        "dms": public_dm_messages(db, current_id),
        "notifications": notifications,
        "notification_count": unread_count,
    }


def remove_old_verification_flash():
    flashes = session.get("_flashes", [])

    if not flashes:
        return

    cleaned = []

    blocked_phrases = [
        "old unverified",
        "verification",
        "email code",
        "verify email",
        "send verification",
        "verified",
    ]

    for category, message in flashes:
        lower = str(message).lower()

        if any(phrase in lower for phrase in blocked_phrases):
            continue

        cleaned.append((category, message))

    session["_flashes"] = cleaned


HTML = """
<!DOCTYPE html>
<html>
<head>
<title>FunkFile</title>
<meta name="viewport" content="width=device-width, initial-scale=1">

<style>
*{box-sizing:border-box;border-radius:0!important}
:root{
    --bg:#060b10;
    --bg2:#09131b;
    --text:#f5f7fa;
    --muted:rgba(245,247,250,.62);
    --line:rgba(255,255,255,.16);
    --line2:rgba(255,255,255,.08);
    --blue:#9fe7ff;
    --danger:#ff5c6c;
    --good:#55e68a;
}
html,body{margin:0;min-height:100vh}
body{
    color:var(--text);
    font-family:Arial,Helvetica,sans-serif;
    background:
        linear-gradient(rgba(255,255,255,.025) 1px,transparent 1px),
        linear-gradient(90deg,rgba(255,255,255,.025) 1px,transparent 1px),
        linear-gradient(135deg,var(--bg),var(--bg2));
    background-size:24px 24px,24px 24px,100% 100%;
}
a{color:var(--blue);font-weight:900;text-decoration:none}
a:hover{text-decoration:underline;color:white}
.app{min-height:100vh;display:flex}
.side{
    width:260px;
    padding:30px 24px;
    border-right:1px solid var(--line);
    background:rgba(0,0,0,.10);
    display:flex;
    flex-direction:column;
    position:fixed;
    left:0;
    top:0;
    bottom:0;
}
.content{
    margin-left:260px;
    flex:1;
    padding:48px 58px;
    min-height:100vh;
}
.logo{
    font-size:12px;
    letter-spacing:4px;
    color:white;
    margin-bottom:34px;
    font-weight:900;
}
.logo:after{
    content:"";
    display:block;
    width:110px;
    height:1px;
    background:var(--blue);
    margin-top:14px;
}
.user-mini{
    display:flex;
    align-items:center;
    gap:12px;
    padding:0 0 22px 0;
    margin-bottom:24px;
    border-bottom:1px solid var(--line);
}
.pfp{
    width:46px;
    height:46px;
    border:1px solid var(--line);
    display:flex;
    align-items:center;
    justify-content:center;
    overflow:hidden;
    color:white;
    font-weight:900;
    font-size:18px;
}
.pfp.big{width:68px;height:68px;font-size:26px}
.pfp img{
    width:100%;
    height:100%;
    object-fit:cover;
    display:block;
}
.user-mini-name{font-weight:900;font-size:14px;color:white}
.user-mini-mail{font-size:12px;color:var(--muted);margin-top:4px;word-break:break-all}
.menu-main{flex:1}
.menu-bottom{border-top:1px solid var(--line);padding-top:18px}
.menu a{
    display:block;
    line-height:2.4;
    color:var(--muted);
    padding:0;
    margin-bottom:4px;
    font-size:14px;
    letter-spacing:.4px;
    text-transform:uppercase;
    font-weight:900;
}
.menu a.active,.menu a:hover{
    color:white;
    box-shadow:inset 3px 0 0 var(--blue);
    padding-left:12px;
    text-decoration:none;
}
.page-title{
    font-size:36px;
    font-weight:900;
    margin:0 0 10px 0;
    color:white;
    letter-spacing:-1px;
}
.page-sub{
    color:var(--muted);
    font-size:14px;
    line-height:1.7;
    margin:0 0 34px 0;
    max-width:720px;
}
.grid{
    display:grid;
    grid-template-columns:repeat(3,minmax(0,1fr));
    gap:0;
    margin-bottom:34px;
    border-top:1px solid var(--line);
    border-bottom:1px solid var(--line);
}
.card{
    border-right:1px solid var(--line);
    padding:20px 22px;
}
.card:last-child{border-right:none}
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
}
.line{
    border-left:1px solid var(--line);
    padding-left:24px;
    max-width:980px;
}
.row{
    padding:18px 0;
    border-bottom:1px solid var(--line2);
}
.row-title{
    font-size:15px;
    color:white;
    margin-bottom:7px;
    font-weight:900;
}
.meta,.small{
    color:var(--muted);
    font-size:14px;
    line-height:1.7;
}
.body-text{white-space:pre-wrap}
.form-box{
    margin-top:34px;
    border-left:1px solid var(--line);
    padding-left:24px;
    max-width:740px;
}
input,textarea{
    background:transparent;
    color:white;
    border:none;
    border-bottom:1px solid var(--line);
    padding:13px 0;
    outline:none;
    font-size:14px;
    width:100%;
    margin-bottom:14px;
}
input::placeholder,textarea::placeholder{color:rgba(255,255,255,.45)}
input:focus,textarea:focus{border-color:var(--blue)}
textarea{min-height:96px;resize:vertical}
button,.btn,.file-button{
    display:inline-flex;
    align-items:center;
    justify-content:center;
    gap:10px;
    background:transparent;
    color:white;
    border:1px solid var(--line);
    padding:12px 18px;
    cursor:pointer;
    transition:.12s ease;
    font-size:14px;
    text-decoration:none;
    font-weight:900;
}
button:hover,.btn:hover,.file-button:hover{
    background:rgba(255,255,255,.06);
    border-color:var(--blue);
    text-decoration:none;
}
.primary{
    background:white;
    color:#06101d;
    border-color:white;
}
.primary:hover{
    background:rgba(255,255,255,.86);
    color:#06101d;
}
.danger{
    border-color:rgba(255,92,108,.55);
    color:#ffd8dd;
}
.danger:hover{
    background:rgba(255,92,108,.08);
    border-color:var(--danger);
}
.login-only{
    min-height:100vh;
    display:flex;
    justify-content:center;
    align-items:center;
    padding:20px;
}
.login-shell{
    width:390px;
    border-left:1px solid var(--line);
    border-right:1px solid var(--line);
    padding:32px;
}
.login-title{
    font-size:34px;
    font-weight:900;
    color:white;
    margin-bottom:8px;
    letter-spacing:-1px;
}
.login-sub{
    color:var(--muted);
    font-size:14px;
    line-height:1.6;
    margin-bottom:26px;
}
.alert-box,.success-box{
    padding:12px 0 12px 14px;
    margin-bottom:18px;
    font-size:14px;
}
.alert-box{
    color:#ffdede;
    border-left:3px solid var(--danger);
}
.success-box{
    color:#d6ffe1;
    border-left:3px solid var(--good);
}
.login-line{
    height:1px;
    background:var(--line);
    margin:18px 0 14px;
}
.switch-text{
    color:var(--muted);
    text-align:center;
    font-size:13px;
    margin-top:18px;
}
.file-tag{
    display:inline-block;
    border:1px solid var(--line);
    padding:2px 8px;
    color:var(--muted);
    font-size:11px;
    text-transform:uppercase;
    letter-spacing:1px;
    margin-bottom:8px;
}
.preview{
    margin-top:14px;
}
.preview img{
    max-width:280px;
    max-height:200px;
    object-fit:cover;
    border:1px solid var(--line);
}
.preview video{
    max-width:360px;
    width:100%;
    border:1px solid var(--line);
}
audio{
    width:100%;
    max-width:430px;
}
audio::-webkit-media-controls-panel{
    border-radius:0;
}
.selected-file{
    color:var(--muted);
    font-size:14px;
    margin-left:10px;
}
.profile-head{
    display:flex;
    align-items:center;
    gap:14px;
    margin-bottom:24px;
}
.dm.me{
    margin-left:auto;
    max-width:70%;
    border-left:1px solid var(--blue);
    padding-left:14px;
}
.dm.them{
    max-width:70%;
    border-left:1px solid var(--line);
    padding-left:14px;
}
@media(max-width:900px){
    .app{display:block}
    .side{
        position:relative;
        width:100%;
        border-right:none;
        border-bottom:1px solid var(--line);
    }
    .content{
        margin-left:0;
        padding:28px;
    }
    .grid{grid-template-columns:1fr}
    .card{
        border-right:none;
        border-bottom:1px solid var(--line);
    }
    .card:last-child{border-bottom:none}
    .login-shell{width:100%}
}
</style>
</head>

<body>

{% if not user_email %}

<div class="login-only">
    <div class="login-shell">
        <div class="logo">FunkFile</div>

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

        {% if view == "register" %}
            <div class="login-title">Create account</div>
            <div class="login-sub">Use a real email domain. No verification code. Temporary and non-mail domains are blocked.</div>

            <form action="/register" method="POST">
                <input name="username" placeholder="Username" required>
                <input name="email" type="email" placeholder="Email" required>
                <input name="password" type="password" placeholder="Password" required>
                <button class="primary" type="submit">Create Account</button>
            </form>

            <div class="login-line"></div>
            <a class="btn" href="/?view=login">Back to Login</a>

            <div class="switch-text">
                Already have an account? <a href="/?view=login">Login</a>
            </div>
        {% else %}
            <div class="login-title">Login</div>
            <div class="login-sub">Private producer space for files, discussion, messages, profiles and account settings.</div>

            <form action="/login" method="POST">
                <input name="email" type="email" placeholder="Email" required>
                <input name="password" type="password" placeholder="Password" required>
                <button type="submit">Login</button>
            </form>

            <div class="login-line"></div>
            <a class="btn primary" href="/?view=register">Create Account</a>

            <div class="switch-text">
                New producer? <a href="/?view=register">Register</a>
            </div>
        {% endif %}
    </div>
</div>

{% else %}

<div class="app">
    <div class="side">
        <div class="logo">FunkFile</div>

        <a class="user-mini" href="/?view=account">
            <div class="pfp">
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
        </a>

        <div class="menu menu-main">
            <a class="{% if view == 'dashboard' %}active{% endif %}" href="/?view=dashboard">home</a>
            <a class="{% if view == 'files' %}active{% endif %}" href="/?view=files">files</a>
            <a class="{% if view == 'discussion' or view == 'topic' %}active{% endif %}" href="/?view=discussion">discussion</a>
            <a class="{% if view == 'messages' or view == 'dm' %}active{% endif %}" href="/?view=messages">messages</a>
            <a class="{% if view == 'profiles' or view == 'profile' %}active{% endif %}" href="/?view=profiles">profiles</a>
            <a class="{% if view == 'notifications' %}active{% endif %}" href="/?view=notifications">notifications <span style="color:#9fe7ff">{{ notification_count }}</span></a>
        </div>

        <div class="menu menu-bottom">
            <a class="{% if view == 'account' %}active{% endif %}" href="/?view=account">account</a>
            <a class="{% if view == 'credits' %}active{% endif %}" href="/?view=credits">credits</a>
        </div>
    </div>

    <main class="content">
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

        {% if view == "dashboard" %}
            <div class="page-title">producer room</div>
            <div class="page-sub">Upload FL Studio files, ZIP packs, audio previews, images and mobile-friendly files.</div>

            <div class="grid">
                <div class="card">
                    <div class="card-label">files</div>
                    <div class="card-number">{{ files|length }}</div>
                </div>
                <div class="card">
                    <div class="card-label">topics</div>
                    <div class="card-number">{{ topics|length }}</div>
                </div>
                <div class="card">
                    <div class="card-label">messages</div>
                    <div class="card-number">{{ dms|length }}</div>
                </div>
            </div>

            <div class="line">
                <div class="row-title">recent files</div>

                {% if files|length == 0 %}
                    <div class="small">No files yet.</div>
                {% endif %}

                {% for file in files[:3] %}
                    <div class="row">
                        <div class="file-tag">{{ file.category }}</div>
                        <div class="row-title">{{ file.name }}</div>
                        <div class="meta">{{ file.size }} · by <a href="/?view=profile&id={{ file.author_id }}">{{ file.author }}</a></div>
                        <a href="/download/{{ file.id }}">download</a>

                        {% if file.is_audio %}
                            <div class="preview"><audio controls src="{{ file.media_url }}"></audio></div>
                        {% elif file.is_image %}
                            <div class="preview"><img src="{{ file.media_url }}" alt="{{ file.name }}"></div>
                        {% elif file.is_video %}
                            <div class="preview"><video controls src="{{ file.media_url }}"></video></div>
                        {% endif %}
                        <br>
<div class="meta">{{ file.comments|length }} file comments</div>

{% for comment in file.comments[:2] %}
    <div class="row" style="margin-left:18px;">
        <div class="meta">
            <a href="/?view=profile&id={{ comment.author_id }}">{{ comment.author }}</a>
        </div>
        <div class="small body-text">{{ comment.body }}</div>
    </div>
{% endfor %}

<form action="/file-comment/{{ file.id }}" method="POST" style="margin-top:12px;">
    <textarea name="body" placeholder="comment under this file" required></textarea>
    <button class="primary" type="submit">comment</button>
</form>
                    </div>
                {% endfor %}

                <br>
                <div class="row-title">recent discussions</div>

                {% if topics|length == 0 %}
                    <div class="small">No topics yet.</div>
                {% endif %}

                {% for topic in topics[:3] %}
                    <div class="row">
                        <div class="row-title">{{ topic.title }}</div>
                        <div class="meta">by <a href="/?view=profile&id={{ topic.author_id }}">{{ topic.author }}</a> · {{ topic.comments|length }} comments</div>
                        <div class="small">{{ topic.body[:160] }}{% if topic.body|length > 160 %}...{% endif %}</div>
                        <br>
                        <a href="/?view=topic&id={{ topic.id }}">open discussion</a>
                    </div>
                {% endfor %}
            </div>

            <div class="form-box">
                <a class="btn" href="/?view=files">upload file</a>
                <a class="btn" href="/?view=discussion">start discussion</a>
            </div>

        {% elif view == "files" %}
            <div class="page-title">files</div>
            <div class="page-sub">Allowed: ZIP, audio, images, video, FL Studio files, PDF and TXT. FL Studio examples: FLP, FST, MID, SF2, DWP, FXP.</div>

            <div class="line">
                {% if files|length == 0 %}
                    <div class="small">No files yet.</div>
                {% endif %}

                {% for file in files %}
                    <div class="row">
                        <div class="file-tag">{{ file.category }}</div>
                        <div class="row-title">{{ file.name }}</div>
                        <div class="meta">{{ file.size }} · by <a href="/?view=profile&id={{ file.author_id }}">{{ file.author }}</a></div>
                        <a href="/download/{{ file.id }}">download</a>

                        {% if file.is_audio %}
                            <div class="preview"><audio controls src="{{ file.media_url }}"></audio></div>
                        {% elif file.is_image %}
                            <div class="preview"><img src="{{ file.media_url }}" alt="{{ file.name }}"></div>
                        {% elif file.is_video %}
                            <div class="preview"><video controls src="{{ file.media_url }}"></video></div>
                        {% endif %}
                        <br>
<div class="row-title">comments</div>

{% if file.comments|length == 0 %}
    <div class="small">No comments under this file yet.</div>
{% endif %}

{% for comment in file.comments %}
    <div class="row" style="margin-left:18px;">
        <div class="meta">
            <a href="/?view=profile&id={{ comment.author_id }}">{{ comment.author }}</a>
        </div>
        <div class="small body-text">{{ comment.body }}</div>
    </div>
{% endfor %}

<form action="/file-comment/{{ file.id }}" method="POST" style="margin-top:12px;">
    <textarea name="body" placeholder="comment under this file" required></textarea>
    <button class="primary" type="submit">comment</button>
</form>
                    </div>
                {% endfor %}
            </div>

            <div class="form-box">
                <form action="/upload" method="POST" enctype="multipart/form-data">
                    <input id="fileInput" type="file" name="uploadfile" accept=".zip,.mp3,.wav,.ogg,.m4a,.aac,.flac,.png,.jpg,.jpeg,.webp,.gif,.mp4,.mov,.flp,.fst,.mid,.midi,.sf2,.sfz,.dwp,.fxp,.fxb,.nmsv,.txt,.pdf" required hidden>
                    <label for="fileInput" class="file-button">select file</label>
                    <span id="fileName" class="selected-file">no file selected</span>
                    <br><br>
                    <button class="primary" type="submit">upload file</button>
                </form>
                <p class="small">Maximum size: {{ max_file_mb }} MB.</p>
            </div>

        {% elif view == "discussion" %}
            <div class="page-title">discussion</div>
            <div class="page-sub">Post beat ideas, FL Studio help, mobile producer questions or feedback requests.</div>

            <div class="line">
                {% if topics|length == 0 %}
                    <div class="small">No topics yet.</div>
                {% endif %}

                {% for topic in topics %}
                    <div class="row">
                        <div class="row-title">{{ topic.title }}</div>
                        <div class="meta">by <a href="/?view=profile&id={{ topic.author_id }}">{{ topic.author }}</a> · {{ topic.comments|length }} comments</div>
                        <div class="small">{{ topic.body[:180] }}{% if topic.body|length > 180 %}...{% endif %}</div>
                        <br>
                        <a href="/?view=topic&id={{ topic.id }}">open discussion</a>
                    </div>
                {% endfor %}
            </div>

            <div class="form-box">
                <form action="/topic" method="POST">
                    <input name="title" placeholder="topic title" required>
                    <textarea name="body" placeholder="write topic text" required></textarea>
                    <button class="primary" type="submit">add topic</button>
                </form>
                <p class="small">Post cooldown: {{ post_cooldown }} seconds.</p>
            </div>

        {% elif view == "topic" %}
            {% if selected_topic %}
                <div class="page-title">{{ selected_topic.title }}</div>
                <div class="page-sub">by <a href="/?view=profile&id={{ selected_topic.author_id }}">{{ selected_topic.author }}</a></div>

                <a class="btn" href="/?view=discussion">back</a>

                {% if selected_topic.can_delete %}
                    <form action="/delete-topic/{{ selected_topic.id }}" method="POST" style="display:inline-block;margin-left:10px;" onsubmit="return confirm('Delete this post?')">
                        <button class="danger" type="submit">delete post</button>
                    </form>
                {% endif %}

                <br><br>

                <div class="line">
                    <div class="small body-text">{{ selected_topic.body }}</div>
                    <br><br>

                    <div class="row-title">comments</div>

                    {% if selected_topic.comments|length == 0 %}
                        <div class="small">No comments yet.</div>
                    {% endif %}

                    {% for comment in selected_topic.comments %}
                        <div class="row">
                            <div class="meta"><a href="/?view=profile&id={{ comment.author_id }}">{{ comment.author }}</a></div>
                            <div class="small body-text">{{ comment.body }}</div>
                        </div>
                    {% endfor %}
                </div>

                <div class="form-box">
                    <form action="/comment/{{ selected_topic.id }}" method="POST">
                        <textarea name="body" placeholder="write comment" required></textarea>
                        <button class="primary" type="submit">comment</button>
                    </form>
                </div>
            {% else %}
                <div class="page-title">topic not found</div>
                <a class="btn" href="/?view=discussion">back</a>
            {% endif %}

        {% elif view == "profiles" %}
            <div class="page-title">profiles</div>
            <div class="page-sub">View people and send direct messages.</div>

            <div class="line">
                {% for person in users %}
                    <div class="row">
                        <div class="profile-head">
                            <div class="pfp">
                                {% if person.pfp_url %}
                                    <img src="{{ person.pfp_url }}" alt="pfp">
                                {% else %}
                                    {{ person.username[0]|upper }}
                                {% endif %}
                            </div>
                            <div>
                                <div class="row-title">{{ person.username }}</div>
                                <div class="meta">{{ person.topic_count }} posts · {{ person.file_count }} files · {{ person.comment_count }} comments</div>
                                <a href="/?view=profile&id={{ person.id }}">view profile</a>
                            </div>
                        </div>
                    </div>
                {% endfor %}
            </div>

        {% elif view == "profile" %}
            {% if selected_profile %}
                <div class="page-title">profile</div>
                <div class="page-sub">Member profile and activity.</div>

                <div class="line">
                    <div class="profile-head">
                        <div class="pfp big">
                            {% if selected_profile.pfp_url %}
                                <img src="{{ selected_profile.pfp_url }}" alt="pfp">
                            {% else %}
                                {{ selected_profile.username[0]|upper }}
                            {% endif %}
                        </div>
                        <div>
                            <div class="row-title">{{ selected_profile.username }}</div>
                            <div class="meta">
                                {{ selected_profile.topic_count }} posts · {{ selected_profile.file_count }} files · {{ selected_profile.comment_count }} comments
                            </div>
                        </div>
                    </div>

                    <div class="row-title">about</div>
                    <div class="small body-text">{{ selected_profile.about or "No about text yet." }}</div>
                    <br>

                    {% if selected_profile.id == current_user_id %}
                        <a class="btn" href="/?view=account">edit account</a>
                    {% else %}
                        <a class="btn" href="/?view=dm&id={{ selected_profile.id }}">direct message</a>
                    {% endif %}
                </div>
            {% else %}
                <div class="page-title">profile not found</div>
                <a class="btn" href="/?view=profiles">back</a>
            {% endif %}

        {% elif view == "messages" %}
            <div class="page-title">messages</div>
            <div class="page-sub">Direct messages with other members.</div>

            <div class="line">
                {% if dm_partners|length == 0 %}
                    <div class="small">No messages yet. Open a profile and start a DM.</div>
                {% endif %}

                {% for partner in dm_partners %}
                    <div class="row">
                        <div class="row-title">{{ partner.username }}</div>
                        <div class="small">{{ partner.last_body[:120] }}</div>
                        <br>
                        <a href="/?view=dm&id={{ partner.id }}">open chat</a>
                    </div>
                {% endfor %}
            </div>

        {% elif view == "dm" %}
            {% if selected_dm_user %}
                <div class="page-title">direct message</div>
                <div class="page-sub">Chatting with <a href="/?view=profile&id={{ selected_dm_user.id }}">{{ selected_dm_user.username }}</a></div>

                <a class="btn" href="/?view=messages">back</a>
                <br><br>

                <div class="line">
                    {% if selected_dm_messages|length == 0 %}
                        <div class="small">No messages yet.</div>
                    {% endif %}

                    {% for msg in selected_dm_messages %}
                        <div class="row dm {% if msg.from == current_user_id %}me{% else %}them{% endif %}">
                            <div class="meta">{% if msg.from == current_user_id %}you{% else %}{{ selected_dm_user.username }}{% endif %}</div>
                            <div class="small body-text">{{ msg.body }}</div>
                        </div>
                    {% endfor %}
                </div>

                <div class="form-box">
                    <form action="/send-dm/{{ selected_dm_user.id }}" method="POST">
                        <textarea name="body" placeholder="write message" required></textarea>
                        <button class="primary" type="submit">send message</button>
                    </form>
                </div>
            {% else %}
                <div class="page-title">chat not found</div>
                <a class="btn" href="/?view=messages">back</a>
            {% endif %}

        {% elif view == "notifications" %}
            <div class="page-title">notifications</div>
            <div class="page-sub">Recent messages, comments, posts and uploads.</div>

            <div class="line">
                {% if notifications|length == 0 %}
                    <div class="small">No notifications yet.</div>
                {% endif %}

                {% for n in notifications %}
                    <div class="row">
                        <div class="row-title">{% if n.unread %}● {% endif %}{{ n.title }}</div>
                        <div class="small">{{ n.body }}</div>
                    </div>
                {% endfor %}
            </div>

        {% elif view == "account" %}
            <div class="page-title">account</div>
            <div class="page-sub">Edit your profile picture, about text, username, password or logout.</div>

            <div class="line">
                <div class="profile-head">
                    <div class="pfp big">
                        {% if pfp_url %}
                            <img src="{{ pfp_url }}" alt="pfp">
                        {% else %}
                            {{ username[0]|upper }}
                        {% endif %}
                    </div>
                    <div>
                        <div class="row-title">{{ username }}</div>
                        <div class="meta">{{ user_email }}</div>
                    </div>
                </div>

                <div class="form-box">
                    <div class="row-title">change profile picture</div>
                    <form action="/change-pfp" method="POST" enctype="multipart/form-data">
                        <input id="pfpInput" type="file" name="pfp" accept=".png,.jpg,.jpeg,.webp,.gif" required hidden>
                        <label for="pfpInput" class="file-button">select profile picture</label>
                        <span id="pfpName" class="selected-file">no profile picture selected</span>
                        <br><br>
                        <button class="primary" type="submit">save profile picture</button>
                    </form>

                    <br><br>
                    <div class="row-title">about me</div>
                    <form action="/change-about" method="POST">
                        <textarea name="about" placeholder="write something about yourself">{{ me.about }}</textarea>
                        <button class="primary" type="submit">save about</button>
                    </form>

                    <br><br>
                    <div class="row-title">change username</div>
                    <form action="/change-username" method="POST">
                        <input name="new_username" placeholder="new username" required>
                        <button class="primary" type="submit">save username</button>
                    </form>

                    <br><br>
                    <div class="row-title">change password</div>
                    <form action="/change-password" method="POST">
                        <input name="old_password" type="password" placeholder="old password" required>
                        <input name="new_password" type="password" placeholder="new password" required>
                        <button class="primary" type="submit">save password</button>
                    </form>

                    <br><br>
                    <a class="btn" href="/logout">logout</a>
                </div>
            </div>

        {% elif view == "credits" %}
            <div class="page-title">credits</div>
            <div class="page-sub">People behind the site.</div>

            <div class="line">
                <div class="row-title">OWNERS</div>
                {% for p in credits["OWNERS"] %}
                    <div class="row"><div class="row-title">{{ p }}</div></div>
                {% endfor %}

                <br>
                <div class="row-title">MEMBERS</div>
                {% for p in credits["MEMBERS"] %}
                    <div class="row"><div class="row-title">{{ p }}</div></div>
                {% endfor %}

                <br>
                <div class="row-title">WEBSITE MADE BY</div>
                {% for p in credits["WEBSITE MADE BY"] %}
                    <div class="row"><div class="row-title">{{ p }}</div></div>
                {% endfor %}
            </div>
        {% endif %}
    </main>
</div>

{% endif %}

<script>
const fileInput = document.getElementById("fileInput");
const fileName = document.getElementById("fileName");

if(fileInput && fileName){
    fileInput.addEventListener("change", () => {
        fileName.textContent = fileInput.files.length ? fileInput.files[0].name : "no file selected";
    });
}

const pfpInput = document.getElementById("pfpInput");
const pfpName = document.getElementById("pfpName");

if(pfpInput && pfpName){
    pfpInput.addEventListener("change", () => {
        pfpName.textContent = pfpInput.files.length ? pfpInput.files[0].name : "no profile picture selected";
    });
}
</script>

</body>
</html>
"""


@app.route("/")
def home():
    requested_view = request.args.get("view", "dashboard")
    requested_id = request.args.get("id", "")

    session.pop("pending_register", None)
    remove_old_verification_flash()

    if not current_email():
        view = "register" if requested_view == "register" else "login"

        return render_template_string(
            HTML,
            view=view,
            files=[],
            topics=[],
            users=[],
            dms=[],
            dm_partners=[],
            notifications=[],
            notification_count=0,
            credits=CREDITS,
            user_email=None,
            username="",
            current_user_id="",
            pfp_url="",
            selected_topic=None,
            selected_profile=None,
            selected_dm_user=None,
            selected_dm_messages=[],
            me={},
            max_file_mb=MAX_FILE_SIZE // (1024 * 1024),
            post_cooldown=POST_COOLDOWN_SECONDS,
            comment_cooldown=COMMENT_COOLDOWN_SECONDS,
        )

    allowed_views = {
        "dashboard",
        "files",
        "discussion",
        "topic",
        "profiles",
        "profile",
        "messages",
        "dm",
        "notifications",
        "account",
        "credits",
    }

    if requested_view not in allowed_views:
        requested_view = "dashboard"

    try:
        store = load_store(force=True)
        db = store["db"]
        user = db["users"].get(current_user_id())
    except Exception as e:
        flash(f"Discord database error: {e}", "error")
        session.clear()
        return redirect(url_for("home", view="login"))

    if not user:
        session.clear()
        flash("Account not found. Please login again.", "error")
        return redirect(url_for("home", view="login"))

    state = build_state(db, store, user)

    selected_topic = None
    selected_profile = None
    selected_dm_user = None
    selected_dm_messages = []

    if requested_view == "topic":
        topic_data = db["topics"].get(requested_id)

        if topic_data:
            selected_topic = public_topic(topic_data, db)

    if requested_view == "profile":
        profile_data = db["users"].get(requested_id)

        if profile_data:
            selected_profile = public_user(profile_data, db, store, user.get("id", ""))

    dm_partners_map = {}

    for dm in state["dms"]:
        other_id = dm["to"] if dm["from"] == user["id"] else dm["from"]
        other_user = db["users"].get(other_id)

        if not other_user:
            continue

        if other_id not in dm_partners_map or dm["created"] > dm_partners_map[other_id]["created"]:
            dm_partners_map[other_id] = {
                "id": other_id,
                "username": other_user.get("username", "unknown"),
                "last_body": dm["body"],
                "created": dm["created"],
            }

    dm_partners = list(dm_partners_map.values())
    dm_partners.sort(key=lambda x: x["created"], reverse=True)

    if requested_view == "dm":
        dm_user_data = db["users"].get(requested_id)

        if dm_user_data:
            selected_dm_user = public_user(dm_user_data, db, store, user.get("id", ""))

            selected_dm_messages = [
                dm for dm in state["dms"]
                if (dm["from"] == user["id"] and dm["to"] == requested_id)
                or (dm["from"] == requested_id and dm["to"] == user["id"])
            ]

            selected_dm_messages.sort(key=lambda x: x["created"])

    if requested_view == "notifications":
        user["last_seen_notifications"] = int(time.time())
        db["users"][user["id"]] = user

        try:
            save_db(db)
            clear_cache()
        except Exception:
            pass

    return render_template_string(
        HTML,
        view=requested_view,
        files=state["files"],
        topics=state["topics"],
        users=state["users"],
        dms=state["dms"],
        dm_partners=dm_partners,
        notifications=state["notifications"],
        notification_count=state["notification_count"],
        credits=CREDITS,
        user_email=user.get("email", ""),
        username=user.get("username", user.get("email", "")),
        current_user_id=user.get("id", ""),
        pfp_url=pfp_url_from_user(user, store),
        selected_topic=selected_topic,
        selected_profile=selected_profile,
        selected_dm_user=selected_dm_user,
        selected_dm_messages=selected_dm_messages,
        me=user,
        max_file_mb=MAX_FILE_SIZE // (1024 * 1024),
        post_cooldown=POST_COOLDOWN_SECONDS,
        comment_cooldown=COMMENT_COOLDOWN_SECONDS,
    )


@app.route("/register", methods=["POST"])
def register():
    session.pop("pending_register", None)
    remove_old_verification_flash()

    try:
        store = load_store(force=True)
        db = store["db"]
    except Exception as e:
        flash(f"Discord database error: {e}", "error")
        return redirect(url_for("home", view="register"))

    username = request.form.get("username", "").strip()
    email = normalize_email(request.form.get("email", ""))
    password = request.form.get("password", "")

    if not valid_username(username):
        flash("Username must be 3-20 characters and only use letters, numbers, underscore, or dot.", "error")
        return redirect(url_for("home", view="register"))

    email_ok, email_reason = validate_real_email(email)

    if not email_ok:
        flash(email_reason, "error")
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
        "created": int(time.time()),
    }

    db["users"][user_id] = user

    try:
        save_db(db)
        clear_cache()
    except Exception as e:
        flash(f"Could not save user to Discord DB: {e}", "error")
        return redirect(url_for("home", view="register"))

    session["email"] = email

    flash("Account created successfully.", "success")
    return go("dashboard")


@app.route("/login", methods=["POST"])
def login():
    session.pop("pending_register", None)
    remove_old_verification_flash()

    try:
        db = load_store(force=True)["db"]
    except Exception as e:
        flash(f"Discord database error: {e}", "error")
        return redirect(url_for("home", view="login"))

    email = normalize_email(request.form.get("email", ""))
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
    store = load_store(force=True)
    db = store["db"]
    user = db["users"].get(current_user_id())

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
        clear_cache()
    except Exception as e:
        flash(f"Could not update username: {e}", "error")
        return go("account")

    flash("Username changed successfully.", "success")
    return go("account")


@app.route("/change-about", methods=["POST"])
@login_required
def change_about():
    store = load_store(force=True)
    db = store["db"]
    user = db["users"].get(current_user_id())

    about = request.form.get("about", "").strip()

    user["about"] = about[:500]
    user["updated_at"] = int(time.time())
    db["users"][user["id"]] = user

    try:
        save_db(db)
        clear_cache()
    except Exception as e:
        flash(f"Could not update about text: {e}", "error")
        return go("account")

    flash("About text changed successfully.", "success")
    return go("account")


@app.route("/change-password", methods=["POST"])
@login_required
def change_password():
    store = load_store(force=True)
    db = store["db"]
    user = db["users"].get(current_user_id())

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
        clear_cache()
    except Exception as e:
        flash(f"Could not update password: {e}", "error")
        return go("account")

    flash("Password changed successfully.", "success")
    return go("account")


@app.route("/change-pfp", methods=["POST"])
@login_required
def change_pfp():
    store = load_store(force=True)
    db = store["db"]
    user = db["users"].get(current_user_id())

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
            content_type=uploaded.content_type,
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
        clear_cache()
    except Exception as e:
        flash(f"Profile picture uploaded, but DB update failed: {e}", "error")
        return go("account")

    flash("Profile picture changed successfully.", "success")
    return go("account")


@app.route("/upload", methods=["POST"])
@login_required
def upload():
    store = load_store(force=True)
    db = store["db"]
    user = db["users"].get(current_user_id())

    if "uploadfile" not in request.files:
        flash("No file selected.", "error")
        return go("files")

    uploaded = request.files["uploadfile"]

    if uploaded.filename == "":
        flash("No file selected.", "error")
        return go("files")

    if not allowed_file(uploaded.filename):
        flash("File type not allowed.", "error")
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

    unique, duplicate_reason = duplicate_file_check(db, original_name, file_bytes)

    if not unique:
        flash(duplicate_reason, "error")
        return go("files")

    file_id = secrets.token_hex(12)

    try:
        save_uploaded_file_to_discord(
            file_id=file_id,
            filename=original_name,
            file_bytes=file_bytes,
            content_type=uploaded.content_type,
        )
    except Exception as e:
        flash(f"Could not upload file to Discord: {e}", "error")
        return go("files")

    metadata = {
        "id": file_id,
        "original_name": original_name,
        "size": size,
        "content_type": uploaded.content_type or mime_for_name(original_name),
        "file_hash": sha256_bytes(file_bytes),
        "audio_hash": wav_audio_hash(file_bytes) if file_ext(original_name) == ".wav" else "",
        "author": user.get("username"),
        "author_id": user.get("id"),
        "created": int(time.time()),
    }
    db["files"][file_id] = metadata

    try:
        save_db(db)
        clear_cache()
    except Exception as e:
        flash(f"File uploaded, but DB update failed: {e}", "error")
        return go("files")

    flash("File uploaded successfully.", "success")
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
        mimetype=file_data.get(
            "content_type",
            mime_for_name(file_data.get("original_name", "file")),
        ),
    )


@app.route("/media/<file_id>")
@login_required
def media_file(file_id):
    try:
        store = load_store(force=True)
        db = store["db"]
        file_urls = store["file_urls"]
    except Exception:
        abort(404)

    file_data = db["files"].get(file_id)

    if not file_data:
        abort(404)

    file_url = file_urls.get(file_id, {}).get("url")

    if not file_url:
        abort(404)

    try:
        response = requests.get(file_url, timeout=60)
        response.raise_for_status()
    except Exception:
        abort(404)

    name = file_data.get("original_name", "media")
    mimetype = mime_for_name(name)

    out = send_file(
        BytesIO(response.content),
        mimetype=mimetype,
        as_attachment=False,
        download_name=name,
    )
    out.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    out.headers["Pragma"] = "no-cache"
    out.headers["Expires"] = "0"
    return out


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

    out = send_file(BytesIO(response.content), mimetype=content_type)
    out.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    out.headers["Pragma"] = "no-cache"
    out.headers["Expires"] = "0"
    return out


@app.route("/topic", methods=["POST"])
@login_required
def add_topic():
    store = load_store(force=True)
    db = store["db"]
    user = db["users"].get(current_user_id())

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
        "created": int(time.time()),
    }

    db["topics"][topic_id] = topic
    user["last_topic_at"] = int(time.time())
    db["users"][user["id"]] = user

    try:
        save_db(db)
        clear_cache()
    except Exception as e:
        flash(f"Could not save topic to Discord DB: {e}", "error")
        return go("discussion")

    flash("Topic added.", "success")
    return go("topic", topic_id)


@app.route("/comment/<topic_id>", methods=["POST"])
@login_required
@app.route("/file-comment/<file_id>", methods=["POST"])
@login_required
def add_file_comment(file_id):
    store = load_store(force=True)
    db = store["db"]
    user = db["users"].get(current_user_id())

    if file_id not in db["files"]:
        flash("File not found.", "error")
        return go("files")

    left = cooldown_left(user, "last_comment_at", COMMENT_COOLDOWN_SECONDS)

    if left > 0:
        flash(f"Slow down. You can comment again in {left} seconds.", "error")
        return go("files")

    body = request.form.get("body", "").strip()

    if not body:
        flash("Comment cannot be empty.", "error")
        return go("files")

    comment_id = secrets.token_hex(12)

    comment = {
        "id": comment_id,
        "file_id": file_id,
        "body": body[:900],
        "author": user.get("username"),
        "author_id": user.get("id"),
        "created": int(time.time()),
    }

    db["comments"][comment_id] = comment
    user["last_comment_at"] = int(time.time())
    db["users"][user["id"]] = user

    try:
        save_db(db)
        clear_cache()
    except Exception as e:
        flash(f"Could not save file comment to Discord DB: {e}", "error")
        return go("files")

    flash("Comment added under file.", "success")
    return go("files")


@app.route("/delete-topic/<topic_id>", methods=["POST"])
@login_required
def delete_topic_route(topic_id):
    store = load_store(force=True)
    db = store["db"]
    user = db["users"].get(current_user_id())
    topic = db["topics"].get(topic_id)

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
        clear_cache()
    except Exception as e:
        flash(f"Could not delete topic: {e}", "error")
        return go("topic", topic_id)

    flash("Post deleted.", "success")
    return go("discussion")


@app.route("/send-dm/<target_id>", methods=["POST"])
@login_required
def send_dm(target_id):
    store = load_store(force=True)
    db = store["db"]

    sender = db["users"].get(current_user_id())
    target = db["users"].get(target_id)

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
        "read": False,
    }

    try:
        save_db(db)
        clear_cache()
    except Exception as e:
        flash(f"Could not send message: {e}", "error")
        return go("dm", target_id)

    flash("Message sent.", "success")
    return go("dm", target_id)


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


@app.route("/recover-users")
def recover_users():
    try:
        store = load_store(force=True)
        current_db = store["db"]

        messages = fetch_discord_messages()
        snapshots = []

        for message in messages:
            content = message.get("content", "") or ""
            attachments = message.get("attachments", []) or []

            if content.startswith("SWDBSNAP|") and attachments:
                try:
                    db_url = attachments[0].get("url", "")
                    response = requests.get(db_url, timeout=45)
                    response.raise_for_status()
                    snapshot_db = normalize_db(response.json())
                    snapshots.append(snapshot_db)
                except Exception:
                    pass

        recovered = 0
        overwritten = 0

        for snap in reversed(snapshots):
            for uid, user in snap.get("users", {}).items():
                if not isinstance(user, dict):
                    continue

                email = normalize_email(user.get("email", ""))

                if not email:
                    continue

                user.pop("email_verified", None)
                user.pop("email_verified_at", None)
                user.pop("verification_code", None)
                user.pop("code_hash", None)
                user.pop("pending_register", None)

                real_uid = user_id_from_email(email)
                user["id"] = real_uid
                user["email"] = email

                if real_uid not in current_db["users"]:
                    current_db["users"][real_uid] = user
                    recovered += 1
                else:
                    existing = current_db["users"][real_uid]

                    # Keep newest password hash if it exists, but fill missing fields.
                    for k, v in user.items():
                        if k not in existing or existing.get(k) in ["", None]:
                            existing[k] = v

                    current_db["users"][real_uid] = existing
                    overwritten += 1

        save_db(current_db)
        clear_cache()

        return (
            "RECOVERY DONE<br>"
            f"Snapshots scanned: {len(snapshots)}<br>"
            f"Users recovered: {recovered}<br>"
            f"Existing users checked: {overwritten}<br>"
            f"Total users now: {len(current_db['users'])}<br>"
            "<br>Now go back and login."
        )

    except Exception as e:
        return f"RECOVERY ERROR: {e}"


@app.errorhandler(413)
def too_large(error):
    flash(f"File too large. Max size is {MAX_FILE_SIZE // (1024 * 1024)} MB.", "error")
    return go("files")


@app.errorhandler(404)
def not_found(error):
    return redirect(url_for("home"))


if __name__ == "__main__":
    app.run(debug=True)

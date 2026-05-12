import os
import re
import json
import gzip
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
    jsonify,
)
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename

try:
    from PIL import Image, ImageOps
except Exception:
    Image = None
    ImageOps = None


app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", secrets.token_hex(64))

DISCORD_BOT_TOKEN = os.environ.get("DISCORD_BOT_TOKEN", "").strip()
DISCORD_DB_CHANNEL_ID = os.environ.get("DISCORD_DB_CHANNEL_ID", "").strip()
DISCORD_API = "https://discord.com/api/v10"


MAX_FILE_SIZE = int(os.environ.get("MAX_FILE_SIZE", str(8 * 1024 * 1024)))
MAX_DB_SIZE = int(os.environ.get("MAX_DB_SIZE", str(7 * 1024 * 1024)))
POST_COOLDOWN_SECONDS = int(os.environ.get("POST_COOLDOWN_SECONDS", "30"))
COMMENT_COOLDOWN_SECONDS = int(os.environ.get("COMMENT_COOLDOWN_SECONDS", "8"))
UPLOAD_COOLDOWN_SECONDS = int(os.environ.get("UPLOAD_COOLDOWN_SECONDS", "60"))
PROFILE_CHANGE_COOLDOWN_SECONDS = int(os.environ.get("PROFILE_CHANGE_COOLDOWN_SECONDS", "600"))
PASSWORD_CHANGE_COOLDOWN_SECONDS = int(os.environ.get("PASSWORD_CHANGE_COOLDOWN_SECONDS", "600"))
USERNAME_COOLDOWN_SECONDS = int(os.environ.get("USERNAME_COOLDOWN_SECONDS", str(5 * 24 * 60 * 60)))
NOTIFICATION_SAVE_COOLDOWN_SECONDS = int(os.environ.get("NOTIFICATION_SAVE_COOLDOWN_SECONDS", "60"))
IMAGE_RESIZE_FACTOR = float(os.environ.get("IMAGE_RESIZE_FACTOR", "0.95"))
IMAGE_JPEG_QUALITY = int(os.environ.get("IMAGE_JPEG_QUALITY", "92"))
IMAGE_WEBP_QUALITY = int(os.environ.get("IMAGE_WEBP_QUALITY", "90"))

app.config["MAX_CONTENT_LENGTH"] = MAX_FILE_SIZE

ALLOWED_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
ALLOWED_EXTENSIONS = {".zip", ".mp3"} | ALLOWED_IMAGE_EXTENSIONS
ALLOWED_PFP_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif"}

DANGEROUS_ZIP_EXTENSIONS = {
    ".exe", ".bat", ".cmd", ".scr", ".ps1", ".vbs", ".js", ".jar",
    ".msi", ".dll", ".com", ".pif", ".lnk", ".reg", ".hta", ".apk",
    ".sh", ".command", ".app"
}

EMAIL_REGEX = re.compile(
    r"^(?=.{6,254}$)(?=.{1,64}@)[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@"
    r"[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+$"
)

BLOCKED_EMAIL_DOMAINS = {
    # Obvious fake/test domains
    "example.com", "example.org", "example.net", "test.com", "fake.com",

    # Temporary/disposable email domains
    "mailinator.com", "10minutemail.com", "10minutemail.net", "10minutemail.org",
    "guerrillamail.com", "guerrillamail.net", "guerrillamail.org",
    "temp-mail.org", "tempmail.com", "tempmail.net", "tempmailo.com",
    "throwawaymail.com", "yopmail.com", "sharklasers.com", "getnada.com",
    "trashmail.com", "dispostable.com", "maildrop.cc", "moakt.com",
    "emailondeck.com", "mintemail.com", "mytemp.email", "tempail.com",
    "fakeinbox.com", "spamgourmet.com", "burnermail.io", "mail.tm",
    "inboxkitten.com", "tempmail.plus", "tmail.io", "fakemail.net",
    "fakemailgenerator.com", "dropmail.me", "33mail.com", "mvrht.com",
    "mailnesia.com", "mailcatch.com", "mailforspam.com", "spambog.com",
    "trash-mail.com", "tempm.com", "temporary-mail.net", "tempmailaddress.com",
    "mohmal.com", "emailfake.com", "fexpost.com", "fexbox.org", "fextemp.com",
    "tmpmail.org", "tmpmail.net", "minuteinbox.com", "emailtemporanea.com",
    "tempinbox.com", "instant-email.org", "spam4.me", "inboxbear.com",

    # Privacy/alias providers you asked to block for this community.
    # Remove these lines if you later decide to allow normal privacy emails.
    "proton.me", "protonmail.com", "pm.me",
    "simplelogin.com", "simplelogin.io", "aleeas.com", "slmail.me",
    "duck.com", "addy.io", "anonaddy.com", "tuta.com", "tutanota.com",
}

# Exact emails and suspicious local-parts that should be removed if they appear in the DB.
BLOCKED_EMAIL_ADDRESSES = {
    "flowznsucks@proton.me",
}

BLOCKED_EMAIL_LOCAL_KEYWORDS = {
    "flowznsucks",
}

# Optional Render env vars:
# EXTRA_BLOCKED_EMAIL_DOMAINS=domain1.com,domain2.com
# EXTRA_BLOCKED_EMAIL_ADDRESSES=user@example.com,user2@example.com
for _domain in os.environ.get("EXTRA_BLOCKED_EMAIL_DOMAINS", "").split(","):
    _domain = _domain.strip().lower()
    if _domain:
        BLOCKED_EMAIL_DOMAINS.add(_domain)

for _email in os.environ.get("EXTRA_BLOCKED_EMAIL_ADDRESSES", "").split(","):
    _email = _email.strip().lower()
    if _email:
        BLOCKED_EMAIL_ADDRESSES.add(_email)

# Speed settings. The old version scanned your whole Discord DB channel very often.
# This version only grabs the latest DB snapshot for normal page loads.
CACHE_SECONDS = int(os.environ.get("CACHE_SECONDS", "180"))
FAST_BOOT_MESSAGE_PAGES = int(os.environ.get("FAST_BOOT_MESSAGE_PAGES", "3"))
ATTACHMENT_CACHE_SECONDS = int(os.environ.get("ATTACHMENT_CACHE_SECONDS", "1800"))
LIVE_POLL_MS = int(os.environ.get("LIVE_POLL_MS", "30000"))

CACHE = {"time": 0, "store": None}
ATTACHMENT_CACHE = {"items": {}}

CREDITS = {
    "OWNERS": ["DJ TUTTER", "DJ LIRA DA ZL"],
    "MEMBERS": ["DJ FRG 011", "DJ PLT 011", "DJ RGLX", "DJ RDC", "DJ SABA 7", "DJ RE7 013", "RSFI", "DJ RDC"],
    "WEBSITE MADE BY": ["DJ SABA 7"],
}


def now_ms():
    return int(time.time() * 1000)


def blank_db():
    return {
        "version": 15,
        "users": {},
        "topics": {},
        "comments": {},
        "file_comments": {},
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

    for key in ["users", "topics", "comments", "file_comments", "files", "dms"]:
        if not isinstance(clean.get(key), dict):
            clean[key] = {}

    clean["version"] = 15
    clean.setdefault("created_at", now_ms())
    clean.setdefault("updated_at", now_ms())
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
        r = requests.request(method, url, headers=headers, timeout=45, **kwargs)

        if r.status_code == 429:
            try:
                retry_after = float(r.json().get("retry_after", 1))
            except Exception:
                retry_after = 1
            time.sleep(retry_after)
            continue

        if not (200 <= r.status_code < 300):
            raise RuntimeError(f"Discord API error {r.status_code}: {r.text[:700]}")

        return r

    raise RuntimeError("Discord API rate-limit retry failed.")


def clear_cache():
    CACHE["time"] = 0
    CACHE["store"] = None


def attachment_name_key(filename):
    return secure_filename(filename or "").strip().lower()


def attachment_info_from_discord(a):
    return {
        "url": a.get("url", ""),
        "proxy_url": a.get("proxy_url", ""),
        "filename": a.get("filename", ""),
        "size": a.get("size", 0),
        "content_type": a.get("content_type", ""),
    }


def best_attachment_url(info):
    if not info:
        return ""
    return info.get("url") or info.get("proxy_url") or ""


def load_db_snapshot_bytes(raw, filename=""):
    """
    Supports both old plain JSON snapshots and new gzip-compressed snapshots.
    New saves use smartweb-db.json.gz to send less data to Discord.
    """
    if not raw:
        return blank_db()

    filename = (filename or "").lower()

    try:
        if filename.endswith(".gz") or raw[:2] == b"\x1f\x8b":
            raw = gzip.decompress(raw)

        return normalize_db(json.loads(raw.decode("utf-8")))
    except Exception:
        return blank_db()


def quick_normalize_email(email):
    return (email or "").strip().lower()


def quick_email_domain(email):
    email = quick_normalize_email(email)
    if "@" not in email:
        return ""
    return email.rsplit("@", 1)[1].strip().lower()


def blocked_email_reason(email):
    """
    Returns a human-readable reason if the email must be blocked/deleted.
    This is used before normal validation too, so blocked old accounts get purged.
    """
    email = quick_normalize_email(email)
    if not email:
        return "Email is empty."

    if email in BLOCKED_EMAIL_ADDRESSES:
        return "This exact email address is blocked."

    local = email.split("@", 1)[0].lower() if "@" in email else email.lower()
    for word in BLOCKED_EMAIL_LOCAL_KEYWORDS:
        word = (word or "").strip().lower()
        if word and word in local:
            return "This email name is blocked."

    domain = quick_email_domain(email)
    if not domain:
        return "Invalid email format."

    for blocked in BLOCKED_EMAIL_DOMAINS:
        blocked = (blocked or "").strip().lower()
        if blocked and (domain == blocked or domain.endswith("." + blocked)):
            return "Temporary, fake, privacy, or blocked email domains are not allowed."

    return ""


def email_is_blocked(email):
    return bool(blocked_email_reason(email))


def delete_user_and_content(db, uid):
    """
    Deletes the account and hides its site data from the DB.
    Discord attachment messages are not deleted here, but their DB entries are removed,
    so they stop showing on the website.
    """
    db = normalize_db(db)
    uid = str(uid or "")
    deleted = {
        "users": 0,
        "topics": 0,
        "comments": 0,
        "file_comments": 0,
        "files": 0,
        "dms": 0,
    }

    if uid in db["users"]:
        db["users"].pop(uid, None)
        deleted["users"] += 1

    deleted_topic_ids = []
    for topic_id, topic in list(db["topics"].items()):
        if topic.get("author_id") == uid:
            deleted_topic_ids.append(topic_id)
            db["topics"].pop(topic_id, None)
            deleted["topics"] += 1

    deleted_file_ids = []
    for file_id, file_data in list(db["files"].items()):
        if file_data.get("author_id") == uid:
            deleted_file_ids.append(file_id)
            db["files"].pop(file_id, None)
            deleted["files"] += 1

    for comment_id, comment in list(db["comments"].items()):
        if comment.get("author_id") == uid or comment.get("topic_id") in deleted_topic_ids:
            db["comments"].pop(comment_id, None)
            deleted["comments"] += 1

    for comment_id, comment in list(db["file_comments"].items()):
        if comment.get("author_id") == uid or comment.get("file_id") in deleted_file_ids:
            db["file_comments"].pop(comment_id, None)
            deleted["file_comments"] += 1

    for dm_id, dm in list(db["dms"].items()):
        if dm.get("from") == uid or dm.get("to") == uid:
            db["dms"].pop(dm_id, None)
            deleted["dms"] += 1

    db["updated_at"] = now_ms()
    return deleted


def purge_blocked_email_accounts(db):
    """
    Deletes every user in the DB whose saved email is now blocked.
    Called when the newest DB snapshot is loaded and before each DB save.
    """
    db = normalize_db(db)
    deleted_users = []

    for uid, user in list(db["users"].items()):
        email = quick_normalize_email(user.get("email", ""))
        reason = blocked_email_reason(email)
        if reason:
            deleted_users.append({
                "id": uid,
                "email": email,
                "username": user.get("username", "unknown"),
                "reason": reason,
            })
            delete_user_and_content(db, uid)

    if deleted_users:
        db["last_blocked_email_purge_at"] = int(time.time())
        db["last_blocked_email_purge_count"] = len(deleted_users)
        db["updated_at"] = now_ms()

    return deleted_users


def format_cooldown(seconds):
    seconds = max(0, int(seconds))
    days = seconds // 86400
    hours = (seconds % 86400) // 3600
    minutes = (seconds % 3600) // 60

    if days > 0:
        return f"{days}d {hours}h"
    if hours > 0:
        return f"{hours}h {minutes}m"
    if minutes > 0:
        return f"{minutes}m"
    return f"{seconds}s"


def image_content_type(filename, fallback="image/png"):
    ext = os.path.splitext((filename or "").lower())[1]
    if ext in [".jpg", ".jpeg"]:
        return "image/jpeg"
    if ext == ".webp":
        return "image/webp"
    if ext == ".gif":
        return "image/gif"
    if ext == ".png":
        return "image/png"
    return fallback or "application/octet-stream"


def compress_image_for_discord(filename, file_bytes, content_type=""):
    """
    Light image compression for Discord storage.
    It resizes normal images to 95% width/height, which is about 10% less pixel data,
    then saves with optimization. Animated GIFs are kept unchanged to avoid breaking them.
    """
    info = {
        "compressed": False,
        "original_size": len(file_bytes or b""),
        "compressed_size": len(file_bytes or b""),
        "saved_bytes": 0,
    }

    ext = os.path.splitext((filename or "").lower())[1]

    if ext not in ALLOWED_IMAGE_EXTENSIONS:
        return file_bytes, content_type or "application/octet-stream", info

    if Image is None or ImageOps is None:
        return file_bytes, content_type or image_content_type(filename), info

    if ext == ".gif":
        # Keep GIFs untouched so animated profile pictures / posts do not break.
        return file_bytes, content_type or "image/gif", info

    try:
        with Image.open(BytesIO(file_bytes)) as img:
            if getattr(img, "is_animated", False):
                return file_bytes, content_type or image_content_type(filename), info

            img = ImageOps.exif_transpose(img)
            width, height = img.size

            if width < 64 or height < 64:
                return file_bytes, content_type or image_content_type(filename), info

            factor = max(0.50, min(1.0, IMAGE_RESIZE_FACTOR))
            new_width = max(1, int(width * factor))
            new_height = max(1, int(height * factor))

            if new_width != width or new_height != height:
                img = img.resize((new_width, new_height), Image.Resampling.LANCZOS)

            out = BytesIO()

            if ext in [".jpg", ".jpeg"]:
                if img.mode not in ["RGB", "L"]:
                    img = img.convert("RGB")
                img.save(out, format="JPEG", quality=IMAGE_JPEG_QUALITY, optimize=True, progressive=True)
                new_type = "image/jpeg"
            elif ext == ".webp":
                if img.mode not in ["RGB", "RGBA"]:
                    img = img.convert("RGBA")
                img.save(out, format="WEBP", quality=IMAGE_WEBP_QUALITY, method=6)
                new_type = "image/webp"
            else:
                # PNG keeps transparency and uses lossless optimization.
                if img.mode not in ["RGB", "RGBA", "L", "LA", "P"]:
                    img = img.convert("RGBA")
                img.save(out, format="PNG", optimize=True, compress_level=9)
                new_type = "image/png"

            compressed = out.getvalue()

            # Only replace the upload if it is actually smaller.
            if 0 < len(compressed) < len(file_bytes):
                info["compressed"] = True
                info["compressed_size"] = len(compressed)
                info["saved_bytes"] = len(file_bytes) - len(compressed)
                return compressed, new_type, info

    except Exception:
        pass

    return file_bytes, content_type or image_content_type(filename), info


def fetch_discord_messages(max_pages=60, stop_after_snapshot=False):
    all_messages = []
    before = None

    for _ in range(max_pages):
        params = {"limit": 100}
        if before:
            params["before"] = before

        r = discord_request("GET", f"/channels/{DISCORD_DB_CHANNEL_ID}/messages", params=params)
        messages = r.json()

        if not messages:
            break

        all_messages.extend(messages)
        before = messages[-1]["id"]

        # For normal website loads we only need the newest DB snapshot.
        # This avoids scanning thousands of Discord messages every page load.
        if stop_after_snapshot and any((m.get("content", "") or "").startswith("SWDBSNAP|") for m in messages):
            break

        if len(messages) < 100:
            break

    return all_messages


def post_discord_text(content):
    r = discord_request(
        "POST",
        f"/channels/{DISCORD_DB_CHANNEL_ID}/messages",
        headers={"Content-Type": "application/json"},
        json={"content": content},
    )
    clear_cache()
    return r.json()


def post_discord_attachment(content, filename, file_bytes, content_type):
    payload = {"content": content}
    data = {"payload_json": json.dumps(payload)}
    files = {"files[0]": (filename, BytesIO(file_bytes), content_type or "application/octet-stream")}

    r = discord_request("POST", f"/channels/{DISCORD_DB_CHANNEL_ID}/messages", data=data, files=files)
    clear_cache()
    return r.json()


def cache_attachment(kind, key, info):
    if not key or not info or not best_attachment_url(info):
        return
    ATTACHMENT_CACHE["items"][(kind, str(key))] = {
        "time": time.time(),
        "info": info,
    }


def get_cached_attachment(kind, key):
    if not key:
        return None
    item = ATTACHMENT_CACHE["items"].get((kind, str(key)))
    if not item:
        return None
    if time.time() - float(item.get("time", 0)) > ATTACHMENT_CACHE_SECONDS:
        ATTACHMENT_CACHE["items"].pop((kind, str(key)), None)
        return None
    return item.get("info")


def load_store(force=False):
    now = time.time()
    if not force and CACHE["store"] is not None and now - CACHE["time"] < CACHE_SECONDS:
        return CACHE["store"]

    # Fast path: only scan the newest few Discord pages until the latest DB snapshot is found.
    # The old code scanned up to 60 pages on every load and every live update.
    messages = fetch_discord_messages(max_pages=FAST_BOOT_MESSAGE_PAGES, stop_after_snapshot=True)
    messages.sort(key=lambda m: int(m.get("id", 0)), reverse=True)

    db = blank_db()
    file_urls = {}
    file_urls_by_name = {}
    pfp_urls = {}
    snapshot_loaded = False

    for msg in messages:
        content = msg.get("content", "") or ""
        attachments = msg.get("attachments", []) or []
        msg_id = msg.get("id", "")

        for a in attachments:
            info = attachment_info_from_discord(a)
            info["message_id"] = msg_id
            name_key = attachment_name_key(info.get("filename", ""))
            if name_key and name_key not in file_urls_by_name:
                file_urls_by_name[name_key] = info
                cache_attachment("name", name_key, info)

        if content.startswith("SWFILE|"):
            file_id = content.split("|", 1)[1].strip()
            if file_id and attachments:
                info = attachment_info_from_discord(attachments[0])
                info["message_id"] = msg_id
                file_urls[file_id] = info
                cache_attachment("file", file_id, info)

        elif content.startswith("SWPFP|"):
            pfp_id = content.split("|", 1)[1].strip()
            if pfp_id and attachments:
                info = attachment_info_from_discord(attachments[0])
                info["message_id"] = msg_id
                pfp_urls[pfp_id] = info
                cache_attachment("pfp", pfp_id, info)

        elif content.startswith("SWDBSNAP|") and not snapshot_loaded and attachments:
            try:
                db_url = attachments[0].get("url", "")
                r = requests.get(db_url, timeout=20)
                r.raise_for_status()
                db = load_db_snapshot_bytes(r.content, attachments[0].get("filename", ""))
                snapshot_loaded = True
            except Exception:
                pass

    db = normalize_db(db)

    # IMPORTANT SPEED/SPAM FIX:
    # Normal page loads and live updates must NEVER write a new DB snapshot to Discord.
    # The old version auto-purged blocked emails inside load_store(), so simply opening
    # the website could create repeated SWDBSNAP messages. Purging is now only done
    # on real write actions, login/register of a blocked user, or the manual purge URL.
    purged_blocked_accounts = []

    store = {
        "db": db,
        "purged_blocked_accounts": purged_blocked_accounts,
        "file_urls": file_urls,
        "file_urls_by_name": file_urls_by_name,
        "pfp_urls": pfp_urls,
        "message_count": len(messages),
        "snapshot_loaded": snapshot_loaded,
    }

    CACHE["time"] = now
    CACHE["store"] = store
    return store


def save_db(db):
    db = normalize_db(db)
    purge_blocked_email_accounts(db)
    db["updated_at"] = now_ms()

    raw_json = json.dumps(db, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    raw = gzip.compress(raw_json, compresslevel=6)

    if len(raw) > MAX_DB_SIZE:
        raise ValueError("Discord DB snapshot is too large even after gzip compression. Delete old data or raise MAX_DB_SIZE.")

    post_discord_attachment(
        content=f"SWDBSNAP|v16quiet|gz|{int(time.time())}",
        filename="smartweb-db.json.gz",
        file_bytes=raw,
        content_type="application/gzip",
    )
    clear_cache()


def save_uploaded_file_to_discord(file_id, filename, file_bytes, content_type):
    return post_discord_attachment(
        content=f"SWFILE|{file_id}",
        filename=filename,
        file_bytes=file_bytes,
        content_type=content_type or "application/octet-stream",
    )


def save_profile_picture_to_discord(pfp_id, filename, file_bytes, content_type):
    return post_discord_attachment(
        content=f"SWPFP|{pfp_id}",
        filename=filename,
        file_bytes=file_bytes,
        content_type=content_type or "application/octet-stream",
    )


def discord_message_attachment_info(message_id):
    cached = get_cached_attachment("message", message_id)
    if cached:
        return cached

    if not message_id:
        return None

    try:
        r = discord_request("GET", f"/channels/{DISCORD_DB_CHANNEL_ID}/messages/{message_id}")
        msg = r.json()
        attachments = msg.get("attachments", []) or []
        if not attachments:
            return None
        info = attachment_info_from_discord(attachments[0])
        info["message_id"] = message_id
        cache_attachment("message", message_id, info)
        return info
    except Exception:
        return None


def slow_find_attachment(prefix, target_id=None, filename=None):
    # Old data may not have the Discord message id saved in the DB.
    # This slow scan is only used as a fallback and then cached.
    name_key = attachment_name_key(filename or "")
    messages = fetch_discord_messages(max_pages=60, stop_after_snapshot=False)

    for msg in messages:
        content = msg.get("content", "") or ""
        attachments = msg.get("attachments", []) or []
        if not attachments:
            continue

        info = attachment_info_from_discord(attachments[0])
        info["message_id"] = msg.get("id", "")
        msg_name_key = attachment_name_key(info.get("filename", ""))

        if target_id and content.startswith(prefix + "|"):
            found_id = content.split("|", 1)[1].strip()
            if found_id == target_id:
                cache_attachment("file" if prefix == "SWFILE" else "pfp", target_id, info)
                if msg_name_key:
                    cache_attachment("name", msg_name_key, info)
                return info

        if name_key and msg_name_key == name_key:
            cache_attachment("name", name_key, info)
            return info

    return None


def find_file_attachment_info(file_id, file_data, store):
    if not isinstance(file_data, dict):
        file_data = {}

    # New fast DB format: saved Discord message id from the upload message.
    msg_id = file_data.get("discord_message_id", "")
    info = discord_message_attachment_info(msg_id)
    if best_attachment_url(info):
        cache_attachment("file", file_id, info)
        return info

    # Fast in-memory cache.
    info = get_cached_attachment("file", file_id)
    if best_attachment_url(info):
        return info

    # Recent messages scanned during normal DB load.
    info = store.get("file_urls", {}).get(file_id)
    if best_attachment_url(info):
        cache_attachment("file", file_id, info)
        return info

    # DB may contain an attachment URL from upload time. This is very fast.
    # If it ever expires, fallback scan below still supports old files after refresh.
    if file_data.get("attachment_url") or file_data.get("attachment_proxy_url") or file_data.get("url") or file_data.get("proxy_url"):
        return {
            "url": file_data.get("attachment_url", "") or file_data.get("url", ""),
            "proxy_url": file_data.get("attachment_proxy_url", "") or file_data.get("proxy_url", ""),
            "filename": file_data.get("attachment_filename", "") or file_data.get("original_name", ""),
            "size": file_data.get("size", 0),
            "content_type": file_data.get("content_type", ""),
            "message_id": msg_id,
        }

    # Fallback by exact original filename.
    original_name = file_data.get("original_name", "")
    name_key = attachment_name_key(original_name)

    info = get_cached_attachment("name", name_key)
    if best_attachment_url(info):
        return info

    info = store.get("file_urls_by_name", {}).get(name_key)
    if best_attachment_url(info):
        cache_attachment("name", name_key, info)
        return info

    return slow_find_attachment("SWFILE", target_id=file_id, filename=original_name)


def find_pfp_attachment_info(pfp_id, user, store):
    if not pfp_id:
        return None

    msg_id = user.get("pfp_discord_message_id", "") if isinstance(user, dict) else ""
    info = discord_message_attachment_info(msg_id)
    if best_attachment_url(info):
        cache_attachment("pfp", pfp_id, info)
        return info

    info = get_cached_attachment("pfp", pfp_id)
    if best_attachment_url(info):
        return info

    info = store.get("pfp_urls", {}).get(pfp_id)
    if best_attachment_url(info):
        cache_attachment("pfp", pfp_id, info)
        return info

    if isinstance(user, dict) and (user.get("pfp_attachment_url") or user.get("pfp_attachment_proxy_url")):
        return {
            "url": user.get("pfp_attachment_url", ""),
            "proxy_url": user.get("pfp_attachment_proxy_url", ""),
            "filename": user.get("pfp_name", "profile.png"),
            "size": 0,
            "content_type": "image/png",
            "message_id": msg_id,
        }

    return slow_find_attachment("SWPFP", target_id=pfp_id, filename=user.get("pfp_name", "") if isinstance(user, dict) else "")


def normalize_email(email):
    return (email or "").strip().lower()


def email_domain_is_blocked(domain):
    domain = (domain or "").lower().strip()
    for blocked in BLOCKED_EMAIL_DOMAINS:
        blocked = (blocked or "").lower().strip()
        if blocked and (domain == blocked or domain.endswith("." + blocked)):
            return True
    return False


def email_domain_format_ok(domain):
    if not domain or "." not in domain or len(domain) > 253:
        return False

    labels = domain.split(".")
    for label in labels:
        if not label or len(label) > 63:
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


def validate_email_basic(email):
    email = normalize_email(email)

    blocked_reason = blocked_email_reason(email)
    if blocked_reason:
        return False, blocked_reason

    if not EMAIL_REGEX.fullmatch(email):
        return False, "Invalid email format."

    local, domain = email.rsplit("@", 1)

    if ".." in local or local.startswith(".") or local.endswith("."):
        return False, "Invalid email format."

    if not email_domain_format_ok(domain):
        return False, "Invalid email domain."

    return True, "Email accepted."


def user_id_from_email(email):
    return hashlib.sha256(email.strip().lower().encode("utf-8")).hexdigest()


def find_user_by_email(db, email):
    """
    Finds accounts by the saved email value instead of only trusting the user dict key.
    This fixes old Discord snapshots where the user id/key was generated differently.
    """
    email = normalize_email(email)
    if not email:
        return None, None

    for uid, user in db.get("users", {}).items():
        if normalize_email(user.get("email", "")) == email:
            if not user.get("id"):
                user["id"] = uid
            return uid, user

    legacy_id = user_id_from_email(email)
    user = db.get("users", {}).get(legacy_id)
    if user:
        if not user.get("id"):
            user["id"] = legacy_id
        return legacy_id, user

    return None, None


def password_matches(saved_hash, password):
    if not saved_hash:
        return False

    try:
        return check_password_hash(saved_hash, password)
    except Exception:
        # Legacy fallback only: if an old test DB stored plain text by mistake.
        return saved_hash == password


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
    return max(0, seconds - (now - last))


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
        if not zipfile.is_zipfile(BytesIO(file_bytes)):
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

    if ext in ALLOWED_IMAGE_EXTENSIONS:
        if not looks_like_image(filename, file_bytes):
            return False, "This does not look like a real image file."
        return True, "Image passed basic safety check."

    return False, "Only ZIP, MP3, PNG, JPG, JPEG, WEBP and GIF files are allowed."


def scan_profile_picture(filename, file_bytes):
    if not looks_like_image(filename, file_bytes):
        return False, "This does not look like a real image file."
    return True, "Image passed basic safety check."


def current_email():
    return session.get("email")


def current_user_id():
    if session.get("user_id"):
        return session.get("user_id")

    email = current_email()
    if not email:
        return None

    try:
        db = load_store()["db"]
        uid, user = find_user_by_email(db, email)
        if uid:
            session["user_id"] = uid
            return uid
    except Exception:
        pass

    return user_id_from_email(email)


def current_user():
    uid = current_user_id()
    if not uid:
        return None

    db = load_store()["db"]
    user = db["users"].get(uid)
    if user:
        return user

    uid, user = find_user_by_email(db, current_email())
    if uid and user:
        session["user_id"] = uid
        return user

    return None


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

    # Do not scan Discord here. Just return the route; /pfp resolves the attachment lazily.
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
            flash("Account not found in the Discord database. Please login again.", "error")
            return redirect(url_for("home", view="login"))

        blocked_reason = blocked_email_reason(user.get("email", current_email()))
        if blocked_reason:
            try:
                store = load_store(force=True)
                db = store["db"]
                uid = user.get("id") or current_user_id()
                delete_user_and_content(db, uid)
                save_db(db)
            except Exception:
                pass
            session.clear()
            flash("This email is blocked. The account was deleted.", "error")
            return redirect(url_for("home", view="register"))

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
    ext = os.path.splitext(name.lower())[1]
    file_id = file_data.get("id", "")
    viewer_id = current_user_id()

    file_comments = [c for c in db.get("file_comments", {}).values() if c.get("file_id") == file_id]
    file_comments.sort(key=lambda c: int(c.get("created", 0)))

    is_audio = ext == ".mp3"
    is_image = ext in ALLOWED_IMAGE_EXTENSIONS

    return {
        "id": file_id,
        "name": name,
        "size": size_text(file_data.get("size", 0)),
        "author": username_from_id(file_data.get("author_id", ""), db, file_data.get("author", "unknown")),
        "author_id": file_data.get("author_id", ""),
        "created": int(file_data.get("created", 0)),
        "is_audio": is_audio,
        "is_image": is_image,
        "stream_url": url_for("stream_file", file_id=file_id) if is_audio else "",
        "preview_url": url_for("preview_file", file_id=file_id) if is_image else "",
        "can_delete": viewer_id == file_data.get("author_id", ""),
        "comments": [
            {
                "id": c.get("id", ""),
                "body": c.get("body", ""),
                "author": username_from_id(c.get("author_id", ""), db, c.get("author", "unknown")),
                "author_id": c.get("author_id", ""),
                "created": int(c.get("created", 0)),
            }
            for c in file_comments
        ],
    }

def public_topic(topic_data, db=None):
    if db is None:
        db = load_store()["db"]

    topic_id = topic_data.get("id", "")
    author_id = topic_data.get("author_id", "")
    viewer_id = current_user_id()

    topic_comments = [c for c in db["comments"].values() if c.get("topic_id") == topic_id]
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
    return {
        "id": uid,
        "username": user.get("username", "unknown"),
        "email": user.get("email", "") if uid == viewer_id else "",
        "about": user.get("about", ""),
        "joined": int(user.get("created", 0)),
        "pfp_url": pfp_url_from_user(user, store),
        "topic_count": len([t for t in db["topics"].values() if t.get("author_id") == uid]),
        "comment_count": len([c for c in db["comments"].values() if c.get("author_id") == uid]) + len([c for c in db.get("file_comments", {}).values() if c.get("author_id") == uid]),
        "file_count": len([f for f in db["files"].values() if f.get("author_id") == uid]),
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
                "created": int(dm.get("created", 0)),
            })

    messages.sort(key=lambda x: x["created"])
    return messages


def public_notifications(db, current_id):
    user = db["users"].get(current_id, {})
    # Read status is session-based to avoid saving a new Discord DB snapshot every time
    # someone opens the notifications page.
    try:
        session_seen = int(session.get("notifications_seen_at", 0) or 0)
    except Exception:
        session_seen = 0
    last_seen = max(int(user.get("last_seen_notifications", 0)), session_seen)
    items = []

    for dm in db["dms"].values():
        created = int(dm.get("created", 0))
        if dm.get("to") == current_id and dm.get("from") != current_id:
            sender = db["users"].get(dm.get("from", ""), {})
            if sender:
                items.append({
                    "id": dm.get("id", ""),
                    "type": "dm",
                    "title": f"New message from {sender.get('username', 'unknown')}",
                    "body": dm.get("body", "")[:160],
                    "from_id": dm.get("from", ""),
                    "target_id": dm.get("from", ""),
                    "created": created,
                    "unread": created > last_seen,
                })

    my_topic_ids = {topic_id for topic_id, topic in db["topics"].items() if topic.get("author_id") == current_id}

    for comment in db["comments"].values():
        created = int(comment.get("created", 0))
        commenter_id = comment.get("author_id", "")

        if comment.get("topic_id") in my_topic_ids and commenter_id != current_id:
            commenter = db["users"].get(commenter_id, {})
            topic = db["topics"].get(comment.get("topic_id", ""), {})

            if commenter:
                items.append({
                    "id": comment.get("id", ""),
                    "type": "comment",
                    "title": f"{commenter.get('username', 'unknown')} commented on your post",
                    "body": topic.get("title", "discussion"),
                    "from_id": commenter_id,
                    "target_id": comment.get("topic_id", ""),
                    "created": created,
                    "unread": created > last_seen,
                })

    my_file_ids = {file_id for file_id, file_data in db["files"].items() if file_data.get("author_id") == current_id}

    for comment in db.get("file_comments", {}).values():
        created = int(comment.get("created", 0))
        commenter_id = comment.get("author_id", "")

        if comment.get("file_id") in my_file_ids and commenter_id != current_id:
            commenter = db["users"].get(commenter_id, {})
            file_data = db["files"].get(comment.get("file_id", ""), {})

            if commenter:
                items.append({
                    "id": comment.get("id", ""),
                    "type": "file_comment",
                    "title": f"{commenter.get('username', 'unknown')} commented on your file",
                    "body": file_data.get("original_name", "file"),
                    "from_id": commenter_id,
                    "target_id": comment.get("file_id", ""),
                    "created": created,
                    "unread": created > last_seen,
                })

    for topic in db["topics"].values():
        created = int(topic.get("created", 0))
        author_id = topic.get("author_id", "")
        author = db["users"].get(author_id, {})
        if author_id != current_id and author:
            items.append({
                "id": topic.get("id", ""),
                "type": "topic",
                "title": f"{author.get('username', 'unknown')} made a new post",
                "body": topic.get("title", "")[:160],
                "from_id": author_id,
                "target_id": topic.get("id", ""),
                "created": created,
                "unread": created > last_seen,
            })

    for file_data in db["files"].values():
        created = int(file_data.get("created", 0))
        author_id = file_data.get("author_id", "")
        author = db["users"].get(author_id, {})
        if author_id != current_id and author:
            items.append({
                "id": file_data.get("id", ""),
                "type": "file",
                "title": f"{author.get('username', 'unknown')} uploaded a file",
                "body": file_data.get("original_name", "file")[:160],
                "from_id": author_id,
                "target_id": file_data.get("id", ""),
                "created": created,
                "unread": created > last_seen,
            })

    items.sort(key=lambda x: x["created"], reverse=True)
    return items[:50]


def build_client_state(db, store, user):
    current_id = user.get("id", "")

    visible_users = {
        uid: data for uid, data in db["users"].items()
        if isinstance(data, dict) and data.get("email") and not blocked_email_reason(data.get("email", ""))
    }

    visible_db = dict(db)
    visible_db["users"] = visible_users

    files = [public_file(file_data, visible_db) for file_data in db["files"].values()]
    files.sort(key=lambda x: x["created"], reverse=True)

    topics = [public_topic(topic_data, visible_db) for topic_data in db["topics"].values()]
    topics.sort(key=lambda x: x["created"], reverse=True)

    public_users = [public_user(user_data, visible_db, store, current_id) for user_data in visible_users.values()]
    public_users.sort(key=lambda x: x["username"].lower())

    notifications = public_notifications(db, current_id)

    return {
        "files": files,
        "topics": topics,
        "users": public_users,
        "dm_messages": public_dm_messages(db, current_id),
        "notifications": notifications,
        "notification_count": len([n for n in notifications if n.get("unread")]),
        "db_updated_at": int(db.get("updated_at", 0)),
        "username": user.get("username", user.get("email", "")),
        "pfp_url": pfp_url_from_user(user, store),
    }


HTML = """
<!DOCTYPE html>
<html>
<head>
<title>producer-room</title>
<meta name="viewport" content="width=device-width, initial-scale=1">

<style>
*{box-sizing:border-box;border-radius:0!important}
:root{
    --bg:#060b10;--bg2:#09131b;--text:#f5f7fa;
    --muted:rgba(245,247,250,.62);--line:rgba(255,255,255,.16);
    --line2:rgba(255,255,255,.08);--blue:#9fe7ff;
    --danger:#ff5c6c;--good:#55e68a;
}
html,body{margin:0;min-height:100vh}
body{
    color:var(--text);
    font-family:Arial,Helvetica,sans-serif;
    background:
        linear-gradient(rgba(255,255,255,.025) 1px, transparent 1px),
        linear-gradient(90deg, rgba(255,255,255,.025) 1px, transparent 1px),
        linear-gradient(135deg, var(--bg), var(--bg2));
    background-size:24px 24px,24px 24px,100% 100%;
    overflow:hidden;
}
.app{height:100vh;display:flex}
.side{
    width:260px;padding:30px 24px;border-right:1px solid var(--line);
    background:rgba(0,0,0,.10);display:flex;flex-direction:column;
}
.content{
    flex:1;padding:48px 58px;overflow-y:auto;background:transparent;
    border:none;transition:opacity .12s ease;
}
.content.fade{opacity:.45}
.content::-webkit-scrollbar{width:8px}
.content::-webkit-scrollbar-track{background:transparent}
.content::-webkit-scrollbar-thumb{background:rgba(255,255,255,.18)}
.title,.login-logo{
    font-size:12px;letter-spacing:4px;color:white;margin-bottom:34px;font-weight:900;
}
.title::after,.login-logo::after{
    content:"";display:block;width:110px;height:1px;background:var(--blue);margin-top:14px;
}
.user-mini{
    display:flex;align-items:center;gap:12px;padding:0 0 22px 0;margin-bottom:24px;
    background:transparent;border:none;border-bottom:1px solid var(--line);
}
.pfp-box{
    width:46px;height:46px;background:transparent;border:1px solid var(--line);
    display:flex;align-items:center;justify-content:center;overflow:hidden;
    color:white;font-weight:900;font-size:18px;
}
.pfp-box img{width:100%;height:100%;object-fit:cover;display:block}
.user-mini-name{color:white;font-weight:900;font-size:14px}
.user-mini-mail{color:var(--muted);font-size:12px;margin-top:4px;word-break:break-all}
.menu-main{flex:1}
.menu-bottom{border-top:1px solid var(--line);padding-top:18px}
.item{
    cursor:pointer;user-select:none;transition:.12s ease;line-height:2.4;color:var(--muted);
    padding:0;margin-bottom:4px;font-size:14px;letter-spacing:.4px;text-transform:uppercase;
    font-weight:900;background:transparent;
}
.item:hover,.item.active{
    color:white;background:transparent;box-shadow:inset 3px 0 0 var(--blue);padding-left:12px;
}
.page-title{font-size:36px;font-weight:900;margin:0 0 10px 0;color:white;letter-spacing:-1px}
.page-sub{color:var(--muted);font-size:14px;line-height:1.7;margin:0 0 34px 0;max-width:720px}
.grid{
    display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:0;margin-bottom:34px;
    border-top:1px solid var(--line);border-bottom:1px solid var(--line);
}
.card{background:transparent;border:none;border-right:1px solid var(--line);padding:20px 22px}
.card:last-child{border-right:none}
.card-label{color:var(--muted);font-size:12px;letter-spacing:1.5px;text-transform:uppercase;margin-bottom:10px}
.card-number{color:white;font-size:30px;font-weight:900}
.card-text{color:var(--muted);font-size:14px;line-height:1.6;margin-top:8px}
.line{border-left:1px solid var(--line);padding-left:24px;max-width:980px}
.file-row,.topic-row,.credit-row,.comment-row,.user-row,.dm-row{
    margin-bottom:0;padding:18px 0;background:transparent;border:none;border-bottom:1px solid var(--line2);
}
.file-title,.credit-name,.topic-title{font-size:15px;color:white;margin-bottom:7px;font-weight:900}
.meta,.topic-meta,.comment-meta,.small{color:var(--muted);font-size:14px;line-height:1.7}
.body-text{white-space:pre-wrap}
.file-link,.topic-open,.fake-link,.name-link{
    color:var(--blue);text-decoration:none;font-size:14px;word-break:break-all;cursor:pointer;font-weight:900;
}
.file-link:hover,.topic-open:hover,.fake-link:hover,.name-link:hover{color:white;text-decoration:underline}
.form-box{margin-top:34px;border-left:1px solid var(--line);padding-left:24px;max-width:740px}
input,textarea{
    background:transparent;color:white;border:none;border-bottom:1px solid var(--line);
    padding:13px 0;outline:none;font-size:14px;
}
input::placeholder,textarea::placeholder{color:rgba(255,255,255,.45)}
input:focus,textarea:focus{border-color:var(--blue);background:transparent}
textarea{width:100%;min-height:96px;resize:vertical;margin-top:10px}
.search-bar{width:330px;margin-bottom:26px}
button,.file-button,.btn{
    display:inline-flex;align-items:center;justify-content:center;gap:10px;background:transparent;
    color:white;border:1px solid var(--line);padding:12px 18px;cursor:pointer;
    transition:.12s ease;font-size:14px;text-decoration:none;font-weight:900;
}
button:hover,.file-button:hover,.btn:hover{background:rgba(255,255,255,.06);border-color:var(--blue)}
.primary-btn,.btn-white{background:white;color:#06101d;border-color:white}
.primary-btn:hover,.btn-white:hover{background:rgba(255,255,255,.86);color:#06101d}
.btn-dark{background:transparent;color:white}
.danger-btn{background:transparent;border-color:rgba(255,92,108,.55);color:#ffd8dd}
.danger-btn:hover{background:rgba(255,92,108,.08);border-color:var(--danger)}
.account-box{width:430px;background:transparent;border:none;padding:0}
.login-card-title{font-size:25px;color:white;margin-bottom:8px;font-weight:900;letter-spacing:-.5px}
.login-card-sub{color:var(--muted);font-size:14px;margin-bottom:24px;word-break:break-all}
.login-btn{width:100%;height:46px;margin-bottom:12px}
button.login-btn,a.login-btn{
    display:flex;align-items:center;justify-content:center;background:transparent;color:white;
    border:1px solid var(--line);cursor:pointer;transition:.12s ease;font-size:14px;
    font-weight:900;text-decoration:none;
}
button.login-btn:hover,a.login-btn:hover{background:rgba(255,255,255,.06);border-color:var(--blue);color:white}
button.login-btn.primary-btn{background:white;color:#06101d;border-color:white}
.login-input{width:100%;margin-bottom:12px}
.selected-file{color:var(--muted);font-size:14px;margin-left:10px}
.account-section{margin-top:28px;padding-top:22px;border-top:1px solid var(--line)}
.account-pfp,.profile-head{display:flex;align-items:center;gap:14px;margin-bottom:22px}
.account-pfp .pfp-box,.profile-head .pfp-box{width:64px;height:64px;font-size:24px}
.credit-heading{color:white;font-size:13px;letter-spacing:2.4px;margin:26px 0 16px;font-weight:900}
.credit-heading:first-child{margin-top:0}
.credit-divider{border-top:1px solid var(--line);width:280px;margin:24px 0}
.dm-message{max-width:70%;margin-bottom:12px;padding:12px 0;border:none;border-bottom:1px solid var(--line2);background:transparent}
.dm-message.me{margin-left:auto;border-color:rgba(159,231,255,.28)}
.dm-message.them{margin-right:auto}
.audio-player{margin-top:14px;padding:8px 0 0 0;border:none;background:transparent}
.audio-controls{display:flex;align-items:center;gap:12px}
.audio-controls .audio-btn{
    width:auto;height:auto;min-width:0;min-height:0;padding:0;border:none!important;
    outline:none!important;background:transparent!important;box-shadow:none!important;
    color:white;font-size:15px;line-height:1;
}
.audio-controls .audio-btn:hover{background:transparent!important;border:none!important;color:var(--blue)}
.audio-range{flex:1;height:3px;padding:0;cursor:pointer;accent-color:var(--blue);background:transparent;border:none}
.audio-time{min-width:82px;text-align:right;color:var(--muted);font-size:12px;font-weight:900}
.file-preview-img{display:block;max-width:360px;max-height:260px;object-fit:contain;margin:14px 0;border:1px solid var(--line);background:rgba(255,255,255,.03)}
.file-preview-img.big{max-width:720px;max-height:520px}
.live-dot{display:inline-block;width:7px;height:7px;background:var(--good);margin-left:8px}
.login-only{height:100vh;display:flex;justify-content:center;align-items:center;padding:20px}
.login-shell{width:390px;background:transparent;border-left:1px solid var(--line);border-right:1px solid var(--line);padding:32px}
.login-title{font-size:34px;font-weight:900;color:white;margin-bottom:8px;letter-spacing:-1px}
.login-sub{color:var(--muted);font-size:14px;line-height:1.6;margin-bottom:26px}
.login-input-wrap{margin-bottom:14px}
.login-input-wrap input{width:100%}
.switch-text{color:var(--muted);text-align:center;font-size:13px;margin-top:18px}
.switch-text a{color:var(--blue);font-weight:900;text-decoration:none}
.alert-box,.success-box{
    padding:12px 0 12px 14px;margin-bottom:18px;font-size:14px;
    border-top:none;border-right:none;border-bottom:none;
}
.alert-box{background:transparent;color:#ffdede;border-left:3px solid var(--danger)}
.success-box{background:transparent;color:#d6ffe1;border-left:3px solid var(--good)}
.login-line{height:1px;background:var(--line);margin:18px 0 14px}
@media(max-width:900px){
    body{overflow:auto}
    .app{height:auto;min-height:100vh;flex-direction:column}
    .side{width:100%;border-right:none;border-bottom:1px solid var(--line)}
    .content{padding:28px}
    .grid{grid-template-columns:1fr}
    .card{border-right:none;border-bottom:1px solid var(--line)}
    .card:last-child{border-bottom:none}
    .account-box,.search-bar{width:100%}
}
</style>
</head>

<body>

{% if not user_email %}

<div class="login-only">
    <div class="login-shell">
        <div class="login-logo">PRODUCER-ROOM</div>

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
            <div class="login-sub">Create your account instantly. No email code is needed.</div>

            <form action="/register" method="POST">
                <div class="login-input-wrap"><input name="username" placeholder="Username" required></div>
                <div class="login-input-wrap"><input name="email" type="email" placeholder="Email" required></div>
                <div class="login-input-wrap"><input name="password" type="password" placeholder="Password" required></div>
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
                <div class="login-input-wrap"><input name="email" type="email" placeholder="Email" required></div>
                <div class="login-input-wrap"><input name="password" type="password" placeholder="Password" required></div>
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
        <div class="title">PRODUCER-ROOM</div>

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
let pfpUrl = {{ pfp_url|tojson }};
const startView = {{ start_view|tojson }};
const startTopicId = {{ start_item_id|tojson }};
const maxFileMb = {{ max_file_mb|tojson }};

const POST_COOLDOWN_TEXT = "{{ post_cooldown }} seconds";
const COMMENT_COOLDOWN_TEXT = "{{ comment_cooldown }} seconds";

let currentView = startView || "dashboard";
let currentViewId = startTopicId || "";

function clickEffect(el){ if(!el) return; }

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
    },120);
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

function userById(id){ return users.find(u=>u.id===id); }

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
            <audio preload="none" src="${escapeAttr(file.stream_url)}" type="audio/mpeg"></audio>
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

function filePreviewHtml(file, big=false){
    if(!file.is_image) return "";
    const cls = big ? "file-preview-img big" : "file-preview-img";
    return `<img class="${cls}" src="${escapeAttr(file.preview_url)}" alt="${escapeAttr(file.name)}">`;
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
            document.querySelectorAll("audio").forEach(a=>{ if(a !== audio) a.pause(); });
            audio.play().catch(()=>{});
        });
        pauseBtn.addEventListener("click", ()=> audio.pause());
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
        audio.addEventListener("error", ()=>{
            timeText.textContent = "MP3 not found";
        });
        update();
    });
}

function showDashboard(button){
    clickEffect(button);
    clearActive();
    if(button) button.classList.add("active");
    setUrl("dashboard");

    const recentTopics = discussions.slice(0,3);
    const recentFiles = files.slice(0,3);

    let html=`
        <div class="page-title">producer room <span class="live-dot"></span></div>
        <div class="page-sub">Upload ZIP packs, share MP3 previews, start discussions, and build a private funk producer space.</div>
        <div class="grid">
            <div class="card"><div class="card-label">uploaded files</div><div class="card-number">${files.length}</div><div class="card-text">ZIP packs and MP3 previews shared by members.</div></div>
            <div class="card"><div class="card-label">topics</div><div class="card-number">${discussions.length}</div><div class="card-text">Producer questions, beat feedback and ideas.</div></div>
            <div class="card"><div class="card-label">messages</div><div class="card-number">${dmMessages.length}</div><div class="card-text">Direct messages between members.</div></div>
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
                <div class="meta">new file · ${escapeHtml(file.size)} · by ${nameLink(file.author_id, file.author)} · ${file.comments.length} comments</div>
                ${filePreviewHtml(file)}
                <a class="file-link" href="/download/${encodeURIComponent(file.id)}" target="_blank">download</a>
                <span class="topic-open" onclick="openFile('${file.id}')" style="margin-left:12px;">open file post</span>
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
        <div class="page-sub">Upload ZIP packs, MP3 previews, or image posts. Images show directly in the files feed.</div>
        <input id="searchInput" class="search-bar" placeholder="search files" oninput="filterFiles()">
        <div class="line">
    `;

    if(files.length===0){ html+=`<div class="small">no files yet.</div>`; }

    files.forEach(file=>{
        html+=`
            <div class="file-row" data-name="${escapeAttr((file.name + ' ' + file.author).toLowerCase())}">
                <div class="file-title">${escapeHtml(file.name)}</div>
                <div class="meta">${escapeHtml(file.size)} · by ${nameLink(file.author_id, file.author)} · ${file.comments.length} comments</div>
                ${filePreviewHtml(file)}
                <a class="file-link" href="/download/${encodeURIComponent(file.id)}" target="_blank">download</a>
                <span class="topic-open" onclick="openFile('${file.id}')" style="margin-left:12px;">open file post</span>
                ${audioPlayerHtml(file)}
            </div>
        `;
    });

    html+=`
        </div>
        <div class="form-box">
            <form action="/upload" method="POST" enctype="multipart/form-data">
                <input id="fileInput" type="file" name="uploadfile" accept=".zip,.mp3,.png,.jpg,.jpeg,.webp,.gif" required hidden>
                <label for="fileInput" class="file-button">select file</label>
                <span id="fileName" class="selected-file">no file selected</span>
                <br><br>
                <button class="primary-btn" type="submit">upload file</button>
            </form>
            <p class="small">allowed: zip, mp3, png, jpg, jpeg, webp, gif. maximum size: ${maxFileMb} MB. Images are lightly compressed before Discord upload. Upload cooldown: 60 seconds.</p>
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

function openFile(fileId){
    const file = files.find(f=>f.id===fileId);
    if(!file) return;

    setUrl("file", fileId);
    clearActive();
    const filesButton=document.getElementById("menuFiles");
    if(filesButton) filesButton.classList.add("active");

    let html=`
        <div class="page-title">${escapeHtml(file.name)}</div>
        <div class="page-sub">by ${nameLink(file.author_id, file.author)} · ${escapeHtml(file.size)}</div>
        <button onclick="showFiles(document.getElementById('menuFiles'))">back to files</button>
        <a class="btn" href="/download/${encodeURIComponent(file.id)}" target="_blank">download</a>
        <br><br>
        <div class="line">
            ${filePreviewHtml(file, true)}
            ${audioPlayerHtml(file)}
            <br>
            <div class="topic-title">comments</div>
    `;

    if(file.comments.length===0){ html+=`<div class="small">no comments yet.</div>`; }

    file.comments.forEach(comment=>{
        html+=`
            <div class="comment-row">
                <div class="comment-meta">${nameLink(comment.author_id, comment.author)}</div>
                <div class="small body-text">${escapeHtml(comment.body)}</div>
            </div>
        `;
    });

    html+=`
        </div>
        <div class="form-box">
            <form action="/file-comment/${file.id}" method="POST">
                <textarea name="body" placeholder="write comment" required></textarea>
                <br><br>
                <button class="primary-btn" type="submit">add comment</button>
            </form>
            <p class="small">comment cooldown: ${COMMENT_COOLDOWN_TEXT}</p>
        </div>
    `;

    fadeChange(html);
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

    if(discussions.length===0){ html+=`<div class="small">no topics yet.</div>`; }

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

    html+=`
        </div>
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

    if(topic.comments.length===0){ html+=`<div class="small">no comments yet.</div>`; }

    topic.comments.forEach(comment=>{
        html+=`
            <div class="comment-row">
                <div class="comment-meta">${nameLink(comment.author_id, comment.author)}</div>
                <div class="small body-text">${escapeHtml(comment.body)}</div>
            </div>
        `;
    });

    html+=`
        </div>
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
                <div class="card"><div class="card-label">posts</div><div class="card-number">${u.topic_count}</div></div>
                <div class="card"><div class="card-label">files</div><div class="card-number">${u.file_count}</div></div>
                <div class="card"><div class="card-label">comments</div><div class="card-number">${u.comment_count}</div></div>
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
        <div class="form-box"><button onclick="showUsers(document.getElementById('menuUsers'))">find people</button></div>
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
                    <div class="small">allowed: png, jpg, jpeg, webp, gif. maximum size: 3 MB. Images are lightly compressed before Discord upload. Cooldown: 10 minutes.</div>
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
                        <div class="small">Username cooldown: 5 days.</div>
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
            action = `<div class="topic-open" onclick="openFile('${n.target_id}')">open file</div>`;
        }else if(n.type === "file_comment"){
            action = `<div class="topic-open" onclick="openFile('${n.target_id}')">open file</div>`;
        }

        html += `
            <div class="topic-row">
                <div class="topic-title">${n.unread ? "● " : ""}${escapeHtml(n.title)}</div>
                <div class="small">${escapeHtml(n.body)}</div>
                <br>${action}
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
        html+=`<div class="credit-row"><div class="credit-name">${escapeHtml(person)}</div></div>`;
    });

    html+=`<div class="credit-divider"></div><div class="credit-heading">MEMBERS</div>`;

    credits["MEMBERS"].forEach(person=>{
        html+=`<div class="credit-row"><div class="credit-name">${escapeHtml(person)}</div></div>`;
    });

    html+=`<div class="credit-divider"></div><div class="credit-heading">WEBSITE MADE BY</div>`;

    credits["WEBSITE MADE BY"].forEach(person=>{
        html+=`<div class="credit-row"><div class="credit-name">${escapeHtml(person)}</div></div>`;
    });

    html+=`</div>`;
    fadeChange(html);
}

function updateNotificationBadge(){
    const badge = document.getElementById("notificationBadge");
    if(!badge) return;
    badge.textContent = notificationCount;
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
    }else if(currentView === "file" && currentViewId){
        openFile(currentViewId);
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
    if(document.hidden) return;

    try{
        const res = await fetch("/live-state?t=" + Date.now(), {cache:"no-store"});
        const data = await res.json();

        if(!data.ok){ return; }

        if(data.db_updated_at !== dbUpdatedAt){
            files = data.files;
            discussions = data.topics;
            users = data.users;
            dmMessages = data.dm_messages;
            notifications = data.notifications;
            notificationCount = data.notification_count;
            dbUpdatedAt = data.db_updated_at;

            if(typeof data.pfp_url === "string"){
                pfpUrl = data.pfp_url ? data.pfp_url + "&cache=" + Date.now() : "";
            }

            updateNotificationBadge();
            rerenderCurrentView();
        }
    }catch(e){
        console.log("live update failed", e);
    }
}

setInterval(checkForUpdates, 60000);

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

function startApp(){
    if(!userEmail) return;

    updateNotificationBadge();

    try{
        if(startView === "files"){
            showFiles(document.getElementById("menuFiles"));
        }else if(startView === "file" && startTopicId){
            openFile(startTopicId);
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
    }catch(err){
        console.error(err);
        const content = document.getElementById("content");
        if(content){
            content.innerHTML = `
                <div class="page-title">error</div>
                <div class="small">Something broke while loading the page. Check browser console.</div>
                <br>
                <button onclick="location.href='/'">reload</button>
            `;
        }
    }
}

if(document.readyState === "loading"){
    document.addEventListener("DOMContentLoaded", startApp);
}else{
    startApp();
}
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
        if requested_view == "register":
            auth_mode = "register"
        else:
            auth_mode = "login"

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
            start_view="login",
            start_item_id="",
            auth_mode=auth_mode,
            max_file_mb=MAX_FILE_SIZE // (1024 * 1024),
            post_cooldown=POST_COOLDOWN_SECONDS,
            comment_cooldown=COMMENT_COOLDOWN_SECONDS,
        )

    auth_mode = ""

    if requested_view in ["login", "register", ""]:
        requested_view = "dashboard"

    allowed_views = {
        "dashboard", "files", "file", "discussion", "topic", "account", "credits",
        "notifications", "profile", "profiles", "dm", "messages",
    }

    if requested_view not in allowed_views:
        requested_view = "dashboard"

    try:
        store = load_store()
        db = store["db"]
        user = db["users"].get(current_user_id())
        if not user:
            uid, user = find_user_by_email(db, current_email())
            if uid and user:
                session["user_id"] = uid
    except Exception as e:
        flash(f"Discord database error: {e}", "error")
        db = blank_db()
        store = {"pfp_urls": {}, "file_urls": {}}
        user = None

    if not user:
        session.clear()
        flash("Account not found. Please login again.", "error")
        return redirect(url_for("home", view="login"))

    blocked_reason = blocked_email_reason(user.get("email", current_email()))
    if blocked_reason:
        try:
            delete_user_and_content(db, user.get("id") or current_user_id())
            save_db(db)
        except Exception:
            pass
        session.clear()
        flash("This email is blocked. The account was deleted.", "error")
        return redirect(url_for("home", view="register"))

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
        comment_cooldown=COMMENT_COOLDOWN_SECONDS,
    )


@app.route("/register", methods=["POST"])
def register():
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

    email_ok, email_reason = validate_email_basic(email)
    if not email_ok:
        # If an old account already exists with a blocked/temp/fake email, delete it now.
        uid, old_user = find_user_by_email(db, email)
        if uid and blocked_email_reason(email):
            try:
                delete_user_and_content(db, uid)
                save_db(db)
                flash("This email is blocked. The old account was deleted.", "error")
            except Exception as e:
                flash(f"This email is blocked, but deletion failed: {e}", "error")
        else:
            flash(email_reason, "error")
        return redirect(url_for("home", view="register"))

    if len(password) < 6:
        flash("Password must be at least 6 characters.", "error")
        return redirect(url_for("home", view="register"))

    # IMPORTANT FIX:
    # No verification system anymore. If this email already exists in the Discord DB,
    # do NOT block the user with "Account already exists".
    # Open/restore that existing account, update the username/password from this form,
    # save the DB, and enter the main page.
    existing_uid, existing_user = find_user_by_email(db, email)

    if existing_user:
        # Existing email opens/restores the account. Username only changes if the 5-day cooldown allows it.
        old_username_for_check = existing_user.get("username", "").strip().lower()
        wants_username_change = username.lower() != old_username_for_check
        username_left = cooldown_left(existing_user, "last_username_change_at", USERNAME_COOLDOWN_SECONDS)

        if wants_username_change and username_left <= 0:
            for other_id, other_user in list(db["users"].items()):
                if other_id == existing_uid:
                    continue
                if other_user.get("username", "").strip().lower() == username.lower():
                    flash("Username already exists. Choose another one.", "error")
                    return redirect(url_for("home", view="register"))

        existing_user["id"] = existing_uid
        existing_user["email"] = email
        existing_user["password_hash"] = generate_password_hash(password)
        existing_user["email_verified"] = True
        existing_user.setdefault("pfp_id", "")
        existing_user.setdefault("pfp_updated", 0)
        existing_user.setdefault("about", "")
        existing_user.setdefault("last_seen_notifications", int(time.time()))
        existing_user.setdefault("last_topic_at", 0)
        existing_user.setdefault("last_comment_at", 0)
        existing_user.setdefault("last_upload_at", 0)
        existing_user.setdefault("last_about_change_at", 0)
        existing_user.setdefault("last_pfp_change_at", 0)
        existing_user.setdefault("last_password_change_at", 0)
        existing_user.setdefault("last_username_change_at", 0)
        existing_user.setdefault("created", int(time.time()))

        old_username = existing_user.get("username", "")
        if username.lower() != old_username.strip().lower():
            left = cooldown_left(existing_user, "last_username_change_at", USERNAME_COOLDOWN_SECONDS)
            if left <= 0:
                existing_user["username"] = username
                existing_user["last_username_change_at"] = int(time.time())
            # If cooldown is active, keep the old username but still open the account.

        existing_user["updated_at"] = int(time.time())
        db["users"][existing_uid] = existing_user

        try:
            save_db(db)
        except Exception as e:
            flash(f"Could not update existing account in Discord DB: {e}", "error")
            return redirect(url_for("home", view="register"))

        session["email"] = email
        session["user_id"] = existing_uid
        flash("Account opened successfully.", "success")
        return go("dashboard")

    # New email: only block username if another account already uses it.
    for existing_id, existing_user in list(db["users"].items()):
        existing_username = existing_user.get("username", "").strip().lower()
        if existing_username == username.lower():
            flash("Username already exists. Choose another one.", "error")
            return redirect(url_for("home", view="register"))

    user_id = user_id_from_email(email)

    user = {
        "id": user_id,
        "username": username,
        "email": email,
        "password_hash": generate_password_hash(password),
        "email_verified": True,
        "pfp_id": "",
        "pfp_updated": 0,
        "about": "",
        "last_seen_notifications": int(time.time()),
        "last_topic_at": 0,
        "last_comment_at": 0,
        "last_upload_at": 0,
        "last_about_change_at": 0,
        "last_pfp_change_at": 0,
        "last_password_change_at": 0,
        "last_username_change_at": 0,
        "created": int(time.time()),
        "updated_at": int(time.time()),
    }

    db["users"][user_id] = user

    try:
        save_db(db)
    except Exception as e:
        flash(f"Could not save account to Discord DB: {e}", "error")
        return redirect(url_for("home", view="register"))

    session["email"] = email
    session["user_id"] = user_id

    flash("Account created successfully.", "success")
    return go("dashboard")


@app.route("/login", methods=["POST"])
def login():
    try:
        db = load_store(force=True)["db"]
    except Exception as e:
        flash(f"Discord database error: {e}", "error")
        return redirect(url_for("home", view="login"))

    email = normalize_email(request.form.get("email", ""))
    password = request.form.get("password", "")

    uid, user = find_user_by_email(db, email)

    if blocked_email_reason(email):
        if uid and user:
            try:
                delete_user_and_content(db, uid)
                save_db(db)
                session.clear()
                flash("This email is blocked. The account was deleted.", "error")
            except Exception as e:
                flash(f"This email is blocked, but deletion failed: {e}", "error")
        else:
            flash("This email is blocked. Temporary, fake, privacy, or blocked email domains are not allowed.", "error")
        return redirect(url_for("home", view="register"))

    if not user:
        flash("Account not found. Check the email or create an account first.", "error")
        return redirect(url_for("home", view="login"))

    if not password_matches(user.get("password_hash", ""), password):
        flash("Wrong password. Please try again.", "error")
        return redirect(url_for("home", view="login"))

    if not user.get("id"):
        user["id"] = uid

    session["email"] = normalize_email(user.get("email", email))
    session["user_id"] = uid

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

    if new_username.lower() == user.get("username", "").strip().lower():
        flash("This is already your username.", "error")
        return go("account")

    left = cooldown_left(user, "last_username_change_at", USERNAME_COOLDOWN_SECONDS)
    if left > 0:
        flash(f"Username can only be changed once every 5 days. Try again in {format_cooldown(left)}.", "error")
        return go("account")

    for existing_user in db["users"].values():
        same_username = existing_user.get("username", "").strip().lower() == new_username.lower()
        different_account = existing_user.get("id") != user.get("id")

        if same_username and different_account:
            flash("Username already exists. Choose another one.", "error")
            return go("account")

    user["username"] = new_username
    user["last_username_change_at"] = int(time.time())
    user["updated_at"] = int(time.time())
    db["users"][user["id"]] = user

    try:
        save_db(db)
    except Exception as e:
        flash(f"Could not update username: {e}", "error")
        return go("account")

    flash("Username changed successfully. You can change it again in 5 days.", "success")
    return go("account")


@app.route("/change-about", methods=["POST"])
@login_required
def change_about():
    store = load_store(force=True)
    db = store["db"]
    user = db["users"].get(current_user_id())

    left = cooldown_left(user, "last_about_change_at", PROFILE_CHANGE_COOLDOWN_SECONDS)
    if left > 0:
        flash(f"Profile info can be changed again in {format_cooldown(left)}.", "error")
        return go("account")

    about = request.form.get("about", "").strip()

    user["about"] = about[:500]
    user["last_about_change_at"] = int(time.time())
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
    store = load_store(force=True)
    db = store["db"]
    user = db["users"].get(current_user_id())

    left = cooldown_left(user, "last_password_change_at", PASSWORD_CHANGE_COOLDOWN_SECONDS)
    if left > 0:
        flash(f"Password can be changed again in {format_cooldown(left)}.", "error")
        return go("account")

    old_password = request.form.get("old_password", "")
    new_password = request.form.get("new_password", "")

    if not password_matches(user.get("password_hash", ""), old_password):
        flash("Old password is wrong.", "error")
        return go("account")

    if len(new_password) < 6:
        flash("New password must be at least 6 characters.", "error")
        return go("account")

    user["password_hash"] = generate_password_hash(new_password)
    user["last_password_change_at"] = int(time.time())
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
    store = load_store(force=True)
    db = store["db"]
    user = db["users"].get(current_user_id())

    left = cooldown_left(user, "last_pfp_change_at", PROFILE_CHANGE_COOLDOWN_SECONDS)
    if left > 0:
        flash(f"Profile picture can be changed again in {format_cooldown(left)}.", "error")
        return go("account")

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

    original_size = len(file_bytes)
    file_bytes, final_content_type, compression_info = compress_image_for_discord(original_name, file_bytes, uploaded.content_type)
    size = len(file_bytes)

    pfp_id = secrets.token_hex(12)

    try:
        discord_msg = save_profile_picture_to_discord(
            pfp_id=pfp_id,
            filename=original_name,
            file_bytes=file_bytes,
            content_type=final_content_type,
        )
    except Exception as e:
        flash(f"Could not upload profile picture to Discord: {e}", "error")
        return go("account")

    attachment = (discord_msg.get("attachments", []) or [{}])[0]
    pfp_info = attachment_info_from_discord(attachment)
    user["pfp_id"] = pfp_id
    user["pfp_name"] = original_name
    user["pfp_original_size"] = original_size
    user["pfp_size"] = size
    user["pfp_compressed"] = bool(compression_info.get("compressed"))
    user["pfp_discord_message_id"] = discord_msg.get("id", "")
    user["pfp_attachment_url"] = pfp_info.get("url", "")
    user["pfp_attachment_proxy_url"] = pfp_info.get("proxy_url", "")
    user["pfp_updated"] = int(time.time())
    user["last_pfp_change_at"] = int(time.time())
    user["updated_at"] = int(time.time())
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

    left = cooldown_left(user, "last_upload_at", UPLOAD_COOLDOWN_SECONDS)
    if left > 0:
        flash(f"Upload cooldown active. Try again in {format_cooldown(left)}.", "error")
        return go("files")

    if "uploadfile" not in request.files:
        flash("No file selected.", "error")
        return go("files")

    uploaded = request.files["uploadfile"]

    if uploaded.filename == "":
        flash("No file selected.", "error")
        return go("files")

    if not allowed_file(uploaded.filename):
        flash("Only ZIP, MP3, PNG, JPG, JPEG, WEBP and GIF files are allowed.", "error")
        return go("files")

    original_name = secure_filename(uploaded.filename)

    for existing_file in db["files"].values():
        if existing_file.get("original_name", "").lower() == original_name.lower():
            flash("A file with this name already exists. Rename it before uploading.", "error")
            return go("files")

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

    original_size = len(file_bytes)
    final_content_type = uploaded.content_type or "application/octet-stream"
    compression_info = {"compressed": False, "original_size": original_size, "compressed_size": original_size, "saved_bytes": 0}

    if os.path.splitext(original_name.lower())[1] in ALLOWED_IMAGE_EXTENSIONS:
        file_bytes, final_content_type, compression_info = compress_image_for_discord(original_name, file_bytes, uploaded.content_type)
        size = len(file_bytes)

    for existing_file in db["files"].values():
        if existing_file.get("original_name", "").strip().lower() == original_name.strip().lower():
            flash("A file with this name already exists. Rename it before uploading again.", "error")
            return go("files")

    file_id = secrets.token_hex(12)

    try:
        discord_msg = save_uploaded_file_to_discord(
            file_id=file_id,
            filename=original_name,
            file_bytes=file_bytes,
            content_type=final_content_type,
        )
    except Exception as e:
        flash(f"Could not upload file to Discord: {e}", "error")
        return go("files")

    attachment = (discord_msg.get("attachments", []) or [{}])[0]
    attachment_info = attachment_info_from_discord(attachment)

    metadata = {
        "id": file_id,
        "original_name": original_name,
        "size": size,
        "original_size": original_size,
        "compressed": bool(compression_info.get("compressed")),
        "saved_bytes": int(compression_info.get("saved_bytes", 0)),
        "content_type": final_content_type or "application/octet-stream",
        "kind": "image" if os.path.splitext(original_name.lower())[1] in ALLOWED_IMAGE_EXTENSIONS else ("audio" if original_name.lower().endswith(".mp3") else "zip"),
        "author": user.get("username"),
        "author_id": user.get("id"),
        "created": int(time.time()),
        "discord_message_id": discord_msg.get("id", ""),
        "attachment_url": attachment_info.get("url", ""),
        "attachment_proxy_url": attachment_info.get("proxy_url", ""),
        "attachment_filename": attachment_info.get("filename", original_name),
    }

    db["files"][file_id] = metadata
    user["last_upload_at"] = int(time.time())
    user["updated_at"] = int(time.time())
    db["users"][user["id"]] = user

    try:
        save_db(db)
    except Exception as e:
        flash(f"File uploaded, but DB update failed: {e}", "error")
        return go("files")

    flash("File uploaded successfully.", "success")
    return go("files")


@app.route("/download/<file_id>")
@login_required
def download(file_id):
    try:
        store = load_store()
        db = store["db"]
    except Exception as e:
        flash(f"Discord database error: {e}", "error")
        return go("files")

    file_data = db["files"].get(file_id)
    if not file_data:
        flash("File not found.", "error")
        return go("files")

    info = find_file_attachment_info(file_id, file_data, store)
    file_url = best_attachment_url(info)
    if not file_url:
        flash("Discord file attachment not found.", "error")
        return go("files")

    # Faster: send the browser directly to the Discord CDN instead of proxying the whole file through Flask.
    return redirect(file_url, code=302)


@app.route("/stream/<file_id>")
@login_required
def stream_file(file_id):
    try:
        store = load_store()
        db = store["db"]
    except Exception:
        abort(404)

    file_data = db["files"].get(file_id)
    if not file_data:
        abort(404)

    if not file_data.get("original_name", "").lower().endswith(".mp3"):
        abort(404)

    info = find_file_attachment_info(file_id, file_data, store)
    file_url = best_attachment_url(info)
    if not file_url:
        abort(404)

    # Do not download the whole MP3 through Flask.
    # Redirect to Discord CDN so browser audio metadata, seeking, and streaming work fast.
    return redirect(file_url, code=302)


@app.route("/preview/<file_id>")
@login_required
def preview_file(file_id):
    try:
        store = load_store()
        db = store["db"]
    except Exception:
        abort(404)

    file_data = db["files"].get(file_id)
    if not file_data:
        abort(404)

    original_name = file_data.get("original_name", "")
    if os.path.splitext(original_name.lower())[1] not in ALLOWED_IMAGE_EXTENSIONS:
        abort(404)

    info = find_file_attachment_info(file_id, file_data, store)
    file_url = best_attachment_url(info)
    if not file_url:
        abort(404)

    return redirect(file_url, code=302)


@app.route("/pfp/<user_id>")
@login_required
def profile_picture(user_id):
    try:
        store = load_store()
        db = store["db"]
    except Exception:
        abort(404)

    user = db["users"].get(user_id)
    if not user:
        abort(404)

    pfp_id = user.get("pfp_id", "")
    if not pfp_id:
        abort(404)

    info = find_pfp_attachment_info(pfp_id, user, store)
    pfp_url = best_attachment_url(info)
    if not pfp_url:
        abort(404)

    return redirect(pfp_url, code=302)


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
    except Exception as e:
        flash(f"Could not save topic to Discord DB: {e}", "error")
        return go("discussion")

    flash("Topic added.", "success")
    return go("topic", topic_id)


@app.route("/comment/<topic_id>", methods=["POST"])
@login_required
def add_comment(topic_id):
    store = load_store(force=True)
    db = store["db"]
    user = db["users"].get(current_user_id())

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
        "created": int(time.time()),
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
        return go("file", file_id)

    body = request.form.get("body", "").strip()
    if not body:
        flash("Comment cannot be empty.", "error")
        return go("file", file_id)

    comment_id = secrets.token_hex(12)
    comment = {
        "id": comment_id,
        "file_id": file_id,
        "body": body[:900],
        "author": user.get("username"),
        "author_id": user.get("id"),
        "created": int(time.time()),
    }

    db.setdefault("file_comments", {})[comment_id] = comment
    user["last_comment_at"] = int(time.time())
    db["users"][user["id"]] = user

    try:
        save_db(db)
    except Exception as e:
        flash(f"Could not save file comment to Discord DB: {e}", "error")
        return go("file", file_id)

    flash("Comment added.", "success")
    return go("file", file_id)


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
    except Exception as e:
        flash(f"Could not send message: {e}", "error")
        return go("dm", target_id)

    flash("Message sent.", "success")
    return go("dm", target_id)


@app.route("/live-state")
@login_required
def live_state():
    try:
        store = load_store()
        db = store["db"]
        user = db["users"].get(current_user_id())

        if not user:
            return jsonify({"ok": False, "error": "Account not found"}), 401

        blocked_reason = blocked_email_reason(user.get("email", current_email()))
        if blocked_reason:
            # Do not save DB from live-state. live-state runs automatically in the browser,
            # so writing here can spam Discord. The account will be deleted on login/register
            # or by opening /purge-blocked-emails once.
            session.clear()
            return jsonify({"ok": False, "error": "This email is blocked. Please log in again."}), 401

        state = build_client_state(db, store, user)
        state["ok"] = True
        return jsonify(state)

    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/notifications-read", methods=["POST"])
@login_required
def notifications_read():
    # IMPORTANT SPEED/SPAM FIX:
    # This route is clicked automatically when the notifications page opens.
    # Do NOT save a new DB snapshot to Discord here. Store read-time in the Flask session only.
    session["notifications_seen_at"] = int(time.time())
    return jsonify({"ok": True, "session_only": True})

@app.route("/purge-blocked-emails")
def purge_blocked_emails_route():
    try:
        store = load_store(force=True)
        db = store["db"]
        deleted = purge_blocked_email_accounts(db)
        if deleted:
            save_db(db)

        lines = [
            "BLOCKED EMAIL PURGE COMPLETE",
            f"Deleted accounts: {len(deleted)}",
            "",
        ]
        for item in deleted[:50]:
            lines.append(f"- {item.get('username')} | {item.get('email')} | {item.get('reason')}")
        if len(deleted) > 50:
            lines.append(f"...and {len(deleted) - 50} more")
        return "<br>".join(lines)
    except Exception as e:
        return f"BLOCKED EMAIL PURGE ERROR: {e}"


@app.route("/discord-test")
def discord_test():
    try:
        store = load_store(force=True)
        db = store["db"]

        sent_test = False
        if request.args.get("send") == "1":
            post_discord_text(f"SWTEST|website connected|{int(time.time())}")
            sent_test = True

        account_count = len([u for u in db["users"].values() if isinstance(u, dict) and u.get("email")])

        return (
            "DISCORD DATABASE WORKS<br>"
            f"Messages scanned: {store['message_count']}<br>"
            f"Snapshot loaded: {store['snapshot_loaded']}<br>"
            f"Accounts: {account_count}<br>"
            f"Topics: {len(db['topics'])}<br>"
            f"Comments: {len(db['comments'])}<br>"
            f"Files: {len(db['files'])}<br>"
            f"DMs: {len(db['dms'])}<br>"
            f"Test message sent: {sent_test}<br>"
            "Add ?send=1 to /discord-test only when you really want to send a test Discord message."
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

import os
import json
import time
import hashlib
import secrets
from io import BytesIO
from functools import wraps

import requests
from flask import Flask, render_template_string, request, redirect, url_for, session, flash, send_file, abort
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", secrets.token_hex(64))

DISCORD_BOT_TOKEN = os.environ.get("DISCORD_BOT_TOKEN", "").strip()
DISCORD_DB_CHANNEL_ID = os.environ.get("DISCORD_DB_CHANNEL_ID", "").strip()
DISCORD_API = "https://discord.com/api/v10"

MAX_FILE_SIZE = int(os.environ.get("MAX_FILE_SIZE", str(8 * 1024 * 1024)))
MAX_DB_SIZE = int(os.environ.get("MAX_DB_SIZE", str(7 * 1024 * 1024)))

app.config["MAX_CONTENT_LENGTH"] = MAX_FILE_SIZE

ALLOWED_EXTENSIONS = {".zip", ".mp3"}
ALLOWED_PFP_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif"}

CACHE_SECONDS = 2
CACHE = {
    "time": 0,
    "store": None
}

CREDITS = {
    "OWNERS": [
        "DJ TUTTER",
        "DJ LIRA DA ZL"
    ],
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
    "WEBSITE MADE BY": [
        "DJ SABA 7"
    ]
}


# ============================================================
# DISCORD SNAPSHOT DATABASE SYSTEM
# ============================================================

def blank_db():
    return {
        "version": 3,
        "users": {},
        "topics": {},
        "comments": {},
        "files": {},
        "created_at": int(time.time()),
        "updated_at": int(time.time())
    }


def normalize_db(db):
    if not isinstance(db, dict):
        db = blank_db()

    clean = blank_db()
    clean.update(db)

    for key in ["users", "topics", "comments", "files"]:
        if not isinstance(clean.get(key), dict):
            clean[key] = {}

    clean["version"] = 3
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
    db["updated_at"] = int(time.time())

    raw = json.dumps(db, separators=(",", ":"), ensure_ascii=False).encode("utf-8")

    if len(raw) > MAX_DB_SIZE:
        raise ValueError("Discord DB snapshot is too large. Delete old data or raise MAX_DB_SIZE.")

    post_discord_attachment(
        content=f"SWDBSNAP|v3|{int(time.time())}",
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


# ============================================================
# HELPERS
# ============================================================

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


def go(view="dashboard", topic_id=None):
    if topic_id:
        return redirect(url_for("home", view=view, id=topic_id))

    return redirect(url_for("home", view=view))


def public_file(file_data, db=None):
    return {
        "id": file_data.get("id", ""),
        "name": file_data.get("original_name", "file"),
        "size": size_text(file_data.get("size", 0)),
        "author": username_from_id(
            file_data.get("author_id", ""),
            db,
            file_data.get("author", "unknown")
        ),
        "created": int(file_data.get("created", 0)),
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
                    c.get("author", "unknown")
                ),
                "created": int(c.get("created", 0)),
            }
            for c in topic_comments
        ],
    }


# ============================================================
# HTML OLD STYLE LAYOUT
# ============================================================

HTML = """
<!DOCTYPE html>
<html>
<head>
<title>FlowZNmelhor</title>
<meta name="viewport" content="width=device-width, initial-scale=1">

<style>
*{
    box-sizing:border-box;
}

:root{
    --white:#ffffff;
    --text:#f7fbff;
    --muted:rgba(255,255,255,.72);
    --soft:rgba(255,255,255,.54);
    --blue:#9fe7ff;
    --blue2:#4eb8ff;
    --dark:#06101d;
    --panel:rgba(3,13,24,.74);
    --panel2:rgba(0,0,0,.34);
    --line:rgba(255,255,255,.26);
    --line2:rgba(255,255,255,.14);
    --red:#ff5468;
}

body{
    margin:0;
    min-height:100vh;
    color:var(--text);
    font-family:Arial,Helvetica,sans-serif;
    background:
        linear-gradient(rgba(2,8,16,.70), rgba(2,8,16,.84)),
        radial-gradient(circle at 18% 18%, rgba(125,215,255,.34), transparent 28%),
        radial-gradient(circle at 84% 78%, rgba(43,110,190,.32), transparent 35%),
        url("/static/bg.png");
    background-size:cover;
    background-position:center;
    overflow:hidden;
}

body::before{
    content:"";
    position:fixed;
    inset:0;
    background-image:
        linear-gradient(rgba(255,255,255,.045) 1px, transparent 1px),
        linear-gradient(90deg, rgba(255,255,255,.045) 1px, transparent 1px);
    background-size:24px 24px;
    opacity:.65;
    pointer-events:none;
    z-index:0;
}

body::after{
    content:"";
    position:fixed;
    width:420px;
    height:420px;
    right:-150px;
    top:-160px;
    background:rgba(159,231,255,.17);
    filter:blur(35px);
    border-radius:50%;
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
    width:370px;
    background:rgba(220,245,255,.20);
    border:1px solid rgba(255,255,255,.42);
    backdrop-filter:blur(18px);
    -webkit-backdrop-filter:blur(18px);
    box-shadow:0 28px 80px rgba(0,0,0,.50);
    border-radius:6px;
    padding:32px;
    position:relative;
    overflow:hidden;
}

.login-shell::before{
    content:"";
    position:absolute;
    inset:0;
    background:
        linear-gradient(120deg, rgba(255,255,255,.32), transparent 42%),
        radial-gradient(circle at 20% 0%, rgba(255,255,255,.20), transparent 35%);
    pointer-events:none;
}

.login-inner{
    position:relative;
    z-index:2;
}

.login-logo{
    font-size:12px;
    letter-spacing:3px;
    font-weight:900;
    color:white;
    margin-bottom:28px;
}

.login-logo::after{
    content:"";
    display:block;
    width:46px;
    height:2px;
    background:var(--blue);
    margin-top:12px;
}

.login-title{
    font-size:30px;
    font-weight:900;
    color:white;
    margin-bottom:8px;
    letter-spacing:-1px;
    text-shadow:0 3px 18px rgba(0,0,0,.40);
}

.login-sub{
    color:rgba(255,255,255,.76);
    font-size:14px;
    line-height:1.6;
    margin-bottom:26px;
}

.login-input-wrap{
    position:relative;
    margin-bottom:16px;
}

.login-input-wrap input{
    width:100%;
    background:transparent;
    border:none;
    border-bottom:1px solid rgba(255,255,255,.70);
    color:white;
    outline:none;
    padding:13px 32px 11px 0;
    font-size:14px;
    font-weight:700;
}

.login-input-wrap input::placeholder{
    color:rgba(255,255,255,.70);
    font-weight:500;
}

.login-icon{
    position:absolute;
    right:2px;
    top:12px;
    color:white;
    font-size:14px;
    opacity:.9;
}

.login-row{
    display:flex;
    justify-content:space-between;
    align-items:center;
    color:rgba(255,255,255,.74);
    font-size:12px;
    margin:6px 0 22px;
}

.login-row input{
    accent-color:white;
}

.btn{
    display:inline-flex;
    align-items:center;
    justify-content:center;
    width:100%;
    height:44px;
    border-radius:4px;
    border:1px solid rgba(255,255,255,.28);
    cursor:pointer;
    transition:.18s ease;
    font-weight:900;
    font-size:14px;
    text-decoration:none;
}

.btn-dark{
    background:#06101d;
    color:white;
}

.btn-dark:hover{
    background:#102b43;
    border-color:var(--blue);
    transform:translateY(-1px);
}

.btn-white{
    background:white;
    color:#06101d;
    border-color:white;
}

.btn-white:hover{
    background:#e9f8ff;
    transform:translateY(-1px);
}

.login-line{
    height:1px;
    background:rgba(255,255,255,.30);
    margin:18px 0 14px;
}

.switch-text{
    color:rgba(255,255,255,.78);
    text-align:center;
    font-size:13px;
    margin-top:18px;
}

.switch-text a{
    color:white;
    font-weight:900;
    text-decoration:none;
}

.alert-box,
.success-box{
    padding:12px 14px;
    margin-bottom:18px;
    font-size:14px;
    border-radius:4px;
}

.alert-box{
    background:rgba(80,0,0,.55);
    color:#ffdede;
    border:1px solid rgba(255,90,90,.46);
    border-left:3px solid #ff5757;
}

.success-box{
    background:rgba(0,70,30,.48);
    color:#d6ffe1;
    border:1px solid rgba(67,232,139,.44);
    border-left:3px solid #43e88b;
}

.side{
    width:260px;
    padding:28px 20px;
    border:1px solid var(--line);
    background:var(--panel);
    backdrop-filter:blur(18px);
    -webkit-backdrop-filter:blur(18px);
    box-shadow:0 22px 60px rgba(0,0,0,.45);
    display:flex;
    flex-direction:column;
    border-radius:6px;
}

.title{
    font-size:12px;
    letter-spacing:3px;
    color:white;
    margin-bottom:34px;
    font-weight:900;
}

.title::after{
    content:"";
    display:block;
    width:42px;
    height:2px;
    background:var(--blue);
    margin-top:12px;
}

.user-mini{
    background:rgba(255,255,255,.08);
    border:1px solid rgba(255,255,255,.14);
    border-radius:4px;
    padding:12px;
    margin-bottom:24px;
    display:flex;
    gap:12px;
    align-items:center;
}

.pfp-box{
    width:46px;
    height:46px;
    border-radius:4px;
    background:rgba(255,255,255,.08);
    border:1px solid rgba(255,255,255,.20);
    display:flex;
    align-items:center;
    justify-content:center;
    overflow:hidden;
    flex:0 0 auto;
    color:white;
    font-weight:900;
    font-size:18px;
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
    transition:.18s ease;
    line-height:2.35;
    color:rgba(255,255,255,.76);
    padding:2px 10px;
    margin-bottom:4px;
    border-radius:4px;
    font-size:14px;
    letter-spacing:.4px;
}

.item:hover,
.item.active{
    color:white;
    background:rgba(255,255,255,.13);
    transform:translateX(4px);
}

.clicked{
    animation:clickFade .35s ease;
}

@keyframes clickFade{
    0%{opacity:1}
    40%{opacity:.45;transform:translateX(8px)}
    100%{opacity:1;transform:translateX(4px)}
}

.content{
    flex:1;
    padding:42px;
    overflow-y:auto;
    transition:opacity .25s ease,transform .25s ease;
    border:1px solid var(--line);
    background:var(--panel);
    backdrop-filter:blur(20px);
    -webkit-backdrop-filter:blur(20px);
    box-shadow:0 22px 65px rgba(0,0,0,.42);
    border-radius:6px;
}

.content.fade{
    opacity:0;
    transform:translateY(8px);
}

.content::-webkit-scrollbar{
    width:10px;
}

.content::-webkit-scrollbar-track{
    background:rgba(255,255,255,.04);
}

.content::-webkit-scrollbar-thumb{
    background:rgba(255,255,255,.28);
    border-radius:4px;
}

.page-title{
    font-size:34px;
    font-weight:900;
    margin-bottom:10px;
    color:white;
    letter-spacing:-1px;
    text-shadow:0 2px 14px rgba(0,0,0,.45);
}

.page-sub{
    color:var(--muted);
    font-size:14px;
    line-height:1.7;
    margin-bottom:28px;
}

.grid{
    display:grid;
    grid-template-columns:repeat(3,minmax(0,1fr));
    gap:16px;
    margin-bottom:28px;
}

.card{
    background:rgba(0,0,0,.34);
    border:1px solid rgba(255,255,255,.20);
    border-radius:5px;
    padding:18px;
    transition:.18s ease;
}

.card:hover{
    background:rgba(0,0,0,.46);
    border-color:rgba(157,228,255,.58);
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
}

.card-text{
    color:var(--muted);
    font-size:14px;
    line-height:1.6;
    margin-top:8px;
}

.line{
    border-left:1px solid rgba(255,255,255,.34);
    padding-left:24px;
    max-width:980px;
}

input,
textarea{
    background:rgba(0,0,0,.36);
    color:white;
    border:1px solid rgba(255,255,255,.34);
    padding:13px 14px;
    outline:none;
    border-radius:4px;
    font-size:14px;
    backdrop-filter:blur(10px);
}

input::placeholder,
textarea::placeholder{
    color:rgba(255,255,255,.62);
}

input:focus,
textarea:focus{
    border-color:var(--blue);
    background:rgba(0,0,0,.48);
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

.row{
    margin-bottom:16px;
}

.file-row,
.topic-row,
.credit-row,
.comment-row{
    margin-bottom:16px;
    padding:16px 18px;
    background:rgba(0,0,0,.34);
    border:1px solid rgba(255,255,255,.22);
    border-radius:5px;
    transition:.18s ease;
}

.file-row:hover,
.topic-row:hover,
.credit-row:hover,
.comment-row:hover{
    background:rgba(0,0,0,.46);
    border-color:rgba(157,228,255,.58);
    transform:translateY(-1px);
}

.file-title,
.credit-name,
.topic-title{
    font-size:15px;
    color:white;
    margin-bottom:7px;
    font-weight:900;
}

.meta,
.topic-meta,
.comment-meta,
.small{
    color:var(--muted);
    font-size:14px;
    line-height:1.7;
}

.body-text{
    white-space:pre-wrap;
}

.file-link,
.topic-open,
.fake-link{
    color:var(--blue);
    text-decoration:none;
    font-size:14px;
    word-break:break-all;
    transition:.18s ease;
    cursor:pointer;
    font-weight:900;
}

.file-link:hover,
.topic-open:hover,
.fake-link:hover{
    color:white;
    padding-left:5px;
}

.form-box{
    margin-top:32px;
    border-left:1px solid rgba(255,255,255,.34);
    padding-left:24px;
    max-width:740px;
}

button,
.file-button{
    display:inline-flex;
    align-items:center;
    justify-content:center;
    gap:10px;
    background:#071827;
    color:white;
    border:1px solid rgba(255,255,255,.28);
    padding:12px 18px;
    border-radius:4px;
    cursor:pointer;
    transition:.18s ease;
    font-size:14px;
    text-decoration:none;
    font-weight:900;
    backdrop-filter:blur(10px);
}

button:hover,
.file-button:hover{
    background:#102b43;
    border-color:var(--blue);
    transform:translateY(-1px);
}

.primary-btn{
    background:white;
    color:#06101d;
    border-color:white;
}

.primary-btn:hover{
    background:#e9f8ff;
    color:#000;
}

.danger-btn{
    background:rgba(80,0,0,.45);
    border-color:rgba(255,90,90,.50);
    color:#ffdede;
}

.danger-btn:hover{
    background:rgba(120,0,0,.60);
    border-color:#ff5757;
}

.account-box{
    width:430px;
    background:rgba(0,0,0,.38);
    border:1px solid rgba(255,255,255,.28);
    border-radius:5px;
    padding:28px;
    box-shadow:0 20px 55px rgba(0,0,0,.35);
}

.login-card-title{
    font-size:25px;
    color:white;
    margin-bottom:8px;
    font-weight:900;
    letter-spacing:-.5px;
}

.login-card-sub{
    color:var(--muted);
    font-size:14px;
    margin-bottom:24px;
    word-break:break-all;
}

.login-btn{
    width:100%;
    margin-bottom:12px;
    height:46px;
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
    border-left:1px solid rgba(157,228,255,.42);
}

.account-section{
    margin-top:28px;
    padding-top:22px;
    border-top:1px solid rgba(255,255,255,.22);
}

.account-pfp{
    display:flex;
    align-items:center;
    gap:14px;
    margin-bottom:22px;
}

.account-pfp .pfp-box{
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
    border-top:1px solid rgba(255,255,255,.28);
    width:280px;
    margin:24px 0;
}

.pill-row{
    display:flex;
    flex-wrap:wrap;
    gap:10px;
    margin-bottom:28px;
}

.pill{
    color:white;
    background:rgba(255,255,255,.09);
    border:1px solid rgba(255,255,255,.18);
    padding:9px 12px;
    border-radius:4px;
    font-size:13px;
    font-weight:800;
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

    .account-box,
    .search-bar{
        width:100%;
    }
}
</style>
</head>

<body>

{% if not user_email %}

<div class="login-only">
    <div class="login-shell">
        <div class="login-inner">
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
                        <span class="login-icon">◆</span>
                    </div>

                    <div class="login-input-wrap">
                        <input name="email" type="email" placeholder="Email" required>
                        <span class="login-icon">✉</span>
                    </div>

                    <div class="login-input-wrap">
                        <input name="password" type="password" placeholder="Password" required>
                        <span class="login-icon">■</span>
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
                <div class="login-sub">Private producer space for files, discussion, credits and account settings.</div>

                <form action="/login" method="POST">
                    <div class="login-input-wrap">
                        <input name="email" type="email" placeholder="Email" required>
                        <span class="login-icon">✉</span>
                    </div>

                    <div class="login-input-wrap">
                        <input name="password" type="password" placeholder="Password" required>
                        <span class="login-icon">■</span>
                    </div>

                    <div class="login-row">
                        <label><input type="checkbox" checked> Remember me</label>
                        <span>discord db</span>
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
</div>

{% else %}

<div class="app">
    <div class="side">
        <div class="title">FLOWZNMELHOR</div>

        <div class="user-mini">
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
            <div class="item" id="menuNotifications" onclick="showNotifications(this)">notifications <span style="color:#9fe7ff">0</span></div>
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
const files = {{ files|tojson }};
const credits = {{ credits|tojson }};
const discussions = {{ topics|tojson }};
const userEmail = {{ user_email|tojson }};
const username = {{ username|tojson }};
const pfpUrl = {{ pfp_url|tojson }};
const startView = {{ start_view|tojson }};
const startTopicId = {{ start_topic_id|tojson }};
const maxFileMb = {{ max_file_mb|tojson }};

function clickEffect(el){
    if(!el) return;
    el.classList.remove("clicked");
    void el.offsetWidth;
    el.classList.add("clicked");
}

document.addEventListener("click", function(e){
    const target = e.target.closest("button, .file-button, .item");
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

function setUrl(view, topicId=null){
    if(view === "topic" && topicId){
        window.history.replaceState(null, "", "/?view=topic&id=" + encodeURIComponent(topicId));
    }else{
        window.history.replaceState(null, "", "/?view=" + encodeURIComponent(view));
    }
}

function pfpHtml(){
    if(pfpUrl){
        return `<div class="pfp-box"><img src="${escapeAttr(pfpUrl)}" alt="pfp"></div>`;
    }
    return `<div class="pfp-box">${escapeHtml(username.charAt(0).toUpperCase())}</div>`;
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
        <div class="page-title">producer room</div>
        <div class="page-sub">
            Upload ZIP packs, share MP3 previews, start discussions, and build a private funk producer space.
        </div>

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
                <div class="card-label">comments</div>
                <div class="card-number">${totalComments}</div>
                <div class="card-text">Community replies and feedback.</div>
            </div>
        </div>

        <div class="pill-row">
            <div class="pill">MTG ideas</div>
            <div class="pill">FL Studio packs</div>
            <div class="pill">Beat feedback</div>
            <div class="pill">Producer chat</div>
            <div class="pill">Brazil funk</div>
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
                <div class="meta">new file · ${escapeHtml(file.size)} · by ${escapeHtml(file.author)}</div>
                <a class="file-link" href="/download/${encodeURIComponent(file.id)}" target="_blank">download</a>
            </div>
        `;
    });

    recentTopics.forEach(topic=>{
        html += `
            <div class="topic-row">
                <div class="topic-title">${escapeHtml(topic.title)}</div>
                <div class="topic-meta">by ${escapeHtml(topic.author)} · ${topic.comments.length} comments</div>
                <div class="topic-open" onclick="openTopic('${topic.id}')">open discussion</div>
            </div>
        `;
    });

    html += `
        </div>

        <div class="form-box">
            <div class="topic-title">make it feel alive</div>
            <p class="small">
                Add daily challenges, recent uploads, trending packs, beat feedback posts, top producers, and small stats.
                Empty pages feel dead. Cards, activity, and challenges make the site feel active.
            </p>
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
        <div class="page-sub">Upload ZIP packs or MP3 previews. Use ZIP for FLP, stems, drum kits and folders.</div>

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
                <div class="meta">${escapeHtml(file.size)} · by ${escapeHtml(file.author)}</div>
                <a class="file-link" href="/download/${encodeURIComponent(file.id)}" target="_blank">download</a>
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
            <p class="small">allowed: zip and mp3 only. maximum size: ${maxFileMb} MB.</p>
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
                <div class="topic-meta">by ${escapeHtml(topic.author)} · ${topic.comments.length} comments</div>
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
        <div class="page-sub">by ${escapeHtml(topic.author)}</div>

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
                <div class="comment-meta">${escapeHtml(comment.author)}</div>
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
        </div>
    `;

    fadeChange(html);
}

function showAccount(button){
    clickEffect(button);
    clearActive();
    if(button) button.classList.add("active");

    setUrl("account");

    let html=`
        <div class="page-title">account</div>
        <div class="page-sub">Edit your profile picture, username, password or logout.</div>

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
                    <div class="small">allowed: png, jpg, jpeg, webp, gif.</div>
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
                    <form action="/logout" method="POST">
                        <button class="login-btn" type="submit">logout</button>
                    </form>
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
        <div class="page-sub">No notifications yet.</div>
        <div class="line">
            <div class="small">This section is ready for future likes, comments, mentions, and upload alerts.</div>
        </div>
    `;

    fadeChange(html);
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

    setTimeout(()=>{
        if(startView === "files"){
            showFiles(document.getElementById("menuFiles"));
        }else if(startView === "discussion"){
            showDiscussion(document.getElementById("menuDiscussion"));
        }else if(startView === "topic" && startTopicId){
            openTopic(startTopicId);
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


# ============================================================
# ROUTES
# ============================================================

@app.route("/")
def home():
    logged_in = bool(current_email())
    requested_view = request.args.get("view", "")
    requested_topic = request.args.get("id", "")

    if not logged_in:
        auth_mode = "register" if requested_view == "register" else "login"
        requested_view = "login"
        requested_topic = ""

        return render_template_string(
            HTML,
            files=[],
            topics=[],
            credits=CREDITS,
            user_email=None,
            username="",
            pfp_url="",
            start_view=requested_view,
            start_topic_id=requested_topic,
            auth_mode=auth_mode,
            max_file_mb=MAX_FILE_SIZE // (1024 * 1024)
        )

    auth_mode = ""

    if requested_view in ["login", "register", ""]:
        requested_view = "dashboard"

    allowed_views = {
        "dashboard",
        "files",
        "discussion",
        "topic",
        "account",
        "credits",
        "notifications"
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
            "username": current_email()
        }

    if not user:
        session.clear()
        flash("Account not found. Please login again.", "error")
        return redirect(url_for("home", view="login"))

    files = [public_file(file_data, db) for file_data in db["files"].values()]
    files.sort(key=lambda x: x["created"], reverse=True)

    topics = [public_topic(topic_data, db) for topic_data in db["topics"].values()]
    topics.sort(key=lambda x: x["created"], reverse=True)

    return render_template_string(
        HTML,
        files=files,
        topics=topics,
        credits=CREDITS,
        user_email=user.get("email", ""),
        username=user.get("username", user.get("email", "")),
        pfp_url=pfp_url_from_user(user, store),
        start_view=requested_view,
        start_topic_id=requested_topic,
        auth_mode=auth_mode,
        max_file_mb=MAX_FILE_SIZE // (1024 * 1024)
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


@app.route("/logout", methods=["POST"])
@login_required
def logout():
    session.clear()
    flash("Logged out.", "success")
    return redirect(url_for("home", view="login"))


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
        mimetype=file_data.get("content_type", "application/octet-stream")
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

    return send_file(
        BytesIO(response.content),
        mimetype=content_type
    )


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

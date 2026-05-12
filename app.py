import os
import json
import time
import base64
import hashlib
import secrets
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
    send_file
)
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename


app = Flask(__name__)

# ============================================================
# CONFIG
# ============================================================

app.secret_key = os.environ.get("SECRET_KEY", secrets.token_hex(64))

DISCORD_BOT_TOKEN = os.environ.get("DISCORD_BOT_TOKEN", "").strip()
DISCORD_DB_CHANNEL_ID = os.environ.get("DISCORD_DB_CHANNEL_ID", "").strip()

DISCORD_API = "https://discord.com/api/v10"

MAX_FILE_SIZE = int(os.environ.get("MAX_FILE_SIZE", str(8 * 1024 * 1024)))
app.config["MAX_CONTENT_LENGTH"] = MAX_FILE_SIZE

ALLOWED_EXTENSIONS = {".zip", ".mp3"}

CACHE_SECONDS = 3
CACHE = {
    "time": 0,
    "db": None
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
# DISCORD DATABASE SYSTEM
# ============================================================

def require_discord_config():
    if not DISCORD_BOT_TOKEN:
        raise RuntimeError("Missing DISCORD_BOT_TOKEN environment variable.")
    if not DISCORD_DB_CHANNEL_ID:
        raise RuntimeError("Missing DISCORD_DB_CHANNEL_ID environment variable.")


def discord_headers():
    require_discord_config()
    return {
        "Authorization": f"Bot {DISCORD_BOT_TOKEN}"
    }


def discord_request(method, url, **kwargs):
    require_discord_config()

    headers = kwargs.pop("headers", {})
    merged_headers = discord_headers()
    merged_headers.update(headers)

    for _ in range(4):
        response = requests.request(
            method,
            url,
            headers=merged_headers,
            timeout=30,
            **kwargs
        )

        if response.status_code == 429:
            try:
                retry_after = float(response.json().get("retry_after", 1))
            except Exception:
                retry_after = 1
            time.sleep(retry_after)
            continue

        if response.status_code < 200 or response.status_code >= 300:
            raise RuntimeError(
                f"Discord API error {response.status_code}: {response.text[:500]}"
            )

        return response

    raise RuntimeError("Discord API rate limit retry failed.")


def encode_json(data):
    raw = json.dumps(data, separators=(",", ":"), ensure_ascii=False)
    return base64.urlsafe_b64encode(raw.encode("utf-8")).decode("utf-8")


def decode_json(encoded):
    padding = "=" * (-len(encoded) % 4)
    raw = base64.urlsafe_b64decode((encoded + padding).encode("utf-8"))
    return json.loads(raw.decode("utf-8"))


def user_key(email):
    email = email.strip().lower()
    return hashlib.sha256(email.encode("utf-8")).hexdigest()


def make_record_message(kind, key, data):
    data = dict(data)
    data["updated_at"] = int(time.time())

    encoded = encode_json(data)
    content = f"SWDB|{kind}|{key}|{encoded}"

    if len(content) > 1900:
        raise ValueError("Data is too long for one Discord message. Make the text shorter.")

    return content


def make_file_message(metadata):
    metadata = dict(metadata)
    metadata["updated_at"] = int(time.time())

    encoded = encode_json(metadata)
    content = f"SWFILE|{encoded}"

    if len(content) > 1900:
        raise ValueError("File metadata is too long for one Discord message.")

    return content


def fetch_all_discord_messages():
    url = f"{DISCORD_API}/channels/{DISCORD_DB_CHANNEL_ID}/messages"

    all_messages = []
    before = None

    for _ in range(25):
        params = {
            "limit": 100
        }

        if before:
            params["before"] = before

        response = discord_request("GET", url, params=params)
        messages = response.json()

        if not messages:
            break

        all_messages.extend(messages)
        before = messages[-1]["id"]

        if len(messages) < 100:
            break

    return all_messages


def post_discord_message(content):
    url = f"{DISCORD_API}/channels/{DISCORD_DB_CHANNEL_ID}/messages"

    response = discord_request(
        "POST",
        url,
        headers={"Content-Type": "application/json"},
        json={"content": content}
    )

    CACHE["time"] = 0
    CACHE["db"] = None

    return response.json()


def post_discord_file(content, filename, file_bytes, content_type):
    url = f"{DISCORD_API}/channels/{DISCORD_DB_CHANNEL_ID}/messages"

    payload = {
        "content": content
    }

    files = {
        "payload_json": (None, json.dumps(payload), "application/json"),
        "files[0]": (filename, BytesIO(file_bytes), content_type or "application/octet-stream")
    }

    response = discord_request("POST", url, files=files)

    CACHE["time"] = 0
    CACHE["db"] = None

    return response.json()


def load_db():
    now = time.time()

    if CACHE["db"] is not None and now - CACHE["time"] < CACHE_SECONDS:
        return CACHE["db"]

    messages = fetch_all_discord_messages()

    users = {}
    topics = {}
    comments = {}
    files = {}

    # Discord returns newest first.
    # For users/topics, newest record wins.
    for message in messages:
        content = message.get("content", "") or ""

        if content.startswith("SWDB|"):
            try:
                _, kind, key, encoded = content.split("|", 3)
                data = decode_json(encoded)
            except Exception:
                continue

            if kind == "user":
                if key not in users:
                    users[key] = data

            elif kind == "topic":
                if key not in topics:
                    topics[key] = data

            elif kind == "comment":
                if key not in comments:
                    comments[key] = data

        elif content.startswith("SWFILE|"):
            try:
                encoded = content.split("|", 1)[1]
                data = decode_json(encoded)
            except Exception:
                continue

            file_id = data.get("id")
            attachments = message.get("attachments", [])

            if file_id and file_id not in files and attachments:
                attachment = attachments[0]
                data["message_id"] = message.get("id")
                data["attachment_url"] = attachment.get("url")
                data["discord_filename"] = attachment.get("filename")
                files[file_id] = data

    db = {
        "users": users,
        "topics": topics,
        "comments": comments,
        "files": files
    }

    CACHE["time"] = now
    CACHE["db"] = db

    return db


def save_user(user):
    key = user_key(user["email"])
    content = make_record_message("user", key, user)
    post_discord_message(content)


def save_topic(topic):
    content = make_record_message("topic", topic["id"], topic)
    post_discord_message(content)


def save_comment(comment):
    content = make_record_message("comment", comment["id"], comment)
    post_discord_message(content)


def save_file_record(metadata, filename, file_bytes, content_type):
    content = make_file_message(metadata)
    return post_discord_file(content, filename, file_bytes, content_type)


# ============================================================
# HELPERS
# ============================================================

def allowed_file(filename):
    ext = os.path.splitext(filename.lower())[1]
    return ext in ALLOWED_EXTENSIONS


def size_text(size):
    if size >= 1024 * 1024:
        return f"{size / (1024 * 1024):.1f} MB"
    return f"{size / 1024:.1f} KB"


def current_email():
    return session.get("email")


def current_user():
    email = current_email()

    if not email:
        return None

    db = load_db()
    return db["users"].get(user_key(email))


def current_username():
    user = current_user()

    if not user:
        return ""

    return user.get("username", user.get("email", ""))


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


def valid_username(username):
    username = username.strip()

    if len(username) < 3 or len(username) > 20:
        return False

    allowed = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_."

    for char in username:
        if char not in allowed:
            return False

    return True


def author_name(email, fallback="unknown"):
    db = load_db()
    user = db["users"].get(user_key(email or ""))

    if user:
        return user.get("username", fallback)

    return fallback


def public_file(file_data):
    return {
        "id": file_data.get("id"),
        "name": file_data.get("original_name", "file"),
        "size": size_text(int(file_data.get("size", 0))),
        "author": author_name(file_data.get("author_email"), file_data.get("author", "unknown")),
        "created": file_data.get("created", 0)
    }


def public_topic(topic_data):
    db = load_db()
    topic_id = topic_data.get("id")

    topic_comments = [
        c for c in db["comments"].values()
        if c.get("topic_id") == topic_id
    ]

    topic_comments.sort(key=lambda x: int(x.get("created", 0)))

    return {
        "id": topic_id,
        "title": topic_data.get("title", ""),
        "body": topic_data.get("body", ""),
        "author": author_name(topic_data.get("author_email"), topic_data.get("author", "unknown")),
        "created": topic_data.get("created", 0),
        "comments": [
            {
                "id": c.get("id"),
                "body": c.get("body", ""),
                "author": author_name(c.get("author_email"), c.get("author", "unknown")),
                "created": c.get("created", 0)
            }
            for c in topic_comments
        ]
    }


def go(view="dashboard", topic_id=None):
    if topic_id:
        return redirect(url_for("home", view=view, id=topic_id))

    return redirect(url_for("home", view=view))


# ============================================================
# HTML
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
    --blue:#9fe7ff;
    --dark:#06101d;
    --panel:rgba(3,13,24,.78);
    --line:rgba(255,255,255,.24);
    --line2:rgba(255,255,255,.12);
}

body{
    margin:0;
    min-height:100vh;
    color:var(--text);
    font-family:Arial,Helvetica,sans-serif;
    background:
        linear-gradient(rgba(2,8,16,.72), rgba(2,8,16,.88)),
        radial-gradient(circle at 18% 18%, rgba(125,215,255,.34), transparent 28%),
        radial-gradient(circle at 84% 78%, rgba(43,110,190,.32), transparent 35%);
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
}

a{
    color:var(--blue);
    text-decoration:none;
    font-weight:900;
}

button,
.file-button{
    display:inline-flex;
    align-items:center;
    justify-content:center;
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

input,
textarea{
    background:rgba(0,0,0,.36);
    color:white;
    border:1px solid rgba(255,255,255,.34);
    padding:13px 14px;
    outline:none;
    border-radius:4px;
    font-size:14px;
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

.login-only{
    min-height:100vh;
    display:flex;
    justify-content:center;
    align-items:center;
    padding:20px;
    position:relative;
    z-index:2;
}

.login-shell{
    width:390px;
    background:rgba(220,245,255,.20);
    border:1px solid rgba(255,255,255,.42);
    backdrop-filter:blur(18px);
    -webkit-backdrop-filter:blur(18px);
    box-shadow:0 28px 80px rgba(0,0,0,.50);
    border-radius:6px;
    padding:32px;
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
}

.login-sub{
    color:rgba(255,255,255,.76);
    font-size:14px;
    line-height:1.6;
    margin-bottom:26px;
}

.login-input{
    width:100%;
    margin-bottom:12px;
}

.login-btn{
    width:100%;
    margin-bottom:12px;
    height:46px;
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

.app{
    position:relative;
    z-index:2;
    min-height:100vh;
    display:flex;
    padding:16px;
    gap:16px;
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
    display:block;
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
    text-decoration:none;
    font-weight:400;
}

.item:hover,
.item.active{
    color:white;
    background:rgba(255,255,255,.13);
    transform:translateX(4px);
}

.content{
    flex:1;
    padding:42px;
    overflow-y:auto;
    border:1px solid var(--line);
    background:var(--panel);
    backdrop-filter:blur(20px);
    -webkit-backdrop-filter:blur(20px);
    box-shadow:0 22px 65px rgba(0,0,0,.42);
    border-radius:6px;
}

.page-title{
    font-size:34px;
    font-weight:900;
    margin-bottom:10px;
    color:white;
    letter-spacing:-1px;
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

.card-text,
.small,
.meta,
.topic-meta,
.comment-meta{
    color:var(--muted);
    font-size:14px;
    line-height:1.7;
}

.line{
    border-left:1px solid rgba(255,255,255,.34);
    padding-left:24px;
    max-width:980px;
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

.form-box{
    margin-top:32px;
    border-left:1px solid rgba(255,255,255,.34);
    padding-left:24px;
    max-width:740px;
}

.search-bar{
    width:330px;
    max-width:100%;
    margin-bottom:26px;
}

.account-box{
    width:430px;
    max-width:100%;
    background:rgba(0,0,0,.38);
    border:1px solid rgba(255,255,255,.28);
    border-radius:5px;
    padding:28px;
    box-shadow:0 20px 55px rgba(0,0,0,.35);
}

.account-section{
    margin-top:28px;
    padding-top:22px;
    border-top:1px solid rgba(255,255,255,.22);
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

.selected-file{
    color:var(--muted);
    font-size:14px;
    margin-left:10px;
}

@media(max-width:900px){
    .app{
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

        {% if view == "register" %}
            <div class="login-title">Create account</div>
            <div class="login-sub">Join the private producer room.</div>

            <form action="/register" method="POST">
                <input class="login-input" name="username" placeholder="Username" required>
                <input class="login-input" name="email" type="email" placeholder="Email" required>
                <input class="login-input" name="password" type="password" placeholder="Password" required>
                <button class="login-btn primary-btn" type="submit">Create Account</button>
            </form>

            <div class="login-line"></div>
            <button class="login-btn" onclick="location.href='/?view=login'">Back to Login</button>

            <div class="switch-text">
                Already have an account? <a href="/?view=login">Login</a>
            </div>
        {% else %}
            <div class="login-title">Login</div>
            <div class="login-sub">Private producer space. Data is stored in your Discord database channel.</div>

            <form action="/login" method="POST">
                <input class="login-input" name="email" type="email" placeholder="Email" required>
                <input class="login-input" name="password" type="password" placeholder="Password" required>
                <button class="login-btn" type="submit">Login</button>
            </form>

            <div class="login-line"></div>
            <button class="login-btn primary-btn" onclick="location.href='/?view=register'">Create Account</button>

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

        <div class="user-mini">
            <div class="user-mini-name">{{ username }}</div>
            <div class="user-mini-mail">{{ user_email }}</div>
        </div>

        <div class="menu-main">
            <a class="item {% if view == 'dashboard' %}active{% endif %}" href="/?view=dashboard">home</a>
            <a class="item {% if view == 'files' %}active{% endif %}" href="/?view=files">files</a>
            <a class="item {% if view == 'discussion' or view == 'topic' %}active{% endif %}" href="/?view=discussion">discussion</a>
        </div>

        <div class="menu-bottom">
            <a class="item {% if view == 'account' %}active{% endif %}" href="/?view=account">account</a>
            <a class="item {% if view == 'credits' %}active{% endif %}" href="/?view=credits">credits</a>
        </div>
    </div>

    <div class="content">
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
            <div class="page-sub">
                Upload ZIP packs, share MP3 previews, start discussions and build a private funk producer space.
            </div>

            <div class="grid">
                <div class="card">
                    <div class="card-label">uploaded files</div>
                    <div class="card-number">{{ files|length }}</div>
                    <div class="card-text">ZIP packs and MP3 previews.</div>
                </div>

                <div class="card">
                    <div class="card-label">topics</div>
                    <div class="card-number">{{ topics|length }}</div>
                    <div class="card-text">Producer questions and ideas.</div>
                </div>

                <div class="card">
                    <div class="card-label">comments</div>
                    <div class="card-number">{{ total_comments }}</div>
                    <div class="card-text">Community replies and feedback.</div>
                </div>
            </div>

            <div class="line">
                <div class="topic-title">recent files</div>
                <br>

                {% if files|length == 0 %}
                    <div class="small">No files yet.</div>
                {% endif %}

                {% for file in files[:3] %}
                    <div class="file-row">
                        <div class="file-title">{{ file.name }}</div>
                        <div class="meta">by {{ file.author }} · {{ file.size }}</div>
                        <a href="/download/{{ file.id }}">download</a>
                    </div>
                {% endfor %}

                <br>
                <div class="topic-title">recent discussions</div>
                <br>

                {% if topics|length == 0 %}
                    <div class="small">No topics yet.</div>
                {% endif %}

                {% for topic in topics[:3] %}
                    <div class="topic-row">
                        <div class="topic-title">{{ topic.title }}</div>
                        <div class="topic-meta">by {{ topic.author }} · {{ topic.comments|length }} comments</div>
                        <div class="small">{{ topic.body[:160] }}{% if topic.body|length > 160 %}...{% endif %}</div>
                        <br>
                        <a href="/?view=topic&id={{ topic.id }}">open discussion</a>
                    </div>
                {% endfor %}
            </div>

            <div class="form-box">
                <button onclick="location.href='/?view=files'">upload file</button>
                <button onclick="location.href='/?view=discussion'">start discussion</button>
            </div>
        {% endif %}

        {% if view == "files" %}
            <div class="page-title">files</div>
            <div class="page-sub">Upload ZIP packs or MP3 previews. Files are stored as Discord attachments.</div>

            <input id="searchInput" class="search-bar" placeholder="search files" oninput="filterRows('searchInput', '.file-row')">

            <div class="line">
                {% if files|length == 0 %}
                    <div class="small">No files yet.</div>
                {% endif %}

                {% for file in files %}
                    <div class="file-row" data-search="{{ file.name|lower }} {{ file.author|lower }}">
                        <div class="file-title">{{ file.name }}</div>
                        <div class="meta">by {{ file.author }} · {{ file.size }}</div>
                        <a href="/download/{{ file.id }}">download</a>
                    </div>
                {% endfor %}
            </div>

            <div class="form-box">
                <form action="/upload" method="POST" enctype="multipart/form-data">
                    <input id="fileInput" type="file" name="uploadfile" accept=".zip,.mp3" required hidden>
                    <label for="fileInput" class="file-button">select zip or mp3</label>
                    <span id="fileName" class="selected-file">no file selected</span>
                    <br><br>
                    <button class="primary-btn" type="submit">upload file</button>
                </form>
                <p class="small">Allowed: ZIP and MP3 only. Max size: {{ max_file_mb }} MB.</p>
            </div>
        {% endif %}

        {% if view == "discussion" %}
            <div class="page-title">discussion</div>
            <div class="page-sub">Ask for feedback, share FL Studio tricks, post beat ideas or start producer challenges.</div>

            <input id="discussionSearchInput" class="search-bar" placeholder="search discussions" oninput="filterRows('discussionSearchInput', '.topic-row')">

            <div class="line">
                {% if topics|length == 0 %}
                    <div class="small">No topics yet.</div>
                {% endif %}

                {% for topic in topics %}
                    <div class="topic-row" data-search="{{ topic.title|lower }} {{ topic.author|lower }} {{ topic.body|lower }}">
                        <div class="topic-title">{{ topic.title }}</div>
                        <div class="topic-meta">by {{ topic.author }} · {{ topic.comments|length }} comments</div>
                        <div class="small">{{ topic.body[:160] }}{% if topic.body|length > 160 %}...{% endif %}</div>
                        <br>
                        <a href="/?view=topic&id={{ topic.id }}">open discussion</a>
                    </div>
                {% endfor %}
            </div>

            <div class="form-box">
                <form action="/topic" method="POST">
                    <input name="title" placeholder="topic title" required style="width:100%;">
                    <textarea name="body" placeholder="write topic text" required></textarea>
                    <br><br>
                    <button class="primary-btn" type="submit">add topic</button>
                </form>
            </div>
        {% endif %}

        {% if view == "topic" %}
            {% if selected_topic %}
                <div class="page-title">{{ selected_topic.title }}</div>
                <div class="page-sub">by {{ selected_topic.author }}</div>

                <button onclick="location.href='/?view=discussion'">back to discussion</button>
                <br><br>

                <div class="line">
                    <div class="small">{{ selected_topic.body }}</div>
                    <br><br>

                    <div class="topic-title">comments</div>
                    <br>

                    {% if selected_topic.comments|length == 0 %}
                        <div class="small">No comments yet.</div>
                    {% endif %}

                    {% for comment in selected_topic.comments %}
                        <div class="comment-row">
                            <div class="comment-meta">{{ comment.author }}</div>
                            <div class="small">{{ comment.body }}</div>
                        </div>
                    {% endfor %}
                </div>

                <div class="form-box">
                    <form action="/comment/{{ selected_topic.id }}" method="POST">
                        <textarea name="body" placeholder="write comment" required></textarea>
                        <br><br>
                        <button class="primary-btn" type="submit">add comment</button>
                    </form>
                </div>
            {% else %}
                <div class="page-title">topic not found</div>
                <div class="page-sub">The discussion topic does not exist.</div>
                <button onclick="location.href='/?view=discussion'">back to discussion</button>
            {% endif %}
        {% endif %}

        {% if view == "account" %}
            <div class="page-title">account</div>
            <div class="page-sub">Edit your username, password or logout.</div>

            <div class="line">
                <div class="account-box">
                    <div class="topic-title">{{ username }}</div>
                    <div class="small">{{ user_email }}</div>

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
        {% endif %}

        {% if view == "credits" %}
            <div class="page-title">credits</div>
            <div class="page-sub">People behind the site.</div>

            <div class="line">
                <div class="credit-heading">OWNERS</div>
                {% for person in credits["OWNERS"] %}
                    <div class="credit-row">
                        <div class="credit-name">{{ person }}</div>
                    </div>
                {% endfor %}

                <div class="credit-divider"></div>

                <div class="credit-heading">MEMBERS</div>
                {% for person in credits["MEMBERS"] %}
                    <div class="credit-row">
                        <div class="credit-name">{{ person }}</div>
                    </div>
                {% endfor %}

                <div class="credit-divider"></div>

                <div class="credit-heading">WEBSITE MADE BY</div>
                {% for person in credits["WEBSITE MADE BY"] %}
                    <div class="credit-row">
                        <div class="credit-name">{{ person }}</div>
                    </div>
                {% endfor %}
            </div>
        {% endif %}
    </div>
</div>

{% endif %}

<script>
function filterRows(inputId, rowSelector){
    const input = document.getElementById(inputId);

    if(!input){
        return;
    }

    const search = input.value.toLowerCase();

    document.querySelectorAll(rowSelector).forEach(row=>{
        const text = row.getAttribute("data-search") || row.innerText.toLowerCase();
        row.style.display = text.includes(search) ? "block" : "none";
    });
}

const fileInput = document.getElementById("fileInput");
const fileName = document.getElementById("fileName");

if(fileInput && fileName){
    fileInput.addEventListener("change", ()=>{
        fileName.textContent = fileInput.files.length ? fileInput.files[0].name : "no file selected";
    });
}
</script>

</body>
</html>
"""


# ============================================================
# ROUTES
# ============================================================

@app.route("/")
def home():
    view = request.args.get("view", "dashboard")
    topic_id = request.args.get("id", "")

    logged_in = bool(current_email())

    if not logged_in:
        if view != "register":
            view = "login"

        return render_template_string(
            HTML,
            view=view,
            user_email=None,
            username="",
            files=[],
            topics=[],
            selected_topic=None,
            total_comments=0,
            credits=CREDITS,
            max_file_mb=MAX_FILE_SIZE // (1024 * 1024)
        )

    allowed_views = {"dashboard", "files", "discussion", "topic", "account", "credits"}

    if view not in allowed_views:
        view = "dashboard"

    try:
        user = current_user()
        db = load_db()
    except Exception as e:
        flash(f"Discord database error: {e}", "error")
        return render_template_string(
            HTML,
            view="dashboard",
            user_email=current_email(),
            username=current_email(),
            files=[],
            topics=[],
            selected_topic=None,
            total_comments=0,
            credits=CREDITS,
            max_file_mb=MAX_FILE_SIZE // (1024 * 1024)
        )

    if not user:
        session.clear()
        flash("Account not found. Please login again.", "error")
        return redirect(url_for("home", view="login"))

    files = [public_file(f) for f in db["files"].values()]
    files.sort(key=lambda x: int(x.get("created", 0)), reverse=True)

    topics = [public_topic(t) for t in db["topics"].values()]
    topics.sort(key=lambda x: int(x.get("created", 0)), reverse=True)

    selected_topic = None

    if view == "topic" and topic_id:
        topic_data = db["topics"].get(topic_id)

        if topic_data:
            selected_topic = public_topic(topic_data)

    total_comments = len(db["comments"])

    return render_template_string(
        HTML,
        view=view,
        user_email=user.get("email"),
        username=user.get("username"),
        files=files,
        topics=topics,
        selected_topic=selected_topic,
        total_comments=total_comments,
        credits=CREDITS,
        max_file_mb=MAX_FILE_SIZE // (1024 * 1024)
    )


@app.route("/register", methods=["POST"])
def register():
    try:
        db = load_db()
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

    key = user_key(email)

    if key in db["users"]:
        flash("Account already exists. Please login instead.", "error")
        return redirect(url_for("home", view="login"))

    for existing_user in db["users"].values():
        if existing_user.get("username",users"]:
        flash("Account already exists. Please login instead.", "error")
        return redirect(url_for("home", view="login"))

    "").strip().lower() == username.lower():
            flash("Username already exists. Choose another one.", "error")
            return redirect(url_for("home", view="register"))

    user = {
        "id": key,
        "username": username,
        "email": email,
        "password_hash": generate_password_hash(password),
        "created": int(time.time())
    }

    try:
        save_user(user)
    except Exception as e:
        flash(f"Could not save user to Discord: {e}", "error")
        return redirect(url_for("home", view="register"))

    session["email"] = email

    flash("Account created successfully.", "success")
    return go("dashboard")


@app.route("/login", methods=["POST"])
def login():
    try:
        db = load_db()
    except Exception as e:
        flash(f"Discord database error: {e}", "error")
        return redirect(url_for("home", view="login"))

    email = request.form.get("email", "").strip().lower()
    password = request.form.get("password", "")

    user = db["users"].get(user_key(email))

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
    user = current_user()
    db = load_db()

    new_username = request.form.get("new_username", "").strip()

    if not valid_username(new_username):
        flash("Username must be 3-20 characters and only use letters, numbers, underscore, or dot.", "error")
        return go("account")

    for existing_user in db["users"].values():
        same_username = existing_user.get("username", "").strip().lower() == new_username.lower()
        different_account = existing_user.get("email", "").lower() != user.get("email", "").lower()

        if same_username and different_account:
            flash("Username already exists. Choose another one.", "error")
            return go("account")

    user["username"] = new_username

    try:
        save_user(user)
    except Exception as e:
        flash(f"Could not update username: {e}", "error")
        return go("account")

    flash("Username changed successfully.", "success")
    return go("account")


@app.route("/change-password", methods=["POST"])
@login_required
def change_password():
    user = current_user()

    old_password = request.form.get("old_password", "")
    new_password = request.form.get("new_password", "")

    if not check_password_hash(user.get("password_hash", ""), old_password):
        flash("Old password is wrong.", "error")
        return go("account")

    if len(new_password) < 6:
        flash("New password must be at least 6 characters.", "error")
        return go("account")

    user["password_hash"] = generate_password_hash(new_password)

    try:
        save_user(user)
    except Exception as e:
        flash(f"Could not update password: {e}", "error")
        return go("account")

    flash("Password changed successfully.", "success")
    return go("account")


@app.route("/upload", methods=["POST"])
@login_required
def upload():
    user = current_user()

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

    metadata = {
        "id": file_id,
        "original_name": original_name,
        "size": size,
        "author": user.get("username"),
        "author_email": user.get("email"),
        "created": int(time.time())
    }

    try:
        save_file_record(
            metadata=metadata,
            filename=original_name,
            file_bytes=file_bytes,
            content_type=uploaded.content_type
        )
    except Exception as e:
        flash(f"Could not upload file to Discord: {e}", "error")
        return go("files")

    flash("File uploaded successfully.", "success")
    return go("files")


@app.route("/download/<file_id>")
@login_required
def download(file_id):
    try:
        db = load_db()
    except Exception as e:
        flash(f"Discord database error: {e}", "error")
        return go("files")

    file_data = db["files"].get(file_id)

    if not file_data:
        flash("File not found.", "error")
        return go("files")

    attachment_url = file_data.get("attachment_url")

    if not attachment_url:
        flash("Discord attachment URL not found.", "error")
        return go("files")

    try:
        response = requests.get(attachment_url, timeout=60)
        response.raise_for_status()
    except Exception as e:
        flash(f"Could not download Discord attachment: {e}", "error")
        return go("files")

    return send_file(
        BytesIO(response.content),
        as_attachment=True,
        download_name=file_data.get("original_name", "download"),
        mimetype="application/octet-stream"
    )


@app.route("/topic", methods=["POST"])
@login_required
def add_topic():
    user = current_user()

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
        "author_email": user.get("email"),
        "created": int(time.time())
    }

    try:
        save_topic(topic)
    except Exception as e:
        flash(f"Could not save topic to Discord: {e}", "error")
        return go("discussion")

    flash("Topic added.", "success")
    return go("topic", topic_id)


@app.route("/comment/<topic_id>", methods=["POST"])
@login_required
def add_comment(topic_id):
    user = current_user()
    db = load_db()

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
        "author_email": user.get("email"),
        "created": int(time.time())
    }

    try:
        save_comment(comment)
    except Exception as e:
        flash(f"Could not save comment to Discord: {e}", "error")
        return go("topic", topic_id)

    flash("Comment added.", "success")
    return go("topic", topic_id)


@app.route("/discord-test")
def discord_test():
    try:
        messages = fetch_all_discord_messages()
        return f"DISCORD DATABASE WORKS. Messages found: {len(messages)}"
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

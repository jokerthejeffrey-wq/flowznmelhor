import os
import json
import time
import base64
import hashlib
import secrets
from io import BytesIO
from functools import wraps

import requests
from flask import Flask, render_template_string, request, redirect, url_for, session, flash, send_file
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", secrets.token_hex(64))

DISCORD_BOT_TOKEN = os.environ.get("DISCORD_BOT_TOKEN", "").strip()
DISCORD_DB_CHANNEL_ID = os.environ.get("DISCORD_DB_CHANNEL_ID", "").strip()
DISCORD_API = "https://discord.com/api/v10"

MAX_FILE_SIZE = int(os.environ.get("MAX_FILE_SIZE", str(8 * 1024 * 1024)))
app.config["MAX_CONTENT_LENGTH"] = MAX_FILE_SIZE
ALLOWED_EXTENSIONS = {".zip", ".mp3"}

CACHE_SECONDS = 2
CACHE = {"time": 0, "db": None}

CREDITS = {
    "OWNERS": ["DJ TUTTER", "DJ LIRA DA ZL"],
    "MEMBERS": ["DJ FRG 011", "DJ PLT 011", "DJ RGLX", "DJ RDC", "DJ SABA 7", "DJ RE7 013", "RSFI", "DJ RDC"],
    "WEBSITE MADE BY": ["DJ SABA 7"]
}


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
    CACHE["db"] = None


def encode_data(data):
    raw = json.dumps(data, separators=(",", ":"), ensure_ascii=False)
    return base64.urlsafe_b64encode(raw.encode("utf-8")).decode("utf-8")


def decode_data(encoded):
    encoded = encoded.strip()
    encoded += "=" * (-len(encoded) % 4)
    raw = base64.urlsafe_b64decode(encoded.encode("utf-8"))
    return json.loads(raw.decode("utf-8"))


def user_id_from_email(email):
    return hashlib.sha256(email.strip().lower().encode("utf-8")).hexdigest()


def make_record(kind, key, data):
    data = dict(data)
    data["updated_at"] = int(time.time())

    content = f"SWDB|{kind}|{key}|{encode_data(data)}"

    if len(content) > 1900:
        raise ValueError("Discord record is too long. Shorten the title/body/comment.")

    return content


def make_delete_record(kind, key, deleted_by):
    data = {
        "kind": kind,
        "key": key,
        "deleted_by": deleted_by,
        "deleted_at": int(time.time())
    }

    content = f"SWDEL|{kind}|{key}|{encode_data(data)}"

    if len(content) > 1900:
        raise ValueError("Discord delete record is too long.")

    return content


def make_file_record(metadata):
    metadata = dict(metadata)
    metadata["updated_at"] = int(time.time())

    content = f"SWFILE|{encode_data(metadata)}"

    if len(content) > 1900:
        raise ValueError("Discord file metadata is too long.")

    return content


def post_discord_message(content):
    r = discord_request(
        "POST",
        f"/channels/{DISCORD_DB_CHANNEL_ID}/messages",
        headers={"Content-Type": "application/json"},
        json={"content": content}
    )

    clear_cache()
    return r.json()


def post_discord_file(content, filename, file_bytes, content_type):
    payload = {"content": content}

    files = {
        "files[0]": (
            filename,
            BytesIO(file_bytes),
            content_type or "application/octet-stream"
        )
    }

    data = {
        "payload_json": json.dumps(payload)
    }

    r = discord_request(
        "POST",
        f"/channels/{DISCORD_DB_CHANNEL_ID}/messages",
        data=data,
        files=files
    )

    clear_cache()
    return r.json()


def fetch_discord_messages():
    all_messages = []
    before = None

    for _ in range(50):
        params = {"limit": 100}

        if before:
            params["before"] = before

        r = discord_request(
            "GET",
            f"/channels/{DISCORD_DB_CHANNEL_ID}/messages",
            params=params
        )

        messages = r.json()

        if not messages:
            break

        all_messages.extend(messages)
        before = messages[-1]["id"]

        if len(messages) < 100:
            break

    return all_messages


def load_db():
    now = time.time()

    if CACHE["db"] is not None and now - CACHE["time"] < CACHE_SECONDS:
        return CACHE["db"]

    messages = fetch_discord_messages()

    users = {}
    topics = {}
    comments = {}
    files = {}

    deleted_topics = set()
    deleted_comments = set()
    deleted_files = set()

    seen = set()

    # Discord returns newest first. First valid record wins.
    for msg in messages:
        content = msg.get("content", "") or ""

        if content.startswith("SWDEL|"):
            try:
                _, kind, key, encoded = content.split("|", 3)
                decode_data(encoded)
            except Exception:
                continue

            marker = (kind, key)

            if marker in seen:
                continue

            seen.add(marker)

            if kind == "topic":
                deleted_topics.add(key)
            elif kind == "comment":
                deleted_comments.add(key)
            elif kind == "file":
                deleted_files.add(key)

            continue

        if content.startswith("SWDB|"):
            try:
                _, kind, key, encoded = content.split("|", 3)
                data = decode_data(encoded)
            except Exception:
                continue

            marker = (kind, key)

            if marker in seen:
                continue

            seen.add(marker)

            if kind == "user":
                users[key] = data
            elif kind == "topic" and key not in deleted_topics:
                topics[key] = data
            elif kind == "comment" and key not in deleted_comments:
                comments[key] = data

            continue

        if content.startswith("SWFILE|"):
            try:
                encoded = content.split("|", 1)[1]
                data = decode_data(encoded)
            except Exception:
                continue

            file_id = data.get("id", "")
            marker = ("file", file_id)

            if not file_id:
                continue

            if marker in seen:
                continue

            if file_id in deleted_files:
                continue

            attachments = msg.get("attachments", [])

            if not attachments:
                continue

            seen.add(marker)

            attachment = attachments[0]

            data["message_id"] = msg.get("id")
            data["attachment_url"] = attachment.get("url")
            data["discord_filename"] = attachment.get("filename")

            files[file_id] = data

    db = {
        "users": users,
        "topics": topics,
        "comments": comments,
        "files": files,
        "deleted_topics": deleted_topics,
        "deleted_comments": deleted_comments,
        "deleted_files": deleted_files,
    }

    CACHE["time"] = now
    CACHE["db"] = db

    return db


def save_user(user):
    post_discord_message(make_record("user", user["id"], user))


def save_topic(topic):
    post_discord_message(make_record("topic", topic["id"], topic))


def save_comment(comment):
    post_discord_message(make_record("comment", comment["id"], comment))


def save_file(metadata, filename, file_bytes, content_type):
    post_discord_file(make_file_record(metadata), filename, file_bytes, content_type)


def delete_topic(topic_id, deleted_by):
    post_discord_message(make_delete_record("topic", topic_id, deleted_by))


def allowed_file(filename):
    return os.path.splitext(filename.lower())[1] in ALLOWED_EXTENSIONS


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

    return load_db()["users"].get(uid)


def username_from_id(uid, fallback="unknown"):
    if not uid:
        return fallback

    user = load_db()["users"].get(uid)

    if not user:
        return fallback

    return user.get("username", fallback)


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


def public_file(file_data):
    return {
        "id": file_data.get("id", ""),
        "name": file_data.get("original_name", "file"),
        "size": size_text(file_data.get("size", 0)),
        "author": username_from_id(
            file_data.get("author_id", ""),
            file_data.get("author", "unknown")
        ),
        "created": int(file_data.get("created", 0)),
    }


def public_topic(topic_data):
    db = load_db()

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
        "author": username_from_id(author_id, topic_data.get("author", "unknown")),
        "author_id": author_id,
        "created": int(topic_data.get("created", 0)),
        "can_delete": viewer_id == author_id,
        "comments": [
            {
                "id": c.get("id", ""),
                "body": c.get("body", ""),
                "author": username_from_id(
                    c.get("author_id", ""),
                    c.get("author", "unknown")
                ),
                "created": int(c.get("created", 0)),
            }
            for c in topic_comments
        ],
    }


HTML = """
<!DOCTYPE html>
<html>
<head>
<title>FlowZNmelhor</title>
<meta name="viewport" content="width=device-width, initial-scale=1">

<style>
*{box-sizing:border-box}
body{
    margin:0;
    background:#071019;
    color:#f4f8ff;
    font-family:Arial,Helvetica,sans-serif;
}
body:before{
    content:"";
    position:fixed;
    inset:0;
    background-image:
        linear-gradient(rgba(255,255,255,.035) 1px,transparent 1px),
        linear-gradient(90deg,rgba(255,255,255,.035) 1px,transparent 1px);
    background-size:24px 24px;
    pointer-events:none;
}
a{
    color:#9fe7ff;
    text-decoration:none;
    font-weight:900;
}
button,.btn,.file-button{
    display:inline-flex;
    align-items:center;
    justify-content:center;
    min-height:42px;
    padding:0 18px;
    background:#0d1e2c;
    border:1px solid #37516a;
    color:white;
    font-size:14px;
    font-weight:900;
    cursor:pointer;
    text-decoration:none;
}
button:hover,.btn:hover,.file-button:hover{
    border-color:#9fe7ff;
    background:#12314a;
}
.btn-white{
    background:white;
    color:#06101d;
    border-color:white;
}
.btn-red{
    background:#19090d;
    color:white;
    border-color:#b63848;
}
.btn-red:hover{
    background:#39111a;
    border-color:#ff5468;
}
input,textarea{
    background:#0b141d;
    border:1px solid #3d4b58;
    color:white;
    outline:none;
    padding:14px;
    font-size:14px;
    width:100%;
}
textarea{
    min-height:96px;
    resize:vertical;
}
input::placeholder,textarea::placeholder{
    color:#89929d;
}
.login-page{
    min-height:100vh;
    display:flex;
    align-items:center;
    justify-content:center;
    padding:20px;
    position:relative;
    z-index:2;
}
.login-box{
    width:390px;
    background:rgba(8,18,28,.92);
    border:1px solid #31404f;
    padding:32px;
    box-shadow:0 25px 70px rgba(0,0,0,.55);
}
.logo{
    font-size:12px;
    letter-spacing:6px;
    font-weight:900;
    color:white;
    margin-bottom:28px;
}
.logo:after{
    content:"";
    display:block;
    width:110px;
    height:2px;
    background:#9fe7ff;
    margin-top:12px;
}
.login-title{
    font-size:34px;
    font-weight:900;
    margin-bottom:8px;
}
.login-sub{
    color:#a8b2bd;
    line-height:1.6;
    margin-bottom:22px;
    font-size:14px;
}
.login-input{
    margin-bottom:12px;
}
.login-btn{
    width:100%;
    margin-bottom:12px;
}
.login-line{
    height:1px;
    background:#31404f;
    margin:18px 0;
}
.switch-text{
    color:#a8b2bd;
    text-align:center;
    font-size:13px;
}
.alert-box,.success-box{
    padding:12px 14px;
    margin-bottom:18px;
    font-size:14px;
    border-left:3px solid;
}
.alert-box{
    background:rgba(90,0,0,.35);
    border-color:#ff5468;
    color:#ffd8dd;
}
.success-box{
    background:rgba(0,90,45,.28);
    border-color:#42e086;
    color:#d6ffe8;
}
.app{
    min-height:100vh;
    display:flex;
    gap:16px;
    position:relative;
    z-index:2;
}
.sidebar{
    width:280px;
    min-height:100vh;
    background:rgba(8,18,28,.94);
    border-right:1px solid #31404f;
    padding:32px 18px;
}
.user-card{
    display:flex;
    gap:12px;
    align-items:center;
    border:1px solid #31404f;
    background:#101a25;
    padding:12px;
    margin-bottom:30px;
}
.avatar{
    width:48px;
    height:48px;
    display:flex;
    align-items:center;
    justify-content:center;
    background:#1d2a36;
    border:1px solid #506070;
    color:white;
    font-size:20px;
    font-weight:900;
}
.user-name{
    font-weight:900;
    color:white;
    margin-bottom:4px;
}
.user-email{
    color:#a8b2bd;
    font-size:12px;
    word-break:break-all;
}
.menu{
    margin-top:20px;
}
.menu a{
    display:block;
    padding:10px 12px;
    color:#a8b2bd;
    border-left:2px solid transparent;
    font-weight:900;
    letter-spacing:.8px;
    text-transform:uppercase;
    margin-bottom:4px;
}
.menu a.active,.menu a:hover{
    color:white;
    background:#1b2430;
    border-left-color:#9fe7ff;
}
.content{
    flex:1;
    padding:50px 42px;
}
.page-title{
    font-size:36px;
    font-weight:900;
    margin-bottom:12px;
}
.page-sub{
    color:#a8b2bd;
    margin-bottom:28px;
    line-height:1.6;
}
.grid{
    display:grid;
    grid-template-columns:repeat(3,minmax(0,1fr));
    gap:16px;
    margin-bottom:30px;
}
.card{
    background:#101a25;
    border:1px solid #31404f;
    padding:20px;
}
.card-label{
    color:#a8b2bd;
    text-transform:uppercase;
    font-size:12px;
    font-weight:900;
    letter-spacing:1px;
    margin-bottom:8px;
}
.card-number{
    font-size:34px;
    font-weight:900;
}
.line{
    border-left:1px solid #31404f;
    padding-left:24px;
    max-width:880px;
}
.row{
    background:#101a25;
    border:1px solid #31404f;
    padding:16px;
    margin-bottom:14px;
}
.row-title{
    color:white;
    font-weight:900;
    margin-bottom:8px;
    font-size:16px;
}
.meta,.small{
    color:#a8b2bd;
    font-size:14px;
    line-height:1.6;
}
.body-text{
    white-space:pre-wrap;
}
.form-box{
    max-width:760px;
    margin-top:30px;
    padding-left:24px;
    border-left:1px solid #31404f;
}
.form-box input,.form-box textarea{
    margin-bottom:12px;
}
.search{
    max-width:360px;
    margin-bottom:24px;
}
.topic-actions{
    display:flex;
    gap:16px;
    margin:26px 0 18px;
}
.selected-file{
    color:#a8b2bd;
    margin-left:10px;
    font-size:14px;
}
.credit-heading{
    font-size:14px;
    letter-spacing:2px;
    font-weight:900;
    margin:26px 0 14px;
}
.credit-divider{
    height:1px;
    background:#31404f;
    max-width:280px;
    margin:24px 0;
}
@media(max-width:850px){
    .app{flex-direction:column}
    .sidebar{width:100%;min-height:auto}
    .content{padding:28px 20px}
    .grid{grid-template-columns:1fr}
}
</style>
</head>

<body>

{% if not user_email %}

<div class="login-page">
    <div class="login-box">
        <div class="logo">FLOWZNMELHOR</div>

        {% with messages = get_flashed_messages(with_categories=true) %}
            {% if messages %}
                {% set category = messages[0][0] %}
                {% set message = messages[0][1] %}
                <div class="{{ 'success-box' if category == 'success' else 'alert-box' }}">{{ message }}</div>
            {% endif %}
        {% endwith %}

        {% if view == 'register' %}
            <div class="login-title">Create account</div>
            <div class="login-sub">Join the private producer room.</div>

            <form action="/register" method="POST">
                <input class="login-input" name="username" placeholder="Username" required>
                <input class="login-input" name="email" type="email" placeholder="Email" required>
                <input class="login-input" name="password" type="password" placeholder="Password" required>
                <button class="login-btn btn-white" type="submit">Create Account</button>
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
            <button class="login-btn btn-white" onclick="location.href='/?view=register'">Create Account</button>

            <div class="switch-text">
                New producer? <a href="/?view=register">Register</a>
            </div>
        {% endif %}
    </div>
</div>

{% else %}

<div class="app">
    <div class="sidebar">
        <div class="logo">FLOWZNMELHOR</div>

        <div class="user-card">
            <div class="avatar">{{ username[0]|upper }}</div>
            <div>
                <div class="user-name">{{ username }}</div>
                <div class="user-email">{{ user_email }}</div>
            </div>
        </div>

        <div class="menu">
            <a class="{% if view == 'dashboard' %}active{% endif %}" href="/?view=dashboard">Home</a>
            <a class="{% if view == 'files' %}active{% endif %}" href="/?view=files">Files</a>
            <a class="{% if view == 'discussion' or view == 'topic' %}active{% endif %}" href="/?view=discussion">Discussion</a>
            <a class="{% if view == 'notifications' %}active{% endif %}" href="/?view=notifications">Notifications <span style="color:#9fe7ff">0</span></a>
            <a class="{% if view == 'account' %}active{% endif %}" href="/?view=account">Account</a>
            <a class="{% if view == 'credits' %}active{% endif %}" href="/?view=credits">Credits</a>
        </div>
    </div>

    <div class="content">
        {% with messages = get_flashed_messages(with_categories=true) %}
            {% if messages %}
                {% set category = messages[0][0] %}
                {% set message = messages[0][1] %}
                <div class="{{ 'success-box' if category == 'success' else 'alert-box' }}">{{ message }}</div>
            {% endif %}
        {% endwith %}

        {% if view == 'dashboard' %}
            <div class="page-title">producer room</div>
            <div class="page-sub">Upload ZIP packs, share MP3 previews, start discussions and build a private funk producer space.</div>

            <div class="grid">
                <div class="card">
                    <div class="card-label">uploaded files</div>
                    <div class="card-number">{{ files|length }}</div>
                </div>

                <div class="card">
                    <div class="card-label">topics</div>
                    <div class="card-number">{{ topics|length }}</div>
                </div>

                <div class="card">
                    <div class="card-label">comments</div>
                    <div class="card-number">{{ total_comments }}</div>
                </div>
            </div>

            <div class="line">
                <div class="row-title">recent files</div>

                {% if files|length == 0 %}
                    <div class="small">No files yet.</div>
                {% endif %}

                {% for file in files[:3] %}
                    <div class="row">
                        <div class="row-title">{{ file.name }}</div>
                        <div class="meta">by {{ file.author }} · {{ file.size }}</div>
                        <a href="/download/{{ file.id }}">download</a>
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
                        <div class="meta">by {{ topic.author }} · {{ topic.comments|length }} comments</div>
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

        {% elif view == 'files' %}
            <div class="page-title">files</div>
            <div class="page-sub">Upload ZIP packs or MP3 previews. Files are stored as Discord attachments.</div>

            <input id="searchInput" class="search" placeholder="search files" oninput="filterRows('searchInput','.file-row')">

            <div class="line">
                {% if files|length == 0 %}
                    <div class="small">No files yet.</div>
                {% endif %}

                {% for file in files %}
                    <div class="row file-row" data-search="{{ file.name|lower }} {{ file.author|lower }}">
                        <div class="row-title">{{ file.name }}</div>
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
                    <button class="btn-white" type="submit">upload file</button>
                </form>
                <p class="small">Allowed: ZIP and MP3 only. Max size: {{ max_file_mb }} MB.</p>
            </div>

        {% elif view == 'discussion' %}
            <div class="page-title">discussion</div>
            <div class="page-sub">Ask for feedback, share FL Studio tricks, post beat ideas or start producer challenges.</div>

            <input id="discussionSearchInput" class="search" placeholder="search discussions" oninput="filterRows('discussionSearchInput','.topic-row')">

            <div class="line">
                {% if topics|length == 0 %}
                    <div class="small">No topics yet.</div>
                {% endif %}

                {% for topic in topics %}
                    <div class="row topic-row" data-search="{{ topic.title|lower }} {{ topic.author|lower }} {{ topic.body|lower }}">
                        <div class="row-title">{{ topic.title }}</div>
                        <div class="meta">by {{ topic.author }} · {{ topic.comments|length }} comments</div>
                        <div class="small">{{ topic.body[:160] }}{% if topic.body|length > 160 %}...{% endif %}</div>
                        <br>
                        <a href="/?view=topic&id={{ topic.id }}">open discussion</a>
                    </div>
                {% endfor %}
            </div>

            <div class="form-box">
                <form action="/topic" method="POST">
                    <input name="title" placeholder="topic title" required>
                    <textarea name="body" placeholder="write topic text" required></textarea>
                    <button class="btn-white" type="submit">add topic</button>
                </form>
            </div>

        {% elif view == 'topic' %}
            {% if selected_topic %}
                <div class="page-title">{{ selected_topic.title }}</div>
                <div class="page-sub">by <b style="color:#9fe7ff">{{ selected_topic.author }}</b></div>

                <div class="topic-actions">
                    <a class="btn" href="/?view=discussion">back</a>

                    {% if selected_topic.can_delete %}
                        <form action="/delete-topic/{{ selected_topic.id }}" method="POST" onsubmit="return confirm('Delete this post?')">
                            <button class="btn-red" type="submit">delete post</button>
                        </form>
                    {% endif %}
                </div>

                <div class="line">
                    <div class="small body-text">{{ selected_topic.body }}</div>
                    <br><br>

                    <div class="row-title">comments</div>

                    {% if selected_topic.comments|length == 0 %}
                        <div class="small">no comments yet.</div>
                    {% endif %}

                    {% for comment in selected_topic.comments %}
                        <div class="row">
                            <div class="meta">{{ comment.author }}</div>
                            <div class="small body-text">{{ comment.body }}</div>
                        </div>
                    {% endfor %}
                </div>

                <div class="form-box">
                    <form action="/comment/{{ selected_topic.id }}" method="POST">
                        <textarea name="body" placeholder="write comment" required></textarea>
                        <button class="btn-white" type="submit">comment</button>
                    </form>
                </div>
            {% else %}
                <div class="page-title">topic not found</div>
                <a class="btn" href="/?view=discussion">back</a>
            {% endif %}

        {% elif view == 'account' %}
            <div class="page-title">account</div>
            <div class="page-sub">Edit your username, password or logout.</div>

            <div class="line">
                <div class="row">
                    <div class="row-title">{{ username }}</div>
                    <div class="small">{{ user_email }}</div>
                </div>

                <div class="form-box">
                    <form action="/change-username" method="POST">
                        <input name="new_username" placeholder="new username" required>
                        <button class="btn-white" type="submit">save username</button>
                    </form>

                    <br>

                    <form action="/change-password" method="POST">
                        <input name="old_password" type="password" placeholder="old password" required>
                        <input name="new_password" type="password" placeholder="new password" required>
                        <button class="btn-white" type="submit">save password</button>
                    </form>

                    <br>

                    <form action="/logout" method="POST">
                        <button type="submit">logout</button>
                    </form>
                </div>
            </div>

        {% elif view == 'credits' %}
            <div class="page-title">credits</div>
            <div class="page-sub">People behind the site.</div>

            <div class="line">
                <div class="credit-heading">OWNERS</div>
                {% for p in credits['OWNERS'] %}
                    <div class="row"><div class="row-title">{{ p }}</div></div>
                {% endfor %}

                <div class="credit-divider"></div>

                <div class="credit-heading">MEMBERS</div>
                {% for p in credits['MEMBERS'] %}
                    <div class="row"><div class="row-title">{{ p }}</div></div>
                {% endfor %}

                <div class="credit-divider"></div>

                <div class="credit-heading">WEBSITE MADE BY</div>
                {% for p in credits['WEBSITE MADE BY'] %}
                    <div class="row"><div class="row-title">{{ p }}</div></div>
                {% endfor %}
            </div>

        {% elif view == 'notifications' %}
            <div class="page-title">notifications</div>
            <div class="page-sub">No notifications yet.</div>
        {% endif %}
    </div>
</div>

{% endif %}

<script>
function filterRows(inputId,rowSelector){
    const input=document.getElementById(inputId);
    if(!input)return;
    const search=input.value.toLowerCase();

    document.querySelectorAll(rowSelector).forEach(row=>{
        const text=row.getAttribute('data-search')||row.innerText.toLowerCase();
        row.style.display=text.includes(search)?'block':'none';
    });
}

const fileInput=document.getElementById('fileInput');
const fileName=document.getElementById('fileName');

if(fileInput&&fileName){
    fileInput.addEventListener('change',()=>{
        fileName.textContent=fileInput.files.length?fileInput.files[0].name:'no file selected';
    });
}
</script>

</body>
</html>
"""


@app.route("/")
def home():
    view = request.args.get("view", "dashboard")
    topic_id = request.args.get("id", "")
    logged_in = bool(current_email())

    if not logged_in:
        view = "register" if view == "register" else "login"

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
            max_file_mb=MAX_FILE_SIZE // (1024 * 1024),
        )

    allowed_views = {
        "dashboard",
        "files",
        "discussion",
        "topic",
        "account",
        "credits",
        "notifications"
    }

    if view not in allowed_views:
        view = "dashboard"

    try:
        user = current_user()
        db = load_db()
    except Exception as e:
        flash(f"Discord database error: {e}", "error")
        user = {
            "email": current_email(),
            "username": current_email()
        }
        db = {
            "files": {},
            "topics": {},
            "comments": {}
        }

    if not user:
        session.clear()
        flash("Account not found. Please login again.", "error")
        return redirect(url_for("home", view="login"))

    files = [public_file(f) for f in db["files"].values()]
    files.sort(key=lambda x: x["created"], reverse=True)

    topics = [public_topic(t) for t in db["topics"].values()]
    topics.sort(key=lambda x: x["created"], reverse=True)

    selected_topic = None

    if view == "topic" and topic_id:
        topic_data = db["topics"].get(topic_id)

        if topic_data:
            selected_topic = public_topic(topic_data)

    return render_template_string(
        HTML,
        view=view,
        user_email=user.get("email"),
        username=user.get("username"),
        files=files,
        topics=topics,
        selected_topic=selected_topic,
        total_comments=len(db["comments"]),
        credits=CREDITS,
        max_file_mb=MAX_FILE_SIZE // (1024 * 1024),
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

    uid = user_id_from_email(email)

    if uid in db["users"]:
        flash("Account already exists. Please login instead.", "error")
        return redirect(url_for("home", view="login"))

    for existing_user in db["users"].values():
        if existing_user.get("username", "").strip().lower() == username.lower():
            flash("Username already exists. Choose another one.", "error")
            return redirect(url_for("home", view="register"))

    user = {
        "id": uid,
        "username": username,
        "email": email,
        "password_hash": generate_password_hash(password),
        "created": int(time.time()),
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
    user = current_user()
    db = load_db()

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
        "author_id": user.get("id"),
        "created": int(time.time()),
    }

    try:
        save_file(metadata, original_name, file_bytes, uploaded.content_type)
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
        r = requests.get(attachment_url, timeout=60)
        r.raise_for_status()
    except Exception as e:
        flash(f"Could not download Discord attachment: {e}", "error")
        return go("files")

    return send_file(
        BytesIO(r.content),
        as_attachment=True,
        download_name=file_data.get("original_name", "download"),
        mimetype="application/octet-stream",
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
        "author_id": user.get("id"),
        "created": int(time.time()),
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

    comment = {
        "id": secrets.token_hex(12),
        "topic_id": topic_id,
        "body": body[:900],
        "author": user.get("username"),
        "author_id": user.get("id"),
        "created": int(time.time()),
    }

    try:
        save_comment(comment)
    except Exception as e:
        flash(f"Could not save comment to Discord: {e}", "error")
        return go("topic", topic_id)

    flash("Comment added.", "success")
    return go("topic", topic_id)


@app.route("/delete-topic/<topic_id>", methods=["POST"])
@login_required
def delete_topic_route(topic_id):
    user = current_user()
    db = load_db()
    topic = db["topics"].get(topic_id)

    if not topic:
        flash("Topic not found.", "error")
        return go("discussion")

    if topic.get("author_id") != user.get("id"):
        flash("You can only delete your own posts.", "error")
        return go("topic", topic_id)

    try:
        delete_topic(topic_id, user.get("id"))
    except Exception as e:
        flash(f"Could not delete topic: {e}", "error")
        return go("topic", topic_id)

    flash("Post deleted.", "success")
    return go("discussion")


@app.route("/discord-test")
def discord_test():
    try:
        messages = fetch_discord_messages()
        test_content = f"SWTEST|website connected|{int(time.time())}"
        post_discord_message(test_content)
        return f"DISCORD DATABASE WORKS. Messages found before test: {len(messages)}. Test message sent."
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

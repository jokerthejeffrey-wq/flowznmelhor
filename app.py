import os
import json
import time
import secrets
from functools import wraps

from flask import Flask, render_template_string, request, redirect, url_for, send_from_directory, session, flash
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)

UPLOAD_FOLDER = os.environ.get("UPLOAD_FOLDER", "uploads")
DATA_FOLDER = os.environ.get("DATA_FOLDER", "data")

USERS_FILE = os.path.join(DATA_FOLDER, "users.json")
DISCUSSION_FILE = os.path.join(DATA_FOLDER, "discussions.json")
FILES_META_FILE = os.path.join(DATA_FOLDER, "files_meta.json")
SECRET_FILE = os.path.join(DATA_FOLDER, "secret_key.txt")

MAX_FILE_SIZE = 1024 * 1024 * 1024
ALLOWED_EXTENSIONS = {".zip", ".mp3"}

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
        "RSFI"
    ],
    "WEBSITE MADE BY": [
        "DJ SABA 7"
    ]
}


def setup_project_files():
    os.makedirs(UPLOAD_FOLDER, exist_ok=True)
    os.makedirs(DATA_FOLDER, exist_ok=True)
    os.makedirs("static", exist_ok=True)

    default_files = {
        USERS_FILE: {},
        DISCUSSION_FILE: [],
        FILES_META_FILE: {}
    }

    for path, default_data in default_files.items():
        if not os.path.exists(path):
            with open(path, "w", encoding="utf-8") as f:
                json.dump(default_data, f, indent=4, ensure_ascii=False)

    if not os.path.exists(SECRET_FILE):
        with open(SECRET_FILE, "w", encoding="utf-8") as f:
            f.write(secrets.token_hex(64))


setup_project_files()


def get_secret_key():
    env_key = os.environ.get("SECRET_KEY")
    if env_key:
        return env_key

    with open(SECRET_FILE, "r", encoding="utf-8") as f:
        return f.read().strip()


app.secret_key = get_secret_key()
app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER
app.config["MAX_CONTENT_LENGTH"] = MAX_FILE_SIZE


def load_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4, ensure_ascii=False)


def load_users():
    return load_json(USERS_FILE, {})


def save_users(users):
    save_json(USERS_FILE, users)


def load_discussions():
    return load_json(DISCUSSION_FILE, [])


def save_discussions(data):
    save_json(DISCUSSION_FILE, data)


def load_files_meta():
    return load_json(FILES_META_FILE, {})


def save_files_meta(data):
    save_json(FILES_META_FILE, data)


def current_user():
    return session.get("email")


def current_username():
    email = current_user()

    if not email:
        return ""

    users = load_users()
    user = users.get(email, {})

    return user.get("username", email)


def valid_username(username):
    username = username.strip()

    if len(username) < 3 or len(username) > 20:
        return False

    allowed = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_."

    for char in username:
        if char not in allowed:
            return False

    return True


def username_exists(username, ignore_email=None):
    username = username.strip().lower()
    users = load_users()

    for email, user in users.items():
        if ignore_email and email == ignore_email:
            continue

        if user.get("username", "").strip().lower() == username:
            return True

    return False


def update_author_name(old_email, new_username):
    discussions = load_discussions()

    for topic in discussions:
        if topic.get("author_email") == old_email:
            topic["author"] = new_username

        for comment in topic.get("comments", []):
            if comment.get("author_email") == old_email:
                comment["author"] = new_username

    save_discussions(discussions)

    meta = load_files_meta()

    for filename, data in meta.items():
        if data.get("owner_email") == old_email:
            data["owner"] = new_username

    save_files_meta(meta)


def allowed_file(filename):
    ext = os.path.splitext(filename.lower())[1]
    return ext in ALLOWED_EXTENSIONS


def get_files():
    data = []

    for file in os.listdir(UPLOAD_FOLDER):
        full = os.path.join(UPLOAD_FOLDER, file)

        if os.path.isfile(full) and allowed_file(file):
            data.append(file)

    return sorted(data, reverse=True)


def file_size_text(filename):
    path = os.path.join(UPLOAD_FOLDER, filename)

    if not os.path.exists(path):
        return "0 KB"

    size = os.path.getsize(path)

    if size >= 1024 * 1024:
        return f"{size / (1024 * 1024):.1f} MB"

    return f"{size / 1024:.1f} KB"


def get_file_info(files):
    meta = load_files_meta()
    result = {}

    for file in files:
        data = meta.get(file, {})
        result[file] = {
            "size": file_size_text(file),
            "owner": data.get("owner", "unknown"),
            "owner_email": data.get("owner_email", ""),
            "uploaded": data.get("uploaded", 0)
        }

    return result


def login_required(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        if not current_user():
            flash("Please login first.", "error")
            return redirect(url_for("home", view="login"))

        return func(*args, **kwargs)

    return wrapper


@app.before_request
def block_guest_actions():
    allowed_endpoints = {
        "home",
        "login",
        "register",
        "static"
    }

    if request.endpoint is None:
        return

    if request.endpoint in allowed_endpoints:
        return

    if not current_user():
        flash("Please login first.", "error")
        return redirect(url_for("home", view="login"))


def go(view="dashboard", topic_id=None):
    if topic_id:
        return redirect(url_for("home", view=view, id=topic_id))

    return redirect(url_for("home", view=view))


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
    --text:#f8fbff;
    --muted:rgba(255,255,255,.68);
    --blue:#9fe7ff;
    --dark:#06101d;
    --panel:rgba(3,10,18,.78);
    --line:rgba(255,255,255,.24);
    --thin:rgba(255,255,255,.12);
    --danger:#ff5c5c;
}

body{
    margin:0;
    min-height:100vh;
    color:var(--text);
    font-family:Arial,Helvetica,sans-serif;
    background:
        linear-gradient(rgba(2,7,13,.78), rgba(2,7,13,.90)),
        radial-gradient(circle at 18% 16%, rgba(100,210,255,.26), transparent 28%),
        radial-gradient(circle at 86% 82%, rgba(55,120,200,.22), transparent 34%),
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
        linear-gradient(rgba(255,255,255,.035) 1px, transparent 1px),
        linear-gradient(90deg, rgba(255,255,255,.035) 1px, transparent 1px);
    background-size:28px 28px;
    opacity:.72;
    pointer-events:none;
    z-index:0;
}

body::after{
    content:"";
    position:fixed;
    inset:16px;
    border:1px solid rgba(255,255,255,.08);
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
    position:relative;
    z-index:1;
    height:100vh;
    display:flex;
    justify-content:center;
    align-items:center;
    padding:20px;
}

.login-shell{
    width:370px;
    background:rgba(4,14,25,.78);
    border:1px solid var(--line);
    backdrop-filter:blur(18px);
    -webkit-backdrop-filter:blur(18px);
    box-shadow:0 28px 90px rgba(0,0,0,.52);
    border-radius:0;
    padding:32px;
    position:relative;
}

.login-shell::before{
    content:"";
    position:absolute;
    left:0;
    top:0;
    right:0;
    height:1px;
    background:linear-gradient(90deg, transparent, rgba(159,231,255,.8), transparent);
}

.login-inner{
    position:relative;
    z-index:2;
}

.login-logo{
    font-size:11px;
    letter-spacing:4px;
    font-weight:900;
    color:white;
    margin-bottom:30px;
    text-transform:uppercase;
}

.login-logo::after{
    content:"";
    display:block;
    width:44px;
    height:1px;
    background:var(--blue);
    margin-top:12px;
}

.login-title{
    font-size:28px;
    font-weight:900;
    color:white;
    margin-bottom:8px;
    letter-spacing:-1px;
}

.login-sub{
    color:var(--muted);
    font-size:13px;
    line-height:1.7;
    margin-bottom:26px;
}

.login-input-wrap{
    position:relative;
    margin-bottom:16px;
}

.login-input-wrap input{
    width:100%;
    background:rgba(255,255,255,.035);
    border:none;
    border-bottom:1px solid rgba(255,255,255,.62);
    color:white;
    outline:none;
    padding:13px 32px 11px 10px;
    font-size:14px;
    font-weight:700;
    border-radius:0;
}

.login-input-wrap input::placeholder{
    color:rgba(255,255,255,.62);
    font-weight:500;
}

.login-input-wrap input:focus{
    background:rgba(255,255,255,.07);
    border-bottom-color:var(--blue);
}

.login-icon{
    position:absolute;
    right:8px;
    top:12px;
    color:white;
    font-size:12px;
    opacity:.84;
}

.login-row{
    display:flex;
    justify-content:space-between;
    align-items:center;
    color:rgba(255,255,255,.70);
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
    border-radius:0;
    border:1px solid rgba(255,255,255,.28);
    cursor:pointer;
    transition:.16s ease;
    font-weight:900;
    font-size:13px;
    text-decoration:none;
    letter-spacing:.4px;
}

.btn-dark{
    background:#06101d;
    color:white;
}

.btn-dark:hover{
    background:#0d2235;
    border-color:var(--blue);
}

.btn-white{
    background:white;
    color:#06101d;
    border-color:white;
}

.btn-white:hover{
    background:#e9f8ff;
}

.login-line{
    height:1px;
    background:rgba(255,255,255,.22);
    margin:18px 0 14px;
}

.switch-text{
    color:rgba(255,255,255,.72);
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
    font-size:13px;
    border-radius:0;
}

.alert-box{
    background:rgba(80,0,0,.48);
    color:#ffdede;
    border:1px solid rgba(255,90,90,.44);
    border-left:2px solid #ff5757;
}

.success-box{
    background:rgba(0,70,30,.44);
    color:#d6ffe1;
    border:1px solid rgba(67,232,139,.42);
    border-left:2px solid #43e88b;
}

.side{
    width:260px;
    padding:26px 20px;
    border:1px solid var(--line);
    background:var(--panel);
    backdrop-filter:blur(18px);
    -webkit-backdrop-filter:blur(18px);
    box-shadow:0 22px 70px rgba(0,0,0,.44);
    display:flex;
    flex-direction:column;
    border-radius:0;
}

.title{
    font-size:11px;
    letter-spacing:4px;
    color:white;
    margin-bottom:28px;
    font-weight:900;
    text-transform:uppercase;
}

.title::after{
    content:"";
    display:block;
    width:42px;
    height:1px;
    background:var(--blue);
    margin-top:12px;
}

.user-mini{
    background:rgba(255,255,255,.045);
    border:1px solid rgba(255,255,255,.12);
    border-radius:0;
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
    border-top:1px solid var(--thin);
    padding-top:18px;
}

.item{
    cursor:pointer;
    user-select:none;
    transition:.16s ease;
    line-height:2.35;
    color:rgba(255,255,255,.68);
    padding:2px 10px;
    margin-bottom:4px;
    border-radius:0;
    font-size:13px;
    letter-spacing:.7px;
    border-left:1px solid transparent;
}

.item:hover,
.item.active{
    color:white;
    background:rgba(255,255,255,.075);
    border-left-color:var(--blue);
    transform:translateX(3px);
}

.clicked{
    animation:clickFade .28s ease;
}

@keyframes clickFade{
    0%{opacity:1}
    45%{opacity:.45;transform:translateX(7px)}
    100%{opacity:1;transform:translateX(3px)}
}

.content{
    flex:1;
    padding:42px;
    overflow-y:auto;
    transition:opacity .22s ease,transform .22s ease;
    border:1px solid var(--line);
    background:var(--panel);
    backdrop-filter:blur(20px);
    -webkit-backdrop-filter:blur(20px);
    box-shadow:0 22px 75px rgba(0,0,0,.44);
    border-radius:0;
}

.content.fade{
    opacity:0;
    transform:translateY(6px);
}

.content::-webkit-scrollbar{
    width:8px;
}

.content::-webkit-scrollbar-track{
    background:rgba(255,255,255,.04);
}

.content::-webkit-scrollbar-thumb{
    background:rgba(255,255,255,.24);
    border-radius:0;
}

.page-title{
    font-size:32px;
    font-weight:900;
    margin-bottom:10px;
    color:white;
    letter-spacing:-1px;
}

.page-sub{
    color:var(--muted);
    font-size:13px;
    line-height:1.7;
    margin-bottom:28px;
    max-width:760px;
}

.grid{
    display:grid;
    grid-template-columns:repeat(3,minmax(0,1fr));
    gap:14px;
    margin-bottom:28px;
}

.card{
    background:rgba(255,255,255,.045);
    border:1px solid rgba(255,255,255,.14);
    border-radius:0;
    padding:18px;
    transition:.16s ease;
    position:relative;
}

.card::before{
    content:"";
    position:absolute;
    left:0;
    top:0;
    width:34px;
    height:1px;
    background:var(--blue);
    opacity:.7;
}

.card:hover{
    background:rgba(255,255,255,.07);
    border-color:rgba(159,231,255,.46);
}

.card-label{
    color:var(--muted);
    font-size:11px;
    letter-spacing:1.8px;
    text-transform:uppercase;
    margin-bottom:10px;
}

.card-number{
    color:white;
    font-size:29px;
    font-weight:900;
}

.card-text{
    color:var(--muted);
    font-size:13px;
    line-height:1.6;
    margin-top:8px;
}

.line{
    border-left:1px solid rgba(255,255,255,.26);
    padding-left:24px;
    max-width:980px;
}

input,
textarea{
    background:rgba(255,255,255,.045);
    color:white;
    border:1px solid rgba(255,255,255,.22);
    border-radius:0;
    padding:13px 14px;
    outline:none;
    font-size:14px;
    backdrop-filter:blur(10px);
}

input::placeholder,
textarea::placeholder{
    color:rgba(255,255,255,.52);
}

input:focus,
textarea:focus{
    border-color:var(--blue);
    background:rgba(255,255,255,.07);
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

.file-row,
.topic-row,
.credit-row,
.comment-row{
    margin-bottom:14px;
    padding:15px 17px;
    background:rgba(255,255,255,.045);
    border:1px solid rgba(255,255,255,.13);
    border-radius:0;
    transition:.16s ease;
    position:relative;
}

.file-row::before,
.topic-row::before,
.credit-row::before,
.comment-row::before{
    content:"";
    position:absolute;
    left:0;
    top:0;
    bottom:0;
    width:1px;
    background:rgba(159,231,255,.38);
    opacity:0;
    transition:.16s ease;
}

.file-row:hover,
.topic-row:hover,
.credit-row:hover,
.comment-row:hover{
    background:rgba(255,255,255,.07);
    border-color:rgba(159,231,255,.38);
}

.file-row:hover::before,
.topic-row:hover::before,
.credit-row:hover::before,
.comment-row:hover::before{
    opacity:1;
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
    font-size:13px;
    line-height:1.7;
}

.file-link,
.topic-open{
    color:var(--blue);
    text-decoration:none;
    font-size:13px;
    word-break:break-all;
    transition:.16s ease;
    cursor:pointer;
    font-weight:900;
}

.file-link:hover,
.topic-open:hover{
    color:white;
    padding-left:4px;
}

.form-box{
    margin-top:30px;
    border-left:1px solid rgba(255,255,255,.24);
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
    border:1px solid rgba(255,255,255,.22);
    padding:12px 18px;
    border-radius:0;
    cursor:pointer;
    transition:.16s ease;
    font-size:13px;
    text-decoration:none;
    font-weight:900;
    letter-spacing:.4px;
    backdrop-filter:blur(10px);
}

button:hover,
.file-button:hover{
    background:#102b43;
    border-color:var(--blue);
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

.delete-btn{
    background:rgba(70,0,0,.35);
    color:#ffdede;
    border-color:rgba(255,92,92,.45);
    padding:9px 13px;
    margin-left:8px;
}

.delete-btn:hover{
    background:rgba(110,0,0,.55);
    border-color:var(--danger);
}

.action-row{
    margin-top:10px;
    display:flex;
    align-items:center;
    flex-wrap:wrap;
    gap:8px;
}

.login-card,
.account-box{
    width:430px;
    background:rgba(255,255,255,.045);
    border:1px solid rgba(255,255,255,.18);
    border-radius:0;
    padding:28px;
    box-shadow:0 20px 55px rgba(0,0,0,.28);
}

.login-card-title{
    font-size:24px;
    color:white;
    margin-bottom:8px;
    font-weight:900;
    letter-spacing:-.5px;
}

.login-card-sub{
    color:var(--muted);
    font-size:13px;
    margin-bottom:24px;
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
    font-size:13px;
    margin-left:10px;
}

.comment-row{
    border-left:1px solid rgba(159,231,255,.32);
}

.account-section{
    margin-top:28px;
    padding-top:22px;
    border-top:1px solid rgba(255,255,255,.16);
}

.credit-heading{
    color:white;
    font-size:12px;
    letter-spacing:2.4px;
    margin:26px 0 16px;
    font-weight:900;
}

.credit-heading:first-child{
    margin-top:0;
}

.credit-divider{
    border-top:1px solid rgba(255,255,255,.22);
    width:280px;
    margin:24px 0;
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

    .login-card,
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
                <div class="login-sub">Create an account to access the site.</div>

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
                <div class="login-sub">Login to continue.</div>

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
                        <span>private access</span>
                    </div>

                    <button class="btn btn-dark" type="submit">Login</button>
                </form>

                <div class="login-line"></div>

                <button class="btn btn-white" onclick="location.href='/?view=register'">Create Account</button>

                <div class="switch-text">
                    New here? <a href="/?view=register">Register</a>
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
            <div class="user-mini-name">{{ username }}</div>
            <div class="user-mini-mail">{{ user_email }}</div>
        </div>

        <div class="menu-main">
            <div class="item" id="menuDashboard" onclick="showDashboard(this)">home</div>
            <div class="item" id="menuFiles" onclick="showFiles(this)">files</div>
            <div class="item" id="menuDiscussion" onclick="showDiscussion(this)">discussion</div>
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
        <div class="small">opening site.</div>
    </div>
</div>

{% endif %}

<script>
const files = {{ files|tojson }};
const fileInfo = {{ file_info|tojson }};
const credits = {{ credits|tojson }};
const discussions = {{ discussions|tojson }};
const userEmail = {{ user_email|tojson }};
const username = {{ username|tojson }};
const startView = {{ start_view|tojson }};
const startTopicId = {{ start_topic_id|tojson }};

function clickEffect(el){
    if(!el) return;
    el.classList.remove("clicked");
    void el.offsetWidth;
    el.classList.add("clicked");
}

function fadeChange(html){
    const content=document.getElementById("content");
    if(!content) return;

    content.classList.add("fade");

    setTimeout(()=>{
        content.innerHTML=html;
        content.classList.remove("fade");
        bindFileInput();
    },140);
}

function bindFileInput(){
    const input=document.getElementById("fileInput");
    const name=document.getElementById("fileName");

    if(!input || !name) return;

    input.addEventListener("change",()=>{
        name.textContent=input.files.length ? input.files[0].name : "no file selected";
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

function showDashboard(button){
    clickEffect(button);
    clearActive();
    if(button) button.classList.add("active");

    setUrl("dashboard");

    const totalComments = discussions.reduce((sum, t)=>sum + t.comments.length, 0);
    const recentTopics = discussions.slice(0,3);
    const recentFiles = files.slice(0,3);

    let html=`
        <div class="page-title">home</div>
        <div class="page-sub">Files, discussions, account and credits.</div>

        <div class="grid">
            <div class="card">
                <div class="card-label">files</div>
                <div class="card-number">${files.length}</div>
                <div class="card-text">Uploaded ZIP and MP3 files.</div>
            </div>

            <div class="card">
                <div class="card-label">discussions</div>
                <div class="card-number">${discussions.length}</div>
                <div class="card-text">Created discussion posts.</div>
            </div>

            <div class="card">
                <div class="card-label">comments</div>
                <div class="card-number">${totalComments}</div>
                <div class="card-text">Replies inside discussions.</div>
            </div>
        </div>

        <div class="line">
            <div class="topic-title">recent activity</div>
            <br>
    `;

    if(recentTopics.length === 0 && recentFiles.length === 0){
        html += `<div class="small">No activity yet.</div>`;
    }

    recentFiles.forEach(file=>{
        const info = fileInfo[file] || {};
        html += `
            <div class="file-row">
                <div class="file-title">${escapeHtml(file)}</div>
                <div class="meta">file · ${escapeHtml(info.size || "")} · by ${escapeHtml(info.owner || "unknown")}</div>
                <div class="action-row">
                    <a class="file-link" href="/download/${encodeURIComponent(file)}" target="_blank">download</a>
                    ${info.owner_email === userEmail ? deleteFileForm(file) : ""}
                </div>
            </div>
        `;
    });

    recentTopics.forEach(topic=>{
        html += `
            <div class="topic-row">
                <div class="topic-title">${escapeHtml(topic.title)}</div>
                <div class="topic-meta">by ${escapeHtml(topic.author)} · ${topic.comments.length} comments</div>
                <div class="action-row">
                    <div class="topic-open" onclick="openTopic('${topic.id}')">open discussion</div>
                    ${topic.author_email === userEmail ? deleteTopicForm(topic.id) : ""}
                </div>
            </div>
        `;
    });

    html += `
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
        <div class="page-sub">Upload ZIP or MP3 files.</div>

        <input id="searchInput" class="search-bar" placeholder="search files" oninput="filterFiles()">

        <div class="line">
    `;

    if(files.length===0){
        html+=`<div class="small">no files yet.</div>`;
    }

    files.forEach(file=>{
        const info = fileInfo[file] || {};

        html+=`
            <div class="file-row" data-name="${escapeAttr(file.toLowerCase())}">
                <div class="file-title">${escapeHtml(file)}</div>
                <div class="meta">${escapeHtml(info.size || "")} · by ${escapeHtml(info.owner || "unknown")}</div>
                <div class="action-row">
                    <a class="file-link" href="/download/${encodeURIComponent(file)}" target="_blank">download</a>
                    ${info.owner_email === userEmail ? deleteFileForm(file) : ""}
                </div>
            </div>
        `;
    });

    html+=`</div>`;

    html+=`
        <div class="form-box">
            <form action="/upload" method="POST" enctype="multipart/form-data">
                <input id="fileInput" type="file" name="uploadfile" accept=".zip,.mp3" required hidden>
                <label for="fileInput" class="file-button">select file</label>
                <span id="fileName" class="selected-file">no file selected</span>
                <br><br>
                <button class="primary-btn" type="submit">upload</button>
            </form>
            <p class="small">allowed: zip and mp3 only.</p>
        </div>
    `;

    fadeChange(html);
}

function filterFiles(){
    const search=document.getElementById("searchInput").value.toLowerCase();

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
        <div class="page-sub">Create or reply to discussion posts.</div>

        <input id="discussionSearchInput" class="search-bar" placeholder="search discussions" oninput="filterDiscussions()">

        <div class="line">
    `;

    if(discussions.length===0){
        html+=`<div class="small">no posts yet.</div>`;
    }

    discussions.forEach(topic=>{
        const searchable=(topic.title + " " + topic.author + " " + topic.body).toLowerCase();

        html+=`
            <div class="topic-row" data-name="${escapeAttr(searchable)}">
                <div class="topic-title">${escapeHtml(topic.title)}</div>
                <div class="topic-meta">by ${escapeHtml(topic.author)} · ${topic.comments.length} comments</div>
                <div class="small">${escapeHtml(topic.body).slice(0,140)}${topic.body.length > 140 ? "..." : ""}</div>
                <div class="action-row">
                    <div class="topic-open" onclick="openTopic('${topic.id}')">open discussion</div>
                    ${topic.author_email === userEmail ? deleteTopicForm(topic.id) : ""}
                </div>
            </div>
        `;
    });

    html+=`</div>`;

    html+=`
        <div class="form-box">
            <form action="/topic" method="POST">
                <input name="title" placeholder="post title" required style="width:100%;">
                <textarea name="body" placeholder="write post" required></textarea>
                <br><br>
                <button class="primary-btn" type="submit">post</button>
            </form>
        </div>
    `;

    fadeChange(html);
}

function filterDiscussions(){
    const search=document.getElementById("discussionSearchInput").value.toLowerCase();

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

        <div class="action-row">
            <button onclick="showDiscussion(document.getElementById('menuDiscussion'))">back</button>
            ${topic.author_email === userEmail ? deleteTopicForm(topic.id) : ""}
        </div>

        <br>

        <div class="line">
            <div class="small">${escapeHtml(topic.body).replaceAll("\\n","<br>")}</div>
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
                <div class="small">${escapeHtml(comment.body).replaceAll("\\n","<br>")}</div>
            </div>
        `;
    });

    html+=`</div>`;

    html+=`
        <div class="form-box">
            <form action="/comment/${topic.id}" method="POST">
                <textarea name="body" placeholder="write comment" required></textarea>
                <br><br>
                <button class="primary-btn" type="submit">comment</button>
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
        <div class="page-sub">Edit your account.</div>

        <div class="line">
            <div class="account-box">
                <div class="login-card-title">${escapeHtml(username)}</div>
                <div class="login-card-sub">${escapeHtml(userEmail)}</div>

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

function showCredits(button){
    clickEffect(button);
    clearActive();
    if(button) button.classList.add("active");

    setUrl("credits");

    let html=`
        <div class="page-title">credits</div>
        <div class="page-sub">Site credits.</div>

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

function deleteTopicForm(topicId){
    return `
        <form action="/delete-topic/${encodeURIComponent(topicId)}" method="POST" onsubmit="return confirm('Delete this post?');">
            <button class="delete-btn" type="submit">delete post</button>
        </form>
    `;
}

function deleteFileForm(filename){
    return `
        <form action="/delete-file/${encodeURIComponent(filename)}" method="POST" onsubmit="return confirm('Delete this file?');">
            <button class="delete-btn" type="submit">delete file</button>
        </form>
    `;
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
    logged_in = bool(current_user())
    requested_view = request.args.get("view", "")
    requested_topic = request.args.get("id", "")

    if not logged_in:
        auth_mode = "register" if requested_view == "register" else "login"
        requested_view = "login"
        requested_topic = ""
    else:
        auth_mode = ""
        if requested_view in ["login", "register", ""]:
            requested_view = "dashboard"

    files = get_files() if logged_in else []

    return render_template_string(
        HTML,
        files=files,
        file_info=get_file_info(files),
        credits=CREDITS,
        discussions=load_discussions() if logged_in else [],
        user_email=current_user(),
        username=current_username(),
        start_view=requested_view,
        start_topic_id=requested_topic,
        auth_mode=auth_mode
    )


@app.route("/register", methods=["POST"])
def register():
    username = request.form.get("username", "").strip()
    email = request.form.get("email", "").strip().lower()
    password = request.form.get("password", "")

    if not valid_username(username):
        flash("Username must be 3-20 characters and only use letters, numbers, underscore, or dot.", "error")
        return redirect(url_for("home", view="register"))

    if username_exists(username):
        flash("Username already exists. Choose another one.", "error")
        return redirect(url_for("home", view="register"))

    if "@" not in email or "." not in email:
        flash("Invalid email address.", "error")
        return redirect(url_for("home", view="register"))

    if len(password) < 6:
        flash("Password must be at least 6 characters.", "error")
        return redirect(url_for("home", view="register"))

    users = load_users()

    if email in users:
        flash("Account already exists. Please login instead.", "error")
        return redirect(url_for("home", view="login"))

    users[email] = {
        "username": username,
        "password_hash": generate_password_hash(password),
        "created": int(time.time())
    }

    save_users(users)

    session["email"] = email
    flash("Account created successfully.", "success")
    return go("dashboard")


@app.route("/login", methods=["POST"])
def login():
    email = request.form.get("email", "").strip().lower()
    password = request.form.get("password", "")

    users = load_users()
    user = users.get(email)

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
    email = current_user()
    new_username = request.form.get("new_username", "").strip()

    if not valid_username(new_username):
        flash("Username must be 3-20 characters and only use letters, numbers, underscore, or dot.", "error")
        return go("account")

    if username_exists(new_username, ignore_email=email):
        flash("Username already exists. Choose another one.", "error")
        return go("account")

    users = load_users()
    user = users.get(email)

    if not user:
        session.clear()
        flash("Account not found.", "error")
        return redirect(url_for("home", view="login"))

    user["username"] = new_username
    users[email] = user
    save_users(users)

    update_author_name(email, new_username)

    flash("Username changed successfully.", "success")
    return go("account")


@app.route("/change-password", methods=["POST"])
@login_required
def change_password():
    email = current_user()
    old_password = request.form.get("old_password", "")
    new_password = request.form.get("new_password", "")

    users = load_users()
    user = users.get(email)

    if not user:
        session.clear()
        flash("Account not found.", "error")
        return redirect(url_for("home", view="login"))

    if not check_password_hash(user.get("password_hash", ""), old_password):
        flash("Old password is wrong.", "error")
        return go("account")

    if len(new_password) < 6:
        flash("New password must be at least 6 characters.", "error")
        return go("account")

    user["password_hash"] = generate_password_hash(new_password)
    users[email] = user
    save_users(users)

    flash("Password changed successfully.", "success")
    return go("account")


@app.route("/upload", methods=["POST"])
@login_required
def upload():
    if "uploadfile" not in request.files:
        flash("No file selected.", "error")
        return go("files")

    file = request.files["uploadfile"]

    if file.filename == "":
        flash("No file selected.", "error")
        return go("files")

    if not allowed_file(file.filename):
        flash("Only ZIP and MP3 files are allowed.", "error")
        return go("files")

    filename = secure_filename(file.filename)
    save_path = os.path.join(UPLOAD_FOLDER, filename)

    if os.path.exists(save_path):
        name, ext = os.path.splitext(filename)
        filename = f"{name}_{int(time.time())}{ext}"
        save_path = os.path.join(UPLOAD_FOLDER, filename)

    file.save(save_path)

    meta = load_files_meta()
    meta[filename] = {
        "owner": current_username(),
        "owner_email": current_user(),
        "uploaded": int(time.time())
    }
    save_files_meta(meta)

    flash("File uploaded successfully.", "success")
    return go("files")


@app.route("/download/<path:filename>")
@login_required
def download(filename):
    filename = secure_filename(filename)
    file_path = os.path.join(UPLOAD_FOLDER, filename)

    if not os.path.exists(file_path):
        flash("File not found on server.", "error")
        return go("files")

    return send_from_directory(
        os.path.abspath(UPLOAD_FOLDER),
        filename,
        as_attachment=True
    )


@app.route("/delete-file/<path:filename>", methods=["POST"])
@login_required
def delete_file(filename):
    filename = secure_filename(filename)

    meta = load_files_meta()
    file_data = meta.get(filename, {})

    if file_data.get("owner_email") != current_user():
        flash("You can only delete files you uploaded.", "error")
        return go("files")

    file_path = os.path.join(UPLOAD_FOLDER, filename)

    if os.path.exists(file_path):
        os.remove(file_path)

    if filename in meta:
        del meta[filename]
        save_files_meta(meta)

    flash("File deleted.", "success")
    return go("files")


@app.route("/topic", methods=["POST"])
@login_required
def add_topic():
    title = request.form.get("title", "").strip()
    body = request.form.get("body", "").strip()

    if not title or not body:
        flash("Post title and text are required.", "error")
        return go("discussion")

    topic_id = str(int(time.time() * 1000))

    discussions = load_discussions()

    discussions.insert(0, {
        "id": topic_id,
        "title": title[:120],
        "body": body[:3000],
        "author": current_username(),
        "author_email": current_user(),
        "created": int(time.time()),
        "comments": []
    })

    save_discussions(discussions)

    flash("Post added.", "success")
    return go("topic", topic_id)


@app.route("/delete-topic/<topic_id>", methods=["POST"])
@login_required
def delete_topic(topic_id):
    discussions = load_discussions()

    for topic in discussions:
        if topic.get("id") == topic_id:
            if topic.get("author_email") != current_user():
                flash("You can only delete posts you created.", "error")
                return go("discussion")

            discussions = [t for t in discussions if t.get("id") != topic_id]
            save_discussions(discussions)
            flash("Post deleted.", "success")
            return go("discussion")

    flash("Post not found.", "error")
    return go("discussion")


@app.route("/comment/<topic_id>", methods=["POST"])
@login_required
def add_comment(topic_id):
    body = request.form.get("body", "").strip()

    if not body:
        flash("Comment cannot be empty.", "error")
        return go("topic", topic_id)

    discussions = load_discussions()
    found = False

    for topic in discussions:
        if topic["id"] == topic_id:
            topic["comments"].append({
                "author": current_username(),
                "author_email": current_user(),
                "body": body[:2000],
                "time": int(time.time())
            })
            found = True
            break

    save_discussions(discussions)

    if found:
        flash("Comment added.", "success")
        return go("topic", topic_id)

    flash("Post not found.", "error")
    return go("discussion")


@app.errorhandler(413)
def too_large(error):
    flash("File too large. Maximum file size is 1 GB.", "error")
    return go("files")


if __name__ == "__main__":
    app.run(debug=True)

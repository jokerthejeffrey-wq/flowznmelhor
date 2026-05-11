import os
import json
import gzip
import time
import secrets
from functools import wraps

from flask import Flask, render_template_string, request, redirect, url_for, send_from_directory, session, flash
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)

UPLOAD_FOLDER = "uploads"
DATA_FOLDER = "data"

USERS_FILE = os.path.join(DATA_FOLDER, "users.json.gz")
DISCUSSION_FILE = os.path.join(DATA_FOLDER, "discussions.json.gz")
SECRET_FILE = os.path.join(DATA_FOLDER, "secret_key.txt")

MAX_FILE_SIZE = 1024 * 1024 * 1024
ALLOWED_EXTENSIONS = {".zip", ".mp3"}

os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(DATA_FOLDER, exist_ok=True)
os.makedirs("static", exist_ok=True)


def get_or_create_secret_key():
    if os.path.exists(SECRET_FILE):
        with open(SECRET_FILE, "r", encoding="utf-8") as f:
            key = f.read().strip()
            if key:
                return key

    key = secrets.token_hex(64)

    with open(SECRET_FILE, "w", encoding="utf-8") as f:
        f.write(key)

    return key


app.secret_key = get_or_create_secret_key()
app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER
app.config["MAX_CONTENT_LENGTH"] = MAX_FILE_SIZE


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


def load_json_gz(path, default):
    if not os.path.exists(path):
        return default

    try:
        with gzip.open(path, "rt", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def save_json_gz(path, data):
    with gzip.open(path, "wt", encoding="utf-8") as f:
        json.dump(data, f, separators=(",", ":"))


def load_users():
    return load_json_gz(USERS_FILE, {})


def save_users(users):
    save_json_gz(USERS_FILE, users)


def load_discussions():
    return load_json_gz(DISCUSSION_FILE, [])


def save_discussions(data):
    save_json_gz(DISCUSSION_FILE, data)


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

.login-card,
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
                        <span>private access</span>
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
        <div class="small">opening your producer space.</div>
    </div>
</div>

{% endif %}

<script>
const files = {{ files|tojson }};
const fileSizes = {{ file_sizes|tojson }};
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
                <div class="file-title">${escapeHtml(file)}</div>
                <div class="meta">new file · ${escapeHtml(fileSizes[file] || "")}</div>
                <a class="file-link" href="/download/${encodeURIComponent(file)}" target="_blank">download</a>
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
            <div class="file-row" data-name="${escapeAttr(file.toLowerCase())}">
                <div class="file-title">${escapeHtml(file)}</div>
                <div class="meta">${escapeHtml(fileSizes[file] || "")}</div>
                <a class="file-link" href="/download/${encodeURIComponent(file)}" target="_blank">download</a>
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
            <p class="small">allowed: zip and mp3 only. maximum size: 1 GB.</p>
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
        <button onclick="showDiscussion(document.getElementById('menuDiscussion'))">back to discussion</button>
        <br><br>

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
        <div class="page-sub">Edit your username, password or logout.</div>

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
    file_sizes = {file: file_size_text(file) for file in files}

    return render_template_string(
        HTML,
        files=files,
        file_sizes=file_sizes,
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


@app.route("/topic", methods=["POST"])
@login_required
def add_topic():
    title = request.form.get("title", "").strip()
    body = request.form.get("body", "").strip()

    if not title or not body:
        flash("Topic title and text are required.", "error")
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

    flash("Topic added.", "success")
    return go("topic", topic_id)


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

    flash("Topic not found.", "error")
    return go("discussion")


@app.errorhandler(413)
def too_large(error):
    flash("File too large. Maximum file size is 1 GB.", "error")
    return go("files")


if __name__ == "__main__":
    app.run(debug=True)

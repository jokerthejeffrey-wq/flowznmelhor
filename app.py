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

CREDITS = [
    "FlowZNmelhor",
    "Your Name Here"
]


def go(view="home", topic_id=None):
    if topic_id:
        return redirect(url_for("home", view=view, id=topic_id))
    return redirect(url_for("home", view=view))


def load_json_gz(path, default):
    if not os.path.exists(path):
        return default

    try:
        with gzip.open(path, "rt", encoding="utf-8") as f:
            return json.load(f)
    except:
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


def username_exists(username, ignore_email=None):
    username = username.strip().lower()
    users = load_users()

    for email, user in users.items():
        if ignore_email and email == ignore_email:
            continue

        if user.get("username", "").strip().lower() == username:
            return True

    return False


def valid_username(username):
    username = username.strip()

    if len(username) < 3:
        return False

    if len(username) > 20:
        return False

    allowed = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_."

    for char in username:
        if char not in allowed:
            return False

    return True


def update_author_name(old_email, new_username):
    discussions = load_discussions()

    for topic in discussions:
        if topic.get("author_email") == old_email:
            topic["author"] = new_username

        for comment in topic.get("comments", []):
            if comment.get("author_email") == old_email:
                comment["author"] = new_username

    save_discussions(discussions)


def login_required(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        if not current_user():
            flash("Please login first.", "error")
            return go("login")
        return func(*args, **kwargs)

    return wrapper


def allowed_file(filename):
    ext = os.path.splitext(filename.lower())[1]
    return ext in ALLOWED_EXTENSIONS


def get_files():
    data = []

    for file in os.listdir(UPLOAD_FOLDER):
        full = os.path.join(UPLOAD_FOLDER, file)

        if os.path.isfile(full) and allowed_file(file):
            data.append(file)

    return sorted(data)


HTML = """
<!DOCTYPE html>
<html>
<head>
<title>FlowZNmelhor</title>

<style>
*{box-sizing:border-box}

body{
    margin:0;
    background:#070707;
    color:#e6e6e6;
    font-family:Arial,sans-serif;
    height:100vh;
    display:flex;
    overflow:hidden;
}

.side{
    width:260px;
    padding:30px 22px;
    border-right:1px solid #1f1f1f;
    display:flex;
    flex-direction:column;
}

.title{
    font-size:13px;
    letter-spacing:2px;
    color:#666;
    margin-bottom:34px;
}

.menu-main{flex:1}

.menu-bottom{
    border-top:1px solid #222;
    padding-top:18px;
}

.item{
    cursor:pointer;
    user-select:none;
    transition:.25s ease;
    line-height:2.1;
    color:#aaa;
}

.item:hover,.item.active{
    color:white;
    transform:translateX(5px);
}

.clicked{animation:clickFade .35s ease}

@keyframes clickFade{
    0%{opacity:1}
    40%{opacity:.35;transform:translateX(8px)}
    100%{opacity:1;transform:translateX(5px)}
}

.content{
    flex:1;
    padding:56px;
    transition:opacity .25s ease,transform .25s ease;
    overflow-y:auto;
}

.content.fade{
    opacity:0;
    transform:translateY(8px);
}

.page-title{
    font-size:32px;
    font-weight:normal;
    margin-bottom:24px;
}

.line{
    border-left:1px solid #333;
    padding-left:24px;
    max-width:900px;
}

input,textarea{
    background:#090909;
    color:#ddd;
    border:1px solid #333;
    padding:12px 14px;
    outline:none;
    border-radius:0;
    font-size:14px;
}

input:focus,textarea:focus{border-color:#777}

textarea{
    width:100%;
    min-height:90px;
    resize:vertical;
    margin-top:10px;
}

.search-bar{
    width:300px;
    margin-bottom:28px;
}

.file-row,.credit-row,.topic-row,.comment-row{
    margin-bottom:22px;
}

.file-title,.credit-name,.topic-title{
    font-size:15px;
    color:#eee;
    margin-bottom:6px;
}

.topic-meta,.comment-meta,.small{
    color:#777;
    font-size:14px;
    line-height:1.7;
}

.file-link,.topic-open{
    color:#777;
    text-decoration:none;
    font-size:14px;
    word-break:break-all;
    transition:.25s ease;
    cursor:pointer;
}

.file-link:hover,.topic-open:hover{
    color:white;
    padding-left:6px;
}

.form-box{
    margin-top:35px;
    border-left:1px solid #333;
    padding-left:24px;
    max-width:720px;
}

button,.file-button{
    display:inline-flex;
    align-items:center;
    justify-content:center;
    gap:10px;
    background:#111;
    color:#eee;
    border:1px solid #333;
    padding:11px 18px;
    border-radius:0;
    cursor:pointer;
    transition:.25s ease;
    font-size:14px;
    text-decoration:none;
}

button:hover,.file-button:hover{
    background:#191919;
    color:white;
    border-color:#555;
    transform:translateY(-1px);
}

.login-card,.account-box{
    width:420px;
    background:#080808;
    border-left:1px solid #333;
    border-radius:0;
    padding:28px;
    box-shadow:none;
}

.login-card-title{
    font-size:24px;
    color:#fff;
    margin-bottom:8px;
}

.login-card-sub{
    color:#777;
    font-size:14px;
    margin-bottom:24px;
}

.login-btn{
    width:100%;
    margin-bottom:12px;
    height:46px;
}

.login-primary{
    background:#f2f2f2;
    color:#050505;
    border-color:#f2f2f2;
}

.login-primary:hover{
    background:white;
    color:#000;
}

.login-input{
    width:100%;
    margin-bottom:12px;
    border-radius:0;
}

.back-btn{margin-top:8px}

.selected-file{
    color:#666;
    font-size:14px;
    margin-left:10px;
}

.comment-row{
    border-left:1px solid #252525;
    padding-left:18px;
}

.alert-box{
    background:#140707;
    color:#ff6b6b;
    border-left:2px solid #ff3333;
    padding:12px 16px;
    margin-bottom:22px;
    font-size:14px;
    max-width:420px;
}

.success-box{
    background:#071407;
    color:#67e88b;
    border-left:2px solid #2ecc71;
    padding:12px 16px;
    margin-bottom:22px;
    font-size:14px;
    max-width:420px;
}

.account-section{
    margin-top:28px;
    padding-top:22px;
    border-top:1px solid #222;
}
</style>
</head>

<body>

<div class="side">
    <div class="title">FLOWZNMELHOR</div>

    <div class="menu-main">
        <div class="item" id="menuFiles" onclick="showFiles(this)">files</div>
        <div class="item" id="menuDiscussion" onclick="showDiscussion(this)">discussion</div>
    </div>

    <div class="menu-bottom">
        {% if user_email %}
            <div class="item" id="menuAccount" onclick="showAccount(this)">account</div>
        {% else %}
            <div class="item" id="menuLogin" onclick="showLogin(this)">login</div>
        {% endif %}

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

    <div class="page-title">select a section</div>
    <div class="small">click a menu item.</div>
</div>

<script>
const files = {{ files|tojson }};
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
    content.classList.add("fade");

    setTimeout(()=>{
        content.innerHTML=html;
        content.classList.remove("fade");
        bindFileInput();
    },180);
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

function showFiles(button){
    clickEffect(button);
    clearActive();
    if(button) button.classList.add("active");

    let html=`
        <div class="page-title">files</div>
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
                <a class="file-link" href="/download/${encodeURIComponent(file)}" target="_blank">download</a>
            </div>
        `;
    });

    html+=`</div>`;

    if(userEmail){
        html+=`
            <div class="form-box">
                <form action="/upload" method="POST" enctype="multipart/form-data">
                    <input id="fileInput" type="file" name="uploadfile" accept=".zip,.mp3" required hidden>

                    <label for="fileInput" class="file-button">select zip or mp3</label>
                    <span id="fileName" class="selected-file">no file selected</span>

                    <br><br>

                    <button type="submit">upload file</button>
                </form>

                <p class="small">allowed: zip and mp3 only. upload folders as zip files.</p>
            </div>
        `;
    }else{
        html+=`
            <div class="form-box">
                <div class="small">login to upload files.</div>
            </div>
        `;
    }

    fadeChange(html);
}

function filterFiles(){
    const search=document.getElementById("searchInput").value.toLowerCase();

    document.querySelectorAll(".file-row").forEach(row=>{
        const name=row.getAttribute("data-name");
        row.style.display=name.includes(search) ? "block" : "none";
    });
}

function showLogin(button){
    clickEffect(button);
    clearActive();
    if(button) button.classList.add("active");

    let html=`<div class="page-title">login</div><div class="line">`;

    if(userEmail){
        html+=`
            <div class="login-card">
                <div class="login-card-title">Account</div>
                <div class="login-card-sub">logged in as ${escapeHtml(username)}</div>

                <form action="/logout" method="POST">
                    <button class="login-btn login-primary" type="submit">logout</button>
                </form>
            </div>
        `;
    }else{
        html+=`
            <div class="login-card" id="loginCard">
                <div class="login-card-title">Welcome back</div>
                <div class="login-card-sub">login or create an account.</div>

                <button class="login-btn login-primary" onclick="showPasswordLogin()">login</button>
                <button class="login-btn" onclick="showCreateAccount()">create account</button>
            </div>
        `;
    }

    html+=`</div>`;
    fadeChange(html);
}

function showPasswordLogin(){
    document.getElementById("loginCard").innerHTML=`
        <div class="login-card-title">Login</div>
        <div class="login-card-sub">enter your account details.</div>

        <form action="/password-login" method="POST">
            <input class="login-input" name="email" type="email" placeholder="email" required>
            <input class="login-input" name="password" type="password" placeholder="password" required>

            <button class="login-btn login-primary" type="submit">login</button>
        </form>

        <button class="login-btn" onclick="showCreateAccount()">create account</button>
        <button class="back-btn" onclick="showLoginFromInside()">back</button>
    `;
}

function showCreateAccount(){
    document.getElementById("loginCard").innerHTML=`
        <div class="login-card-title">Create account</div>
        <div class="login-card-sub">choose a unique username.</div>

        <form action="/register" method="POST">
            <input class="login-input" name="username" placeholder="username" required>
            <input class="login-input" name="email" type="email" placeholder="email" required>
            <input class="login-input" name="password" type="password" placeholder="password" required>

            <button class="login-btn login-primary" type="submit">create account</button>
        </form>

        <button class="login-btn" onclick="showPasswordLogin()">login</button>
        <button class="back-btn" onclick="showLoginFromInside()">back</button>
    `;
}

function showLoginFromInside(){
    document.getElementById("loginCard").innerHTML=`
        <div class="login-card-title">Welcome back</div>
        <div class="login-card-sub">login or create an account.</div>

        <button class="login-btn login-primary" onclick="showPasswordLogin()">login</button>
        <button class="login-btn" onclick="showCreateAccount()">create account</button>
    `;
}

function showAccount(button){
    clickEffect(button);
    clearActive();
    if(button) button.classList.add("active");

    let html=`
        <div class="page-title">account</div>
        <div class="line">
            <div class="account-box">
                <div class="login-card-title">${escapeHtml(username)}</div>
                <div class="login-card-sub">${escapeHtml(userEmail)}</div>

                <div class="account-section">
                    <div class="topic-title">change username</div>
                    <br>

                    <form action="/change-username" method="POST">
                        <input class="login-input" name="new_username" placeholder="new username" required>
                        <button class="login-btn login-primary" type="submit">save username</button>
                    </form>
                </div>

                <div class="account-section">
                    <div class="topic-title">change password</div>
                    <br>

                    <form action="/change-password" method="POST">
                        <input class="login-input" name="old_password" type="password" placeholder="old password" required>
                        <input class="login-input" name="new_password" type="password" placeholder="new password" required>
                        <button class="login-btn login-primary" type="submit">save password</button>
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

function showDiscussion(button){
    clickEffect(button);
    clearActive();
    if(button) button.classList.add("active");

    let html=`
        <div class="page-title">discussion</div>
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
                <div class="topic-open" onclick="openTopic('${topic.id}')">open discussion</div>
            </div>
        `;
    });

    html+=`</div>`;

    if(userEmail){
        html+=`
            <div class="form-box">
                <form action="/topic" method="POST">
                    <input name="title" placeholder="topic title" required style="width:100%;">
                    <textarea name="body" placeholder="write topic text" required></textarea>
                    <br><br>
                    <button type="submit">add topic</button>
                </form>
            </div>
        `;
    }else{
        html+=`
            <div class="form-box">
                <div class="small">login to add topic or comment.</div>
            </div>
        `;
    }

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

    clearActive();
    const discussionButton=document.getElementById("menuDiscussion");
    if(discussionButton) discussionButton.classList.add("active");

    let html=`
        <div class="page-title">${escapeHtml(topic.title)}</div>
        <div class="line">
            <div class="topic-meta">by ${escapeHtml(topic.author)}</div>
            <br>
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

    if(userEmail){
        html+=`
            <div class="form-box">
                <form action="/comment/${topic.id}" method="POST">
                    <textarea name="body" placeholder="write comment" required></textarea>
                    <br><br>
                    <button type="submit">add comment</button>
                </form>
            </div>
        `;
    }

    fadeChange(html);
}

function showCredits(button){
    clickEffect(button);
    clearActive();
    if(button) button.classList.add("active");

    let html=`<div class="page-title">credits</div><div class="line">`;

    credits.forEach(person=>{
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
    setTimeout(()=>{
        if(startView === "files"){
            showFiles(document.getElementById("menuFiles"));
        }else if(startView === "discussion"){
            showDiscussion(document.getElementById("menuDiscussion"));
        }else if(startView === "topic" && startTopicId){
            openTopic(startTopicId);
        }else if(startView === "account"){
            const accountButton=document.getElementById("menuAccount");
            if(accountButton){
                showAccount(accountButton);
            }else{
                showLogin(document.getElementById("menuLogin"));
            }
        }else if(startView === "login"){
            showLogin(document.getElementById("menuLogin"));
        }
    },100);
});
</script>

</body>
</html>
"""


@app.route("/")
def home():
    return render_template_string(
        HTML,
        files=get_files(),
        credits=CREDITS,
        discussions=load_discussions(),
        user_email=current_user(),
        username=current_username(),
        start_view=request.args.get("view", ""),
        start_topic_id=request.args.get("id", "")
    )


@app.route("/register", methods=["POST"])
def register():
    username = request.form.get("username", "").strip()
    email = request.form.get("email", "").strip().lower()
    password = request.form.get("password", "")

    if not valid_username(username):
        flash("Username must be 3-20 characters and only use letters, numbers, underscore, or dot.", "error")
        return go("login")

    if username_exists(username):
        flash("Username already exists. Choose another one.", "error")
        return go("login")

    if "@" not in email or "." not in email:
        flash("Invalid email address.", "error")
        return go("login")

    if len(password) < 6:
        flash("Password must be at least 6 characters.", "error")
        return go("login")

    users = load_users()

    if email in users:
        flash("Account already exists. Please login instead.", "error")
        return go("login")

    users[email] = {
        "username": username,
        "password_hash": generate_password_hash(password),
        "created": int(time.time())
    }

    save_users(users)
    session["email"] = email

    flash("Account created successfully.", "success")
    return go("account")


@app.route("/password-login", methods=["POST"])
def password_login():
    email = request.form.get("email", "").strip().lower()
    password = request.form.get("password", "")

    users = load_users()
    user = users.get(email)

    if not user:
        flash("Account not found. Please create an account first.", "error")
        return go("login")

    if not check_password_hash(user.get("password_hash", ""), password):
        flash("Wrong password. Please try again.", "error")
        return go("login")

    session["email"] = email

    flash("Logged in successfully.", "success")
    return go("account")


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
        return go("login")

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
        return go("login")

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


@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    flash("Logged out.", "success")
    return go("login")


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


@app.route("/download/<filename>")
def download(filename):
    filename = secure_filename(filename)

    return send_from_directory(
        UPLOAD_FOLDER,
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

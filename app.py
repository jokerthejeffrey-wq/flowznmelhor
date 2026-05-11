import os
import json
import gzip
import time
import random
from functools import wraps
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash
from flask import Flask, render_template_string, request, redirect, url_for, send_from_directory, session
from flask_mail import Mail, Message
from authlib.integrations.flask_client import OAuth

app = Flask(__name__)

app.secret_key = os.environ.get("SECRET_KEY", "change-this-secret-key")

UPLOAD_FOLDER = "uploads"
DATA_FOLDER = "data"
USERS_FILE = os.path.join(DATA_FOLDER, "users.json.gz")
DISCUSSION_FILE = os.path.join(DATA_FOLDER, "discussions.json.gz")

MAX_FILE_SIZE = 1024 * 1024 * 1024
ALLOWED_EXTENSIONS = {".zip", ".mp3"}

app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER
app.config["MAX_CONTENT_LENGTH"] = MAX_FILE_SIZE

app.config["MAIL_SERVER"] = os.environ.get("MAIL_SERVER", "smtp.gmail.com")
app.config["MAIL_PORT"] = int(os.environ.get("MAIL_PORT", "587"))
app.config["MAIL_USE_TLS"] = True
app.config["MAIL_USERNAME"] = os.environ.get("MAIL_USERNAME", "")
app.config["MAIL_PASSWORD"] = os.environ.get("MAIL_PASSWORD", "")
app.config["MAIL_DEFAULT_SENDER"] = os.environ.get("MAIL_USERNAME", "")

mail = Mail(app)

oauth = OAuth(app)
google = oauth.register(
    name="google",
    client_id=os.environ.get("GOOGLE_CLIENT_ID", ""),
    client_secret=os.environ.get("GOOGLE_CLIENT_SECRET", ""),
    server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
    client_kwargs={"scope": "openid email profile"}
)

CREDITS = [
    "FlowZNmelhor",
    "Burlacu",
    "Your Name Here"
]

os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(DATA_FOLDER, exist_ok=True)


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


def login_required(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        if not current_user():
            return redirect(url_for("home"))
        return func(*args, **kwargs)
    return wrapper


def send_verification_email(email, code):
    if not app.config["MAIL_USERNAME"] or not app.config["MAIL_PASSWORD"]:
        print("EMAIL VERIFICATION CODE:", code)
        return

    msg = Message(
        subject="FlowZNmelhor verification code",
        recipients=[email],
        body=f"Your FlowZNmelhor verification code is: {code}"
    )
    mail.send(msg)


def safe_folder_name(name):
    name = secure_filename(name.strip())
    return name if name else "folder"


def allowed_file(filename):
    ext = os.path.splitext(filename.lower())[1]
    return ext in ALLOWED_EXTENSIONS


def get_folders():
    folders = []

    for name in os.listdir(UPLOAD_FOLDER):
        path = os.path.join(UPLOAD_FOLDER, name)
        if os.path.isdir(path):
            folders.append(name)

    return sorted(folders)


def get_files():
    data = {}

    for folder in get_folders():
        folder_path = os.path.join(UPLOAD_FOLDER, folder)
        data[folder] = []

        for file in os.listdir(folder_path):
            full = os.path.join(folder_path, file)
            if os.path.isfile(full) and allowed_file(file):
                data[folder].append(file)

        data[folder].sort()

    return data


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

.menu-main{
    flex:1;
}

.menu-bottom{
    border-top:1px solid #222;
    padding-top:18px;
}

.item,.subitem{
    cursor:pointer;
    user-select:none;
    transition:.25s ease;
    line-height:2.1;
}

.item{color:#aaa}
.subitem{color:#777}

.item:hover,.subitem:hover,.item.active,.subitem.active{
    color:white;
    transform:translateX(5px);
}

.clicked{animation:clickFade .35s ease}

@keyframes clickFade{
    0%{opacity:1}
    40%{opacity:.35;transform:translateX(8px)}
    100%{opacity:1;transform:translateX(5px)}
}

.branch{
    margin-left:14px;
    padding-left:14px;
    border-left:1px solid #333;
    max-height:0;
    overflow:hidden;
    opacity:0;
    transition:max-height .45s ease,opacity .35s ease;
}

.branch.open{
    max-height:300px;
    opacity:1;
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
    padding:10px 14px;
    outline:none;
    border-radius:0;
    font-size:14px;
}

input:focus,textarea:focus{
    border-color:#666;
}

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

.file-row,.credit-row,.topic-row,.comment-row,.folder-row{
    margin-bottom:22px;
}

.file-title,.credit-name,.topic-title,.folder-title{
    font-size:15px;
    color:#eee;
    margin-bottom:6px;
}

.topic-meta,.comment-meta,.small{
    color:#666;
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

.file-button,button{
    display:inline-block;
    background:#111;
    color:#ddd;
    border:1px solid #333;
    padding:10px 18px;
    border-radius:0;
    cursor:pointer;
    transition:.25s ease;
    font-size:14px;
}

.file-button:hover,button:hover{
    background:#1b1b1b;
    color:white;
    border-color:#555;
}

.selected-file{
    color:#666;
    font-size:14px;
    margin-left:10px;
}

.comment-row{
    border-left:1px solid #252525;
    padding-left:18px;
}

.google-btn{
    margin-top:10px;
    display:inline-block;
    text-decoration:none;
    background:#111;
    color:#ddd;
    border:1px solid #333;
    padding:10px 18px;
    font-size:14px;
}

.google-btn:hover{
    background:#1b1b1b;
    color:white;
}

.hidden{
    display:none;
}
</style>
</head>

<body>

<div class="side">
    <div class="title">FLOWZNMELHOR</div>

    <div class="menu-main">
        <div class="item" onclick="toggleFiles(this)">files</div>

        <div id="fileBranch" class="branch">
            <div class="subitem" onclick="showFiles(this)">Projects and Files</div>
        </div>

        <div class="item" onclick="showDiscussion(this)">discussion</div>
        <div class="item" onclick="clearPage(this,'burlacu')">burlacu</div>
    </div>

    <div class="menu-bottom">
        <div class="item" onclick="showLogin(this)">login</div>
        <div class="item" onclick="showCredits(this)">credits</div>
    </div>
</div>

<div class="content" id="content">
    <div class="page-title">select a section</div>
    <div class="small">click a menu item.</div>
</div>

<script>
const folders = {{ folders|tojson }};
const files = {{ files|tojson }};
const credits = {{ credits|tojson }};
const discussions = {{ discussions|tojson }};
const userEmail = {{ user_email|tojson }};

function clickEffect(el){
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
    document.querySelectorAll(".subitem").forEach(i=>i.classList.remove("active"));
}

function toggleFiles(button){
    clickEffect(button);

    const branch=document.getElementById("fileBranch");
    branch.classList.toggle("open");

    clearActive();
    button.classList.add("active");

    fadeChange(`
        <div class="page-title">files</div>
        <div class="small">click Projects and Files.</div>
    `);
}

function showFiles(button){
    clickEffect(button);
    document.querySelectorAll(".subitem").forEach(i=>i.classList.remove("active"));
    button.classList.add("active");

    let html=`
        <div class="page-title">Projects and Files</div>
        <input id="searchInput" class="search-bar" placeholder="search files" oninput="filterFiles()">
        <div class="line">
    `;

    if(folders.length===0){
        html+=`<div class="small">no folders yet.</div>`;
    }

    folders.forEach(folder=>{
        html+=`
            <div class="folder-row">
                <div class="folder-title">${escapeHtml(folder)}</div>
        `;

        if(!files[folder] || files[folder].length===0){
            html+=`<div class="small">empty folder.</div>`;
        }

        files[folder].forEach(file=>{
            html+=`
                <div class="file-row" data-name="${escapeAttr((folder + ' ' + file).toLowerCase())}">
                    <div class="file-title">${escapeHtml(file)}</div>
                    <a class="file-link" href="/download/${encodeURIComponent(folder)}/${encodeURIComponent(file)}" target="_blank">download</a>
                </div>
            `;
        });

        html+=`</div>`;
    });

    html+=`</div>`;

    if(userEmail){
        html+=`
            <div class="form-box">
                <form action="/create-folder" method="POST">
                    <input name="folder" placeholder="new folder name" required>
                    <button type="submit">add folder</button>
                </form>

                <br>

                <form action="/upload" method="POST" enctype="multipart/form-data">
                    <input name="folder" placeholder="folder name" required>
                    <br><br>

                    <input id="fileInput" type="file" name="uploadfile" accept=".zip,.mp3" required hidden>

                    <label for="fileInput" class="file-button">select file</label>
                    <span id="fileName" class="selected-file">no file selected</span>

                    <br><br>

                    <button type="submit">upload file</button>
                </form>

                <p class="small">allowed: zip and mp3 only. maximum upload size: 1 GB.</p>
            </div>
        `;
    }else{
        html+=`
            <div class="form-box">
                <div class="small">login to add folders or upload files.</div>
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
    button.classList.add("active");

    let html=`<div class="page-title">login</div><div class="line">`;

    if(userEmail){
        html+=`
            <div class="small">logged in as ${escapeHtml(userEmail)}</div>
            <br>
            <form action="/logout" method="POST">
                <button type="submit">logout</button>
            </form>
        `;
    }else{
        html+=`
            <div class="small">create account with email verification</div>
            <br>

            <form action="/register" method="POST">
                <input name="email" type="email" placeholder="email" required>
                <br><br>
                <input name="password" type="password" placeholder="password" required>
                <br><br>
                <button type="submit">create account</button>
            </form>

            <br><br>

            <div class="small">verify email</div>
            <br>

            <form action="/verify" method="POST">
                <input name="email" type="email" placeholder="email" required>
                <br><br>
                <input name="code" placeholder="verification code" required>
                <br><br>
                <button type="submit">verify</button>
            </form>

            <br><br>

            <div class="small">login with password</div>
            <br>

            <form action="/password-login" method="POST">
                <input name="email" type="email" placeholder="email" required>
                <br><br>
                <input name="password" type="password" placeholder="password" required>
                <br><br>
                <button type="submit">login</button>
            </form>

            <br>

            <a class="google-btn" href="/google-login">login with google</a>
        `;
    }

    html+=`</div>`;
    fadeChange(html);
}

function showDiscussion(button){
    clickEffect(button);
    clearActive();
    button.classList.add("active");

    let html=`
        <div class="page-title">discussion</div>
        <div class="line">
    `;

    if(discussions.length===0){
        html+=`<div class="small">no topics yet.</div>`;
    }

    discussions.forEach(topic=>{
        html+=`
            <div class="topic-row">
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

function openTopic(topicId){
    const topic=discussions.find(t=>t.id===topicId);
    if(!topic) return;

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
    button.classList.add("active");

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

function clearPage(button,name){
    clickEffect(button);
    clearActive();
    button.classList.add("active");

    fadeChange(`
        <div class="page-title">${name}</div>
        <div class="small">content will be added here later.</div>
    `);
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
</script>

</body>
</html>
"""


@app.route("/")
def home():
    return render_template_string(
        HTML,
        folders=get_folders(),
        files=get_files(),
        credits=CREDITS,
        discussions=load_discussions(),
        user_email=current_user()
    )


@app.route("/register", methods=["POST"])
def register():
    email = request.form.get("email", "").strip().lower()
    password = request.form.get("password", "")

    if "@" not in email or "." not in email:
        return "Invalid email.", 400

    if len(password) < 6:
        return "Password must be at least 6 characters.", 400

    users = load_users()

    if email in users:
        return "Account already exists.", 400

    code = str(random.randint(100000, 999999))

    users[email] = {
        "password_hash": generate_password_hash(password),
        "verified": False,
        "verification_code": code,
        "created": int(time.time()),
        "google": False
    }

    save_users(users)
    send_verification_email(email, code)

    return redirect(url_for("home"))


@app.route("/verify", methods=["POST"])
def verify():
    email = request.form.get("email", "").strip().lower()
    code = request.form.get("code", "").strip()

    users = load_users()
    user = users.get(email)

    if not user:
        return "Account not found.", 404

    if user.get("verification_code") != code:
        return "Wrong verification code.", 400

    user["verified"] = True
    user["verification_code"] = ""
    users[email] = user
    save_users(users)

    session["email"] = email
    return redirect(url_for("home"))


@app.route("/password-login", methods=["POST"])
def password_login():
    email = request.form.get("email", "").strip().lower()
    password = request.form.get("password", "")

    users = load_users()
    user = users.get(email)

    if not user:
        return "Account not found.", 404

    if not user.get("verified"):
        return "Email is not verified.", 403

    if not check_password_hash(user.get("password_hash", ""), password):
        return "Wrong password.", 403

    session["email"] = email
    return redirect(url_for("home"))


@app.route("/google-login")
def google_login():
    redirect_uri = url_for("google_callback", _external=True)
    return google.authorize_redirect(redirect_uri)


@app.route("/google-callback")
def google_callback():
    token = google.authorize_access_token()
    info = google.parse_id_token(token)

    email = info.get("email", "").lower()

    if not email:
        return "Google login failed.", 400

    users = load_users()

    if email not in users:
        users[email] = {
            "password_hash": "",
            "verified": True,
            "verification_code": "",
            "created": int(time.time()),
            "google": True
        }
        save_users(users)

    session["email"] = email
    return redirect(url_for("home"))


@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return redirect(url_for("home"))


@app.route("/create-folder", methods=["POST"])
@login_required
def create_folder():
    folder = safe_folder_name(request.form.get("folder", ""))

    os.makedirs(os.path.join(UPLOAD_FOLDER, folder), exist_ok=True)

    return redirect(url_for("home"))


@app.route("/upload", methods=["POST"])
@login_required
def upload():
    folder = safe_folder_name(request.form.get("folder", ""))
    folder_path = os.path.join(UPLOAD_FOLDER, folder)

    os.makedirs(folder_path, exist_ok=True)

    if "uploadfile" not in request.files:
        return redirect(url_for("home"))

    file = request.files["uploadfile"]

    if file.filename == "":
        return redirect(url_for("home"))

    if not allowed_file(file.filename):
        return "Only ZIP and MP3 files are allowed.", 400

    filename = secure_filename(file.filename)
    save_path = os.path.join(folder_path, filename)

    file.save(save_path)

    return redirect(url_for("home"))


@app.route("/download/<folder>/<filename>")
def download(folder, filename):
    folder = safe_folder_name(folder)
    filename = secure_filename(filename)

    return send_from_directory(
        os.path.join(UPLOAD_FOLDER, folder),
        filename,
        as_attachment=True
    )


@app.route("/topic", methods=["POST"])
@login_required
def add_topic():
    title = request.form.get("title", "").strip()
    body = request.form.get("body", "").strip()

    if not title or not body:
        return redirect(url_for("home"))

    discussions = load_discussions()

    discussions.insert(0, {
        "id": str(int(time.time() * 1000)),
        "title": title[:120],
        "body": body[:3000],
        "author": current_user(),
        "comments": []
    })

    save_discussions(discussions)
    return redirect(url_for("home"))


@app.route("/comment/<topic_id>", methods=["POST"])
@login_required
def add_comment(topic_id):
    body = request.form.get("body", "").strip()

    if not body:
        return redirect(url_for("home"))

    discussions = load_discussions()

    for topic in discussions:
        if topic["id"] == topic_id:
            topic["comments"].append({
                "author": current_user(),
                "body": body[:2000],
                "time": int(time.time())
            })
            break

    save_discussions(discussions)
    return redirect(url_for("home"))


@app.errorhandler(413)
def too_large(error):
    return "File too large. Maximum file size is 1 GB.", 413


if __name__ == "__main__":
    app.run(debug=True)

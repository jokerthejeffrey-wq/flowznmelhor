import os
import json
import gzip
import time
from werkzeug.utils import secure_filename
from flask import Flask, render_template_string, request, redirect, url_for, send_from_directory, session

app = Flask(__name__)

app.secret_key = "a9f3c7e2b1d8440fa837e91d0c21aa55"

UPLOAD_FOLDER = "zips"
DATA_FOLDER = "data"
DISCUSSION_FILE = os.path.join(DATA_FOLDER, "discussions.json.gz")

MAX_FILE_SIZE = 1024 * 1024 * 1024

CREDITS = [
    "FlowZNmelhor",
    "Burlacu",
    "Your Name Here"
]

app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER
app.config["MAX_CONTENT_LENGTH"] = MAX_FILE_SIZE

os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(DATA_FOLDER, exist_ok=True)


def load_discussions():
    if not os.path.exists(DISCUSSION_FILE):
        return []

    try:
        with gzip.open(DISCUSSION_FILE, "rt", encoding="utf-8") as f:
            return json.load(f)
    except:
        return []


def save_discussions(data):
    with gzip.open(DISCUSSION_FILE, "wt", encoding="utf-8") as f:
        json.dump(data, f, separators=(",", ":"))


def get_zip_files():
    return [
        f for f in os.listdir(UPLOAD_FOLDER)
        if f.lower().endswith(".zip") and os.path.isfile(os.path.join(UPLOAD_FOLDER, f))
    ]


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
}

.title{
    font-size:13px;
    letter-spacing:2px;
    color:#666;
    margin-bottom:34px;
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
    max-height:250px;
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
    max-width:850px;
}

.search-bar,input,textarea{
    background:#090909;
    color:#ddd;
    border:1px solid #333;
    padding:10px 14px;
    outline:none;
    border-radius:0;
    font-size:14px;
}

.search-bar{
    width:300px;
    margin-bottom:28px;
}

textarea{
    width:100%;
    min-height:90px;
    resize:vertical;
    margin-top:10px;
}

input:focus,textarea:focus,.search-bar:focus{
    border-color:#666;
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

.upload-box,.form-box{
    margin-top:35px;
    border-left:1px solid #333;
    padding-left:24px;
    max-width:700px;
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

.login-box{
    margin-bottom:30px;
    border-left:1px solid #333;
    padding-left:24px;
}

.comment-row{
    border-left:1px solid #252525;
    padding-left:18px;
}

.topic-actions{
    margin-top:10px;
}

.hidden{
    display:none;
}
</style>
</head>

<body>

<div class="side">
    <div class="title">FLOWZNMELHOR</div>

    <div class="item" onclick="toggleZips(this)">zips</div>
    <div id="zipBranch" class="branch">
        <div class="subitem" onclick="showProjects(this)">Projects and Zips</div>
    </div>

    <div class="item" onclick="showDiscussion(this)">discussion</div>
    <div class="item" onclick="clearPage(this,'burlacu')">burlacu</div>
    <div class="item" onclick="showCredits(this)">credits</div>
</div>

<div class="content" id="content">
    <div class="page-title">select a section</div>
    <div class="small">click a menu item.</div>
</div>

<script>
const uploadedFiles = {{ files|tojson }};
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
    const input=document.getElementById("zipInput");
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

function toggleZips(button){
    clickEffect(button);

    const branch=document.getElementById("zipBranch");
    branch.classList.toggle("open");

    clearActive();
    button.classList.add("active");

    fadeChange(`
        <div class="page-title">zips</div>
        <div class="small">click Projects and Zips.</div>
    `);
}

function filterZips(){
    const search=document.getElementById("searchInput").value.toLowerCase();

    document.querySelectorAll(".file-row").forEach(row=>{
        const name=row.getAttribute("data-name");
        row.style.display=name.includes(search) ? "block" : "none";
    });
}

function showProjects(button){
    clickEffect(button);

    document.querySelectorAll(".subitem").forEach(i=>i.classList.remove("active"));
    button.classList.add("active");

    let html=`
        <div class="page-title">Projects and Zips</div>

        <input id="searchInput" class="search-bar" placeholder="search zips" oninput="filterZips()">

        <div class="line">
    `;

    if(uploadedFiles.length===0){
        html+=`<div class="small">no zips uploaded yet.</div>`;
    }

    uploadedFiles.forEach(file=>{
        html+=`
            <div class="file-row" data-name="${file.toLowerCase()}">
                <div class="file-title">${file}</div>
                <a class="file-link" href="/download/${file}" target="_blank">download</a>
            </div>
        `;
    });

    html+=`
        </div>

        <div class="upload-box">
            <form action="/upload" method="POST" enctype="multipart/form-data">
                <input id="zipInput" type="file" name="zipfile" accept=".zip" required hidden>

                <label for="zipInput" class="file-button">select zip</label>
                <span id="fileName" class="selected-file">no file selected</span>

                <br><br>

                <button type="submit">upload zip</button>
            </form>

            <p class="small">maximum upload size: 1 GB</p>
        </div>
    `;

    fadeChange(html);
}

function loginBlock(){
    if(userEmail){
        return `
            <div class="login-box">
                <div class="small">logged in as ${userEmail}</div>
                <form action="/logout" method="POST">
                    <button type="submit">logout</button>
                </form>
            </div>
        `;
    }

    return `
        <div class="login-box">
            <form action="/login" method="POST">
                <input name="email" type="email" placeholder="email login" required>
                <button type="submit">login</button>
            </form>
            <div class="small">use Gmail or any email.</div>
        </div>
    `;
}

function showDiscussion(button){
    clickEffect(button);
    clearActive();
    button.classList.add("active");

    let html=`
        <div class="page-title">discussion</div>
        ${loginBlock()}
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
        ${loginBlock()}

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

    let html=`
        <div class="page-title">credits</div>
        <div class="line">
    `;

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
</script>

</body>
</html>
"""


@app.route("/")
def home():
    return render_template_string(
        HTML,
        files=get_zip_files(),
        credits=CREDITS,
        discussions=load_discussions(),
        user_email=session.get("email")
    )


@app.route("/login", methods=["POST"])
def login():
    email = request.form.get("email", "").strip().lower()

    if "@" not in email or "." not in email:
        return "Invalid email.", 400

    session["email"] = email
    return redirect(url_for("home"))


@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return redirect(url_for("home"))


@app.route("/topic", methods=["POST"])
def add_topic():
    if "email" not in session:
        return redirect(url_for("home"))

    title = request.form.get("title", "").strip()
    body = request.form.get("body", "").strip()

    if not title or not body:
        return redirect(url_for("home"))

    discussions = load_discussions()

    discussions.insert(0, {
        "id": str(int(time.time() * 1000)),
        "title": title[:120],
        "body": body[:3000],
        "author": session["email"],
        "comments": []
    })

    save_discussions(discussions)
    return redirect(url_for("home"))


@app.route("/comment/<topic_id>", methods=["POST"])
def add_comment(topic_id):
    if "email" not in session:
        return redirect(url_for("home"))

    body = request.form.get("body", "").strip()

    if not body:
        return redirect(url_for("home"))

    discussions = load_discussions()

    for topic in discussions:
        if topic["id"] == topic_id:
            topic["comments"].append({
                "author": session["email"],
                "body": body[:2000],
                "time": int(time.time())
            })
            break

    save_discussions(discussions)
    return redirect(url_for("home"))


@app.route("/upload", methods=["POST"])
def upload():
    if "zipfile" not in request.files:
        return redirect(url_for("home"))

    file = request.files["zipfile"]

    if file.filename == "":
        return redirect(url_for("home"))

    if not file.filename.lower().endswith(".zip"):
        return "Only ZIP files are allowed.", 400

    filename = secure_filename(file.filename)
    save_path = os.path.join(app.config["UPLOAD_FOLDER"], filename)

    file.save(save_path)

    return redirect(url_for("home"))


@app.route("/download/<filename>")
def download(filename):
    filename = secure_filename(filename)
    return send_from_directory(UPLOAD_FOLDER, filename, as_attachment=True)


@app.errorhandler(413)
def too_large(error):
    return "File too large. Maximum ZIP size is 1 GB.", 413


if __name__ == "__main__":
    app.run(debug=True)

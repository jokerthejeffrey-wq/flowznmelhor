import os
import re
import time
import threading
import subprocess
import requests
from flask import Flask, render_template_string

PORT = 5000
CLOUDFLARED = "cloudflared.exe"

app = Flask(__name__)

PACKS = {
    "Project ZIPs": [
        {"name": "Project Files", "url": "https://www.mediafire.com/file/example1/project.zip/file"},
        {"name": "Main Source", "url": "https://www.mediafire.com/file/example2/source.zip/file"}
    ],
    "Asset Packs": [
        {"name": "Textures Pack", "url": "https://www.mediafire.com/file/example3/textures.zip/file"},
        {"name": "Sounds Pack", "url": "https://www.mediafire.com/file/example4/sounds.zip/file"}
    ]
}

HTML = """
<!DOCTYPE html>
<html>
<head>
<title>FlowZNmelhor</title>
<style>
*{box-sizing:border-box}
body{margin:0;background:#080808;color:#e8e8e8;font-family:Arial,sans-serif;height:100vh;display:flex;overflow:hidden}
.side{width:270px;padding:30px 24px;border-right:1px solid #222}
.title{font-size:13px;letter-spacing:2px;color:#666;margin-bottom:34px}
.tree{font-size:15px;line-height:2.1}
.item,.subitem{cursor:pointer;user-select:none;transition:.3s ease;color:#aaa}
.subitem{color:#777}
.item:hover,.subitem:hover,.item.active,.subitem.active{color:white;transform:translateX(6px)}
.branch{margin-left:14px;padding-left:14px;border-left:1px solid #333;max-height:0;overflow:hidden;opacity:0;transition:max-height .5s ease,opacity .45s ease}
.branch.open{max-height:320px;opacity:1}
.content{flex:1;padding:56px;transition:opacity .28s ease,transform .28s ease}
.content.fade{opacity:0;transform:translateY(8px)}
.page-title{font-size:32px;font-weight:normal;margin-bottom:32px}
.links{max-width:680px;border-left:1px solid #333;padding-left:24px}
.link-block{margin-bottom:26px;animation:fadeIn .45s ease forwards}
.link-name{color:#e8e8e8;margin-bottom:6px;font-size:15px}
.link-url{color:#777;text-decoration:none;font-size:14px;word-break:break-all;transition:.25s ease}
.link-url:hover{color:white;padding-left:7px}
.empty{color:#666;line-height:1.7}
.clicked{animation:clickFade .45s ease}
@keyframes clickFade{0%{opacity:1}40%{opacity:.3;transform:translateX(10px)}100%{opacity:1;transform:translateX(6px)}}
@keyframes fadeIn{from{opacity:0;transform:translateY(8px)}to{opacity:1;transform:translateY(0)}}
</style>
</head>
<body>

<div class="side">
    <div class="title">FLOWZNMELHOR</div>

    <div class="tree">
        <div class="item" onclick="toggleZips(this)">zips</div>

        <div id="zipBranch" class="branch">
            {% for pack_name in packs.keys() %}
                <div class="subitem" onclick='showPack(this, {{ pack_name|tojson }})'>
                    {{ pack_name }}
                </div>
            {% endfor %}
        </div>

        <div class="item" onclick="clearPage(this)">tutorials</div>
        <div class="item" onclick="clearPage(this)">burlacu</div>
    </div>
</div>

<div class="content" id="content">
    <div class="page-title">select a section</div>
    <div class="empty">click zips, then choose a pack.</div>
</div>

<script>
const packs = {{ packs|tojson }};

function clickEffect(button){
    button.classList.remove("clicked");
    void button.offsetWidth;
    button.classList.add("clicked");
}

function fadeChange(html){
    const content=document.getElementById("content");
    content.classList.add("fade");
    setTimeout(()=>{
        content.innerHTML=html;
        content.classList.remove("fade");
    },220);
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
    fadeChange(`<div class="page-title">zips</div><div class="empty">choose project zips or asset packs from the left.</div>`);
}

function showPack(button,packName){
    clickEffect(button);
    document.querySelectorAll(".subitem").forEach(i=>i.classList.remove("active"));
    button.classList.add("active");

    const links=packs[packName];
    let html=`<div class="page-title">${packName}</div><div class="links">`;

    links.forEach(item=>{
        html+=`
            <div class="link-block">
                <div class="link-name">${item.name}</div>
                <a class="link-url" href="${item.url}" target="_blank">${item.url}</a>
            </div>
        `;
    });

    html+=`</div>`;
    fadeChange(html);
}

function clearPage(button){
    clickEffect(button);
    clearActive();
    button.classList.add("active");
    fadeChange(`<div class="page-title">${button.textContent}</div><div class="empty">content will be added here later.</div>`);
}
</script>

</body>
</html>
"""

@app.route("/")
def home():
    return render_template_string(HTML, packs=PACKS)

def download_cloudflared():
    if os.path.exists(CLOUDFLARED):
        return

    print("Downloading cloudflared.exe...")
    url = "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe"
    r = requests.get(url, timeout=60)

    with open(CLOUDFLARED, "wb") as f:
        f.write(r.content)

    print("cloudflared.exe downloaded.")

def run_flask():
    app.run(host="127.0.0.1", port=PORT, debug=False)

def run_tunnel():
    download_cloudflared()

    print("Starting FlowZNmelhor public website...")

    process = subprocess.Popen(
        [
            CLOUDFLARED,
            "tunnel",
            "--url",
            f"http://127.0.0.1:{PORT}"
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True
    )

    for line in process.stdout:
        print(line.strip())

        match = re.search(r"https://[-a-zA-Z0-9]+\\.trycloudflare\\.com", line)
        if match:
            print()
            print("FLOWZNMELHOR PUBLIC LINK:")
            print(match.group(0))
            print()

if __name__ == "__main__":
    threading.Thread(target=run_flask, daemon=True).start()
    time.sleep(1)
    run_tunnel()
from flask import Flask, render_template, request, jsonify
import os, json, io, time, platform, subprocess
from urllib.parse import urlparse, urljoin
from werkzeug.utils import secure_filename
from PIL import Image
import requests
from bs4 import BeautifulSoup

try:
    import psutil
except Exception:
    psutil = None

app = Flask(__name__, static_folder="static", template_folder="templates")
DATA_FILE = "tiles.json"
ICON_FOLDER = os.path.join(app.static_folder, "icons")
os.makedirs(ICON_FOLDER, exist_ok=True)

DEFAULT_ICON = "/static/icons/default.png"

def load_tiles():
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                s = f.read().strip()
                return json.loads(s) if s else []
        except Exception:
            return []
    return []

def save_tiles(tiles):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(tiles, f, ensure_ascii=False, indent=2)

def _save_png(content: bytes, filename: str) -> str:
    path = os.path.join(ICON_FOLDER, filename)
    try:
        im = Image.open(io.BytesIO(content)).convert("RGBA")
        im = im.resize((128, 128), Image.LANCZOS)
        im.save(path, format="PNG")
    except Exception:
        with open(path, "wb") as f:
            f.write(content)
    return f"/static/icons/{filename}"

def fetch_icon_for_url(site_url: str) -> str:
    # Normalize url
    try:
        parsed = urlparse(site_url)
        if not parsed.scheme:
            site_url = "http://" + site_url
            parsed = urlparse(site_url)
        domain = parsed.netloc.split("@")[-1].split(":")[0].lower()
    except Exception:
        return DEFAULT_ICON

    # 1) <link rel=icon ...>
    try:
        html = requests.get(site_url, timeout=6, headers={"User-Agent":"Mozilla/5.0"}).text
        soup = BeautifulSoup(html, "html.parser")
        icon_href = None
        for rel in ["icon", "shortcut icon", "apple-touch-icon", "mask-icon"]:
            tag = soup.find("link", rel=lambda v: v and rel in v.lower())
            if tag and tag.get("href"):
                icon_href = tag["href"]; break
        if icon_href:
            icon_url = urljoin(site_url, icon_href)
            r = requests.get(icon_url, timeout=6, headers={"User-Agent":"Mozilla/5.0"})
            if r.ok and r.content:
                return _save_png(r.content, f"{domain}.png")
    except Exception:
        pass

    # 2) /favicon.ico
    try:
        fav = f"{parsed.scheme}://{parsed.netloc}/favicon.ico"
        r = requests.get(fav, timeout=6, headers={"User-Agent":"Mozilla/5.0"})
        if r.ok and r.content:
            return _save_png(r.content, f"{domain}.png")
    except Exception:
        pass

    # 3) Providers
    for provider in [
        f"https://www.google.com/s2/favicons?domain={domain}&sz=128",
        f"https://icons.duckduckgo.com/ip3/{domain}.ico",
    ]:
        try:
            r = requests.get(provider, timeout=6, headers={"User-Agent":"Mozilla/5.0"})
            if r.ok and r.content:
                return _save_png(r.content, f"{domain}.png")
        except Exception:
            continue

    return DEFAULT_ICON

@app.route("/")
def index():
    tiles = load_tiles()
    for t in tiles:
        if not t.get("icon"):
            t["icon"] = DEFAULT_ICON
    return render_template("index.html", tiles=tiles)

@app.route("/upload_icon", methods=["POST"])
def upload_icon():
    file = request.files.get("file")
    if not file or file.filename == "":
        return jsonify(success=False, error="no file"), 400
    fname = secure_filename(file.filename)
    stem = os.path.splitext(fname)[0]
    fname = f"{stem}_{int(time.time())}.png"
    try:
        content = file.read()
        icon_path = _save_png(content, fname)
        return jsonify(success=True, icon=icon_path)
    except Exception as e:
        return jsonify(success=False, error=str(e)), 500

@app.route("/add", methods=["POST"])
def add():
    data = request.get_json(force=True, silent=True) or request.form
    label = (data.get("label") or "").strip()
    url = (data.get("url") or "").strip()
    icon_url = (data.get("icon") or data.get("icon_url") or "").strip()
    if not label or not url:
        return jsonify(success=False, error="label and url required"), 400

    if icon_url.startswith("/static/icons/"):
        icon = icon_url
    elif icon_url:
        try:
            r = requests.get(icon_url, timeout=6, headers={"User-Agent":"Mozilla/5.0"})
            icon = _save_png(r.content, f"custom_{int(time.time())}.png") if r.ok else fetch_icon_for_url(url)
        except Exception:
            icon = fetch_icon_for_url(url)
    else:
        icon = fetch_icon_for_url(url)

    tiles = load_tiles()
    tiles.append({"label": label, "url": url, "icon": icon})
    save_tiles(tiles)
    return jsonify(success=True)

@app.route("/remove", methods=["POST"])
def remove():
    data = request.get_json(force=True, silent=True) or request.form
    url = (data.get("url") or "").strip()
    tiles = load_tiles()
    tiles = [t for t in tiles if t.get("url") != url]
    save_tiles(tiles)
    return jsonify(success=True)

@app.route("/edit", methods=["POST"])
def edit():
    data = request.get_json(force=True, silent=True) or request.form
    original_url = (data.get("original_url") or "").strip()
    tiles = load_tiles()
    for t in tiles:
        if t.get("url") == original_url:
            t["label"] = (data.get("label") or t["label"]).strip()
            t["url"] = (data.get("url") or t["url"]).strip()
            new_icon = (data.get("icon") or data.get("icon_url") or "").strip()
            if new_icon:
                if new_icon.startswith("/static/icons/"):
                    t["icon"] = new_icon
                else:
                    try:
                        r = requests.get(new_icon, timeout=6, headers={"User-Agent":"Mozilla/5.0"})
                        if r.ok and r.content:
                            t["icon"] = _save_png(r.content, f"custom_{int(time.time())}.png")
                    except Exception:
                        pass
            break
    save_tiles(tiles)
    return jsonify(success=True)

def list_ports():
    # prefer psutil
    try:
        import psutil as _ps
        conns = _ps.net_connections(kind="inet")
        out = []
        for c in conns:
            if c.status == _ps.CONN_LISTEN and c.laddr:
                port = c.laddr.port
                pid = c.pid
                proc = None
                if pid:
                    try:
                        proc = _ps.Process(pid).name()
                    except Exception:
                        proc = None
                out.append({"port": port, "pid": pid, "proc": proc})
        uniq = {}
        for r in out:
            uniq[(r["port"], r["pid"])] = r
        return {"ok": True, "ports": sorted(uniq.values(), key=lambda x: x["port"]), "source":"psutil"}
    except Exception:
        pass

    # fallback netstat
    try:
        if platform.system().lower().startswith("win"):
            cmd = ["netstat", "-ano"]
        else:
            cmd = ["sh", "-lc", "netstat -tulnp | cat"]
        txt = subprocess.check_output(cmd, text=True, stderr=subprocess.STDOUT, timeout=5)
        ports = []
        for line in txt.splitlines():
            if "LISTEN" in line or "Ascolto" in line or "LISTENING" in line:
                parts = line.split()
                for p in parts:
                    if ":" in p:
                        try:
                            ports.append(int(p.rsplit(":",1)[-1]))
                        except Exception:
                            pass
        ports = sorted(set(ports))
        return {"ok": True, "ports": [{"port": p} for p in ports], "source":"netstat"}
    except Exception as e:
        return {"ok": False, "error": str(e)}

@app.route("/ports")
def ports():
    return jsonify(list_ports())

if __name__ == "__main__":
    default = os.path.join(ICON_FOLDER, "default.png")
    if not os.path.exists(default):
        Image.new("RGBA", (128,128), (230,230,230,255)).save(default, "PNG")
    app.run(host="0.0.0.0", port=4000, debug=False)

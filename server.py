"""
Agent Gmail — Serveur déploiement Render
Protégé par mot de passe + Groq gratuit
"""

import os
import requests
import pickle
import base64
import json
import hashlib
from email.mime.text import MIMEText
from flask import Flask, request, jsonify, send_from_directory, session, redirect, url_for
from flask_cors import CORS
from dotenv import load_dotenv
from google_auth_oauthlib.flow import InstalledAppFlow, Flow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build

load_dotenv()

app = Flask(__name__, static_folder=".")
app.secret_key = os.getenv("SECRET_KEY", "change-this-secret-key-in-env")
CORS(app)

GROQ_API_KEY     = os.getenv("GROQ_API_KEY")
GROQ_MODEL       = "llama-3.1-8b-instant"
GROQ_URL         = "https://api.groq.com/openai/v1/chat/completions"
TOKEN_FILE       = "token.pickle"
CREDENTIALS_FILE = "credentials.json"

# Mot de passe hashé (SHA256 du mot de passe en clair)
APP_PASSWORD_HASH = os.getenv("APP_PASSWORD_HASH", "")

SCOPES = [
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/calendar",
]


def check_auth():
    """Vérifie si l'utilisateur est connecté."""
    return session.get("authenticated") == True


def hash_password(pwd):
    return hashlib.sha256(pwd.encode()).hexdigest()


def get_creds():
    creds = None
    token_data = os.getenv("GOOGLE_TOKEN")
    if token_data:
        import io
        creds = pickle.loads(base64.b64decode(token_data))
    elif os.path.exists(TOKEN_FILE):
        with open(TOKEN_FILE, "rb") as f:
            creds = pickle.load(f)
    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
        _save_creds(creds)
    return creds


def _save_creds(creds):
    with open(TOKEN_FILE, "wb") as f:
        pickle.dump(creds, f)


def gmail():
    return build("gmail", "v1", credentials=get_creds())


def calendar():
    return build("calendar", "v3", credentials=get_creds())


def require_google():
    creds = get_creds()
    return creds is not None and creds.valid


def call_groq(system, messages):
    if not GROQ_API_KEY:
        raise Exception("Cle Groq manquante (GROQ_API_KEY)")
    msgs = [{"role": "system", "content": system}]
    for m in messages:
        msgs.append({"role": m["role"], "content": m["content"]})
    r = requests.post(GROQ_URL,
        headers={"Authorization": "Bearer " + GROQ_API_KEY, "Content-Type": "application/json"},
        json={"model": GROQ_MODEL, "messages": msgs, "max_tokens": 1000, "temperature": 0.7},
        timeout=60)
    d = r.json()
    if not r.ok:
        raise Exception("Erreur Groq : " + d.get("error", {}).get("message", str(r.status_code)))
    return d["choices"][0]["message"]["content"]


def get_msg_meta(service, msg_id):
    m = service.users().messages().get(userId="me", id=msg_id, format="metadata",
        metadataHeaders=["From", "Subject", "Date"]).execute()
    h = {x["name"]: x["value"] for x in m["payload"]["headers"]}
    labels = m.get("labelIds", [])
    return {
        "id": msg_id, "threadId": m.get("threadId", ""),
        "from": h.get("From", ""), "subject": h.get("Subject", "(sans objet)"),
        "date": h.get("Date", ""), "preview": m.get("snippet", "")[:150],
        "unread": "UNREAD" in labels, "starred": "STARRED" in labels,
    }


def search_all(service, query, max_total=5000):
    ids = []
    page_token = None
    while len(ids) < max_total:
        params = {"userId": "me", "q": query, "maxResults": min(500, max_total - len(ids))}
        if page_token:
            params["pageToken"] = page_token
        res = service.users().messages().list(**params).execute()
        for msg in res.get("messages", []):
            ids.append(msg["id"])
        page_token = res.get("nextPageToken")
        if not page_token:
            break
    return ids


# ── PAGE DE LOGIN ──────────────────────────────────────────────────
LOGIN_HTML = """<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0">
<meta name="theme-color" content="#f59e0b">
<title>Agent Gmail — Connexion</title>
<style>
*{box-sizing:border-box;margin:0;padding:0;}
body{background:#07070d;color:#e2e8f0;font-family:'Segoe UI',sans-serif;
  display:flex;align-items:center;justify-content:center;min-height:100vh;padding:20px;}
.box{background:#0e0e17;border:1px solid #222235;border-radius:12px;
  padding:36px 28px;width:100%;max-width:360px;text-align:center;}
.icon{font-size:48px;margin-bottom:16px;}
h1{font-size:20px;font-weight:700;margin-bottom:6px;}
p{font-size:12px;color:#6b7280;margin-bottom:24px;}
input{width:100%;background:#07070d;border:1px solid #222235;border-radius:8px;
  color:#e2e8f0;font-size:16px;padding:12px 16px;outline:none;margin-bottom:12px;
  transition:border-color .2s;}
input:focus{border-color:#6b4500;}
.btn{width:100%;background:#f59e0b;color:#000;border:none;border-radius:8px;
  font-weight:700;font-size:14px;padding:13px;cursor:pointer;letter-spacing:.5px;}
.btn:active{background:#fbbf24;}
.err{color:#f87171;font-size:12px;margin-top:8px;}
</style>
</head>
<body>
<div class="box">
  <div class="icon">📧</div>
  <h1>Agent Gmail</h1>
  <p>Entre ton mot de passe pour accéder à ton assistant</p>
  <form method="POST" action="/login">
    <input type="password" name="password" placeholder="Mot de passe" autofocus>
    {error}
    <button type="submit" class="btn">Connexion</button>
  </form>
</div>
</body>
</html>"""


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        pwd = request.form.get("password", "")
        if APP_PASSWORD_HASH and hash_password(pwd) == APP_PASSWORD_HASH:
            session["authenticated"] = True
            return redirect("/")
        elif not APP_PASSWORD_HASH and pwd == os.getenv("APP_PASSWORD", ""):
            session["authenticated"] = True
            return redirect("/")
        return LOGIN_HTML.replace("{error}", '<div class="err">❌ Mot de passe incorrect</div>')
    return LOGIN_HTML.replace("{error}", "")


@app.route("/logout")
def logout():
    session.clear()
    return redirect("/login")


# ── ROUTES PRINCIPALES ─────────────────────────────────────────────
@app.route("/")
def index():
    if not check_auth():
        return redirect("/login")
    return send_from_directory(".", "index.html")


@app.route("/manifest.json")
def manifest():
    return send_from_directory(".", "manifest.json")


@app.route("/sw.js")
def sw():
    return send_from_directory(".", "sw.js")


@app.route("/icons/<path:filename>")
def icons(filename):
    return send_from_directory("icons", filename)


@app.route("/health")
def health():
    creds = get_creds()
    return jsonify({
        "status": "ok",
        "api_key_configured": bool(GROQ_API_KEY),
        "google_connected": creds is not None and creds.valid,
        "authenticated": check_auth()
    })


@app.route("/auth/google")
def auth_google():
    if not check_auth():
        return redirect("/login")
    if not os.path.exists(CREDENTIALS_FILE):
        return "credentials.json introuvable", 400
    flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_FILE, SCOPES)
    creds = flow.run_local_server(port=0, prompt="consent")
    _save_creds(creds)
    return "<html><body style='background:#07070d;color:#10b981;display:flex;align-items:center;justify-content:center;height:100vh;font-family:monospace;margin:0'><div style='text-align:center'><div style='font-size:48px'>&#10003;</div><div style='font-size:20px;margin-top:12px'>Google connecte !</div></div></body></html>"


# ── PROXY IA ───────────────────────────────────────────────────────
@app.route("/api/chat", methods=["POST"])
def chat():
    if not check_auth():
        return jsonify({"error": {"message": "Non autorise"}}), 401
    payload = request.get_json()
    if not payload:
        return jsonify({"error": {"message": "Requete invalide"}}), 400
    try:
        text = call_groq(
            payload.get("system", "Tu es un assistant. Reponds en francais."),
            payload.get("messages", [])
        )
        return jsonify({"content": [{"type": "text", "text": text}], "model": GROQ_MODEL, "stop_reason": "end_turn"})
    except Exception as e:
        print("ERREUR GROQ:", str(e))
        return jsonify({"error": {"message": str(e)}}), 500


# ── GMAIL ──────────────────────────────────────────────────────────
@app.route("/api/gmail/list", methods=["GET"])
def gmail_list():
    if not check_auth() or not require_google():
        return jsonify({"error": "Non autorise"}), 401
    count = int(request.args.get("count", 20))
    query = request.args.get("q", "")
    svc = gmail()
    params = {"userId": "me", "maxResults": count}
    if query:
        params["q"] = query
    res = svc.users().messages().list(**params).execute()
    emails = [get_msg_meta(svc, m["id"]) for m in res.get("messages", [])]
    return jsonify({"emails": emails, "total": res.get("resultSizeEstimate", len(emails))})


@app.route("/api/gmail/delete", methods=["POST"])
def gmail_delete():
    if not check_auth() or not require_google():
        return jsonify({"error": "Non autorise"}), 401
    data = request.get_json()
    ids = data.get("ids", [])
    svc = gmail()
    if len(ids) > 1:
        for i in range(0, len(ids), 1000):
            svc.users().messages().batchDelete(userId="me", body={"ids": ids[i:i+1000]}).execute()
    elif len(ids) == 1:
        svc.users().messages().trash(userId="me", id=ids[0]).execute()
    return jsonify({"deleted": len(ids), "message": str(len(ids)) + " email(s) supprimes."})


@app.route("/api/gmail/delete-query", methods=["POST"])
def gmail_delete_query():
    if not check_auth() or not require_google():
        return jsonify({"error": "Non autorise"}), 401
    data = request.get_json()
    query = data.get("query", "")
    preview = data.get("preview_only", False)
    if not query:
        return jsonify({"error": "Requete manquante"}), 400
    svc = gmail()
    ids = search_all(svc, query, max_total=5000)
    if preview:
        prev = []
        for mid in ids[:10]:
            try:
                prev.append(get_msg_meta(svc, mid))
            except:
                pass
        return jsonify({"total": len(ids), "preview": prev, "query": query})
    deleted = 0
    for i in range(0, len(ids), 1000):
        batch = ids[i:i+1000]
        try:
            svc.users().messages().batchDelete(userId="me", body={"ids": batch}).execute()
            deleted += len(batch)
        except:
            for mid in batch:
                try:
                    svc.users().messages().trash(userId="me", id=mid).execute()
                    deleted += 1
                except:
                    pass
    return jsonify({"deleted": deleted, "message": str(deleted) + " email(s) supprimes."})


@app.route("/api/gmail/mark", methods=["POST"])
def gmail_mark():
    if not check_auth() or not require_google():
        return jsonify({"error": "Non autorise"}), 401
    data = request.get_json()
    ids = data.get("ids", [])
    action = data.get("action", "read")
    svc = gmail()
    bodies = {
        "read":    {"removeLabelIds": ["UNREAD"]},
        "unread":  {"addLabelIds": ["UNREAD"]},
        "star":    {"addLabelIds": ["STARRED"]},
        "unstar":  {"removeLabelIds": ["STARRED"]},
        "archive": {"removeLabelIds": ["INBOX"]},
    }
    labels_map = {"read":"lus","unread":"non lus","star":"en favoris","unstar":"retirés des favoris","archive":"archivés"}
    body = bodies.get(action, {"removeLabelIds": ["UNREAD"]})
    svc.users().messages().batchModify(userId="me", body={"ids": ids, **body}).execute()
    return jsonify({"modified": len(ids), "message": str(len(ids)) + " email(s) " + labels_map.get(action, action) + "."})


@app.route("/api/gmail/send", methods=["POST"])
def gmail_send():
    if not check_auth() or not require_google():
        return jsonify({"error": "Non autorise"}), 401
    data = request.get_json()
    msg = MIMEText(data["body"])
    msg["to"] = data["to"]
    msg["subject"] = data["subject"]
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    gmail().users().messages().send(userId="me", body={"raw": raw}).execute()
    return jsonify({"sent": True, "message": "Email envoye a " + data["to"]})


@app.route("/api/gmail/stats", methods=["GET"])
def gmail_stats():
    if not check_auth() or not require_google():
        return jsonify({"error": "Non autorise"}), 401
    svc = gmail()
    profile = svc.users().getProfile(userId="me").execute()
    unread = svc.users().messages().list(userId="me", q="is:unread", maxResults=1).execute()
    return jsonify({
        "email": profile.get("emailAddress", ""),
        "total_messages": profile.get("messagesTotal", 0),
        "total_threads": profile.get("threadsTotal", 0),
        "unread_estimate": unread.get("resultSizeEstimate", 0),
    })


# ── CALENDAR ───────────────────────────────────────────────────────
@app.route("/api/calendar/create", methods=["POST"])
def calendar_create():
    if not check_auth() or not require_google():
        return jsonify({"error": "Non autorise"}), 401
    data = request.get_json()
    event = {
        "summary": data["title"],
        "start": {"dateTime": data["start"], "timeZone": "Europe/Paris"},
        "end":   {"dateTime": data["end"],   "timeZone": "Europe/Paris"},
    }
    if data.get("description"):
        event["description"] = data["description"]
    created = calendar().events().insert(calendarId="primary", body=event).execute()
    return jsonify({"created": True, "message": "Evenement cree avec succes."})


if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    print("\n" + "="*50)
    print("  Agent Gmail — Deploiement")
    print("="*50)
    print("  -> http://localhost:" + str(port))
    print("  -> Groq : " + ("OK" if GROQ_API_KEY else "MANQUANTE"))
    print("  -> Password : " + ("Configure" if APP_PASSWORD_HASH else "Non configure"))
    print("="*50 + "\n")
    app.run(host="0.0.0.0", port=port)

import os
import json
import re
import base64
import sqlite3
import uuid
import smtplib
import urllib.error
import urllib.request
from io import BytesIO
from email.message import EmailMessage
from difflib import SequenceMatcher
from datetime import datetime
from flask import Flask, abort, jsonify, render_template, request, redirect, url_for, session, flash, send_file, send_from_directory
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY") or os.urandom(32)

app.config["DATABASE"] = os.environ.get(
    "DATABASE_PATH", os.path.join(app.instance_path, "findit.sqlite3")
)
app.config["UPLOAD_FOLDER"] = os.environ.get(
    "UPLOAD_FOLDER", os.path.join("static", "uploads")
)
app.config["PRIVATE_UPLOAD_FOLDER"] = os.environ.get(
    "PRIVATE_UPLOAD_FOLDER", os.path.join(app.instance_path, "private_uploads")
)
app.config["MAX_CONTENT_LENGTH"] = 5 * 1024 * 1024
app.config["LOST_FOUND_OFFICE"] = os.environ.get("LOST_FOUND_OFFICE", "")
app.config["OPENAI_API_KEY"] = os.environ.get("OPENAI_API_KEY", "")
app.config["OPENAI_MODEL"] = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
app.config["AI_IMAGE_MATCHING"] = os.environ.get("AI_IMAGE_MATCHING", "true").lower() == "true"
app.config["MAIL_SERVER"] = os.environ.get("MAIL_SERVER", "")
app.config["MAIL_PORT"] = int(os.environ.get("MAIL_PORT", "587"))
app.config["MAIL_USERNAME"] = os.environ.get("MAIL_USERNAME", "")
app.config["MAIL_PASSWORD"] = os.environ.get("MAIL_PASSWORD", "")
app.config["MAIL_SENDER"] = os.environ.get("MAIL_SENDER", "")
app.config["MAIL_USE_TLS"] = os.environ.get("MAIL_USE_TLS", "true").lower() == "true"
app.config["PUBLIC_BASE_URL"] = os.environ.get("PUBLIC_BASE_URL", "").rstrip("/")

os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)
os.makedirs(app.config["PRIVATE_UPLOAD_FOLDER"], exist_ok=True)


class SQLiteCursor:
    def __init__(self, cursor):
        self._cursor = cursor

    def execute(self, statement, parameters=()):
        statement = re.sub(
            r"DATE_SUB\(NOW\(\),\s*INTERVAL\s+1\s+DAY\)",
            "datetime('now', '-1 day')",
            statement,
            flags=re.IGNORECASE,
        )
        statement = statement.replace("NOW()", "CURRENT_TIMESTAMP").replace("%s", "?")
        self._cursor.execute(statement, parameters)
        return self

    def fetchone(self):
        return self._cursor.fetchone()

    def fetchall(self):
        return self._cursor.fetchall()

    @property
    def lastrowid(self):
        return self._cursor.lastrowid

    def close(self):
        self._cursor.close()


class SQLiteConnection:
    def __init__(self, connection):
        self._connection = connection

    def cursor(self):
        return SQLiteCursor(self._connection.cursor())

    def commit(self):
        self._connection.commit()

    def close(self):
        self._connection.close()


class SQLiteDatabase:
    @property
    def connection(self):
        from flask import g

        if "findit_connection" not in g:
            connection = sqlite3.connect(app.config["DATABASE"])
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            g.findit_connection = SQLiteConnection(connection)
        return g.findit_connection


mysql = SQLiteDatabase()


CHATBOT_TRANSLATIONS = {
    "en": {
        "claim-search": "Which found item would you like to claim? Tell me its name or type, and include a detail like its color or brand.",
        "claim-search-empty": "Tell me the item's name or type, plus a detail like its color or brand.",
        "claim-search-none": "I couldn't find a matching found report. Try another item name or type, or browse found items and tell me which one.",
        "claim-search-found": "Which of these found reports is the item you'd like to claim?",
        "claim-select-invalid": "Please select one of the found items listed above.",
        "claim-unavailable": "That item is no longer accepting claims. Please choose another found report.",
        "claim-login": "Please sign in before starting a claim. After signing in, come back to this item.",
        "claim-own": "You can't claim your own found report. Please choose another item.",
        "claim-proof": "You selected {item}. What specific details prove it is yours, such as its brand or model, color, unique marks, or contents? Then I'll ask where and when you lost it. Staff will review your answers privately.",
        "lost-report": "I'm sorry you lost it. Open Report Item, choose Lost, and add the item details, last-seen date, and location. Submitting a report lets the system notify you about possible matches.",
        "report-found": "To report an item you found, open Report Item, choose Found, describe the item, and enter when and where you found it. Add a clear photo if you can, then submit the report.",
        "report-lost": "To report a lost item, open Report Item, choose Lost, describe the item, and enter when and where you last had it. Add identifying details and a photo if available, then submit the report.",
        "claim-start": "Which found item would you like to claim? Tell me its name or type, and include a detail like its color or brand.",
        "office-missing": "The campus office location is not configured. Please ask Security or an administrator for the verified location.",
        "match-found": "Here are found reports that may match:",
        "match-none": "I couldn't find a matching found report yet. Try Browse Items or submit a lost report to receive match alerts.",
        "ai-missing": "AI answers aren't configured yet. Set the OPENAI_API_KEY environment variable, then restart FindIt. I can still help with item search and guided reports or claims.",
    },
    "kn": {
        "claim-search": "ನೀವು ಯಾವ ಸಿಕ್ಕಿದ ವಸ್ತುವನ್ನು ಕ್ಲೇಮ್ ಮಾಡಲು ಬಯಸುತ್ತೀರಿ? ಅದರ ಹೆಸರು ಅಥವಾ ಪ್ರಕಾರವನ್ನು ತಿಳಿಸಿ.",
        "claim-search-empty": "ವಸ್ತುವಿನ ಹೆಸರು ಅಥವಾ ಪ್ರಕಾರದ ಜೊತೆಗೆ ಬಣ್ಣ ಅಥವಾ ಬ್ರ್ಯಾಂಡ್‌ನಂತಹ ಒಂದು ವಿವರವನ್ನು ತಿಳಿಸಿ.",
        "claim-search-none": "ಹೊಂದುವ ಸಿಕ್ಕಿದ ವರದಿ ಕಾಣಿಸಲಿಲ್ಲ. ಬೇರೆ ಹೆಸರು ಅಥವಾ ಪ್ರಕಾರವನ್ನು ಪ್ರಯತ್ನಿಸಿ, ಅಥವಾ ಸಿಕ್ಕಿದ ವಸ್ತುಗಳನ್ನು ಬ್ರೌಸ್ ಮಾಡಿ.",
        "claim-search-found": "ಇವುಗಳಲ್ಲಿ ನೀವು ಕ್ಲೇಮ್ ಮಾಡಲು ಬಯಸುವ ವಸ್ತು ಯಾವುದು?",
        "claim-select-invalid": "ದಯವಿಟ್ಟು ಮೇಲಿನ ಸಿಕ್ಕಿದ ವಸ್ತುಗಳಲ್ಲಿ ಒಂದನ್ನು ಆಯ್ಕೆಮಾಡಿ.",
        "claim-unavailable": "ಆ ವಸ್ತುವಿಗೆ ಈಗ ಕ್ಲೇಮ್ ಸ್ವೀಕರಿಸಲಾಗುತ್ತಿಲ್ಲ. ಬೇರೆ ವರದಿಯನ್ನು ಆಯ್ಕೆಮಾಡಿ.",
        "claim-login": "ಕ್ಲೇಮ್ ಪ್ರಾರಂಭಿಸುವ ಮೊದಲು ಸೈನ್ ಇನ್ ಮಾಡಿ. ನಂತರ ಈ ವಸ್ತುವಿಗೆ ಹಿಂತಿರುಗಿ.",
        "claim-own": "ನಿಮ್ಮದೇ ಸಿಕ್ಕಿದ ವರದಿಯನ್ನು ನೀವು ಕ್ಲೇಮ್ ಮಾಡಲು ಸಾಧ್ಯವಿಲ್ಲ. ಬೇರೆ ವಸ್ತುವನ್ನು ಆಯ್ಕೆಮಾಡಿ.",
        "claim-proof": "{item} ಅನ್ನು ಗುರುತಿಸುವ ವಿವರಗಳನ್ನು ತಿಳಿಸಿ; ಉದಾಹರಣೆಗೆ ಬ್ರ್ಯಾಂಡ್, ಬಣ್ಣ, ವಿಶೇಷ ಗುರುತುಗಳು ಅಥವಾ ಒಳಗಿನ ವಸ್ತುಗಳು. ನಂತರ ಅದು ಎಲ್ಲಿ ಮತ್ತು ಯಾವಾಗ ಕಳೆದುಹೋಯಿತು ಎಂದು ಕೇಳುತ್ತೇನೆ.",
        "lost-report": "ವಸ್ತು ಕಳೆದುಹೋದಕ್ಕೆ ವಿಷಾದಿಸುತ್ತೇನೆ. ವರದಿ ವಸ್ತುವನ್ನು ತೆರೆಯಿರಿ, Lost ಆಯ್ಕೆಮಾಡಿ, ವಸ್ತುವಿನ ವಿವರಗಳು, ಕಳೆದುಹೋದ ದಿನಾಂಕ/ಸಮಯ ಮತ್ತು ಕೊನೆಯಾಗಿ ಇದ್ದ ಸ್ಥಳವನ್ನು ನೀಡಿ. ಸಲ್ಲಿಸಿದ ನಂತರ ಹೊಂದಾಣಿಕೆ ಸೂಚನೆಗಳು ಬರಬಹುದು.",
        "report-found": "ಸಿಕ್ಕಿದ ವಸ್ತುವನ್ನು ವರದಿ ಮಾಡಲು Report Item ತೆರೆಯಿರಿ, Found ಆಯ್ಕೆಮಾಡಿ, ವಸ್ತುವಿನ ವಿವರಗಳು, ಸಿಕ್ಕಿದ ದಿನಾಂಕ/ಸಮಯ ಮತ್ತು ಸ್ಥಳವನ್ನು ನೀಡಿ. ಸಾಧ್ಯವಾದರೆ ಸ್ಪಷ್ಟ ಫೋಟೋ ಸೇರಿಸಿ ಸಲ್ಲಿಸಿ.",
        "report-lost": "ಕಳೆದುಹೋದ ವಸ್ತುವನ್ನು ವರದಿ ಮಾಡಲು Report Item ತೆರೆಯಿರಿ, Lost ಆಯ್ಕೆಮಾಡಿ, ವಸ್ತುವಿನ ವಿವರಗಳು, ಕಳೆದುಹೋದ ದಿನಾಂಕ/ಸಮಯ ಮತ್ತು ಕೊನೆಯಾಗಿ ಇದ್ದ ಸ್ಥಳವನ್ನು ನೀಡಿ. ಸಾಧ್ಯವಾದರೆ ಫೋಟೋ ಸೇರಿಸಿ ಸಲ್ಲಿಸಿ.",
        "claim-start": "ಯಾವ ಸಿಕ್ಕಿದ ವಸ್ತುವನ್ನು ಕ್ಲೇಮ್ ಮಾಡಲು ಬಯಸುತ್ತೀರಿ? ಅದರ ಹೆಸರು ಅಥವಾ ಪ್ರಕಾರ ಮತ್ತು ಬಣ್ಣ ಅಥವಾ ಬ್ರ್ಯಾಂಡ್‌ನಂತಹ ವಿವರವನ್ನು ತಿಳಿಸಿ.",
        "office-missing": "ಕ್ಯಾಂಪಸ್ ಕಚೇರಿಯ ಸ್ಥಳವನ್ನು ಇನ್ನೂ ಹೊಂದಿಸಲಾಗಿಲ್ಲ. ದೃಢೀಕೃತ ಸ್ಥಳಕ್ಕಾಗಿ ಭದ್ರತಾ ಸಿಬ್ಬಂದಿ ಅಥವಾ ನಿರ್ವಾಹಕರನ್ನು ಸಂಪರ್ಕಿಸಿ.",
        "match-found": "ಹೊಂದಾಣಿಕೆಯಾಗಬಹುದಾದ ಸಿಕ್ಕಿದ ವರದಿಗಳು ಇಲ್ಲಿವೆ:",
        "match-none": "ಹೊಂದುವ ಸಿಕ್ಕಿದ ವರದಿ ಸಿಗಲಿಲ್ಲ. ಸಿಕ್ಕಿದ ವಸ್ತುಗಳನ್ನು ಬ್ರೌಸ್ ಮಾಡಿ ಅಥವಾ ಕಳೆದುಹೋದ ವರದಿ ಸಲ್ಲಿಸಿ.",
        "ai-missing": "AI ಉತ್ತರಗಳನ್ನು ಇನ್ನೂ ಹೊಂದಿಸಲಾಗಿಲ್ಲ. OPENAI_API_KEY ಹೊಂದಿಸಿ FindIt ಅನ್ನು ಮರುಪ್ರಾರಂಭಿಸಿ. ವಸ್ತು ಹುಡುಕಾಟ ಮತ್ತು ವರದಿ/ಕ್ಲೇಮ್ ಮಾರ್ಗದರ್ಶನ ಇನ್ನೂ ಲಭ್ಯವಿದೆ.",
    },
    "hi": {
        "claim-search": "आप किस मिली हुई वस्तु पर दावा करना चाहते हैं? उसका नाम या प्रकार बताइए।",
        "claim-search-empty": "वस्तु का नाम या प्रकार और रंग या ब्रांड जैसी कोई पहचान बताइए।",
        "claim-search-none": "मिलती-जुलती मिली हुई रिपोर्ट नहीं मिली। दूसरा नाम या प्रकार आज़माएँ, या मिली हुई वस्तुएँ देखें।",
        "claim-search-found": "इनमें से आप किस वस्तु पर दावा करना चाहते हैं?",
        "claim-select-invalid": "कृपया ऊपर दिखाई गई मिली हुई वस्तुओं में से एक चुनें।",
        "claim-unavailable": "इस वस्तु पर अभी दावा स्वीकार नहीं किया जा रहा है। दूसरी रिपोर्ट चुनें।",
        "claim-login": "दावा शुरू करने से पहले साइन इन करें। फिर इस वस्तु पर वापस आएँ।",
        "claim-own": "आप अपनी ही मिली हुई रिपोर्ट पर दावा नहीं कर सकते। कृपया दूसरी वस्तु चुनें।",
        "claim-proof": "{item} की पहचान के लिए खास विवरण बताएँ, जैसे ब्रांड, रंग, विशिष्ट निशान या अंदर की वस्तुएँ। फिर मैं पूछूँगा कि यह कहाँ और कब खोई थी।",
        "lost-report": "आपकी वस्तु खोने का दुख है। Report Item खोलें, Lost चुनें, वस्तु का विवरण, खोने की तारीख/समय और आखिरी बार देखी गई जगह भरें। रिपोर्ट जमा करने के बाद संभावित मिलान की सूचनाएँ मिल सकती हैं।",
        "report-found": "मिली हुई वस्तु की रिपोर्ट करने के लिए Report Item खोलें, Found चुनें, वस्तु का विवरण, मिलने की तारीख/समय और जगह भरें। संभव हो तो साफ़ फोटो जोड़कर जमा करें।",
        "report-lost": "खोई हुई वस्तु की रिपोर्ट करने के लिए Report Item खोलें, Lost चुनें, वस्तु का विवरण, खोने की तारीख/समय और आखिरी बार देखी गई जगह भरें। संभव हो तो फोटो जोड़कर जमा करें।",
        "claim-start": "आप किस मिली हुई वस्तु पर दावा करना चाहते हैं? उसका नाम या प्रकार और रंग या ब्रांड जैसी पहचान बताएँ।",
        "office-missing": "कैंपस कार्यालय का स्थान अभी सेट नहीं है। सही स्थान के लिए सुरक्षा कर्मी या प्रशासक से पूछें।",
        "match-found": "ये मिली हुई रिपोर्टें आपकी वस्तु से मेल खा सकती हैं:",
        "match-none": "मिलती-जुलती मिली हुई रिपोर्ट नहीं मिली। मिली हुई वस्तुएँ देखें या खोई हुई वस्तु की रिपोर्ट जमा करें।",
        "ai-missing": "AI जवाब अभी सेट नहीं हैं। OPENAI_API_KEY सेट करके FindIt को फिर शुरू करें। वस्तु खोज और रिपोर्ट/दावे का मार्गदर्शन फिर भी उपलब्ध है।",
    },
}


def chatbot_text(key, language="en", **values):
    catalog = CHATBOT_TRANSLATIONS.get(language, {})
    message = catalog.get(key, key)
    return message.format(**values)


def close_database_connection(_error=None):
    from flask import g

    connection = g.pop("findit_connection", None)
    if connection is not None:
        connection.close()


app.teardown_appcontext(close_database_connection)


def initialize_database():
    database_directory = os.path.dirname(os.path.abspath(app.config["DATABASE"]))
    os.makedirs(database_directory, exist_ok=True)
    connection = sqlite3.connect(app.config["DATABASE"])
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        schema_path = os.path.join(os.path.dirname(__file__), "database.sql")
        with open(schema_path, encoding="utf-8") as schema_file:
            connection.executescript(schema_file.read())
    finally:
        connection.close()


def ensure_claim_image_column():
    cur = None
    try:
        cur = mysql.connection.cursor()
        for table, column, definition in (
            ("items", "brand", "VARCHAR(100) NULL"),
            ("items", "color", "VARCHAR(60) NULL"),
            ("items", "is_anonymous", "BOOLEAN NOT NULL DEFAULT FALSE"),
            ("items", "qr_token", "CHAR(36) NULL"),
            ("items", "event_time", "TIME NULL"),
            ("claims", "proof_image", "VARCHAR(255) NULL"),
            ("claims", "proof_file", "VARCHAR(255) NULL"),
            ("claims", "handover_at", "DATETIME NULL"),
            ("users", "phone", "VARCHAR(20) NULL"),
            ("users", "student_id", "VARCHAR(40) NULL"),
        ):
            cur.execute(f"PRAGMA table_info({table})")
            if not any(row[1] == column for row in cur.fetchall()):
                cur.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
        cur.execute("SELECT name FROM sqlite_master WHERE type='index' AND name='uq_users_phone'")
        if cur.fetchone() is None:
            cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_users_phone ON users(phone)")
        cur.execute("SELECT name FROM sqlite_master WHERE type='index' AND name='uq_users_student_id'")
        if cur.fetchone() is None:
            cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_users_student_id ON users(student_id)")
        cur.execute("SELECT proof_image FROM claims WHERE proof_image IS NOT NULL")
        for (legacy_name,) in cur.fetchall():
            safe_name = os.path.basename(legacy_name)
            old_path = os.path.join(app.config["UPLOAD_FOLDER"], safe_name)
            private_path = os.path.join(app.config["PRIVATE_UPLOAD_FOLDER"], safe_name)
            if os.path.isfile(old_path) and not os.path.exists(private_path):
                os.replace(old_path, private_path)
        cur.execute("""CREATE TABLE IF NOT EXISTS audit_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            actor_id INT NULL,
            action VARCHAR(80) NOT NULL,
            entity_type VARCHAR(40) NOT NULL,
            entity_id INT NULL,
            details TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (actor_id) REFERENCES users(id) ON DELETE SET NULL
        )""")
        cur.execute("""CREATE TABLE IF NOT EXISTS handover_receipts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            item_id INT NOT NULL UNIQUE,
            claim_id INT NOT NULL,
            receiver_id INT NOT NULL,
            receiver_name VARCHAR(100) NOT NULL,
            receiver_confirmation BOOLEAN NOT NULL DEFAULT FALSE,
            authorized_staff_id INT NOT NULL,
            authorized_staff_name VARCHAR(100) NOT NULL,
            confirmed_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (item_id) REFERENCES items(id) ON DELETE CASCADE,
            FOREIGN KEY (claim_id) REFERENCES claims(id) ON DELETE CASCADE,
            FOREIGN KEY (receiver_id) REFERENCES users(id) ON DELETE CASCADE,
            FOREIGN KEY (authorized_staff_id) REFERENCES users(id) ON DELETE CASCADE
        )""")
        cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_items_qr_token ON items(qr_token)")
        mysql.connection.commit()
    except Exception:
        app.logger.exception("FindIt database feature migration did not complete")
    finally:
        if cur:
            cur.close()


initialize_database()
with app.app_context():
    ensure_claim_image_column()


def logged_in():
    return "user_id" in session

def has_role(*roles):
    return session.get("role") in roles

def is_admin():
    return has_role("admin")

def log_audit(cur, action, entity_type, entity_id=None, details=None):
    cur.execute(
        "INSERT INTO audit_logs(actor_id,action,entity_type,entity_id,details) VALUES(%s,%s,%s,%s,%s)",
        (session.get("user_id"), action, entity_type, entity_id,
         json.dumps(details or {}, ensure_ascii=True)),
    )

def send_email_notification(recipient, subject, body):
    if not recipient or not app.config["MAIL_SERVER"] or not app.config["MAIL_SENDER"]:
        return
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = app.config["MAIL_SENDER"]
    message["To"] = recipient
    message.set_content(body)
    try:
        with smtplib.SMTP(app.config["MAIL_SERVER"], app.config["MAIL_PORT"], timeout=8) as smtp:
            if app.config["MAIL_USE_TLS"]:
                smtp.starttls()
            if app.config["MAIL_USERNAME"]:
                smtp.login(app.config["MAIL_USERNAME"], app.config["MAIL_PASSWORD"])
            smtp.send_message(message)
    except (OSError, smtplib.SMTPException) as error:
        app.logger.warning("Unable to send FindIt notification email: %s", error)


def generate_chatbot_answer(message, conversation_history=None, language="en"):
    api_key = app.config["OPENAI_API_KEY"]
    if not api_key:
        return None
    language_name = {"en": "English", "kn": "Kannada", "hi": "Hindi"}.get(language, "English")

    system_prompt = (
        "You are FindIt's friendly AI assistant for a campus lost-and-found platform. "
        "Answer the user's question clearly and conversationally. You may answer general "
        "questions, but never invent campus-specific facts, office locations, item availability, "
        "claim decisions, or app features. For a lost-item report, guide the user to Report Item, "
        "choose Lost, and provide the item description, date/time lost, and last-known location. "
        "For a found-item report, guide the user to Report Item, choose Found, and provide the "
        "item description, date/time and place found; suggest adding a clear photo. For a claim, "
        "guide the user to select a matching found report, describe identifying details, explain "
        "where and when they lost the item, then submit it for staff review. Never claim that "
        "a report or claim was submitted unless the app confirms it. Ask a brief follow-up if "
        "the user's request is unclear. Do not request passwords or payment information. "
        f"Respond in {language_name}; if the user writes in Kannada or Hindi while English is selected, "
        "match the language of their latest message."
    )
    messages = [{"role": "system", "content": system_prompt}]
    for entry in conversation_history or []:
        if (
            isinstance(entry, dict)
            and entry.get("role") in ("user", "assistant")
            and isinstance(entry.get("content"), str)
        ):
            messages.append({
                "role": entry["role"],
                "content": entry["content"][:1200],
            })
    messages.append({"role": "user", "content": message})
    payload = json.dumps({
        "model": app.config["OPENAI_MODEL"],
        "messages": messages,
        "max_completion_tokens": 450,
    }).encode("utf-8")
    api_request = urllib.request.Request(
        "https://api.openai.com/v1/chat/completions",
        data=payload,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(api_request, timeout=15) as response:
        result = json.loads(response.read().decode("utf-8"))

    answer = result["choices"][0]["message"]["content"]
    if not isinstance(answer, str) or not answer.strip():
        raise ValueError("OpenAI returned an empty chatbot response")
    return answer.strip()


def image_as_data_url(image):
    from PIL import Image, ImageOps

    image.seek(0)
    with Image.open(image) as source:
        source = ImageOps.exif_transpose(source).convert("RGB")
        source.thumbnail((512, 512))
        output = BytesIO()
        source.save(output, format="JPEG", quality=78, optimize=True)
    image.seek(0)
    return "data:image/jpeg;base64," + base64.b64encode(output.getvalue()).decode("ascii")


def ai_image_similarity(upload, candidates):
    if not app.config["OPENAI_API_KEY"] or not candidates:
        return {}
    try:
        content = [{
            "type": "text",
            "text": (
                "Image 1 is the user's uploaded reference. The next images are candidate found-item photos "
                "in order. Compare visual object identity, color, shape, and distinctive visible details. "
                "Return only JSON in this schema: "
                '{"matches":[{"index":1,"score":0}]}. Include each candidate index once, with integer '
                "similarity scores from 0 to 100. Do not infer identity from unrelated background."
            ),
        }, {
            "type": "image_url",
            "image_url": {"url": image_as_data_url(upload.stream), "detail": "low"},
        }]
        candidate_indices = []
        for candidate in candidates:
            filename = candidate.get("image")
            if not filename:
                continue
            path = os.path.join(app.config["UPLOAD_FOLDER"], os.path.basename(filename))
            if not os.path.isfile(path):
                continue
            with open(path, "rb") as image_file:
                image_url = image_as_data_url(image_file)
            index = len(candidate_indices) + 1
            candidate_indices.append(candidate["id"])
            content.append({"type": "text", "text": f"Candidate image {index}."})
            content.append({
                "type": "image_url",
                "image_url": {"url": image_url, "detail": "low"},
            })
        if not candidate_indices:
            return {}
        request_payload = json.dumps({
            "model": app.config["OPENAI_MODEL"],
            "messages": [{
                "role": "user",
                "content": content,
            }],
            "response_format": {"type": "json_object"},
            "max_completion_tokens": 300,
        }).encode("utf-8")
        api_request = urllib.request.Request(
            "https://api.openai.com/v1/chat/completions",
            data=request_payload,
            headers={
                "Authorization": f"Bearer {app.config['OPENAI_API_KEY']}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with urllib.request.urlopen(api_request, timeout=20) as response:
            result = json.loads(response.read().decode("utf-8"))
        raw_scores = json.loads(result["choices"][0]["message"]["content"])["matches"]
        scores = {}
        for match in raw_scores:
            index = match.get("index")
            score = match.get("score")
            if (
                isinstance(index, int) and 1 <= index <= len(candidate_indices)
                and isinstance(score, (int, float)) and 0 <= score <= 100
            ):
                scores[candidate_indices[index - 1]] = round(score / 100, 4)
        return scores
    except (OSError, urllib.error.URLError, json.JSONDecodeError, KeyError, TypeError, ValueError) as error:
        app.logger.warning("AI image matching unavailable; using perceptual image similarity: %s", error)
        return {}


def photo_similarity(upload, stored_filename):
    if not upload or not upload.filename or not stored_filename:
        return None
    path = os.path.join(app.config["UPLOAD_FOLDER"], os.path.basename(stored_filename))
    if not os.path.isfile(path):
        return None
    try:
        import imagehash
        from PIL import Image
        upload.stream.seek(0)
        uploaded_hash = imagehash.phash(Image.open(upload.stream))
        upload.stream.seek(0)
        stored_hash = imagehash.phash(Image.open(path))
        return max(0, 1 - (uploaded_hash - stored_hash) / 64)
    except (ImportError, OSError, ValueError):
        upload.stream.seek(0)
        return None

def rank_item_matches(cur, form_data, statuses, limit=5, image_upload=None):
    cur.execute(
        """SELECT id,item_name,category,description,location,event_date,status,brand,color,image,user_id,is_anonymous,event_time
           FROM items WHERE status IN (%s) ORDER BY created_at DESC LIMIT 300"""
        % ",".join(["%s"] * len(statuses)),
        tuple(statuses),
    )
    rows = cur.fetchall()
    report_date = datetime.strptime(form_data["event_date"], "%Y-%m-%d").date()
    query_tokens = set(form_data["description"].lower().split())
    ranked = []
    for row in rows:
        name_score = SequenceMatcher(None, form_data["item_name"].lower(), row[1].lower()).ratio()
        location_score = SequenceMatcher(None, form_data["location"].lower(), row[4].lower()).ratio()
        candidate_tokens = set(row[3].lower().split())
        description_score = len(query_tokens & candidate_tokens) / max(1, len(query_tokens | candidate_tokens))
        try:
            candidate_date = row[5] if hasattr(row[5], "year") else datetime.strptime(str(row[5]), "%Y-%m-%d").date()
            date_score = max(0, 1 - abs((report_date - candidate_date).days) / 30)
        except (TypeError, ValueError):
            date_score = 0
        time_score = 0
        if form_data.get("event_time") and row[12]:
            try:
                report_time = datetime.strptime(form_data["event_time"], "%H:%M").time()
                candidate_time = row[12] if hasattr(row[12], "hour") else datetime.strptime(str(row[12]), "%H:%M:%S").time()
                minute_difference = abs((report_time.hour * 60 + report_time.minute) - (candidate_time.hour * 60 + candidate_time.minute))
                time_score = max(0, 1 - minute_difference / 720)
            except (TypeError, ValueError):
                time_score = 0
        features = [(name_score, .28), (location_score, .16), (description_score, .14),
                    (date_score, .10), (time_score, .08), (1.0 if form_data["category"] == row[2] else 0, .18)]
        if form_data.get("brand"):
            features.append((1.0 if form_data["brand"].lower() == (row[7] or "").lower() else 0, .03))
        if form_data.get("color"):
            features.append((1.0 if form_data["color"].lower() == (row[8] or "").lower() else 0, .03))
        image_score = photo_similarity(image_upload, row[9])
        if image_score is not None:
            features.append((image_score, .25))
        score = sum(value * weight for value, weight in features) / sum(weight for _, weight in features)
        ranked.append({
            "id": row[0], "name": row[1], "category": row[2], "location": row[4],
            "status": row[6], "image": row[9], "user_id": row[10],
            "reporter": "Anonymous" if row[11] else None,
            "image_score": round(image_score * 100) if image_score is not None else None,
            "score": round(score * 100),
        })
    ranked.sort(key=lambda match: match["score"], reverse=True)
    if image_upload and app.config["OPENAI_API_KEY"] and app.config["AI_IMAGE_MATCHING"]:
        visual_scores = ai_image_similarity(image_upload, ranked[:10])
        for match in ranked:
            vision_score = visual_scores.get(match["id"])
            if vision_score is not None:
                match["image_score"] = round(vision_score * 100)
                match["match_method"] = "AI visual + metadata"
                match["score"] = round(match["score"] * 0.6 + vision_score * 100 * 0.4)
    return sorted(ranked, key=lambda match: match["score"], reverse=True)[:limit]

@app.route("/")
def index():
    cur = None
    items = []
    database_ready = True
    try:
        cur = mysql.connection.cursor()
        cur.execute("""SELECT i.*, CASE WHEN i.is_anonymous=1 THEN 'Anonymous' ELSE u.name END AS reporter
                       FROM items i JOIN users u ON i.user_id=u.id
                       WHERE i.status IN ('Lost','Found')
                       ORDER BY i.created_at DESC LIMIT 8""")
        items = cur.fetchall()
    except Exception:
        database_ready = False
        app.logger.warning("FindIt homepage is running without database-backed reports")
    finally:
        if cur:
            cur.close()
    if not database_ready:
        return """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>FindIt | Setup Required</title>
<style>body{font-family:system-ui,sans-serif;max-width:680px;margin:12vh auto;padding:24px;color:#0f172a;background:#f8fafc}main{background:white;padding:40px;border-radius:16px;box-shadow:0 12px 40px #0f172a18}h1{margin-top:0}p{color:#475569;line-height:1.6}a{display:inline-block;margin-right:16px;color:#4f46e5;font-weight:600}</style></head>
<body><main><h1>FindIt is online</h1><p>The web server is running, but MySQL is not available yet. Start MySQL and import <strong>database.sql</strong> to enable reports, accounts, and claims.</p>
<a href="/login">Log in</a><a href="/register">Register</a></main></body></html>""", 503
    try:
        return render_template("index.html", items=items)
    except Exception:
        app.logger.exception("FindIt homepage template could not be rendered")
        return "<h1>FindIt is online</h1><p>Start MySQL and import database.sql to enable the full homepage.</p>", 503

@app.route("/register", methods=["GET","POST"])
def register():
    if request.method == "POST":
        name = request.form["name"].strip()
        email = request.form["email"].strip().lower()
        phone = request.form.get("phone", "").strip()
        student_id = request.form.get("student_id", "").strip().upper()
        password = request.form["password"]
        if not name or not email or not password:
            flash("Name, email, and password are required.")
            return render_template("register.html")
        cur = mysql.connection.cursor()
        cur.execute(
            "SELECT id FROM users WHERE email=%s OR phone=%s OR student_id=%s",
            (email, phone or None, student_id or None),
        )
        if cur.fetchone():
            cur.close()
            flash("This email, phone number, or student ID is already registered.")
            return redirect(url_for("register"))
        cur.execute(
            "INSERT INTO users(name,email,phone,student_id,password,role) VALUES(%s,%s,%s,%s,%s,'user')",
            (name, email, phone or None, student_id or None, generate_password_hash(password)),
        )
        mysql.connection.commit()
        cur.close()
        flash("Registration successful. Please login with your email, mobile number, or student ID.")
        return redirect(url_for("login"))
    return render_template("register.html")

@app.route("/login", methods=["GET","POST"])
def login():
    if request.method == "POST":
        identifier = request.form.get("identifier", "").strip().lower()
        password = request.form.get("password", "")
        cur = mysql.connection.cursor()
        cur.execute(
            "SELECT id,name,email,phone,student_id,password,role FROM users WHERE email=%s OR phone=%s OR student_id=%s",
            (identifier, identifier, identifier.upper() if identifier else None),
        )
        user = cur.fetchone()
        cur.close()
        if user and check_password_hash(user[5], password):
            session["user_id"] = user[0]
            session["name"] = user[1]
            session["role"] = user[6]
            return redirect(url_for("dashboard"))
        flash("Invalid login details. Try your email, mobile number, or student ID.")
    return render_template("login.html")

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("index"))

@app.route("/report", methods=["GET","POST"])
def report():
    if not logged_in():
        return redirect(url_for("login"))
    if request.method == "POST":
        item_type=request.form.get("item_type", "Lost")
        if item_type not in ("Lost", "Found"):
            abort(400)
        form_data = {
            "item_type": item_type,
            "item_name": request.form.get("item_name", "").strip(),
            "category": request.form.get("category", "Other"),
            "description": request.form.get("description", "").strip(),
            "location": request.form.get("location", "").strip(),
            "event_date": request.form.get("event_date", ""),
            "event_time": request.form.get("event_time", ""),
            "brand": request.form.get("brand", "").strip(),
            "color": request.form.get("color", "").strip(),
            "is_anonymous": request.form.get("is_anonymous") == "on",
        }
        if not all(form_data[key] for key in ("item_name", "description", "location", "event_date")):
            flash("Complete the required report fields.")
            return render_template("report.html", form_data=form_data)
        try:
            datetime.strptime(form_data["event_date"], "%Y-%m-%d")
            if form_data["event_time"]:
                datetime.strptime(form_data["event_time"], "%H:%M")
        except ValueError:
            flash("Enter a valid date and time.")
            return render_template("report.html", form_data=form_data)
        if form_data["category"] not in ("Electronics", "Documents", "Wallet", "Keys", "Bag", "Books", "Other"):
            abort(400)
        image=request.files.get("image")
        cur = mysql.connection.cursor()
        duplicate_items = rank_item_matches(cur, form_data, [item_type], image_upload=image)
        duplicate_items = [match for match in duplicate_items if match["score"] >= 65]
        match_statuses = ["Found", "Claim Submitted", "Verification", "Returned"] if item_type == "Lost" else ["Lost"]
        possible_matches = rank_item_matches(cur, form_data, match_statuses, image_upload=image)
        if duplicate_items and request.form.get("confirm_report") != "yes":
            cur.close()
            return render_template("report.html", form_data=form_data,
                                   duplicate_items=duplicate_items,
                                   possible_matches=[m for m in possible_matches if m["score"] >= 35])
        cur.close()
        filename=None
        if image and image.filename:
            extension=os.path.splitext(image.filename)[1].lower()
            if extension not in (".png", ".jpg", ".jpeg", ".webp"):
                flash("Item photos must be PNG, JPG, or WEBP images.")
                return render_template("report.html", form_data=form_data)
            filename=secure_filename(
                f"{session['user_id']}_{datetime.now().strftime('%Y%m%d%H%M%S')}_{image.filename}"
            )
            image.save(os.path.join(app.config["UPLOAD_FOLDER"], filename))
        status=item_type
        cur=mysql.connection.cursor()
        cur.execute("""INSERT INTO items
                      (user_id,item_name,category,description,location,event_date,image,status,icon,brand,color,is_anonymous,qr_token,event_time)
                      VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (session["user_id"],form_data["item_name"],form_data["category"],form_data["description"],
                     form_data["location"],form_data["event_date"],filename,status,"fa-box",
                       form_data["brand"] or None,form_data["color"] or None,form_data["is_anonymous"],
                       str(uuid.uuid4()) if status == "Found" else None,form_data["event_time"] or None))
        item_id = cur.lastrowid
        log_audit(cur, "item_reported", "item", item_id,
                  {"status": status, "anonymous": form_data["is_anonymous"]})
        match_emails = []
        for match in possible_matches:
            if match["score"] >= 50 and match["user_id"] != session["user_id"]:
                cur.execute("INSERT INTO notifications(user_id,message) VALUES(%s,%s)",
                            (match["user_id"], f"Possible match: {form_data['item_name']} may match your report #{match['id']} ({match['score']}%)."))
                cur.execute("SELECT email FROM users WHERE id=%s", (match["user_id"],))
                match_email = cur.fetchone()
                if match_email:
                    match_emails.append((match_email[0], match["id"], match["score"]))
        mysql.connection.commit()
        cur.close()
        for match_email, matched_item_id, match_score in match_emails:
            send_email_notification(
                match_email, "Possible FindIt match",
                f"A new report may match item #{matched_item_id} ({match_score}% similarity). Sign in to review the match alert.",
            )
        flash(f"{status} item reported successfully.")
        return redirect(url_for("dashboard"))
    return render_template("report.html", form_data={})

@app.route("/items")
def items():
    q=request.args.get("q","").strip()
    category=request.args.get("category","")
    status=request.args.get("status","")
    location=request.args.get("location","").strip()
    neighborhood=request.args.get("neighborhood","").strip()
    cur=mysql.connection.cursor()
    sql="""SELECT i.*, CASE WHEN i.is_anonymous=1 THEN 'Anonymous' ELSE u.name END AS reporter FROM items i
           JOIN users u ON i.user_id=u.id WHERE 1=1"""
    params=[]
    if q:
        sql += " AND (i.item_name LIKE %s OR i.brand LIKE %s OR i.color LIKE %s OR i.description LIKE %s OR i.location LIKE %s)"
        like=f"%{q}%"; params += [like,like,like,like,like]
    if category:
        sql += " AND i.category=%s"; params.append(category)
    if status:
        sql += " AND i.status=%s"; params.append(status)
    if location:
        sql += " AND i.location LIKE %s"; params.append(f"%{location}%")
    if neighborhood:
        sql += " AND (i.location LIKE %s OR i.description LIKE %s)"; params.extend([f"%{neighborhood}%", f"%{neighborhood}%"])
    sql += " ORDER BY i.created_at DESC"
    cur.execute(sql,params)
    results=cur.fetchall()
    map_items = [{
        "id": row[0],
        "name": row[2],
        "status": row[8],
        "location": row[5],
        "lat": 12.9716 + (row[0] % 5) * 0.003,
        "lng": 77.5946 + (row[0] % 7) * 0.002,
    } for row in results[:20]]
    cur.close()
    return render_template("items.html",items=results,q=q,category=category,status=status,location=location,neighborhood=neighborhood,map_items=map_items)

@app.route("/item/<int:item_id>")
def item_detail(item_id):
    cur = mysql.connection.cursor()
    cur.execute(
        """SELECT i.id, i.user_id, i.item_name, i.category, i.description, i.location,
                  i.event_date, i.image, i.status, i.icon,
                  CASE WHEN i.is_anonymous=1 THEN 'Anonymous' ELSE u.name END AS reporter
           FROM items i JOIN users u ON i.user_id=u.id WHERE i.id=%s""",
        (item_id,),
    )
    item = cur.fetchone()
    cur.close()
    if not item:
        return "Item not found", 404
    return render_template("item_detail.html", item=item)

@app.route("/qr/<token>")
def qr_item(token):
    cur = mysql.connection.cursor()
    cur.execute("SELECT id FROM items WHERE qr_token=%s", (token,))
    item = cur.fetchone()
    cur.close()
    if not item:
        abort(404)
    return redirect(url_for("item_detail", item_id=item[0]))

@app.route("/admin/item/<int:item_id>/qr.png")
def item_qr(item_id):
    if not has_role("staff", "security", "admin"):
        abort(403)
    cur = mysql.connection.cursor()
    cur.execute("SELECT status,qr_token FROM items WHERE id=%s", (item_id,))
    item = cur.fetchone()
    if not item:
        cur.close()
        abort(404)
    if item[0] not in ("Found", "Claim Submitted", "Verification"):
        cur.close()
        abort(400)
    token = item[1] or str(uuid.uuid4())
    if not item[1]:
        cur.execute("UPDATE items SET qr_token=%s WHERE id=%s", (token, item_id))
        mysql.connection.commit()
    cur.close()
    try:
        import qrcode
    except ImportError:
        abort(503, description="Install project requirements to enable QR codes.")
    qr_url = url_for("qr_item", token=token, _external=True)
    if app.config["PUBLIC_BASE_URL"]:
        qr_url = f"{app.config['PUBLIC_BASE_URL']}{url_for('qr_item', token=token)}"
    qr = qrcode.make(qr_url)
    output = BytesIO()
    qr.save(output, format="PNG")
    output.seek(0)
    return send_file(output, mimetype="image/png", download_name=f"findit-item-{item_id}.png")

@app.route("/claim/<int:item_id>", methods=["POST"])
def claim(item_id):
    if not logged_in():
        return redirect(url_for("login"))
    proof = request.form["proof"].strip()
    cur = mysql.connection.cursor()
    cur.execute("""SELECT i.user_id,i.status,u.email FROM items i
                   JOIN users u ON u.id=i.user_id WHERE i.id=%s""", (item_id,))
    item = cur.fetchone()
    if not item or item[1] not in ("Found", "Claim Submitted"):
        cur.close()
        flash("This item is not accepting claims right now.")
        return redirect(url_for("item_detail", item_id=item_id))
    if item[0] == session["user_id"]:
        cur.close()
        flash("You cannot claim your own found report.")
        return redirect(url_for("item_detail", item_id=item_id))
    cur.execute("SELECT id FROM claims WHERE item_id=%s AND user_id=%s AND status='Pending'",
                (item_id, session["user_id"]))
    if cur.fetchone():
        flash("You already have a pending claim.")
    else:
        proof_image = None
        proof_file = None
        uploaded_image = request.files.get("proof_image")
        uploaded_file = request.files.get("proof_file")
        if uploaded_image and uploaded_image.filename:
            proof_image = secure_filename(
                f"proof_{uuid.uuid4().hex}_{uploaded_image.filename}"
            )
            extension = os.path.splitext(proof_image)[1].lower()
            if extension not in (".png", ".jpg", ".jpeg", ".webp"):
                cur.close()
                flash("Claim photos must be PNG, JPG, or WEBP images.")
                return redirect(url_for("item_detail", item_id=item_id))
            uploaded_image.save(os.path.join(app.config["PRIVATE_UPLOAD_FOLDER"], proof_image))
        if uploaded_file and uploaded_file.filename:
            extension = os.path.splitext(uploaded_file.filename)[1].lower()
            if extension not in (".pdf", ".png", ".jpg", ".jpeg", ".webp"):
                cur.close()
                flash("Evidence must be a PDF or image file.")
                return redirect(url_for("item_detail", item_id=item_id))
            proof_file = f"evidence_{uuid.uuid4().hex}{extension}"
            uploaded_file.save(os.path.join(app.config["PRIVATE_UPLOAD_FOLDER"], proof_file))
        cur.execute(
            "INSERT INTO claims(item_id,user_id,proof,proof_image,proof_file,status) VALUES(%s,%s,%s,%s,%s,'Pending')",
            (item_id, session["user_id"], proof, proof_image, proof_file),
        )
        claim_id = cur.lastrowid
        if item[1] == "Found":
            cur.execute("UPDATE items SET status='Claim Submitted' WHERE id=%s", (item_id,))
            log_audit(cur, "item_status_changed", "item", item_id,
                      {"from": "Found", "to": "Claim Submitted", "reason": "claim_submitted"})
        cur.execute("INSERT INTO notifications(user_id,message) VALUES(%s,%s)",
                    (item[0], f"A new claim was submitted for item #{item_id}."))
        log_audit(cur, "claim_submitted", "claim", claim_id, {"item_id": item_id})
        mysql.connection.commit()
        send_email_notification(item[2], "New FindIt claim", f"A claimant submitted proof for item #{item_id}. Please review it in the staff panel.")
        flash("Claim submitted for verification.")
    cur.close()
    return redirect(url_for("item_detail", item_id=item_id))

@app.route("/dashboard")
def dashboard():
    if not logged_in():
        return redirect(url_for("login"))
    cur=mysql.connection.cursor()
    cur.execute("SELECT * FROM items WHERE user_id=%s ORDER BY created_at DESC",(session["user_id"],))
    my_items=cur.fetchall()
    cur.execute("""SELECT c.id, i.item_name, u.name AS claimant, c.status, c.created_at
                   FROM claims c JOIN items i ON c.item_id=i.id
                   JOIN users u ON c.user_id=u.id
                   WHERE i.user_id=%s ORDER BY c.created_at DESC""",(session["user_id"],))
    claims=cur.fetchall()
    cur.execute("""SELECT c.id,i.id,i.item_name,c.status,i.status,c.created_at,hr.id
                   FROM claims c JOIN items i ON i.id=c.item_id
                   LEFT JOIN handover_receipts hr ON hr.item_id=i.id
                   WHERE c.user_id=%s ORDER BY c.created_at DESC""",
                (session["user_id"],))
    my_claims=cur.fetchall()
    cur.execute("SELECT COUNT(*) FROM items")
    total_reports = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM items WHERE status='Found'")
    found_reports = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM items WHERE status IN ('Returned','Closed','Recovered')")
    recovered_reports = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM items WHERE status='Lost'")
    lost_reports = cur.fetchone()[0]
    cur.execute("""SELECT i.id, i.item_name, i.category, i.location, i.status, i.image, i.created_at,
                          CASE WHEN i.is_anonymous=1 THEN 'Anonymous' ELSE u.name END AS reporter
                   FROM items i JOIN users u ON i.user_id=u.id
                   ORDER BY i.created_at DESC LIMIT 6""")
    recent_items = cur.fetchall()
    cur.close()
    return render_template(
        "dashboard.html",
        my_items=my_items,
        claims=claims,
        my_claims=my_claims,
        recent_items=recent_items,
        totals={"reports": total_reports, "found": found_reports, "recovered": recovered_reports,
            "lost": lost_reports,
            "lost_bar": max(12, round(lost_reports / max(1, lost_reports, found_reports, recovered_reports) * 100)),
            "found_bar": max(12, round(found_reports / max(1, lost_reports, found_reports, recovered_reports) * 100)),
            "recovered_bar": max(12, round(recovered_reports / max(1, lost_reports, found_reports, recovered_reports) * 100))},
    )

@app.route("/notifications")
def notifications():
    if not logged_in():
        return redirect(url_for("login"))
    cur=mysql.connection.cursor()
    cur.execute("""SELECT c.id,i.item_name FROM claims c JOIN items i ON i.id=c.item_id
                   WHERE c.user_id=%s AND c.status='Pending'
                   AND c.created_at < DATE_SUB(NOW(), INTERVAL 1 DAY)""",
                (session["user_id"],))
    pending_claims = cur.fetchall()
    for claim_id, item_name in pending_claims:
        reminder = f"Reminder: your claim #{claim_id} for {item_name} is still pending."
        cur.execute("SELECT id FROM notifications WHERE user_id=%s AND message=%s",
                    (session["user_id"], reminder))
        if not cur.fetchone():
            cur.execute("INSERT INTO notifications(user_id,message) VALUES(%s,%s)",
                        (session["user_id"], reminder))
    mysql.connection.commit()
    cur.execute("SELECT * FROM notifications WHERE user_id=%s ORDER BY created_at DESC",
                (session["user_id"],))
    notes=cur.fetchall()
    cur.close()
    return render_template("notifications.html",notifications=notes)

@app.route("/api/chatbot", methods=["POST"])
def chatbot():
    payload = request.get_json(silent=True) or {}
    message = str(payload.get("message", "")).strip()
    if len(message) > 2000:
        return jsonify(answer="Please keep chatbot messages under 2,000 characters.", items=[]), 400
    question = message.lower()
    flow = str(payload.get("flow", "")).strip()
    language = str(payload.get("language", "en"))
    if language not in ("en", "kn", "hi"):
        language = "en"
    conversation_history = payload.get("history", [])
    if not isinstance(conversation_history, list):
        conversation_history = []
    conversation_history = [
        entry for entry in conversation_history[-10:]
        if isinstance(entry, dict)
        and entry.get("role") in ("user", "assistant")
        and isinstance(entry.get("content"), str)
        and entry["content"].strip()
    ]
    if not question and flow != "claim-select":
        return jsonify(answer="Ask me about reports, claims, item matches, or campus locations.", items=[])
    if flow == "claim-search":
        item_query = str(payload.get("message", "")).strip().lower()
        ignored_words = {"a", "an", "and", "claim", "found", "for", "i", "item", "it", "my", "the", "to"}
        terms = [
            term for term in re.findall(r"[\w'-]+", item_query)
            if len(term) > 1 and term not in ignored_words
        ][:8]
        if not terms:
            return jsonify(
                answer=chatbot_text("claim-search-empty", language),
                items=[],
                next_step="claim-search",
            )
        match_conditions = []
        params = []
        for term in terms:
            match_conditions.append(
                "(LOWER(i.item_name) LIKE %s OR LOWER(i.category) LIKE %s OR LOWER(i.description) LIKE %s)"
            )
            params.extend([f"%{term}%", f"%{term}%", f"%{term}%"])
        cur = mysql.connection.cursor()
        cur.execute(
            """SELECT i.id,i.item_name,i.category,i.location,i.event_date FROM items i
               WHERE i.status='Found' AND (""" + " OR ".join(match_conditions)
            + ") ORDER BY i.created_at DESC LIMIT 5",
            tuple(params),
        )
        rows = cur.fetchall()
        cur.close()
        items = [
            {
                "id": row[0],
                "name": row[1],
                "category": row[2],
                "location": row[3],
                "date": str(row[4]),
            }
            for row in rows
        ]
        answer = (
            chatbot_text("claim-search-found", language)
            if items
            else chatbot_text("claim-search-none", language)
        )
        return jsonify(answer=answer, items=items, next_step="claim-select" if items else "claim-search")
    if flow == "claim-select":
        try:
            item_id = int(payload.get("item_id"))
        except (TypeError, ValueError):
            return jsonify(answer=chatbot_text("claim-select-invalid", language), items=[]), 400
        cur = mysql.connection.cursor()
        cur.execute(
            "SELECT id,user_id,item_name,status FROM items WHERE id=%s",
            (item_id,),
        )
        item = cur.fetchone()
        if not item or item[3] not in ("Found", "Claim Submitted"):
            cur.close()
            return jsonify(answer=chatbot_text("claim-unavailable", language), items=[])
        if not logged_in():
            cur.close()
            return jsonify(
                answer=chatbot_text("claim-login", language),
                items=[],
                login_url=url_for("login"),
            )
        if item[1] == session["user_id"]:
            cur.close()
            return jsonify(answer=chatbot_text("claim-own", language), items=[])
        cur.close()
        return jsonify(
            answer=chatbot_text("claim-proof", language, item=item[2]),
            items=[],
            next_step="claim-proof",
            claim_item={
                "id": item[0],
                "name": item[2],
                "claim_url": url_for("claim", item_id=item[0]),
            },
        )
    lost_item_intent = (
        any(phrase in question for phrase in ("i lost", "i've lost", "i have lost", "lost my"))
        or any(phrase in question for phrase in ("खो गया", "खो गई", "खो गया है", "खोई हुई", "कहाँ खो"))
        or any(phrase in question for phrase in ("ಕಳೆದುಹೋಯಿತು", "ಕಳೆದುಹೋಗಿದೆ", "ಕಳೆದುಕೊಂಡೆ", "ಕಳೆದುಹೋದ"))
    )
    if lost_item_intent:
        return jsonify(
            answer=chatbot_text("lost-report", language),
            items=[],
        )
    question_words = set(re.findall(r"\b[\w'-]+\b", question))
    report_steps_intent = (
        "report" in question_words
        and bool(question_words & {"how", "steps", "guide", "submit", "file"})
        and bool(question_words & {"lost", "found"})
    )
    report_steps_intent = report_steps_intent or (
        any(word in question for word in ("रिपोर्ट", "दर्ज"))
        and any(word in question for word in ("कैसे", "चरण", "बताएँ"))
        and any(word in question for word in ("खो", "मिली", "मिला", "पाया"))
    ) or (
        any(word in question for word in ("ವರದಿ", "ದಾಖಲಿಸಿ"))
        and any(word in question for word in ("ಹೇಗೆ", "ಹಂತ", "ತಿಳಿಸಿ"))
        and any(word in question for word in ("ಕಳೆದು", "ಸಿಕ್ಕಿದ", "ಸಿಕ್ಕಿತು"))
    )
    if report_steps_intent:
        if "found" in question or any(word in question for word in ("मिली", "मिला", "पाया", "ಸಿಕ್ಕಿದ", "ಸಿಕ್ಕಿತು")):
            answer = chatbot_text("report-found", language)
        else:
            answer = chatbot_text("report-lost", language)
        return jsonify(answer=answer, items=[])
    claim_intent = "claim" in question_words and bool(
        question_words & {"how", "want", "start", "submit", "help", "guide", "can", "could", "may"}
        or any(phrase in question for phrase in ("claim this", "claim the", "claim item"))
    )
    claim_intent = claim_intent or (
        any(word in question for word in ("दावा", "क्लेम"))
        and any(word in question for word in ("कैसे", "चाहता", "चाहती", "करना"))
    ) or (
        any(word in question for word in ("ಕ್ಲೇಮ್", "ಹಕ್ಕು"))
        and any(word in question for word in ("ಹೇಗೆ", "ಬಯಸುತ್ತೇನೆ", "ಮಾಡಬೇಕು", "ಮಾಡಲು"))
    )
    if claim_intent or ("proof" in question_words and "claim" in question_words):
        return jsonify(
            answer=chatbot_text("claim-start", language),
            items=[],
            next_step="claim-search",
        )
    if any(word in question for word in ("office", "कार्यालय", "कचहरी", "ಕಚೇರಿ")):
        office = app.config["LOST_FOUND_OFFICE"]
        answer = (
            f"The lost-and-found office is at {office}."
            if office else chatbot_text("office-missing", language)
        )
        return jsonify(answer=answer, items=[])
    item_words = {
        "phone", "phones", "card", "cards", "id", "ids", "document", "documents",
        "फोन", "मोबाइल", "कार्ड", "दस्तावेज़", "ಕಾರ್ಡ್", "ಫೋನ್", "ಐಡಿ",
    }
    search_intent = (
        bool(question_words & {
            "show", "find", "search", "browse", "list", "display",
            "दिखाओ", "दिखाएँ", "खोजो", "तೋರಿಸಿ", "ಹುಡುಕಿ",
        })
        or ("found" in question_words and bool(question_words & item_words))
        or (bool(question_words & {"मिली", "मिले", "मिला", "सಿಕ್ಕಿದ", "ಸಿಕ್ಕಿತು"}) and bool(question_words & item_words))
        or ("match" in question_words and bool(question_words & {"lost", "my", "item"}))
    )
    if search_intent:
        cur = mysql.connection.cursor()
        conditions = ["i.status='Found'"]
        params = []
        if any(word in question for word in ("phone", "फोन", "मोबाइल", "ಫೋನ್")):
            conditions.append("(LOWER(i.item_name) LIKE %s OR LOWER(i.description) LIKE %s)")
            params.extend(["%phone%", "%phone%"])
        elif any(word in question for word in ("id", "card", "document", "कार्ड", "दस्तावेज़", "ಕಾರ್ಡ್", "ಐಡಿ")):
            conditions.append("(LOWER(i.item_name) LIKE %s OR LOWER(i.description) LIKE %s OR i.category='Documents')")
            params.extend(["%id%", "%card%"])
        cur.execute(
            """SELECT i.id,i.item_name,i.category,i.location,i.event_date FROM items i
               WHERE """ + " AND ".join(conditions) + " ORDER BY i.created_at DESC LIMIT 5",
            tuple(params),
        )
        rows = cur.fetchall()
        cur.close()
        items = [{"name": row[1], "category": row[2], "location": row[3],
                  "date": str(row[4]), "url": url_for("item_detail", item_id=row[0])}
                 for row in rows]
        answer = (
            chatbot_text("match-found", language) if items
            else chatbot_text("match-none", language)
        )
        return jsonify(answer=answer, items=items)
    if not app.config["OPENAI_API_KEY"]:
        return jsonify(
            answer=chatbot_text("ai-missing", language),
            items=[],
        ), 503
    try:
        answer = generate_chatbot_answer(message, conversation_history, language)
    except urllib.error.HTTPError as error:
        app.logger.warning("OpenAI chatbot request failed with HTTP status %s", error.code)
        return jsonify(
            answer="The AI assistant couldn't answer right now. Check the OpenAI API key and model configuration, then try again.",
            items=[],
        ), 503
    except (OSError, TimeoutError, json.JSONDecodeError, KeyError, TypeError, ValueError) as error:
        app.logger.warning("OpenAI chatbot request failed: %s", error)
        return jsonify(
            answer="The AI assistant is temporarily unavailable. Please try again in a moment.",
            items=[],
        ), 503
    return jsonify(answer=answer, items=[], ai_generated=True)

@app.route("/admin")
def admin_panel():
    if not has_role("admin", "staff", "security"):
        return redirect(url_for("login"))
    cur=mysql.connection.cursor()
    cur.execute("SELECT COUNT(*) FROM users"); users=cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM items"); total=cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM items WHERE status='Lost'"); lost=cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM items WHERE status='Found'"); found=cur.fetchone()[0]
    cur.execute("""SELECT c.id,c.proof,c.status,c.created_at,i.item_name,u.name,u.email,c.proof_image,c.proof_file,c.item_id,i.status
                   FROM claims c JOIN items i ON c.item_id=i.id JOIN users u ON c.user_id=u.id
                   ORDER BY c.created_at DESC""")
    claims=cur.fetchall()
    cur.execute("""SELECT i.id,i.item_name,i.location,i.status FROM items i JOIN users u ON i.user_id=u.id
                   ORDER BY i.created_at DESC""")
    all_items=cur.fetchall()
    cur.execute("SELECT id,name,email,role FROM users ORDER BY name")
    all_users=cur.fetchall()
    cur.execute("""SELECT a.id,a.action,a.entity_type,a.entity_id,a.details,a.created_at,u.name
                   FROM audit_logs a LEFT JOIN users u ON a.actor_id=u.id
                   ORDER BY a.created_at DESC LIMIT 100""")
    audit_logs=cur.fetchall()
    cur.close()
    return render_template("admin.html",
        stats={"users":users,"total":total,"lost":lost,"found":found},
        claims=claims,items=all_items,users=all_users,audit_logs=audit_logs)

@app.route("/admin/user/<int:user_id>/role", methods=["POST"])
def update_user_role(user_id):
    if not is_admin():
        abort(403)
    role = request.form.get("role", "")
    if role not in ("user", "staff", "security", "admin"):
        abort(400)
    if user_id == session.get("user_id") and role != "admin":
        flash("You cannot remove your own administrator role.")
        return redirect(url_for("admin_panel"))
    cur = mysql.connection.cursor()
    cur.execute("SELECT role FROM users WHERE id=%s", (user_id,))
    previous = cur.fetchone()
    if not previous:
        cur.close()
        abort(404)
    cur.execute("UPDATE users SET role=%s WHERE id=%s", (role, user_id))
    log_audit(cur, "user_role_changed", "user", user_id,
              {"from": previous[0], "to": role})
    mysql.connection.commit()
    cur.close()
    flash("User role updated.")
    return redirect(url_for("admin_panel"))

@app.route("/admin/claim/<int:claim_id>/<action>", methods=["POST"])
def claim_action(claim_id,action):
    if not has_role("admin", "staff", "security"):
        return redirect(url_for("login"))
    if action not in ("approve","reject"):
        return redirect(url_for("admin_panel"))
    cur=mysql.connection.cursor()
    cur.execute("""SELECT c.user_id,c.item_id,u.email,c.status,i.status FROM claims c
                   JOIN users u ON u.id=c.user_id JOIN items i ON i.id=c.item_id
                   WHERE c.id=%s""",(claim_id,))
    claim=cur.fetchone()
    if not claim or claim[3] != "Pending" or claim[4] != "Claim Submitted":
        cur.close()
        return redirect(url_for("admin_panel"))
    new_status="Approved" if action=="approve" else "Rejected"
    cur.execute("UPDATE claims SET status=%s WHERE id=%s",(new_status,claim_id))
    other_claimants = []
    if action=="approve":
        cur.execute("UPDATE items SET status='Verification' WHERE id=%s",(claim[1],))
        log_audit(cur, "item_status_changed", "item", claim[1],
                  {"from": "Claim Submitted", "to": "Verification", "reason": "claim_approved"})
        cur.execute("""SELECT c.id,c.user_id,u.email FROM claims c
                       JOIN users u ON u.id=c.user_id
                       WHERE c.item_id=%s AND c.id<>%s AND c.status='Pending'""",
                    (claim[1],claim_id))
        other_claimants = cur.fetchall()
        for other_claim in other_claimants:
            cur.execute("UPDATE claims SET status='Rejected' WHERE id=%s", (other_claim[0],))
            cur.execute("INSERT INTO notifications(user_id,message) VALUES(%s,%s)",
                        (other_claim[1], f"Your claim #{other_claim[0]} was closed because another claim for item #{claim[1]} was approved."))
            log_audit(cur, "claim_auto_rejected", "claim", other_claim[0],
                      {"item_id": claim[1], "approved_claim_id": claim_id})
    else:
        cur.execute("SELECT COUNT(*) FROM claims WHERE item_id=%s AND status='Pending'", (claim[1],))
        if not cur.fetchone()[0]:
            cur.execute("UPDATE items SET status='Found' WHERE id=%s",(claim[1],))
            log_audit(cur, "item_status_changed", "item", claim[1],
                      {"from": "Claim Submitted", "to": "Found", "reason": "all_claims_rejected"})
    log_audit(cur, "claim_approved" if action == "approve" else "claim_rejected", "claim", claim_id,
              {"item_id": claim[1]})
    cur.execute("INSERT INTO notifications(user_id,message) VALUES(%s,%s)",
                (claim[0],f"Your claim #{claim_id} was {new_status.lower()}."))
    mysql.connection.commit()
    cur.close()
    send_email_notification(claim[2], f"FindIt claim {new_status.lower()}",
                            f"Your claim #{claim_id} was {new_status.lower()}.")
    for other_claim in other_claimants:
        send_email_notification(
            other_claim[2], "FindIt claim closed",
            f"Your claim #{other_claim[0]} was closed because another claim for item #{claim[1]} was approved.",
        )
    return redirect(url_for("admin_panel"))

@app.route("/claim/<int:claim_id>/evidence/<kind>")
def claim_evidence(claim_id, kind):
    if not has_role("staff", "security", "admin"):
        abort(403)
    evidence_column = {"image": "proof_image", "document": "proof_file"}.get(kind)
    if not evidence_column:
        abort(404)
    cur = mysql.connection.cursor()
    cur.execute(f"SELECT {evidence_column} FROM claims WHERE id=%s", (claim_id,))
    evidence = cur.fetchone()
    cur.close()
    if not evidence or not evidence[0]:
        abort(404)
    return send_from_directory(app.config["PRIVATE_UPLOAD_FOLDER"], evidence[0], as_attachment=True)

@app.route("/admin/item/<int:item_id>/status", methods=["POST"])
def item_status(item_id):
    if not has_role("admin", "staff", "security"):
        abort(403)
    transitions = {
        "Lost": {"Found", "Closed"},
        "Found": {"Claim Submitted", "Closed"},
        "Claim Submitted": {"Verification", "Found"},
        "Verification": {"Found"},
        "Returned": {"Closed"},
        "Recovered": {"Closed"},
    }
    new_status = request.form.get("status", "")
    cur = mysql.connection.cursor()
    cur.execute("SELECT status FROM items WHERE id=%s", (item_id,))
    current = cur.fetchone()
    if not current:
        cur.close()
        abort(404)
    if new_status not in transitions.get(current[0], set()):
        cur.close()
        abort(400)
    cur.execute("UPDATE items SET status=%s WHERE id=%s", (new_status, item_id))
    log_audit(cur, "item_status_changed", "item", item_id,
              {"from": current[0], "to": new_status})
    mysql.connection.commit()
    cur.close()
    flash(f"Item status updated to {new_status}.")
    return redirect(url_for("admin_panel"))

@app.route("/admin/item/<int:item_id>/handover", methods=["POST"])
def item_handover(item_id):
    if not has_role("admin", "security"):
        abort(403)
    cur = mysql.connection.cursor()
    cur.execute("SELECT status FROM items WHERE id=%s", (item_id,))
    item = cur.fetchone()
    if not item:
        cur.close()
        abort(404)
    if item[0] != "Verification":
        cur.close()
        abort(400)
    cur.execute("""SELECT c.id,c.user_id,u.email,u.name FROM claims c JOIN users u ON u.id=c.user_id
                   WHERE c.item_id=%s AND c.status='Approved' ORDER BY c.id DESC LIMIT 1""", (item_id,))
    claimant = cur.fetchone()
    if not claimant:
        cur.close()
        abort(400, description="An approved claim is required before handover.")
    receiver_name = request.form.get("receiver_name", "").strip()
    receiver_confirmed = request.form.get("receiver_confirmed") == "on"
    if receiver_name.casefold() != claimant[3].strip().casefold() or not receiver_confirmed:
        cur.close()
        flash("Confirm the receiver's identity and acknowledgement before recording handover.")
        return redirect(url_for("admin_panel"))
    cur.execute(
        """INSERT INTO handover_receipts
           (item_id,claim_id,receiver_id,receiver_name,receiver_confirmation,
            authorized_staff_id,authorized_staff_name)
           VALUES(%s,%s,%s,%s,1,%s,%s)""",
        (item_id, claimant[0], claimant[1], receiver_name,
         session["user_id"], session.get("name", "Authorized staff")),
    )
    receipt_id = cur.lastrowid
    cur.execute("UPDATE items SET status='Returned' WHERE id=%s", (item_id,))
    cur.execute("UPDATE claims SET handover_at=NOW() WHERE item_id=%s AND status='Approved'", (item_id,))
    log_audit(cur, "item_handed_over", "item", item_id,
              {"receipt_id": receipt_id, "receiver_id": claimant[1]})
    cur.execute("INSERT INTO notifications(user_id,message) VALUES(%s,%s)",
                (claimant[1], f"Handover for item #{item_id} was recorded. Receipt #{receipt_id} is available."))
    mysql.connection.commit()
    cur.close()
    send_email_notification(claimant[2], "FindIt handover recorded",
                            f"Handover for item #{item_id} has been recorded. Receipt #{receipt_id} is available.")
    flash("Handover recorded.")
    return redirect(url_for("handover_receipt", item_id=item_id))


@app.route("/item/<int:item_id>/handover-receipt")
def handover_receipt(item_id):
    if not logged_in():
        return redirect(url_for("login"))
    cur = mysql.connection.cursor()
    cur.execute(
        """SELECT r.id,r.item_id,i.item_name,c.user_id,r.receiver_name,
                  r.authorized_staff_name,r.confirmed_at,r.receiver_confirmation,
                  reporter.name
           FROM handover_receipts r
           JOIN items i ON i.id=r.item_id
           JOIN claims c ON c.id=r.claim_id
           JOIN users reporter ON reporter.id=i.user_id
           WHERE r.item_id=%s""",
        (item_id,),
    )
    receipt = cur.fetchone()
    cur.close()
    if not receipt:
        abort(404)
    if not has_role("admin", "staff", "security") and receipt[3] != session["user_id"]:
        abort(403)
    return render_template("handover_receipt.html", receipt=receipt)

@app.route("/admin/item/<int:item_id>/delete", methods=["POST"])
def delete_item(item_id):
    if not is_admin():
        abort(403)
    cur = mysql.connection.cursor()
    cur.execute("SELECT item_name,status FROM items WHERE id=%s", (item_id,))
    item = cur.fetchone()
    if not item:
        cur.close()
        abort(404)
    log_audit(cur, "item_deleted", "item", item_id,
              {"name": item[0], "status": item[1]})
    cur.execute("DELETE FROM items WHERE id=%s", (item_id,))
    mysql.connection.commit()
    cur.close()
    flash("Report deleted.")
    return redirect(url_for("admin_panel"))

if __name__=="__main__":
    app.run(host="0.0.0.0", debug=False)

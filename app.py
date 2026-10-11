import os
import json
import re
import hmac
import base64
import hashlib
import sqlite3
import uuid
import secrets
import smtplib
import urllib.error
import urllib.request
from io import BytesIO
from email.message import EmailMessage
from difflib import SequenceMatcher
from datetime import datetime
from flask import Flask, abort, jsonify, render_template, request, redirect, url_for, session, flash, send_file, send_from_directory
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.datastructures import FileStorage
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
app.config["GEMINI_API_KEY"] = os.environ.get("GEMINI_API_KEY", "")
app.config["GEMINI_MODEL"] = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
app.config["OPENAI_API_KEY"] = os.environ.get("OPENAI_API_KEY", "")
app.config["OPENAI_MODEL"] = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
app.config["AI_IMAGE_MATCHING"] = os.environ.get("AI_IMAGE_MATCHING", "true").lower() == "true"
app.config["SMART_MATCH_MODEL"] = "sentence-transformers/all-MiniLM-L6-v2"
app.config["MAP_TILE_URL"] = os.environ.get(
    "MAP_TILE_URL", "https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
)
app.config["REWARD_SUCCESSFUL_RETURN_POINTS"] = int(
    os.environ.get("REWARD_SUCCESSFUL_RETURN_POINTS", "100")
)
app.config["REWARD_APPROVED_CLUE_POINTS"] = int(
    os.environ.get("REWARD_APPROVED_CLUE_POINTS", "10")
)
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

    @property
    def rowcount(self):
        return self._cursor.rowcount

    def close(self):
        self._cursor.close()


class SQLiteConnection:
    def __init__(self, connection):
        self._connection = connection

    def cursor(self):
        return SQLiteCursor(self._connection.cursor())

    def commit(self):
        self._connection.commit()

    def rollback(self):
        self._connection.rollback()

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

_smart_match_model = None
_smart_match_fallback_logged = False


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
        "ai-missing": "AI answers aren't configured yet. Set the GEMINI_API_KEY environment variable in your hosting settings, then restart FindIt. I can still help with item search and guided reports or claims.",
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
        "ai-missing": "AI ಉತ್ತರಗಳನ್ನು ಇನ್ನೂ ಹೊಂದಿಸಲಾಗಿಲ್ಲ. ಹೋಸ್ಟಿಂಗ್ ಸೆಟ್ಟಿಂಗ್‌ಗಳಲ್ಲಿ GEMINI_API_KEY ಹೊಂದಿಸಿ FindIt ಅನ್ನು ಮರುಪ್ರಾರಂಭಿಸಿ. ವಸ್ತು ಹುಡುಕಾಟ ಮತ್ತು ವರದಿ/ಕ್ಲೇಮ್ ಮಾರ್ಗದರ್ಶನ ಇನ್ನೂ ಲಭ್ಯವಿದೆ.",
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
        "ai-missing": "AI जवाब अभी सेट नहीं हैं। होस्टिंग सेटिंग में GEMINI_API_KEY सेट करके FindIt को फिर शुरू करें। वस्तु खोज और रिपोर्ट/दावे का मार्गदर्शन फिर भी उपलब्ध है।",
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
            ("items", "city", "VARCHAR(100) NULL"),
            ("items", "state", "VARCHAR(100) NULL"),
            ("items", "approx_lat", "REAL NULL"),
            ("items", "approx_lng", "REAL NULL"),
            ("items", "private_details", "TEXT NULL"),
            ("claims", "proof_image", "VARCHAR(255) NULL"),
            ("claims", "proof_file", "VARCHAR(255) NULL"),
            ("claims", "handover_at", "DATETIME NULL"),
            ("users", "phone", "VARCHAR(20) NULL"),
            ("users", "student_id", "VARCHAR(40) NULL"),
            ("users", "reward_public", "BOOLEAN NOT NULL DEFAULT FALSE"),
            ("users", "reward_public_name", "VARCHAR(80) NULL"),
            ("notifications", "notification_type", "VARCHAR(20) NOT NULL DEFAULT 'general'"),
            ("notifications", "item_id", "INT NULL"),
            ("notifications", "matched_item_id", "INT NULL"),
            ("notifications", "match_score", "INTEGER NULL"),
            ("notifications", "explanation", "TEXT NULL"),
            ("notifications", "claim_id", "INT NULL"),
        ):
            cur.execute(f"PRAGMA table_info({table})")
            if not any(row[1] == column for row in cur.fetchall()):
                cur.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
        cur.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='claims'")
        claims_schema = cur.fetchone()
        if claims_schema and "'Under Review'" not in (claims_schema[0] or ""):
            mysql.connection.commit()
            cur.execute("PRAGMA foreign_keys=OFF")
            cur.execute("""CREATE TABLE claims_status_migration (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                item_id INT NOT NULL,
                user_id INT NOT NULL,
                proof TEXT NOT NULL,
                proof_image VARCHAR(255) DEFAULT NULL,
                proof_file VARCHAR(255) DEFAULT NULL,
                status TEXT NOT NULL DEFAULT 'Pending'
                    CHECK (status IN ('Pending','Under Review','Approved','Rejected','Returned')),
                handover_at DATETIME DEFAULT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (item_id) REFERENCES items(id) ON DELETE CASCADE,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            )""")
            cur.execute("""INSERT INTO claims_status_migration
                (id,item_id,user_id,proof,proof_image,proof_file,status,handover_at,created_at)
                SELECT id,item_id,user_id,proof,proof_image,proof_file,status,handover_at,created_at
                FROM claims""")
            cur.execute("DROP TABLE claims")
            cur.execute("ALTER TABLE claims_status_migration RENAME TO claims")
            mysql.connection.commit()
            cur.execute("PRAGMA foreign_keys=ON")
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
        cur.execute("""CREATE TABLE IF NOT EXISTS reward_transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INT NOT NULL,
            contribution_type VARCHAR(40) NOT NULL,
            recovery_id INT NULL,
            contribution_id INT NULL,
            points INTEGER NOT NULL CHECK (points <> 0),
            reason VARCHAR(500) NOT NULL,
            created_by INT NULL,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
            FOREIGN KEY (recovery_id) REFERENCES items(id) ON DELETE SET NULL,
            FOREIGN KEY (created_by) REFERENCES users(id) ON DELETE SET NULL
        )""")
        cur.execute("""CREATE UNIQUE INDEX IF NOT EXISTS uq_reward_successful_return
                       ON reward_transactions(user_id,recovery_id)
                       WHERE contribution_type='successful_return'
                         AND recovery_id IS NOT NULL""")
        cur.execute("""CREATE UNIQUE INDEX IF NOT EXISTS uq_reward_clue_contribution
                       ON reward_transactions(user_id,contribution_id)
                       WHERE contribution_type='helpful_clue'
                         AND contribution_id IS NOT NULL""")
        cur.execute("""CREATE UNIQUE INDEX IF NOT EXISTS uq_reward_return_reversal
                       ON reward_transactions(user_id,recovery_id)
                       WHERE contribution_type='reversal'
                         AND recovery_id IS NOT NULL""")
        cur.execute("""CREATE TABLE IF NOT EXISTS reward_badges (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name VARCHAR(80) NOT NULL UNIQUE,
            successful_returns INTEGER NOT NULL UNIQUE CHECK (successful_returns > 0),
            active BOOLEAN NOT NULL DEFAULT TRUE
        )""")
        cur.execute(
            "UPDATE reward_badges SET successful_returns=30 "
            "WHERE name='FindIt Champion' AND successful_returns=10"
        )
        cur.execute(
            "UPDATE reward_badges SET successful_returns=15 "
            "WHERE name='Community Hero' AND successful_returns=5"
        )
        cur.execute(
            "UPDATE reward_badges SET name='Trusted Finder', successful_returns=5 "
            "WHERE name='Trusted Helper' AND successful_returns=3"
        )
        for badge_name, threshold in (
            ("Helpful Member", 1),
            ("Trusted Finder", 5),
            ("Community Hero", 15),
            ("FindIt Champion", 30),
        ):
            cur.execute(
                "INSERT OR IGNORE INTO reward_badges(name,successful_returns) VALUES(%s,%s)",
                (badge_name, threshold),
            )
        cur.execute("""CREATE TABLE IF NOT EXISTS user_reward_badges (
            user_id INT NOT NULL,
            badge_id INT NOT NULL,
            awarded_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY(user_id,badge_id),
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
            FOREIGN KEY (badge_id) REFERENCES reward_badges(id) ON DELETE CASCADE
        )""")
        cur.execute("""CREATE TABLE IF NOT EXISTS reward_contributions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INT NOT NULL,
            item_id INT NOT NULL,
            contribution_type VARCHAR(40) NOT NULL,
            details VARCHAR(1000) NOT NULL,
            status VARCHAR(20) NOT NULL DEFAULT 'Pending'
                CHECK (status IN ('Pending','Approved','Rejected')),
            reviewed_by INT NULL,
            reviewed_at TIMESTAMP NULL,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
            FOREIGN KEY (item_id) REFERENCES items(id) ON DELETE CASCADE,
            FOREIGN KEY (reviewed_by) REFERENCES users(id) ON DELETE SET NULL,
            UNIQUE(user_id,item_id,contribution_type)
        )""")
        cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_items_qr_token ON items(qr_token)")
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_items_status_city_state_date "
            "ON items(status,city,state,event_date)"
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_items_status_date ON items(status,event_date)"
        )
        cur.execute("""CREATE UNIQUE INDEX IF NOT EXISTS uq_notifications_match_pair
                       ON notifications(item_id,matched_item_id)
                       WHERE notification_type='match'
                         AND item_id IS NOT NULL AND matched_item_id IS NOT NULL""")
        mysql.connection.commit()
    except Exception:
        if cur:
            try:
                mysql.connection.rollback()
                cur.execute("PRAGMA foreign_keys=ON")
            except Exception:
                app.logger.exception("Unable to restore database state after migration failure")
        app.logger.exception("FindIt database feature migration did not complete")
    finally:
        if cur:
            cur.close()


initialize_database()
with app.app_context():
    ensure_claim_image_column()


def logged_in():
    return "user_id" in session

def csrf_token():
    if "_csrf_token" not in session:
        session["_csrf_token"] = secrets.token_urlsafe(32)
    return session["_csrf_token"]

def require_csrf_token():
    expected = session.get("_csrf_token", "")
    supplied = request.form.get("_csrf_token", "")
    if not expected or not supplied or not hmac.compare_digest(expected, supplied):
        abort(400, description="Invalid or missing security token.")

@app.context_processor
def inject_csrf_token():
    return {"csrf_token": csrf_token}

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
    api_key = app.config["GEMINI_API_KEY"]
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
        "model": app.config["GEMINI_MODEL"],
        "messages": messages,
        "max_tokens": 450,
    }).encode("utf-8")
    api_request = urllib.request.Request(
        "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
        data=payload,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    api_request.add_header("Authorization", "Bearer " + api_key)
    with urllib.request.urlopen(api_request, timeout=15) as response:
        result = json.loads(response.read().decode("utf-8"))

    answer = result["choices"][0]["message"]["content"]
    if not isinstance(answer, str) or not answer.strip():
        raise ValueError("Gemini returned an empty chatbot response")
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


def ai_image_similarity(upload, candidates, include_explanations=False):
    if not app.config["OPENAI_API_KEY"] or not candidates:
        return {}
    try:
        content = [{
            "type": "text",
            "text": (
                "Image 1 is the user's uploaded reference. The next images are candidate report photos "
                "in order. Compare visible category/shape, main colors, patterns, readable brand/model "
                "markings, and distinctive visible features when present. Do not guess unreadable text or "
                "infer identity from unrelated backgrounds. The comparison is not proof the objects are "
                "identical. "
                "Return only JSON in this schema: "
                '{"matches":[{"index":1,"score":0,"explanation":"short visible-feature comparison"}]}. '
                "Include each candidate index once, with integer similarity scores from 0 to 100. "
                "Keep each explanation brief and limited to visible similarities or differences."
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
            "max_completion_tokens": 700,
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
                normalized_score = round(score / 100, 4)
                explanation = match.get("explanation", "")
                if not isinstance(explanation, str):
                    explanation = ""
                explanation = explanation.strip()[:240]
                scores[candidate_indices[index - 1]] = (
                    {"score": normalized_score, "explanation": explanation}
                    if include_explanations else normalized_score
                )
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


def sanitize_item_photo(upload):
    if not upload or not upload.filename:
        return None, None

    extension = os.path.splitext(upload.filename)[1].lower()
    allowed_formats = {
        ".jpg": "JPEG", ".jpeg": "JPEG", ".png": "PNG", ".webp": "WEBP",
    }
    if extension not in allowed_formats:
        raise ValueError("Item photos must be JPEG, PNG, or WEBP images.")

    try:
        from PIL import Image, ImageOps
    except ImportError as error:
        raise ValueError("Image validation is unavailable; install the project's image dependencies.") from error

    try:
        upload.stream.seek(0)
        with Image.open(upload.stream) as source:
            if source.format != allowed_formats[extension]:
                raise ValueError("The photo contents do not match the selected file type.")
            if source.width <= 0 or source.height <= 0 or source.width * source.height > 20_000_000:
                raise ValueError("The photo dimensions are too large. Choose an image under 20 megapixels.")
            source.verify()

        upload.stream.seek(0)
        with Image.open(upload.stream) as source:
            image = ImageOps.exif_transpose(source).convert("RGB")
            image.thumbnail((1600, 1600))
            output = BytesIO()
            image.save(output, format="JPEG", quality=85, optimize=True)
        return f"{uuid.uuid4().hex}.jpg", output.getvalue()
    except ValueError:
        raise
    except (OSError, Image.DecompressionBombError) as error:
        raise ValueError("The uploaded file is not a valid JPEG, PNG, or WEBP image.") from error


def add_visual_scores(image_upload, matches, allow_ai_visual=False, visual_weight=0.25):
    if not image_upload or not image_upload.filename:
        return matches
    local_scores = {}
    for match in matches:
        if match.get("image"):
            score = photo_similarity(image_upload, match["image"])
            if score is not None:
                local_scores[match["id"]] = score

    ai_scores = {}
    if (
        allow_ai_visual
        and app.config["OPENAI_API_KEY"]
        and app.config["AI_IMAGE_MATCHING"]
    ):
        candidates = [
            {"id": match["id"], "image": match["image"]}
            for match in matches if match.get("image")
        ][:10]
        ai_scores = ai_image_similarity(
            image_upload, candidates, include_explanations=True
        )

    for match in matches:
        ai_result = ai_scores.get(match["id"])
        if isinstance(ai_result, dict):
            visual_score = ai_result["score"]
            if ai_result.get("explanation"):
                match["visual_explanation"] = ai_result["explanation"]
        else:
            visual_score = ai_result if ai_result is not None else local_scores.get(match["id"])
        if visual_score is None:
            continue
        previous_score = match["score"]
        match["image_score"] = round(visual_score * 100)
        match["score"] = round(
            previous_score * (1 - visual_weight) + visual_score * 100 * visual_weight
        )
        match["match_method"] = (
            "AI visual + metadata" if match["id"] in ai_scores
            else "Photo similarity + metadata"
        )
        visual_basis = (
            "AI visual comparison"
            if match["id"] in ai_scores
            else "local perceptual-image similarity"
        )
        match.setdefault("reasons", []).append(
            f"{visual_basis} ({match['image_score']}/100) considers overall appearance, "
            "including shape, color, and visible patterns; compare the items yourself"
        )
    return sorted(matches, key=lambda match: (-match["score"], match["id"]))


def rank_item_matches(
    cur, form_data, statuses, limit=5, image_upload=None, allow_ai_visual=False
):
    cur.execute(
        """SELECT id,item_name,category,description,location,event_date,status,brand,color,image,user_id,is_anonymous,event_time,city,state
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
            "id": row[0], "name": row[1], "category": row[2],
            "location": public_item_region(row[13], row[14], row[4]),
            "status": row[6], "image": row[9], "user_id": row[10],
            "reporter": "Anonymous" if row[11] else None,
            "image_score": round(image_score * 100) if image_score is not None else None,
            "score": round(score * 100),
        })
    ranked.sort(key=lambda match: match["score"], reverse=True)
    if (
        image_upload
        and allow_ai_visual
        and app.config["OPENAI_API_KEY"]
        and app.config["AI_IMAGE_MATCHING"]
    ):
        visual_scores = ai_image_similarity(
            image_upload, ranked[:10], include_explanations=True
        )
        for match in ranked:
            vision_result = visual_scores.get(match["id"])
            if isinstance(vision_result, dict):
                vision_score = vision_result["score"]
                if vision_result.get("explanation"):
                    match["visual_explanation"] = vision_result["explanation"]
            else:
                vision_score = vision_result
            if vision_score is not None:
                match["image_score"] = round(vision_score * 100)
                match["match_method"] = "AI visual + metadata"
                match["score"] = round(match["score"] * 0.6 + vision_score * 100 * 0.4)
    return ranked[:limit]


def _description_hash(text):
    return hashlib.sha256(text.strip().encode("utf-8")).hexdigest()


def _lexical_description_similarity(left, right):
    left_text = left.casefold().strip()
    right_text = right.casefold().strip()
    left_words = set(re.findall(r"\w+", left_text))
    right_words = set(re.findall(r"\w+", right_text))
    token_similarity = len(left_words & right_words) / max(1, len(left_words | right_words))
    return 0.6 * SequenceMatcher(None, left_text, right_text).ratio() + 0.4 * token_similarity


def semantic_description_scores(cur, query_description, candidates):
    """Return candidate scores and the text-similarity method used."""
    global _smart_match_model, _smart_match_fallback_logged
    if not candidates:
        return {}, "Sentence Transformers"

    model_name = app.config["SMART_MATCH_MODEL"]
    try:
        if _smart_match_model is None:
            from sentence_transformers import SentenceTransformer

            _smart_match_model = SentenceTransformer(model_name)
    except ImportError:
        if not _smart_match_fallback_logged:
            app.logger.warning(
                "Sentence Transformers is unavailable; using lexical smart-match similarity"
            )
            _smart_match_fallback_logged = True
        return {
            row[0]: _lexical_description_similarity(query_description, row[3])
            for row in candidates
        }, "Lexical fallback"
    except OSError as error:
        if not _smart_match_fallback_logged:
            app.logger.warning(
                "Sentence Transformer model could not be loaded; using lexical smart-match similarity: %s",
                error,
            )
            _smart_match_fallback_logged = True
        return {
            row[0]: _lexical_description_similarity(query_description, row[3])
            for row in candidates
        }, "Lexical fallback"

    candidate_ids = [row[0] for row in candidates]
    placeholders = ",".join(["%s"] * len(candidate_ids))
    cur.execute(
        f"""SELECT item_id,text_hash,embedding FROM item_embeddings
            WHERE model=%s AND item_id IN ({placeholders})""",
        (model_name, *candidate_ids),
    )
    cached = {
        row[0]: (row[1], row[2])
        for row in cur.fetchall()
    }
    embeddings = {}
    missing = []
    for row in candidates:
        text_hash = _description_hash(row[3])
        cached_row = cached.get(row[0])
        if cached_row and cached_row[0] == text_hash:
            try:
                embeddings[row[0]] = json.loads(cached_row[1])
                continue
            except json.JSONDecodeError:
                app.logger.warning("Discarding invalid cached embedding for item %s", row[0])
        missing.append((row[0], text_hash, row[3]))

    if missing:
        encoded = _smart_match_model.encode(
            [entry[2] for entry in missing],
            convert_to_numpy=True,
            normalize_embeddings=True,
        )
        for (item_id, text_hash, _), vector in zip(missing, encoded):
            embedding = vector.tolist()
            embeddings[item_id] = embedding
            cur.execute(
                """INSERT INTO item_embeddings(item_id,model,text_hash,embedding)
                   VALUES(%s,%s,%s,%s)
                   ON CONFLICT(item_id,model) DO UPDATE SET
                     text_hash=excluded.text_hash,embedding=excluded.embedding,
                     updated_at=CURRENT_TIMESTAMP""",
                (item_id, model_name, text_hash, json.dumps(embedding)),
            )

    query_embedding = _smart_match_model.encode(
        [query_description],
        convert_to_numpy=True,
        normalize_embeddings=True,
    )[0].tolist()
    scores = {
        item_id: max(0.0, min(1.0, sum(a * b for a, b in zip(query_embedding, embedding))))
        for item_id, embedding in embeddings.items()
    }
    return scores, "Sentence Transformers (all-MiniLM-L6-v2)"


def _optional_text_similarity(left, right):
    if not left or not right:
        return None
    return SequenceMatcher(None, left.casefold().strip(), right.casefold().strip()).ratio()


CITY_ALIASES = {
    "Ahmedabad": ("ahmedabad", "amdavad"),
    "Bengaluru": ("bengaluru", "bangalore"),
    "Chennai": ("chennai", "madras"),
    "Delhi": ("delhi", "new delhi", "nct delhi"),
    "Gurugram": ("gurugram", "gurgaon"),
    "Hyderabad": ("hyderabad",),
    "Kochi": ("kochi", "cochin"),
    "Kolkata": ("kolkata", "calcutta"),
    "Mumbai": ("mumbai", "bombay"),
    "Mysuru": ("mysuru", "mysore"),
    "Pune": ("pune", "poona"),
    "Thiruvananthapuram": ("thiruvananthapuram", "trivandrum"),
}

STATE_ALIASES = {
    "Andhra Pradesh": ("andhra pradesh", "andhra"),
    "Arunachal Pradesh": ("arunachal pradesh",),
    "Assam": ("assam",),
    "Bihar": ("bihar",),
    "Chhattisgarh": ("chhattisgarh", "chattisgarh"),
    "Andaman and Nicobar Islands": ("andaman and nicobar islands", "andaman nicobar"),
    "Chandigarh": ("chandigarh",),
    "Goa": ("goa",),
    "Gujarat": ("gujarat",),
    "Haryana": ("haryana",),
    "Himachal Pradesh": ("himachal pradesh",),
    "Jharkhand": ("jharkhand",),
    "Karnataka": ("karnataka",),
    "Kerala": ("kerala",),
    "Madhya Pradesh": ("madhya pradesh",),
    "Maharashtra": ("maharashtra",),
    "Manipur": ("manipur",),
    "Meghalaya": ("meghalaya",),
    "Mizoram": ("mizoram",),
    "Nagaland": ("nagaland",),
    "Odisha": ("odisha", "orissa"),
    "Punjab": ("punjab",),
    "Rajasthan": ("rajasthan",),
    "Sikkim": ("sikkim",),
    "Tamil Nadu": ("tamil nadu",),
    "Telangana": ("telangana",),
    "Tripura": ("tripura",),
    "Uttar Pradesh": ("uttar pradesh",),
    "Uttarakhand": ("uttarakhand", "uttaranchal"),
    "West Bengal": ("west bengal",),
    "Delhi": ("delhi", "nct delhi"),
    "Dadra and Nagar Haveli and Daman and Diu": (
        "dadra and nagar haveli and daman and diu",
        "daman and diu",
    ),
    "Jammu and Kashmir": ("jammu and kashmir", "jammu & kashmir"),
    "Ladakh": ("ladakh",),
    "Lakshadweep": ("lakshadweep",),
    "Puducherry": ("puducherry", "pondicherry"),
}

CITY_STATE = {
    "Ahmedabad": "Gujarat",
    "Bengaluru": "Karnataka",
    "Chennai": "Tamil Nadu",
    "Delhi": "Delhi",
    "Gurugram": "Haryana",
    "Hyderabad": "Telangana",
    "Kochi": "Kerala",
    "Kolkata": "West Bengal",
    "Mumbai": "Maharashtra",
    "Mysuru": "Karnataka",
    "Pune": "Maharashtra",
    "Thiruvananthapuram": "Kerala",
}

CITY_CENTROIDS = {
    "Ahmedabad": (23.0225, 72.5714),
    "Amritsar": (31.6340, 74.8723),
    "Bengaluru": (12.9716, 77.5946),
    "Bhopal": (23.2599, 77.4126),
    "Bhubaneswar": (20.2961, 85.8245),
    "Chandigarh": (30.7333, 76.7794),
    "Chennai": (13.0827, 80.2707),
    "Dehradun": (30.3165, 78.0322),
    "Delhi": (28.6139, 77.2090),
    "Guwahati": (26.1445, 91.7362),
    "Hyderabad": (17.3850, 78.4867),
    "Jaipur": (26.9124, 75.7873),
    "Kochi": (9.9312, 76.2673),
    "Kolkata": (22.5726, 88.3639),
    "Lucknow": (26.8467, 80.9462),
    "Mumbai": (19.0760, 72.8777),
    "Mysuru": (12.2958, 76.6394),
    "Patna": (25.5941, 85.1376),
    "Pune": (18.5204, 73.8567),
    "Raipur": (21.2514, 81.6296),
    "Ranchi": (23.3441, 85.3096),
    "Srinagar": (34.0837, 74.7973),
    "Thiruvananthapuram": (8.5241, 76.9366),
}


def _normalize_place(value, aliases):
    normalized = re.sub(r"[^a-z0-9 ]", " ", (value or "").casefold())
    normalized = " ".join(normalized.split())
    for canonical, variations in aliases.items():
        if normalized in variations:
            return canonical
    return normalized.title() if normalized else ""


def normalize_city(value):
    return _normalize_place(value, CITY_ALIASES)


def normalize_state(value):
    return _normalize_place(value, STATE_ALIASES)


def resolve_report_region(city, state, location=""):
    city = normalize_city(city)
    state = normalize_state(state)
    searchable_location = " " + re.sub(
        r"[^a-z0-9 ]", " ", (location or "").casefold()
    ) + " "
    if not city:
        for canonical, aliases in CITY_ALIASES.items():
            if any(f" {alias} " in searchable_location for alias in aliases):
                city = canonical
                break
    if not state:
        for canonical, aliases in STATE_ALIASES.items():
            if any(f" {alias} " in searchable_location for alias in aliases):
                state = canonical
                break
    if city and not state:
        state = CITY_STATE.get(city, "")
    return city or None, state or None


def public_item_region(city, state, location=""):
    normalized_city, normalized_state = resolve_report_region(city, state, location)
    return ", ".join(value for value in (normalized_city, normalized_state) if value) or "City/state not provided"


app.jinja_env.globals["public_item_region"] = public_item_region


def _valid_approx_coordinates(latitude, longitude):
    if latitude in (None, "") or longitude in (None, ""):
        return None, None
    try:
        latitude, longitude = float(latitude), float(longitude)
    except (TypeError, ValueError):
        return None, None
    if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
        return None, None
    return latitude, longitude


def _distance_km(lat1, lng1, lat2, lng2):
    from math import asin, cos, radians, sin, sqrt

    lat1, lng1, lat2, lng2 = map(radians, (lat1, lng1, lat2, lng2))
    delta_lat, delta_lng = lat2 - lat1, lng2 - lng1
    haversine = sin(delta_lat / 2) ** 2 + cos(lat1) * cos(lat2) * sin(delta_lng / 2) ** 2
    return 6371 * 2 * asin(sqrt(haversine))


def score_memory_map_candidate(visit, candidate):
    location_score = None
    reasons = []
    if all(value is not None for value in (
        visit.get("approx_lat"), visit.get("approx_lng"),
        candidate.get("approx_lat"), candidate.get("approx_lng"),
    )):
        distance = _distance_km(
            visit["approx_lat"], visit["approx_lng"],
            candidate["approx_lat"], candidate["approx_lng"],
        )
        location_score = max(0, 100 - distance * 2)
        reasons.append(f"Approximate locations are {distance:.1f} km apart")
    else:
        visit_city, visit_state = resolve_report_region(
            visit.get("city"), visit.get("state"), visit.get("place_name", "")
        )
        candidate_city, candidate_state = resolve_report_region(
            candidate.get("city"), candidate.get("state"), candidate.get("location", "")
        )
        if visit_city and candidate_city and visit_city == candidate_city:
            location_score = 75
            if visit_state and candidate_state and visit_state == candidate_state:
                location_score = 85
            reasons.append(f"Both locations are in {visit_city}")
        else:
            visit_place = " ".join(
                re.findall(r"[a-z0-9]+", visit.get("place_name", "").casefold())
            )
            candidate_place = " ".join(
                re.findall(r"[a-z0-9]+", candidate.get("location", "").casefold())
            )
            if visit_place and candidate_place:
                similarity = SequenceMatcher(None, visit_place, candidate_place).ratio()
                if similarity >= 0.35:
                    location_score = similarity * 70
                    reasons.append("Visited-place name resembles the report area")
    time_score = None
    visit_time = _parse_memory_datetime(visit.get("visited_at"))
    report_time = _parse_memory_datetime(
        f"{candidate.get('event_date', '')}T{candidate.get('event_time') or '12:00'}"
    )
    if visit_time and report_time:
        hours = abs((visit_time - report_time).total_seconds()) / 3600
        time_score = max(0, 100 - (hours / 24) * 12)
        reasons.append(f"Visit and report times are about {hours:.0f} hours apart")

    signals = [
        (location_score, 0.65),
        (time_score, 0.35),
    ]
    available_signals = [(score, weight) for score, weight in signals if score is not None]
    if not available_signals:
        return None, reasons
    total_weight = sum(weight for _, weight in available_signals)
    score = round(sum(value * weight for value, weight in available_signals) / total_weight)
    return score, reasons


def _parse_memory_datetime(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace(" ", "T"))
    except ValueError:
        return None


def validate_memory_place_form():
    place_name = request.form.get("place_name", "").strip()
    visited_at = request.form.get("visited_at", "").strip()
    city = request.form.get("city", "").strip()[:100]
    state = request.form.get("state", "").strip()[:100]
    notes = request.form.get("notes", "").strip()
    latitude_value = request.form.get("approx_lat", "").strip()
    longitude_value = request.form.get("approx_lng", "").strip()

    if not place_name or len(place_name) > 180:
        return None, "Enter a place name of up to 180 characters."
    if len(notes) > 500:
        return None, "Notes must be 500 characters or fewer."
    try:
        datetime.strptime(visited_at, "%Y-%m-%dT%H:%M")
    except ValueError:
        return None, "Enter a valid date and approximate time you visited."
    if bool(latitude_value) != bool(longitude_value):
        return None, "Enter both approximate coordinates or leave both blank."
    if latitude_value:
        latitude, longitude = _valid_approx_coordinates(latitude_value, longitude_value)
        if latitude is None:
            return None, "Enter valid approximate coordinates."
    else:
        latitude, longitude = None, None
    city, state = resolve_report_region(city, state, place_name)
    return {
        "place_name": place_name,
        "visited_at": visited_at.replace("T", " "),
        "city": city,
        "state": state,
        "approx_lat": latitude,
        "approx_lng": longitude,
        "notes": notes or None,
    }, None


def memory_map_candidates(cur, visits):
    if not visits:
        return []
    cur.execute(
        """SELECT id,item_name,location,event_date,event_time,city,state,
                  approx_lat,approx_lng,status
           FROM items WHERE status IN ('Found','Claim Submitted')
           ORDER BY created_at DESC LIMIT 500"""
    )
    candidates = cur.fetchall()
    matches = {}
    for visit in visits:
        for row in candidates:
            candidate = {
                "id": row[0], "item_name": row[1], "location": row[2],
                "event_date": row[3], "event_time": row[4], "city": row[5],
                "state": row[6], "approx_lat": row[7], "approx_lng": row[8],
                "status": row[9],
            }
            score, reasons = score_memory_map_candidate(visit, candidate)
            if score is None:
                continue
            previous = matches.get(candidate["id"])
            if previous is None or score > previous["score"]:
                matches[candidate["id"]] = {
                    "id": candidate["id"],
                    "item_name": candidate["item_name"],
                    "status": candidate["status"],
                    "score": score,
                    "reasons": reasons,
                    "visit_id": visit["id"],
                    "visit_place": visit["place_name"],
                    "lat": candidate["approx_lat"],
                    "lng": candidate["approx_lng"],
                }
    return sorted(matches.values(), key=lambda match: match["score"], reverse=True)[:10]


def build_public_map_items(records):
    map_items = []
    for record in records:
        coordinates = CITY_CENTROIDS.get(record.get("city"))
        if not coordinates:
            continue
        map_items.append({
            "name": record["name"],
            "status": record["status"],
            "location": public_item_region(record.get("city"), record.get("state")),
            "lat": coordinates[0],
            "lng": coordinates[1],
        })
    return map_items


def _smart_match_score(query, candidate, description_score):
    components = [
        ("Description", description_score, 0.45),
    ]
    if query.get("category") and candidate[2]:
        components.append((
            "Category",
            1.0 if query["category"].casefold() == candidate[2].casefold() else 0.0,
            0.20,
        ))

    metadata_scores = [
        _optional_text_similarity(query.get("color"), candidate[8]),
        _optional_text_similarity(query.get("brand"), candidate[7]),
    ]
    available_metadata = [score for score in metadata_scores if score is not None]
    if available_metadata:
        components.append(("Color / brand", sum(available_metadata) / len(available_metadata), 0.15))

    if query.get("event_date") and candidate[5]:
        try:
            query_date = datetime.strptime(query["event_date"], "%Y-%m-%d").date()
            candidate_date = (
                candidate[5] if hasattr(candidate[5], "year")
                else datetime.strptime(str(candidate[5])[:10], "%Y-%m-%d").date()
            )
            days_apart = abs((query_date - candidate_date).days)
            components.append(("Date proximity", max(0.0, 1.0 - days_apart / 30), 0.10))
        except (TypeError, ValueError):
            pass

    query_city, query_state = resolve_report_region(
        query.get("city"), query.get("state"), query.get("location", "")
    )
    candidate_city, candidate_state = resolve_report_region(
        candidate[13] if len(candidate) > 13 else None,
        candidate[14] if len(candidate) > 14 else None,
        candidate[4],
    )
    city_score = (
        1.0 if query_city and candidate_city and query_city == candidate_city
        else 0.0 if query_city and candidate_city else None
    )
    state_score = (
        1.0 if query_state and candidate_state and query_state == candidate_state
        else 0.0 if query_state and candidate_state else None
    )
    region_scores = [value for value in (city_score, state_score) if value is not None]
    location_similarity = (
        sum(region_scores) / len(region_scores)
        if region_scores else _optional_text_similarity(query.get("location"), candidate[4])
    )
    if query.get("location") and candidate[4] and not region_scores:
        query_tokens = set(re.findall(r"\w+", query["location"].casefold()))
        candidate_tokens = set(re.findall(r"\w+", candidate[4].casefold()))
        token_overlap = len(query_tokens & candidate_tokens) / max(1, len(query_tokens | candidate_tokens))
        location_similarity = max(location_similarity or 0.0, token_overlap)
    distance = None
    query_coords = _valid_approx_coordinates(query.get("approx_lat"), query.get("approx_lng"))
    candidate_coords = _valid_approx_coordinates(
        candidate[15] if len(candidate) > 15 else None,
        candidate[16] if len(candidate) > 16 else None,
    )
    if all(value is not None for value in (*query_coords, *candidate_coords)):
        distance = _distance_km(*query_coords, *candidate_coords)
        location_similarity = max(0.0, 1.0 - distance / 500)
    if location_similarity is not None:
        components.append(("Location proximity", location_similarity, 0.10))

    total_weight = sum(weight for _, _, weight in components)
    score = 100 * sum(value * weight for _, value, weight in components) / total_weight
    explanations = []
    for label, value, _ in components:
        if label == "Description":
            explanations.append(
                f"Description is semantically similar ({round(value * 100)}% text similarity)"
            )
        elif label == "Category" and value:
            explanations.append("Category matches")
        elif label == "Color / brand" and value >= 0.7:
            explanations.append("Color or brand details are similar")
        elif label == "Date proximity" and value >= 0.5:
            explanations.append("Report dates are close")
        elif label == "Location proximity" and (distance is not None or value >= 0.35):
            if distance is not None:
                explanations.append(f"Approximate locations are {round(distance)} km apart")
            elif query_city and candidate_city and query_city == candidate_city:
                explanations.append(f"Same city: {candidate_city}")
            elif query_state and candidate_state and query_state == candidate_state:
                explanations.append(f"Same state: {candidate_state}")
            else:
                explanations.append("Location details overlap")
    return round(score), explanations


def rank_smart_matches(
    cur, query, statuses, limit=5, exclude_ids=(), filters=(), filter_params=(),
    image_upload=None, allow_ai_visual=False,
):
    if not statuses or not query.get("description", "").strip():
        return []
    placeholders = ",".join(["%s"] * len(statuses))
    where_filters = ["status IN (%s)" % placeholders, *filters]
    cur.execute(
        f"""SELECT id,item_name,category,description,location,event_date,status,
                   brand,color,image,user_id,is_anonymous,event_time,
                   city,state,approx_lat,approx_lng
            FROM items WHERE {" AND ".join(where_filters)}
            ORDER BY created_at DESC LIMIT 500""",
        (*statuses, *filter_params),
    )
    rows = [row for row in cur.fetchall() if row[0] not in set(exclude_ids)]
    similarities, method = semantic_description_scores(cur, query["description"], rows)
    ranked = []
    for row in rows:
        score, explanations = _smart_match_score(query, row, similarities[row[0]])
        ranked.append({
            "id": row[0], "name": row[1], "category": row[2], "description": row[3],
            "location": public_item_region(row[13], row[14], row[4]),
            "date": row[5], "status": row[6], "image": row[9],
            "city": resolve_report_region(row[13], row[14], row[4])[0],
            "state": resolve_report_region(row[13], row[14], row[4])[1],
            "user_id": row[10], "score": score, "reasons": explanations,
            "text_method": method,
        })
    ranked = sorted(ranked, key=lambda match: (-match["score"], match["id"]))
    ranked = add_visual_scores(
        image_upload, ranked, allow_ai_visual=allow_ai_visual, visual_weight=0.25,
    )
    return ranked[:limit]


def store_match_notification(cur, user_id, lost_item_id, found_item_id, score, explanation):
    try:
        cur.execute(
            """INSERT OR IGNORE INTO notifications
               (user_id,message,notification_type,item_id,matched_item_id,match_score,explanation)
               VALUES(%s,%s,'match',%s,%s,%s,%s)""",
            (
                user_id,
                f"Possible match for your lost report ({score}% estimated match).",
                lost_item_id,
                found_item_id,
                score,
                explanation,
            ),
        )
        return cur.rowcount > 0
    except Exception:
        app.logger.exception(
            "Unable to save match notification for lost report %s and found report %s",
            lost_item_id,
            found_item_id,
        )
        return False


def store_claim_notification(cur, user_id, message, item_id, claim_id):
    try:
        cur.execute(
            """INSERT INTO notifications(user_id,message,notification_type,item_id,claim_id)
               VALUES(%s,%s,'claim',%s,%s)""",
            (user_id, message, item_id, claim_id),
        )
    except Exception:
        app.logger.exception("Unable to save notification for claim %s", claim_id)


def grant_successful_return_reward(cur, finder_id, item_id, actor_id):
    points = app.config["REWARD_SUCCESSFUL_RETURN_POINTS"]
    cur.execute(
        """INSERT OR IGNORE INTO reward_transactions
           (user_id,contribution_type,recovery_id,points,reason,created_by)
           VALUES(%s,'successful_return',%s,%s,'Verified item return',%s)""",
        (finder_id, item_id, points, actor_id),
    )
    if cur.rowcount == 0:
        return False
    cur.execute(
        """INSERT INTO notifications(user_id,message,notification_type,item_id)
           VALUES(%s,%s,'reward',%s)""",
        (finder_id, f"You earned {points} Good Citizen points for a verified return.", item_id),
    )
    cur.execute(
        """SELECT b.id,b.name FROM reward_badges b
           WHERE b.active=1 AND b.successful_returns<=(
             SELECT COUNT(*) FROM reward_transactions r
             WHERE r.user_id=%s AND r.contribution_type='successful_return' AND r.points>0
               AND NOT EXISTS(
                 SELECT 1 FROM reward_transactions rev
                 WHERE rev.user_id=r.user_id AND rev.recovery_id=r.recovery_id
                   AND rev.contribution_type='reversal'
               )
           )
           AND NOT EXISTS(
             SELECT 1 FROM user_reward_badges ub
             WHERE ub.user_id=%s AND ub.badge_id=b.id
           ) ORDER BY b.successful_returns""",
        (finder_id, finder_id),
    )
    newly_earned = cur.fetchall()
    for badge_id, badge_name in newly_earned:
        cur.execute(
            "INSERT OR IGNORE INTO user_reward_badges(user_id,badge_id) VALUES(%s,%s)",
            (finder_id, badge_id),
        )
        if cur.rowcount:
            cur.execute(
                """INSERT INTO notifications(user_id,message,notification_type,item_id)
                   VALUES(%s,%s,'reward',%s)""",
                (finder_id, f"You unlocked the {badge_name} badge!", item_id),
            )
    return True


def approve_reward_contribution(cur, contribution_id, admin_id):
    cur.execute(
        """SELECT user_id,item_id,contribution_type,status FROM reward_contributions
           WHERE id=%s""",
        (contribution_id,),
    )
    contribution = cur.fetchone()
    if not contribution:
        abort(404)
    if contribution[0] == admin_id:
        abort(403)
    if contribution[3] != "Pending" or contribution[2] != "helpful_clue":
        abort(400)
    cur.execute(
        """UPDATE reward_contributions SET status='Approved',reviewed_by=%s,
                  reviewed_at=CURRENT_TIMESTAMP WHERE id=%s AND status='Pending'""",
        (admin_id, contribution_id),
    )
    if cur.rowcount != 1:
        abort(409)
    points = app.config["REWARD_APPROVED_CLUE_POINTS"]
    cur.execute(
        """INSERT OR IGNORE INTO reward_transactions
           (user_id,contribution_type,contribution_id,points,reason,created_by)
           VALUES(%s,'helpful_clue',%s,%s,'Moderator-approved helpful community clue',%s)""",
        (contribution[0], contribution_id, points, admin_id),
    )
    if cur.rowcount:
        cur.execute(
            """INSERT INTO notifications(user_id,message,notification_type,item_id)
               VALUES(%s,%s,'reward',%s)""",
            (contribution[0], f"Your helpful clue was approved. You earned {points} Good Citizen points.",
             contribution[1]),
        )
    return contribution


def reverse_successful_return_reward(cur, transaction_id, admin_id, reason):
    cur.execute(
        """SELECT user_id,recovery_id,points FROM reward_transactions
           WHERE id=%s AND contribution_type='successful_return' AND points>0""",
        (transaction_id,),
    )
    award = cur.fetchone()
    if not award:
        abort(404)
    user_id, recovery_id, points = award
    if user_id == admin_id:
        abort(403)
    cur.execute(
        """SELECT 1 FROM reward_transactions
           WHERE user_id=%s AND recovery_id=%s AND contribution_type='reversal'""",
        (user_id, recovery_id),
    )
    if cur.fetchone():
        abort(409, description="This return award has already been reversed.")
    cur.execute(
        """SELECT COALESCE(SUM(points),0) FROM reward_transactions WHERE user_id=%s""",
        (user_id,),
    )
    if cur.fetchone()[0] < points:
        abort(400, description="The award cannot be fully reversed without making the balance negative.")
    cur.execute(
        """INSERT OR IGNORE INTO reward_transactions
           (user_id,contribution_type,recovery_id,points,reason,created_by)
           VALUES(%s,'reversal',%s,%s,%s,%s)""",
        (user_id, recovery_id, -points, reason, admin_id),
    )
    if cur.rowcount != 1:
        abort(409, description="This return award has already been reversed.")
    reversal_id = cur.lastrowid
    cur.execute(
        """INSERT INTO notifications(user_id,message,notification_type,item_id)
           VALUES(%s,%s,'reward',%s)""",
        (user_id, f"An administrator reversed {points} points for a verified return after review.",
         recovery_id),
    )
    cur.execute(
        """SELECT COUNT(*) FROM reward_transactions s
           WHERE s.user_id=%s AND s.contribution_type='successful_return' AND s.points>0
             AND NOT EXISTS(
               SELECT 1 FROM reward_transactions r
               WHERE r.user_id=s.user_id AND r.recovery_id=s.recovery_id
                 AND r.contribution_type='reversal'
             )""",
        (user_id,),
    )
    verified_return_count = cur.fetchone()[0]
    cur.execute(
        """SELECT b.name FROM user_reward_badges ub JOIN reward_badges b ON b.id=ub.badge_id
           WHERE ub.user_id=%s AND b.successful_returns>%s""",
        (user_id, verified_return_count),
    )
    revoked_badges = [row[0] for row in cur.fetchall()]
    cur.execute(
        """DELETE FROM user_reward_badges WHERE user_id=%s AND badge_id IN
           (SELECT id FROM reward_badges WHERE successful_returns>%s)""",
        (user_id, verified_return_count),
    )
    for badge_name in revoked_badges:
        cur.execute(
            """INSERT INTO notifications(user_id,message,notification_type,item_id)
               VALUES(%s,%s,'reward',%s)""",
            (user_id, f"The {badge_name} badge was removed after a return reward reversal.",
             recovery_id),
        )
    log_audit(
        cur, "reward_return_reversed", "reward_transaction", reversal_id,
        {"user_id": user_id, "recovery_id": recovery_id, "points": -points,
         "reason": reason, "reversed_transaction_id": transaction_id},
    )
    return user_id


REPORT_ASSISTANT_CATEGORIES = {
    "Electronics": ("phone", "mobile", "laptop", "tablet", "headphone", "earbud", "charger", "camera", "watch", "usb", "pendrive"),
    "Documents": ("document", "passport", "license", "licence", "id card", "identity card", "certificate", "pan card", "aadhaar", "aadhar"),
    "Wallet": ("wallet", "purse"),
    "Keys": ("key", "keys", "keychain"),
    "Bag": ("bag", "backpack", "handbag", "suitcase", "luggage"),
    "Books": ("book", "notebook", "textbook"),
}
REPORT_ASSISTANT_COLORS = (
    "black", "white", "gray", "grey", "silver", "gold", "red", "blue",
    "green", "yellow", "orange", "purple", "pink", "brown", "beige",
)
REPORT_ASSISTANT_BRANDS = (
    "Apple", "Samsung", "OnePlus", "Xiaomi", "Redmi", "Realme", "Oppo",
    "Vivo", "Google", "Dell", "HP", "Lenovo", "Asus", "Acer", "Sony",
    "JBL", "Boat", "Nike", "Adidas", "Puma", "Gucci",
)
REPORT_ASSISTANT_FEATURES = (
    "cracked", "scratched", "dented", "torn", "broken", "engraved",
    "sticker", "initials", "stain", "stained", "patched", "personalized",
    "personalised", "patterned", "stitching", "badge", "mark", "marks",
)
REPORT_ASSISTANT_STOP_WORDS = {
    "about", "after", "again", "also", "and", "any", "around", "because",
    "before", "been", "but", "can", "found", "from",
    "have", "here", "into", "item", "its", "left", "lost", "near", "over",
    "please", "report", "same", "some", "that", "the", "their", "there",
    "this", "through", "very", "was", "were", "what", "when", "where",
    "with", "you", "your",
}


def build_report_assistant_suggestions(description, item_type, category, location,
                                       city, state, event_date):
    text = re.sub(r"\s+", " ", (description or "").strip())
    lowered = text.casefold()
    tokens = re.findall(r"[a-zA-Z0-9][a-zA-Z0-9'-]*", text)
    unique_tokens = list(dict.fromkeys(token.casefold() for token in tokens))
    category_suggestion = None
    for suggested_category, terms in REPORT_ASSISTANT_CATEGORIES.items():
        if any(re.search(rf"\b{re.escape(term)}\b", lowered) for term in terms):
            category_suggestion = suggested_category
            break

    color_suggestion = next(
        (color for color in REPORT_ASSISTANT_COLORS
         if re.search(rf"\b{color}\b", lowered)),
        None,
    )
    brand_suggestion = next(
        (brand for brand in REPORT_ASSISTANT_BRANDS
         if re.search(rf"\b{re.escape(brand.casefold())}\b", lowered)),
        None,
    )
    feature_suggestions = [
        feature for feature in REPORT_ASSISTANT_FEATURES
        if re.search(rf"\b{re.escape(feature)}\b", lowered)
    ]
    item_name = None
    if category_suggestion:
        terms = REPORT_ASSISTANT_CATEGORIES[category_suggestion]
        found_term = next(
            (term for term in terms if re.search(rf"\b{re.escape(term)}\b", lowered)),
            None,
        )
        if found_term:
            item_name = found_term.title()

    keywords = [
        token for token in unique_tokens
        if len(token) > 2 and token not in REPORT_ASSISTANT_STOP_WORDS
    ][:8]
    resolved_city, resolved_state = resolve_report_region(city, state, location)
    missing = []
    if not category or category == "Other":
        missing.append("Choose or confirm an item category.")
    if not location:
        missing.append("Add a broad public-area location; do not enter a private address.")
    if not (resolved_city and resolved_state):
        missing.append("Add the city and state so reports can be found across India.")
    if not event_date:
        missing.append("Add the date the item was lost or found.")
    if not feature_suggestions:
        missing.append("If known, add a distinctive visible feature, mark, or identifying detail.")
    if len(tokens) < 3:
        missing.append("Add a little more description, such as item type and one observed detail.")

    return {
        "item_type": item_type,
        "suggestions": {
            "item_name": item_name,
            "category": category_suggestion,
            "color": color_suggestion,
            "brand": brand_suggestion,
            "features": feature_suggestions,
        },
        "keywords": keywords,
        "missing": missing,
        "review_required": True,
    }


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
        require_csrf_token()
        item_type=request.form.get("item_type", "Lost")
        if item_type not in ("Lost", "Found"):
            abort(400)
        form_data = {
            "item_type": item_type,
            "item_name": request.form.get("item_name", "").strip(),
            "category": request.form.get("category", "Other"),
            "description": request.form.get("description", "").strip(),
            "location": request.form.get("location", "").strip(),
            "city": request.form.get("city", "").strip(),
            "state": request.form.get("state", "").strip(),
            "approx_lat": request.form.get("approx_lat", "").strip(),
            "approx_lng": request.form.get("approx_lng", "").strip(),
            "event_date": request.form.get("event_date", ""),
            "event_time": request.form.get("event_time", ""),
            "brand": request.form.get("brand", "").strip(),
            "color": request.form.get("color", "").strip(),
            "private_details": request.form.get("private_details", "").strip()[:2000],
            "is_anonymous": request.form.get("is_anonymous") == "on",
        }
        if not all(form_data[key] for key in ("item_name", "description", "location", "event_date")):
            flash("Complete the required report fields.")
            return render_template("report.html", form_data=form_data)
        if len(request.form.get("private_details", "").strip()) > 2000:
            flash("Private identifying details must be 2,000 characters or fewer.")
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
        if item_type == "Lost":
            form_data["private_details"] = ""
        form_data["city"], form_data["state"] = resolve_report_region(
            form_data["city"], form_data["state"], form_data["location"]
        )
        form_data["approx_lat"], form_data["approx_lng"] = _valid_approx_coordinates(
            form_data["approx_lat"], form_data["approx_lng"]
        )
        uploaded_photo = request.files.get("image")
        photo_filename, photo_bytes = None, None
        if uploaded_photo and uploaded_photo.filename:
            try:
                photo_filename, photo_bytes = sanitize_item_photo(uploaded_photo)
            except ValueError as error:
                flash(str(error))
                return render_template("report.html", form_data=form_data)
            image = FileStorage(
                stream=BytesIO(photo_bytes),
                filename=photo_filename,
                content_type="image/jpeg",
            )
        else:
            image = None
        allow_ai_visual = request.form.get("ai_photo_consent") == "on"
        cur = mysql.connection.cursor()
        duplicate_items = rank_item_matches(
            cur, form_data, [item_type], image_upload=image,
            allow_ai_visual=allow_ai_visual,
        )
        duplicate_items = [match for match in duplicate_items if match["score"] >= 65]
        match_statuses = ["Found", "Claim Submitted"] if item_type == "Lost" else ["Lost"]
        try:
            possible_matches = rank_smart_matches(
                cur, form_data, match_statuses, image_upload=image,
                allow_ai_visual=allow_ai_visual,
            )
        except Exception:
            app.logger.exception("Smart matching failed while creating report; report submission will continue")
            possible_matches = []
        possible_matches = [match for match in possible_matches if match["score"] >= 35]
        if request.form.get("photo_search_only") == "yes":
            mysql.connection.commit()
            cur.close()
            return render_template(
                "report.html",
                form_data=form_data,
                duplicate_items=duplicate_items,
                possible_matches=possible_matches,
                photo_search_complete=True,
            )
        if (duplicate_items or possible_matches) and request.form.get("confirm_report") != "yes":
            mysql.connection.commit()
            cur.close()
            return render_template("report.html", form_data=form_data,
                                   duplicate_items=duplicate_items,
                                   possible_matches=possible_matches)
        cur.close()
        filename = None
        if photo_bytes is not None:
            filename = photo_filename
            with open(os.path.join(app.config["UPLOAD_FOLDER"], filename), "wb") as saved_photo:
                saved_photo.write(photo_bytes)
        status=item_type
        cur=mysql.connection.cursor()
        cur.execute("""INSERT INTO items
                      (user_id,item_name,category,description,location,event_date,image,status,icon,
                       brand,color,is_anonymous,qr_token,event_time,city,state,approx_lat,approx_lng,
                       private_details)
                      VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (session["user_id"],form_data["item_name"],form_data["category"],form_data["description"],
                     form_data["location"],form_data["event_date"],filename,status,"fa-box",
                       form_data["brand"] or None,form_data["color"] or None,form_data["is_anonymous"],
                       str(uuid.uuid4()) if status == "Found" else None,form_data["event_time"] or None,
                       form_data["city"],form_data["state"],form_data["approx_lat"],form_data["approx_lng"],
                       form_data["private_details"] or None))
        item_id = cur.lastrowid
        log_audit(cur, "item_reported", "item", item_id,
                  {"status": status, "anonymous": form_data["is_anonymous"]})
        match_emails = []
        for match in possible_matches:
            if match["score"] < 50 or match["user_id"] == session["user_id"]:
                continue
            if item_type == "Lost":
                lost_item_id, found_item_id = item_id, match["id"]
                lost_owner_id = session["user_id"]
            else:
                lost_item_id, found_item_id = match["id"], item_id
                lost_owner_id = match["user_id"]
            explanation = "; ".join(match.get("reasons", [])) or "Reports have similar details."
            was_created = store_match_notification(
                cur,
                lost_owner_id,
                lost_item_id,
                found_item_id,
                match["score"],
                explanation,
            )
            if was_created:
                cur.execute("SELECT email FROM users WHERE id=%s", (lost_owner_id,))
                match_email = cur.fetchone()
                if match_email:
                    match_emails.append(
                        (match_email[0], lost_item_id, found_item_id, match["score"])
                    )
        mysql.connection.commit()
        cur.close()
        for match_email, lost_item_id, found_item_id, match_score in match_emails:
            send_email_notification(
                match_email, "Possible FindIt match",
                f"Report #{found_item_id} may match your lost report #{lost_item_id} "
                f"({match_score}% estimated match). Sign in to review the notification.",
            )
        flash(f"{status} item reported successfully.")
        return redirect(url_for("dashboard"))
    report_prefill = {
        "item_type": "Lost",
        "item_name": request.args.get("item_name", "").strip()[:150],
        "category": request.args.get("category", "Other").strip(),
        "description": request.args.get("description", "").strip()[:1000],
        "location": request.args.get("location", "").strip()[:180],
        "city": request.args.get("city", "").strip()[:100],
        "state": request.args.get("state", "").strip()[:100],
        "event_date": request.args.get("event_date", "").strip()[:10],
        "event_time": request.args.get("event_time", "").strip()[:5],
    }
    if report_prefill["category"] not in LOCATION_PREDICTOR_CATEGORIES:
        report_prefill["category"] = "Other"
    return render_template("report.html", form_data=report_prefill)

@app.route("/api/report-assistant", methods=["POST"])
def report_assistant():
    if not logged_in():
        return jsonify(error="Sign in to use report suggestions."), 401
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify(error="Send report details as JSON."), 400
    item_type = str(payload.get("item_type", "Lost"))
    if item_type not in ("Lost", "Found"):
        return jsonify(error="Choose Lost or Found before requesting suggestions."), 400
    description = str(payload.get("description", "")).strip()[:2000]
    if len(re.findall(r"\w+", description)) < 2:
        return jsonify(
            item_type=item_type,
            suggestions={"item_name": None, "category": None, "color": None,
                         "brand": None, "features": []},
            keywords=[],
            missing=["Add a little more description, such as item type and one observed detail."],
            review_required=True,
        )
    try:
        result = build_report_assistant_suggestions(
            description,
            item_type,
            str(payload.get("category", ""))[:80],
            str(payload.get("location", ""))[:200],
            str(payload.get("city", ""))[:100],
            str(payload.get("state", ""))[:100],
            str(payload.get("event_date", ""))[:10],
        )
    except (TypeError, ValueError, re.error):
        app.logger.exception("Report assistant could not analyze a report description")
        return jsonify(error="Suggestions are temporarily unavailable. You can complete the report manually."), 503
    return jsonify(result)


LOCATION_PREDICTOR_CATEGORIES = {
    "Electronics", "Documents", "Wallet", "Keys", "Bag", "Books", "Other",
}
LOCATION_CHECK_STATUSES = {
    "Not checked", "Checked — item not found", "Item found",
    "Contacted — waiting for response",
}
LOCATION_TRAVEL_METHODS = {
    "", "Bus", "Train", "Metro", "Taxi / cab", "Auto-rickshaw",
    "Car", "Flight", "Walk / bicycle", "Other",
}


def build_location_predictions(details):
    item_name = details.get("item_name", "").strip()
    category = details.get("category", "").strip()
    description = details.get("description", "").strip()
    last_location = details.get("last_location", "").strip()
    last_used = details.get("last_used", "").strip()
    places = details.get("places", [])
    travel_method = details.get("travel_method", "").strip()
    item_context = " ".join((item_name, category, description)).casefold()
    context = " ".join((
        item_context,
        last_location.casefold(),
        last_used.casefold(),
        " ".join(place.casefold() for place in places),
        travel_method.casefold(),
    ))

    recommendations = []
    seen_places = set()

    def suggest(place, priority_score, reason, action, general=False):
        key = re.sub(r"\s+", " ", place).strip().casefold()
        if not key or key in seen_places:
            return
        seen_places.add(key)
        priority = "High" if priority_score >= 85 else (
            "Medium" if priority_score >= 60 else "Low"
        )
        recommendations.append({
            "place": place,
            "priority": priority,
            "reason": reason,
            "next_action": action,
            "general": general,
            "status": "Not checked",
            "_rank": priority_score,
        })

    if last_used:
        suggest(
            f"Where you last used it: {last_used}",
            100,
            "You entered this as the last place or activity where you used the item.",
            "Retrace the immediate area and ask the venue's reception or lost-property desk.",
        )
    if last_location:
        suggest(
            f"Last-seen place: {last_location}",
            96,
            "This is the last location you provided before noticing the item was missing.",
            "Check the exact area where you last saw or had it, then contact the venue's reception.",
        )

    for index, place in enumerate(places):
        suggest(
            f"Visited place: {place}",
            max(70, 84 - index * 3),
            "You listed this as a place visited after last seeing the item.",
            "Check the area you visited and ask its reception or lost-property desk.",
        )

    has_transport_context = bool(
        travel_method in {"Bus", "Train", "Metro", "Taxi / cab", "Auto-rickshaw", "Flight"}
        or re.search(r"\b(bus|train|metro|rail|station|taxi|cab|auto|flight|airport)\b", context)
    )
    if has_transport_context:
        suggest(
            "Vehicle or seating area on your route",
            77,
            "Your travel method or description mentions public or hired transport.",
            "If safe and practical, check the seat and nearby storage; do not re-enter a vehicle unsafely.",
            general=True,
        )
        suggest(
            "Transport operator's lost-property office",
            73,
            "Transport is part of the details you provided; operators may have a lost-property process.",
            "Contact the relevant operator with the travel date, approximate time, and route.",
            general=True,
        )

    campus_context = bool(re.search(
        r"\b(college|campus|school|university|class|classroom|lecture|hostel|library)\b",
        context,
    ))
    if campus_context:
        suggest(
            "Reception, security desk, or campus lost-property office",
            72,
            "Your details mention a college, school, campus, classroom, or library.",
            "Ask the reception or security desk whether an item matching your description was handed in.",
            general=True,
        )
        if re.search(r"\b(library|class|classroom|lecture)\b", context):
            suggest(
                "Classroom, library, or study area you used",
                68,
                "Your description mentions a classroom, lecture, or library setting.",
                "Check the seat, desk, and nearby help desk for the specific room or area you visited.",
                general=True,
            )

    venue_context = bool(re.search(
        r"\b(cafe|café|restaurant|shop|store|market|office|workplace)\b", context,
    ))
    if venue_context:
        suggest(
            "Counter or reception at the venue you named",
            66,
            "Your details mention a business or workplace where an item could be handed in.",
            "Contact that venue using its official contact details and ask about its lost-property process.",
            general=True,
        )

    is_wallet_or_document = category in {"Wallet", "Documents"} or bool(re.search(
        r"\b(wallet|purse|id card|identity card|college id|passport|license|licence|card)\b",
        item_context,
    ))
    is_keys = category == "Keys" or bool(re.search(r"\b(keys?|keychain)\b", item_context))
    is_bag = category == "Bag" or bool(re.search(
        r"\b(bag|backpack|handbag|suitcase|luggage)\b", item_context,
    ))
    is_phone = bool(re.search(r"\b(phone|mobile|smartphone)\b", item_context))

    if is_wallet_or_document:
        suggest(
            "Bags, pockets, and the places you handled the item",
            62,
            "Wallets and small documents are commonly kept in a pocket or personal bag; this is a practical checklist, not a measured pattern.",
            "Check each bag and pocket you had on the route, then retrace places where you paid or showed the document.",
            general=True,
        )
    elif is_keys:
        suggest(
            "Bag, pocket, desk, or entry area you used",
            62,
            "Keys are usually carried or set down during entry and exit; these are general places to check.",
            "Check the bags and pockets you carried and the desks or entry areas you actually visited.",
            general=True,
        )
    elif is_bag:
        suggest(
            "Seat, storage area, or reception at places you visited",
            62,
            "A bag can be set down near a seat or handed in at a venue; this is general guidance.",
            "Retrace where you sat or stored belongings, then contact the reception at places you listed.",
            general=True,
        )
    elif is_phone:
        suggest(
            "Last-used surface or charging area",
            62,
            "Phones are often set down during use or charging; check places you actually used or charged it.",
            "Retrace the last activity you mentioned and check its nearby surface or charging point.",
            general=True,
        )

    last_seen = details.get("last_seen", "").strip()
    if last_seen and has_transport_context:
        try:
            seen_at = datetime.strptime(last_seen, "%Y-%m-%dT%H:%M")
            if (datetime.now() - seen_at).total_seconds() > 24 * 60 * 60:
                suggest(
                    "Venue or transport lost-property desk",
                    75,
                    "More than a day has elapsed since the time you entered, so checking formal lost-property desks is a practical next step.",
                    "Contact the venue or transport operator with the date and approximate time.",
                    general=True,
                )
        except ValueError:
            pass

    if not recommendations:
        suggest(
            "Most recent place or activity you can recall",
            30,
            "There is not enough location or item detail to make a specific recommendation.",
            "Add the item type and last-seen place or one place visited, then run the predictor again.",
            general=True,
        )

    recommendations.sort(key=lambda item: (-item["_rank"], item["place"].casefold()))
    for recommendation in recommendations:
        recommendation.pop("_rank", None)

    has_relevant_details = bool(
        last_location or last_used or places or travel_method or category
    )
    return {
        "recommendations": recommendations,
        "insufficient": not has_relevant_details or not (item_name or category or description),
        "engine": "Local rule-based guidance",
        "notice": (
            "Suggestions are estimates based only on the details you entered. They are not confirmed locations, "
            "AI-verified findings, or statistically proven probabilities."
        ),
    }


def _location_predictor_form_data(form):
    places = form.getlist("places")
    if len(places) > 8:
        raise ValueError("Add no more than 8 visited places.")
    if any(len(place.strip()) > 180 for place in places):
        raise ValueError("Each visited place must be 180 characters or fewer.")
    travel_method = form.get("travel_method", "").strip()
    if travel_method not in LOCATION_TRAVEL_METHODS:
        raise ValueError("Choose a listed travel method.")
    category = form.get("category", "").strip()
    if category and category not in LOCATION_PREDICTOR_CATEGORIES:
        raise ValueError("Choose a valid item category.")
    last_seen = form.get("last_seen", "").strip()
    if last_seen:
        try:
            datetime.strptime(last_seen, "%Y-%m-%dT%H:%M")
        except ValueError as exc:
            raise ValueError("Enter a valid approximate last-seen date and time.") from exc
    values = {
        "item_name": form.get("item_name", "").strip(),
        "category": category,
        "description": form.get("description", "").strip(),
        "last_seen": last_seen,
        "last_location": form.get("last_location", "").strip(),
        "places": [place.strip() for place in places if place.strip()],
        "travel_method": travel_method,
        "last_used": form.get("last_used", "").strip(),
        "city": form.get("city", "").strip(),
        "state": form.get("state", "").strip(),
    }
    limits = {
        "item_name": 150, "description": 1000, "last_location": 180,
        "last_used": 300, "city": 100, "state": 100,
    }
    for key, limit in limits.items():
        if len(values[key]) > limit:
            raise ValueError(f"{key.replace('_', ' ').capitalize()} must be {limit} characters or fewer.")
    return values


def rank_detective_suggestions_with_gemini(details, recommendations):
    if not app.config["GEMINI_API_KEY"]:
        raise ValueError("Gemini is not configured")

    candidates = [
        {"index": index, "place": item["place"], "reason": item["reason"]}
        for index, item in enumerate(recommendations)
    ]
    prompt = (
        "You are helping rank a user's lost-item search checklist. Use only the "
        "user-provided information and the candidate list. Do not invent locations, "
        "businesses, addresses, movements, or facts. Do not remove candidates. "
        "Return JSON only in the form {\"order\":[integer candidate indexes]}, "
        "listing each supplied index exactly once. Rank by fit to the user's details. "
        "The application will preserve its own High/Medium/Low priority bands.\n"
        f"User details: {json.dumps(details, ensure_ascii=False)}\n"
        f"Candidates: {json.dumps(candidates, ensure_ascii=False)}"
    )
    payload = json.dumps({
        "model": app.config["GEMINI_MODEL"],
        "messages": [
            {
                "role": "system",
                "content": "Rank only the provided candidate checklist. Return valid JSON only.",
            },
            {"role": "user", "content": prompt},
        ],
        "max_tokens": 300,
        "response_format": {"type": "json_object"},
    }).encode("utf-8")
    api_request = urllib.request.Request(
        "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
        data=payload,
        headers={
            "Authorization": "Bearer " + app.config["GEMINI_API_KEY"],
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(api_request, timeout=12) as response:
        result = json.loads(response.read().decode("utf-8"))

    content = result["choices"][0]["message"]["content"]
    parsed = json.loads(content)
    order = parsed.get("order") if isinstance(parsed, dict) else None
    expected = set(range(len(recommendations)))
    if (
        not isinstance(order, list)
        or len(order) != len(recommendations)
        or any(type(index) is not int for index in order)
        or set(order) != expected
    ):
        raise ValueError("Gemini returned an invalid candidate ranking")

    priority_order = {"High": 0, "Medium": 1, "Low": 2}
    model_order = {candidate_index: rank for rank, candidate_index in enumerate(order)}
    candidate_order = {
        id(recommendations[candidate_index]): rank
        for candidate_index, rank in model_order.items()
    }
    return sorted(
        recommendations,
        key=lambda item: (
            priority_order.get(item["priority"], 3),
            candidate_order[id(item)],
        ),
    )


@app.route("/location-predictor", methods=["GET", "POST"])
def location_predictor():
    cur = mysql.connection.cursor()
    predictor_input = {}
    recommendations = []
    notice = None
    error = None
    insufficient = False
    saved_plan_id = None
    predictor_prefilled = False

    if request.method == "POST":
        require_csrf_token()
        try:
            predictor_input = _location_predictor_form_data(request.form)
            prediction = build_location_predictions(predictor_input)
            recommendations = prediction["recommendations"]
            notice = prediction["notice"]
            insufficient = prediction["insufficient"]
            if request.form.get("save_plan") == "on":
                if not logged_in():
                    error = "Sign in to save this search plan. Your details were not saved."
                else:
                    plan = {"inputs": predictor_input, "recommendations": recommendations}
                    cur.execute(
                        """INSERT INTO location_prediction_plans(user_id,plan_json)
                           VALUES(%s,%s)""",
                        (session["user_id"], json.dumps(plan, ensure_ascii=False)),
                    )
                    saved_plan_id = cur.lastrowid
                    mysql.connection.commit()
                    cur.close()
                    return redirect(url_for("location_predictor", saved=saved_plan_id))
        except ValueError as exc:
            error = str(exc)
        except (TypeError, sqlite3.Error):
            mysql.connection.rollback()
            app.logger.exception("Unable to create the lost-item location search plan")
            error = "The prediction could not be generated. Please check your entries and try again."

    prefill_item_id = request.args.get("item_id", "").strip()
    if request.method == "GET" and prefill_item_id:
        if not logged_in():
            cur.close()
            abort(403)
        try:
            prefill_item_id = int(prefill_item_id)
        except ValueError:
            cur.close()
            abort(404)
        cur.execute(
            """SELECT item_name,category,description,event_date,event_time,location,city,state
               FROM items WHERE id=%s AND user_id=%s AND status='Lost'""",
            (prefill_item_id, session["user_id"]),
        )
        report = cur.fetchone()
        if not report:
            cur.close()
            abort(404)
        cur.execute(
            """SELECT place_name FROM journey_places
               WHERE item_id=%s AND user_id=%s ORDER BY visited_at,id LIMIT 8""",
            (prefill_item_id, session["user_id"]),
        )
        visited_places = [row[0] for row in cur.fetchall()]
        event_date = str(report[3] or "")
        event_time = str(report[4] or "")[:5]
        predictor_input = {
            "item_name": report[0] or "",
            "category": report[1] or "",
            "description": report[2] or "",
            "last_seen": f"{event_date}T{event_time}" if event_date and event_time else "",
            "last_location": report[5] or "",
            "places": visited_places,
            "travel_method": "",
            "last_used": "",
            "city": report[6] or "",
            "state": report[7] or "",
        }
        predictor_prefilled = True

    requested_saved_id = request.args.get("saved", "").strip()
    if requested_saved_id:
        if not logged_in():
            cur.close()
            abort(403)
        try:
            requested_saved_id = int(requested_saved_id)
        except ValueError:
            cur.close()
            abort(404)
        cur.execute(
            """SELECT plan_json FROM location_prediction_plans
               WHERE id=%s AND user_id=%s""",
            (requested_saved_id, session["user_id"]),
        )
        saved_row = cur.fetchone()
        if not saved_row:
            cur.close()
            abort(404)
        try:
            saved_plan = json.loads(saved_row[0])
            predictor_input = saved_plan["inputs"]
            recommendations = saved_plan["recommendations"]
            saved_plan_id = requested_saved_id
            notice = (
                "This is your private saved plan. Suggestions are estimates, not confirmed locations "
                "or statistically proven probabilities."
            )
        except (json.JSONDecodeError, KeyError, TypeError):
            cur.close()
            app.logger.exception("Saved location predictor plan %s is invalid", requested_saved_id)
            abort(500)

    saved_plans = []
    if logged_in():
        cur.execute(
            """SELECT id,plan_json,created_at FROM location_prediction_plans
               WHERE user_id=%s ORDER BY created_at DESC,id DESC LIMIT 10""",
            (session["user_id"],),
        )
        for plan_id, plan_json, created_at in cur.fetchall():
            try:
                plan_data = json.loads(plan_json)
                saved_plans.append({
                    "id": plan_id,
                    "item_name": plan_data.get("inputs", {}).get("item_name") or "Unspecified item",
                    "city": plan_data.get("inputs", {}).get("city", ""),
                    "state": plan_data.get("inputs", {}).get("state", ""),
                    "created_at": created_at,
                })
            except (json.JSONDecodeError, AttributeError):
                app.logger.warning("Skipping malformed saved location predictor plan %s", plan_id)
    cur.close()
    return render_template(
        "location_predictor.html",
        predictor_input=predictor_input,
        recommendations=recommendations,
        notice=notice,
        error=error,
        insufficient=insufficient,
        saved_plan_id=saved_plan_id,
        saved_plans=saved_plans,
        check_statuses=sorted(LOCATION_CHECK_STATUSES),
        predictor_prefilled=predictor_prefilled,
    )


@app.route("/lost-item-detective", methods=["GET", "POST"])
def lost_item_detective():
    cur = mysql.connection.cursor()
    detective_input = {}
    recommendations = []
    notice = None
    error = None
    insufficient = False
    saved_plan_id = None
    investigation_complete = False
    ai_used = False
    ai_error = None
    recommendation_method = "Local rule-based guidance"
    source_item_id = None
    lost_reports = []
    saved_plans = []

    if request.method == "POST":
        require_csrf_token()
        source_item_id = request.form.get("source_item_id", "").strip() or None
        if source_item_id:
            if not logged_in():
                cur.close()
                abort(403)
            try:
                source_item_id = int(source_item_id)
            except ValueError:
                cur.close()
                abort(400)
            cur.execute(
                """SELECT id FROM items
                   WHERE id=%s AND user_id=%s AND status='Lost'""",
                (source_item_id, session["user_id"]),
            )
            if not cur.fetchone():
                cur.close()
                abort(404)

        try:
            detective_input = _location_predictor_form_data(request.form)
            prediction = build_location_predictions(detective_input)
            recommendations = prediction["recommendations"]
            notice = prediction["notice"]
            insufficient = prediction["insufficient"]
            if request.form.get("use_ai_ranking") == "on":
                if not app.config["GEMINI_API_KEY"]:
                    ai_error = "Gemini is not configured; the local rule-based ranking is shown."
                else:
                    try:
                        recommendations = rank_detective_suggestions_with_gemini(
                            detective_input, recommendations
                        )
                        ai_used = True
                        recommendation_method = (
                            "Local rules with Gemini tie-breaking within priority levels"
                        )
                    except (
                        urllib.error.URLError, TimeoutError, OSError,
                        json.JSONDecodeError, KeyError, TypeError, ValueError,
                    ) as exc:
                        app.logger.warning(
                            "Gemini ranking unavailable for lost-item detective (%s)",
                            type(exc).__name__,
                        )
                        ai_error = (
                            "Gemini could not rank suggestions. The local rule-based "
                            "ranking is shown instead."
                        )
            investigation_complete = True
            if request.form.get("save_plan") == "on":
                if not logged_in():
                    error = "Sign in to save this investigation. Your answers were not saved."
                else:
                    plan = {
                        "kind": "detective",
                        "source_item_id": source_item_id,
                        "inputs": detective_input,
                        "recommendations": recommendations,
                        "recommendation_method": recommendation_method,
                    }
                    cur.execute(
                        """INSERT INTO location_prediction_plans(user_id,plan_json)
                           VALUES(%s,%s)""",
                        (session["user_id"], json.dumps(plan, ensure_ascii=False)),
                    )
                    saved_plan_id = cur.lastrowid
                    mysql.connection.commit()
                    cur.close()
                    return redirect(url_for("lost_item_detective", saved=saved_plan_id))
        except ValueError as exc:
            error = str(exc)
        except (TypeError, sqlite3.Error):
            mysql.connection.rollback()
            app.logger.exception("Unable to generate the lost-item detective search plan")
            error = "The investigation could not be completed. Please review your answers and try again."

    selected_item_id = request.args.get("item_id", "").strip()
    if request.method == "GET" and selected_item_id:
        if not logged_in():
            cur.close()
            abort(403)
        try:
            selected_item_id = int(selected_item_id)
        except ValueError:
            cur.close()
            abort(404)
        cur.execute(
            """SELECT item_name,category,description,event_date,event_time,location,city,state
               FROM items WHERE id=%s AND user_id=%s AND status='Lost'""",
            (selected_item_id, session["user_id"]),
        )
        report = cur.fetchone()
        if not report:
            cur.close()
            abort(404)
        cur.execute(
            """SELECT place_name FROM journey_places
               WHERE item_id=%s AND user_id=%s ORDER BY visited_at,id LIMIT 8""",
            (selected_item_id, session["user_id"]),
        )
        detective_input = {
            "item_name": report[0] or "",
            "category": report[1] or "",
            "description": report[2] or "",
            "last_seen": (
                f"{report[3]}T{str(report[4])[:5]}"
                if report[3] and report[4] else ""
            ),
            "last_location": report[5] or "",
            "places": [row[0] for row in cur.fetchall()],
            "travel_method": "",
            "last_used": "",
            "city": report[6] or "",
            "state": report[7] or "",
        }
        source_item_id = selected_item_id

    requested_saved_id = request.args.get("saved", "").strip()
    if requested_saved_id:
        if not logged_in():
            cur.close()
            abort(403)
        try:
            requested_saved_id = int(requested_saved_id)
        except ValueError:
            cur.close()
            abort(404)
        cur.execute(
            """SELECT plan_json FROM location_prediction_plans
               WHERE id=%s AND user_id=%s""",
            (requested_saved_id, session["user_id"]),
        )
        saved_row = cur.fetchone()
        if not saved_row:
            cur.close()
            abort(404)
        try:
            saved_plan = json.loads(saved_row[0])
            if saved_plan.get("kind") != "detective":
                cur.close()
                abort(404)
            detective_input = saved_plan["inputs"]
            recommendations = saved_plan["recommendations"]
            recommendation_method = saved_plan.get(
                "recommendation_method", "Local rule-based guidance"
            )
            ai_used = "Gemini" in recommendation_method
            source_item_id = saved_plan.get("source_item_id")
            saved_plan_id = requested_saved_id
            investigation_complete = True
            notice = (
                "This is your private saved investigation. Suggestions are estimates, "
                "not confirmed locations or statistically proven probabilities."
            )
        except (json.JSONDecodeError, KeyError, TypeError):
            cur.close()
            app.logger.exception("Saved detective investigation %s is invalid", requested_saved_id)
            abort(500)

    if logged_in():
        cur.execute(
            """SELECT id,item_name,event_date,location,city,state FROM items
               WHERE user_id=%s AND status='Lost' ORDER BY created_at DESC,id DESC""",
            (session["user_id"],),
        )
        lost_reports = [
            {
                "id": row[0], "item_name": row[1] or "Unspecified item",
                "event_date": row[2] or "", "location": row[3] or "",
                "city": row[4] or "", "state": row[5] or "",
            }
            for row in cur.fetchall()
        ]
        cur.execute(
            """SELECT id,plan_json,created_at FROM location_prediction_plans
               WHERE user_id=%s ORDER BY created_at DESC,id DESC LIMIT 20""",
            (session["user_id"],),
        )
        for plan_id, plan_json, created_at in cur.fetchall():
            try:
                plan_data = json.loads(plan_json)
                if plan_data.get("kind") == "detective":
                    plan_details = plan_data.get("inputs", {})
                    saved_plans.append({
                        "id": plan_id,
                        "item_name": plan_details.get("item_name") or "Unspecified item",
                        "created_at": created_at,
                    })
            except (json.JSONDecodeError, AttributeError):
                app.logger.warning("Skipping malformed saved location plan %s", plan_id)

    cur.close()
    return render_template(
        "lost_item_detective.html",
        detective_input=detective_input,
        recommendations=recommendations,
        notice=notice,
        error=error,
        insufficient=insufficient,
        saved_plan_id=saved_plan_id,
        saved_plans=saved_plans,
        lost_reports=lost_reports,
        source_item_id=source_item_id,
        investigation_complete=investigation_complete,
        ai_used=ai_used,
        ai_error=ai_error,
        has_gemini=bool(app.config["GEMINI_API_KEY"]),
        recommendation_method=recommendation_method,
        check_statuses=sorted(LOCATION_CHECK_STATUSES),
    )


@app.route(
    "/location-predictor/<int:plan_id>/check/<int:step_index>",
    methods=["POST"],
)
def update_location_prediction_step(plan_id, step_index):
    if not logged_in():
        return redirect(url_for("login"))
    require_csrf_token()
    status = request.form.get("status", "")
    if status not in LOCATION_CHECK_STATUSES:
        abort(400)
    cur = mysql.connection.cursor()
    cur.execute(
        """SELECT plan_json FROM location_prediction_plans
           WHERE id=%s AND user_id=%s""",
        (plan_id, session["user_id"]),
    )
    row = cur.fetchone()
    if not row:
        cur.close()
        abort(404)
    try:
        plan = json.loads(row[0])
        if step_index < 0 or step_index >= len(plan["recommendations"]):
            cur.close()
            abort(404)
        plan["recommendations"][step_index]["status"] = status
    except (json.JSONDecodeError, KeyError, TypeError):
        cur.close()
        app.logger.exception("Saved location predictor plan %s is invalid", plan_id)
        abort(500)
    cur.execute(
        "UPDATE location_prediction_plans SET plan_json=%s WHERE id=%s AND user_id=%s",
        (json.dumps(plan, ensure_ascii=False), plan_id, session["user_id"]),
    )
    mysql.connection.commit()
    cur.close()
    destination = (
        "lost_item_detective"
        if plan.get("kind") == "detective"
        else "location_predictor"
    )
    return redirect(url_for(destination, saved=plan_id))


@app.route("/location-predictor/<int:plan_id>/delete", methods=["POST"])
def delete_location_prediction_plan(plan_id):
    if not logged_in():
        return redirect(url_for("login"))
    require_csrf_token()
    cur = mysql.connection.cursor()
    cur.execute(
        "SELECT plan_json FROM location_prediction_plans WHERE id=%s AND user_id=%s",
        (plan_id, session["user_id"]),
    )
    row = cur.fetchone()
    if not row:
        cur.close()
        abort(404)
    destination = "location_predictor"
    try:
        if json.loads(row[0]).get("kind") == "detective":
            destination = "lost_item_detective"
    except (json.JSONDecodeError, AttributeError):
        app.logger.warning("Deleting malformed saved location plan %s", plan_id)
    cur.execute(
        "DELETE FROM location_prediction_plans WHERE id=%s AND user_id=%s",
        (plan_id, session["user_id"]),
    )
    mysql.connection.commit()
    cur.close()
    flash("Saved search plan deleted.")
    return redirect(url_for(destination))


@app.route("/items")
def items():
    q = request.args.get("q", "").strip()[:300]
    category = request.args.get("category", "").strip()
    status = request.args.get("status", "").strip()
    location = request.args.get("location", "").strip()[:200]
    neighborhood = request.args.get("neighborhood", "").strip()[:100]
    city_values = list(dict.fromkeys(
        normalize_city(value.strip())
        for value in request.args.get("city", "").split(",")[:10]
        if value.strip()
    ))
    state_values = list(dict.fromkeys(
        normalize_state(value.strip())
        for value in request.args.get("state", "").split(",")[:10]
        if value.strip()
    ))
    city = ", ".join(city_values)
    state = ", ".join(state_values)
    date_from = request.args.get("date_from", "").strip()
    date_to = request.args.get("date_to", "").strip()
    sort = request.args.get(
        "sort", "score" if request.args.get("smart") == "1" and q else "newest"
    )
    try:
        page = max(1, min(int(request.args.get("page", "1")), 100000))
    except ValueError:
        page = 1
    per_page = 20
    smart_mode = request.args.get("smart") == "1" and bool(q)
    allowed_statuses = ("Lost", "Found", "Claim Submitted", "Verification", "Returned", "Closed", "Recovered")
    if status and status not in (("Lost", "Found", "Claim Submitted") if smart_mode else allowed_statuses):
        status = ""
    if category and category not in ("Electronics", "Documents", "Wallet", "Keys", "Bag", "Books", "Other"):
        category = ""
    for field, value in (("date_from", date_from), ("date_to", date_to)):
        if value:
            try:
                datetime.strptime(value, "%Y-%m-%d")
            except ValueError:
                if field == "date_from":
                    date_from = ""
                else:
                    date_to = ""
    if date_from and date_to and date_from > date_to:
        date_from, date_to = date_to, date_from

    filters = []
    filter_params = []
    if category:
        filters.append("category=%s")
        filter_params.append(category)
    if status and not smart_mode:
        filters.append("status=%s")
        filter_params.append(status)
    if city_values:
        city_conditions = []
        city_params = []
        for selected_city in city_values:
            city_search_values = [selected_city, *CITY_ALIASES.get(selected_city, ())]
            city_conditions.extend("lower(city)=%s" for _ in city_search_values)
            city_params.extend(value.casefold() for value in city_search_values)
            city_conditions.extend("lower(location) LIKE %s" for _ in city_search_values)
            city_params.extend(f"%{value.casefold()}%" for value in city_search_values)
        filters.append("(" + " OR ".join(city_conditions) + ")")
        filter_params.extend(city_params)
    if state_values:
        state_conditions = []
        state_params = []
        for selected_state in state_values:
            state_search_values = [selected_state, *STATE_ALIASES.get(selected_state, ())]
            state_conditions.extend("lower(state)=%s" for _ in state_search_values)
            state_params.extend(value.casefold() for value in state_search_values)
            state_conditions.extend("lower(location) LIKE %s" for _ in state_search_values)
            state_params.extend(f"%{value.casefold()}%" for value in state_search_values)
        filters.append("(" + " OR ".join(state_conditions) + ")")
        filter_params.extend(state_params)
    if date_from:
        filters.append("event_date >= %s")
        filter_params.append(date_from)
    if date_to:
        filters.append("event_date <= %s")
        filter_params.append(date_to)
    if location:
        filters.append("(location LIKE %s OR city LIKE %s OR state LIKE %s)")
        pattern = f"%{location}%"
        filter_params.extend([pattern, pattern, pattern])
    if neighborhood:
        filters.append("(location LIKE %s OR description LIKE %s)")
        filter_params.extend([f"%{neighborhood}%", f"%{neighborhood}%"])
    if q and not smart_mode:
        for keyword in q.split()[:10]:
            filters.append(
                "(item_name LIKE %s OR brand LIKE %s OR color LIKE %s OR "
                "description LIKE %s OR city LIKE %s OR state LIKE %s OR location LIKE %s)"
            )
            pattern = f"%{keyword}%"
            filter_params.extend([pattern] * 7)

    cur = mysql.connection.cursor()
    candidate_select = """SELECT id,item_name,category,description,location,event_date,status,
                                 brand,color,image,user_id,is_anonymous,event_time,
                                 city,state,approx_lat,approx_lng
                          FROM items"""
    where_clause = " WHERE " + " AND ".join(filters) if filters else ""
    if smart_mode:
        match_statuses = {
            "Lost": ("Found", "Claim Submitted"),
            "Found": ("Lost",),
            "Claim Submitted": ("Lost",),
            "": ("Lost", "Found", "Claim Submitted"),
        }[status]
        query = {
            "description": q,
            "category": category,
            "city": city_values[0] if len(city_values) == 1 else "",
            "state": state_values[0] if len(state_values) == 1 else "",
            "location": location or neighborhood,
            "approx_lat": request.args.get("approx_lat"),
            "approx_lng": request.args.get("approx_lng"),
            "event_date": date_from or date_to,
        }
        if date_from and date_to:
            try:
                start = datetime.strptime(date_from, "%Y-%m-%d").date()
                end = datetime.strptime(date_to, "%Y-%m-%d").date()
                query["event_date"] = (start + (end - start) / 2).isoformat()
            except ValueError:
                pass
        ranked = rank_smart_matches(
            cur,
            query,
            match_statuses,
            limit=500,
            filters=filters,
            filter_params=filter_params,
        )
        mysql.connection.commit()
        if sort == "newest":
            ranked.sort(key=lambda item: (str(item["date"]), item["id"]), reverse=True)
        elif sort == "oldest":
            ranked.sort(key=lambda item: (str(item["date"]), item["id"]))
        elif sort != "score":
            sort = "score"
        total_results = len(ranked)
        total_pages = max(1, (total_results + per_page - 1) // per_page)
        page = min(page, total_pages)
        smart_matches = ranked[(page - 1) * per_page:page * per_page]
        items = []
    else:
        order_by = {
            "newest": "event_date DESC,id DESC",
            "oldest": "event_date ASC,id ASC",
            "score": "event_date DESC,id DESC",
        }.get(sort, "event_date DESC,id DESC")
        cur.execute("SELECT COUNT(*) FROM items" + where_clause, tuple(filter_params))
        total_results = cur.fetchone()[0]
        total_pages = max(1, (total_results + per_page - 1) // per_page)
        page = min(page, total_pages)
        cur.execute(
            candidate_select + where_clause + f" ORDER BY {order_by} LIMIT %s OFFSET %s",
            (*filter_params, per_page, (page - 1) * per_page),
        )
        rows = cur.fetchall()
        items = [{
            "id": row[0], "name": row[1], "category": row[2], "description": row[3],
            "location": public_item_region(row[13], row[14], row[4]),
            "date": row[5], "status": row[6], "image": row[9],
            "user_id": row[10], "city": resolve_report_region(row[13], row[14], row[4])[0],
            "state": resolve_report_region(row[13], row[14], row[4])[1],
        } for row in rows]
        smart_matches = []
    cur.close()
    total_pages = max(1, (total_results + per_page - 1) // per_page)
    page = min(page, total_pages)
    visible_records = smart_matches if smart_mode else items
    map_items = build_public_map_items(visible_records)
    pagination_args = request.args.to_dict()
    previous_url = url_for("items", **{**pagination_args, "page": page - 1}) if page > 1 else ""
    next_url = url_for("items", **{**pagination_args, "page": page + 1}) if page < total_pages else ""
    return render_template(
        "items.html", items=items, q=q, category=category, status=status,
        location=location, neighborhood=neighborhood, city=city, state=state,
        date_from=date_from, date_to=date_to, sort=sort, page=page,
        total_pages=total_pages, total_results=total_results, per_page=per_page,
        smart_mode=smart_mode, smart_matches=smart_matches, map_items=map_items,
        previous_url=previous_url, next_url=next_url,
    )

@app.route("/item/<int:item_id>")
def item_detail(item_id):
    cur = mysql.connection.cursor()
    cur.execute(
        """SELECT i.id, i.user_id, i.item_name, i.category, i.description, i.location,
                  i.event_date, i.image, i.status, i.icon,
                  CASE WHEN i.is_anonymous=1 THEN 'Anonymous' ELSE u.name END AS reporter,
                  i.city,i.state,i.private_details,i.created_at
           FROM items i JOIN users u ON i.user_id=u.id WHERE i.id=%s""",
        (item_id,),
    )
    item = cur.fetchone()
    cur.close()
    if not item:
        return "Item not found", 404
    recovery_timeline = []
    viewer_id = session.get("user_id")
    can_review_claims = has_role("admin", "staff", "security")
    is_report_owner = logged_in() and item[1] == viewer_id
    timeline_cur = mysql.connection.cursor() if logged_in() else None
    if logged_in() and (is_report_owner or can_review_claims):
        timeline_cur.execute(
            """SELECT action,details,created_at FROM audit_logs
               WHERE entity_type='item' AND entity_id=%s ORDER BY created_at,id""",
            (item_id,),
        )
        for action, raw_details, created_at in timeline_cur.fetchall():
            try:
                details = json.loads(raw_details or "{}")
            except json.JSONDecodeError:
                app.logger.warning("Ignoring malformed recovery timeline details for item %s", item_id)
                details = {}
            if not isinstance(details, dict):
                details = {}
            if action == "item_reported":
                title = "Reported"
                description = f"{details.get('status', item[8])} report created."
            elif action == "item_status_changed":
                status = details.get("to", "")
                title = "Ownership Verified" if status == "Verification" else status
                description = f"Report status changed from {details.get('from', 'unknown')} to {title}."
            elif action == "item_handed_over":
                title = "Returned"
                description = "Handover was confirmed and recorded."
            else:
                continue
            recovery_timeline.append({
                "title": title,
                "description": description,
                "timestamp": created_at,
                "source": "report",
            })
        if not any(
            event["source"] == "report" and event["title"] == "Reported"
            for event in recovery_timeline
        ):
            recovery_timeline.append({
                "title": "Reported",
                "description": f"{item[8]} report created.",
                "timestamp": item[14],
                "source": "report",
            })

    can_view_claim_progress = logged_in() and (
        is_report_owner or can_review_claims
    )
    if can_view_claim_progress:
        timeline_cur.execute(
            """SELECT c.id,c.user_id,c.status,c.created_at,u.name
               FROM claims c JOIN users u ON u.id=c.user_id
               WHERE c.item_id=%s ORDER BY c.created_at,c.id""",
            (item_id,),
        )
        claim_rows = timeline_cur.fetchall()
    elif logged_in():
        timeline_cur.execute(
            """SELECT c.id,c.user_id,c.status,c.created_at,u.name
               FROM claims c JOIN users u ON u.id=c.user_id
               WHERE c.item_id=%s AND c.user_id=%s ORDER BY c.created_at,c.id""",
            (item_id, viewer_id),
        )
        claim_rows = timeline_cur.fetchall()
    else:
        claim_rows = []

    if claim_rows:
        for claim_id, claimant_id, claim_status, created_at, claimant_name in claim_rows:
            who = (
                f" ({claimant_name})"
                if can_view_claim_progress and claimant_id != viewer_id else ""
            )
            recovery_timeline.append({
                "title": "Claim Submitted",
                "description": f"Private ownership evidence was submitted{who}.",
                "timestamp": created_at,
                "source": "claim",
            })
            timeline_cur.execute(
                """SELECT action,details,created_at FROM audit_logs
                   WHERE entity_type='claim' AND entity_id=%s ORDER BY created_at,id""",
                (claim_id,),
            )
            for action, raw_details, event_time in timeline_cur.fetchall():
                try:
                    details = json.loads(raw_details or "{}")
                except json.JSONDecodeError:
                    app.logger.warning("Ignoring malformed timeline details for claim %s", claim_id)
                    details = {}
                if not isinstance(details, dict):
                    details = {}
                if action == "claim_status_changed":
                    status = details.get("to", claim_status)
                elif action == "claim_auto_rejected":
                    status = "Rejected"
                else:
                    continue
                label = "Ownership Verified" if status == "Approved" else status
                recovery_timeline.append({
                    "title": label,
                    "description": f"Claim status changed to {label}{who}.",
                    "timestamp": event_time,
                    "source": "claim",
                })

    if logged_in() and is_report_owner:
        timeline_cur.execute(
            """SELECT n.match_score,n.explanation,n.created_at,i.item_name,i.id
               FROM notifications n JOIN items i ON i.id=n.matched_item_id
               WHERE n.user_id=%s AND n.notification_type='match' AND n.item_id=%s
               ORDER BY n.created_at,n.id""",
            (viewer_id, item_id),
        )
        for score, explanation, created_at, candidate_name, candidate_id in timeline_cur.fetchall():
            recovery_timeline.append({
                "title": "Possible Match",
                "description": (
                    f"{candidate_name} was suggested ({score}% estimated match). "
                    f"{explanation or 'Reports have similar details.'}"
                ),
                "timestamp": created_at,
                "source": "match",
                "url": url_for("item_detail", item_id=candidate_id),
            })
    claim_titles = {
        event["title"] for event in recovery_timeline
        if event["source"] == "claim"
    }
    recovery_timeline = [
        event for event in recovery_timeline
        if not (event["source"] == "report" and event["title"] in claim_titles)
    ]
    if timeline_cur:
        timeline_cur.close()

    recovery_timeline.sort(key=lambda event: str(event["timestamp"] or ""))
    memory_places = []
    memory_matches = []
    memory_map_points = []
    can_manage_memory_map = is_report_owner and item[8] in (
        "Lost", "Closed", "Recovered"
    )
    if can_manage_memory_map:
        memory_cur = mysql.connection.cursor()
        memory_cur.execute(
            """SELECT id,place_name,city,state,visited_at,approx_lat,approx_lng,notes
               FROM journey_places WHERE item_id=%s AND user_id=%s
               ORDER BY visited_at,id""",
            (item_id, viewer_id),
        )
        for row in memory_cur.fetchall():
            visit = {
                "id": row[0], "place_name": row[1], "city": row[2],
                "state": row[3], "visited_at": str(row[4]),
                "approx_lat": row[5], "approx_lng": row[6], "notes": row[7],
            }
            memory_places.append(visit)
            if row[5] is not None and row[6] is not None:
                memory_map_points.append({
                    "kind": "visit",
                    "name": row[1],
                    "region": public_item_region(row[2], row[3]),
                    "lat": row[5],
                    "lng": row[6],
                })
        if item[8] == "Lost":
            memory_matches = memory_map_candidates(memory_cur, memory_places)
        for match in memory_matches:
            if match["lat"] is not None and match["lng"] is not None:
                memory_map_points.append({
                    "kind": "report",
                    "name": match["item_name"],
                    "region": match["status"],
                    "lat": match["lat"],
                    "lng": match["lng"],
                })
        memory_cur.close()
    return render_template(
        "item_detail.html",
        item=item,
        public_location=public_item_region(item[11], item[12], item[5]),
        recovery_timeline=recovery_timeline,
        show_recovery_timeline=bool(recovery_timeline) and logged_in(),
        memory_places=memory_places,
        memory_matches=memory_matches,
        memory_map_points=memory_map_points,
        memory_map_enabled=bool(app.config["MAP_TILE_URL"] and memory_map_points),
        map_tile_url=app.config["MAP_TILE_URL"],
        can_manage_memory_map=can_manage_memory_map,
        can_create_search_mission=is_report_owner and item[8] == "Lost",
        private_details=(
            item[13] if logged_in() and (
                item[1] == session["user_id"]
                or has_role("admin", "staff", "security")
            ) else None
        ),
    )

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
    require_csrf_token()
    proof = request.form.get("proof", "").strip()
    if not proof or len(proof) > 2000:
        flash("Provide private ownership details (up to 2,000 characters).")
        return redirect(url_for("item_detail", item_id=item_id))
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
    cur.execute("""SELECT id FROM claims WHERE item_id=%s AND user_id=%s
                   AND status IN ('Pending','Under Review')""",
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


@app.route("/item/<int:item_id>/memory-map", methods=["POST"])
def add_memory_map_place(item_id):
    if not logged_in():
        return redirect(url_for("login"))
    require_csrf_token()
    entry, error = validate_memory_place_form()
    if error:
        flash(error)
        return redirect(url_for("item_detail", item_id=item_id, _anchor="memory-map"))
    cur = mysql.connection.cursor()
    cur.execute(
        "SELECT status FROM items WHERE id=%s AND user_id=%s",
        (item_id, session["user_id"]),
    )
    item = cur.fetchone()
    if not item:
        cur.close()
        abort(404)
    if item[0] != "Lost":
        cur.close()
        abort(400, description="Memory Map entries are available for active lost reports.")
    cur.execute(
        """INSERT INTO journey_places
           (item_id,user_id,place_name,city,state,visited_at,approx_lat,approx_lng,notes)
           VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
        (
            item_id, session["user_id"], entry["place_name"], entry["city"],
            entry["state"], entry["visited_at"], entry["approx_lat"],
            entry["approx_lng"], entry["notes"],
        ),
    )
    mysql.connection.commit()
    cur.close()
    return redirect(url_for("item_detail", item_id=item_id, _anchor="memory-map"))


@app.route("/item/<int:item_id>/memory-map/<int:place_id>/edit", methods=["POST"])
def edit_memory_map_place(item_id, place_id):
    if not logged_in():
        return redirect(url_for("login"))
    require_csrf_token()
    entry, error = validate_memory_place_form()
    if error:
        flash(error)
        return redirect(url_for("item_detail", item_id=item_id, _anchor="memory-map"))
    cur = mysql.connection.cursor()
    cur.execute(
        """UPDATE journey_places SET place_name=%s,city=%s,state=%s,visited_at=%s,
                  approx_lat=%s,approx_lng=%s,notes=%s,updated_at=CURRENT_TIMESTAMP
           WHERE id=%s AND item_id=%s AND user_id=%s""",
        (
            entry["place_name"], entry["city"], entry["state"], entry["visited_at"],
            entry["approx_lat"], entry["approx_lng"], entry["notes"],
            place_id, item_id, session["user_id"],
        ),
    )
    if cur.rowcount == 0:
        cur.close()
        abort(404)
    mysql.connection.commit()
    cur.close()
    return redirect(url_for("item_detail", item_id=item_id, _anchor="memory-map"))


@app.route("/item/<int:item_id>/memory-map/<int:place_id>/delete", methods=["POST"])
def delete_memory_map_place(item_id, place_id):
    if not logged_in():
        return redirect(url_for("login"))
    require_csrf_token()
    cur = mysql.connection.cursor()
    cur.execute(
        "DELETE FROM journey_places WHERE id=%s AND item_id=%s AND user_id=%s",
        (place_id, item_id, session["user_id"]),
    )
    if cur.rowcount == 0:
        cur.close()
        abort(404)
    mysql.connection.commit()
    cur.close()
    return redirect(url_for("item_detail", item_id=item_id, _anchor="memory-map"))


MISSION_STATUSES = {"Active", "Paused", "Completed", "Successful", "Archived"}
MISSION_TASK_STATUSES = {
    "Not Started", "In Progress", "Completed", "Not Applicable",
}
MISSION_PRIORITIES = {"High", "Medium", "Low"}


def _owned_mission(cur, mission_id, user_id):
    cur.execute(
        """SELECT m.id,m.item_id,m.status,m.last_reminded_at,i.item_name,
                  i.status,i.category,i.description,i.event_date,i.event_time,
                  i.location,i.city,i.state
           FROM search_missions m JOIN items i ON i.id=m.item_id
           WHERE m.id=%s AND m.user_id=%s""",
        (mission_id, user_id),
    )
    return cur.fetchone()


def _mission_tasks_from_report(cur, item):
    item_id, name, category, description, event_date, event_time, location, city, state = item
    cur.execute(
        """SELECT place_name FROM journey_places
           WHERE item_id=%s AND user_id=%s ORDER BY visited_at,id LIMIT 8""",
        (item_id, session["user_id"]),
    )
    places = [row[0] for row in cur.fetchall()]
    details = {
        "item_name": name or "",
        "category": category or "",
        "description": description or "",
        "last_seen": (
            f"{event_date}T{str(event_time)[:5]}"
            if event_date and event_time else ""
        ),
        "last_location": location or "",
        "places": places,
        "travel_method": "",
        "last_used": "",
        "city": city or "",
        "state": state or "",
    }
    prediction = build_location_predictions(details)
    tasks = [{
        "title": "Review possible matches on FindIt!",
        "details": (
            "Search active found reports using the item name, description, and city. "
            "Match scores are leads only; use the existing secure claim process."
        ),
        "priority": "High" if category or name else "Medium",
        "sort_order": 0,
    }]
    for index, recommendation in enumerate(prediction["recommendations"], start=1):
        tasks.append({
            "title": recommendation["place"][:180],
            "details": (
                f"{recommendation['reason']} Next action: "
                f"{recommendation['next_action']}"
            )[:500],
            "priority": recommendation["priority"],
            "sort_order": index,
        })
    return tasks


@app.route("/missions/new/<int:item_id>", methods=["GET", "POST"])
def create_search_mission(item_id):
    if not logged_in():
        return redirect(url_for("login"))
    cur = mysql.connection.cursor()
    cur.execute(
        """SELECT id,item_name,category,description,event_date,event_time,
                  location,city,state,status
           FROM items WHERE id=%s AND user_id=%s""",
        (item_id, session["user_id"]),
    )
    item = cur.fetchone()
    if not item:
        cur.close()
        abort(404)
    if item[9] != "Lost":
        cur.close()
        abort(400, description="Search missions can only be created for a lost report.")

    if request.method == "POST":
        require_csrf_token()
        cur.execute(
            """SELECT id FROM search_missions
               WHERE user_id=%s AND item_id=%s AND status IN ('Active','Paused')""",
            (session["user_id"], item_id),
        )
        existing = cur.fetchone()
        if existing:
            cur.close()
            return redirect(url_for("search_mission", mission_id=existing[0]))
        try:
            cur.execute(
                """INSERT INTO search_missions(user_id,item_id,status)
                   VALUES(%s,%s,'Active')""",
                (session["user_id"], item_id),
            )
        except sqlite3.IntegrityError:
            mysql.connection.rollback()
            cur.execute(
                """SELECT id FROM search_missions
                   WHERE user_id=%s AND item_id=%s AND status IN ('Active','Paused')""",
                (session["user_id"], item_id),
            )
            existing = cur.fetchone()
            cur.close()
            if existing:
                return redirect(url_for("search_mission", mission_id=existing[0]))
            raise
        mission_id = cur.lastrowid
        tasks = _mission_tasks_from_report(cur, item[:9])
        for task in tasks:
            cur.execute(
                """INSERT INTO search_mission_tasks
                   (mission_id,title,details,priority,sort_order)
                   VALUES(%s,%s,%s,%s,%s)""",
                (
                    mission_id, task["title"], task["details"],
                    task["priority"], task["sort_order"],
                ),
            )
        mysql.connection.commit()
        cur.close()
        flash("Your personalised search mission is ready. Suggested tasks are not marked complete.")
        return redirect(url_for("search_mission", mission_id=mission_id))

    cur.close()
    return render_template("search_mission_create.html", item=item)


@app.route("/missions")
def search_missions():
    if not logged_in():
        return redirect(url_for("login"))
    cur = mysql.connection.cursor()
    cur.execute(
        """SELECT m.id,m.item_id,m.status,m.updated_at,i.item_name,i.status,
                  COUNT(t.id),
                  SUM(CASE WHEN t.status='Completed' THEN 1 ELSE 0 END),
                  SUM(CASE WHEN t.status='Not Applicable' THEN 1 ELSE 0 END)
           FROM search_missions m JOIN items i ON i.id=m.item_id
           LEFT JOIN search_mission_tasks t ON t.mission_id=m.id
           WHERE m.user_id=%s
           GROUP BY m.id ORDER BY m.updated_at DESC,m.id DESC""",
        (session["user_id"],),
    )
    missions = [
        {
            "id": row[0], "item_id": row[1], "status": row[2],
            "updated_at": row[3], "item_name": row[4],
            "item_status": row[5], "task_count": row[6] or 0,
            "completed_count": row[7] or 0,
            "not_applicable_count": row[8] or 0,
            "remaining_count": max(0, (row[6] or 0) - (row[7] or 0) - (row[8] or 0)),
            "progress": round(((row[7] or 0) + (row[8] or 0)) * 100 / row[6]) if row[6] else 0,
        }
        for row in cur.fetchall()
    ]
    cur.close()
    return render_template("search_missions.html", missions=missions)


@app.route("/missions/<int:mission_id>")
def search_mission(mission_id):
    if not logged_in():
        return redirect(url_for("login"))
    cur = mysql.connection.cursor()
    mission = _owned_mission(cur, mission_id, session["user_id"])
    if not mission:
        cur.close()
        abort(404)
    cur.execute(
        """SELECT id,title,details,priority,status,is_custom,updated_at
           FROM search_mission_tasks WHERE mission_id=%s ORDER BY sort_order,id""",
        (mission_id,),
    )
    tasks = [
        {
            "id": row[0], "title": row[1], "details": row[2] or "",
            "priority": row[3], "status": row[4],
            "is_custom": bool(row[5]), "updated_at": row[6],
        }
        for row in cur.fetchall()
    ]
    total = len(tasks)
    completed = sum(task["status"] == "Completed" for task in tasks)
    not_applicable = sum(task["status"] == "Not Applicable" for task in tasks)
    cur.close()
    return render_template(
        "search_mission.html",
        mission=mission,
        tasks=tasks,
        task_statuses=sorted(MISSION_TASK_STATUSES),
        priorities=["High", "Medium", "Low"],
        completed_count=completed,
        not_applicable_count=not_applicable,
        task_count=total,
        remaining_count=total - completed - not_applicable,
        progress=round((completed + not_applicable) * 100 / total) if total else 0,
        can_mark_success=mission[5] in ("Returned", "Recovered"),
    )


@app.route("/missions/<int:mission_id>/tasks/add", methods=["POST"])
def add_search_mission_task(mission_id):
    if not logged_in():
        return redirect(url_for("login"))
    require_csrf_token()
    title = request.form.get("title", "").strip()
    details = request.form.get("details", "").strip()
    priority = request.form.get("priority", "Medium")
    if not title or len(title) > 180 or len(details) > 500 or priority not in MISSION_PRIORITIES:
        abort(400, description="Enter a task title (up to 180 characters), details (up to 500), and valid priority.")
    cur = mysql.connection.cursor()
    mission = _owned_mission(cur, mission_id, session["user_id"])
    if not mission:
        cur.close()
        abort(404)
    if mission[2] not in ("Active", "Paused"):
        cur.close()
        abort(400, description="Tasks can only be changed on active or paused missions.")
    cur.execute(
        "SELECT COALESCE(MAX(sort_order),-1)+1 FROM search_mission_tasks WHERE mission_id=%s",
        (mission_id,),
    )
    sort_order = cur.fetchone()[0]
    cur.execute(
        """INSERT INTO search_mission_tasks
           (mission_id,title,details,priority,is_custom,sort_order)
           VALUES(%s,%s,%s,%s,1,%s)""",
        (mission_id, title, details or None, priority, sort_order),
    )
    cur.execute(
        "UPDATE search_missions SET updated_at=CURRENT_TIMESTAMP WHERE id=%s",
        (mission_id,),
    )
    mysql.connection.commit()
    cur.close()
    return redirect(url_for("search_mission", mission_id=mission_id))


@app.route("/missions/<int:mission_id>/tasks/<int:task_id>", methods=["POST"])
def update_search_mission_task(mission_id, task_id):
    if not logged_in():
        return redirect(url_for("login"))
    require_csrf_token()
    title = request.form.get("title", "").strip()
    details = request.form.get("details", "").strip()
    priority = request.form.get("priority", "")
    status = request.form.get("status", "")
    if (
        not title or len(title) > 180 or len(details) > 500
        or priority not in MISSION_PRIORITIES
        or status not in MISSION_TASK_STATUSES
    ):
        abort(400, description="Check the task title, details, priority, and status.")
    cur = mysql.connection.cursor()
    mission = _owned_mission(cur, mission_id, session["user_id"])
    if not mission:
        cur.close()
        abort(404)
    if mission[2] not in ("Active", "Paused"):
        cur.close()
        abort(400, description="Tasks can only be changed on active or paused missions.")
    cur.execute(
        """UPDATE search_mission_tasks
           SET title=%s,details=%s,priority=%s,status=%s,updated_at=CURRENT_TIMESTAMP
           WHERE id=%s AND mission_id=%s""",
        (title, details or None, priority, status, task_id, mission_id),
    )
    if cur.rowcount != 1:
        cur.close()
        abort(404)
    cur.execute(
        "UPDATE search_missions SET updated_at=CURRENT_TIMESTAMP WHERE id=%s",
        (mission_id,),
    )
    mysql.connection.commit()
    cur.close()
    return redirect(url_for("search_mission", mission_id=mission_id))


@app.route("/missions/<int:mission_id>/tasks/<int:task_id>/delete", methods=["POST"])
def delete_search_mission_task(mission_id, task_id):
    if not logged_in():
        return redirect(url_for("login"))
    require_csrf_token()
    cur = mysql.connection.cursor()
    mission = _owned_mission(cur, mission_id, session["user_id"])
    if not mission:
        cur.close()
        abort(404)
    if mission[2] not in ("Active", "Paused"):
        cur.close()
        abort(400)
    cur.execute(
        "DELETE FROM search_mission_tasks WHERE id=%s AND mission_id=%s",
        (task_id, mission_id),
    )
    if cur.rowcount != 1:
        cur.close()
        abort(404)
    cur.execute(
        "UPDATE search_missions SET updated_at=CURRENT_TIMESTAMP WHERE id=%s",
        (mission_id,),
    )
    mysql.connection.commit()
    cur.close()
    return redirect(url_for("search_mission", mission_id=mission_id))


@app.route("/missions/<int:mission_id>/action", methods=["POST"])
def update_search_mission_status(mission_id):
    if not logged_in():
        return redirect(url_for("login"))
    require_csrf_token()
    action = request.form.get("action", "")
    cur = mysql.connection.cursor()
    mission = _owned_mission(cur, mission_id, session["user_id"])
    if not mission:
        cur.close()
        abort(404)
    transitions = {
        "pause": {"Active": "Paused"},
        "resume": {"Paused": "Active"},
        "complete": {"Active": "Completed", "Paused": "Completed"},
        "archive": {
            "Active": "Archived", "Paused": "Archived",
            "Completed": "Archived", "Successful": "Archived",
        },
    }
    if action == "success":
        if mission[5] not in ("Returned", "Recovered"):
            cur.close()
            flash(
                "A mission can be marked successful after the report is Returned or Recovered "
                "through FindIt's existing handover/recovery workflow."
            )
            return redirect(url_for("search_mission", mission_id=mission_id))
        new_status = "Successful"
    else:
        new_status = transitions.get(action, {}).get(mission[2])
    if not new_status:
        cur.close()
        abort(400, description="That mission status transition is not allowed.")
    cur.execute(
        "UPDATE search_missions SET status=%s,updated_at=CURRENT_TIMESTAMP WHERE id=%s AND user_id=%s",
        (new_status, mission_id, session["user_id"]),
    )
    mysql.connection.commit()
    cur.close()
    return redirect(url_for("search_mission", mission_id=mission_id))


@app.route("/missions/<int:mission_id>/remind", methods=["POST"])
def remind_search_mission(mission_id):
    if not logged_in():
        return redirect(url_for("login"))
    require_csrf_token()
    cur = mysql.connection.cursor()
    mission = _owned_mission(cur, mission_id, session["user_id"])
    if not mission:
        cur.close()
        abort(404)
    if mission[2] != "Active":
        cur.close()
        abort(400, description="Reminders are available for active missions only.")
    cur.execute(
        """SELECT id FROM search_missions
           WHERE id=%s AND user_id=%s
             AND (last_reminded_at IS NULL
                  OR datetime(last_reminded_at)<=datetime('now','-1 day'))""",
        (mission_id, session["user_id"]),
    )
    if not cur.fetchone():
        cur.close()
        flash("You can request another mission reminder tomorrow.")
        return redirect(url_for("search_mission", mission_id=mission_id))
    cur.execute(
        """UPDATE search_missions SET last_reminded_at=CURRENT_TIMESTAMP,
                  updated_at=CURRENT_TIMESTAMP WHERE id=%s AND user_id=%s""",
        (mission_id, session["user_id"]),
    )
    cur.execute(
        """INSERT INTO notifications(user_id,message,notification_type,item_id)
           VALUES(%s,%s,'general',%s)""",
        (
            session["user_id"],
            f"Search mission reminder: continue your plan for {mission[4]}.",
            mission[1],
        ),
    )
    mysql.connection.commit()
    cur.close()
    flash("A reminder was added to your FindIt notifications.")
    return redirect(url_for("search_mission", mission_id=mission_id))


@app.route("/item/<int:item_id>/helpful-clue", methods=["POST"])
def submit_helpful_clue(item_id):
    if not logged_in():
        return redirect(url_for("login"))
    require_csrf_token()
    details = request.form.get("details", "").strip()
    if not details or len(details) > 1000:
        flash("Enter a helpful clue of up to 1,000 characters.")
        return redirect(url_for("item_detail", item_id=item_id))
    cur = mysql.connection.cursor()
    cur.execute(
        "SELECT user_id,status FROM items WHERE id=%s",
        (item_id,),
    )
    item = cur.fetchone()
    if not item:
        cur.close()
        abort(404)
    if item[0] == session["user_id"]:
        cur.close()
        abort(403)
    if item[1] not in ("Found", "Claim Submitted"):
        cur.close()
        abort(400)
    try:
        cur.execute(
            """INSERT INTO reward_contributions
               (user_id,item_id,contribution_type,details)
               VALUES(%s,%s,'helpful_clue',%s)""",
            (session["user_id"], item_id, details),
        )
        contribution_id = cur.lastrowid
    except sqlite3.IntegrityError:
        mysql.connection.rollback()
        cur.close()
        flash("You have already submitted a clue for this report.")
        return redirect(url_for("item_detail", item_id=item_id))
    mysql.connection.commit()
    cur.close()
    flash("Your clue was sent for moderator review. Points are awarded only if it is approved.")
    return redirect(url_for("item_detail", item_id=item_id))


@app.route("/rewards", methods=["GET", "POST"])
def good_citizen_rewards():
    if not logged_in():
        return redirect(url_for("login"))
    cur = mysql.connection.cursor()
    if request.method == "POST":
        require_csrf_token()
        is_public = request.form.get("reward_public") == "on"
        display_name = request.form.get("reward_public_name", "").strip()
        if is_public and (len(display_name) < 2 or len(display_name) > 80):
            cur.close()
            flash("Choose a public display name between 2 and 80 characters.")
            return redirect(url_for("good_citizen_rewards"))
        cur.execute(
            """UPDATE users SET reward_public=%s,reward_public_name=%s
               WHERE id=%s""",
            (is_public, display_name if is_public else None, session["user_id"]),
        )
        mysql.connection.commit()
        cur.close()
        flash("Leaderboard privacy settings saved.")
        return redirect(url_for("good_citizen_rewards"))

    period = request.args.get("period", "all")
    if period not in ("all", "month"):
        period = "all"
    current_month = datetime.now().strftime("%Y-%m")
    cur.execute(
        """SELECT COALESCE(SUM(points),0),
                  SUM(CASE WHEN contribution_type='successful_return' AND points>0
                      AND NOT EXISTS(
                        SELECT 1 FROM reward_transactions rev
                        WHERE rev.user_id=reward_transactions.user_id
                          AND rev.recovery_id=reward_transactions.recovery_id
                          AND rev.contribution_type='reversal'
                      ) THEN 1 ELSE 0 END),
                  SUM(CASE WHEN contribution_type='helpful_clue' AND points>0 THEN 1 ELSE 0 END)
           FROM reward_transactions WHERE user_id=%s""",
        (session["user_id"],),
    )
    totals = cur.fetchone()
    cur.execute(
        """SELECT b.name,b.successful_returns,ub.awarded_at
           FROM user_reward_badges ub JOIN reward_badges b ON b.id=ub.badge_id
           WHERE ub.user_id=%s AND b.active=1 ORDER BY b.successful_returns""",
        (session["user_id"],),
    )
    badges = cur.fetchall()
    cur.execute(
        """SELECT contribution_type,points,reason,created_at
           FROM reward_transactions WHERE user_id=%s
           ORDER BY created_at DESC,id DESC LIMIT 50""",
        (session["user_id"],),
    )
    history = cur.fetchall()
    cur.execute(
        "SELECT reward_public,reward_public_name FROM users WHERE id=%s",
        (session["user_id"],),
    )
    preferences = cur.fetchone()
    date_condition = "AND substr(r.created_at,1,7)=%s" if period == "month" else ""
    params = (current_month,) if period == "month" else ()
    cur.execute(
        f"""SELECT u.reward_public_name,
                   SUM(CASE WHEN r.contribution_type='successful_return'
                              AND r.points>0 AND NOT EXISTS(
                                SELECT 1 FROM reward_transactions rev
                                WHERE rev.user_id=r.user_id
                                  AND rev.recovery_id=r.recovery_id
                                  AND rev.contribution_type='reversal'
                              ) THEN 1 ELSE 0 END) AS returns,
                   SUM(CASE WHEN r.contribution_type='helpful_clue'
                              AND r.points>0 THEN 1 ELSE 0 END) AS helpful_clues,
                   COALESCE(SUM(r.points),0) AS points
            FROM reward_transactions r JOIN users u ON u.id=r.user_id
            WHERE u.reward_public=1 {date_condition}
            GROUP BY r.user_id,u.reward_public_name
            HAVING returns+helpful_clues>0
            ORDER BY returns+helpful_clues DESC,points DESC,u.reward_public_name COLLATE NOCASE
            LIMIT 50""",
        params,
    )
    leaderboard = cur.fetchall()
    cur.close()
    return render_template(
        "rewards.html",
        points=totals[0] or 0,
        successful_returns=totals[1] or 0,
        helpful_contributions=totals[2] or 0,
        badges=badges,
        history=history,
        preferences=preferences,
        leaderboard=leaderboard,
        period=period,
    )


@app.route("/admin/rewards")
def admin_rewards():
    if not is_admin():
        abort(403)
    cur = mysql.connection.cursor()
    cur.execute(
        """SELECT r.id,r.user_id,u.name,r.contribution_type,r.recovery_id,
                  r.contribution_id,r.points,r.reason,r.created_at,r.created_by,
                  EXISTS(SELECT 1 FROM reward_transactions rev
                         WHERE rev.user_id=r.user_id AND rev.recovery_id=r.recovery_id
                           AND rev.contribution_type='reversal') AS is_reversed
           FROM reward_transactions r JOIN users u ON u.id=r.user_id
           ORDER BY r.created_at DESC,r.id DESC LIMIT 100"""
    )
    transactions = cur.fetchall()
    cur.execute(
        """SELECT c.id,c.user_id,u.name,c.item_id,i.item_name,c.details,c.created_at
           FROM reward_contributions c JOIN users u ON u.id=c.user_id
           JOIN items i ON i.id=c.item_id WHERE c.status='Pending'
           ORDER BY c.created_at,c.id LIMIT 100"""
    )
    pending_clues = cur.fetchall()
    cur.execute("SELECT id,name FROM users ORDER BY name LIMIT 500")
    reward_users = cur.fetchall()
    cur.execute(
        "SELECT id,name,successful_returns,active FROM reward_badges ORDER BY successful_returns"
    )
    badge_definitions = cur.fetchall()
    cur.close()
    return render_template(
        "admin_rewards.html",
        transactions=transactions,
        pending_clues=pending_clues,
        reward_users=reward_users,
        badge_definitions=badge_definitions,
        return_points=app.config["REWARD_SUCCESSFUL_RETURN_POINTS"],
        clue_points=app.config["REWARD_APPROVED_CLUE_POINTS"],
    )


@app.route("/admin/rewards/clue/<int:contribution_id>/<action>", methods=["POST"])
def review_reward_clue(contribution_id, action):
    if not is_admin():
        abort(403)
    require_csrf_token()
    if action not in ("approve", "reject"):
        abort(400)
    cur = mysql.connection.cursor()
    cur.execute(
        "SELECT user_id,item_id,status FROM reward_contributions WHERE id=%s",
        (contribution_id,),
    )
    contribution = cur.fetchone()
    if not contribution:
        cur.close()
        abort(404)
    if contribution[0] == session["user_id"]:
        cur.close()
        abort(403)
    if contribution[2] != "Pending":
        cur.close()
        abort(400)
    if action == "approve":
        approve_reward_contribution(cur, contribution_id, session["user_id"])
    else:
        cur.execute(
            """UPDATE reward_contributions SET status='Rejected',reviewed_by=%s,
                      reviewed_at=CURRENT_TIMESTAMP WHERE id=%s AND status='Pending'""",
            (session["user_id"], contribution_id),
        )
        cur.execute(
            """INSERT INTO notifications(user_id,message,notification_type,item_id)
               VALUES(%s,'Your helpful clue was not approved; no points were awarded.','reward',%s)""",
            (contribution[0], contribution[1]),
        )
    audit_action = "reward_clue_approved" if action == "approve" else "reward_clue_rejected"
    log_audit(cur, audit_action, "reward_contribution", contribution_id,
              {"user_id": contribution[0], "item_id": contribution[1]})
    mysql.connection.commit()
    cur.close()
    return redirect(url_for("admin_rewards"))


@app.route("/admin/rewards/adjust", methods=["POST"])
def adjust_reward_points():
    if not is_admin():
        abort(403)
    require_csrf_token()
    try:
        target_user_id = int(request.form.get("user_id", ""))
        points = int(request.form.get("points", ""))
    except ValueError:
        abort(400)
    reason = request.form.get("reason", "").strip()
    if points == 0 or abs(points) > 10000 or len(reason) < 10 or len(reason) > 500:
        abort(400)
    if target_user_id == session["user_id"]:
        abort(403)
    cur = mysql.connection.cursor()
    cur.execute("SELECT id FROM users WHERE id=%s", (target_user_id,))
    if not cur.fetchone():
        cur.close()
        abort(404)
    cur.execute(
        "SELECT COALESCE(SUM(points),0) FROM reward_transactions WHERE user_id=%s",
        (target_user_id,),
    )
    balance = cur.fetchone()[0]
    if balance + points < 0:
        cur.close()
        abort(400, description="An adjustment cannot reduce the balance below zero.")
    cur.execute(
        """INSERT INTO reward_transactions
           (user_id,contribution_type,points,reason,created_by)
           VALUES(%s,'manual_adjustment',%s,%s,%s)""",
        (target_user_id, points, reason, session["user_id"]),
    )
    transaction_id = cur.lastrowid
    cur.execute(
        """INSERT INTO notifications(user_id,message,notification_type)
           VALUES(%s,%s,'reward')""",
        (target_user_id, f"An administrator adjusted your Good Citizen balance by {points:+d} points."),
    )
    log_audit(cur, "reward_manual_adjustment", "reward_transaction", transaction_id,
              {"user_id": target_user_id, "points": points, "reason": reason})
    mysql.connection.commit()
    cur.close()
    return redirect(url_for("admin_rewards"))


@app.route("/admin/rewards/reverse/<int:transaction_id>", methods=["POST"])
def reverse_reward_transaction(transaction_id):
    if not is_admin():
        abort(403)
    require_csrf_token()
    reason = request.form.get("reason", "").strip()
    if len(reason) < 10 or len(reason) > 500:
        abort(400)
    cur = mysql.connection.cursor()
    reverse_successful_return_reward(cur, transaction_id, session["user_id"], reason)
    mysql.connection.commit()
    cur.close()
    return redirect(url_for("admin_rewards"))


@app.route("/admin/rewards/badge/<int:badge_id>", methods=["POST"])
def configure_reward_badge(badge_id):
    if not is_admin():
        abort(403)
    require_csrf_token()
    try:
        threshold = int(request.form.get("successful_returns", ""))
    except ValueError:
        abort(400)
    if not 1 <= threshold <= 10000:
        abort(400)
    active = request.form.get("active") == "on"
    cur = mysql.connection.cursor()
    cur.execute("SELECT name,successful_returns,active FROM reward_badges WHERE id=%s", (badge_id,))
    current = cur.fetchone()
    if not current:
        cur.close()
        abort(404)
    try:
        cur.execute(
            "UPDATE reward_badges SET successful_returns=%s,active=%s WHERE id=%s",
            (threshold, active, badge_id),
        )
    except sqlite3.IntegrityError:
        mysql.connection.rollback()
        cur.close()
        abort(400, description="Each badge must have a distinct return threshold.")
    log_audit(cur, "reward_badge_configured", "reward_badge", badge_id,
              {"from_threshold": current[1], "to_threshold": threshold,
               "active": active})
    mysql.connection.commit()
    cur.close()
    return redirect(url_for("admin_rewards"))


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
    cur.execute(
        """SELECT m.id,m.item_id,m.status,m.updated_at,i.item_name,i.status,
                  COUNT(t.id),
                  SUM(CASE WHEN t.status='Completed' THEN 1 ELSE 0 END),
                  SUM(CASE WHEN t.status='Not Applicable' THEN 1 ELSE 0 END)
           FROM search_missions m JOIN items i ON i.id=m.item_id
           LEFT JOIN search_mission_tasks t ON t.mission_id=m.id
           WHERE m.user_id=%s
           GROUP BY m.id ORDER BY m.updated_at DESC,m.id DESC LIMIT 8""",
        (session["user_id"],),
    )
    dashboard_missions = [
        {
            "id": row[0], "item_id": row[1], "status": row[2],
            "updated_at": row[3], "item_name": row[4],
            "item_status": row[5], "task_count": row[6] or 0,
            "completed_count": row[7] or 0,
            "not_applicable_count": row[8] or 0,
            "remaining_count": max(0, (row[6] or 0) - (row[7] or 0) - (row[8] or 0)),
            "progress": round(((row[7] or 0) + (row[8] or 0)) * 100 / row[6]) if row[6] else 0,
        }
        for row in cur.fetchall()
    ]
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
    cur.execute(
        "SELECT COALESCE(SUM(points),0) FROM reward_transactions WHERE user_id=%s",
        (session["user_id"],),
    )
    reward_points = cur.fetchone()[0]
    cur.execute(
        """SELECT COUNT(*) FROM reward_transactions
           WHERE user_id=%s AND contribution_type='successful_return' AND points>0
             AND NOT EXISTS(
               SELECT 1 FROM reward_transactions rev
               WHERE rev.user_id=reward_transactions.user_id
                 AND rev.recovery_id=reward_transactions.recovery_id
                 AND rev.contribution_type='reversal'
             )""",
        (session["user_id"],),
    )
    successful_returns = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM user_reward_badges WHERE user_id=%s", (session["user_id"],))
    reward_badges_count = cur.fetchone()[0]
    cur.close()
    return render_template(
        "dashboard.html",
        my_items=my_items,
        claims=claims,
        my_claims=my_claims,
        dashboard_missions=dashboard_missions,
        recent_items=recent_items,
        reward_summary={
            "points": reward_points,
            "successful_returns": successful_returns,
            "badges": reward_badges_count,
        },
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
    cur.execute("""SELECT n.id,n.user_id,n.message,n.is_read,n.created_at,
                          n.notification_type,n.item_id,n.matched_item_id,
                          n.match_score,n.explanation,n.claim_id,
                          matched.item_name AS matched_item_title,
                          claimed.item_name AS claim_item_title
                   FROM notifications n
                   LEFT JOIN items matched ON matched.id=n.matched_item_id
                   LEFT JOIN claims c ON c.id=n.claim_id
                   LEFT JOIN items claimed ON claimed.id=c.item_id
                   WHERE n.user_id=%s ORDER BY n.created_at DESC,n.id DESC""",
                (session["user_id"],))
    notes = [dict(row) for row in cur.fetchall()]
    cur.close()
    return render_template("notifications.html",notifications=notes)

@app.route("/notifications/<int:notification_id>/read", methods=["POST"])
def mark_notification_read(notification_id):
    if not logged_in():
        return redirect(url_for("login"))
    cur = mysql.connection.cursor()
    cur.execute(
        "UPDATE notifications SET is_read=1 WHERE id=%s AND user_id=%s",
        (notification_id, session["user_id"]),
    )
    mysql.connection.commit()
    cur.close()
    return redirect(url_for("notifications"))

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
            """SELECT i.id,i.item_name,i.category,i.location,i.event_date,i.city,i.state FROM items i
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
                "location": public_item_region(row[5], row[6], row[3]),
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
            """SELECT i.id,i.item_name,i.category,i.location,i.event_date,i.city,i.state FROM items i
               WHERE """ + " AND ".join(conditions) + " ORDER BY i.created_at DESC LIMIT 5",
            tuple(params),
        )
        rows = cur.fetchall()
        cur.close()
        items = [{"name": row[1], "category": row[2],
                  "location": public_item_region(row[5], row[6], row[3]),
                  "date": str(row[4]), "url": url_for("item_detail", item_id=row[0])}
                 for row in rows]
        answer = (
            chatbot_text("match-found", language) if items
            else chatbot_text("match-none", language)
        )
        return jsonify(answer=answer, items=items)
    if not app.config["GEMINI_API_KEY"]:
        return jsonify(
            answer=chatbot_text("ai-missing", language),
            items=[],
        ), 503
    try:
        answer = generate_chatbot_answer(message, conversation_history, language)
    except urllib.error.HTTPError as error:
        try:
            provider_error = json.loads(error.read().decode("utf-8")).get("error", {})
        except (AttributeError, UnicodeDecodeError, json.JSONDecodeError):
            provider_error = {}
        if not isinstance(provider_error, dict):
            provider_error = {}
        error_type = provider_error.get("type")
        error_code = provider_error.get("code")
        app.logger.warning(
            "Gemini chatbot request failed with HTTP status %s (type=%s, code=%s)",
            error.code,
            error_type,
            error_code,
        )
        if error.code in (400, 401):
            answer = "Gemini rejected the request. Check that GEMINI_API_KEY is valid and GEMINI_MODEL is supported in Render."
        elif error.code == 403:
            answer = "Gemini denied access. Check the API key, project permissions, and access to the configured model."
        elif error.code == 404:
            answer = "Gemini could not find the configured model. Check GEMINI_MODEL in Render; the default is gemini-2.5-flash."
        elif error.code == 429:
            answer = "Gemini rate limits or API quota blocked this request. Check your Google AI Studio project limits and billing."
        else:
            answer = "Gemini is temporarily unavailable. Please try again shortly."
        return jsonify(
            answer=answer,
            items=[],
        ), 503
    except (OSError, TimeoutError, json.JSONDecodeError, KeyError, TypeError, ValueError) as error:
        app.logger.warning("Gemini chatbot request failed: %s", error)
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
    cur.execute("""SELECT c.id,c.proof,c.status,c.created_at,i.item_name,u.name,u.email,
                          c.proof_image,c.proof_file,c.item_id,i.status,i.private_details
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
    if not logged_in():
        return redirect(url_for("login"))
    if not has_role("admin", "staff", "security"):
        abort(403)
    require_csrf_token()
    if action not in ("review", "approve", "reject"):
        abort(400)
    cur=mysql.connection.cursor()
    cur.execute("""SELECT c.user_id,c.item_id,u.email,c.status,i.status,i.user_id FROM claims c
                   JOIN users u ON u.id=c.user_id JOIN items i ON i.id=c.item_id
                   WHERE c.id=%s""",(claim_id,))
    claim=cur.fetchone()
    if not claim:
        cur.close()
        abort(404)
    if claim[0] == session["user_id"] or claim[5] == session["user_id"]:
        cur.close()
        abort(403)
    allowed_statuses = {
        "review": ("Pending",),
        "approve": ("Pending", "Under Review"),
        "reject": ("Pending", "Under Review"),
    }
    if claim[3] not in allowed_statuses[action] or claim[4] != "Claim Submitted":
        cur.close()
        abort(400)
    new_status = {
        "review": "Under Review",
        "approve": "Approved",
        "reject": "Rejected",
    }[action]
    cur.execute("UPDATE claims SET status=%s WHERE id=%s",(new_status,claim_id))
    other_claimants = []
    if action == "approve":
        cur.execute("UPDATE items SET status='Verification' WHERE id=%s",(claim[1],))
        log_audit(cur, "item_status_changed", "item", claim[1],
                  {"from": "Claim Submitted", "to": "Verification", "reason": "claim_approved"})
        cur.execute("""SELECT c.id,c.user_id,u.email FROM claims c
                       JOIN users u ON u.id=c.user_id
        WHERE c.item_id=%s AND c.id<>%s
          AND c.status IN ('Pending','Under Review')""",
                    (claim[1],claim_id))
        other_claimants = cur.fetchall()
        for other_claim in other_claimants:
            cur.execute("UPDATE claims SET status='Rejected' WHERE id=%s", (other_claim[0],))
            store_claim_notification(
                cur,
                other_claim[1],
                f"Your claim #{other_claim[0]} was closed because another claim was approved.",
                claim[1],
                other_claim[0],
            )
            log_audit(cur, "claim_auto_rejected", "claim", other_claim[0],
                      {"item_id": claim[1], "approved_claim_id": claim_id})
    elif action == "reject":
        cur.execute("""SELECT COUNT(*) FROM claims WHERE item_id=%s
                       AND status IN ('Pending','Under Review')""", (claim[1],))
        if not cur.fetchone()[0]:
            cur.execute("UPDATE items SET status='Found' WHERE id=%s",(claim[1],))
            log_audit(cur, "item_status_changed", "item", claim[1],
                      {"from": "Claim Submitted", "to": "Found", "reason": "all_claims_rejected"})
    log_audit(cur, "claim_status_changed", "claim", claim_id,
              {"item_id": claim[1], "from": claim[3], "to": new_status})
    store_claim_notification(
        cur,
        claim[0],
        f"Your claim status is now {new_status.lower()}.",
        claim[1],
        claim_id,
    )
    if claim[5] != claim[0]:
        store_claim_notification(
            cur,
            claim[5],
            f"Claim #{claim_id} for your report is now {new_status}.",
            claim[1],
            claim_id,
        )
    mysql.connection.commit()
    cur.close()
    send_email_notification(claim[2], f"FindIt claim {new_status.lower()}",
                            f"Your claim #{claim_id} status is now {new_status}.")
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
    if not logged_in():
        return redirect(url_for("login"))
    if not has_role("admin", "staff", "security"):
        abort(403)
    require_csrf_token()
    transitions = {
        "Lost": {"Found", "Closed"},
        "Found": {"Claim Submitted", "Closed"},
        "Claim Submitted": {"Found"},
        "Verification": {"Found"},
        "Returned": {"Closed"},
        "Recovered": {"Closed"},
    }
    new_status = request.form.get("status", "")
    cur = mysql.connection.cursor()
    cur.execute("SELECT status,user_id FROM items WHERE id=%s", (item_id,))
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
    cur.execute(
        "SELECT user_id FROM claims WHERE item_id=%s",
        (item_id,),
    )
    recipients = {row[0] for row in cur.fetchall()}
    recipients.add(current[1])
    recipients.discard(session["user_id"])
    for recipient_id in recipients:
        cur.execute(
            """INSERT INTO notifications(user_id,message,notification_type,item_id)
               VALUES(%s,%s,'general',%s)""",
            (
                recipient_id,
                f"Report #{item_id} status changed from {current[0]} to {new_status}.",
                item_id,
            ),
        )
    mysql.connection.commit()
    cur.close()
    flash(f"Item status updated to {new_status}.")
    return redirect(url_for("admin_panel"))

@app.route("/admin/item/<int:item_id>/handover", methods=["POST"])
def item_handover(item_id):
    if not logged_in():
        return redirect(url_for("login"))
    if not has_role("admin", "security"):
        abort(403)
    require_csrf_token()
    cur = mysql.connection.cursor()
    cur.execute("SELECT status,user_id FROM items WHERE id=%s", (item_id,))
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
    cur.execute(
        "UPDATE claims SET status='Returned',handover_at=NOW() WHERE id=%s AND status='Approved'",
        (claimant[0],),
    )
    store_claim_notification(
        cur,
        claimant[1],
        f"Your claim status is now returned. Receipt #{receipt_id} is available.",
        item_id,
        claimant[0],
    )
    cur.execute("SELECT user_id FROM items WHERE id=%s", (item_id,))
    report_owner_id = cur.fetchone()[0]
    if report_owner_id != claimant[1]:
        store_claim_notification(
            cur,
            report_owner_id,
            f"Claim #{claimant[0]} for your report is now returned.",
            item_id,
            claimant[0],
        )
    log_audit(cur, "claim_status_changed", "claim", claimant[0],
              {"item_id": item_id, "from": "Approved", "to": "Returned"})
    log_audit(cur, "item_handed_over", "item", item_id,
              {"receipt_id": receipt_id, "receiver_id": claimant[1]})
    grant_successful_return_reward(cur, item[1], item_id, session["user_id"])
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

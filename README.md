# FindIt - Digital Lost & Found Management Platform

This project uses the user's supplied FindIt frontend as the homepage and adds a Flask + SQLite backend.

## Included backend
- Registration/login
- Role-aware access for users, staff, security, and administrators
- Lost/found item reports with anonymous public display
- Photo uploads
- Database storage
- Search/filter by keyword, status, and campus location
- Duplicate warnings and weighted metadata/photo similarity suggestions
- Item details
- Claims with private photo/document evidence, restricted to staff, security, and admins
- Claim review, item lifecycle tracking, handover records, and audit logs
- In-system and optional email notifications, including match and pending reminders
- Unique QR codes for found-item records
- User dashboard
- Staff/admin operations dashboard

Item matching combines category, color, brand, location, date/time, description, and image similarity. Perceptual-hash similarity works locally; when an OpenAI key is configured, AI vision also compares resized public report photos to rank likely visual matches. Public item report images are sent to OpenAI for this comparison; private claim evidence is never sent.

The AI chatbot answers general questions using OpenAI, retains recent AI conversation context, and guides users through lost/found reports and claims. A persistent language selector switches the chatbot, shared navigation, landing-page copy, and common report/claim controls between English, Kannada, and Hindi; user-entered report content stays as submitted. Set the campus office location to provide a verified answer.

When security or an administrator records a handover, FindIt requires the receiver's name and confirmation, logs the authorized staff member, and creates a printable handover receipt with the item ID, receiver, confirmation, and timestamp.

## Run
1. Install Python.
2. Open this folder in VS Code.
3. Run: `python -m pip install -r requirements.txt`
4. Run: `python app.py`. The SQLite database is created automatically at `instance/findit.sqlite3`.
5. Open: http://127.0.0.1:5000

## Deploy from GitHub
This is a Flask server-rendered application, so GitHub stores the source but does not host the running server. Connect this repository to a Python web host that supports GitHub deployments, configure the web service to install `requirements.txt` and start with `gunicorn app:app` (also provided in `Procfile`), and set a persistent disk for the SQLite database and uploaded files. Configure `DATABASE_PATH`, `UPLOAD_FOLDER`, and `PRIVATE_UPLOAD_FOLDER` to paths on that disk. Set a long random `SECRET_KEY`; add optional AI and mail settings in the host's secret/environment settings, never in the repository. Without persistent storage, database and uploaded-file changes may be lost when the host restarts or redeploys.

To build the Android release APK against the deployed HTTPS URL, run from `mobile_apk`: `.\gradlew.bat assembleRelease -PserverUrl=https://your-domain.example`. To sign your own release build, create a private `keystore.properties` file using the key store you control; it is intentionally excluded from Git. The committed APK is the build configured for the original local Wi-Fi server address and will not work against a different deployment URL.

## Connect an Android phone
For local testing, the Android phone and computer must be on the same Wi-Fi network. Build with `-PserverUrl=http://<computer-ip>:5000` using the computer's current private IPv4 address (not `10.0.2.2`, which is only for Android emulators). Keep `python app.py` running while using the phone. If Windows Firewall blocks the connection, allow inbound TCP port 5000 on the Private network profile. For an internet deployment, build with the host's HTTPS URL instead.

After registering your first account, promote it to administrator:
After registering the first account, promote it to administrator from PowerShell:
`python -c "import sqlite3; db=sqlite3.connect('instance/findit.sqlite3'); db.execute('UPDATE users SET role=? WHERE email=?', ('admin', 'you@example.com')); db.commit(); db.close()"`

Administrators can assign `user`, `staff`, `security`, or `admin` roles from the Staff panel. Existing SQLite databases are migrated on app startup. Set `DATABASE_PATH` to use a different database file.

Optional configuration uses environment variables (do not commit credentials):
- `SECRET_KEY`: persistent random secret for signed sessions; without it, sessions reset whenever the app restarts.
- `LOST_FOUND_OFFICE`: verified campus office location shown by the assistant.
- `OPENAI_API_KEY`: enables AI-generated answers for general chatbot questions. Chat messages sent for AI answers are processed by OpenAI; item search and guided report/claim workflows remain local.
- `OPENAI_MODEL`: OpenAI chat model name (defaults to `gpt-4o-mini`).
- `AI_IMAGE_MATCHING`: set to `false` to disable OpenAI visual comparisons and use local perceptual hashes only.
- `PUBLIC_BASE_URL`: externally reachable site URL (for example, the campus-hosted URL) embedded in QR codes; localhost QR codes only work on the same device.
- `MAIL_SERVER`, `MAIL_PORT`, `MAIL_USERNAME`, `MAIL_PASSWORD`, `MAIL_SENDER`, `MAIL_USE_TLS`: SMTP delivery for claim, match, and handover emails. In-system notifications work without SMTP.

The original frontend is preserved in `templates/index.html`.

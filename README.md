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
- FindIt AI Photo Clue Finder in the existing Lost/Found report form, with validated and sanitized photos, local perceptual similarity, optional consent-based OpenAI comparison, and combined opposite-type matching
- Hybrid smart matching across active lost/found reports: Sentence Transformers (`all-MiniLM-L6-v2`) description similarity combined with category, color/brand, date, and location signals
- Item details
- Owner- and claimant-scoped recovery journey timelines built from the existing item audit log, claim history, and match notifications
- Claims use private claimant answers and optional photo/document evidence, restricted to staff, security, and admins. Finders can optionally store private identifying details on found reports; only that report's owner and authorized claim reviewers can see them. Reviewers compare evidence without exposing the hidden details to claimants. Claim statuses are Pending, Under Review, Approved, Rejected, and Returned.
- Claim review, item lifecycle tracking, handover records, and audit logs
- In-system and optional email notifications, including match and pending reminders
- Unique QR codes for found-item records
- User dashboard
- Staff/admin operations dashboard

The **FindIt AI Photo Clue Finder** is integrated in the existing report form. Choose **Find possible reports using this photo** to search without creating or publishing a report, or use the normal report button to continue submitting. JPEG, PNG, and WebP item photos are checked against their actual image contents and dimensions, then re-encoded as metadata-stripped JPEGs before matching or storage. Same-type duplicate reports use the existing perceptual-hash and metadata score. Opposite-type candidate reports combine existing semantic/category/color/date/location ranking with visual similarity when both photos are available. The local fallback compares image patterns only; users should confirm visible clues themselves in the existing color, brand, and description fields. Candidate reports link into the existing item detail and secure claim workflow. Visual scores and matching reasons are estimates—not proof that two objects are identical. Test with `python -m pytest tests\test_photo_clue_finder.py -q`.

Optional OpenAI visual comparison ranks selected candidate photos only when `OPENAI_API_KEY` is configured, `AI_IMAGE_MATCHING` is enabled, and the reporter checks the explicit consent box. The external request receives normalized photos and candidate image IDs only, not names, report descriptions, contact details, private identifying clues, or claim evidence. If the service fails, local perceptual similarity and text matching remain available. A photo saved with a submitted report follows the existing public item-photo behavior; the upload form explains this before submission. No database migration or new dependency is required; Pillow is already installed through the existing `qrcode[pil]` requirement.

Smart matching is available while submitting a report and through **Browse Items** (enter a natural-language description and select **Natural-language smart matching**). Its ranking uses description similarity (45%), category (20%), color/brand (15%), date proximity (10%), and location proximity (10%). Missing signals are omitted and the remaining weights are normalized. Only active, eligible lost/found reports are considered; returned, closed, and verification-stage reports are excluded. Locations are compared as report text without restricting matches to Bengaluru, so reports from other Indian cities remain eligible. Each suggestion includes reasons and an estimated ranking score; scores are not probabilities or proof of ownership.

Browse Items also supports India-wide or comma-separated multi-city search, city/state aliases (including Bangalore/Bengaluru and Bombay/Mumbai), report-date ranges, category/status filters, sort order, and pagination. Smart search ranks eligible matches with the existing semantic/rule-based scorer. Optional approximate coordinates can contribute a distance signal; users are never asked for live location. New report forms accept city/state and optional approximate coordinates. Public browse, smart-search, chatbot, and item-detail views show city/state only and do not expose the free-text area or stored coordinates; map markers use approximate city-centre points only. Existing reports without city/state remain searchable using common city/state names found in their location text.

When an opposite-type report scores at least 50, FindIt stores a deduplicated match notification for the lost-report owner in the existing notification centre. The notification shows the found-report title, estimated score, explanation, and a link to the candidate report; match notices never include contact information or private claim evidence. Claim approval, rejection, and auto-rejection notices are also shown there. Users can mark their own notifications as read.

The existing report form includes a Smart Report Assistant. It extracts possible item name, category, color, brand, visible features, and keywords only when those details appear in the user's description. It highlights useful missing report information; users explicitly choose whether to apply each suggested field, can edit it, and must submit the normal report form themselves. It works for Lost and Found reports and does not require an external AI service.

Existing SQLite databases are migrated automatically when the application starts: `items.private_details` is added and the claims status constraint is expanded to support Under Review and Returned while preserving existing claims and handover records. No manual migration command is required; back up the database before upgrading. Claim verification is a human review process—AI match scores are ranking estimates only and are never proof of ownership.

Recovery journey timelines reuse existing `audit_logs`, claim timestamps/status history, and match notifications; no additional timeline table or migration is required. Report owners and authorized reviewers see report and claim progress, claimants see only their own claim progress, and public visitors do not see private recovery activity. Status changes continue to use the existing authorized workflows and valid transition rules.

The owner-only **FindIt Memory Map** lets users manually record visited places, city/state, approximate visit time, optional approximate coordinates, and notes for their own Lost reports. Entries are editable/deletable and sorted by the entered visit time. Active Found/Claim Submitted reports are ranked against the visits using available distance/place and time signals; matching estimates are not verified tracking data or proof of ownership. A Leaflet map using the configured `MAP_TILE_URL` (OpenStreetMap tiles by default) appears when approximate coordinates are available. No live or background location is collected. Journey entries are stored in a new `journey_places` SQLite table created automatically at startup; existing tables and data are preserved.

The **FindIt AI Lost Item Location Predictor** turns a user's item details, last-seen location, manually entered visited places, travel method, and approximate time into an ordered search checklist. It currently uses transparent local rules, not a connected AI model or historical probability data. Suggestions are estimates, never confirmed locations or proof of ownership; it does not invent addresses, businesses, or map coordinates. The feature works across Indian cities and links to FindIt's existing cross-city report search. Checklist progress is saved only when a signed-in user explicitly opts in; saved plans are private to their owner and can be updated or deleted. Predictor plans are stored in `location_prediction_plans`, created automatically by the existing schema setup at startup; back up the SQLite database before upgrading. No additional environment variables, AI model, or geocoding service is required. Test it with `python -m pytest tests\test_location_predictor.py -q`; run the full suite with `python -m pytest tests -q`.

The **AI Lost Item Detective** (`/lost-item-detective`) adds a one-question-at-a-time investigation flow to that same search engine. Signed-in users can select their own Lost report to prefill its details and Memory Map places, or start a new investigation; after reviewing the generated checklist, they can continue to the existing Lost report form with their answers prefilled. Investigation history is private and saved only with explicit opt-in. By default, recommendations use local rules. If `GEMINI_API_KEY` is configured, users may separately consent to send their answers to Google's Gemini API for tie-breaking between candidates within the same High/Medium/Low priority; Gemini cannot invent or add candidate locations, and failed or invalid responses fall back to the local ranking with a visible notice. The user interface explains the data transfer before consent. The detective reuses `location_prediction_plans`, so no additional migration, package, or environment variable is required. Set `GEMINI_API_KEY` and optionally `GEMINI_MODEL` to enable the existing Gemini integration; the normal chatbot uses Gemini separately. Test the detective with `python -m pytest tests\test_lost_item_detective.py -q`.

The **Good Citizen Rewards** section awards points only when security/administrators record a receiver-confirmed handover (default 100 points to the finder) or when an administrator approves a helpful clue (default 10 points). The append-only `reward_transactions` ledger is authoritative, unique indexes prevent duplicate return/clue awards, and report submissions, unverified claims, AI scores, rejected clues, and cancelled recoveries do not earn points. Badge thresholds are configurable by administrators and count verified returns only. Users can view their balance/history and opt in to a leaderboard under a chosen public display name; email and phone details are never published. Administrators can review clues, configure badges, and record reasoned ledger adjustments with audit logging. Leaderboard benefits/redemptions are not configured. Reward tables/badges and legacy user privacy columns are created/migrated automatically at startup; back up the SQLite database before upgrading. Configure `REWARD_SUCCESSFUL_RETURN_POINTS` and `REWARD_APPROVED_CLUE_POINTS` to change the point policy.

`sentence-transformers` is included in `requirements.txt`. Install dependencies with `python -m pip install -r requirements.txt`; the first smart match loads and caches `all-MiniLM-L6-v2` (internet access may be required for the initial model download). Report embeddings are cached in SQLite and refreshed when the description changes. If the package or model is unavailable, smart matching logs a warning and uses lexical text similarity until the model is available.

The AI chatbot answers general questions using Google's Gemini API, retains recent AI conversation context, and guides users through lost/found reports and claims. Chat messages sent for general AI answers are processed by Google; item search and guided report/claim workflows remain local. A persistent language selector switches the chatbot, shared navigation, landing-page copy, and common report/claim controls between English, Kannada, and Hindi; user-entered report content stays as submitted. Set the campus office location to provide a verified answer.

When security or an administrator records a handover, FindIt requires the receiver's name and confirmation, logs the authorized staff member, and creates a printable handover receipt with the item ID, receiver, confirmation, and timestamp.

## Run
1. Install Python.
2. Open this folder in VS Code.
3. Run: `python -m pip install -r requirements.txt`
4. Run: `python app.py`. The SQLite database is created automatically at `instance/findit.sqlite3`.
5. Open: http://127.0.0.1:5000
6. Run the tests, including the reward workflow: `python -m pytest tests -q` (focused rewards tests: `python -m pytest tests\test_rewards.py -q`).

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
- `GEMINI_API_KEY`: Google AI Studio API key used for general chatbot answers. Add it to the hosting provider's secret/environment settings, not the repository.
- `GEMINI_MODEL`: Gemini chat model (defaults to `gemini-2.5-flash`).
- `OPENAI_API_KEY`: optional key used for AI visual comparisons of public report photos only when the reporter explicitly opts in; it is not used by the chatbot.
- `OPENAI_MODEL`: OpenAI vision model name (defaults to `gpt-4o-mini`).
- `AI_IMAGE_MATCHING`: set to `false` to disable OpenAI visual comparisons and use local perceptual hashes only. OpenAI matching also requires per-upload user consent.
- `PUBLIC_BASE_URL`: externally reachable site URL (for example, the campus-hosted URL) embedded in QR codes; localhost QR codes only work on the same device.
- `MAIL_SERVER`, `MAIL_PORT`, `MAIL_USERNAME`, `MAIL_PASSWORD`, `MAIL_SENDER`, `MAIL_USE_TLS`: SMTP delivery for claim, match, and handover emails. In-system notifications work without SMTP.

The original frontend is preserved in `templates/index.html`.

## FindIt Search Missions & Progress Tracker

An authenticated owner can create a mission from one of their active Lost reports. Suggested checklist tasks reuse the local rule-based Location Predictor and, when available, the owner's manually entered Memory Map places. Tasks include the reason/next action, a priority, and editable status; users can add or remove tasks, monitor progress, pause/resume, complete, or archive a mission. The dashboard includes a My Search Missions section. Mission data and tasks are private to the owning account.

Recommendations are practical estimates, not confirmed locations or probabilities. Tasks are never marked complete automatically. Optional in-app reminders are user-triggered and limited to one per mission every 24 hours; no background reminder service or location tracking is used. A mission can be marked successful only after the related report has already been marked Returned or Recovered using the existing authorized claim/handover workflow; mission actions do not change report status.

The `search_missions` and `search_mission_tasks` SQLite tables and indexes are created automatically from `database.sql` at startup; no manual migration is needed. Back up the database before upgrading. Test the feature with `python -m pytest tests\test_search_missions.py -q`; run the full suite with `python -m pytest tests -q`.

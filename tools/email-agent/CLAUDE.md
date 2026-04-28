# MangaLens Email Agent — Build Specification

## Purpose and Scope

An agentic AI pipeline that monitors `hello@manga-lens.com` (forwarded to personal
Gmail), classifies each incoming email, and takes automated action: filing GitHub
issues for bugs and feature requests, auto-replying to senders, and archiving spam.

This tool lives in `tools/email-agent/` and is entirely independent of the
`backend/`, `extension/`, and `website/` subdirectories. Do not modify those.

---

## File Structure

```
tools/email-agent/
├── CLAUDE.md                  ← this file
├── .env                       ← secrets (never committed)
├── .env.example               ← committed template
├── requirements.txt
├── setup_auth.py              ← one-time interactive OAuth flow
├── agent.py                   ← main entry point
├── classifier.py              ← Claude classification + severity + dedup
├── gmail_client.py            ← Gmail API wrapper (fetch, reply, mark spam)
├── github_client.py           ← GitHub API wrapper (create issue, comment)
├── attachment_parser.py       ← Vision API + log parsing via Claude
├── audit.py                   ← append-only JSONL audit log writer
├── processed.txt              ← one Gmail message ID per line (idempotency)
├── logs/
│   └── audit.jsonl            ← append-only audit log (gitignored)
├── dashboard.html             ← single-file offline dashboard
├── evals/
│   ├── README.md
│   ├── run_evals.py
│   ├── results/               ← gitignored eval run outputs
│   └── fixtures/
│       ├── 01_bug_stacktrace.json
│       ├── 02_bug_screenshot.json
│       ├── 03_ambiguous_bug_or_feedback.json
│       ├── 04_feature_request.json
│       ├── 05_general_praise.json
│       ├── 06_angry_complaint.json
│       ├── 07_obvious_spam.json
│       ├── 08_phishing.json
│       ├── 09_out_of_office.json
│       ├── 10_newsletter.json
│       ├── 11_duplicate_issue.json
│       ├── 12_non_english.json
│       ├── 13_very_short.json
│       ├── 14_log_attachment.json
│       └── 15_subject_only.json
└── systemd/
    └── email-agent.timer
    └── email-agent.service
```

---

## Environment Variables

File: `tools/email-agent/.env`

| Variable | Description |
|---|---|
| `ANTHROPIC_API_KEY` | Anthropic API key |
| `GITHUB_TOKEN` | GitHub personal access token (scope: `repo`) |
| `GITHUB_REPO` | `AbrahamOsmondE/manga-lens` |
| `GMAIL_CREDENTIALS_FILE` | Path to OAuth client credentials JSON downloaded from GCP Console |
| `GMAIL_TOKEN_FILE` | Path to stored OAuth token after `setup_auth.py` runs (default: `token.json`) |
| `GMAIL_FILTER` | `to:hello@manga-lens.com is:unread` |
| `AUDIT_LOG_PATH` | `logs/audit.jsonl` |
| `PROCESSED_IDS_FILE` | `processed.txt` |

`.env.example`:
```
ANTHROPIC_API_KEY=your_anthropic_api_key
GITHUB_TOKEN=ghp_xxxx
GITHUB_REPO=AbrahamOsmondE/manga-lens
GMAIL_CREDENTIALS_FILE=credentials.json
GMAIL_TOKEN_FILE=token.json
GMAIL_FILTER=to:hello@manga-lens.com is:unread
AUDIT_LOG_PATH=logs/audit.jsonl
PROCESSED_IDS_FILE=processed.txt
```

---

## One-Time Setup

### 1. Enable Gmail API

1. Go to [console.cloud.google.com](https://console.cloud.google.com) → APIs & Services → Library
2. Enable **Gmail API**
3. Go to APIs & Services → Credentials → Create Credentials → **OAuth 2.0 Client ID**
4. Application type: **Desktop app**
5. Download the JSON file → save as `tools/email-agent/credentials.json`
6. Go to OAuth consent screen → add your Gmail address as a test user

### 2. Run the OAuth flow (once)

```bash
cd tools/email-agent
pip install -r requirements.txt
python setup_auth.py
```

This opens a browser, asks you to sign into your Google account, and saves
`token.json`. All subsequent runs use the stored refresh token headlessly.

`setup_auth.py` must request these scopes:
```python
SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/gmail.send",
]
```

### 3. Create GitHub labels (once)

Run this once to ensure all required labels exist on the repo:

```bash
python github_client.py --setup-labels
```

Labels to create if missing:
`bug`, `feature`, `ux`, `crash`, `feedback`,
`priority:critical`, `priority:high`, `priority:medium`, `priority:low`,
`user-reported`

### 4. Fill in `.env`

```bash
cp .env.example .env
# Fill in ANTHROPIC_API_KEY, GITHUB_TOKEN
```

---

## Agent Logic Spec

### Entry point — `agent.py`

```
main():
  load .env
  processed_ids = load_set(PROCESSED_IDS_FILE)

  emails = gmail_client.fetch_unread(GMAIL_FILTER)

  for email in emails:
    if email.id in processed_ids:
      continue

    result = process_email(email)

    audit.append(result)
    processed_ids.add(email.id)
    save_processed_ids(processed_ids)

process_email(email) → AuditRecord:
  1. parse attachments → attachment_context (str or None)
  2. classify(email, attachment_context) → category, severity
  3. act(email, category, severity, attachment_context) → AuditRecord
```

### `classifier.py`

#### Step 1 — Classification

Model: `claude-haiku-4-5-20251001`

Prompt template:
```
You are an email triage assistant for MangaLens, a Chrome extension that
translates manga pages to English.

Classify the following email into exactly one category:
- BUG_REPORT: the user is reporting a crash, broken feature, or wrong output
- FEATURE_REQUEST: the user is requesting an enhancement or new capability
- FEEDBACK: general praise, complaint, question, or comment not tied to a specific bug
- SPAM: unsolicited commercial email, phishing, newsletter, out-of-office, or irrelevant

Rules:
- If the email is an automated reply (out-of-office, delivery failure, newsletter),
  classify as SPAM.
- If you are genuinely uncertain between BUG_REPORT and FEEDBACK, prefer BUG_REPORT.
- Reply with a JSON object only. No explanation.

Email:
From: {from}
Subject: {subject}
Body:
{body}

{attachment_section}

Reply format:
{{"category": "BUG_REPORT"|"FEATURE_REQUEST"|"FEEDBACK"|"SPAM"}}
```

`attachment_section` is included only when attachments were parsed:
```
Attachment analysis:
{attachment_context}
```

#### Step 2 — Severity (BUG_REPORT and FEATURE_REQUEST only)

Model: `claude-haiku-4-5-20251001`

Prompt template:
```
You are assessing the severity of a user-reported issue for MangaLens,
a Chrome extension that translates manga pages to English.

Severity levels:
- CRITICAL: extension completely non-functional, data loss, security issue,
  affects all users
- HIGH: core feature broken for the user, no workaround available
- MEDIUM: feature partially broken or degraded, workaround exists
- LOW: cosmetic issue, minor inconvenience, edge case

Email:
From: {from}
Subject: {subject}
Body:
{body}

{attachment_section}

Reply with a JSON object only. No explanation.
{{"severity": "CRITICAL"|"HIGH"|"MEDIUM"|"LOW"}}
```

#### Step 3 — Deduplication (BUG_REPORT and FEATURE_REQUEST only)

Fetch all open GitHub issues (title + body, truncated to 500 chars each).
Pass them to Claude with the new email.

Model: `claude-haiku-4-5-20251001`

Prompt template:
```
You are checking whether a new user report is a duplicate of an existing
GitHub issue for MangaLens (a manga translation Chrome extension).

New report:
Subject: {subject}
Body: {body}

Existing open issues:
{issues_list}

For each existing issue, the format is:
#{{number}}: {{title}}
{{body_truncated}}
---

If any existing issue describes substantially the same problem as the new
report (same root cause, same broken behaviour), return the issue number
and a confidence score.

Reply with a JSON object only. No explanation.
If a duplicate exists with confidence >= 0.85:
  {{"is_duplicate": true, "duplicate_issue_number": 42, "confidence": 0.91}}
If no duplicate:
  {{"is_duplicate": false, "duplicate_issue_number": null, "confidence": 0.0}}
```

`issues_list` format:
```
#12: Translation spinner never disappears
The spinner appears but the translated image never replaces the original...
---
#15: Korean manga not translated correctly
When I visit a Korean manhwa site the text comes out as garbage...
---
```

### `attachment_parser.py`

Called before classification if the email has attachments.

**Image attachments** (MIME type starts with `image/`):
Model: `claude-sonnet-4-6` (vision)

Prompt template:
```
This screenshot was attached to a bug report for MangaLens, a Chrome
extension that translates manga pages to English.

Describe what you see that appears to be wrong, broken, or unexpected.
Focus on: error messages, UI anomalies, incorrect text, missing elements.
Be concise — 2-4 sentences max. If nothing appears wrong, say so.
```

Pass the image as a base64 `image` content block.

**Text/log attachments** (MIME type `text/plain` or filename ends in `.log`, `.txt`):
Model: `claude-haiku-4-5-20251001`

Prompt template:
```
The following text was attached to a bug report for MangaLens, a Chrome
extension that translates manga pages to English. It may be a console log,
error log, or plain text.

Extract the most relevant error messages, stack traces, or anomalies.
Present them as a concise bullet list (max 5 bullets). If nothing relevant
is found, return an empty string.

Attachment content (truncated to 4000 chars):
{content}
```

**Other attachment types**: skip silently, log MIME type in audit record.

`attachment_parser.py` returns a single string `attachment_context` that is
passed to the classifier prompts. If multiple attachments, concatenate with `\n\n`.
If no attachments, returns `None`.

### `github_client.py`

#### create_issue(title, body, labels)

```python
repo.create_issue(
    title=title,
    body=body,
    labels=labels
)
```

Issue body template (filled by Claude — see below):
```markdown
## Summary
{summary}

## Steps to Reproduce
{steps}

## Expected Behaviour
{expected}

## Actual Behaviour
{actual}

## Severity
{severity}

## Attachments Analysis
{attachment_context or "None"}

---
*Source: user email (ID redacted) — auto-filed by MangaLens email agent*
```

Claude prompt to generate issue fields:

Model: `claude-haiku-4-5-20251001`

```
You are writing a GitHub issue for MangaLens (a manga translation Chrome
extension) based on a user email.

Email:
From: {from}
Subject: {subject}
Body: {body}

{attachment_section}

Generate a GitHub issue with these fields. Be concise and technical.
Infer steps to reproduce from context; if not clear, write "Not provided".

Reply with a JSON object only:
{{
  "title": "concise imperative title, max 60 chars",
  "summary": "1-2 sentence summary of the issue",
  "steps": "numbered list of steps to reproduce, or 'Not provided'",
  "expected": "what should happen",
  "actual": "what actually happens",
  "labels": ["bug"|"feature"|"ux"|"crash"|"feedback", "priority:high"|...]
}}

Label rules:
- Always include "user-reported"
- Include "bug" for BUG_REPORT, "feature" for FEATURE_REQUEST
- Include "crash" if the extension stops working entirely
- Include "ux" if it's a visual or usability issue
- Include the priority label matching the severity: {severity}
```

#### comment_on_issue(issue_number, body)

```python
issue = repo.get_issue(issue_number)
issue.create_comment(body)
```

Comment body template:
```markdown
Another user reported the same issue via email.

**Additional context:**
{email_summary}

{attachment_context_if_any}

*Auto-filed by MangaLens email agent*
```

`email_summary` is a 1-2 sentence Claude-generated summary of the new email,
generated with the same haiku model using a simple summarisation prompt.

### `gmail_client.py`

#### fetch_unread(query) → list[Email]

```python
service.users().messages().list(userId="me", q=query).execute()
# then fetch full message for each ID
service.users().messages().get(userId="me", id=msg_id, format="full").execute()
```

`Email` dataclass fields:
```python
@dataclass
class Email:
    id: str
    thread_id: str
    from_address: str
    subject: str
    body: str          # plain text, decoded from base64
    attachments: list[Attachment]  # list of {filename, mime_type, data_b64}
    raw_headers: dict
```

Body extraction: prefer `text/plain` part. If only `text/html`, strip tags.
Truncate body to 8000 characters before passing to Claude.

#### send_reply(thread_id, to_address, subject, body)

```python
message = create_message(to_address, "Re: " + subject, body)
message["threadId"] = thread_id
service.users().messages().send(userId="me", body=message).execute()
```

#### mark_read(message_id)

```python
service.users().messages().modify(
    userId="me", id=message_id,
    body={"removeLabelIds": ["UNREAD"]}
).execute()
```

#### mark_spam(message_id)

```python
service.users().messages().modify(
    userId="me", id=message_id,
    body={"addLabelIds": ["SPAM"], "removeLabelIds": ["INBOX", "UNREAD"]}
).execute()
```

### Auto-reply drafting

Model: `claude-haiku-4-5-20251001`

**BUG_REPORT reply prompt:**
```
Write a short, friendly reply to a user who reported a bug in MangaLens
(a Chrome extension that translates manga pages to English).

Their issue has been filed as GitHub issue #{issue_number}:
{issue_url}

Their email:
Subject: {subject}
Body (truncated): {body_truncated}

Rules:
- Sound human, not like a bot
- Thank them genuinely
- Mention the GitHub issue number and link
- 3-5 sentences max
- Do not promise a fix timeline
- Sign off as "The MangaLens Team"

Reply with plain text only — no subject line, no JSON.
```

**FEATURE_REQUEST reply prompt:**
```
Write a short, friendly reply to a user who requested a feature for MangaLens
(a Chrome extension that translates manga pages to English).

Their request has been logged as GitHub issue #{issue_number}:
{issue_url}

Their email:
Subject: {subject}
Body (truncated): {body_truncated}

Rules:
- Sound human, not like a bot
- Thank them for the suggestion
- Mention it's been logged
- Set honest expectations (no promises)
- 3-5 sentences max
- Sign off as "The MangaLens Team"

Reply with plain text only.
```

**FEEDBACK reply prompt:**
```
Write a short, friendly reply to a user who sent feedback about MangaLens
(a Chrome extension that translates manga pages to English).

Their email:
Subject: {subject}
Body (truncated): {body_truncated}

Rules:
- Sound warm and genuine
- If positive feedback, express real gratitude
- If negative/complaint, acknowledge their experience without being defensive
- 2-4 sentences max
- Sign off as "The MangaLens Team"

Reply with plain text only.
```

---

## Eval Framework

### Running evals

```bash
cd tools/email-agent
python evals/run_evals.py
```

Runs all 15 fixtures offline (no Gmail/GitHub API calls). Prints a report
and writes `evals/results/latest.json`. Exits with code 1 if overall
score < 0.80.

### Fixture format

```json
{
  "id": "01_bug_stacktrace",
  "email": {
    "from": "user@example.com",
    "subject": "Extension crashes on MangaDex",
    "body": "Hi,\n\nI get this error when I try to translate...\n\nTypeError: Cannot read properties of null\n  at translateImage (content.js:103)",
    "attachments": []
  },
  "expected": {
    "category": "BUG_REPORT",
    "severity": "HIGH",
    "should_create_issue": true,
    "should_reply": true,
    "is_duplicate": false
  },
  "notes": "Clear bug with stack trace — should be easy to classify correctly"
}
```

### Scoring formula

`run_evals.py` calls `classifier.classify()` and `classifier.extract_severity()`
for each fixture (with GitHub API mocked to return no existing issues).

Metrics:
```
category_accuracy   = correct_categories / total
severity_accuracy   = correct_severities / total_with_severity  (BUG+FEATURE only)
action_accuracy     = correct_actions / total
  (action correct if: should_create_issue matches, should_reply matches)

per_category_precision = TP / (TP + FP)  for each category
per_category_recall    = TP / (TP + FN)  for each category

overall_score = (category_accuracy * 0.5) +
                (severity_accuracy * 0.3) +
                (action_accuracy   * 0.2)
```

Pass threshold: `overall_score >= 0.80`

### Human-readable report format

```
MangaLens Email Agent — Eval Report
=====================================
Fixtures run : 15
Overall score: 0.87  ✓ PASS

Category accuracy : 13/15 (0.87)
Severity accuracy :  8/9  (0.89)
Action accuracy   : 14/15 (0.93)

Per-category results:
  BUG_REPORT      precision=1.00  recall=0.83
  FEATURE_REQUEST precision=1.00  recall=1.00
  FEEDBACK        precision=0.75  recall=1.00
  SPAM            precision=1.00  recall=1.00

Failures:
  03_ambiguous_bug_or_feedback — expected BUG_REPORT, got FEEDBACK
  06_angry_complaint           — expected severity HIGH, got MEDIUM
```

### `evals/results/latest.json` format

```json
{
  "timestamp": "2026-04-28T12:00:00Z",
  "overall_score": 0.87,
  "passed": true,
  "category_accuracy": 0.87,
  "severity_accuracy": 0.89,
  "action_accuracy": 0.93,
  "per_category": { ... },
  "failures": [ ... ],
  "fixtures_run": 15
}
```

---

## Dashboard

Single file: `tools/email-agent/dashboard.html`

The dashboard is served at `https://manga-lens.com/dashboard` protected by
nginx Basic Auth (see Deployment section). It can also be opened locally as
a file (`file://`) — in that case the user selects `audit.jsonl` via a
file picker.

### Layout

```
┌─────────────────────────────────────────────────────┐
│  MangaLens Email Agent Dashboard                    │
├──────────┬──────────┬──────────┬────────────────────┤
│ Total    │ Bugs     │ Spam     │ Feature Requests   │
│ 42       │ 18       │ 12       │ 7                  │
├──────────┴──────────┴──────────┴────────────────────┤
│  [Pie chart — category breakdown]                   │
├─────────────────────────────────────────────────────┤
│  Filters: [category ▼]  [from date]  [to date]     │
├──────────┬──────────┬────────┬────────┬─────────────┤
│ Date     │ From     │Subject │Category│ Issue       │
├──────────┼──────────┼────────┼────────┼─────────────┤
│ 2026-... │ user@... │ ...    │ BUG    │ #42 link    │
└──────────┴──────────┴────────┴────────┴─────────────┘
```

### Implementation

- No external dependencies — pure HTML + inline CSS + vanilla JS
- On load: if URL param `?data=` is present, fetch that path; otherwise show
  a `<input type="file">` picker for `audit.jsonl`
- Parse JSONL line by line (split on `\n`, `JSON.parse` each line)
- Pie chart: drawn on a `<canvas>` element using 2D context arc calls
- Summary cards: computed from parsed records
- Table: rendered as `<table>`, sorted by timestamp descending
- Filter controls re-render the table on change (no page reload)

### Fields shown in table

| Column | Source field |
|---|---|
| Date | `timestamp` (formatted as `YYYY-MM-DD HH:mm`) |
| From | `from` |
| Subject | `subject` |
| Category | `category` (colour-coded badge) |
| Severity | `severity` or `—` |
| Issue | link to `github_issue_url` or `duplicate_of` or `—` |
| Action | `action_taken` |
| Tokens | `tokens_used` |

---

## Deployment on GCE

### Install dependencies (once)

```bash
cd ~/manga-lens/tools/email-agent
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### Systemd timer — `systemd/email-agent.timer`

```ini
[Unit]
Description=MangaLens email agent timer

[Timer]
OnBootSec=2min
OnUnitActiveSec=5min
Unit=email-agent.service

[Install]
WantedBy=timers.target
```

### Systemd service — `systemd/email-agent.service`

```ini
[Unit]
Description=MangaLens email agent
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
User=abraham
WorkingDirectory=/home/abraham/manga-lens/tools/email-agent
ExecStart=/home/abraham/manga-lens/tools/email-agent/venv/bin/python agent.py
StandardOutput=journal
StandardError=journal
```

### Activate (once)

```bash
sudo cp systemd/email-agent.timer /etc/systemd/system/
sudo cp systemd/email-agent.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now email-agent.timer
sudo systemctl list-timers email-agent.timer  # verify
```

### View logs

```bash
journalctl -u email-agent.service -f
```

### Dashboard — nginx Basic Auth

Serve `dashboard.html` at `https://manga-lens.com/dashboard`, protected
by a password so only you can access it.

**One-time setup on GCE instance:**

```bash
sudo apt install apache2-utils
sudo htpasswd -c /etc/nginx/.htpasswd abraham
# enter your chosen password when prompted
sudo cp ~/manga-lens/tools/email-agent/dashboard.html /var/www/manga-lens.com/dashboard.html
```

**Add to the nginx 443 block for `manga-lens.com`** (in
`/etc/nginx/sites-enabled/mangalens`, inside the `manga-lens.com` server block):

```nginx
location /dashboard {
    root /var/www/manga-lens.com;
    try_files /dashboard.html =404;
    auth_basic "MangaLens Admin";
    auth_basic_user_file /etc/nginx/.htpasswd;
}
```

Then reload:
```bash
sudo nginx -t && sudo systemctl reload nginx
```

Access at: `https://manga-lens.com/dashboard`
The dashboard page fetches audit.jsonl from a relative path — copy or
symlink `logs/audit.jsonl` to `/var/www/manga-lens.com/audit.jsonl` and
add a cron to refresh it (or serve it via a second nginx location):

```nginx
location /audit.jsonl {
    alias /home/abraham/manga-lens/tools/email-agent/logs/audit.jsonl;
    auth_basic "MangaLens Admin";
    auth_basic_user_file /etc/nginx/.htpasswd;
    add_header Content-Type "application/x-ndjson";
}
```

The dashboard loads `audit.jsonl` by appending `?data=/audit.jsonl` to its
URL, so the full access flow is:
`https://manga-lens.com/dashboard?data=/audit.jsonl`

---

## `requirements.txt`

```
google-api-python-client==2.125.0
google-auth-oauthlib==1.2.0
anthropic>=0.28.0
PyGithub==2.3.0
python-dotenv==1.0.1
```

---

## `.gitignore` additions

Add to root `.gitignore`:
```
tools/email-agent/.env
tools/email-agent/credentials.json
tools/email-agent/token.json
tools/email-agent/processed.txt
tools/email-agent/logs/
tools/email-agent/evals/results/
```

---

## Definition of Done

- [ ] `setup_auth.py` completes without error and writes `token.json`
- [ ] `agent.py` fetches unread emails matching the filter
- [ ] A real test email to `hello@manga-lens.com` is classified correctly
- [ ] BUG_REPORT email creates a GitHub issue on `AbrahamOsmondE/manga-lens`
- [ ] Reply is received in the sender's inbox with correct GitHub issue link
- [ ] SPAM email is archived and marked spam — no reply sent
- [ ] Sending the same email twice results in one issue, not two (idempotency)
- [ ] A known duplicate email comments on the existing issue instead of creating a new one
- [ ] Image attachment: Claude Vision description appears in the GitHub issue body
- [ ] Log file attachment: extracted errors appear in the GitHub issue body
- [ ] `python evals/run_evals.py` exits 0 with score ≥ 0.80
- [ ] `dashboard.html` loads `audit.jsonl` and renders table + pie chart correctly
- [ ] Dashboard is accessible at `https://manga-lens.com/dashboard` (password protected)
- [ ] systemd timer runs every 5 minutes — verified with `systemctl list-timers`
- [ ] `journalctl -u email-agent.service` shows structured logs for each email processed

---

## Known Constraints and Failure Modes

| Failure | Behaviour |
|---|---|
| Gmail API quota exceeded | Log error, skip batch, retry next tick |
| Anthropic API timeout | Log error, mark email as unprocessed (do not add to processed.txt) |
| GitHub API rate limit | Log error, skip issue creation, mark email as unprocessed |
| Email body > 8000 chars | Truncate to 8000 chars before Claude call |
| Attachment > 5 MB | Skip attachment processing, note in audit log |
| Non-UTF-8 email body | Decode with `errors="replace"`, log warning |
| Duplicate detection returns malformed JSON | Default to `is_duplicate: false`, create new issue |
| Gmail token expired | `google-auth-oauthlib` refreshes automatically using stored refresh token |
| `processed.txt` deleted | Agent re-processes all emails — safe because GitHub issue creation is the only side effect, and duplicates are caught by the deduplication step |
| Agent crashes mid-run | Email not added to `processed.txt` — will be retried next tick |

**Do not use** `batch/` Gmail endpoints — the simple list+get approach is
sufficient at this volume and avoids quota complexity.

**Never log** full email bodies to `journalctl` — they may contain PII.
Log only: message ID (truncated to 8 chars), category, action taken, token count.

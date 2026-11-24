# Security review

Review date: 2026-11-21. Scope: the local HTTP server (`app/server.py`), the
desktop UI (`app/web`), file handling (`app/store.py`, `app/ocr.py`), Excel/CSV
output (`app/exporter.py`, copy/save in the UI) and secret handling (`.env`).

Run the automated checks with:

```powershell
.\.venv\Scripts\python tests\security_test.py
.\.venv\Scripts\pip-audit -r requirements.txt
```

Latest result: **30/30 security tests pass**; pip-audit reports **no known
vulnerabilities** in the pinned dependencies.

## Threat model

| Attacker | Can reach | Wants |
|---|---|---|
| A malicious website open in any browser on this PC | `127.0.0.1:8765` via the victim's browser (CSRF, DNS rebinding) | the LLM API key, document contents, local files |
| Another device on the same Wi-Fi / hotspot | `<PC IP>:8765` | upload junk, read documents, change settings |
| A booby-trapped document (photo/PDF from anyone) | the OCR/AI pipeline and the exported workbook | crash the app, run formulas in Excel, mislead the AI |

## Findings and fixes

| # | Severity | Finding | Fix |
|---|---|---|---|
| 1 | High | **DNS rebinding.** A web page whose domain re-resolves to 127.0.0.1 could call the desktop API as same-origin, point `LLM_BASE_URL` at its own server and receive the API key on the next AI request, read OCR'd documents, import local files and write files via export. | Desktop routes require `Host: 127.0.0.1:<port>` or `localhost:<port>` **and** a per-launch random key (`X-D2X-Key` header) that is injected into the desktop page only. |
| 2 | Medium | **CSRF.** Simple cross-site form posts (no preflight) reached `/api/upload` and `/api/scan`. | Same per-launch key; a custom header cannot be sent cross-site without a CORS preflight, which the server never approves. |
| 3 | Medium | **API key redirection.** Changing the API URL in Settings kept the stored key, so it would be sent to the new host. | Moving an endpoint to a different host requires typing the key again (`key_required`). |
| 4 | Medium | **Formula injection.** Document text such as `=HYPERLINK(...)` or `=cmd|...` became a live formula in the exported `.xlsx`, CSV and clipboard copy. | Text starting with `= + - @` is written as a text cell; CSV/clipboard values get a leading `'`. Real numbers (e.g. `-12.50`) still export as numbers. |
| 5 | Low | Desktop assets (`/static`) were readable from the LAN. | Desktop page and assets are now localhost-only. |
| 6 | Low | No browser security headers. | CSP, `X-Frame-Options: DENY`, `nosniff`, `Referrer-Policy: no-referrer` (stops the phone token leaking). |
| 7 | Low | Multiple app instances shared port 8765 (Windows `SO_REUSEADDR`), so requests could reach a different instance. | Single-instance mutex plus an exclusive port probe. |

## Verified as already safe

- Phone page and upload need a 72-bit random token that changes every launch; the desktop API is refused from LAN addresses even with the right key.
- Uploads: extension allow-list (images/PDF only), 80 MB limit, files stored under random names inside the inbox (path traversal names are harmless).
- Decompression-bomb images fail with an error, and the worker keeps running.
- PDFs are capped at 30 pages.
- XSS: file names, OCR text and AI output are HTML-escaped in the UI. A file named `<img onerror=...>` renders as text.
- Secrets: API keys are kept in Windows Credential Manager (DPAPI, current user, `CRED_PERSIST_LOCAL_MACHINE`), named per install folder; plain-text keys found in `.env` are moved there and blanked (`tests/keystore_test.py`). They are never returned by any API or shown in Settings, and never logged.
- Updates: only HTTPS release assets of `github.com/weilun9/doc2excel` are downloaded, checked against the size and sha256 digest GitHub reports, then installed (`tests/updater_test.py`).

## Residual risks (accepted)

- **Data at rest:** imported documents and OCR text are stored unencrypted in `%LOCALAPPDATA%\Doc2Excel`, protected only by the Windows user account. API keys are encrypted, but any program running as the same Windows user (e.g. malware) can still ask Credential Manager for them.
- **Release integrity:** the sha256 check catches damaged or altered downloads, not a compromised GitHub account; installers are not code-signed. Keep two-factor login on the GitHub account.
- **Third-party AI:** in AI mode, the OCR text (and the image, with a vision model) is sent to the configured provider. Offline mode sends nothing.
- **Prompt injection:** a document can contain text that tries to steer the AI. The AI can only fill a table (no tools, no file access), and every table is reviewed before export.
- **Public networks:** Windows Firewall allows Doc2Excel on public networks so phone upload works. On untrusted Wi-Fi, the token-protected phone page is reachable by others on that network. Close the app when you are not using it.
- The server is Werkzeug's built-in server. That is acceptable for a single-user local tool, but it is not meant to be exposed to the internet.

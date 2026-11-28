# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

(A local Flask page shown in a pywebview / WebView2 window on Windows; the
phone upload page is mobile web.)

## Users

The owner, working alone, reviews a handful of supplier bills a day (scanned
or phone photos of Malaysian supplier invoices and receipts) and keys them into
SmartAcc DynaMod or sends them to an AI ERP. Each bill is checked carefully
against its photo before it is confirmed.

## Product Purpose

Turn paper documents into checked, structured data: OCR the image, let an AI
(or offline rules) build header fields and line items, let the user compare
and correct them against the original, then export to Excel, produce DynaMod
import files, or send the file to the ERP. Success is a bill confirmed
correctly with the fewest clicks and the least eye travel between photo and
table.

## Positioning

Runs entirely on the user's PC: offline OCR and offline table rules still work
with no network, phone upload works over the local Wi-Fi or a USB cable, and
no document leaves the machine unless the user configures an AI or ERP. It
knows the SmartAcc DynaMod import format and the user's own supplier/barcode
template.

## Operating Context

- Inputs: phone QR upload, choose files, WIA scanner, watched folder, drag and
  drop, Ctrl+V paste.
- Pipeline per document: queued → read text (OCR) → build table (AI) → to
  review → confirmed; also error. ERP state: sending / sent / already / failed.
- Review: original photo on the left (pages, zoom, rotate), draggable divider,
  result on the right with Table and OCR text tabs; Edit, Undo, Cancel,
  Confirm, Re-run AI, Re-run OCR, Copy, Save to file, Send to ERP.
- Outputs: Excel export (per document / combined + summary / both), DynaMod
  work sheet and import files, ERP scan queue.
- Settings: AI model, vision OCR model, photo clean-up, ERP connection,
  DynaMod paths, version and self-update.

## Capabilities and Constraints

- Plain HTML/CSS/JS in `app/web/`, served by the local Flask app; no build
  step. Must run offline: no CDN, any library is vendored into `app/web/`.
- Every `/api` call carries the per-launch key (`X-D2X-Key` header, `keyed()`
  for URLs); API keys are never shown back to the page.
- UI in English and Chinese (`i18n.js`); every new string needs both.
- Recognised content on the review screen stays at 1.5x the base size.

## Product Principles

1. Review speed first: photo and table side by side, the next action always in
   reach, no hunting through menus to confirm a bill.
2. Status at a glance: what is processing, needs review, confirmed, failed or
   sent must be readable from the list without opening anything.
3. Trust through accuracy: nothing is confirmed, rotated or re-read without the
   user seeing what changes; edits are undoable.
4. Local and private by default.

## Accessibility & Inclusion

English is the primary UI language; Chinese is secondary and must fit the same
layouts. Respect `prefers-reduced-motion`. Keyboard use matters in the review
grid (Enter moves down, Esc closes, Ctrl+Z undoes).

---
name: Doc2Excel
description: Midnight Transit — the batch of bills as a rail line, reviewed beside the photo.
colors:
  ink: "#0f1d3a"
  ink-2: "#1a2a4d"
  text: "#14203b"
  muted: "#5b6478"
  porcelain: "#f7f5ef"
  paper: "#ffffff"
  line: "#e3dfd3"
  line-2: "#d6d1c2"
  photo-ground: "#e4e0d4"
  scarlet: "#d7372f"
  scarlet-press: "#b92c25"
  cobalt: "#2457c5"
  amber: "#e9a300"
  green: "#1f8a4c"
  tint: "#eaf0fb"
typography:
  body:
    fontFamily: "Atkinson Next, Segoe UI, Microsoft YaHei UI, sans-serif"
    fontSize: "14px"
    fontWeight: 400
    lineHeight: 1.45
  title:
    fontFamily: "Atkinson Next, Segoe UI, Microsoft YaHei UI, sans-serif"
    fontSize: "27px"
    fontWeight: 700
    lineHeight: 1.2
  review-data:
    fontFamily: "Atkinson Next, Segoe UI, Microsoft YaHei UI, sans-serif"
    fontSize: "21px"
    fontWeight: 600
    lineHeight: 1.3
  ocr-text:
    fontFamily: "Consolas, Microsoft YaHei UI, monospace"
    fontSize: "19.5px"
    fontWeight: 400
    lineHeight: 1.5
  label:
    fontFamily: "Atkinson Next, Segoe UI, Microsoft YaHei UI, sans-serif"
    fontSize: "12.5px"
    fontWeight: 500
    lineHeight: 1.4
rounded:
  sm: "6px"
  md: "8px"
  lg: "10px"
  xl: "14px"
spacing:
  xs: "4px"
  sm: "8px"
  md: "14px"
  lg: "20px"
  xl: "28px"
components:
  button-primary:
    backgroundColor: "{colors.scarlet}"
    textColor: "{colors.paper}"
    rounded: "{rounded.md}"
    padding: "8px 14px"
  button-primary-hover:
    backgroundColor: "{colors.scarlet-press}"
  button-secondary:
    backgroundColor: "{colors.paper}"
    textColor: "{colors.text}"
    rounded: "{rounded.md}"
    padding: "8px 14px"
  button-on:
    backgroundColor: "{colors.ink}"
    textColor: "{colors.porcelain}"
  shell:
    backgroundColor: "{colors.ink}"
    textColor: "{colors.porcelain}"
    height: "76px"
  inbox-row-open:
    backgroundColor: "{colors.tint}"
  tab-active:
    backgroundColor: "{colors.ink}"
    textColor: "{colors.porcelain}"
    rounded: "{rounded.sm}"
---

# Doc2Excel design system

## Overview

A working tool for one person checking a handful of supplier bills a day. The
whole batch is drawn as a rail line in a slim midnight header: Read, Build,
Review, Confirmed, then branches to Excel, DynaMod and ERP, with a count at
each station. Below it the desk is three work areas: the inbox, the original
photo, and the recognised table. Confirm & next moves the bill one station on
and opens the next. The world lends only type, palette, density and that one
signature move; controls stay ordinary web controls.

## Colors

Restrained: midnight ink frames the work, porcelain grounds it, and four line
inks carry meaning only.

- **ink** — the shell band, active tabs, the selection bar, popovers, toasts.
- **porcelain / paper** — work areas; the photo sits on a darker photo-ground.
- **scarlet** — the review line and the single primary action (Confirm & next,
  Save, Update). Never decoration.
- **cobalt** — ERP (sent) and links. **amber** — DynaMod. **green** — Excel and
  confirmed, and the "totals add up" check.
- Warnings use a pale amber ground with dark amber text; errors a pale red
  ground with dark red text. Text on colour is tinted from that colour, never grey.

## Typography

Atkinson Hyperlegible Next, self-hosted in `app/web/fonts/` (OFL), chosen for
unmistakable 0/O and 1/l/I in invoice numbers. Chinese falls back to Microsoft
YaHei UI. One face on a strict ladder: 12.5 labels, 14 body, 16 headings,
21 review data (the 1.5x rule for recognised content), 27 the bill title.
OCR text stays monospace because it is data. Tabular figures for every number.

## Layout

Header 76px (stations 15px, counts 13px; picker captions sit above their selects). Inbox 310px (272px under 1300px). Photo and result split by a
draggable, keyboard-resizable divider (default 44%, 20-80%). Under 1300px the
engine captions and branch labels hide (a missing-key warning always stays),
the toolbars go icon-only and the next-bill hint hides. Minimum
window 1000x640 has no horizontal overflow.

## Elevation & Depth

Flat work areas separated by 1px lines. Only floating things cast shadows:
the photo sheet, the photo toolbar, popovers, menus, dialogs, toasts — soft,
offset downward, tinted with ink.

## Shapes

Radius 6 (tabs, small chips), 8 (buttons, inputs, tables), 10 (toolbars,
menus), 14 (dialogs, popovers). Stations are circles; the current station is a
double ring. Status markers differ by shape as well as colour: ring = waiting
for review, filled = done, dashed spinning ring = processing, triangle = error.

## Components

- **Line map** (header): stations are buttons that filter the inbox.
- **Inbox row**: checkbox, status marker, title + meta, amount, delete on hover.
- **Selection bar**: appears only when documents are ticked; Excel / DynaMod / ERP.
- **Phone popover**: grows from the Phone button; QR, address picker, count.
- **Review**: floating photo toolbar; Table / OCR text segmented tabs; header
  fields as a grid of cells; proof line for totals; action bar with Edit,
  Re-run menu, and Confirm & next naming the next bill.
- **Settings**: section nav on ink with a status line per section; one form at a time.

## Do's and Don'ts

- Do keep scarlet for the one primary action per view.
- Do show status with shape and colour together.
- Do keep motion to entering things (dialogs, popovers, menus, toasts) and
  press feedback; honour `prefers-reduced-motion`.
- Don't animate polling updates, tab switches or keyboard actions.
- Don't add side stripes, eyebrow labels, glyph icons or gradient text.
- Don't load anything from the network: the app runs offline.

# Doc2Excel

A Windows desktop app that turns scanned or photographed paper documents
(invoices, receipts, statements, forms) into Excel workbooks.

- **Upload from a phone:** press **Phone** (or look at the start screen) for a
  QR code. Scan it with a phone on the same Wi-Fi to open an upload page, then
  take photos or pick images/PDFs. A failed upload can be retried on the phone.
- **Other inputs:** drop files onto the window, choose files, scan from a
  WIA desktop scanner, or save files into the watched folder.
- **OCR on the PC:** local RapidOCR (PaddleOCR models on ONNX Runtime) works
  offline. If you configure a vision LLM, it is used instead while online.
  PDFs that already contain text skip OCR.
- **AI builds the table:** an OpenAI-compatible LLM (DeepSeek by default)
  extracts header fields (supplier, invoice no., dates, totals…) and line
  items. Long documents are sent in parts, so no rows are cut off. With a
  vision model, one call per page reads the image and builds the table.
  When the API is unreachable or no key is set, the app switches to offline
  layout rules automatically: columns are anchored on the table's header row,
  so blank cells stay blank and wrapped descriptions stay in one row.
- **Review, then export:** the line across the top shows how many documents
  are at each step (Read, Build, Review, Confirmed; click one to filter the
  inbox). The next bill to review opens beside its photo; correct it with Edit,
  Undo and Cancel, then **Confirm & next** (Ctrl+Enter) opens the following one.
  A check line says whether the line totals add up to the bill's total. Tick
  documents to export a formatted `.xlsx` (one sheet per document, a combined
  sheet + summary, or both), make DynaMod files or send them to the ERP.

The app runs only while its window is open. Closing it stops the local server,
the folder watcher and all processing. Nothing autostarts.

## Run it

**Installer:** run `Doc2Excel-Setup-<version>.exe`. It installs for the current
user only (no admin) into `%LOCALAPPDATA%\Programs\Doc2Excel`, adds Start menu
and desktop shortcuts, and keeps your existing `.env` when upgrading. Settings
then live in `%LOCALAPPDATA%\Programs\Doc2Excel\.env`.

**Or without installing:**

1. Put `Doc2Excel.exe` in any folder, for example `F:\Apps\Doc2Excel\`.
2. Copy `.env.example` to `.env` in the **same folder** as the exe and fill in
   your key (see below). You can also skip this and use **Settings** in the app,
   which writes the same file.
3. Double-click `Doc2Excel.exe`. The first launch takes a few seconds to unpack.
4. The first time it runs, Windows Firewall asks whether Doc2Excel may use the
   network. Allow **Private networks** so your phone can reach it on Wi-Fi.

### Upload from a phone

- Phone and PC must be on the same Wi-Fi. Guest networks with "client
  isolation" turned on block phone-to-PC traffic.
- Scan the QR code. If the PC has several network adapters, pick the right
  address in the drop-down under the QR code (usually `192.168.x.x`).
- The link contains a random token that changes every time the app starts, so
  an old link stops working once the app is closed.

### Phone shows "cannot connect to server"

1. Check that the phone's IP (iPhone: Settings > Wi-Fi > (i)) starts with the
   same three numbers as the address under the QR code, e.g. `192.168.1.x`.
2. From the PC, run `ping <phone IP>` with the phone screen on. If the result
   is "Destination host unreachable", the router is keeping Wi-Fi devices
   apart ("AP isolation" / "client isolation" / "wireless isolation"). Turn it
   off in the router's admin page (often `http://192.168.1.1`), for the band
   the phone and PC use.
3. If you can't change the router, turn on Windows **Mobile hotspot** on the PC
   and connect the phone to it. Then pick the `192.168.137.1` address in the
   drop-down under the QR code.

### Upload over a USB cable (iPhone)

Works even when Wi-Fi isolates devices, and is the fastest link.

1. Plug the iPhone in, unlock it, tap **Trust**, and turn on **Personal Hotspot**.
2. Once per Windows boot, start Apple's service from an **admin** PowerShell
   (it stays set to Manual, so it never autostarts):
   `Start-Service "Apple Mobile Device Service"`
3. Open Doc2Excel. The QR code automatically prefers the cable/hotspot address
   (`172.20.10.x`); scan it as usual.

### Copy or save extracted text

In the review screen, **Copy** and **Save to file** act on the current tab:
OCR text → clipboard / `.txt`; table → clipboard (paste into Excel) / `.csv`.
Ctrl+V on the main window imports a copied image (e.g. from WeChat).

### Scanner and watched folder

- **Scan from scanner** opens the Windows scanner dialog (WIA). It works with
  most USB/network scanners that have a Windows driver.
- Any image/PDF saved into the watched folder (default
  `Documents\Doc2Excel\Inbox`) is imported within a few seconds and moved to
  `Inbox\imported\`. Point your scanner software there for hands-free import.

## SmartAcc DynaMod supplier bills

For entering supplier invoices / credit notes into SmartAcc DynaMod
(Supplier Invoice or Supplier Credit Note → Import):

1. In **Settings → SmartAcc DynaMod**, pick your own `OCR进单整理模板.xlsx`.
   Its `供应商` sheet (name keyword → Supplier ID) and `条码对照` sheet
   (Supplier ID + item code → system barcode, PCS per unit) are read from that
   file every time. It stays on your PC; nothing from it is built into the app.
2. Scan the bills, check them in the review screen, tick them and press
   **DynaMod 进单**. You get:
   - a filled copy of your template (`表头` + `明细`, your formulas kept) to
     review in Excel. Lines that need you are marked in `标记`; a carton size
     that is unknown is left blank and yellow (the template would count it as 1);
   - one import file per bill (`<SupplierID>-<bill no>.xlsx`, a copy of
     DynaMod's own blank template, values only) in the import folder
     (default `C:\Opp`) for every bill that passes all checks: Supplier ID
     found, every line has a barcode, PCS known, and 明细 total = 纸本小计.
3. For the others: fix them in the work sheet, **save it in Excel**, then press
   **整理表 → 导入文件** and pick it. Import files are made from the green
   `明细` A:E columns, one per 单据ID.

Rules used (same as the template): barcode = system > handwritten > first 12
digits of the printed EAN; PCS = qty × PCS per unit (DOZEN = 12, CTN/OTR/BOX
need a size); Unit Price = ROUND(nett line amount ÷ PCS, 4); an FOC line of the
same item adds to the quantity, amount unchanged.

Command line: `Doc2Excel.exe --process *.jpg --dynamod OCR进单整理模板.xlsx --out 整理.xlsx`
and `Doc2Excel.exe --from-worksheet 整理.xlsx`.

## Where API keys go

API keys (`LLM_API_KEY`, `VISION_API_KEY`, `ERP_TOKEN`) are stored in
**Windows Credential Manager**, encrypted for your Windows user, under names
like `Doc2Excel:LLM_API_KEY:<install folder>`. They are not in the code and not
in `.env`, and the Settings screen never shows a saved key back. A copy of
`.env`, a backup, or another Windows account gets no key.

Enter keys in **Settings**, or type them into `.env` as below: on the next start
Doc2Excel moves them into Credential Manager and blanks the line. Uninstalling
keeps the keys so a reinstall still has them. To remove them, delete the
`Doc2Excel:` entries in Control Panel > Credential Manager > Windows Credentials.
If Credential Manager can't be used, keys stay in `.env` and Settings says so.

```ini
# Text LLM that builds the table (any OpenAI-compatible API)
LLM_BASE_URL=https://api.deepseek.com
LLM_API_KEY=your-deepseek-key
LLM_MODEL=deepseek-chat

# Optional vision model for OCR (must accept images), e.g. Qwen-VL
VISION_BASE_URL=https://dashscope-intl.aliyuncs.com/compatible-mode/v1
VISION_API_KEY=
VISION_MODEL=qwen-vl-max

OCR_MODE=auto   # auto | local | vision
AI_MODE=auto    # auto | online | offline
```

**Online/offline detection:** with `auto`, the app checks whether the API host
is reachable (cached for 20 s). If it is reachable and a key is set, it uses the
AI. Otherwise it falls back to offline rules. The two chips at the top of the
window show which mode is active. Use **Re-run AI** on a document after you
come back online.

## Data locations

| What | Where |
|---|---|
| Settings | `.env` next to `Doc2Excel.exe` |
| API keys | Windows Credential Manager (`Doc2Excel:…` entries) |
| Imported files, previews, session state | `%LOCALAPPDATA%\Doc2Excel\` |
| Watched folder (default) | `Documents\Doc2Excel\Inbox` |
| Log (exe only) | `%LOCALAPPDATA%\Doc2Excel\doc2excel.log` |

Delete a document in the app to remove its stored copy.

## Command-line mode

The exe can also process files without opening a window, which is handy for
batch jobs and testing:

```powershell
.\Doc2Excel.exe --process invoice.png receipt.jpg scan.pdf --out result.xlsx --mode both
```

## Build from source

Tested with Python 3.12 (the pinned `rapidocr-onnxruntime` package supports it).

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt
.\.venv\Scripts\python run.py                     # run the app from source
.\.venv\Scripts\python samples\make_samples.py    # generate test documents
powershell -ExecutionPolicy Bypass -File build.ps1   # -> dist\Doc2Excel.exe (+ installer if NSIS is installed)
```

## Project layout

```
app/
  main.py        entry point: window (pywebview / Edge WebView2), CLI mode
  server.py      local Flask server: desktop API (localhost only) + phone page (token)
  store.py       document queue and background worker (OCR -> table)
  ocr.py         RapidOCR / vision LLM / PDF text layer -> rows of cells + box positions
  layout.py      offline table from box positions: page flattening, deskew,
                 header-anchored columns, wrapped-line merging
  structure.py   LLM table extraction (chunked for long documents) + offline rules
  dynamod.py     SmartAcc DynaMod: bills, 进单整理表, one import file per bill
  exporter.py    formatted .xlsx output
  sources.py     watched folder, WIA scanner, LAN address
  config.py      .env settings
  llm.py         OpenAI-compatible client + reachability check
  web/           desktop UI (index.html, app.js) and phone page (mobile.html)
samples/         sample invoice/receipt generator
assets/dynamod/  DynaMod's blank import templates (header only)
tests/           security_test.py (server/exporter), table_test.py (table building),
                 dynamod_test.py (DynaMod export)
build.ps1        PyInstaller one-file build (+ NSIS installer)
installer/       doc2excel.nsi: per-user installer, keeps .env on upgrade
```

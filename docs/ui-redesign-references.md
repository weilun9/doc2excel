# UI redesign: references and direction

Working notes for the Doc2Excel UI/UX rebuild (`app/web/`). The Figma mock-ups
come first and are approved before any code changes.

## Taste

The user asked for the "impeccable" skill (pbakaus/impeccable, a community
frontend-design skill). It is not installed in this environment, so the design
follows `emil-design-eng` (Emil Kowalski's design-engineering rules), per the
user's standing rule for UI work:

- Motion only where it explains something: modal/sheet enter, toast, status
  change, drop target. No animation on keyboard actions, list polling or tabs
  used many times a day.
- `transform`/`opacity` only; custom ease-out `cubic-bezier(0.23, 1, 0.32, 1)`;
  UI under 300 ms, modals/toasts up to 400 ms; press feedback `scale(0.97)`.
- Hover gated behind `(hover: hover) and (pointer: fine)`.
- `prefers-reduced-motion`: keep fades, drop movement and scale.
- A professional work tool: crisp and fast, no bounce, no decoration.

## Reference list

| Source | Element | Use in Doc2Excel |
| --- | --- | --- |
| [affectUI](https://affectui.com) Dropzone (`/components/dropzone`) | File rows with type badge, progress, per-row retry/remove, live region | "Add documents" card and the phone page upload list |
| affectUI Empty State (`/components/empty-state`) | Icon on a soft tile, title, one line, one button for the first step | Empty document list |
| affectUI Data Table (`/components/data-table`) | Spreadsheet-like cells, edit in place, undo | Line-item grid in review (look only; behaviour stays as today) |
| affectUI Resizable (`/components/resizable`) | Divider with a visible grip, min/max, keyboard handle | Photo / result splitter (add arrow-key resizing) |
| affectUI Toaster (`/components/toaster`) | Calm toasts; promise toast morphs spinner → check | Toasts; ERP send and export progress |
| affectUI App Shell Inset (`/screens/app-shell`) | Inset work area beside a quiet sidebar | Main window layout |
| [React Bits](https://reactbits.dev) Animated List | Selection that glides, arrow-key navigation | Document list keyboard focus (no glide while polling) |
| React Bits Counter | Odometer digits | Selected count in the bulk action bar (static under reduced motion) |
| React Bits Stepper | Step indicator | Per-document pipeline: queued → read text → build table → review |
| [Design Bookmark / Motion](https://designbookmark.com/motion) File drop | Drop target that reacts on drag-over | Window-wide drop overlay |
| Design Bookmark Segmented control / Tab pill | Compact pill switch | OCR/AI mode pickers; Table / OCR text tabs |
| Design Bookmark Step progress, Stat card | Progress, small numbers | Status summary above the list (to review / confirmed / errors) |
| Design Bookmark Sidebar collapse, Toggle switch | Settings navigation, on/off | Settings dialog with section nav; photo clean-up switch |
| Design Bookmark (Kokonut UI) Notification stack, Toolbar | Stacked notices, floating toolbar | Toast stack; floating photo toolbar (page, zoom, rotate) |
| [Mobbin](https://mobbin.com/explore) web patterns: file upload, empty state, tables, settings | Real shipped screens to sanity-check layouts | Checked against settings (section nav left, form right) and bulk-action bars |
| [Scrolltide](https://moge.ai/product/scrolltide) | Prompt/template library for scroll-driven sites; app UI starters (dashboard, workspace) | Little fits a desktop tool; workspace layout only as a cross-check. Its site returned an empty page when fetched. |
| Motion Prompt (Shohei Ohtaki, [listing](https://claudemarketplaces.com/plugins/showheyohtaki-motion-prompt/motion-prompt)) | Naming vocabulary for UI motion | Used to name the few motions above precisely; no official homepage found |

## Direction

- **Main window**: quiet left rail (add files, scanner, watched folder, phone
  QR); the document list as clean rows with a status pill and pipeline stepper;
  a bulk action bar that appears when documents are checked (count, export
  mode, Export Excel, DynaMod, Send to ERP). OCR/AI pickers become segmented
  controls in the top bar with the engine chips beside them.
- **Review**: header with title, status and the primary Confirm; secondary
  actions (Re-run AI, Re-run OCR, Send to ERP) grouped; floating photo toolbar;
  splitter with a visible grip; Table / OCR text as a segmented control; edit
  mode clearly tinted so it is obvious when changes are live. Right side keeps
  the 1.5x text size.
- **Settings**: section nav on the left (AI, Vision OCR, Photo, ERP, DynaMod,
  Version), one section of form on the right; Save/Cancel pinned.
- **Phone page**: big camera button, list with progress per file, same tokens.
- Brand green `#1f6f54` kept; neutral greys; one accent. System fonts only
  (offline). No new libraries.

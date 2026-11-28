---
version: 1
slug: "app-web-index-html"
primary_target: "app/web/index.html"
related_targets: ["app/web/mobile.html"]
---

## Scope

Desktop app window (`app/web/index.html`): inbox, review, settings and dialogs; the phone upload page (`app/web/mobile.html`) shares the world. Mode: Operate.

## Task

One owner checks a handful of supplier bills a day: compare photo and table, correct, confirm, then export to Excel, make DynaMod files or send to the ERP. Success: confirm a bill with the fewest clicks and least eye travel, and read batch status without opening anything. Constraints: offline, no build step, both languages (English first), review text at 1.5x.

## Direction contract

THESIS: The batch is a rail line. Every bill rides Read, Build, Review, Confirmed and branches to Excel, DynaMod or ERP; refuses the category default of a grey list plus a modal review.

OWN-WORLD: Midnight-blue enamel (#0f1d3a) shell band, porcelain (#f7f5ef) work areas, four line inks: scarlet for the review line and primary action, cobalt for ERP, amber for DynaMod, green for Excel/confirmed. Stations are white rings; the current one is a double ring. Humanist sans (Segoe UI) at fixed sizes, tabular figures. Lines bend only at 45/90 degrees.

STORY: The owner sees at a glance how many bills sit at each station, opens the next one to review, checks photo against table, confirms and lands on the next bill.

FIRST VIEWPORT: A slim 56px midnight header: brand left, the pipeline map inline (stations with counts, branch stubs to Excel/DynaMod/ERP), mode pickers and Settings right. Below, three work areas: inbox (320px) with line-colour station dots, photo centre, table right with Confirm & next (scarlet) bottom right naming the next bill.

FORM: Midnight transit diagram (dealt challenger, won on both axes in the bolder hand; user asked for a slimmer header). seed:35771fa8bfcbe367

Signature interaction: the station map in the header is live status; clicking a station filters the inbox; Confirm & next moves the bill one station along. Motion: a station count changes with a short tick; modals and toasts ease out under 300 ms; none under prefers-reduced-motion.

FINISH: unreviewed and undocumented is unfinished; this build ends with the finish review, the verdict, DESIGN.md, and every shipping raster carrying its provenance

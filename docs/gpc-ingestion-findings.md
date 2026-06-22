# GPC guides (Units 27–44) — ingestion findings (issue #8)

Footer/header scan of the two GPC PDFs, recorded the way slice #4 recorded the Solo
1–26 scan. The starting assumption (issue #8) was that the GPC guides are *structurally
similar* to the Solo 1–26 guides built in slices #1–#4/#7. **Mostly true, with real
exceptions** — confirmed by running both PDFs through the existing
`render_unit_markdown` / `write_corpus` pipeline and inspecting where it had to fail loud.

Sources:
- `corpus/00 Combined Trainer Guides units 27-44 GPC.pdf` (104 pages, Units 27–44, all present)
- `corpus/Combined Pilot Guides 27 - 44 GPC.pdf` (101 pages)

## Same as the Solo guides (assumption holds)

- Footer Citation format `Page N - N`; per-unit page numbering; first content page often
  footer-less and inferred as the page before `Page U-2` (same rule as Solo).
- Per-Source running banners (`Gliding Australia Training Manual` / `Trainer Guide` /
  `Pilot Guide`), two-column ruled tables, and the `Suggested Patter:` Reference-Patter
  blocks all behave as in Solo.
- **No A/S/W variant splits** in the 27–44 range (unlike Solo Units 13/14/20).

## Differences found — handled

1. **New / variant section vocabulary** (all print at section size, were emitting
   `content_type: None` silently). Added to `HEADER_DICTIONARY`:
   - Trainer: `PRE-REQUISITE UNITS` (hyphenated), `STUDENT EXERCISES`, `EXERCISES`,
     `BRIEFING`, `PERSONAL/GLIDER/TRAILER AND RETRIEVE PREPARATION`, `CHECKLIST`
     (U35 ground-ops); `SEARCH AND RESCUE`, `BASIC NAVIGATION PRINCIPLES` (U36 nav);
     `INSTRUCTOR NOTES`, `TRAINING NOTES AND LESSON PLANNING FOR POWERED SAILPLANE PILOTS`
     (U42 powered).
   - Pilot: `THREAT AND ERROR MANAGEMENT` (rare on the Pilot side), `EXERCISES FOR THIS
     UNIT`, `PERSONAL/GLIDER/TRAILER PREPARATION`.
   - The scan also surfaced the *same* class of silent `content_type: None` headers in the
     already-committed **Solo** tree (Trainer 21 `RECOGNITION OF PRIOR LEARNING` /
     `RADIOTELEPHONE OPERATOR AUTHORISATION`, Trainer 24 borrowing the Pilot-side
     `WHAT ARE THE PRE-REQUISITES FOR THIS UNIT?` + `BRIEFING`, Pilot 14S `COMMON
     PROBLEMS`, Pilot 17 bare `FLIGHT EXERCISES`, Pilot 9 `.RESOURCES & REFERENCES` with a
     stray leading glyph). All are now mapped.
2. **Loose PyMuPDF block grouping.** Unlike Solo (one element per block outside tables),
   the GPC Trainer guide packs *multiple bullets per block* and *glues a section heading
   onto the tail of the preceding paragraph/bullet*. The renderer now **segments each
   block** into its elements instead of classifying it by its first line. This also
   recovered content that the old head-based classifier had been dropping in several Solo
   units (verified: zero word loss across the Solo tree, the three Solo goldens unchanged).
3. **Section headings wrapped across two lines** (U42 `TRAINING NOTES … FOR POWERED` /
   `SAILPLANE PILOTS`) join into one section; two genuinely-separate sections glued in one
   block (U33 `LESSON PLANNING AND CONDUCT` then `Briefing`) stay separate — the join only
   fires while the open heading is not yet a mapped section.
4. **Section vs. sub-heading by size.** `Briefing` is a 12pt sub-heading in Trainer 13A but
   a 14pt section in Trainer 24 / GPC 33. Section *detection* below 14pt now uses a frozen
   curated vocabulary (the original Solo section headers) so a newly-mapped word can't
   promote a same-named small sub-heading; *content_type* uses the full dictionary.
5. **Pilot title-page running title.** Pilot 33's title page prints `Unit 33` (bold,
   section-sized) on its own line with the name separately; the `Unit NN[- Name]` running
   title is stripped as chrome wherever it appears, and the name is recovered from the
   content page's header.

## Differences found — fail loud (reported, not emitted)

The safety net added this slice: a section-sized heading absent from the dictionary raises
`UnitStructureError` rather than emitting `content_type: None`. With every known heading
mapped, the only remaining loud skips are genuinely-unhandled structure in the **Pilot**
GPC guide, collected by `write_corpus` with a reason:

- **Pilot 37** — duplicate footer run `Page 37-1..3` then `Page 37-1..6` (non-consecutive).
- **Pilot 38, 43, 44** — absent from the Pilot guide (no footers map).

All Trainer Units 27–44 emit; Pilot emits 27–36 and 39–42.

## Known limitation / follow-up

The GPC Trainer **competency table** packs a left-column element label and the row's first
right-column bullet (`● Demonstrate`) into one PyMuPDF block, so the block-x two-column
split renders the label and its sub-bullets less cleanly than the Solo competency table
(content is faithful and complete — nothing is lost). A clean fix needs line-level x
coordinates in the table splitter and must not disturb the Problem/Probable-Cause table
(whose left cells legitimately contain markers); deferred to the ADR-0002 human-
verification gate / a follow-up slice.

# LLM data pipeline (no Excel in chat)

**Rule:** Never paste `.xlsx` into an LLM. Parse once at export time, then use JSON only.

## What’s in each export folder (after finalize)

| File | Purpose |
|------|---------|
| **`features.compact.json`** | All companies, minimal tokens — attach for screening / Prompt 1 |
| **`Company__id__Name.features.json`** | One company, full features — attach 2–3 for deep dive / Prompt 2 |

That’s it. No `consolidated.json`, `features.json`, `screening.md`, or `export-manifest.json` on disk (manifest is used during export, then deleted).

## Pipeline

```
Screener download (.xlsx)
        → ingest per company → *.features.json (xlsx deleted)
        → finalize → features.compact.json (+ prune extras)
```

Bash export runs **2 parallel workers** by default (`SCREENER_EXPORT_WORKERS=2`).

```bash
npm run screener:cli          # options 2/3 export
npm run screener:finalize -- downloads/screen-export-…/   # or menu 4
npm run screener:retry-export -- downloads/screen-export-…/  # menu 5
```

## Manual tools (optional)

```bash
npm run screener:consolidate -- <dir>     # writes consolidated.json (debug only)
npm run screener:features -- consolidated.json
npm run screener:llm-pack -- features.json  # also writes screening.md
```

## Env

```bash
SCREENER_EXPORT_WORKERS=2
SCREENER_EXPORT_DELAY_MS=12000
SCREENER_WORKER_STAGGER_SEC=45
```

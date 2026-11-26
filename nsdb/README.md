# NVPDA Tools

Navypedia mirror data tools ported into the SSW mod workspace.

## Files

- `fetcher.py`: scraping/cache engine. Reads the live site `https://www.navypedia.org` (the 2021 HTTrack mirror `myownonpmirror.com` is only a fallback when the live site answers 5xx), rate-limited to <=1 req/s per host, resumable, and caches fetched HTML on disk so the parser can be re-run offline.
- `NVPEDIA.py`: Tkinter browser for the local NSDB database.
- `scraper.py`: legacy scraper kept for compatibility and one-off rebuild work.
- `SHIP_TYPE_LIST.csv`: legacy ship type label list (no longer used for discovery).

The large SQLite database is not duplicated here. Use:

```bash
python3 tools/nvpda/NVPEDIA.py
```

For batch refresh through the GUI/CLI entry point:

```bash
python3 tools/nvpda/NVPEDIA.py --fetch-all
```

Typical use (never write into the canonical `NSDB.db`; build a new file and swap it in after verification):

```bash
python3 nsdb/fetcher.py --db /path/NEW.db all            # whole site; rerun to resume
python3 nsdb/fetcher.py --db /path/NEW.db status         # progress (read-only)
python3 nsdb/fetcher.py --db /path/NEW.db classes --file urls.txt
python3 nsdb/fetcher.py --db /path/NEW.db --offline --cache-dir /path/NEW_htmlcache all   # rebuild from cached HTML
```

Images default to `--images auto`: copied per `source_url` from the previous `NSDB.db`, fetched only for classes it lacks.

The country entry point is `https://www.navypedia.org/ships_index.htm`. Type pages are discovered structurally (every content link under a country page), not by matching ship-type labels, so `SHIP_TYPE_LIST.csv` is no longer needed for discovery.

New columns (only created in new DB files, or with `--migrate`): `CLASS.Displacement_standard`, `WHEN_DATE`, `HEADING`, `extra_specs` (JSON of labels that have no dedicated column), `ships_kind`, `source_site`, `parser_version`; `SHIPS.Comp`, `Fate`, `Extra`, `seq`; tables `SHIP_GROUPS` (group summary tables), `FETCH_ERRORS`, `FETCH_STATE`. Existing column names and meanings are unchanged. Regression tests: `tests/test_nsdb_fetcher.py` (offline, fixtures in `tests/fixtures/nsdb/`).

# NVPDA Tools

Navypedia mirror data tools ported into the SSW mod workspace.

## Files

- `fetcher.py`: current scraping/cache engine. Uses `https://myownonpmirror.com` and stores data in `nvpda-db/NSDB.db`.
- `NVPEDIA.py`: Tkinter browser for the local NSDB database.
- `scraper.py`: legacy scraper kept for compatibility and one-off rebuild work.
- `SHIP_TYPE_LIST.csv`: ship type label list used while discovering country/type pages.

The large SQLite database is not duplicated here. Use:

```bash
python3 tools/nvpda/NVPEDIA.py
```

For batch refresh through the GUI/CLI entry point:

```bash
python3 tools/nvpda/NVPEDIA.py --fetch-all
```

`fetcher.py` resolves relative mirror links such as `brit_bb_ganges.html` from pages like `https://myownonpmirror.com/ships/uk/uk_battleships.html`, normalizes saved URLs to `.html`, and records `source_url` in `CLASS` and `SHIPS`.

The country entry point is:

```text
https://myownonpmirror.com/ships_index.html
```

`NavypediaFetcher.fetch_countries()` reads this page and returns normalized country records. `fetch_all_countries(refresh_country_index=True)` can rebuild the local `COUNTRIES` table from that mirror index before the batch fetch.

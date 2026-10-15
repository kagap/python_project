# pbigen: data in, Power BI report out

A script that takes a CSV or Excel file and builds a ready-made Power BI project (PBIP): a data model loaded from the file, DAX measures, and a multi-page report
with a side navigation panel, KPI cards, charts, a tooltip page and a collapsible Filters pane. It runs locally and needs no AI model, so it costs no tokens.

```bash
pip install pandas openpyxl
python pbigen.py data.csv --title "Sales dashboard" --open       # builds out/<name>/<name>.pbip and opens it in Power BI Desktop
```

Then, in Power BI Desktop: click **Refresh now** in the banner (the project holds no data until the first refresh), and use **File > Save as > .pbix** if you want a single file.
Power BI has no supported way to write a .pbix directly (its data model is a compressed binary), so the last step is manual. The project format is the one Microsoft documents for generating reports from code.

| Option | What it does |
|---|---|
| `--name`, `--title` | project name and the title shown on the report |
| `--date COLUMN` | date column for the time charts (default: the first date-like column) |
| `--metrics a,b` | numeric columns to build `Total` and `Average` measures for (default: the first four non-ID numeric columns) |
| `--dims x,y` | categorical columns for the filters and chart axes (default: text columns with 2 to 40 distinct values) |
| `--measure "Name=DAX\|format"` | a custom DAX measure, repeatable; `{T}` stands for the table name. Custom measures are used first in the cards and charts |
| `--only-custom` | build only `Rows` and the `--measure` ones, with no automatic Total/Average measures |
| `--embed` | store the data inside the model (CSV only), so the project does not depend on where the file is |
| `--about`, `--source` | text for the About page: what the data is, and where it comes from (licence) |
| `--sheet` | Excel sheet name (default: the first sheet) |
| `--open` | open the project in Power BI Desktop when done |

Example with custom measures:

```bash
python pbigen.py shoppers.csv --name OnlineShoppers --embed --only-custom --date Date --dims "Month,VisitorType,Weekend" \
  --measure "Conversion rate=DIVIDE(CALCULATE(COUNTROWS({T}), {T}[Revenue] = TRUE()), COUNTROWS({T}))|0.0%" \
  --about "12,330 browsing sessions of an online shop." --source "UCI Machine Learning Repository, CC BY 4.0"
```

## What the report looks like

Overview, Breakdown, Compare, Trends (only if there is a date column), Matrix, Details and About pages, plus a hidden Tooltip page.

- **Layout:** a dark navigation panel on the left (the active page is highlighted), white cards with rounded corners, and charts of different sizes: KPI cards on top, the main trend wide on the left, details below.
- **Palette:** navy, teal and one orange accent, set by a custom theme saved in the project; no gridlines, and no value axis where bars carry labels.
- **KPI cards** get a small trend line behind the number when there is a date column.
- **Filters:** the dimensions and the date are report-level filters in the collapsible Filters pane on the right, and they apply to every page.
- **Tooltips:** hovering over a bar, slice or point shows a small card with the main measures for that item.
- **Charts:** horizontal bars sorted from the largest value, columns for short labels such as months and hours, donuts with the total in the middle, a scatter and a treemap on the Compare page, matrices on the Matrix page.

## What it decides for you

- **Types:** integers, decimals (also with a decimal comma), booleans, dates in common formats (ISO, `dd.mm.yyyy`, `dd/mm/yyyy`, `mm/dd/yyyy`) and text.
- **Measures:** `Rows`, your `--measure` ones, and for each metric a `Total` and an `Average`. Rates, prices and scores use the average as the headline, everything else the total.
- **IDs** (mostly unique text or numbers, names like `id`, `key`, `code`) are never used as measures or filters.
- With a date column, a `Month` and a `Year` column are added in Power Query for the time charts.

## Checking the output

`pbivalidate.py` checks every JSON file of a generated project against Microsoft's public schemas (`pip install jsonschema referencing requests`):

```bash
python pbivalidate.py out/<name>
```

## What was tested

Tested with Power BI Desktop 2.158 (Microsoft Store version, October 2026) on three UCI datasets (12,330, 32,561 and 8,760 rows; with and without a date column): the projects open, refresh, and render all pages with the right numbers, and every JSON file passes schema validation.
**Not clicked through automatically:** the tooltips, the Filters pane and the page navigation. **Not yet tested in Desktop:** Excel input, CSV files with a decimal comma or with `dd.mm.yyyy` text dates (the Power Query steps for them are generated, but unchecked), and tables with many more than 14 columns.
The generated layout is a strong default, not a finished design: expect to adjust titles and chart choices.

## Limits

- **Keep the output folder path short.** Windows stops at 260 characters, and Power BI Desktop opens an empty report without a clear error if the deepest file path is longer. The script warns above 240 characters.
- One table only (no joins between files), and no relationships or calendar table. Without `--embed`, the data is read from the path where the file was when the project was generated; if the file moves, change the source in Power Query (Transform data > Source).
- Sparklines on the KPI cards need a date column; without one the cards are plain.

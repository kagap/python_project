# pbigen: data in, Power BI report out

A script that takes a CSV or Excel file and builds a ready-made Power BI project (PBIP): a data model loaded from the file, DAX measures, and a two-page report
(title, slicers, KPI cards, a line chart over time, bar charts, a donut and a details table). It runs locally and needs no AI model, so it costs no tokens.

```bash
pip install pandas openpyxl
python pbigen.py data.csv --title "Sales dashboard" --open       # builds out/<name>/<name>.pbip and opens it in Power BI Desktop
```

Then, in Power BI Desktop: click **Refresh now** in the banner (the project holds no data until the first refresh), and use **File > Save as > .pbix** if you want a single file.
Power BI has no supported way to write a .pbix directly (its data model is a compressed binary), so the last step is manual. The project format is the one Microsoft documents for generating reports from code.

| Option | What it does |
|---|---|
| `--name`, `--title` | project name and the title shown on the report |
| `--date COLUMN` | date column for the time chart (default: the first date-like column) |
| `--metrics a,b` | numeric columns to build measures for (default: the first four non-ID numeric columns, preferring names like amount, sales, price) |
| `--dims x,y` | categorical columns for slicers and chart axes (default: text columns with 2 to 40 distinct values) |
| `--sheet` | Excel sheet name (default: the first sheet) |
| `--open` | open the project in Power BI Desktop when done |

## What it decides for you

- **Types:** integers, decimals (also with a decimal comma), booleans, dates in common formats (ISO, `dd.mm.yyyy`, `dd/mm/yyyy`, `mm/dd/yyyy`) and text.
- **Measures:** `Rows`, then for each metric a `Total` and an `Average`. Rates, prices and scores use the average as the headline, everything else the total.
- **Layout:** with a date column the first chart is a monthly line (a `Month` and a `Year` column are added in Power Query), the next charts are bars by the best categorical columns, and a donut for a column with up to six values.
- **IDs** (mostly unique text or numbers, names like `id`, `key`, `code`) are never used as measures or slicers.

## Checking the output

`pbivalidate.py` checks every JSON file of a generated project against Microsoft's public schemas (`pip install jsonschema referencing requests`):

```bash
python pbivalidate.py out/<name>
```

## What was tested

Tested with Power BI Desktop 2.158 (Microsoft Store version, October 2026) on an 1,243-row CSV with dates, text and numeric columns: the project opens without errors, refreshes and renders both pages,
and all 24 JSON files pass schema validation. **Not yet tested in Desktop:** Excel input, CSV files with a decimal comma or with `dd.mm.yyyy` dates (the Power Query steps for them are generated, but unchecked), and tables with many more than 14 columns.
The generated layout is a sensible default, not a finished design: expect to adjust titles, colours and chart choices.

## Limits

One table only (no joins between files), and no relationships or calendar table. The data is read from the path where the file was when the project was generated; if the file moves, change the source in Power Query (Transform data > Source).

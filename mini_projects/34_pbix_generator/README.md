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
| `--style NAME` | look of the report: `clean` (light, menu on the left, default), `midnight` (dark blue, dropdown filters in the header, tabs under it), `sunset` (brown and orange, title and tabs in one bar), `violet` (purple, menu on the left, round cards), `neon` (near-black with cyan and magenta, tabs along the bottom), `paper` (cream, serif headline, text tabs) or `bordeaux` (warm white, burgundy menu on the right), `factory` (light grey, floating header card with pills, safety yellow), `chalk` (dark green with numbered chips for the pages) or `ash` (charcoal with a filter panel on the left and an air-quality colour scale), `storm` (slate blue with a numbered rail), `terminal` (amber monospace on black with function-key tabs) `garage` (cream and racing red with a floating dock at the bottom), `frost` (airy ice blue with a centred light title and underlined tabs), `pastel` (lavender floating side card, pink, blue and yellow, very round cards) or `newsprint` (serif masthead between double rules, black and red, square flat cards) |
| `--layout NAME` | which pages and charts: `classic` (default: bars, donut, scatter, treemap, matrices), `pulse` (a time series: heat map, combo chart, gauge, waterfall, ribbon, 100% stacked columns), `flow` (categories and outcomes: funnel, ribbon, waterfall, pie, 100% stacked bars) `lab` (numeric columns by type: gauge, multi-row card, small multiples, 100% stacked columns), `board` (a scoreboard: ranked table with shaded cells, outcomes against target), `story` (insight cards beside the charts; takes six `--insight "BIG NUMBER|sentence"` options) or `mix` (stacked columns and bars over bands; needs a date), `atlas` (a tile-grid map, a dot map and a ranking, with no map service), `macro` (latest values and yearly changes, rates, cycles; needs a date) `catalog` (brands, engines and the electric turn of a vehicle catalogue), `climate` (warming stripes, month-by-day heat map, extremes, rain and snow of a long daily record), `pyramid` (population pyramids by sex and age, a tile map and generations) or `bump` (a bump chart of ranks over time, ribbons and a rank heat table) |
| `--geo "Col=Category,..."` | tag columns as geographic (`StateOrProvince`, `Latitude`, `Longitude`) for map visuals; note that Bing maps are being retired and Azure Maps does not work in Publish to web |
| `--rows-name` | name of the row-count measure, for example `Patients` or `Wines` |
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

Sixteen styles (`--style`) and thirteen layouts (`--layout`) are built in, so reports made from different data do not all look alike: a style is a palette plus a header or navigation (menu left, right, tabs on top or at the bottom, filters in the header), a layout is the set of pages and chart types. The description below is of the default `clean` style and `classic` layout; in the dark styles the cards, text and chart colours follow the style's palette.

The extra layouts take their columns by position, so list them in the order shown (with `--dims` and `--measure`):

| Layout | `--dims` in order | `--measure` in order | Needs |
|---|---|---|---|
| `pulse` | hour-like axis, weekday-like columns, a segment, a part-of-day band | average (headline), total, a second average, a maximum, a share | a date column |
| `flow` | ordered group (age), entry type, outcome in stage order, topic, sex, race, stay length, then up to four treatment fields | a rate, two averages, another average, an additive total | one row per case |
| `lab` | item type, an ordered score, three bands | score, three averages, a share, another average | numeric columns |
| `board` | department, team, day, week, target outcome, incentive band, overtime band, style changes | actual (headline), share on target, target, overtime, incentive | a date for the Trend page |
| `story` | subject, sex, study time, mother's education, past failures, weekend alcohol, going out, result, absence band, wants higher education, mother's job, internet | final grade, pass rate, absences, first-period grade, a share | six `--insight` cards |
| `mix` | season, wind, concentration band (in order), heating season, hour | average (headline), a share, temperature, a maximum, wind speed | a date column |
| `atlas` | state, strength group, month, decade, rating, hour | deaths, injuries, average length, a violent count, a share, average width, a tile label (`SELECTEDVALUE` of the state code) | columns `Grid_row`, `Grid_col`, `Tornado_id`, `Start_lat`, `Start_lon` |
| `macro` | recession flag, decade, inflation band, Fed stance | six latest values, their six changes against a year earlier, then eight averages | a date column and a `Year` column (added from the date) |
| `catalog` | class, fuel type, make, transmission, drive, decade, cylinders, engine-size band | average MPG, CO2, fuel cost, EV range, electric share, hybrid and electric share, electric models | a `Year` column |
| `climate` | decade, period (first one is the baseline, named like `1 1869-1900`), month name, season, day of the month, weekday, temperature band, weather type, year band | average temperature, anomaly against the baseline, average high, average low, precipitation, snowfall, hot days, days never thawed, record high, record low, a constant stripe (`=1`), wet days | a date column (a `Year` column is added); `Year_band` colours the stripes: seven bands from below -1.0 to above +2.0 |
| `pyramid` | estimate year, region, state, sex (`Male` and `Female`), age group, life stage, generation | residents, women, men, men mirrored (negative, format `#,0;#,0`), average age, share 65 and over, share under 15, women per 100 men, dependants per 100 of working age, a tile label (`SELECTEDVALUE` of the state code), change between the first and last year | columns Age, Population, Grid_row, Grid_col; every measure should count one year at a time (Estimate_year = MAX(Estimate_year)) |
| `bump` | place, region, period (a census year), group, entry, state | rank axis (negative minimum rank), residents, average rank, best rank, periods in the top, rank change, growth, places, rank score (11 minus rank), largest, rank in the period | columns Rank, Population, Rank_change, Population_growth_pct; measure names must not contain = |

Examples (the first three of each round): [`NYCWeather_CentralPark_1869_2025`](https://github.com/kagap/powerbi/tree/main/NYCWeather_CentralPark_1869_2025) (`frost` + `climate`), [`USPopulation_AgeBySex_2020_2024`](https://github.com/kagap/powerbi/tree/main/USPopulation_AgeBySex_2020_2024) (`pastel` + `pyramid`), [`USCities_Top10_1790_2020`](https://github.com/kagap/powerbi/tree/main/USCities_Top10_1790_2020) (`newsprint` + `bump`), [`USTornadoes_1950_2023`](https://github.com/kagap/powerbi/tree/main/USTornadoes_1950_2023) (`storm` + `atlas`), [`USMacro_Since1990`](https://github.com/kagap/powerbi/tree/main/USMacro_Since1990) (`terminal` + `macro`), [`FuelEconomy_Cars1984_2026`](https://github.com/kagap/powerbi/tree/main/FuelEconomy_Cars1984_2026) (`garage` + `catalog`), [`MetroTraffic_I94Volume`](https://github.com/kagap/powerbi/tree/main/MetroTraffic_I94Volume) (`neon` + `pulse`), [`DiabetesReadmission_US130Hospitals`](https://github.com/kagap/powerbi/tree/main/DiabetesReadmission_US130Hospitals) (`paper` + `flow`) and [`WineQuality_VinhoVerde`](https://github.com/kagap/powerbi/tree/main/WineQuality_VinhoVerde) (`bordeaux` + `lab`).

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

"""pbigen: turn a CSV or Excel file into a ready-made Power BI project (PBIP) with a data model, DAX measures and report pages.

usage:  python pbigen.py data.csv [--name Sales] [--out ./out] [--title "Sales dashboard"] [--sheet Sheet1]
                                  [--date COLUMN] [--metrics a,b,c] [--dims x,y,z] [--culture en-US]

What it does
  1. profiles the file with pandas (types, distinct values, missing values, which columns are IDs, dates, categories, measures);
  2. writes the semantic model as TMDL (one table loaded with Power Query from the file, typed columns, DAX measures);
  3. writes the report as PBIR (a title, slicers, KPI cards, a line chart over time, bar charts, a donut and a details page);
  4. you open `<name>.pbip` in Power BI Desktop, click Refresh once, and use File > Save as to get the .pbix.

Power BI has no supported way to write a .pbix directly (the data model inside is a compressed binary), so the project format is the
route Microsoft documents for generating reports from code. The file formats follow the public schemas in
https://github.com/microsoft/json-schemas (the same versions that the installed Power BI Desktop writes).
"""
import argparse
import csv
import json
import os
import re
import shutil
import subprocess
import sys
import uuid
from datetime import datetime
from pathlib import Path

import pandas as pd

NS = uuid.UUID("6f0c3a52-0c1e-4c0b-9a55-7d1a2b3c4d5e")
SCHEMA = "https://developer.microsoft.com/json-schemas/fabric/item/report/definition"
V_REPORT, V_PAGE, V_PAGES, V_VISUAL, V_VERSION = "3.3.0", "2.1.0", "1.1.0", "2.13.0", "1.0.0"
IMPORT_VERSIONS = {"visual": "2.13.0", "report": "3.4.0", "page": "2.3.1"}
FALLBACK_THEME = "Fluent2-CY26SU09"
PAGE_W, PAGE_H = 1280, 720

DATE_FORMATS = [  # (python strptime, .NET format for Power Query)
    ("%Y-%m-%d", "yyyy-MM-dd"), ("%Y-%m-%d %H:%M:%S", "yyyy-MM-dd HH:mm:ss"), ("%Y-%m-%dT%H:%M:%S", "yyyy-MM-ddTHH:mm:ss"),
    ("%d.%m.%Y", "dd.MM.yyyy"), ("%d.%m.%Y %H:%M", "dd.MM.yyyy HH:mm"), ("%d/%m/%Y", "dd/MM/yyyy"), ("%m/%d/%Y", "MM/dd/yyyy"),
    ("%Y/%m/%d", "yyyy/MM/dd"), ("%d-%m-%Y", "dd-MM-yyyy"), ("%Y-%m", "yyyy-MM"),
]
AVERAGE_HINT = re.compile(r"(rate|ratio|pct|percent|share|avg|average|mean|price|score|rating|age|temp|density|per_|_per|index|margin)", re.I)
ID_HINT = re.compile(r"(^id$|_id$|^id_|uuid|guid|\bkey\b|_key$|^index$|^nr$|number$|code$)", re.I)
METRIC_HINT = re.compile(r"(amount|sales|revenue|total|value|cost|profit|price|quantity|qty|count|units|spend|income|salary|score|population|gdp)", re.I)


# ------------------------------------------------------------------ profiling
def read_table(path, sheet=None):
    path = Path(path)
    if path.suffix.lower() in (".xlsx", ".xlsm", ".xls"):
        return pd.read_excel(path, sheet_name=sheet or 0, dtype=object), {"kind": "excel", "sheet": sheet}
    raw = path.read_bytes()
    for enc in ("utf-8-sig", "cp1250", "latin-1"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    try:
        delim = csv.Sniffer().sniff(text[:20000], delimiters=",;\t|").delimiter
    except csv.Error:
        delim = ","
    df = pd.read_csv(path, sep=delim, encoding=enc, dtype=object, keep_default_na=True, na_values=["", "NA", "N/A", "null", "NULL"])
    return df, {"kind": "csv", "delimiter": delim, "encoding": 65001 if enc.startswith("utf") else 1250 if enc == "cp1250" else 28591}


def classify(df, culture):
    """Return a list of column profiles with a kind: int, float, date, datetime, bool, text."""
    cols = []
    for name in df.columns:
        s = df[name].dropna()
        n, nuniq = len(s), s.nunique()
        info = {"name": str(name).strip(), "orig": name, "non_null": n, "distinct": int(nuniq), "missing_pct": 100 * (1 - n / max(len(df), 1)),
                "kind": "text", "decimal_comma": False, "date_format": None}
        if n == 0:
            info["kind"] = "empty"
        elif s.map(lambda v: isinstance(v, (datetime, pd.Timestamp))).all():
            info["kind"] = "datetime" if (pd.to_datetime(s).dt.normalize() != pd.to_datetime(s)).any() else "date"
            info["date_format"] = None
        elif s.astype(str).str.lower().isin(["true", "false"]).all():
            info["kind"] = "bool"
        else:
            txt = s.astype(str).str.strip()
            if txt.str.fullmatch(r"-?\d+").all() and not txt.str.fullmatch(r"-?0\d+").any():
                info["kind"] = "int"
            elif txt.str.fullmatch(r"-?\d+([.]\d+)?([eE][-+]?\d+)?").all():
                info["kind"] = "float"
            elif txt.str.fullmatch(r"-?\d+,\d+").all():
                info["kind"], info["decimal_comma"] = "float", True
            else:
                for py, net in DATE_FORMATS:
                    try:
                        parsed = pd.to_datetime(txt.head(2000), format=py, errors="coerce")
                    except (ValueError, TypeError):
                        continue
                    if parsed.notna().all():
                        has_time = "%H" in py
                        info["kind"], info["date_format"] = ("datetime" if has_time else "date"), net
                        break
        cols.append(info)
    return cols


def pick_roles(cols, n_rows, args):
    """Decide which columns are the date, the dimensions and the metrics."""
    by_name = {c["name"]: c for c in cols}
    usable = [c for c in cols if c["kind"] != "empty"]

    def is_id(c):
        if c["kind"] in ("int", "text") and c["non_null"] and c["distinct"] / c["non_null"] > 0.9 and c["distinct"] > 30:
            return True
        return bool(ID_HINT.search(c["name"])) and c["kind"] in ("int", "text") and c["distinct"] > 0.5 * c["non_null"]

    dates = [c for c in usable if c["kind"] in ("date", "datetime")]
    date = by_name.get(args.date) if args.date else (sorted(dates, key=lambda c: (not re.search(r"date|time|day|month|data", c["name"], re.I), c["missing_pct"]))[0] if dates else None)

    dims_pool = [c for c in usable if c["kind"] in ("text", "bool") and 2 <= c["distinct"] <= 40 and not is_id(c)]
    dims_pool += [c for c in usable if c["kind"] == "int" and 2 <= c["distinct"] <= 12 and not is_id(c) and not METRIC_HINT.search(c["name"])]
    dims_pool.sort(key=lambda c: (c["missing_pct"] > 30, abs(c["distinct"] - 8), c["name"]))
    if args.dims:
        dims = [by_name[x] for x in args.dims.split(",") if x in by_name]
    else:
        dims = dims_pool

    metric_pool = [c for c in usable if c["kind"] in ("int", "float") and not is_id(c) and c not in dims_pool[:0]
                   and not (c["kind"] == "int" and c["distinct"] <= 12 and not METRIC_HINT.search(c["name"]))
                   and not (re.fullmatch(r"(year|rok|yr)", c["name"], re.I))]
    metric_pool.sort(key=lambda c: (not METRIC_HINT.search(c["name"]), c["missing_pct"] > 30, cols.index(c)))
    metrics = [by_name[x] for x in args.metrics.split(",") if x in by_name] if args.metrics else metric_pool
    dims = [d for d in dims if d not in metrics]
    return date, dims, metrics[:4]


# ------------------------------------------------------------------ names and small helpers
def gid(*parts):
    return str(uuid.uuid5(NS, "|".join(parts)))


def oid(*parts):
    return uuid.uuid5(NS, "|".join(parts)).hex[:20]


def tq(name):
    """TMDL object name: single quotes when it has anything but letters, digits and underscores."""
    return name if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name) else "'" + name.replace("'", "''") + "'"


def tv(value):
    """TMDL text property value."""
    return '"' + value.replace('"', '""') + '"'


def dax_col(table, col):
    return f"{table}[{col.replace(']', ']]')}]"


def m_str(value):
    return '"' + value.replace('"', '""') + '"'


def literal(text):
    return {"expr": {"Literal": {"Value": "'" + text.replace("'", "''") + "'"}}}


def table_name(path, given):
    base = given or Path(path).stem
    base = re.sub(r"[^A-Za-z0-9_]+", "_", base).strip("_") or "Data"
    return base if not base[0].isdigit() else "T_" + base


# ------------------------------------------------------------------ semantic model
def build_measures(table, rows_name, metrics):
    """(name, dax, formatString, kind) for each measure; the first one for each metric is the headline."""
    measures = [(rows_name, f"COUNTROWS({table})", "#,0", "rows")]
    for c in metrics:
        col = dax_col(table, c["name"])
        fmt = "#,0" if c["kind"] == "int" else "#,0.00"
        total, avg = (f"Total {c['name']}", f"Average {c['name']}")
        measures.append((total, f"SUM({col})", fmt, "total"))
        measures.append((avg, f"AVERAGE({col})", "#,0.00", "average"))
    return measures


def headline(metric, measures):
    """The measure that best summarises a metric: an average for rates and prices, a total otherwise."""
    wanted = f"Average {metric['name']}" if AVERAGE_HINT.search(metric["name"]) else f"Total {metric['name']}"
    return wanted


def m_query(src, table, cols, date_col, path):
    steps, last = [], "Source"
    if src["kind"] == "excel":
        steps.append(f'Source = Excel.Workbook(File.Contents({m_str(str(path))}), null, true)')
        steps.append(f'Sheet = Source{{[Item={m_str(src.get("sheet_name") or "Sheet1")},Kind="Sheet"]}}[Data]')
        last = "Sheet"
    else:
        steps.append(f'Source = Csv.Document(File.Contents({m_str(str(path))}), [Delimiter={m_str(src["delimiter"])}, Columns={len(cols)}, '
                     f'Encoding={src["encoding"]}, QuoteStyle=QuoteStyle.Csv])')
    steps.append(f'Promoted = Table.PromoteHeaders({last}, [PromoteAllScalars=true])')
    last = "Promoted"
    plain, comma, textual, dated = [], [], [], []
    for c in cols:
        t = {"int": "Int64.Type", "float": "type number", "bool": "type logical", "text": "type text"}.get(c["kind"])
        if c["kind"] in ("date", "datetime"):
            dated.append(c)
        elif c["kind"] == "float" and c["decimal_comma"]:
            comma.append(f'{{{m_str(c["name"])}, type number}}')
        elif t:
            plain.append(f'{{{m_str(c["name"])}, {t}}}')
    if plain:
        steps.append(f'Typed = Table.TransformColumnTypes({last}, {{{", ".join(plain)}}}, "en-US")')
        last = "Typed"
    if comma:
        steps.append(f'TypedComma = Table.TransformColumnTypes({last}, {{{", ".join(comma)}}}, "pl-PL")')
        last = "TypedComma"
    for i, c in enumerate(dated):
        fn, ty = ("DateTime.FromText", "type datetime") if c["kind"] == "datetime" else ("Date.FromText", "type date")
        fmt = f', Format={m_str(c["date_format"])}' if c["date_format"] else ""
        step = f"Dates{i}"
        if src["kind"] == "excel" or c["date_format"] is None:
            steps.append(f'{step} = Table.TransformColumnTypes({last}, {{{{{m_str(c["name"])}, {ty}}}}}, "en-US")')
        else:
            steps.append(f'{step} = Table.TransformColumns({last}, {{{{{m_str(c["name"])}, each if _ = null or _ = "" then null else '
                         f'{fn}(Text.From(_), [Culture="en-US"{fmt}]), {ty}}}}})')
        last = step
    if date_col:
        steps.append(f'AddMonth = Table.AddColumn({last}, "Month", each Date.StartOfMonth(DateTime.Date(Record.Field(_, {m_str(date_col["name"])}))), type date)')
        steps.append('AddYear = Table.AddColumn(AddMonth, "Year", each Date.Year(Record.Field(_, "Month")), Int64.Type)')
        last = "AddYear"
    body = ",\n\t\t\t\t".join(steps)
    return f"let\n\t\t\t\t{body}\n\t\t\tin\n\t\t\t\t{last}"


def tmdl_table(table, cols, date_col, measures, m):
    out = [f"table {tq(table)}", f"\tlineageTag: {gid(table)}", ""]
    for name, dax, fmt, _ in measures:
        out += [f"\tmeasure {tq(name)} = {dax}", f"\t\tformatString: {fmt}", f"\t\tlineageTag: {gid(table, 'm', name)}", ""]
    tmdl_type = {"int": ("int64", "0", "none"), "float": ("double", "#,0.00", "none"), "date": ("dateTime", "Short Date", "none"),
                 "datetime": ("dateTime", "General Date", "none"), "bool": ("boolean", None, "none"), "text": ("string", None, "none")}
    all_cols = [(c["name"], c["kind"]) for c in cols]
    if date_col:
        all_cols += [("Month", "date"), ("Year", "int")]
    for name, kind in all_cols:
        dt, fmt, summ = tmdl_type[kind]
        if date_col and name == "Month":
            fmt = "mmm yyyy"
        out += [f"\tcolumn {tq(name)}", f"\t\tdataType: {dt}"]
        if fmt:
            out.append(f"\t\tformatString: {fmt}")
        out += [f"\t\tlineageTag: {gid(table, 'c', name)}", f"\t\tsummarizeBy: {summ}", f"\t\tsourceColumn: {tv(name)}", ""]
    out += [f"\tpartition {tq(table)} = m", "\t\tmode: import", "\t\tsource =", "\t\t\t" + m, ""]
    return "\n".join(out)


# ------------------------------------------------------------------ report
def fld_col(table, col):
    return {"Column": {"Expression": {"SourceRef": {"Entity": table}}, "Property": col}}


def fld_meas(table, name):
    return {"Measure": {"Expression": {"SourceRef": {"Entity": table}}, "Property": name}}


def proj(field, table, name, fmt=None, active=False):
    p = {"field": field, "queryRef": f"{table}.{name}", "nativeQueryRef": name}
    if fmt:
        p["format"] = fmt
    if active:
        p["active"] = True
    return p


def container(page, key, x, y, w, h, z, visual):
    return {"$schema": f"{SCHEMA}/visualContainer/{V_VISUAL}/schema.json", "name": oid(page, key),
            "position": {"x": x, "y": y, "z": z, "height": h, "width": w, "tabOrder": z}, "visual": visual}


def title_obj(text):
    return {"title": [{"properties": {"show": {"expr": {"Literal": {"Value": "true"}}}, "text": literal(text)}}],
            "subTitle": [{"properties": {"show": {"expr": {"Literal": {"Value": "false"}}}}}]}


def v_textbox(page, key, x, y, w, h, z, runs):
    paragraphs = [{"textRuns": [{"value": text, **({"textStyle": style} if style else {})}]} for text, style in runs]
    v = {"visualType": "textbox", "objects": {"general": [{"properties": {"paragraphs": paragraphs}}]}, "drillFilterOtherVisuals": True}
    return container(page, key, x, y, w, h, z, v)


def v_card(page, table, measure, fmt, x, y, w, h, z):
    v = {"visualType": "cardVisual",
         "query": {"queryState": {"Data": {"projections": [proj(fld_meas(table, measure), table, measure, fmt)]}},
                   "sortDefinition": {"sort": [{"field": fld_meas(table, measure), "direction": "Descending"}], "isDefaultSort": True}},
         "drillFilterOtherVisuals": True}
    return container(page, "card-" + measure, x, y, w, h, z, v)


def v_slicer(page, table, col, label, x, y, w, h, z, dropdown=True):
    objects = {"header": [{"properties": {"show": {"expr": {"Literal": {"Value": "false"}}}}}]}
    if dropdown:
        objects["data"] = [{"properties": {"mode": literal("Dropdown")}}]
    v = {"visualType": "slicer",
         "query": {"queryState": {"Values": {"projections": [proj(fld_col(table, col), table, col, active=True)]}}},
         "objects": objects, "visualContainerObjects": {"title": [{"properties": {"show": {"expr": {"Literal": {"Value": "true"}}}, "text": literal(label)}}]},
         "drillFilterOtherVisuals": True}
    return container(page, "slicer-" + col, x, y, w, h, z, v)


def v_chart(page, kind, table, cat_field, cat_name, measure, fmt, title, x, y, w, h, z, sort_by_measure=True):
    q = {"queryState": {"Category": {"projections": [proj(cat_field, table, cat_name, active=True)]},
                        "Y": {"projections": [proj(fld_meas(table, measure), table, measure, fmt)]}}}
    if sort_by_measure:
        q["sortDefinition"] = {"sort": [{"field": fld_meas(table, measure), "direction": "Descending"}], "isDefaultSort": True}
    else:
        q["sortDefinition"] = {"sort": [{"field": cat_field, "direction": "Ascending"}], "isDefaultSort": True}
    v = {"visualType": kind, "query": q, "visualContainerObjects": title_obj(title), "drillFilterOtherVisuals": True}
    if kind in ("clusteredBarChart", "lineChart"):
        v["objects"] = {"labels": [{"properties": {"show": {"expr": {"Literal": {"Value": "true"}}}}}]} if kind == "clusteredBarChart" else {}
        if not v["objects"]:
            del v["objects"]
    return container(page, f"{kind}-{cat_name}-{measure}", x, y, w, h, z, v)


def v_table(page, table, columns, extra_measures, title, x, y, w, h, z):
    projections = [proj(fld_col(table, c), table, c) for c in columns] + [proj(fld_meas(table, m), table, m) for m in extra_measures]
    v = {"visualType": "tableEx", "query": {"queryState": {"Values": {"projections": projections}}},
         "visualContainerObjects": title_obj(title), "drillFilterOtherVisuals": True}
    return container(page, "table-" + title, x, y, w, h, z, v)


def build_report(table, title, date, dims, metrics, measures, cols, plan_notes):
    """Return {relative path: json} for the PBIR definition folder."""
    mnames = {m[0]: m for m in measures}
    rows_name = measures[0][0]
    heads = [headline(m, measures) for m in metrics]
    fmts = {m[0]: m[2] for m in measures}

    pages = {}
    P1, P2 = oid("page", "overview"), oid("page", "details")
    z = [0]

    def nz():
        z[0] += 1000
        return z[0]

    v1 = [v_textbox(P1, "title", 24, 12, 1232, 44, nz(), [(title, {"fontSize": "22pt", "fontWeight": "bold"})])]
    # slicers: the date range, then up to two dimensions
    sl = []
    if date:
        sl.append((date["name"], date["name"], False))
    for d in dims[:2 if date else 3]:
        sl.append((d["name"], d["name"], True))
    sw = 300
    for i, (col, label, dd) in enumerate(sl):
        v1.append(v_slicer(P1, table, col, label, 24 + i * (sw + 16), 62, sw, 78, nz(), dropdown=dd))
    # KPI cards: row count then the headline measures
    cards = [rows_name] + heads[:3]
    cw = (1232 - 16 * (len(cards) - 1)) / max(len(cards), 1)
    for i, name in enumerate(cards):
        v1.append(v_card(P1, table, name, fmts[name], round(24 + i * (cw + 16), 1), 150, round(cw, 1), 84, nz()))
    # chart grid
    slots = [(24, 246, 760, 224), (800, 246, 456, 224), (24, 480, 620, 228), (660, 480, 596, 228)]
    m1 = heads[0] if heads else rows_name
    m2 = heads[1] if len(heads) > 1 else rows_name
    fmt1, fmt2 = fmts[m1], fmts[m2]
    charts = []
    if date:
        charts.append(("lineChart", fld_col(table, "Month"), "Month", m1, fmt1, f"{m1} by month", False))
    if dims:
        charts.append(("clusteredBarChart", fld_col(table, dims[0]["name"]), dims[0]["name"], m1, fmt1, f"{m1} by {dims[0]['name']}", True))
    if len(dims) > 1:
        charts.append(("clusteredBarChart", fld_col(table, dims[1]["name"]), dims[1]["name"], m2, fmt2, f"{m2} by {dims[1]['name']}", True))
    elif len(dims) == 1 and len(heads) > 1:
        charts.append(("clusteredColumnChart", fld_col(table, dims[0]["name"]), dims[0]["name"], m2, fmt2, f"{m2} by {dims[0]['name']}", True))
    small = [d for d in dims if d["distinct"] <= 6]
    if small:
        d = small[0]
        charts.append(("donutChart", fld_col(table, d["name"]), d["name"], rows_name, "#,0", f"Share of rows by {d['name']}", True))
    elif len(dims) > 2:
        charts.append(("clusteredBarChart", fld_col(table, dims[2]["name"]), dims[2]["name"], m1, fmt1, f"{m1} by {dims[2]['name']}", True))
    for slot, (kind, field, cname, meas, fmt, ttl, by_measure) in zip(slots, charts):
        v1.append(v_chart(P1, kind, table, field, cname, meas, fmt, ttl, *slot, nz(), sort_by_measure=by_measure))
    pages[P1] = ("Overview", v1)

    # page 2: a details table and the same filters
    v2 = [v_textbox(P2, "title", 24, 12, 1232, 44, nz(), [(f"{title}: details", {"fontSize": "22pt", "fontWeight": "bold"})])]
    for i, (col, label, dd) in enumerate(sl):
        v2.append(v_slicer(P2, table, col, label, 24 + i * (sw + 16), 62, sw, 78, nz(), dropdown=dd))
    shown = [c["name"] for c in cols if c["kind"] != "empty"][:14]
    v2.append(v_table(P2, table, shown, [rows_name], "Rows", 24, 150, 1232, 552, nz()))
    pages[P2] = ("Details", v2)

    files = {
        "version.json": {"$schema": f"{SCHEMA}/versionMetadata/{V_VERSION}/schema.json", "version": "2.0.0"},
        "pages/pages.json": {"$schema": f"{SCHEMA}/pagesMetadata/{V_PAGES}/schema.json", "pageOrder": [P1, P2], "activePageName": P1},
    }
    for pid, (pname, visuals) in pages.items():
        files[f"pages/{pid}/page.json"] = {"$schema": f"{SCHEMA}/page/{V_PAGE}/schema.json", "name": pid, "displayName": pname,
                                           "displayOption": "FitToPage", "height": PAGE_H, "width": PAGE_W}
        for v in visuals:
            files[f"pages/{pid}/visuals/{v['name']}/visual.json"] = v
    return files


def find_base_theme():
    """Find the newest base theme file that Power BI Desktop ships with (it is copied into the report)."""
    roots = [Path("C:/Program Files/Microsoft Power BI Desktop")]
    try:        # the Microsoft Store version lives in a folder that cannot be listed, so ask Windows for its path
        out = subprocess.run(["powershell", "-NoProfile", "-Command", "(Get-AppxPackage Microsoft.MicrosoftPowerBIDesktop).InstallLocation"],
                             capture_output=True, text=True, timeout=60).stdout.strip()
        if out:
            roots.insert(0, Path(out))
    except (OSError, subprocess.SubprocessError):
        pass
    best = None
    for root in roots:
        try:
            for f in root.glob("**/sharedresources/BaseThemes/Fluent2-CY*.json"):
                if best is None or f.name > best.name:
                    best = f
        except (PermissionError, OSError):
            continue
    return best


def report_json(theme_name):
    ver = IMPORT_VERSIONS
    return {"$schema": f"{SCHEMA}/report/{V_REPORT}/schema.json",
            "themeCollection": {"baseTheme": {"name": theme_name, "reportVersionAtImport": ver, "type": "SharedResources"}},
            "objects": {"section": [{"properties": {"verticalAlignment": literal("Top")}}],
                        "outspacePane": [{"properties": {"expanded": {"expr": {"Literal": {"Value": "false"}}}}}]},
            "resourcePackages": [{"name": "SharedResources", "type": "SharedResources",
                                  "items": [{"name": theme_name, "path": f"BaseThemes/{theme_name}.json", "type": "BaseTheme"}]}],
            "settings": {"useStylableVisualContainerHeader": True, "exportDataMode": "AllowSummarized", "defaultDrillFilterOtherVisuals": True,
                         "allowChangeFilterTypes": True, "useEnhancedTooltips": True, "useDefaultAggregateDisplayName": True}}


# ------------------------------------------------------------------ main
def write_json(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")


def write_text(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("data")
    ap.add_argument("--name", help="project name (default: file name)")
    ap.add_argument("--out", default="out")
    ap.add_argument("--title", help="report title")
    ap.add_argument("--sheet", help="Excel sheet name")
    ap.add_argument("--date", help="column to use as the date")
    ap.add_argument("--metrics", help="comma-separated numeric columns to build measures for")
    ap.add_argument("--dims", help="comma-separated categorical columns to use as slicers and chart axes")
    ap.add_argument("--culture", default="en-US")
    ap.add_argument("--open", action="store_true", help="open the project in Power BI Desktop when done (Windows)")
    args = ap.parse_args()

    data_path = Path(args.data).resolve()
    df, src = read_table(data_path, args.sheet)
    if src["kind"] == "excel":
        src["sheet_name"] = args.sheet or pd.ExcelFile(data_path).sheet_names[0]
    cols = classify(df, args.culture)
    cols = [c for c in cols if c["kind"] != "empty"]
    date, dims, metrics = pick_roles(cols, len(df), args)
    table = table_name(data_path, args.name)
    name = table
    title = args.title or data_path.stem.replace("_", " ").title()
    measures = build_measures(table, "Rows", metrics)

    out = Path(args.out) / name
    if out.exists():
        shutil.rmtree(out)
    sm, rp = out / f"{name}.SemanticModel", out / f"{name}.Report"

    write_json(out / f"{name}.pbip", {"$schema": "https://developer.microsoft.com/json-schemas/fabric/pbip/pbipProperties/1.0.0/schema.json",
                                      "version": "1.0", "artifacts": [{"report": {"path": f"{name}.Report"}}], "settings": {"enableAutoRecovery": True}})
    write_text(out / ".gitignore", "**/.pbi/localSettings.json\n**/.pbi/cache.abf\n")

    write_json(sm / "definition.pbism", {"$schema": "https://developer.microsoft.com/json-schemas/fabric/item/semanticModel/definitionProperties/1.0.0/schema.json",
                                         "version": "4.0", "settings": {}})
    write_text(sm / "definition" / "database.tmdl", "database\n\tcompatibilityLevel: 1600\n")
    write_text(sm / "definition" / "model.tmdl",
               "model Model\n\tculture: en-US\n\tdefaultPowerBIDataSourceVersion: powerBI_V3\n\tdiscourageImplicitMeasures\n\tsourceQueryCulture: en-US\n\n"
               f"ref table {tq(table)}\n")
    m = m_query(src, table, cols, date, data_path)
    write_text(sm / "definition" / "tables" / f"{table}.tmdl", tmdl_table(table, cols, date, measures, m))

    write_json(rp / "definition.pbir", {"$schema": "https://developer.microsoft.com/json-schemas/fabric/item/report/definitionProperties/2.0.0/schema.json",
                                        "version": "4.0", "datasetReference": {"byPath": {"path": f"../{name}.SemanticModel"}}})
    theme_file = find_base_theme()
    theme_name = theme_file.stem if theme_file else FALLBACK_THEME
    write_json(rp / "definition" / "report.json", report_json(theme_name))
    if theme_file:
        dest = rp / "StaticResources" / "SharedResources" / "BaseThemes" / theme_file.name
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(theme_file, dest)
    for rel, obj in build_report(table, title, date, dims, metrics, measures, cols, None).items():
        write_json(rp / "definition" / rel, obj)

    print(f"read {len(df):,} rows x {len(cols)} columns from {data_path.name}")
    print(f"  date column : {date['name'] if date else '-'}")
    print(f"  dimensions  : {', '.join(d['name'] for d in dims[:6]) or '-'}")
    print(f"  metrics     : {', '.join(c['name'] for c in metrics) or '-'}")
    print(f"  measures    : {', '.join(m[0] for m in measures)}")
    print(f"wrote {out / (name + '.pbip')}")
    print("next: in Power BI Desktop click Refresh (banner at the top), then File > Save as > .pbix")
    if args.open and hasattr(os, "startfile"):
        os.startfile(out / f"{name}.pbip")


if __name__ == "__main__":
    main()

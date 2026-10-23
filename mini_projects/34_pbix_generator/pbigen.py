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
import base64
import csv
import gzip
import io
import json
import os
import re
import shutil
import subprocess
import sys
import uuid
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pandas as pd

NS = uuid.UUID("6f0c3a52-0c1e-4c0b-9a55-7d1a2b3c4d5e")
SCHEMA = "https://developer.microsoft.com/json-schemas/fabric/item/report/definition"
V_REPORT, V_PAGE, V_PAGES, V_VISUAL, V_VERSION = "3.3.0", "2.1.0", "1.1.0", "2.13.0", "1.0.0"
IMPORT_VERSIONS = {"visual": "2.13.0", "report": "3.4.0", "page": "2.3.1"}
FALLBACK_THEME = "Fluent2-CY26SU09"
PAGE_W, PAGE_H = 1920, 1080          # the layout below is designed on a 1280 x 720 grid and scaled up to this page size
K = PAGE_W / 1280

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
                "kind": "text", "decimal_comma": False, "date_format": None,
                "avg_len": float(s.astype(str).str.len().mean()) if n else 0.0}
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
    if args.only_custom:
        metrics = []
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
def parse_custom_measures(items, table):
    """--measure "Name=DAX|format": {T} in the DAX stands for the table name."""
    out = []
    for item in items or []:
        name, _, rest = item.partition("=")
        dax, _, fmt = rest.rpartition("|") if "|" in rest else (rest, "", "#,0")
        out.append((name.strip(), dax.strip().replace("{T}", table), fmt.strip() or "#,0", "custom"))
    return out


def build_measures(table, rows_name, metrics, custom=()):
    """(name, dax, formatString, kind) for each measure; the first one for each metric is the headline."""
    measures = [(rows_name, f"COUNTROWS({table})", "#,0", "rows")] + list(custom)
    taken = {m[0] for m in measures}
    for c in metrics:
        col = dax_col(table, c["name"])
        fmt = "#,0" if c["kind"] == "int" else "#,0.00"
        total, avg = (f"Total {c['name']}", f"Average {c['name']}")
        if total not in taken:
            measures.append((total, f"SUM({col})", fmt, "total"))
        if avg not in taken:
            measures.append((avg, f"AVERAGE({col})", "#,0.00", "average"))
    return measures


def headline(metric, measures):
    """The measure that best summarises a metric: an average for rates and prices, a total otherwise."""
    wanted = f"Average {metric['name']}" if AVERAGE_HINT.search(metric["name"]) else f"Total {metric['name']}"
    return wanted


def m_query(src, table, cols, date_col, path):
    steps, last = [], "Source"
    if src.get("embedded"):
        chunks = [src["embedded"][i:i + 3000] for i in range(0, len(src["embedded"]), 3000)]
        literal_text = ' &\n\t\t\t\t\t'.join(m_str(c) for c in chunks)
        steps.append(f'Source = Csv.Document(Binary.Decompress(Binary.FromText({literal_text}, BinaryEncoding.Base64), Compression.GZip), '
                     f'[Delimiter=",", Columns={len(cols)}, Encoding=65001, QuoteStyle=QuoteStyle.Csv])')
    elif src["kind"] == "excel":
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


def tmdl_table(table, cols, date_col, measures, m, geo=None):
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
        if geo and name in geo:
            out.append(f"\t\tdataCategory: {geo[name]}")
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


# ---------------------------------------------------------------------------------------------------------------------
# Report design: card layout, a three-colour palette, side navigation, the collapsible Filters pane, tooltip page, KPI sparklines
# ---------------------------------------------------------------------------------------------------------------------
#   NAVY = navigation / header band, TEAL = brand colour (active tab, first series), ACCENT = the one colour that points things out,
#   INK / MUTED = text, BG = page, CARD = card fill, EDGE = card border
STYLES = {
    "clean": dict(nav="left", nav_w=170, top=64, bottom=708, NAVY="#1B2A49", TEAL="#0E7C86", ACCENT="#F2994A", INK="#2D3748", MUTED="#718096", BG="#F3F4F6", CARD="#FFFFFF",
                  EDGE="#E2E8F0", nav_text="#CBD5E0", nav_active_text="#FFFFFF", radius=8, border=1, dark=False, title_card=False, top_slicers=False,
                  callout="INK", heat=("#E6F2F3", "#7CC0C6", "#0E7C86"), series=["#0E7C86", "#A0AEC0", "#F2994A", "#1B2A49", "#5BB5BD", "#CBD5E0", "#F6C28B", "#4A5568"]),
    # dark navy, slicers in a strip at the top, tabs under the header (after a health-insurance dashboard)
    "midnight": dict(nav="top", header="strip", top=108, NAVY="#081226", TEAL="#4F86B8", ACCENT="#A9C7EC", INK="#E8F0FB", MUTED="#8FA6C8", BG="#0C1B38", CARD="#142B52",
                     EDGE="#27487F", nav_text="#9DB4D6", nav_active_text="#0C1B38", radius=4, border=1, dark=True, title_card=False, top_slicers=True,
                     callout="INK", heat=("#142B52", "#3F6EA3", "#9DB8E0"), series=["#4F86B8", "#9DB8E0", "#9FD3C7", "#C3A6D8", "#E8C77B", "#6F9FD0", "#7FC4B4", "#D9A5B8"]),
    # dark brown with orange, title and tabs in one bar, thick orange card borders (after a YouTube analytics dashboard)
    "sunset": dict(nav="top", header="bar", top=66, NAVY="#1A0F0A", TEAL="#F26B1D", ACCENT="#FFC27A", INK="#FDEBDD", MUTED="#D9A98A", BG="#2B1A12", CARD="#3A2418",
                   EDGE="#F26B1D", nav_text="#E8C3A5", nav_active_text="#1A0F0A", radius=0, border=2, dark=True, title_card=False, top_slicers=False,
                   callout="TEAL", heat=("#3A2418", "#C2410C", "#FFB347"), series=["#F26B1D", "#FFB347", "#C2410C", "#E9A66B", "#9A5530", "#FFD8A8", "#7A3E1D", "#F7926B"]),
    # purple, left rail, round cards without borders, yellow accent (after an e-commerce sales dashboard)
    "violet": dict(nav="left", nav_w=150, top=64, bottom=708, NAVY="#1E0E3C", TEAL="#8E5CFF", ACCENT="#F5B700", INK="#F6F0FF", MUTED="#CDBBF0", BG="#2E1659", CARD="#432380",
                   EDGE="#6D3FC0", nav_text="#CDBBF0", nav_active_text="#1E0E3C", radius=16, border=0, dark=True, title_card=True, top_slicers=False,
                   callout="ACCENT", heat=("#432380", "#8E5CFF", "#F5B700"), series=["#F5B700", "#35C9B1", "#FF6B9A", "#8EC5FF", "#B388FF", "#FFA45B", "#7BE0AD", "#E0D4FF"]),
    # near-black with cyan and magenta, thin header and the page tabs along the bottom edge
    "neon": dict(nav="bottom", top=54, bottom=668, NAVY="#04050B", TEAL="#00D4F0", ACCENT="#FF2BD6", INK="#E9ECFF", MUTED="#7F89BD", BG="#080A14",
                 CARD="#0E1224", EDGE="#27306A", nav_text="#9AA4D8", nav_active_text="#04050B", radius=6, border=1, dark=True, title_card=False,
                 top_slicers=False, callout="TEAL", heat=("#0E1224", "#6C4DFF", "#19C7E0"),
                 series=["#00D4F0", "#FF2BD6", "#B6FF3B", "#FFC53D", "#7C5CFF", "#FF6B6B", "#35F0A8", "#4DA3FF"]),
    # cream paper, serif headline, tabs as text with an underline, sage green and terracotta
    "paper": dict(nav="top", header="editorial", top=112, font="Georgia", NAVY="#1F2A24", TEAL="#2F6B4F", ACCENT="#C8553D", INK="#1F2A24", MUTED="#6B7468",
                  BG="#EFEBE0", CARD="#FBF9F4", EDGE="#D8D1BF", nav_text="#4B554D", nav_active_text="#2F6B4F", radius=2, border=1, dark=False,
                  title_card=False, top_slicers=False, callout="INK", heat=("#FBF9F4", "#A8C5B4", "#2F6B4F"),
                  series=["#2F6B4F", "#C8553D", "#D9A441", "#5B7B8C", "#8FB39B", "#E3A18F", "#A8B5A2", "#7A6A53"]),
    # light grey with a floating header card, round pills and safety yellow (a factory floor board)
    "factory": dict(nav="top", header="card", top=82, NAVY="#263238", TEAL="#FFC400", ACCENT="#E8590C", INK="#263238", MUTED="#78909C", BG="#E9EDF0", CARD="#FFFFFF",
                    EDGE="#D5DCE1", nav_text="#455A64", nav_active_text="#263238", radius=12, border=1, dark=False, title_card=False, top_slicers=False,
                    callout="INK", heat=("#FFFFFF", "#FFE082", "#F57C00"),
                    series=["#37474F", "#FFC400", "#E8590C", "#78909C", "#90A4AE", "#FFD54F", "#8D6E63", "#26A69A"]),
    # dark green board with chalk yellow and pink, numbered chips for the pages (a classroom story)
    "chalk": dict(nav="top", header="chips", top=72, NAVY="#102521", TEAL="#F2C14E", ACCENT="#F28DA6", INK="#F4F1E8", MUTED="#A9C2B8", BG="#16302B", CARD="#1F403A",
                  EDGE="#2F5A52", nav_text="#C7D8D0", nav_active_text="#102521", radius=10, border=0, dark=True, title_card=False, top_slicers=False,
                  callout="TEAL", heat=("#1F403A", "#7FD1B9", "#F2C14E"),
                  series=["#F2C14E", "#F28DA6", "#7FD1B9", "#8EC5FF", "#E6E1D3", "#F29E6D", "#B8A9FF", "#9BD16B"]),
    # charcoal with an air-quality scale from green to maroon; filters live in a panel on the left
    "ash": dict(nav="split", nav_w=200, top=54, NAVY="#14171C", TEAL="#5CC6B0", ACCENT="#F29B38", INK="#E8ECF1", MUTED="#8C97A7", BG="#1D2128", CARD="#2A2F38",
                EDGE="#3C434F", nav_text="#B5BEC9", nav_active_text="#14171C", radius=6, border=1, dark=True, title_card=False, top_slicers=False,
                callout="ACCENT", heat=("#2A2F38", "#F29B38", "#E5533D"),
                series=["#5CC689", "#F2D14B", "#F29B38", "#E5533D", "#A04FC7", "#7A1F3D", "#6AA9E9", "#9AA5B1"]),
    # slate blue with lightning yellow, a slim numbered rail on the left (a weather map)
    "storm": dict(nav="rail", nav_w=108, top=74, NAVY="#0B1320", TEAL="#FFD60A", ACCENT="#38BDF8", INK="#E6EEF8", MUTED="#8DA2BC", BG="#111B27", CARD="#18283A",
                  EDGE="#2A4058", nav_text="#9FB3CC", nav_active_text="#0B1320", radius=8, border=1, dark=True, title_card=False, top_slicers=False,
                  callout="TEAL", heat=("#18283A", "#2F7DB5", "#FFD60A"),
                  series=["#FFD60A", "#38BDF8", "#F97316", "#A78BFA", "#34D399", "#F43F5E", "#E2E8F0", "#64748B"]),
    # black screen with amber monospace text and function-key tabs (a market terminal)
    "terminal": dict(nav="top", header="terminal", top=40, font="Consolas", NAVY="#000000", TEAL="#FFB000", ACCENT="#00D26A", INK="#FFB000", MUTED="#B07A00", BG="#000000",
                     CARD="#0B0B0B", EDGE="#4A3A00", nav_text="#FFB000", nav_active_text="#000000", radius=0, border=1, dark=True, title_card=False, top_slicers=False,
                     callout="INK", heat=("#0B0B0B", "#7A5200", "#FFB000"),
                     series=["#FFB000", "#00D26A", "#FF4D4D", "#4DA3FF", "#E0E0E0", "#B07A00", "#C792EA", "#00BFA5"]),
    # cream paper, racing red and a floating dock for the pages (a car showroom)
    "garage": dict(nav="dock", top=62, bottom=660, NAVY="#1E1E24", TEAL="#D62828", ACCENT="#F4A100", INK="#1E1E24", MUTED="#7A7468", BG="#F4EFE6", CARD="#FFFFFF",
                   EDGE="#E2D8C6", nav_text="#E8E4DA", nav_active_text="#FFFFFF", radius=18, border=1, dark=False, title_card=False, top_slicers=False,
                   callout="TEAL", heat=("#FFFFFF", "#F6C28B", "#D62828"),
                   series=["#D62828", "#1E1E24", "#F4A100", "#3A7CA5", "#8D99AE", "#2A9D8F", "#E76F51", "#6D597A"]),
    # airy white and ice blue, a centred light title with underlined tabs (a climate record)
    "frost": dict(nav="top", header="center", top=98, NAVY="#12303F", TEAL="#2A9DBF", ACCENT="#E4572E", INK="#17394A", MUTED="#6A8394", BG="#F3F7FA", CARD="#FFFFFF",
                  EDGE="#DCE7EE", nav_text="#6A8394", nav_active_text="#17394A", radius=4, border=1, dark=False, title_card=False, top_slicers=False,
                  callout="INK", heat=("#2B6CB0", "#F2F2F2", "#C0392B"),
                  series=["#2A9DBF", "#E4572E", "#F2B134", "#1B5E7B", "#8ECAE6", "#F28F6B", "#6C8EA4", "#A7C957"]),
    # pink and mint pastels on a floating lavender side card with very round cards (a friendly demography)
    "pastel": dict(nav="float", nav_w=150, top=66, NAVY="#CDB9EF", TEAL="#FF7FA5", ACCENT="#6BCB9D", INK="#3D2C4F", MUTED="#8F7BA3", BG="#FDF4F8", CARD="#FFFFFF",
                   EDGE="#F3DCE8", nav_text="#4B3A63", nav_active_text="#3D2C4F", radius=22, border=1, dark=False, title_card=False, top_slicers=False,
                   callout="TEAL", heat=("#FFF1F5", "#FFB3C7", "#FF5C8A"), heat_text="ink",
                   series=["#7DB9F5", "#FF7FA5", "#FFD166", "#6BCB9D", "#B8A1E3", "#F4A261", "#90E0EF", "#CDB4DB"]),
    # a newspaper front page: serif masthead between double rules, black and red on newsprint, square flat cards
    "newsprint": dict(nav="top", header="masthead", top=122, font="Georgia", NAVY="#111111", TEAL="#B3121D", ACCENT="#111111", INK="#111111", MUTED="#5A5A5A", BG="#F1EEE4",
                      CARD="#F8F5EC", EDGE="#111111", nav_text="#111111", nav_active_text="#B3121D", radius=0, border=1, dark=False, title_card=False, top_slicers=False,
                      callout="TEAL", heat=("#F8F5EC", "#E3A9A9", "#B3121D"),
                      series=["#111111", "#B3121D", "#6B6B6B", "#C69C3C", "#3D5A80", "#9A8F7A", "#333333", "#8D99AE"]),
    # near-black with ivory text and gold, a serif title with the pages on the right under a double gold rule (an honours roll)
    "laurel": dict(nav="top", header="plaque", top=74, font="Georgia", NAVY="#08090C", TEAL="#C9A24B", ACCENT="#8FB8DE", INK="#F3EEDF", MUTED="#A59F8C", BG="#101218", CARD="#181B24",
                   EDGE="#2C2F3C", nav_text="#B9B29B", nav_active_text="#C9A24B", radius=2, border=1, dark=True, title_card=False, top_slicers=False,
                   callout="TEAL", heat=("#181B24", "#4F4326", "#9A7B2E"), heat_text="ink",
                   series=["#C9A24B", "#7FA6C9", "#C97B63", "#8FBF9F", "#B79CD6", "#E3D5A1", "#5E7F9C", "#D9A5A5"]),
    # deep indigo with a glowing orange sun, the pages as dots on an orbit line (a star's activity)
    "solar": dict(nav="top", header="orbit", top=104, NAVY="#06061A", TEAL="#FF9F1C", ACCENT="#FFE066", INK="#FFF3E0", MUTED="#B7ABD6", BG="#0B0A22", CARD="#15143A",
                  EDGE="#2A2860", nav_text="#C5B8E8", nav_active_text="#FFD54A", radius=14, border=1, dark=True, title_card=False, top_slicers=False,
                  callout="ACCENT", heat=("#1A1A4A", "#8A3A12", "#E0701A"), heat_text="ink", orbit="#4A47A3",
                  series=["#FF9F1C", "#8E7DFF", "#FFE066", "#EF476F", "#4CC9F0", "#F7B267", "#B5179E", "#80ED99"]),
    # light grey content beside a slim basalt rail with numbered pages, lava red (a seismic catalogue)
    "basalt": dict(nav="mini", nav_w=64, top=64, NAVY="#1D2023", TEAL="#E4572E", ACCENT="#2F6F73", INK="#23272B", MUTED="#7B848C", BG="#E9EBEC", CARD="#FFFFFF",
                   EDGE="#D3D8DB", nav_text="#C7CDD2", nav_active_text="#FFFFFF", radius=3, border=1, dark=False, title_card=False, top_slicers=False,
                   callout="TEAL", heat=("#FFF1EB", "#F5A27F", "#C2260F"),
                   series=["#E4572E", "#2F6F73", "#F2A541", "#4F5D75", "#9AA5B1", "#B5473A", "#6BA292", "#232A30"]),
    # warm white with a burgundy rail on the right-hand side
    "bordeaux": dict(nav="right", nav_w=180, NAVY="#4A1424", TEAL="#8C2740", ACCENT="#C9A227", INK="#2B1A1F", MUTED="#7A6168", BG="#F6F1EE", CARD="#FFFFFF",
                     EDGE="#E6D9D4", nav_text="#E8CDD3", nav_active_text="#4A1424", radius=10, border=1, dark=False, title_card=False, top_slicers=False,
                     callout="TEAL", heat=("#FBF3F0", "#D89AA8", "#8C2740"),
                     series=["#8C2740", "#C9A227", "#5E7F6B", "#D98E73", "#4A1424", "#9DB4A4", "#E3C770", "#B86B7E"]),
}
STYLE = dict(STYLES["clean"])
NAVY = TEAL = ACCENT = INK = MUTED = BG = CARD = EDGE = ""
STYLE_NAME = "clean"
CUSTOM_THEME = "pbigen_clean.json"


def apply_style(name):
    """Make `name` the active style: the colours are module globals that the visual builders read."""
    global NAVY, TEAL, ACCENT, INK, MUTED, BG, CARD, EDGE, STYLE, STYLE_NAME, CUSTOM_THEME, NAV_W, CX, CW
    STYLE_NAME, STYLE = name, dict(STYLES[name])
    NAVY, TEAL, ACCENT, INK, MUTED, BG, CARD, EDGE = (STYLE[k] for k in ("NAVY", "TEAL", "ACCENT", "INK", "MUTED", "BG", "CARD", "EDGE"))
    CUSTOM_THEME = f"pbigen_{name}.json"
    NAV_W = STYLE.get("nav_w", 0)
    STYLE.setdefault("top", 64), STYLE.setdefault("bottom", 708), STYLE.setdefault("font", "Segoe UI")
    CX = NAV_W + 28 if STYLE["nav"] == "float" else NAV_W + 16 if STYLE["nav"] in ("left", "split", "rail", "mini") else 16
    CW = {"left": 1280 - CX - 24, "right": 1280 - NAV_W - 32, "split": 1280 - CX - 16, "rail": 1280 - CX - 16, "float": 1280 - CX - 16, "mini": 1280 - CX - 16}.get(STYLE["nav"], 1280 - 32)


def lit(v):
    return {"expr": {"Literal": {"Value": v}}}


def tx(text):
    return lit("'" + text.replace("'", "''") + "'")


def flag(x):
    return lit("true" if x else "false")


def num(x, suffix="D"):
    return lit(f"{x}{suffix}")


def solid(hex_color):
    return {"solid": {"color": lit("'" + hex_color + "'")}}


def container(page, key, x, y, w, h, z, visual, hidden=False, scale=None):
    k = K if scale is None else scale
    c = {"$schema": f"{SCHEMA}/visualContainer/{V_VISUAL}/schema.json", "name": oid(page, key),
         "position": {"x": round(x * k, 1), "y": round(y * k, 1), "z": z, "height": round(h * k, 1), "width": round(w * k, 1), "tabOrder": z},
         "visual": visual}
    if hidden:
        c["isHidden"] = True
    return c


def card_objects(title=None):
    """A white card with a thin border and rounded corners, and a small bold title (or none)."""
    o = {"background": [{"properties": {"show": flag(True), "color": solid(CARD), "transparency": num(0)}}],
         "border": [{"properties": {"show": flag(STYLE["border"] > 0), "color": solid(EDGE), "radius": num(STYLE["radius"]), "width": num(max(STYLE["border"], 1))}}],
         "subTitle": [{"properties": {"show": flag(False)}}]}
    o["title"] = [{"properties": {"show": flag(True), "text": tx(title), "fontColor": solid(INK), "fontSize": num(12), "bold": flag(True)}}] if title \
        else [{"properties": {"show": flag(False)}}]
    return o


def chart_objects(kind):
    """Quiet charts: no gridlines, no value axis when the bars carry labels, no legend unless the chart needs one; text in the style's colours."""
    off, on = flag(False), flag(True)
    txt = {"labelColor": solid(MUTED)}
    cat = {"showAxisTitle": off, "gridlineShow": off, "axisType": tx("Categorical"), **txt}          # numeric categories (hours, ids) stay categories
    lab = {"show": on, "color": solid(INK)}
    if kind in ("clusteredBarChart", "clusteredColumnChart"):
        return {"valueAxis": [{"properties": {"show": off, "gridlineShow": off, **txt}}], "categoryAxis": [{"properties": cat}],
                "labels": [{"properties": lab}], "legend": [{"properties": {"show": off}}]}
    if kind in ("lineChart", "areaChart"):
        return {"valueAxis": [{"properties": {"gridlineShow": off, "showAxisTitle": off, **txt}}], "categoryAxis": [{"properties": cat}],
                "legend": [{"properties": {"show": off}}]}
    if kind == "scatterChart":
        return {"legend": [{"properties": {"show": off}}], "valueAxis": [{"properties": {"gridlineShow": off, **txt}}],
                "categoryAxis": [{"properties": {"gridlineShow": off, **txt}}], "categoryLabels": [{"properties": {"show": on, "color": solid(INK)}}]}
    if kind == "treemap":
        return {"legend": [{"properties": {"show": off}}], "labels": [{"properties": lab}]}
    if kind == "donutChart":
        return {"legend": [{"properties": {"show": on, "labelColor": solid(MUTED)}}], "labels": [{"properties": lab}]}
    return {}


def grid_objects(size=None):
    """Colours for tables and matrices (on a dark card the default white cells would be wrong); `size` is an optional font size in points."""
    head = {"fontColor": solid(INK), "backColor": solid(NAVY if STYLE["dark"] else "#EEF2F7"), **({"fontSize": num(size)} if size else {})}
    body = {"fontColor": solid(INK), "backColor": solid(CARD), "fontColorPrimary": solid(INK), "backColorPrimary": solid(CARD),
            "fontColorSecondary": solid(INK), "backColorSecondary": solid(BG if STYLE["dark"] else "#F8FAFC"), **({"fontSize": num(size)} if size else {})}
    return {"columnHeaders": [{"properties": head}], "rowHeaders": [{"properties": {**head, "backColor": solid(CARD)}}], "values": [{"properties": body}],
            "grid": [{"properties": {"gridVertical": flag(False), "gridHorizontalColor": solid(EDGE), "outlineColor": solid(EDGE)}}]}


def tooltip_obj(tip_page):
    return {"visualTooltip": [{"properties": {"show": flag(True), "type": tx("ReportPage"), "section": tx(tip_page)}}]} if tip_page else {}


def v_textbox(page, key, x, y, w, h, z, runs, card=False, scale=None):
    font = {"fontFamily": STYLE["font"]} if STYLE["font"] != "Segoe UI" else {}
    paragraphs = []
    for text, style in runs:
        style = dict(style) if style else None
        align = style.pop("_align", None) if style else None
        para = {"textRuns": [{"value": text, **({"textStyle": {**font, **style}} if style else {})}]}
        if align:
            para["horizontalTextAlignment"] = align
        paragraphs.append(para)
    v = {"visualType": "textbox", "objects": {"general": [{"properties": {"paragraphs": paragraphs}}]}, "drillFilterOtherVisuals": True}
    if card:
        v["visualContainerObjects"] = card_objects()
    return container(page, key, x, y, w, h, z, v, scale=scale)


def v_shape(page, key, x, y, w, h, z, fill, hidden=False, border=False, radius=0, shape="rectangle"):
    if h <= 8 and shape == "rectangle" and not radius and not border:         # a rule: an empty text box with a background colour
        v = {"visualType": "textbox", "objects": {"general": [{"properties": {"paragraphs": [{"textRuns": [{"value": ""}]}]}}]},
             "visualContainerObjects": {"background": [{"properties": {"show": flag(True), "color": solid(fill), "transparency": num(0)}}], "border": [{"properties": {"show": flag(False)}}],
                                        "padding": [{"properties": {"top": num(0, "L"), "bottom": num(0, "L"), "left": num(0, "L"), "right": num(0, "L")}}]},
             "drillFilterOtherVisuals": True}
        return container(page, key, x, y, w, h, z, v, hidden=hidden)
    v = {"visualType": "basicShape",
         "objects": {"general": [{"properties": {"shapeType": tx(shape)}}],
                     "fill": [{"properties": {"show": flag(True), "fillColor": solid(fill)}}],
                     "line": [{"properties": {"show": flag(border), "lineColor": solid(EDGE), "weight": num(1)}}],
                     "rotation": [{"properties": {"shapeAngle": num(0)}}]},
         "visualContainerObjects": {"background": [{"properties": {"show": flag(False)}}], "border": [{"properties": {"show": flag(False)}}],
                                    "padding": [{"properties": {"top": num(0, "L"), "bottom": num(0, "L"), "left": num(0, "L"), "right": num(0, "L")}}]},
         "drillFilterOtherVisuals": True}
    if radius:
        v["objects"]["general"][0]["properties"]["roundEdge"] = num(radius, "L")
    return container(page, key, x, y, w, h, z, v, hidden=hidden)


def v_button(page, key, x, y, w, h, z, label, action, fill=None, text_color="#FFFFFF", size=11, bold=False, hidden=False):
    """A text button: a text box (with an optional coloured background) that carries the click action itself.
    action = ('page', page_name) or ('bookmark', bookmark_name)."""
    kind, target = action
    link = {"show": flag(True), "type": tx("PageNavigation" if kind == "page" else "Bookmark")}
    link["navigationSection" if kind == "page" else "bookmark"] = tx(target)
    style = {"fontSize": f"{size}pt", "color": text_color, **({"fontWeight": "bold"} if bold else {}), **({"fontFamily": STYLE["font"]} if STYLE["font"] != "Segoe UI" else {})}
    bg = ({"show": flag(True), "color": solid(fill), "transparency": num(0)} if fill else {"show": flag(False)})
    v = {"visualType": "textbox",
         "objects": {"general": [{"properties": {"paragraphs": [{"textRuns": [{"value": label, "textStyle": style}], "horizontalTextAlignment": "center"}]}}]},
         "visualContainerObjects": {"background": [{"properties": bg}], "border": [{"properties": {"show": flag(False)}}],
                                    "padding": [{"properties": {"top": num(max(round(h * K / 2 - size * 1.4), 2), "L")}}],
                                    "visualLink": [{"properties": link}]},
         "drillFilterOtherVisuals": True}
    return [container(page, key, x, y, w, h, z, v, hidden=hidden)]


def v_card(page, table, measure, fmt, x, y, w, h, z, plain=False):
    v = {"visualType": "cardVisual",
         "query": {"queryState": {"Data": {"projections": [proj(fld_meas(table, measure), table, measure, fmt)]}},
                   "sortDefinition": {"sort": [{"field": fld_meas(table, measure), "direction": "Descending"}], "isDefaultSort": True}},
         "visualContainerObjects": card_objects(), "drillFilterOtherVisuals": True}
    if plain:                                   # no automatic thousands or millions: 10,319 and not 10 tys.
        v["objects"] = {"value": [{"properties": {"labelDisplayUnits": num(1)}}]}
    return container(page, "card-" + measure, x, y, w, h, z, v)


def v_kpi(page, table, measure, fmt, trend_field, trend_name, x, y, w, h, z):
    """A KPI tile: the card, and a small line chart with no axes laid over its lower half (a sparkline)."""
    off = flag(False)
    number = container(page, "kpi-" + measure, x, y, w, h, z, {
        "visualType": "cardVisual",
        "query": {"queryState": {"Data": {"projections": [proj(fld_meas(table, measure), table, measure, fmt)]}}},
        "visualContainerObjects": card_objects(), "drillFilterOtherVisuals": True})
    spark = container(page, "spark-" + measure, x + w * 0.4, y + h * 0.36, w * 0.57, h * 0.6, z + 1, {
        "visualType": "lineChart",
        "query": {"queryState": {"Category": {"projections": [proj(trend_field, table, trend_name, active=True)]},
                                 "Y": {"projections": [proj(fld_meas(table, measure), table, measure, fmt)]}},
                  "sortDefinition": {"sort": [{"field": trend_field, "direction": "Ascending"}], "isDefaultSort": True}},
        "objects": {"valueAxis": [{"properties": {"show": off, "gridlineShow": off}}], "categoryAxis": [{"properties": {"show": off}}],
                    "legend": [{"properties": {"show": off}}], "labels": [{"properties": {"show": off}}]},
        "visualContainerObjects": {"title": [{"properties": {"show": off}}], "subTitle": [{"properties": {"show": off}}],
                                   "background": [{"properties": {"show": off}}], "border": [{"properties": {"show": off}}]},
        "drillFilterOtherVisuals": True})
    return [number, spark]


def v_slicer(page, table, col, label, x, y, w, h, z, dropdown=True, hidden=False, boxed=False):
    objects = {"header": [{"properties": {"show": flag(False)}}]}
    if dropdown:
        objects["data"] = [{"properties": {"mode": tx("Dropdown")}}]
    if not dropdown and STYLE["dark"]:
        objects["items"] = [{"properties": {"fontColor": solid(INK)}}]
    if boxed:     # a dropdown in a filled box (dark styles)
        objects["items"] = [{"properties": {"fontColor": solid(INK), "background": solid(CARD), "outline": solid(EDGE)}}]
    v = {"visualType": "slicer",
         "query": {"queryState": {"Values": {"projections": [proj(fld_col(table, col), table, col, active=True)]}}},
         "objects": objects,
         "visualContainerObjects": {"title": [{"properties": {"show": flag(True), "text": tx(label), "fontColor": solid(MUTED), "fontSize": num(10)}}],
                                    "background": [{"properties": {"show": flag(boxed), "color": solid(CARD), "transparency": num(0)}}],
                                    "border": [{"properties": {"show": flag(boxed), "color": solid(EDGE), "radius": num(STYLE["radius"] // 2), "width": num(1)}}]},
         "syncGroup": {"groupName": col, "fieldChanges": True, "filterChanges": True},
         "drillFilterOtherVisuals": True}
    return container(page, "slicer-" + col, x, y, w, h, z, v, hidden=hidden)


def v_chart(page, kind, table, cat_field, cat_name, measure, fmt, title, x, y, w, h, z, sort_by_measure=True, tip=None):
    q = {"queryState": {"Category": {"projections": [proj(cat_field, table, cat_name, active=True)]},
                        "Y": {"projections": [proj(fld_meas(table, measure), table, measure, fmt)]}}}
    q["sortDefinition"] = {"sort": [{"field": fld_meas(table, measure) if sort_by_measure else cat_field,
                                     "direction": "Descending" if sort_by_measure else "Ascending"}], "isDefaultSort": True}
    vco = card_objects(title)
    vco.update(tooltip_obj(tip))
    v = {"visualType": kind, "query": q, "objects": chart_objects(kind), "visualContainerObjects": vco, "drillFilterOtherVisuals": True}
    if not v["objects"]:
        del v["objects"]
    return container(page, f"{kind}-{cat_name}-{measure}", x, y, w, h, z, v)


def v_treemap(page, table, cat_field, cat_name, measure, fmt, title, x, y, w, h, z, tip=None):
    vco = card_objects(title)
    vco.update(tooltip_obj(tip))
    v = {"visualType": "treemap",
         "query": {"queryState": {"Group": {"projections": [proj(cat_field, table, cat_name, active=True)]},
                                  "Values": {"projections": [proj(fld_meas(table, measure), table, measure, fmt)]}}},
         "objects": chart_objects("treemap"), "visualContainerObjects": vco, "drillFilterOtherVisuals": True}
    return container(page, f"treemap-{cat_name}-{measure}", x, y, w, h, z, v)


def v_scatter(page, table, cat_field, cat_name, x_meas, y_meas, size_meas, fmts, title, x, y, w, h, z, tip=None):
    vco = card_objects(title)
    vco.update(tooltip_obj(tip))
    v = {"visualType": "scatterChart",
         "query": {"queryState": {"Category": {"projections": [proj(cat_field, table, cat_name, active=True)]},
                                  "X": {"projections": [proj(fld_meas(table, x_meas), table, x_meas, fmts[x_meas], active=True)]},
                                  "Y": {"projections": [proj(fld_meas(table, y_meas), table, y_meas, fmts[y_meas], active=True)]},
                                  "Size": {"projections": [proj(fld_meas(table, size_meas), table, size_meas, fmts[size_meas])]}}},
         "objects": chart_objects("scatterChart"), "visualContainerObjects": vco, "drillFilterOtherVisuals": True}
    return container(page, f"scatter-{cat_name}-{x_meas}-{y_meas}", x, y, w, h, z, v)


def v_matrix(page, table, rows, columns, measures, fmts, title, x, y, w, h, z):
    state = {"Rows": {"projections": [proj(fld_col(table, c), table, c, active=True) for c in rows]},
             "Values": {"projections": [proj(fld_meas(table, m), table, m, fmts[m]) for m in measures]}}
    if columns:
        state["Columns"] = {"projections": [proj(fld_col(table, c), table, c, active=True) for c in columns]}
    v = {"visualType": "pivotTable", "query": {"queryState": state}, "objects": grid_objects(), "visualContainerObjects": card_objects(title), "drillFilterOtherVisuals": True}
    return container(page, f"matrix-{'-'.join(rows)}-{'-'.join(columns)}", x, y, w, h, z, v)


def v_table(page, table, columns, extra_measures, title, x, y, w, h, z):
    projections = [proj(fld_col(table, c), table, c) for c in columns] + [proj(fld_meas(table, m), table, m) for m in extra_measures]
    v = {"visualType": "tableEx", "query": {"queryState": {"Values": {"projections": projections}}}, "objects": grid_objects(),
         "visualContainerObjects": card_objects(title), "drillFilterOtherVisuals": True}
    return container(page, "table-" + title, x, y, w, h, z, v)


def map_ramp():
    """The colour ramp of a filled map: from the middle to the strong end of the style's heat colours."""
    lo, mid, hi = STYLE["heat"]
    return {"linearGradient2": {"min": {"color": {"Literal": {"Value": "'" + mid + "'"}}}, "max": {"color": {"Literal": {"Value": "'" + hi + "'"}}},
                                "nullColoringStrategy": {"strategy": {"Literal": {"Value": "'noColor'"}}}}}


def kind_objects(kind, legend=False, axis=None, units=None, categorical=True):
    """Formatting for the chart types the layouts add (combo, stacked area, ribbon, funnel, waterfall, gauge, 100% stacked, pie, multi-row card)."""
    off, on = flag(False), flag(True)
    txt = {"labelColor": solid(MUTED)}
    lab = {"show": on, "color": solid(INK)}
    leg = [{"properties": {"show": flag(legend), "position": tx("Top"), "labelColor": solid(MUTED)}}]
    cat = {"showAxisTitle": off, "gridlineShow": off, **txt}
    val = {"showAxisTitle": off, "gridlineShow": off, **txt}
    if kind in ("hundredPercentStackedBarChart", "hundredPercentStackedColumnChart", "clusteredColumnChart", "clusteredBarChart", "barChart", "columnChart"):
        return {"categoryAxis": [{"properties": {**cat, "axisType": tx("Categorical")}}], "valueAxis": [{"properties": {**val, "show": off}}],
                "labels": [{"properties": lab}], "legend": leg}
    if kind in ("lineChart", "areaChart", "stackedAreaChart", "hundredPercentStackedAreaChart", "ribbonChart", "lineClusteredColumnComboChart"):
        return {"categoryAxis": [{"properties": {**cat, "axisType": tx("Categorical")} if kind in ("ribbonChart",) or (categorical and kind in ("lineChart", "areaChart", "stackedAreaChart", "hundredPercentStackedAreaChart")) else cat}],
                "valueAxis": [{"properties": val}], "legend": leg}
    on_bar = {"show": on, "color": solid(NAVY if STYLE["dark"] else "#FFFFFF"), **({"labelDisplayUnits": num(units)} if units else {})}      # labels sit on the bars
    if kind == "funnel":
        return {"labels": [{"properties": on_bar}], "legend": leg}
    if kind == "waterfallChart":
        return {"categoryAxis": [{"properties": cat}], "valueAxis": [{"properties": {**val, "show": off}}], "labels": [{"properties": {**lab, **({"labelDisplayUnits": num(units)} if units else {})}}], "legend": leg}
    if kind == "gauge":
        o = {"labels": [{"properties": {"color": solid(INK)}}], "calloutValue": [{"properties": {"color": solid(INK)}}]}
        if axis:
            o["axis"] = [{"properties": {"min": num(axis[0]), "max": num(axis[1])}}]
        return o
    if kind == "filledMap":
        return {"legend": [{"properties": {"show": off}}], "dataPoint": [{"properties": {"fillRule": map_ramp()}}]}
    if kind == "map":
        return {"legend": leg}
    if kind == "pieChart":
        return {"legend": [{"properties": {"show": on, "position": tx("Right"), "labelColor": solid(MUTED)}}], "labels": [{"properties": lab}]}
    if kind == "multiRowCard":
        return {"dataLabels": [{"properties": {"color": solid(INK), "fontSize": num(15)}}], "cardTitle": [{"properties": {"color": solid(MUTED), "fontSize": num(9)}}],
                "card": [{"properties": {"barShow": off, "outline": tx("None")}}]}
    o = chart_objects(kind)
    if kind == "scatterChart" and legend:
        o["legend"] = leg
    return o


def filter_config(table, col, values, page, key):
    """A visual-level filter: the column must be one of the values (text or whole numbers)."""
    lit = lambda x: {"Literal": {"Value": f"{x}L" if isinstance(x, int) else "'" + str(x) + "'"}}
    return {"filters": [{"name": oid(page, key, "filter"), "field": fld_col(table, col), "type": "Categorical", "howCreated": "User",
                         "filter": {"Version": 2, "From": [{"Name": "t", "Entity": table, "Type": 0}],
                                    "Where": [{"Condition": {"In": {"Expressions": [{"Column": {"Expression": {"SourceRef": {"Source": "t"}}, "Property": col}}],
                                                                    "Values": [[lit(x)] for x in values]}}}]}}]}


def v_gen(page, key, kind, table, roles, fmts, title, x, y, w, h, z, tip=None, sort=None, legend=False, axis=None, units=None, vfilter=None, ranges=None, no_labels=False,
          objects=None, extra=None, colors=None):
    """Any chart: roles = {role: [("c", column) | ("m", measure)]}; sort = ("c" | "m", name, "Ascending" | "Descending")."""
    state = {}
    for role, items in roles.items():
        projs = []
        for typ, name in items:
            if typ == "c" and kind == "scatterChart" and role in ("X", "Y"):
                projs.append({"field": {"Aggregation": {"Expression": fld_col(table, name), "Function": 1}}, "queryRef": f"Average({table}.{name})",
                              "nativeQueryRef": f"Average of {name}", "active": True})
            elif typ == "c":
                projs.append(proj(fld_col(table, name), table, name, active=True))
            else:
                projs.append(proj(fld_meas(table, name), table, name, fmts[name], active=(kind == "scatterChart" and role in ("X", "Y"))))
        state[role] = {"projections": projs}
    q = {"queryState": state}
    if sort:
        typ, name, direction = sort
        q["sortDefinition"] = {"sort": [{"field": fld_col(table, name) if typ == "c" else fld_meas(table, name), "direction": direction}], "isDefaultSort": True}
    vco = card_objects(title)
    vco.update(tooltip_obj(tip))
    v = {"visualType": kind, "query": q, "objects": kind_objects(kind, legend, axis, units, not any(r == "Category" and typ == "c" and name in ("Month", "Date") for r, items in roles.items() for typ, name in items)), "visualContainerObjects": vco, "drillFilterOtherVisuals": True}
    if not v["objects"]:
        del v["objects"]
    if objects is not None:                        # a complete replacement of the formatting
        v["objects"] = objects
    for name_, props_ in (extra or {}).items():    # or single properties added to it
        if name_ in v.get("objects", {}):
            v["objects"][name_][0]["properties"].update(props_)
        else:
            v.setdefault("objects", {})[name_] = [{"properties": props_}]
    if units and "labels" in v.get("objects", {}):    # labels in full (10,319 and not 10 tys.)
        v["objects"]["labels"][0]["properties"]["labelDisplayUnits"] = num(units)
    if colors:                                     # {series label: colour} for the column named in `colors_col` (the Series role)
        col_name, mapping = colors
        v.setdefault("objects", {})["dataPoint"] = [{"properties": {"fill": solid(col)},
                                                     "selector": {"data": [{"scopeId": {"Comparison": {"ComparisonKind": 0, "Left": fld_col(table, col_name),
                                                                                                        "Right": {"Literal": {"Value": "'" + label + "'"}}}}}]}}
                                                    for label, col in mapping.items()]
    if no_labels and kind == "scatterChart":       # a scatter of many points: no labels, no axis titles
        v["objects"]["categoryLabels"][0]["properties"]["show"] = flag(False)
        for axis_name in ("categoryAxis", "valueAxis"):
            v["objects"][axis_name][0]["properties"]["showAxisTitle"] = flag(False)
    if ranges and kind == "scatterChart":          # ((x min, x max), (y min, y max))
        for axis_name, (lo, hi) in zip(("categoryAxis", "valueAxis"), ranges):
            v["objects"][axis_name][0]["properties"].update({"start": num(lo), "end": num(hi)})
    cont = container(page, f"{kind}-{key}", x, y, w, h, z, v)
    if vfilter:             # a visual-level filter: (column, [allowed values])
        cont["filterConfig"] = filter_config(table, vfilter[0], vfilter[1], page, key)
    return cont


def v_heat(page, table, rows_col, cols_col, measure, fmts, title, x, y, w, h, z, color_measure=None, size=12, totals=True):
    """A matrix whose cells are shaded by the measure (a heat map); the three colours come from the style."""
    lo, mid, hi = STYLE["heat"]

    def colour(c):
        return {"color": {"Literal": {"Value": "'" + c + "'"}}}
    cm = color_measure or measure
    rule = {"solid": {"color": {"expr": {"FillRule": {"Input": fld_meas(table, cm),
            "FillRule": {"linearGradient3": {"min": colour(lo), "mid": colour(mid), "max": colour(hi),
                                             "nullColoringStrategy": {"strategy": {"Literal": {"Value": "'asZero'"}}}}}}}}}}
    objects = grid_objects(size)
    text = {"solid": {"color": {"expr": {"FillRule": {"Input": fld_meas(table, cm), "FillRule": {"linearGradient2": {
        "min": colour(INK), "max": colour(NAVY if STYLE["dark"] else "#FFFFFF"), "nullColoringStrategy": {"strategy": {"Literal": {"Value": "'asZero'"}}}}}}}}}}
    objects["values"].append({"properties": {"backColor": rule, "fontColor": solid(INK) if STYLE.get("heat_text") == "ink" else text},
                              "selector": {"data": [{"dataViewWildcard": {"matchingOption": 1}}], "metadata": f"{table}.{measure}"}})
    if not totals:
        objects["subTotals"] = [{"properties": {"rowSubtotals": flag(False), "columnSubtotals": flag(False)}}]
    state = {"Rows": {"projections": [proj(fld_col(table, rows_col), table, rows_col, active=True)]},
             "Columns": {"projections": [proj(fld_col(table, cols_col), table, cols_col, active=True)]},
             "Values": {"projections": [proj(fld_meas(table, measure), table, measure, fmts[measure])]}}
    v = {"visualType": "pivotTable", "query": {"queryState": state}, "objects": objects, "visualContainerObjects": card_objects(title), "drillFilterOtherVisuals": True}
    return container(page, f"heat-{rows_col}-{cols_col}", x, y, w, h, z, v)


def v_tilemap(page, table, row_col, col_col, label_measure, color_measure, fmts, title, x, y, w, h, z, size=26, padding=18):
    """A tile-grid map (one square per region, placed on a grid): a matrix of grid rows by grid columns whose cell text is `label_measure` and whose colour follows
    `color_measure`. No map service is involved, so it works in Publish to web."""
    lo, mid, hi = STYLE["heat"]

    def rule(a, b, c3=None):
        pts = {"min": {"color": {"Literal": {"Value": "'" + a + "'"}}}, "max": {"color": {"Literal": {"Value": "'" + b + "'"}}},
               "nullColoringStrategy": {"strategy": {"Literal": {"Value": "'asZero'"}}}}
        kind = "linearGradient2"
        if c3:
            pts["mid"], kind = {"color": {"Literal": {"Value": "'" + c3 + "'"}}}, "linearGradient3"
        return {"solid": {"color": {"expr": {"FillRule": {"Input": fld_meas(table, color_measure), "FillRule": {kind: pts}}}}}}
    selector = {"data": [{"dataViewWildcard": {"matchingOption": 1}}], "metadata": f"{table}.{label_measure}"}
    objects = grid_objects(size)
    hidden = {"fontColor": solid(CARD), "backColor": solid(CARD), "fontSize": num(8)}          # the grid numbers are hidden by painting them in the card colour
    objects["columnHeaders"] = [{"properties": hidden}]
    objects["rowHeaders"] = [{"properties": hidden}]
    objects["subTotals"] = [{"properties": {"rowSubtotals": flag(False), "columnSubtotals": flag(False)}}]
    objects["grid"] = [{"properties": {"gridVertical": flag(False), "gridHorizontal": flag(False), "rowPadding": num(padding), "outlineColor": solid(CARD)}}]
    objects["values"].append({"properties": {"backColor": rule(mid, hi), "fontColor": solid(INK) if STYLE.get("heat_text") == "ink" else rule(INK, NAVY if STYLE["dark"] else "#FFFFFF")}, "selector": selector})
    state = {"Rows": {"projections": [proj(fld_col(table, row_col), table, row_col, active=True)]},
             "Columns": {"projections": [proj(fld_col(table, col_col), table, col_col, active=True)]},
             "Values": {"projections": [proj(fld_meas(table, label_measure), table, label_measure)]}}
    v = {"visualType": "pivotTable", "query": {"queryState": state}, "objects": objects, "visualContainerObjects": card_objects(title), "drillFilterOtherVisuals": True}
    return container(page, f"tilemap-{label_measure}", x, y, w, h, z, v)


def kpi_row(c, pid, vis, nz, names, y=64, h=110, spark=3, plain=()):
    """A row of KPI tiles; the first `spark` of them get a trend line when the data has a date."""
    cw = (CW - c.gap * (len(names) - 1)) / len(names)
    for i, name in enumerate(names):
        pos = c.g(i * (cw + c.gap), y, cw, h)
        if c.date and i < spark:
            z = nz()
            nz(), nz()
            vis += v_kpi(pid, c.table, name, c.fmts[name], c.trend_f, "Month", *pos, z)
        else:
            vis.append(v_card(pid, c.table, name, c.fmts[name], *pos, nz(), plain=name in plain))


def layout_pulse(c):
    """For a time series: dims = hour-like axis, weekday-like columns, a segment (series) and a part-of-day band; measures 1 to 5 are the KPIs.
    Pages: Overview (KPIs, combo, gauge, one line per weekday), Rhythm (heat map, waterfall), Weather (stacked area, scatter, ribbon), Seasons."""
    t, f, g, tip, mw, sw = c.table, c.fmts, c.g, c.tip, c.main_w, c.side_w
    hour, wday, seg, band = c.d(0), c.d(1), c.d(2), c.d(3)
    m1, m2, m3, m4, m5 = (c.m(i) for i in range(5))
    pid, vis, nz = c.new_page("overview", c.title)
    kpi_row(c, pid, vis, nz, [m1, m3, m5, m4, m2])          # averages and shares carry the trend lines; a total would only show gaps in the records
    vis.append(v_gen(pid, "month", "lineClusteredColumnComboChart", t, {"Category": [("c", "Month")], "Y": [("m", m1)], "Y2": [("m", m3)]}, f,
                     f"{m1} (columns) and {m3} (line) by month", *g(0, 186, mw, 232), nz(), tip=tip, legend=True, sort=("c", "Month", "Ascending")))
    vis.append(v_gen(pid, "gauge", "gauge", t, {"Y": [("m", m1)], "MaxValue": [("m", m4)]}, f, f"{m1} against the busiest hour", *g(mw + c.gap, 186, sw, 232), nz()))
    vis.append(v_gen(pid, "hours", "lineChart", t, {"Category": [("c", hour)], "Series": [("c", wday)], "Y": [("m", m1)]}, f,
                     f"{m1} by {hour}, one line per {wday}", *g(0, 430, mw, 278), nz(), tip=tip, legend=True, sort=("c", hour, "Ascending")))
    vis.append(v_gen(pid, "band", "clusteredColumnChart", t, {"Category": [("c", band)], "Y": [("m", m1)]}, f, f"{m1} by {band}", *g(mw + c.gap, 430, sw, 278), nz(), tip=tip,
                     sort=("c", band, "Ascending")))

    pid, vis, nz = c.new_page("rhythm", f"The weekly rhythm of {m1.lower()}")
    vis.append(v_heat(pid, t, hour, wday, m1, f, f"{m1}: {hour} by {wday}", *g(0, 64, mw, 644), nz()))
    vis.append(v_gen(pid, "wday", "clusteredBarChart", t, {"Category": [("c", wday)], "Y": [("m", m1)]}, f, f"{m1} by {wday}", *g(mw + c.gap, 64, sw, 296), nz(), tip=tip,
                     sort=("c", wday, "Ascending")))
    vis.append(v_gen(pid, "waterfall", "waterfallChart", t, {"Category": [("c", band)], "Y": [("m", m2)]}, f, f"How {m2.lower()} add up across the day", *g(mw + c.gap, 372, sw, 336), nz(), tip=tip,
                     sort=("c", band, "Ascending")))

    pid, vis, nz = c.new_page("weather", f"{m1} and the weather")
    vis.append(v_gen(pid, "area", "hundredPercentStackedColumnChart", t, {"Category": [("c", "Month")], "Series": [("c", seg)], "Y": [("m", c.rows_name)]}, f,
                     f"Share of hours by {seg}, by month", *g(0, 64, mw, 296), nz(), tip=tip, legend=True, sort=("c", "Month", "Ascending")))
    vis.append(v_gen(pid, "seg", "clusteredBarChart", t, {"Category": [("c", seg)], "Y": [("m", m1)]}, f, f"{m1} by {seg}", *g(mw + c.gap, 64, sw, 296), nz(), tip=tip,
                     sort=("m", m1, "Descending")))
    vis.append(v_gen(pid, "scatter", "scatterChart", t, {"Category": [("c", seg)], "X": [("m", m3)], "Y": [("m", m1)], "Size": [("m", c.rows_name)]}, f,
                     f"{m1} against {m3} by {seg}", *g(0, 372, sw, 336), nz(), tip=tip))
    vis.append(v_gen(pid, "ribbon", "ribbonChart", t, {"Category": [("c", "Year")], "Series": [("c", seg)], "Y": [("m", m2)]}, f,
                     f"Which {seg.lower()} carries the most: {m2.lower()} by year", *g(sw + c.gap, 372, mw, 336), nz(), tip=tip, legend=True, sort=("c", "Year", "Ascending")))

    pid, vis, nz = c.new_page("seasons", "Seasons and years")
    vis.append(v_gen(pid, "line", "lineChart", t, {"Category": [("c", "Month")], "Y": [("m", m1)]}, f, f"{m1} by month", *g(0, 64, mw, 296), nz(), tip=tip, sort=("c", "Month", "Ascending")))
    vis.append(v_gen(pid, "year", "clusteredColumnChart", t, {"Category": [("c", "Year")], "Y": [("m", m1)]}, f, f"{m1} by year", *g(mw + c.gap, 64, sw, 296), nz(), tip=tip,
                     sort=("c", "Year", "Ascending")))
    vis.append(v_gen(pid, "temp", "lineChart", t, {"Category": [("c", "Month")], "Y": [("m", m3)]}, f, f"{m3} by month", *g(0, 372, sw, 336), nz(), tip=tip, sort=("c", "Month", "Ascending")))
    vis.append(v_gen(pid, "total", "areaChart", t, {"Category": [("c", "Month")], "Y": [("m", c.rows_name)]}, f, f"{c.rows_name} recorded per month (dips are missing records, not quiet roads)",
                     *g(sw + c.gap, 372, mw, 336), nz(), tip=tip, sort=("c", "Month", "Ascending")))


def layout_flow(c):
    """For categories and outcomes: dims = ordered group (age), entry (type), outcome (stage order), topic, gender, race, stay length, then up to four treatment fields;
    measures: 1 rate, 2 and 3 averages, 4 another average, 5 an additive total. Pages: Overview (funnel, 100% stacked), Patients (ribbon, pie), Stay (waterfall), Treatment."""
    t, f, g, tip, mw, sw, gap = c.table, c.fmts, c.g, c.tip, c.main_w, c.side_w, c.gap
    age, adm, ret, dx, gen, race, stay = (c.d(i) for i in range(7))
    tr = [c.d(i) for i in range(7, 11)]
    m1, m2, m3, m4, m5 = (c.m(i) for i in range(5))
    rows = c.rows_name
    pid, vis, nz = c.new_page("overview", c.title)
    kpi_row(c, pid, vis, nz, [rows, m1, m2, m3, m4])
    w1, w2 = round(CW * 0.34), round(CW * 0.33)
    w3 = CW - w1 - w2 - 2 * gap
    vis.append(v_gen(pid, "dx", "clusteredBarChart", t, {"Category": [("c", dx)], "Y": [("m", m1)]}, f, f"{m1} by {dx}", *g(0, 186, w1, 232), nz(), tip=tip, sort=("m", m1, "Descending")))
    vis.append(v_gen(pid, "funnel", "funnel", t, {"Category": [("c", ret)], "Y": [("m", rows)]}, f, f"{rows} by {ret}", *g(w1 + gap, 186, w2, 232), nz(), tip=tip, sort=("c", ret, "Ascending")))
    vis.append(v_gen(pid, "adm", "donutChart", t, {"Category": [("c", adm)], "Y": [("m", rows)]}, f, f"{rows} by {adm}", *g(w1 + w2 + 2 * gap, 186, w3, 232), nz(), tip=tip,
                     legend=True))
    vis.append(v_gen(pid, "age100", "hundredPercentStackedBarChart", t, {"Category": [("c", age)], "Series": [("c", ret)], "Y": [("m", rows)]}, f,
                     f"{ret} within each {age} group (share)", *g(0, 430, mw, 278), nz(), tip=tip, legend=True, sort=("c", age, "Ascending")))
    vis.append(v_treemap(pid, t, fld_col(t, dx), dx, rows, f[rows], f"{rows} by {dx}", *g(mw + gap, 430, sw, 278), nz(), tip=tip))

    pid, vis, nz = c.new_page("patients", "Who the patients are")
    vis.append(v_gen(pid, "ribbon", "ribbonChart", t, {"Category": [("c", age)], "Series": [("c", dx)], "Y": [("m", rows)]}, f, f"{dx} ranking by {age}", *g(0, 64, mw, 296), nz(), tip=tip,
                     legend=True, sort=("c", age, "Ascending")))
    vis.append(v_gen(pid, "gender", "pieChart", t, {"Category": [("c", gen)], "Y": [("m", rows)]}, f, f"{rows} by {gen}", *g(mw + gap, 64, sw, 296), nz(), tip=tip))
    vis.append(v_gen(pid, "agecol", "clusteredColumnChart", t, {"Category": [("c", age)], "Y": [("m", m1)]}, f, f"{m1} by {age}", *g(0, 372, sw, 336), nz(), tip=tip, sort=("c", age, "Ascending")))
    vis.append(v_gen(pid, "race", "clusteredBarChart", t, {"Category": [("c", race)], "Y": [("m", m1)]}, f, f"{m1} by {race}", *g(sw + gap, 372, mw, 336), nz(), tip=tip,
                     sort=("m", m1, "Descending")))

    pid, vis, nz = c.new_page("stay", "Length of stay and workload")
    vis.append(v_gen(pid, "scatter", "scatterChart", t, {"Category": [("c", dx)], "X": [("m", m3)], "Y": [("m", m2)], "Size": [("m", rows)]}, f, f"{m2} against {m3} by {dx}",
                     *g(0, 64, mw, 296), nz(), tip=tip))
    vis.append(v_gen(pid, "waterfall", "waterfallChart", t, {"Category": [("c", dx)], "Y": [("m", m5)]}, f, f"{m5} by {dx}", *g(mw + gap, 64, sw, 296), nz(), tip=tip, sort=("m", m5, "Descending"), units=1000))
    vis.append(v_gen(pid, "stay", "clusteredColumnChart", t, {"Category": [("c", stay)], "Y": [("m", rows)]}, f, f"{rows} by {stay}", *g(0, 372, sw, 336), nz(), tip=tip, sort=("c", stay, "Ascending")))
    vis.append(v_gen(pid, "adm2", "clusteredBarChart", t, {"Category": [("c", adm)], "Y": [("m", m2)]}, f, f"{m2} by {adm}", *g(sw + gap, 372, mw, 336), nz(), tip=tip, sort=("m", m2, "Descending")))

    pid, vis, nz = c.new_page("treatment", f"{m1} and treatment")
    slots = [g(0, 64, mw, 296), g(mw + gap, 64, sw, 296), g(0, 372, sw, 336), g(sw + gap, 372, mw, 336)]
    for slot, dn in zip(slots, tr):
        vis.append(v_gen(pid, "tr-" + dn, "clusteredColumnChart", t, {"Category": [("c", dn)], "Y": [("m", m1)]}, f, f"{m1} by {dn}", *slot, nz(), tip=tip, sort=("m", m1, "Descending")))


def layout_lab(c):
    """For numeric columns measured on items of two or more types: dims = type, an ordered score, three bands; measures: 1 score, 2 to 4 averages, 5 a share, 6 another average.
    Pages: Overview (gauge, multi-row card, columns, scatter by score), Chemistry (small multiples), Compare (100% stacked, matrix), Bands."""
    t, f, g, tip, mw, sw, gap = c.table, c.fmts, c.g, c.tip, c.main_w, c.side_w, c.gap
    typ, score, qband, aband, sband = (c.d(i) for i in range(5))
    m1, m2, m3, m4, m5, m6 = (c.m(i) for i in range(6))
    rows = c.rows_name
    pid, vis, nz = c.new_page("overview", c.title)
    gw, cw_ = round(CW * 0.18), round(CW * 0.42)
    vis.append(v_gen(pid, "gauge", "gauge", t, {"Y": [("m", m1)]}, f, f"{m1} (scale 3 to 9)", *g(0, 64, gw, 232), nz(), axis=(3, 9)))
    vis.append(v_gen(pid, "multi", "multiRowCard", t, {"Values": [("m", x) for x in (rows, m2, m3, m4, m5)]}, f, "At a glance", *g(gw + gap, 64, cw_, 232), nz()))
    vis.append(v_gen(pid, "score", "clusteredColumnChart", t, {"Category": [("c", score)], "Series": [("c", typ)], "Y": [("m", rows)]}, f, f"{rows} by {score}",
                     *g(gw + cw_ + 2 * gap, 64, CW - gw - cw_ - 2 * gap, 232), nz(), tip=tip, legend=True, sort=("c", score, "Ascending")))
    vis.append(v_gen(pid, "scatter", "scatterChart", t, {"Category": [("c", score)], "X": [("m", m2)], "Y": [("m", m3)], "Size": [("m", rows)]}, f,
                     f"Each {score}: {m2.lower()} against {m3.lower()}", *g(0, 308, mw, 200), nz(), tip=tip))
    vis.append(v_treemap(pid, t, fld_col(t, aband), aband, rows, f[rows], f"{rows} by {aband}", *g(mw + gap, 308, sw, 200), nz(), tip=tip))
    vis.append(v_gen(pid, "aband100", "hundredPercentStackedBarChart", t, {"Category": [("c", aband)], "Series": [("c", qband)], "Y": [("m", rows)]}, f,
                     f"{qband} within each {aband} band (share)", *g(0, 520, mw, 188), nz(), tip=tip, legend=True, sort=("c", aband, "Descending")))
    vis.append(v_gen(pid, "type", "donutChart", t, {"Category": [("c", typ)], "Y": [("m", rows)]}, f, f"{rows} by {typ}", *g(mw + gap, 520, sw, 188), nz(), tip=tip, legend=True))

    pid, vis, nz = c.new_page("chemistry", f"How the ingredients change with {score.lower()}")
    slots = [g(0, 64, mw, 296), g(mw + gap, 64, sw, 296), g(0, 372, sw, 336), g(sw + gap, 372, mw, 336)]
    for slot, m in zip(slots, (m2, m3, m6, m4)):
        vis.append(v_gen(pid, "line-" + m, "lineChart", t, {"Category": [("c", score)], "Series": [("c", typ)], "Y": [("m", m)]}, f, f"{m} by {score}", *slot, nz(), tip=tip,
                         legend=True, sort=("c", score, "Ascending")))

    pid, vis, nz = c.new_page("compare", "The types side by side")
    vis.append(v_gen(pid, "share", "hundredPercentStackedColumnChart", t, {"Category": [("c", score)], "Series": [("c", typ)], "Y": [("m", rows)]}, f,
                     f"Share of each {score} by {typ}", *g(0, 64, mw, 296), nz(), tip=tip, legend=True, sort=("c", score, "Ascending")))
    vis.append(v_matrix(pid, t, [typ], [], [rows, m1, m2, m3, m5], f, f"Measures by {typ}", *g(mw + gap, 64, sw, 296), nz()))
    vis.append(v_gen(pid, "good", "clusteredColumnChart", t, {"Category": [("c", aband)], "Series": [("c", typ)], "Y": [("m", m5)]}, f, f"{m5} by {aband} and {typ}",
                     *g(0, 372, sw, 336), nz(), tip=tip, legend=True, sort=("c", aband, "Ascending")))
    vis.append(v_gen(pid, "sugar", "clusteredColumnChart", t, {"Category": [("c", sband)], "Series": [("c", typ)], "Y": [("m", m1)]}, f, f"{m1} by {sband} and {typ}",
                     *g(sw + gap, 372, mw, 336), nz(), tip=tip, legend=True, sort=("c", sband, "Ascending")))

    pid, vis, nz = c.new_page("bands", "Alcohol, sugar and quality bands")
    vis.append(v_gen(pid, "aband", "clusteredColumnChart", t, {"Category": [("c", aband)], "Y": [("m", m1)]}, f, f"{m1} by {aband}", *g(0, 64, mw, 296), nz(), tip=tip, sort=("c", aband, "Ascending")))
    vis.append(v_gen(pid, "sband", "clusteredColumnChart", t, {"Category": [("c", sband)], "Y": [("m", m1)]}, f, f"{m1} by {sband}", *g(mw + gap, 64, sw, 296), nz(), tip=tip,
                     sort=("c", sband, "Ascending")))
    vis.append(v_gen(pid, "qband", "clusteredBarChart", t, {"Category": [("c", qband)], "Series": [("c", typ)], "Y": [("m", rows)]}, f, f"{rows} by {qband} and {typ}",
                     *g(0, 372, sw, 336), nz(), tip=tip, legend=True, sort=("c", qband, "Descending")))
    vis.append(v_treemap(pid, t, fld_col(t, sband), sband, rows, f[rows], f"{rows} by {sband}", *g(sw + gap, 372, mw, 336), nz(), tip=tip))


def v_insight(page, key, big, text, x, y, w, h, z):
    """A card with one big number and a sentence (the story layout's commentary)."""
    colour = {"INK": INK, "TEAL": TEAL, "ACCENT": ACCENT}[STYLE["callout"]]
    return v_textbox(page, key, x, y, w, h, z, [(big, {"fontSize": "36pt", "fontWeight": "bold", "color": colour}), (text, {"fontSize": "15pt", "color": INK})], card=True)


def v_rank(page, table, cols, measures, bar, fmts, title, x, y, w, h, z, sort_by, vfilter=None, direction="Descending"):
    """A ranked table: sorted by a measure, with data bars on one measure column."""
    projections = [proj(fld_col(table, cc), table, cc) for cc in cols] + [proj(fld_meas(table, m), table, m, fmts[m]) for m in measures]
    objs = grid_objects(13)
    objs["total"] = [{"properties": {"totals": flag(False)}}]
    lo, mid, hi = STYLE["heat"]
    shade = {"solid": {"color": {"expr": {"FillRule": {"Input": fld_meas(table, bar), "FillRule": {"linearGradient3": {
        "min": {"color": {"Literal": {"Value": "'" + lo + "'"}}}, "mid": {"color": {"Literal": {"Value": "'" + mid + "'"}}}, "max": {"color": {"Literal": {"Value": "'" + hi + "'"}}},
        "nullColoringStrategy": {"strategy": {"Literal": {"Value": "'asZero'"}}}}}}}}}}
    objs["values"].append({"properties": {"backColor": shade}, "selector": {"data": [{"dataViewWildcard": {"matchingOption": 1}}], "metadata": f"{table}.{bar}"}})
    v = {"visualType": "tableEx",
         "query": {"queryState": {"Values": {"projections": projections}}, "sortDefinition": {"sort": [{"field": fld_meas(table, sort_by), "direction": direction}], "isDefaultSort": True}},
         "objects": objs, "visualContainerObjects": card_objects(title), "drillFilterOtherVisuals": True}
    key = "rank-" + bar + "-" + "".join(ch for ch in title if ch.isalnum())[:24]
    cont = container(page, key, x, y, w, h, z, v)
    if vfilter:
        cont["filterConfig"] = filter_config(table, vfilter[0], vfilter[1], page, key)
    return cont


def layout_board(c):
    """A scoreboard (actual against target): dims = department, team, day, week, target outcome, incentive band, overtime band, style changes;
    measures: 1 actual (headline), 2 share on target, 3 target, 4 overtime, 5 incentive. Pages: Overview (ranked table with data bars), Teams, Drivers, Trend."""
    t, f, g, tip, mw, sw, gap = c.table, c.fmts, c.g, c.tip, c.main_w, c.side_w, c.gap
    dept, team, day, week, met, incb, otb, sty = (c.d(i) for i in range(8))
    m1, m2, m3, m4, m5 = (c.m(i) for i in range(5))
    rows, dcol = c.rows_name, c.date["name"] if c.date else None
    pid, vis, nz = c.new_page("overview", c.title)
    kpi_row(c, pid, vis, nz, [rows, m1, m2, m3, m4, m5], spark=0)
    wt, wm = round(CW * 0.4), round(CW * 0.27)
    wr = CW - wt - wm - 2 * gap
    vis.append(v_rank(pid, t, [team], [rows, m1, m2, m3], m1, f, f"{team}s ranked by {m1.lower()}", *g(0, 186, wt, 522), nz(), m1))
    vis.append(v_gen(pid, "met", "barChart", t, {"Category": [("c", team)], "Series": [("c", met)], "Y": [("m", rows)]}, f, f"{rows} on and off target by {team.lower()}",
                     *g(wt + gap, 186, wm, 522), nz(), tip=tip, legend=True, sort=("c", team, "Descending")))
    vis.append(v_gen(pid, "dept", "clusteredBarChart", t, {"Category": [("c", dept)], "Y": [("m", m2)]}, f, f"{m2} by {dept}", *g(wt + wm + 2 * gap, 186, wr, 232), nz(), tip=tip,
                     sort=("m", m2, "Descending")))
    vis.append(v_gen(pid, "day", "clusteredColumnChart", t, {"Category": [("c", day)], "Y": [("m", m1)]}, f, f"{m1} by {day}", *g(wt + wm + 2 * gap, 430, wr, 278), nz(), tip=tip,
                     sort=("c", day, "Ascending")))

    pid, vis, nz = c.new_page("teams", "Team by team")
    vis.append(v_gen(pid, "team", "clusteredBarChart", t, {"Category": [("c", team)], "Y": [("m", m1)]}, f, f"{m1} by {team}", *g(0, 64, mw, 296), nz(), tip=tip, sort=("m", m1, "Descending")))
    vis.append(v_gen(pid, "inc", "scatterChart", t, {"Category": [("c", team)], "X": [("m", m5)], "Y": [("m", m1)], "Size": [("m", rows)]}, f, f"{m1} against {m5.lower()} by {team.lower()}",
                     *g(mw + gap, 64, sw, 296), nz(), tip=tip))
    vis.append(v_gen(pid, "met", "clusteredBarChart", t, {"Category": [("c", team)], "Y": [("m", m2)]}, f, f"{m2} by {team.lower()}", *g(0, 372, sw, 336), nz(), tip=tip,
                     sort=("m", m2, "Descending")))
    vis.append(v_gen(pid, "week", "clusteredColumnChart", t, {"Category": [("c", week)], "Y": [("m", m1), ("m", m3)]}, f, f"{m1} and {m3.lower()} by {week.lower()}",
                     *g(sw + gap, 372, mw, 336), nz(), tip=tip, legend=True, sort=("c", week, "Ascending")))

    pid, vis, nz = c.new_page("drivers", "What goes with higher output")
    vis.append(v_gen(pid, "inc", "clusteredColumnChart", t, {"Category": [("c", incb)], "Y": [("m", m1)]}, f, f"{m1} by {incb}", *g(0, 64, mw, 296), nz(), tip=tip, sort=("c", incb, "Ascending")))
    vis.append(v_gen(pid, "ot", "clusteredColumnChart", t, {"Category": [("c", otb)], "Y": [("m", m1)]}, f, f"{m1} by {otb}", *g(mw + gap, 64, sw, 296), nz(), tip=tip,
                     sort=("c", otb, "Ascending")))
    vis.append(v_gen(pid, "sty", "clusteredColumnChart", t, {"Category": [("c", sty)], "Y": [("m", m1)]}, f, f"{m1} by {sty}", *g(0, 372, sw, 336), nz(), tip=tip, sort=("c", sty, "Ascending")))
    vis.append(v_gen(pid, "ot2", "scatterChart", t, {"Category": [("c", team)], "X": [("m", m4)], "Y": [("m", m1)], "Size": [("m", rows)]}, f, f"{m1} against {m4.lower()} by {team.lower()}",
                     *g(sw + gap, 372, mw, 336), nz(), tip=tip))

    pid, vis, nz = c.new_page("trend", "Day by day")
    if dcol:
        vis.append(v_gen(pid, "line", "lineChart", t, {"Category": [("c", dcol)], "Y": [("m", m1), ("m", m3)]}, f, f"{m1} against {m3.lower()}, day by day", *g(0, 64, CW, 296), nz(), tip=tip,
                         legend=True, sort=("c", dcol, "Ascending")))
        vis.append(v_gen(pid, "inc", "areaChart", t, {"Category": [("c", dcol)], "Y": [("m", m5)]}, f, f"{m5} by day", *g(sw + gap, 372, mw, 336), nz(), tip=tip, sort=("c", dcol, "Ascending")))
    vis.append(v_gen(pid, "met", "clusteredColumnChart", t, {"Category": [("c", week)], "Y": [("m", m2)]}, f, f"{m2} by {week.lower()}", *g(0, 372, sw, 336), nz(), tip=tip, sort=("c", week, "Ascending")))


def layout_story(c):
    """Insight cards beside the charts: dims = subject, sex, study time, mother's education, past failures, weekend alcohol, going out, result, absence band, wants higher
    education, mother's job, internet; measures: 1 final grade, 2 pass rate, 3 absences, 4 first-period grade, 5 a share; six --insight cards. Pages: Overview, Study, Home, Habits."""
    t, f, g, tip, gap = c.table, c.fmts, c.g, c.tip, c.gap
    sub, sex, study, medu, fail, alc, out, res, absb, higher, mjob, net = (c.d(i) for i in range(12))
    m1, m2, m3, m4, m5 = (c.m(i) for i in range(5))
    rows = c.rows_name
    ins = list(c.insights) + [("", "")] * 6
    w1 = round(CW * 0.3)
    rw = CW - w1 - gap

    pid, vis, nz = c.new_page("overview", c.title)
    for i in range(3):
        vis.append(v_insight(pid, f"insight{i}", ins[i][0], ins[i][1], *g(0, 64 + i * 216, w1, 204), nz()))
    vis.append(v_gen(pid, "study", "clusteredColumnChart", t, {"Category": [("c", study)], "Y": [("m", m4), ("m", m1)]}, f, f"{m4} and {m1.lower()} by {study.lower()}", *g(w1 + gap, 64, rw, 316), nz(),
                     tip=tip, legend=True, sort=("c", study, "Ascending")))
    vis.append(v_gen(pid, "medu", "hundredPercentStackedBarChart", t, {"Category": [("c", medu)], "Series": [("c", res)], "Y": [("m", rows)]}, f, f"{res} within each {medu.lower()} group (share)",
                     *g(w1 + gap, 392, rw, 316), nz(), tip=tip, legend=True, sort=("c", medu, "Descending")))

    def chapter(key, heading, k, top_left, top_right, bottom):
        pid, vis, nz = c.new_page(key, heading)
        vis.append(v_insight(pid, "insight", ins[k][0], ins[k][1], *g(0, 64, w1, 250), nz()))
        vis.append(v_card(pid, t, m1, f[m1], *g(0, 326, w1, 186), nz()))
        vis.append(v_card(pid, t, m2, f[m2], *g(0, 522, w1, 186), nz()))
        lw = round(rw * 0.55)
        for (kind, name, roles, ttl, srt), pos in zip((top_left, top_right, bottom), (g(w1 + gap, 64, lw, 316), g(w1 + gap + lw + gap, 64, rw - lw - gap, 316), g(w1 + gap, 392, rw, 316))):
            vis.append(v_gen(pid, name, kind, t, roles, f, ttl, *pos, nz(), tip=tip, legend=kind in ("columnChart", "hundredPercentStackedColumnChart"), sort=srt))

    chapter("study", "Hours at the desk", 3,
            ("clusteredColumnChart", "study", {"Category": [("c", study)], "Y": [("m", m1)]}, f"{m1} by {study.lower()}", ("c", study, "Ascending")),
            ("clusteredBarChart", "fail", {"Category": [("c", fail)], "Y": [("m", m2)]}, f"{m2} by {fail.lower()}", ("c", fail, "Ascending")),
            ("scatterChart", "abs", {"Category": [("c", study)], "X": [("m", m3)], "Y": [("m", m1)], "Size": [("m", rows)]}, f"{m1} against {m3.lower()} by {study.lower()}", None))
    chapter("home", "Where students come from", 4,
            ("clusteredColumnChart", "medu", {"Category": [("c", medu)], "Y": [("m", m1)]}, f"{m1} by {medu.lower()}", ("c", medu, "Ascending")),
            ("clusteredBarChart", "net", {"Category": [("c", net)], "Y": [("m", m1)]}, f"{m1} by {net.lower()}", ("m", m1, "Descending")),
            ("hundredPercentStackedColumnChart", "higher", {"Category": [("c", medu)], "Series": [("c", higher)], "Y": [("m", rows)]}, f"{higher.replace('_', ' ')} within each {medu.lower()} group", ("c", medu, "Ascending")))
    chapter("habits", "Weekends, friends and absences", 5,
            ("clusteredColumnChart", "alc", {"Category": [("c", alc)], "Y": [("m", m1)]}, f"{m1} by {alc.lower()}", ("c", alc, "Ascending")),
            ("clusteredColumnChart", "out", {"Category": [("c", out)], "Y": [("m", m1)]}, f"{m1} by {out.lower()}", ("c", out, "Ascending")),
            ("columnChart", "absb", {"Category": [("c", alc)], "Series": [("c", absb)], "Y": [("m", rows)]}, f"{rows} by {alc.lower()} and {absb.lower()}", ("c", alc, "Ascending")))


def layout_mix(c):
    """Stacked columns and bars over bands: dims = season, wind, a concentration band (in order), heating season, hour; measures: 1 average (headline), 2 a share, 3 temperature,
    4 maximum, 5 wind speed. Needs a date. Filters sit in the left panel when the style has one. Pages: Overview, Bands, Seasons, Wind."""
    t, f, g, tip, mw, sw, gap = c.table, c.fmts, c.g, c.tip, c.main_w, c.side_w, c.gap
    season, wind, band, heat, hour = (c.d(i) for i in range(5))
    m1, m2, m3, m4, m5 = (c.m(i) for i in range(5))
    rows = c.rows_name
    pid, vis, nz = c.new_page("overview", c.title)
    kpi_row(c, pid, vis, nz, [m1, m2, m3, m4, m5])
    vis.append(v_gen(pid, "area", "areaChart", t, {"Category": [("c", "Month")], "Y": [("m", m1)]}, f, f"{m1} by month", *g(0, 186, mw, 232), nz(), tip=tip, sort=("c", "Month", "Ascending")))
    vis.append(v_gen(pid, "band", "donutChart", t, {"Category": [("c", band)], "Y": [("m", rows)]}, f, f"{rows} by {band}", *g(mw + gap, 186, sw, 232), nz(), tip=tip, legend=True))
    vis.append(v_gen(pid, "year", "columnChart", t, {"Category": [("c", "Year")], "Series": [("c", band)], "Y": [("m", rows)]}, f, f"{rows} by year and {band}", *g(0, 430, mw, 278), nz(),
                     tip=tip, legend=True, sort=("c", "Year", "Ascending")))
    vis.append(v_gen(pid, "season", "barChart", t, {"Category": [("c", season)], "Series": [("c", band)], "Y": [("m", rows)]}, f, f"{rows} by {season} and {band}", *g(mw + gap, 430, sw, 278), nz(),
                     tip=tip, sort=("c", season, "Descending")))

    pid, vis, nz = c.new_page("bands", "How often the air is good or bad")
    vis.append(v_gen(pid, "month", "columnChart", t, {"Category": [("c", "Month")], "Series": [("c", band)], "Y": [("m", rows)]}, f, f"{rows} by month and {band}", *g(0, 64, CW, 296), nz(),
                     tip=tip, legend=True, sort=("c", "Month", "Ascending")))
    vis.append(v_gen(pid, "wind", "barChart", t, {"Category": [("c", wind)], "Series": [("c", band)], "Y": [("m", rows)]}, f, f"{rows} by {wind} and {band}", *g(0, 372, sw, 336), nz(),
                     tip=tip, sort=("c", wind, "Descending")))
    vis.append(v_gen(pid, "hour", "clusteredColumnChart", t, {"Category": [("c", hour)], "Series": [("c", heat)], "Y": [("m", m1)]}, f, f"{m1} by {hour}, heating season and the rest of the year",
                     *g(sw + gap, 372, mw, 336), nz(), tip=tip, legend=True, sort=("c", hour, "Ascending")))

    pid, vis, nz = c.new_page("seasons", "Seasons and heating")
    vis.append(v_gen(pid, "line", "lineChart", t, {"Category": [("c", "Month")], "Y": [("m", m1)]}, f, f"{m1} by month", *g(0, 64, mw, 296), nz(), tip=tip, sort=("c", "Month", "Ascending")))
    vis.append(v_gen(pid, "season", "clusteredColumnChart", t, {"Category": [("c", season)], "Y": [("m", m1)]}, f, f"{m1} by {season}", *g(mw + gap, 64, sw, 296), nz(), tip=tip,
                     sort=("c", season, "Ascending")))
    vis.append(v_gen(pid, "heat", "clusteredColumnChart", t, {"Category": [("c", heat)], "Y": [("m", m1)]}, f, f"{m1} by {heat.lower()}", *g(0, 372, sw, 336), nz(), tip=tip, sort=("m", m1, "Descending")))
    vis.append(v_gen(pid, "hours", "lineChart", t, {"Category": [("c", hour)], "Series": [("c", season)], "Y": [("m", m1)]}, f, f"{m1} by {hour}, one line per {season.lower()}", *g(sw + gap, 372, mw, 336),
                     nz(), tip=tip, legend=True, sort=("c", hour, "Ascending")))

    pid, vis, nz = c.new_page("wind", "Which way the wind blows")
    vis.append(v_gen(pid, "bar", "clusteredBarChart", t, {"Category": [("c", wind)], "Y": [("m", m1)]}, f, f"{m1} by {wind}", *g(0, 64, mw, 296), nz(), tip=tip, sort=("m", m1, "Descending")))
    vis.append(v_treemap(pid, t, fld_col(t, wind), wind, rows, f[rows], f"{rows} by {wind}", *g(mw + gap, 64, sw, 296), nz(), tip=tip))
    vis.append(v_gen(pid, "scatter", "scatterChart", t, {"Category": [("c", wind)], "X": [("m", m5)], "Y": [("m", m1)], "Size": [("m", rows)]}, f, f"{m1} against {m5.lower()} by {wind.lower()}",
                     *g(0, 372, sw, 336), nz(), tip=tip))
    vis.append(v_gen(pid, "share", "hundredPercentStackedColumnChart", t, {"Category": [("c", wind)], "Series": [("c", band)], "Y": [("m", rows)]}, f, f"Share of hours by {wind.lower()} and {band}",
                     *g(sw + gap, 372, mw, 336), nz(), tip=tip, legend=True, sort=("c", wind, "Ascending")))


def layout_atlas(c):
    """Maps first: dims = state, strength group, month, decade, rating, hour; measures: 1 deaths, 2 injuries, 3 average length, 4 violent count, 5 deadly share, 6 average width;
    measure 7 is the tile label (the state code); needs columns Grid_row, Grid_col, Tornado_id, Start_lat and Start_lon. Pages: Map (tile grid), States, Strength, Seasons, Dots."""
    t, f, g, tip, gap = c.table, c.fmts, c.g, c.tip, c.gap
    state, strength, month, decade, ef, hour = (c.d(i) for i in range(6))
    m1, m2, m3, m4, m5, m6, tile = (c.m(i) for i in range(7))
    rows = c.rows_name
    mw = round(CW * 0.61)
    sw = CW - mw - gap
    w1 = round(CW * 0.6)
    rw = CW - w1 - gap
    pid, vis, nz = c.new_page("overview", c.title)
    vis.append(v_tilemap(pid, t, "Grid_row", "Grid_col", tile, rows, f, f"{rows} by {state.lower()} (one square a state; the more tornadoes, the yellower)", *g(0, 64, w1, 644), nz()))
    cw3 = (rw - 2 * gap) / 3
    for i, name in enumerate([rows, m1, m4]):
        vis.append(v_card(pid, t, name, f[name], *g(w1 + gap + i * (cw3 + gap), 64, cw3, 100), nz()))
    vis.append(v_gen(pid, "decade", "columnChart", t, {"Category": [("c", decade)], "Y": [("m", rows)]}, f, f"{rows} by {decade.lower()}", *g(w1 + gap, 176, rw, 250), nz(), tip=tip,
                     sort=("c", decade, "Ascending")))
    vis.append(v_gen(pid, "strength", "donutChart", t, {"Category": [("c", strength)], "Y": [("m", rows)]}, f, f"{rows} by {strength.lower()}", *g(w1 + gap, 438, rw, 270), nz(), tip=tip,
                     legend=True))

    pid, vis, nz = c.new_page("states", "State by state")
    lw = round(CW * 0.38)
    vis.append(v_gen(pid, "deaths", "clusteredBarChart", t, {"Category": [("c", state)], "Y": [("m", m1)]}, f, f"{m1} by {state.lower()}", *g(0, 64, lw, 644), nz(), tip=tip,
                     sort=("m", m1, "Descending")))
    vis.append(v_treemap(pid, t, fld_col(t, state), state, rows, f[rows], f"{rows} by {state.lower()}", *g(lw + gap, 64, CW - lw - gap, 296), nz(), tip=tip))
    vis.append(v_gen(pid, "scatter", "scatterChart", t, {"Category": [("c", state)], "X": [("m", rows)], "Y": [("m", m1)], "Size": [("m", m2)]}, f,
                     f"{m1} against {rows.lower()} by {state.lower()}", *g(lw + gap, 372, CW - lw - gap, 336), nz(), tip=tip))

    pid, vis, nz = c.new_page("strength", "From weak to violent")
    vis.append(v_gen(pid, "funnel", "funnel", t, {"Category": [("c", ef)], "Y": [("m", rows)]}, f, f"{rows} by rating", *g(0, 64, mw, 296), nz(), tip=tip, sort=("c", ef, "Ascending")))
    vis.append(v_gen(pid, "deaths", "clusteredColumnChart", t, {"Category": [("c", ef)], "Y": [("m", m1)]}, f, f"{m1} by rating", *g(mw + gap, 64, sw, 296), nz(), tip=tip,
                     sort=("c", ef, "Ascending")))
    vis.append(v_gen(pid, "share", "hundredPercentStackedColumnChart", t, {"Category": [("c", decade)], "Series": [("c", strength)], "Y": [("m", rows)]}, f,
                     f"Share of {strength.lower()} by {decade.lower()}", *g(0, 372, mw, 336), nz(), tip=tip, legend=True, sort=("c", decade, "Ascending")))
    vis.append(v_gen(pid, "size", "scatterChart", t, {"Category": [("c", ef)], "X": [("m", m3)], "Y": [("m", m6)], "Size": [("m", rows)]}, f, f"{m6} against {m3.lower()} by rating",
                     *g(mw + gap, 372, sw, 336), nz(), tip=tip))

    pid, vis, nz = c.new_page("seasons", "When tornadoes strike")
    vis.append(v_gen(pid, "month", "columnChart", t, {"Category": [("c", month)], "Series": [("c", strength)], "Y": [("m", rows)]}, f, f"{rows} by month and {strength.lower()}", *g(0, 64, mw, 296),
                     nz(), tip=tip, legend=True, sort=("c", month, "Ascending")))
    vis.append(v_gen(pid, "hour", "lineChart", t, {"Category": [("c", hour)], "Y": [("m", rows)]}, f, f"{rows} by {hour.lower()} of the day", *g(mw + gap, 64, sw, 296), nz(), tip=tip,
                     sort=("c", hour, "Ascending")))
    vis.append(v_heat(pid, t, decade, month, rows, f, f"{rows}: {decade.lower()} by month", *g(0, 372, CW, 336), nz()))

    pid, vis, nz = c.new_page("paths", "Where the violent ones started")
    vis.append(v_gen(pid, "dots", "scatterChart", t, {"Category": [("c", "Tornado_id")], "Series": [("c", ef)], "X": [("c", "Start_lon")], "Y": [("c", "Start_lat")], "Size": [("m", m1)]}, f,
                     "Violent tornadoes (EF4 and EF5) where they touched down (longitude against latitude), sized by deaths", *g(0, 64, w1, 644), nz(), tip=tip, legend=True,
                     vfilter=(ef, ["EF4", "EF5"]), ranges=((-112, -70), (26, 50)), no_labels=True))
    vis.append(v_card(pid, t, m4, f[m4], *g(w1 + gap, 64, (rw - gap) / 2, 100), nz()))
    vis.append(v_card(pid, t, m5, f[m5], *g(w1 + gap + (rw - gap) / 2 + gap, 64, (rw - gap) / 2, 100), nz()))
    vis.append(v_gen(pid, "dec", "columnChart", t, {"Category": [("c", decade)], "Y": [("m", m4)]}, f, f"{m4} by {decade.lower()}", *g(w1 + gap, 176, rw, 250), nz(), tip=tip,
                     sort=("c", decade, "Ascending")))
    vis.append(v_gen(pid, "st", "clusteredBarChart", t, {"Category": [("c", state)], "Y": [("m", m4)]}, f, f"{m4} by {state.lower()}", *g(w1 + gap, 438, rw, 270), nz(), tip=tip,
                     sort=("m", m4, "Descending")))


def layout_macro(c):
    """Latest values and what changed: dims = recession flag, decade, inflation band, Fed stance; needs a date; measures 1 to 6 are latest values, 7 to 12 their change against a
    year earlier, 13 to 19 averages (unemployment, inflation, Fed funds, 10-year, 2-year, mortgage, 10y-2y spread, payroll growth). Pages: Overview, Rates, Inflation, Cycles."""
    t, f, g, tip, gap = c.table, c.fmts, c.g, c.tip, c.gap
    rec, dec, infb, stance = (c.d(i) for i in range(4))
    latest = [c.m(i) for i in range(6)]
    change = [c.m(i) for i in range(6, 12)]
    unemp, infl, fed, y10, y2, mort, spread, payroll = (c.m(i) for i in range(12, 20))
    mw = round(CW * 0.61)
    sw = CW - mw - gap
    pid, vis, nz = c.new_page("overview", c.title)
    cw6 = (CW - 5 * gap) / 6
    for i in range(6):
        vis.append(v_card(pid, t, latest[i], f[latest[i]], *g(i * (cw6 + gap), 64, cw6, 84), nz()))
    for i in range(6):
        vis.append(v_card(pid, t, change[i], f[change[i]], *g(i * (cw6 + gap), 156, cw6, 64), nz()))
    vis.append(v_gen(pid, "combo", "lineClusteredColumnComboChart", t, {"Category": [("c", "Month")], "Y": [("m", unemp)], "Y2": [("m", infl)]}, f, f"{unemp} (columns) and {infl.lower()} (line)",
                     *g(0, 232, mw, 240), nz(), tip=tip, legend=True, sort=("c", "Month", "Ascending")))
    vis.append(v_gen(pid, "rates", "lineChart", t, {"Category": [("c", "Month")], "Y": [("m", fed), ("m", y10), ("m", mort)]}, f, "Fed funds, 10-year yield and mortgage rate", *g(mw + gap, 232, sw, 240), nz(),
                     tip=tip, legend=True, sort=("c", "Month", "Ascending")))
    vis.append(v_gen(pid, "spread", "areaChart", t, {"Category": [("c", "Month")], "Y": [("m", spread)]}, f, f"{spread} (below zero the yield curve is inverted)", *g(0, 484, mw, 224), nz(), tip=tip,
                     sort=("c", "Month", "Ascending")))
    vis.append(v_gen(pid, "phillips", "scatterChart", t, {"Category": [("c", "Year")], "Series": [("c", dec)], "X": [("m", unemp)], "Y": [("m", infl)]}, f, f"{infl} against {unemp.lower()}, one point a year",
                     *g(mw + gap, 484, sw, 224), nz(), tip=tip, legend=True))

    pid, vis, nz = c.new_page("rates", "Rates and the yield curve")
    vis.append(v_gen(pid, "lines", "lineChart", t, {"Category": [("c", "Month")], "Y": [("m", y10), ("m", y2), ("m", fed)]}, f, "10-year yield, 2-year yield and Fed funds rate", *g(0, 64, CW, 296), nz(), tip=tip,
                     legend=True, sort=("c", "Month", "Ascending")))
    vis.append(v_gen(pid, "spread", "areaChart", t, {"Category": [("c", "Month")], "Y": [("m", spread)]}, f, spread, *g(0, 372, mw, 336), nz(), tip=tip, sort=("c", "Month", "Ascending")))
    vis.append(v_gen(pid, "mort", "clusteredColumnChart", t, {"Category": [("c", dec)], "Y": [("m", mort)]}, f, f"{mort} by {dec.lower()}", *g(mw + gap, 372, sw, 336), nz(), tip=tip,
                     sort=("c", dec, "Ascending")))

    pid, vis, nz = c.new_page("jobs", "Prices and jobs")
    vis.append(v_gen(pid, "infl", "lineChart", t, {"Category": [("c", "Month")], "Y": [("m", infl)]}, f, infl, *g(0, 64, mw, 296), nz(), tip=tip, sort=("c", "Month", "Ascending")))
    vis.append(v_gen(pid, "dec", "clusteredColumnChart", t, {"Category": [("c", dec)], "Y": [("m", infl)]}, f, f"{infl} by {dec.lower()}", *g(mw + gap, 64, sw, 296), nz(), tip=tip,
                     sort=("c", dec, "Ascending")))
    vis.append(v_gen(pid, "band", "clusteredBarChart", t, {"Category": [("c", infb)], "Y": [("m", unemp)]}, f, f"{unemp} by {infb.lower()}", *g(0, 372, sw, 336), nz(), tip=tip,
                     sort=("c", infb, "Descending")))
    vis.append(v_gen(pid, "pay", "lineChart", t, {"Category": [("c", "Month")], "Y": [("m", payroll)]}, f, payroll, *g(sw + gap, 372, mw, 336), nz(), tip=tip, sort=("c", "Month", "Ascending")))

    pid, vis, nz = c.new_page("cycles", "Recessions, decades and the Fed")
    vis.append(v_gen(pid, "rec", "clusteredBarChart", t, {"Category": [("c", rec)], "Y": [("m", unemp), ("m", infl), ("m", fed), ("m", y10)]}, f, f"Averages in recessions and expansions", *g(0, 64, mw, 296),
                     nz(), tip=tip, legend=True))
    vis.append(v_matrix(pid, t, [dec], [], [unemp, infl, fed, y10, mort], f, f"Averages by {dec.lower()}", *g(mw + gap, 64, sw, 296), nz()))
    vis.append(v_gen(pid, "stance", "clusteredColumnChart", t, {"Category": [("c", stance)], "Y": [("m", unemp), ("m", infl)]}, f, f"{unemp} and {infl.lower()} by {stance.lower()}", *g(0, 372, sw, 336),
                     nz(), tip=tip, legend=True, sort=("c", stance, "Ascending")))
    vis.append(v_heat(pid, t, dec, rec, unemp, f, f"{unemp}: {dec.lower()} by phase of the cycle", *g(sw + gap, 372, mw, 336), nz()))


def layout_catalog(c):
    """A vehicle catalogue: dims = class, fuel type, make, transmission, drive, decade, cylinders, engine-size band; needs a Year column; measures: 1 average MPG, 2 CO2, 3 fuel cost,
    4 EV range, 5 electric share, 6 hybrid and electric share, 7 electric models. Pages: Overview, Brands, Engines, Electric."""
    t, f, g, tip, gap = c.table, c.fmts, c.g, c.tip, c.gap
    cls, fuel, make, trans, drive, decade, cyl, band = (c.d(i) for i in range(8))
    mpg, co2, cost, ev_range, ev_share, mix_share, ev_models = (c.m(i) for i in range(7))
    rows = c.rows_name
    mw = round(CW * 0.61)
    sw = CW - mw - gap
    pid, vis, nz = c.new_page("overview", c.title)
    kpi_row(c, pid, vis, nz, [rows, mpg, co2, cost, ev_range, ev_share], spark=0)
    vis.append(v_gen(pid, "mpg", "lineChart", t, {"Category": [("c", "Year")], "Series": [("c", cls)], "Y": [("m", mpg)]}, f, f"{mpg} by model year and {cls.lower()}", *g(0, 186, mw, 232), nz(), tip=tip,
                     legend=True, sort=("c", "Year", "Ascending")))
    vis.append(v_gen(pid, "fuel", "donutChart", t, {"Category": [("c", fuel)], "Y": [("m", rows)]}, f, f"{rows} by {fuel.lower()}", *g(mw + gap, 186, sw, 232), nz(), tip=tip, legend=True))
    vis.append(v_gen(pid, "share", "hundredPercentStackedColumnChart", t, {"Category": [("c", "Year")], "Series": [("c", fuel)], "Y": [("m", rows)]}, f, f"Share of models by model year and {fuel.lower()}",
                     *g(0, 430, mw, 278), nz(), tip=tip, legend=True, sort=("c", "Year", "Ascending")))
    vis.append(v_gen(pid, "cls", "clusteredBarChart", t, {"Category": [("c", cls)], "Y": [("m", mpg)]}, f, f"{mpg} by {cls.lower()}", *g(mw + gap, 430, sw, 278), nz(), tip=tip,
                     sort=("c", cls, "Descending")))

    pid, vis, nz = c.new_page("brands", "Brand by brand")
    vis.append(v_rank(pid, t, [make], [rows, mpg, co2, cost], mpg, f, f"{make}s ranked by number of models", *g(0, 64, mw, 644), nz(), rows))
    vis.append(v_treemap(pid, t, fld_col(t, make), make, rows, f[rows], f"{rows} by {make.lower()}", *g(mw + gap, 64, sw, 296), nz(), tip=tip))
    vis.append(v_gen(pid, "scatter", "scatterChart", t, {"Category": [("c", cls)], "X": [("m", co2)], "Y": [("m", mpg)], "Size": [("m", rows)]}, f, f"{mpg} against {co2.lower()} by {cls.lower()}",
                     *g(mw + gap, 372, sw, 336), nz(), tip=tip))

    pid, vis, nz = c.new_page("engines", "Under the bonnet")
    vis.append(v_gen(pid, "cyl", "columnChart", t, {"Category": [("c", cyl)], "Series": [("c", fuel)], "Y": [("m", rows)]}, f, f"{rows} by {cyl.lower()} and {fuel.lower()}", *g(0, 64, mw, 296), nz(), tip=tip,
                     legend=True, sort=("c", cyl, "Ascending")))
    vis.append(v_gen(pid, "mpgcyl", "lineChart", t, {"Category": [("c", cyl)], "Y": [("m", mpg)]}, f, f"{mpg} by {cyl.lower()}", *g(mw + gap, 64, sw, 296), nz(), tip=tip, sort=("c", cyl, "Ascending")))
    vis.append(v_gen(pid, "band", "scatterChart", t, {"Category": [("c", band)], "X": [("m", co2)], "Y": [("m", mpg)], "Size": [("m", rows)]}, f, f"{mpg} against {co2.lower()} by {band.lower()}",
                     *g(0, 372, sw, 336), nz(), tip=tip))
    vis.append(v_gen(pid, "bandcol", "clusteredColumnChart", t, {"Category": [("c", band)], "Y": [("m", mpg)]}, f, f"{mpg} by {band.lower()}", *g(sw + gap, 372, mw, 336), nz(), tip=tip,
                     sort=("c", band, "Ascending")))

    pid, vis, nz = c.new_page("electric", "The electric turn")
    vis.append(v_gen(pid, "range", "areaChart", t, {"Category": [("c", "Year")], "Y": [("m", ev_range)]}, f, f"{ev_range} by model year", *g(0, 64, mw, 296), nz(), tip=tip, sort=("c", "Year", "Ascending")))
    vis.append(v_gen(pid, "models", "clusteredColumnChart", t, {"Category": [("c", "Year")], "Y": [("m", ev_models)]}, f, f"{ev_models} by model year", *g(mw + gap, 64, sw, 296), nz(), tip=tip,
                     sort=("c", "Year", "Ascending")))
    vis.append(v_gen(pid, "gauge", "gauge", t, {"Y": [("m", ev_range)]}, f, f"{ev_range} (scale 0 to 500)", *g(0, 372, sw, 336), nz(), axis=(0, 500)))
    vis.append(v_gen(pid, "make", "clusteredBarChart", t, {"Category": [("c", make)], "Y": [("m", ev_range)]}, f, f"{ev_range} by {make.lower()}", *g(sw + gap, 372, mw, 336), nz(), tip=tip,
                     sort=("m", ev_range, "Descending")))


def lab(name):
    """A column or measure name as it reads in a title: no underscores, lower case."""
    return name.replace("_", " ").lower()


STRIPE_BANDS = ["1 Below -1.0", "2 -1.0 to -0.5", "3 -0.5 to 0", "4 0 to +0.5", "5 +0.5 to +1.0", "6 +1.0 to +2.0", "7 Above +2.0"]


def stripe_objects(table, band_col):
    """Formatting of a warming-stripes chart: full-height columns without gaps or axes, one colour for each anomaly band, from the cool end to the warm end of the style's heat colours."""
    lo, mid, hi = STYLE["heat"]
    off = flag(False)

    def mix(a, b, t):
        pa, pb = [int(a[i:i + 2], 16) for i in (1, 3, 5)], [int(b[i:i + 2], 16) for i in (1, 3, 5)]
        return "#" + "".join(f"{round(x + (y - x) * t):02X}" for x, y in zip(pa, pb))
    n = len(STRIPE_BANDS)
    colours = [mix(lo, mid, i / ((n - 1) / 2)) if i <= (n - 1) / 2 else mix(mid, hi, (i - (n - 1) / 2) / ((n - 1) / 2)) for i in range(n)]
    points = [{"properties": {"fill": solid(col)},
               "selector": {"data": [{"scopeId": {"Comparison": {"ComparisonKind": 0, "Left": fld_col(table, band_col), "Right": {"Literal": {"Value": "'" + label + "'"}}}}}]}}
              for label, col in zip(STRIPE_BANDS, colours)]
    return {"dataPoint": points, "categoryAxis": [{"properties": {"show": off, "innerPadding": num(0)}}],
            "valueAxis": [{"properties": {"show": off, "gridlineShow": off}}], "labels": [{"properties": {"show": off}}],
            "legend": [{"properties": {"show": flag(True), "position": tx("Bottom"), "labelColor": solid(MUTED)}}]}


YEAR_AXIS = {"categoryAxis": {"axisType": tx("Scalar"), "start": num(1869), "end": num(2025)}}      # one continuous axis for 157 years


def layout_climate(c):
    """A long daily record: dims = decade, period, month, season, day of month, weekday, temperature band, weather type, year band (colour of the year's stripe, see STRIPE_BANDS); needs a date (and so Year); measures: 1 average temperature,
    2 anomaly against the first period, 3 average high, 4 average low, 5 precipitation, 6 snowfall, 7 hot days, 8 days that never thawed, 9 record high, 10 record low, 11 a constant 1
    for the stripes, 12 wet days. Pages: Overview, Stripes, Calendar, Extremes, Rain and snow."""
    t, f, g, tip, gap = c.table, c.fmts, c.g, c.tip, c.gap
    decade, period, month, season, dom, wday, band, wtype, ybnd = (c.d(i) for i in range(9))
    avg, anom, hi, lo, prcp, snow, hot, frz, rec_hi, rec_lo, stripe, wet = (c.m(i) for i in range(12))
    rows = c.rows_name
    mw = round(CW * 0.61)
    sw = CW - mw - gap
    pid, vis, nz = c.new_page("overview", c.title)
    kpi_row(c, pid, vis, nz, [rows, avg, anom, rec_hi, rec_lo], h=100, spark=0)
    vis.append(v_gen(pid, "stripes", "columnChart", t, {"Category": [("c", "Year")], "Series": [("c", ybnd)], "Y": [("m", stripe)]}, f, "Warming stripes: one column a year, blue cooler and red warmer than the first period",
                     *g(0, 176, CW, 170), nz(), tip=tip, sort=("c", "Year", "Ascending"), objects=stripe_objects(t, ybnd)))
    vis.append(v_gen(pid, "line", "lineChart", t, {"Category": [("c", "Year")], "Y": [("m", avg)]}, f, f"{avg} by year", *g(0, 358, mw, 350), nz(), tip=tip, sort=("c", "Year", "Ascending"),
                     extra=YEAR_AXIS))
    vis.append(v_gen(pid, "frz", "clusteredColumnChart", t, {"Category": [("c", decade)], "Y": [("m", frz)]}, f, f"{frz} by {lab(decade)}", *g(mw + gap, 358, sw, 350), nz(), tip=tip,
                     sort=("c", decade, "Ascending")))

    pid, vis, nz = c.new_page("stripes", "Every year against the first decades")
    vis.append(v_gen(pid, "stripes", "columnChart", t, {"Category": [("c", "Year")], "Series": [("c", ybnd)], "Y": [("m", stripe)]}, f, "Warming stripes", *g(0, 64, CW, 280), nz(), tip=tip, sort=("c", "Year", "Ascending"),
                     objects=stripe_objects(t, ybnd)))
    vis.append(v_heat(pid, t, decade, month, anom, f, f"{anom}: {lab(decade)} by month", *g(0, 356, mw, 352), nz(), totals=False))
    vis.append(v_gen(pid, "anom", "lineChart", t, {"Category": [("c", "Year")], "Y": [("m", anom)]}, f, anom, *g(mw + gap, 356, sw, 352), nz(), tip=tip, sort=("c", "Year", "Ascending"), extra=YEAR_AXIS))

    pid, vis, nz = c.new_page("calendar", "A year in the life of the weather")
    vis.append(v_heat(pid, t, month, dom, avg, f, f"{avg}: month by day of the month (all years)", *g(0, 64, CW, 330), nz(), size=11, totals=False))
    vis.append(v_gen(pid, "months", "lineChart", t, {"Category": [("c", month)], "Series": [("c", period)], "Y": [("m", avg)]}, f, f"{avg} by month, one line per period", *g(0, 406, mw, 302), nz(),
                     tip=tip, legend=True, sort=("c", month, "Ascending")))
    vis.append(v_gen(pid, "season", "clusteredColumnChart", t, {"Category": [("c", season)], "Y": [("m", hi), ("m", lo)]}, f, f"{hi} and {lab(lo)} by {lab(season)}", *g(mw + gap, 406, sw, 302), nz(),
                     tip=tip, legend=True, sort=("c", season, "Ascending")))

    pid, vis, nz = c.new_page("extremes", "Hot days, frozen days and records")
    vis.append(v_gen(pid, "hot", "clusteredColumnChart", t, {"Category": [("c", decade)], "Y": [("m", hot)]}, f, f"{hot} by {lab(decade)}", *g(0, 64, mw, 296), nz(), tip=tip,
                     sort=("c", decade, "Ascending")))
    vis.append(v_gen(pid, "frz", "clusteredColumnChart", t, {"Category": [("c", decade)], "Y": [("m", frz)]}, f, f"{frz} by {lab(decade)}", *g(mw + gap, 64, sw, 296), nz(), tip=tip,
                     sort=("c", decade, "Ascending")))
    vis.append(v_matrix(pid, t, [decade], [], [rec_hi, rec_lo, hi, lo, hot, frz], f, f"Records and averages by {lab(decade)}", *g(0, 372, mw, 336), nz()))
    vis.append(v_gen(pid, "scatter", "scatterChart", t, {"Category": [("c", "Year")], "X": [("m", avg)], "Y": [("m", prcp)]}, f, f"{prcp} against {lab(avg)}, one point a year", *g(mw + gap, 372, sw, 336),
                     nz(), tip=tip, no_labels=True))

    pid, vis, nz = c.new_page("wet", "Rain and snow")
    vis.append(v_gen(pid, "prcp", "clusteredColumnChart", t, {"Category": [("c", "Year")], "Y": [("m", prcp)]}, f, f"{prcp} by year", *g(0, 64, mw, 296), nz(), tip=tip, sort=("c", "Year", "Ascending")))
    vis.append(v_gen(pid, "types", "hundredPercentStackedColumnChart", t, {"Category": [("c", decade)], "Series": [("c", wtype)], "Y": [("m", rows)]}, f, f"Share of days by {lab(wtype)} and {lab(decade)}",
                     *g(mw + gap, 64, sw, 296), nz(), tip=tip, legend=True, sort=("c", decade, "Ascending")))
    vis.append(v_gen(pid, "snow", "lineChart", t, {"Category": [("c", "Year")], "Y": [("m", snow)]}, f, f"{snow} by year", *g(0, 372, mw, 336), nz(), tip=tip, sort=("c", "Year", "Ascending"), extra=YEAR_AXIS))
    vis.append(v_gen(pid, "wet", "clusteredColumnChart", t, {"Category": [("c", decade)], "Y": [("m", wet)]}, f, f"{wet} by {lab(decade)}", *g(mw + gap, 372, sw, 336), nz(), tip=tip,
                     sort=("c", decade, "Ascending")))


def layout_pyramid(c):
    """Age and sex: dims = year, region, state, sex, age group, life stage, generation; needs columns Age, Grid_row and Grid_col; measures: 1 population, 2 women, 3 men, 4 men mirrored
    (negative, shown without a sign), 5 average age, 6 share 65 and over, 7 share under 15, 8 women per 100 men, 9 dependants per 100 of working age, 10 the state tile label,
    11 change between the first and the last year. Pages: Pyramid, Years, States, Regions, Generations."""
    t, f, g, tip, gap = c.table, c.fmts, c.g, c.tip, c.gap
    yr, reg, st, sex, ageg, life, gen = (c.d(i) for i in range(7))
    pop, women, men, men_neg, avg_age, p65, p15, wpm, dep, tile, change = (c.m(i) for i in range(11))
    mw = round(CW * 0.61)
    sw = CW - mw - gap

    def pyramid(pid, key, title, pos, z, filt=None, legend=True):
        return v_gen(pid, key, "barChart", t, {"Category": [("c", ageg)], "Y": [("m", men_neg), ("m", women)]}, f, title, *pos, z, tip=tip, legend=legend, sort=("c", ageg, "Ascending"), vfilter=filt)
    pid, vis, nz = c.new_page("overview", c.title)
    kpi_row(c, pid, vis, nz, [pop, avg_age, p65, p15, wpm], h=100, spark=0)
    vis.append(pyramid(pid, "pyramid", f"Population by {lab(ageg)}: men to the left, women to the right", g(0, 176, mw, 532), nz()))
    vis.append(v_gen(pid, "ages", "areaChart", t, {"Category": [("c", "Age")], "Y": [("m", pop)]}, f, f"{pop} by single year of age", *g(mw + gap, 176, sw, 250), nz(), tip=tip, sort=("c", "Age", "Ascending")))
    vis.append(v_gen(pid, "life", "donutChart", t, {"Category": [("c", life)], "Y": [("m", pop)]}, f, f"{pop} by {lab(life)}", *g(mw + gap, 438, sw, 270), nz(), tip=tip, legend=True))

    pid, vis, nz = c.new_page("years", "The first year and the last")
    half = (CW - gap) / 2
    vis.append(pyramid(pid, "pyr-first", "2020", g(0, 64, half, 400), nz(), filt=(yr, [2020])))
    vis.append(pyramid(pid, "pyr-last", "2024", g(half + gap, 64, half, 400), nz(), filt=(yr, [2024])))
    vis.append(v_gen(pid, "change", "clusteredColumnChart", t, {"Category": [("c", ageg)], "Y": [("m", change)]}, f, f"{change} by {lab(ageg)}", *g(0, 476, CW, 232), nz(), tip=tip,
                     sort=("c", ageg, "Ascending")))

    pid, vis, nz = c.new_page("states", "State by state")
    w1 = round(CW * 0.46)
    vis.append(v_tilemap(pid, t, "Grid_row", "Grid_col", tile, p65, f, f"{p65} by state (one square a state; the older, the stronger)", *g(0, 64, w1, 644), nz(), size=20, padding=12))
    vis.append(v_gen(pid, "scatter", "scatterChart", t, {"Category": [("c", st)], "X": [("m", p15)], "Y": [("m", p65)], "Size": [("m", pop)]}, f, f"{p65} against {lab(p15)} by {lab(st)}",
                     *g(w1 + gap, 64, CW - w1 - gap, 296), nz(), tip=tip))
    vis.append(v_gen(pid, "age", "clusteredBarChart", t, {"Category": [("c", st)], "Y": [("m", avg_age)]}, f, f"{avg_age} by {lab(st)}", *g(w1 + gap, 372, CW - w1 - gap, 336), nz(), tip=tip,
                     sort=("m", avg_age, "Descending")))

    pid, vis, nz = c.new_page("regions", "Four regions, four shapes")
    half = (CW - gap) / 2
    for i, rname in enumerate(["Northeast", "Midwest", "South", "West"]):
        vis.append(pyramid(pid, "pyr-" + rname, rname, g((i % 2) * (half + gap), 64 + (i // 2) * 328, half, 316), nz(), filt=(reg, [rname]), legend=i == 0))

    pid, vis, nz = c.new_page("generations", "Who is in each age group")
    vis.append(v_gen(pid, "gen", "barChart", t, {"Category": [("c", ageg)], "Series": [("c", gen)], "Y": [("m", pop)]}, f, f"{pop} by {lab(ageg)} and {lab(gen)}", *g(0, 64, mw, 644), nz(), tip=tip,
                     legend=True, sort=("c", ageg, "Ascending")))
    vis.append(v_gen(pid, "years", "columnChart", t, {"Category": [("c", yr)], "Series": [("c", gen)], "Y": [("m", pop)]}, f, f"{pop} by year and {lab(gen)}", *g(mw + gap, 64, sw, 296), nz(), tip=tip,
                     sort=("c", yr, "Ascending")))
    vis.append(v_treemap(pid, t, fld_col(t, reg), reg, pop, f[pop], f"{pop} by {lab(reg)}", *g(mw + gap, 372, sw, 336), nz(), tip=tip))


def layout_bump(c):
    """A ranking over time: dims = city, region, decade (text), group (regular or short stay), entry type, state; needs a Census_year-like column named in the measures; measures: 1 rank
    shown negative without a sign (so that 1 is at the top), 2 population, 3 average rank, 4 best rank, 5 decades in the top 10, 6 rank change, 7 growth, 8 cities, 9 rank score
    (11 minus rank, for the colours), 10 largest population, 11 rank as text for the table cells. Pages: Bump, Flows, Rank table, Staying power, Size."""
    t, f, g, tip, gap = c.table, c.fmts, c.g, c.tip, c.gap
    city, reg, dec, grp, entry, state = (c.d(i) for i in range(6))
    rank_axis, pop, avg_rank, best, decades, change, growth, cities, score, maxpop, rank_pos = (c.m(i) for i in range(11))
    rows = c.rows_name
    mw = round(CW * 0.61)
    sw = CW - mw - gap
    regular = ["1 Regular (6+ decades)"]
    pid, vis, nz = c.new_page("overview", c.title)
    kpi_row(c, pid, vis, nz, [cities, rows, growth, maxpop], h=90, spark=0)
    vis.append(v_gen(pid, "bump", "lineChart", t, {"Category": [("c", dec)], "Series": [("c", city)], "Y": [("m", rank_axis)]}, f, "Rank by census: 1 is the largest city (cities that stayed in the top 10 for six decades or more)",
                     *g(0, 166, CW, 342), nz(), tip=tip, legend=True, sort=("c", dec, "Ascending"), vfilter=(grp, regular),
                     extra={"lineStyles": {"strokeWidth": num(4), "showMarker": flag(True), "markerSize": num(9)}}))
    vis.append(v_gen(pid, "region", "columnChart", t, {"Category": [("c", dec)], "Series": [("c", reg)], "Y": [("m", rows)]}, f, f"Places in the top 10 by {lab(reg)}", *g(0, 520, mw, 188), nz(), tip=tip,
                     legend=True, sort=("c", dec, "Ascending")))
    vis.append(v_gen(pid, "stay", "clusteredBarChart", t, {"Category": [("c", city)], "Y": [("m", decades)]}, f, f"{decades} by {lab(city)}", *g(mw + gap, 520, sw, 188), nz(), tip=tip,
                     sort=("m", decades, "Descending")))

    pid, vis, nz = c.new_page("flows", "How the order changed")
    vis.append(v_gen(pid, "ribbon", "ribbonChart", t, {"Category": [("c", dec)], "Series": [("c", city)], "Y": [("m", pop)]}, f, "Population by census: the ribbons show who overtook whom", *g(0, 64, CW, 400),
                     nz(), tip=tip, legend=True, sort=("c", dec, "Ascending")))
    vis.append(v_gen(pid, "entry", "hundredPercentStackedColumnChart", t, {"Category": [("c", dec)], "Series": [("c", entry)], "Y": [("m", rows)]}, f, "New faces in the top 10 each census", *g(0, 476, mw, 232),
                     nz(), tip=tip, legend=True, sort=("c", dec, "Ascending")))
    vis.append(v_gen(pid, "growth", "clusteredColumnChart", t, {"Category": [("c", dec)], "Y": [("m", growth)]}, f, growth, *g(mw + gap, 476, sw, 232), nz(), tip=tip, sort=("c", dec, "Ascending")))

    pid, vis, nz = c.new_page("table", "Rank by census")
    vis.append(v_heat(pid, t, city, dec, rank_pos, f, "Rank in each census (blank: not in the top 10; the stronger the colour, the nearer the top)", *g(0, 64, CW, 644), nz(), color_measure=score, size=10, totals=False))

    pid, vis, nz = c.new_page("staying", "Who stays, who falls")
    vis.append(v_gen(pid, "change", "clusteredBarChart", t, {"Category": [("c", city)], "Y": [("m", change)]}, f, f"{change}, summed over all censuses", *g(0, 64, sw, 644), nz(), tip=tip,
                     sort=("m", change, "Descending")))
    vis.append(v_gen(pid, "scatter", "scatterChart", t, {"Category": [("c", city)], "X": [("m", decades)], "Y": [("m", avg_rank)], "Size": [("m", maxpop)]}, f, f"{avg_rank} against {lab(decades)}",
                     *g(sw + gap, 64, mw, 296), nz(), tip=tip))
    vis.append(v_gen(pid, "best", "clusteredColumnChart", t, {"Category": [("c", reg)], "Y": [("m", cities)]}, f, f"{cities} by {lab(reg)}", *g(sw + gap, 372, mw, 336), nz(), tip=tip,
                     sort=("m", cities, "Descending")))

    pid, vis, nz = c.new_page("size", "How big they got")
    vis.append(v_gen(pid, "pop", "lineChart", t, {"Category": [("c", dec)], "Series": [("c", city)], "Y": [("m", pop)]}, f, "Population by census (regular top-10 cities)", *g(0, 64, mw, 400), nz(), tip=tip,
                     legend=True, sort=("c", dec, "Ascending"), vfilter=(grp, regular)))
    vis.append(v_treemap(pid, t, fld_col(t, city), city, pop, f[pop], "The top 10 of the latest census", *g(mw + gap, 64, sw, 400), nz(), tip=tip))
    vis.append(v_rank(pid, t, [city, state], [pop, rank_pos, growth], pop, f, "The top 10 of the latest census", *g(0, 476, CW, 232), nz(), pop, vfilter=(dec, [2020])))


def layout_people(c):
    """Laureates (one row a person and prize): dims = category, decade, gender, continent, country group (top countries and Other), age band, prize sharing, era, birth country;
    needs columns Award_year, Age_at_award and Laureate_prize (unique) and Laureate; measures: 1 prizes (distinct), 2 share of women, 3 women, 4 average age, 5 median age,
    6 countries, 7 laureates per prize, 8 youngest, 9 oldest, 10 award year (average, for the dot plot). Pages: Overview, Age, Women, Places, Shared."""
    t, f, g, tip, gap = c.table, c.fmts, c.g, c.tip, c.gap
    cat, dec, gen, cont, cg, ageb, shared, era, country = (c.d(i) for i in range(9))
    prizes, wshare, women, avg_age, med_age, countries, per_prize, youngest, oldest, yr = (c.m(i) for i in range(10))
    rows = c.rows_name
    mw = round(CW * 0.61)
    sw = CW - mw - gap
    pid, vis, nz = c.new_page("overview", c.title)
    kpi_row(c, pid, vis, nz, [rows, prizes, wshare, avg_age, countries], h=100, spark=0, plain=[rows])
    vis.append(v_gen(pid, "decades", "columnChart", t, {"Category": [("c", dec)], "Series": [("c", cat)], "Y": [("m", rows)]}, f, f"{rows} by {lab(dec)} and {lab(cat)}", *g(0, 176, mw, 260), nz(), tip=tip,
                     legend=True, sort=("c", dec, "Ascending")))
    vis.append(v_gen(pid, "cont", "hundredPercentStackedAreaChart", t, {"Category": [("c", dec)], "Series": [("c", cont)], "Y": [("m", rows)]}, f, f"Share of {rows.lower()} by {lab(cont)}",
                     *g(mw + gap, 176, sw, 260), nz(), tip=tip, legend=True, sort=("c", dec, "Ascending")))
    vis.append(v_gen(pid, "country", "clusteredBarChart", t, {"Category": [("c", cg)], "Y": [("m", rows)]}, f, f"{rows} by country of birth", *g(0, 448, sw, 260), nz(), tip=tip,
                     sort=("m", rows, "Descending")))
    vis.append(v_gen(pid, "dots", "scatterChart", t, {"Category": [("c", "Laureate_prize")], "X": [("m", yr)], "Y": [("m", avg_age)], "Series": [("c", cat)]}, f, f"{avg_age} at the award: one dot a laureate",
                     *g(sw + gap, 448, mw, 260), nz(), tip=tip, legend=True, no_labels=True, ranges=((1895, 2030), (10, 100))))

    pid, vis, nz = c.new_page("age", "How old they were")
    half = (mw - gap) / 2
    vis.append(v_gen(pid, "dots", "scatterChart", t, {"Category": [("c", "Laureate_prize")], "X": [("m", yr)], "Y": [("m", avg_age)], "Series": [("c", cat)]}, f, f"{avg_age} at the award, by year of the award",
                     *g(0, 64, mw, 330), nz(), tip=tip, legend=True, no_labels=True, ranges=((1895, 2030), (10, 100))))
    vis.append(v_gen(pid, "line", "lineChart", t, {"Category": [("c", dec)], "Series": [("c", cat)], "Y": [("m", avg_age)]}, f, f"{avg_age} by {lab(dec)} and {lab(cat)}", *g(mw + gap, 64, sw, 330), nz(),
                     tip=tip, legend=True, sort=("c", dec, "Ascending")))
    vis.append(v_gen(pid, "bands", "clusteredColumnChart", t, {"Category": [("c", ageb)], "Y": [("m", rows)]}, f, f"{rows} by {lab(ageb)}", *g(0, 406, sw, 302), nz(), tip=tip, sort=("c", ageb, "Ascending")))
    vis.append(v_rank(pid, t, ["Laureate", cat], [yr, avg_age], avg_age, f, "The oldest (80 and over)", *g(sw + gap, 406, half, 302), nz(), avg_age, vfilter=(ageb, ["6 80 and over"])))
    vis.append(v_rank(pid, t, ["Laureate", cat], [yr, avg_age], avg_age, f, "The youngest (under 40)", *g(sw + gap + half + gap, 406, half, 302), nz(), avg_age, vfilter=(ageb, ["1 Under 40"]),
                      direction="Ascending"))

    pid, vis, nz = c.new_page("women", "Women among the laureates")
    vis.append(v_gen(pid, "share", "clusteredColumnChart", t, {"Category": [("c", dec)], "Y": [("m", wshare)]}, f, f"{wshare} by {lab(dec)}", *g(0, 64, mw, 296), nz(), tip=tip, sort=("c", dec, "Ascending")))
    vis.append(v_gen(pid, "bycat", "clusteredBarChart", t, {"Category": [("c", cat)], "Y": [("m", women)]}, f, f"{women} by {lab(cat)}", *g(mw + gap, 64, sw, 296), nz(), tip=tip,
                     sort=("m", women, "Descending")))
    vis.append(v_gen(pid, "mix", "hundredPercentStackedColumnChart", t, {"Category": [("c", dec)], "Series": [("c", gen)], "Y": [("m", rows)]}, f, f"Share of {rows.lower()} by {lab(gen)} and {lab(dec)}",
                     *g(0, 372, mw, 336), nz(), tip=tip, legend=True, sort=("c", dec, "Ascending")))
    vis.append(v_rank(pid, t, ["Laureate", cat], [yr, avg_age], avg_age, f, "Every woman laureate, latest first", *g(mw + gap, 372, sw, 336), nz(), yr, vfilter=(gen, ["Woman"])))

    pid, vis, nz = c.new_page("places", "Where they were born")
    vis.append(v_heat(pid, t, cg, cat, rows, f, f"{rows}: {lab(cg)} by {lab(cat)} (the stronger the colour, the more)", *g(0, 64, mw, 400), nz(), size=12, totals=False))
    vis.append(v_treemap(pid, t, fld_col(t, country), country, rows, f[rows], f"{rows} by {lab(country)}", *g(mw + gap, 64, sw, 400), nz(), tip=tip))
    vis.append(v_gen(pid, "cont", "clusteredColumnChart", t, {"Category": [("c", cont)], "Y": [("m", rows)]}, f, f"{rows} by {lab(cont)}", *g(0, 476, sw, 232), nz(), tip=tip, sort=("m", rows, "Descending")))
    vis.append(v_gen(pid, "era", "hundredPercentStackedColumnChart", t, {"Category": [("c", era)], "Series": [("c", cg)], "Y": [("m", rows)]}, f, f"Share of {rows.lower()} by {lab(cg)} and {lab(era)}",
                     *g(sw + gap, 476, mw, 232), nz(), tip=tip, legend=True, sort=("c", era, "Ascending")))

    pid, vis, nz = c.new_page("shared", "Sole winners and shared prizes")
    vis.append(v_gen(pid, "shared", "hundredPercentStackedColumnChart", t, {"Category": [("c", dec)], "Series": [("c", shared)], "Y": [("m", rows)]}, f, f"Share of {rows.lower()} by {lab(shared)} and {lab(dec)}",
                     *g(0, 64, mw, 296), nz(), tip=tip, legend=True, sort=("c", dec, "Ascending")))
    vis.append(v_gen(pid, "per", "clusteredColumnChart", t, {"Category": [("c", dec)], "Y": [("m", per_prize)]}, f, f"{per_prize} by {lab(dec)}", *g(mw + gap, 64, sw, 296), nz(), tip=tip,
                     sort=("c", dec, "Ascending")))
    vis.append(v_gen(pid, "bycat", "barChart", t, {"Category": [("c", cat)], "Series": [("c", shared)], "Y": [("m", rows)]}, f, f"{rows} by {lab(cat)} and {lab(shared)}", *g(0, 372, mw, 336), nz(), tip=tip,
                     legend=True, sort=("c", cat, "Ascending")))
    vis.append(v_gen(pid, "percat", "clusteredBarChart", t, {"Category": [("c", cat)], "Y": [("m", per_prize)]}, f, f"{per_prize} by {lab(cat)}", *g(mw + gap, 372, sw, 336), nz(), tip=tip,
                     sort=("m", per_prize, "Descending")))


CYCLES = [f"Cycle {i:02d}" for i in range(1, 26)]


def layout_cycle(c):
    """A long monthly series with cycles: dims = cycle (text like Cycle 07), decade, century, calendar month, year in the cycle, activity band, data quality; needs a date (so Year and
    Month) and a column Months_since_minimum; measures: 1 average monthly number, 2 smoothed average, 3 highest month, 4 peak of the smoothed number, 5 cycles, 6 cycle length in years,
    7 share of quiet months, 8 quiet months. Pages: Overview, Cycles, Heat map, Strength, Quiet times."""
    t, f, g, tip, gap = c.table, c.fmts, c.g, c.tip, c.gap
    cyc, dec, cen, mon, cyr, act, qual = (c.d(i) for i in range(7))
    avg, smooth, top, peak, cycles, length, qshare, qcount = (c.m(i) for i in range(8))
    rows = c.rows_name
    mw = round(CW * 0.61)
    sw = CW - mw - gap
    half = (CW - gap) / 2
    scalar = {"categoryAxis": {"axisType": tx("Scalar"), "start": num(0), "end": num(160)}}
    pid, vis, nz = c.new_page("overview", c.title)
    kpi_row(c, pid, vis, nz, [rows, avg, top, cycles, qshare], h=100, spark=0, plain=[rows])
    vis.append(v_gen(pid, "months", "lineChart", t, {"Category": [("c", "Month")], "Y": [("m", avg), ("m", smooth)]}, f, f"{avg} and {lab(smooth)}, month by month", *g(0, 176, CW, 270), nz(), tip=tip,
                     legend=True, sort=("c", "Month", "Ascending")))
    vis.append(v_gen(pid, "peaks", "clusteredColumnChart", t, {"Category": [("c", cyc)], "Y": [("m", peak)]}, f, f"{peak} in each cycle", *g(0, 458, mw, 250), nz(), tip=tip, sort=("c", cyc, "Ascending"),
                     vfilter=(cyc, CYCLES)))
    vis.append(v_gen(pid, "levels", "donutChart", t, {"Category": [("c", act)], "Y": [("m", rows)]}, f, f"{rows} by {lab(act)}", *g(mw + gap, 458, sw, 250), nz(), tip=tip, legend=True, units=1))

    pid, vis, nz = c.new_page("cycles", "Every cycle from its minimum")
    vis.append(v_gen(pid, "early", "lineChart", t, {"Category": [("c", "Months_since_minimum")], "Series": [("c", cyc)], "Y": [("m", smooth)]}, f, f"Cycles 1 to 12: {lab(smooth)}, months since the minimum",
                     *g(0, 64, half, 330), nz(), tip=tip, legend=True, sort=("c", "Months_since_minimum", "Ascending"), vfilter=(cyc, CYCLES[:12]), extra=scalar))
    vis.append(v_gen(pid, "late", "lineChart", t, {"Category": [("c", "Months_since_minimum")], "Series": [("c", cyc)], "Y": [("m", smooth)]}, f, f"Cycles 13 to 25: {lab(smooth)}, months since the minimum",
                     *g(half + gap, 64, half, 330), nz(), tip=tip, legend=True, sort=("c", "Months_since_minimum", "Ascending"), vfilter=(cyc, CYCLES[12:]), extra=scalar))
    vis.append(v_gen(pid, "profile", "areaChart", t, {"Category": [("c", cyr)], "Y": [("m", smooth)]}, f, f"The typical cycle: {lab(smooth)} by year of the cycle", *g(0, 406, mw, 302), nz(), tip=tip,
                     sort=("c", cyr, "Ascending"), vfilter=(cyr, list(range(1, 14)))))
    vis.append(v_gen(pid, "length", "clusteredColumnChart", t, {"Category": [("c", cyc)], "Y": [("m", length)]}, f, f"{length} of each completed cycle", *g(mw + gap, 406, sw, 302), nz(), tip=tip,
                     sort=("c", cyc, "Ascending"), vfilter=(cyc, CYCLES[:24])))

    pid, vis, nz = c.new_page("heat", "Cycle by cycle, year by year")
    vis.append(v_heat(pid, t, cyc, cyr, avg, f, f"{avg} by cycle and year of the cycle (the stronger the colour, the more spots)", *g(0, 64, CW, 644), nz(), size=12, totals=False))

    pid, vis, nz = c.new_page("strength", "Strong cycles and weak ones")
    vis.append(v_gen(pid, "scatter", "scatterChart", t, {"Category": [("c", cyc)], "X": [("m", length)], "Y": [("m", peak)], "Size": [("m", rows)]}, f, f"{peak} against {lab(length)}, a dot a cycle",
                     *g(0, 64, mw, 330), nz(), tip=tip, vfilter=(cyc, CYCLES[:24])))
    vis.append(v_rank(pid, t, [cyc], [peak, length, avg, qshare], peak, f, "The cycles by peak", *g(mw + gap, 64, sw, 330), nz(), peak, vfilter=(cyc, CYCLES)))
    vis.append(v_gen(pid, "top", "clusteredBarChart", t, {"Category": [("c", cyc)], "Y": [("m", top)]}, f, f"{top} in each cycle", *g(0, 406, mw, 302), nz(), tip=tip, sort=("c", cyc, "Ascending"),
                     vfilter=(cyc, CYCLES)))
    vis.append(v_gen(pid, "cent", "clusteredColumnChart", t, {"Category": [("c", cen)], "Y": [("m", avg)]}, f, f"{avg} by {lab(cen)}", *g(mw + gap, 406, sw, 302), nz(), tip=tip, sort=("c", cen, "Ascending")))

    pid, vis, nz = c.new_page("quiet", "Quiet times and busy times")
    vis.append(v_gen(pid, "bands", "hundredPercentStackedColumnChart", t, {"Category": [("c", dec)], "Series": [("c", act)], "Y": [("m", rows)]}, f, f"Share of months by {lab(act)} and {lab(dec)}",
                     *g(0, 64, CW, 300), nz(), tip=tip, legend=True, sort=("c", dec, "Ascending")))
    vis.append(v_gen(pid, "quiet", "clusteredColumnChart", t, {"Category": [("c", dec)], "Y": [("m", qcount)]}, f, f"{qcount} by {lab(dec)}", *g(0, 376, mw, 332), nz(), tip=tip, sort=("c", dec, "Ascending")))
    vis.append(v_gen(pid, "month", "clusteredColumnChart", t, {"Category": [("c", mon)], "Y": [("m", avg)]}, f, f"{avg} by calendar month (the sun has no seasons)", *g(mw + gap, 376, sw, 332), nz(), tip=tip,
                     sort=("c", mon, "Ascending")))


def layout_quake(c):
    """Earthquakes (one row a quake): dims = magnitude band, magnitude step (text, 6.0 to 9.5), depth class, depth band, region group, region, decade, hemisphere, magnitude type; needs a date
    (so Year) and columns Cell (a 5-degree cell), Cell_lat and Cell_lon; measures: 1 average magnitude, 2 largest magnitude, 3 average depth, 4 quakes of magnitude 7 and over,
    5 of 8 and over, 6 share deeper than 300 km, 7 share shallower than 70 km, 8 cell longitude, 9 cell latitude, 10 regions, 11 base-10 logarithm of the number of quakes. Pages: Map, Magnitude, Depth, Regions, Time."""
    t, f, g, tip, gap = c.table, c.fmts, c.g, c.tip, c.gap
    magb, mags, depc, depb, regg, reg, dec, hemi, mtype = (c.d(i) for i in range(9))
    avg_mag, max_mag, avg_depth, m7, m8, deep, shallow, cell_lon, cell_lat, regions, log_count = (c.m(i) for i in range(11))
    rows = c.rows_name
    mw = round(CW * 0.61)
    sw = CW - mw - gap
    year_axis = {"categoryAxis": {"axisType": tx("Scalar"), "start": num(1950), "end": num(2025)}, "valueAxis": {"start": num(0)}}
    pid, vis, nz = c.new_page("overview", c.title)
    kpi_row(c, pid, vis, nz, [rows, m7, max_mag, avg_depth, regions], h=90, spark=0, plain=[rows, m7])
    vis.append(v_gen(pid, "map", "scatterChart", t, {"Category": [("c", "Cell")], "X": [("m", cell_lon)], "Y": [("m", cell_lat)], "Size": [("m", rows)], "Series": [("c", depc)]}, f,
                     f"Where they happen: a dot for every 5-degree cell (size: {rows.lower()}; colour: {lab(depc)})", *g(0, 166, CW, 542), nz(), tip=tip, legend=True, no_labels=True,
                     ranges=((-180, 180), (-90, 90)),
                     colors=(depc, {"1 Shallow (under 70 km)": STYLE["series"][1], "2 Intermediate (70-300 km)": STYLE["series"][2], "3 Deep (over 300 km)": STYLE["series"][0]})))

    pid, vis, nz = c.new_page("magnitude", "Big ones are rare")
    vis.append(v_gen(pid, "funnel", "funnel", t, {"Category": [("c", magb)], "Y": [("m", rows)]}, f, f"{rows} by {lab(magb)}", *g(0, 64, sw, 330), nz(), tip=tip, sort=("c", magb, "Ascending"), units=1))
    vis.append(v_gen(pid, "log", "lineChart", t, {"Category": [("c", mags)], "Y": [("m", log_count)]}, f, f"{log_count} by {lab(mags)}: a straight line, about 10 times fewer for every whole point", *g(sw + gap, 64, mw, 330),
                     nz(), tip=tip, sort=("c", mags, "Ascending"), extra={"lineStyles": {"strokeWidth": num(4), "showMarker": flag(True), "markerSize": num(8)}, "valueAxis": {"show": flag(True)}}))
    vis.append(v_gen(pid, "m7", "lineChart", t, {"Category": [("c", "Year")], "Y": [("m", m7)]}, f, f"{m7} by year", *g(0, 406, mw, 302), nz(), tip=tip, sort=("c", "Year", "Ascending"), extra=year_axis))
    vis.append(v_gen(pid, "dec", "clusteredColumnChart", t, {"Category": [("c", dec)], "Y": [("m", rows)]}, f, f"{rows} by {lab(dec)}", *g(mw + gap, 406, sw, 302), nz(), tip=tip, sort=("c", dec, "Ascending"), units=1))

    pid, vis, nz = c.new_page("depth", "Shallow, deep and in between")
    vis.append(v_gen(pid, "band", "clusteredColumnChart", t, {"Category": [("c", depb)], "Y": [("m", rows)]}, f, f"{rows} by {lab(depb)}", *g(0, 64, mw, 330), nz(), tip=tip, sort=("c", depb, "Ascending"), units=1))
    vis.append(v_gen(pid, "class", "donutChart", t, {"Category": [("c", depc)], "Y": [("m", rows)]}, f, f"{rows} by {lab(depc)}", *g(mw + gap, 64, sw, 330), nz(), tip=tip, legend=True, units=1))
    vis.append(v_gen(pid, "regdepth", "hundredPercentStackedBarChart", t, {"Category": [("c", regg)], "Series": [("c", depc)], "Y": [("m", rows)]}, f, f"Share of {rows.lower()} by {lab(regg)} and {lab(depc)}",
                     *g(0, 406, mw, 302), nz(), tip=tip, legend=True, sort=("c", regg, "Ascending")))
    vis.append(v_gen(pid, "avgdepth", "clusteredBarChart", t, {"Category": [("c", regg)], "Y": [("m", avg_depth)]}, f, f"{avg_depth} by {lab(regg)}", *g(mw + gap, 406, sw, 302), nz(), tip=tip,
                     sort=("m", avg_depth, "Descending")))

    pid, vis, nz = c.new_page("regions", "The usual places")
    vis.append(v_gen(pid, "regions", "clusteredBarChart", t, {"Category": [("c", regg)], "Y": [("m", rows)]}, f, f"{rows} by {lab(regg)}", *g(0, 64, sw, 644), nz(), tip=tip, sort=("m", rows, "Descending"), units=1))
    vis.append(v_treemap(pid, t, fld_col(t, reg), reg, rows, f[rows], f"{rows} by {lab(reg)}", *g(sw + gap, 64, mw, 330), nz(), tip=tip))
    vis.append(v_rank(pid, t, [regg], [rows, max_mag, avg_depth, m7], rows, f, "Regions compared", *g(sw + gap, 406, mw, 302), nz(), rows))

    pid, vis, nz = c.new_page("time", "Decade by decade")
    vis.append(v_heat(pid, t, dec, magb, rows, f, f"{rows}: {lab(dec)} by {lab(magb)}", *g(0, 64, sw, 330), nz(), size=12, totals=False))
    vis.append(v_gen(pid, "years", "areaChart", t, {"Category": [("c", "Year")], "Y": [("m", rows)]}, f, f"{rows} by year", *g(sw + gap, 64, mw, 330), nz(), tip=tip, sort=("c", "Year", "Ascending"), extra=year_axis))
    vis.append(v_rank(pid, t, ["Date", "Place", regg], [max_mag, avg_depth], max_mag, f, "The great earthquakes (magnitude 8 and over), strongest first", *g(0, 406, CW, 302), nz(), max_mag,
                      vfilter=(magb, ["5 8.0 and over"])))


LAYOUT_PAGES = {
    "pulse": [("overview", "Overview"), ("rhythm", "Rhythm"), ("weather", "Weather"), ("seasons", "Seasons"), ("details", "Details"), ("about", "About")],
    "flow": [("overview", "Overview"), ("patients", "Patients"), ("stay", "Stay"), ("treatment", "Treatment"), ("details", "Details"), ("about", "About")],
    "lab": [("overview", "Overview"), ("chemistry", "Chemistry"), ("compare", "Compare"), ("bands", "Bands"), ("details", "Details"), ("about", "About")],
    "board": [("overview", "Overview"), ("teams", "Teams"), ("drivers", "Drivers"), ("trend", "Trend"), ("details", "Details"), ("about", "About")],
    "story": [("overview", "Overview"), ("study", "Study"), ("home", "Home"), ("habits", "Habits"), ("details", "Details"), ("about", "About")],
    "mix": [("overview", "Overview"), ("bands", "Bands"), ("seasons", "Seasons"), ("wind", "Wind"), ("details", "Details"), ("about", "About")],
    "atlas": [("overview", "Map"), ("states", "States"), ("strength", "Strength"), ("seasons", "Seasons"), ("paths", "Dots"), ("details", "Details"), ("about", "About")],
    "macro": [("overview", "Overview"), ("rates", "Rates"), ("jobs", "Inflation"), ("cycles", "Cycles"), ("details", "Details"), ("about", "About")],
    "catalog": [("overview", "Overview"), ("brands", "Brands"), ("engines", "Engines"), ("electric", "Electric"), ("details", "Details"), ("about", "About")],
    "climate": [("overview", "Overview"), ("stripes", "Stripes"), ("calendar", "Calendar"), ("extremes", "Extremes"), ("wet", "Rain and snow"), ("details", "Details"), ("about", "About")],
    "pyramid": [("overview", "Pyramid"), ("years", "Years"), ("states", "States"), ("regions", "Regions"), ("generations", "Generations"), ("details", "Details"), ("about", "About")],
    "people": [("overview", "Overview"), ("age", "Age"), ("women", "Women"), ("places", "Places"), ("shared", "Shared"), ("details", "Details"), ("about", "About")],
    "cycle": [("overview", "Overview"), ("cycles", "Cycles"), ("heat", "Heat map"), ("strength", "Strength"), ("quiet", "Quiet times"), ("details", "Details"), ("about", "About")],
    "quake": [("overview", "Map"), ("magnitude", "Magnitude"), ("depth", "Depth"), ("regions", "Regions"), ("time", "Time"), ("details", "Details"), ("about", "About")],
    "bump": [("overview", "Bump chart"), ("flows", "Flows"), ("table", "Rank table"), ("staying", "Staying power"), ("size", "Size"), ("details", "Details"), ("about", "About")],
}
LAYOUTS = {"pulse": layout_pulse, "flow": layout_flow, "lab": layout_lab, "board": layout_board, "story": layout_story, "mix": layout_mix, "atlas": layout_atlas, "macro": layout_macro, "catalog": layout_catalog, "climate": layout_climate, "pyramid": layout_pyramid, "bump": layout_bump,
           "people": layout_people, "cycle": layout_cycle, "quake": layout_quake}


def theme_json():
    call = {"INK": INK, "TEAL": TEAL, "ACCENT": ACCENT}[STYLE["callout"]]
    return {"name": f"pbigen {STYLE_NAME}", "dataColors": STYLE["series"], "background": CARD, "foreground": INK, "tableAccent": TEAL,
            "good": TEAL, "neutral": MUTED, "bad": ACCENT, "maximum": TEAL, "center": MUTED, "minimum": ACCENT,
            "textClasses": {"callout": {"fontSize": 26, "fontFace": STYLE["font"] if STYLE["font"] != "Segoe UI" else "Segoe UI Semibold", "color": call},
                            "title": {"fontSize": 12, "fontFace": STYLE["font"] if STYLE["font"] != "Segoe UI" else "Segoe UI Semibold", "color": INK},
                            "header": {"fontSize": 12, "fontFace": STYLE["font"] if STYLE["font"] != "Segoe UI" else "Segoe UI Semibold", "color": INK},
                            "label": {"fontSize": 10, "fontFace": STYLE["font"], "color": MUTED}},
            "visualStyles": {"page": {"*": {"background": [{"color": {"solid": {"color": BG}}, "transparency": 0}],
                                            "outspace": [{"color": {"solid": {"color": NAVY if STYLE["dark"] else "#E5E7EB"}}, "transparency": 0}]}}}}


NAV_W, CX, CW = 0, 16, 1248                           # content column on the 1280 grid, set by apply_style
apply_style("clean")


def build_report(table, title, date, dims, metrics, measures, cols, about=None, source=None, layout="classic", insights=None):
    """Return ({relative path: json}, theme dict) for the PBIR definition folder."""
    rows_name = measures[0][0]
    customs = [m[0] for m in measures if m[3] == "custom"]
    heads = customs + [headline(m, measures) for m in metrics]
    fmts = {m[0]: m[2] for m in measures}
    m1 = heads[0] if heads else rows_name
    m2 = heads[1] if len(heads) > 1 else rows_name
    m3 = heads[2] if len(heads) > 2 else m1
    int_dims = {d["name"] for d in dims if d["kind"] == "int"}
    col_dims = int_dims | {d["name"] for d in dims if 7 <= d["distinct"] <= 15 and d["avg_len"] <= 10}

    keys = ["overview"] + (["breakdown"] if dims else []) + (["compare"] if dims and (len(heads) >= 2 or len(dims) >= 2) else []) \
        + (["trends"] if date else []) + (["matrix"] if dims else []) + ["details", "about"]
    labels = {"overview": "Overview", "breakdown": "Breakdown", "compare": "Compare", "trends": "Trends", "matrix": "Matrix", "details": "Details", "about": "About"}
    if layout != "classic":
        keys, labels = [k for k, _ in LAYOUT_PAGES[layout]], dict(LAYOUT_PAGES[layout])
        if layout in ("pulse", "mix", "macro", "climate", "cycle", "quake") and not date:
            sys.exit(f"the {layout} layout needs a date column")
    pid_of = {k: oid("page", k) for k in keys}
    tip_pid = oid("page", "tooltip")

    sl = []
    if date:
        sl.append((date["name"], date["name"], False))
    for d in dims[:3 if date else 4]:
        sl.append((d["name"], d["name"], True))
    sl = sl[:4]
    strip = [d["name"] for d in dims[:4]] if STYLE["top_slicers"] else []          # dropdowns in the header
    side = [d for d in dims[:3]] if STYLE["nav"] == "split" else []                  # list slicers in the left panel
    top, bot = STYLE["top"], STYLE["bottom"]                  # where the content starts and ends on the 720 grid
    ys, hs = (bot - top) / 644, (bot - top) / 644

    pages = []                    # (id, display name, visuals, page extras)

    def dim_chart(page, dim, meas, pos, z, ttl=None):
        kind = "clusteredColumnChart" if dim["name"] in col_dims else "clusteredBarChart"
        return v_chart(page, kind, table, fld_col(table, dim["name"]), dim["name"], meas, fmts[meas], ttl or f"{meas} by {dim['name']}", *pos, z,
                       sort_by_measure=dim["name"] not in int_dims, tip=tip_pid)

    def new_page(key, heading):
        pid = pid_of[key]
        counter = [0]

        def nz():
            counter[0] += 10
            return counter[0]

        if STYLE["nav"] in ("left", "right"):
            x0 = 0 if STYLE["nav"] == "left" else 1280 - NAV_W
            vis = [v_shape(pid, "nav-bg", x0, 0, NAV_W, 720, 1, NAVY),
                   v_textbox(pid, "nav-title", x0 + 14, 18, NAV_W - 24, 56, 2, [(title, {"fontSize": "13pt", "fontWeight": "bold", "color": "#FFFFFF"})])]
            solid_active = STYLE_NAME in ("violet", "bordeaux")
            for i, k in enumerate(keys):
                active = k == key
                vis += v_button(pid, f"nav-{k}", x0 + 12, 96 + i * 44, NAV_W - 24, 36, 10 + 2 * i, labels[k], ("page", pid_of[k]),
                                fill=ACCENT if solid_active and active else (TEAL if active else None),
                                text_color=STYLE["nav_active_text"] if active and solid_active else ("#FFFFFF" if active else STYLE["nav_text"]), bold=active)
            vis.append(v_textbox(pid, "title", CX, 14, CW, 40, nz() + 100, [(heading, {"fontSize": "20pt", "fontWeight": "bold", "color": INK})], card=STYLE["title_card"]))
        elif STYLE["nav"] == "bottom":         # slim header on top, the page tabs along the bottom edge
            vis = [v_shape(pid, "nav-bg", 0, 0, 1280, 44, 1, NAVY), v_shape(pid, "nav-bg2", 0, 676, 1280, 44, 1, NAVY),
                   v_shape(pid, "nav-rule", 0, 43, 1280, 2, 3, TEAL),
                   v_textbox(pid, "nav-title", 14, 5, 700, 36, 2, [(title, {"fontSize": "14pt", "fontWeight": "bold", "color": TEAL})]),
                   v_textbox(pid, "nav-sub", 760, 9, 506, 30, 4, [(heading if key != "overview" else " ", {"fontSize": "11pt", "color": MUTED})])]
            bw = 128
            for i, k in enumerate(keys):
                active = k == key
                vis += v_button(pid, f"nav-{k}", 16 + i * (bw + 8), 684, bw, 28, 10 + 2 * i, labels[k], ("page", pid_of[k]),
                                fill=TEAL if active else CARD, text_color=STYLE["nav_active_text"] if active else STYLE["nav_text"], bold=active, size=10)
        elif STYLE["nav"] == "float":           # a floating rounded side card with the title and the pages
            vis = [v_shape(pid, "nav-bg", 12, 12, NAV_W, 696, 1, NAVY, radius=26),
                   v_textbox(pid, "nav-title", 26, 30, NAV_W - 28, 100, 2, [(title, {"fontSize": "13pt", "fontWeight": "bold", "color": INK})])]
            for i, k in enumerate(keys):
                active = k == key
                vis += v_button(pid, f"nav-{k}", 24, 156 + i * 46, NAV_W - 24, 38, 10 + 2 * i, labels[k], ("page", pid_of[k]),
                                fill=CARD if active else None, text_color=INK if active else STYLE["nav_text"], bold=active)
            vis.append(v_textbox(pid, "title", CX, 14, CW, 44, nz() + 100, [(heading, {"fontSize": "20pt", "fontWeight": "bold", "color": INK})]))
        elif STYLE["nav"] == "rail":            # a slim numbered rail, the report title and the page heading above the content
            vis = [v_shape(pid, "nav-bg", 0, 0, NAV_W, 720, 1, NAVY)]
            for i, k in enumerate(keys):
                active = k == key
                vis += v_button(pid, f"nav-{k}", 8, 20 + i * 48, NAV_W - 16, 38, 10 + 2 * i, f"{i + 1}  {labels[k]}", ("page", pid_of[k]),
                                fill=TEAL if active else None, text_color=STYLE["nav_active_text"] if active else STYLE["nav_text"], bold=active, size=10)
            vis.append(v_textbox(pid, "nav-title", CX, 6, CW, 24, 2, [(title, {"fontSize": "10pt", "color": MUTED})]))
            vis.append(v_textbox(pid, "title", CX, 26, CW, 42, nz() + 100, [(heading, {"fontSize": "20pt", "fontWeight": "bold", "color": INK})]))
        elif STYLE["nav"] == "dock":            # a big italic title on top and a floating dock with the pages at the bottom
            bw = 104
            total = len(keys) * (bw + 6) - 6
            x0 = (1280 - total) / 2
            vis = [v_textbox(pid, "nav-title", 16, 6, 1100, 48, 2, [(title if key == "overview" else heading, {"fontSize": "22pt", "fontWeight": "bold", "fontStyle": "italic", "color": TEAL})]),
                   v_shape(pid, "nav-dock", x0 - 14, 672, total + 28, 40, 1, NAVY, radius=20)]
            for i, k in enumerate(keys):
                active = k == key
                vis += v_button(pid, f"nav-{k}", x0 + i * (bw + 6), 676, bw, 32, 10 + 2 * i, labels[k], ("page", pid_of[k]),
                                fill=TEAL if active else None, text_color=STYLE["nav_active_text"] if active else STYLE["nav_text"], bold=active, size=10)
        elif STYLE["nav"] == "split":           # a left panel with the filters, the page tabs across the top of the content
            vis = [v_shape(pid, "nav-bg", 0, 0, NAV_W, 720, 1, NAVY),
                   v_textbox(pid, "nav-title", 14, 14, NAV_W - 24, 64, 2, [(title, {"fontSize": "13pt", "fontWeight": "bold", "color": "#FFFFFF"})])]
            y0 = 92
            for i, d in enumerate(side):
                hh = min(40 + 24 * (d["distinct"] + (1 if d["missing_pct"] > 0 else 0)), 230)
                vis.append(v_slicer(pid, table, d["name"], d["name"], 12, y0, NAV_W - 24, hh, 40 + i, dropdown=False))
                y0 += hh + 8
            bw = (CW - 5 * 6) / len(keys)
            for i, k in enumerate(keys):
                active = k == key
                vis += v_button(pid, f"nav-{k}", CX + i * (bw + 6), 10, bw, 30, 10 + 2 * i, labels[k], ("page", pid_of[k]),
                                fill=TEAL if active else CARD, text_color=STYLE["nav_active_text"] if active else STYLE["nav_text"], bold=active, size=10)
        elif STYLE["nav"] == "mini":            # a slim rail with numbered buttons and a tiny caption under each
            vis = [v_shape(pid, "nav-bg", 0, 0, NAV_W, 720, 1, NAVY)]
            for i, k in enumerate(keys):
                active = k == key
                vis += v_button(pid, f"nav-{k}", 9, 14 + i * 58, NAV_W - 18, 30, 10 + 2 * i, str(i + 1), ("page", pid_of[k]),
                                fill=TEAL if active else None, text_color=STYLE["nav_active_text"] if active else STYLE["nav_text"], bold=active, size=13)
                vis.append(v_textbox(pid, f"nav-cap-{k}", 2, 46 + i * 58, NAV_W - 4, 16, 11 + 2 * i, [(labels[k], {"fontSize": "7pt", "color": "#FFFFFF" if active else STYLE["nav_text"], "_align": "center"})]))
            vis.append(v_textbox(pid, "nav-title", CX, 6, CW, 24, 2, [(title, {"fontSize": "10pt", "color": MUTED})]))
            vis.append(v_textbox(pid, "title", CX, 26, CW, 42, nz() + 100, [(heading, {"fontSize": "20pt", "fontWeight": "bold", "color": INK})]))
        elif STYLE["header"] == "plaque":       # a serif title on the left, the pages in small capitals on the right, a double gold rule below
            vis = [v_textbox(pid, "nav-title", 12, 8, 560, 46, 2, [(title, {"fontSize": "20pt", "fontWeight": "bold", "color": TEAL, "fontFamily": STYLE["font"]})]),
                   v_shape(pid, "nav-r1", 16, 58, 1248, 2, 3, TEAL), v_shape(pid, "nav-r2", 16, 63, 1248, 1, 3, EDGE)]
            bw = 96
            for i, k in enumerate(keys):
                active = k == key
                x0 = 1264 - (len(keys) - i) * (bw + 2) + 2
                vis += v_button(pid, f"nav-{k}", x0, 22, bw, 26, 10 + 2 * i, labels[k].upper(), ("page", pid_of[k]), fill=None,
                                text_color=STYLE["nav_active_text"] if active else STYLE["nav_text"], bold=active, size=9)
                if active:
                    vis.append(v_shape(pid, "nav-under", x0 + 14, 47, bw - 28, 2, 50, TEAL))
        elif STYLE["header"] == "orbit":        # a glowing sun and the title, then an orbit line with one dot for every page and its name below
            n = len(keys)
            step = 1248 / n
            vis = [v_shape(pid, "nav-halo", 14, 6, 52, 52, 1, "#4A2A0E", shape="oval"), v_shape(pid, "nav-sun", 23, 15, 34, 34, 2, TEAL, shape="oval"),
                   v_textbox(pid, "nav-title", 76, 10, 1100, 44, 3, [(title, {"fontSize": "20pt", "fontWeight": "bold", "color": INK})]),
                   v_shape(pid, "nav-orbit", 16, 68, 1248, 3, 4, STYLE.get("orbit", EDGE))]
            for i, k in enumerate(keys):
                active = k == key
                cx = 16 + (i + 0.5) * step
                d = 18 if active else 10
                vis.append(v_shape(pid, f"nav-dot-{k}", cx - d / 2, 69.5 - d / 2, d, d, 20 + i, ACCENT if active else MUTED, shape="oval"))
                vis += v_button(pid, f"nav-{k}", cx - step / 2 + 2, 80, step - 4, 22, 30 + 2 * i, labels[k], ("page", pid_of[k]), fill=None,
                                text_color=STYLE["nav_active_text"] if active else STYLE["nav_text"], bold=active, size=10)
        elif STYLE["header"] == "center":       # a centred light title, centred tabs with an underline and a hairline
            vis = [v_textbox(pid, "nav-title", 16, 8, 1248, 46, 2, [(title, {"fontSize": "24pt", "color": INK, "fontFamily": "Segoe UI Light", "_align": "center"})]),
                   v_shape(pid, "nav-rule", 16, 88, 1248, 1, 3, EDGE)]
            bw = 128
            x0 = (1280 - (len(keys) * (bw + 4) - 4)) / 2
            for i, k in enumerate(keys):
                active = k == key
                vis += v_button(pid, f"nav-{k}", x0 + i * (bw + 4), 58, bw, 26, 10 + 2 * i, labels[k], ("page", pid_of[k]),
                                fill=None, text_color=INK if active else MUTED, bold=active, size=11)
                if active:
                    vis.append(v_shape(pid, "nav-under", x0 + i * (bw + 4) + 16, 85, bw - 32, 3, 50, TEAL))
        elif STYLE["header"] == "masthead":     # a newspaper masthead: a serif title between double rules, small-caps sections
            vis = [v_shape(pid, "nav-r1", 16, 6, 1248, 4, 3, INK), v_shape(pid, "nav-r2", 16, 12, 1248, 1, 3, INK),
                   v_textbox(pid, "nav-title", 16, 14, 1248, 58, 2, [(title, {"fontSize": "28pt", "fontWeight": "bold", "color": INK, "_align": "center"})]),
                   v_shape(pid, "nav-r3", 16, 78, 1248, 1, 3, INK)]
            bw = 150
            x0 = (1280 - (len(keys) * (bw + 4) - 4)) / 2
            for i, k in enumerate(keys):
                active = k == key
                vis += v_button(pid, f"nav-{k}", x0 + i * (bw + 4), 84, bw, 24, 10 + 2 * i, labels[k].upper(), ("page", pid_of[k]),
                                fill=None, text_color=TEAL if active else INK, bold=active, size=10)
            vis += [v_shape(pid, "nav-r4", 16, 112, 1248, 1, 3, INK), v_shape(pid, "nav-r5", 16, 116, 1248, 3, 3, INK)]
        elif STYLE["header"] == "terminal":     # a thin black bar with an amber title and function-key tabs
            vis = [v_shape(pid, "nav-bg", 0, 0, 1280, 28, 1, NAVY), v_shape(pid, "nav-rule", 0, 28, 1280, 2, 3, TEAL),
                   v_textbox(pid, "nav-title", 8, 3, 620, 22, 2, [(title.upper(), {"fontSize": "10pt", "fontWeight": "bold", "color": TEAL})])]
            bw = 108
            for i, k in enumerate(keys):
                active = k == key
                vis += v_button(pid, f"nav-{k}", 1280 - 8 - (len(keys) - i) * (bw + 4) + 4, 3, bw, 22, 10 + 2 * i, f"F{i + 1} {labels[k].upper()}", ("page", pid_of[k]),
                                fill=TEAL if active else None, text_color=STYLE["nav_active_text"] if active else STYLE["nav_text"], bold=active, size=8)
        elif STYLE["header"] == "card":         # a floating header card with the title and round pills
            vis = [v_shape(pid, "nav-bg", 16, 10, 1248, 54, 1, CARD, border=True, radius=14),
                   v_textbox(pid, "nav-title", 30, 18, 520, 40, 2, [(title, {"fontSize": "15pt", "fontWeight": "bold", "color": INK})])]
            bw = 96
            for i, k in enumerate(keys):
                active = k == key
                vis += v_button(pid, f"nav-{k}", 1264 - 12 - (len(keys) - i) * (bw + 6) + 6, 22, bw, 30, 10 + 2 * i, labels[k], ("page", pid_of[k]),
                                fill=TEAL if active else None, text_color=STYLE["nav_active_text"] if active else STYLE["nav_text"], bold=active, size=10)
        elif STYLE["header"] == "chips":        # a big title, numbered chips on the right and a thin rule
            vis = [v_textbox(pid, "nav-title", 14, 8, 640, 46, 2, [(title, {"fontSize": "17pt", "fontWeight": "bold", "color": INK})]),
                   v_shape(pid, "nav-rule", 16, 58, 1248, 2, 3, EDGE)]
            bw = 108
            for i, k in enumerate(keys):
                active = k == key
                vis += v_button(pid, f"nav-{k}", 1264 - (len(keys) - i) * (bw + 6) + 6, 16, bw, 28, 10 + 2 * i, f"{i + 1}  {labels[k]}", ("page", pid_of[k]),
                                fill=TEAL if active else CARD, text_color=STYLE["nav_active_text"] if active else STYLE["nav_text"], bold=active, size=10)
        elif STYLE["header"] == "editorial":    # a serif headline, a heavy rule, text tabs with an underline
            vis = [v_textbox(pid, "nav-title", 12, 8, 900, 48, 2, [(title, {"fontSize": "24pt", "fontWeight": "bold", "color": INK, "fontFamily": STYLE["font"]})]),
                   v_shape(pid, "nav-rule", 16, 64, 1248, 3, 3, INK)]
            bw = 118
            for i, k in enumerate(keys):
                active = k == key
                vis += v_button(pid, f"nav-{k}", 16 + i * (bw + 6), 74, bw, 28, 10 + 2 * i, labels[k], ("page", pid_of[k]),
                                fill=None, text_color=STYLE["nav_active_text"] if active else STYLE["nav_text"], bold=active, size=11)
                if active:
                    vis.append(v_shape(pid, "nav-under", 16 + i * (bw + 6) + 10, 101, bw - 20, 3, 50, ACCENT))
        else:
            hh = 56 if STYLE["header"] == "strip" else 52
            vis = [v_shape(pid, "nav-bg", 0, 0, 1280, hh, 1, NAVY),
                   v_textbox(pid, "nav-title", 14, 8 if STYLE["header"] == "strip" else 10, 300 if STYLE["header"] == "strip" else 330, 40, 2,
                             [(title, {"fontSize": "14pt", "fontWeight": "bold", "color": INK if STYLE["header"] == "strip" else TEAL})])]
            if STYLE["header"] == "strip":         # tabs in a row under the header, dropdown filters in the header
                bw = 112
                for i, k in enumerate(keys):
                    active = k == key
                    vis += v_button(pid, f"nav-{k}", 16 + i * (bw + 6), 64, bw, 30, 10 + 2 * i, labels[k], ("page", pid_of[k]),
                                    fill=TEAL if active else CARD, text_color=STYLE["nav_active_text"] if active else STYLE["nav_text"], bold=active, size=10)
                sw = (1280 - 330 - 16 - 3 * 10) / 4
                for i, dn in enumerate(strip):
                    vis.append(v_slicer(pid, table, dn, dn, 330 + i * (sw + 10), 4, sw, 50, 40 + i, boxed=True))
            else:                                  # title and tabs share one bar
                bw = min(116, (1280 - 360 - 16) / len(keys) - 6)
                for i, k in enumerate(keys):
                    active = k == key
                    vis += v_button(pid, f"nav-{k}", 360 + i * (bw + 6), 11, bw, 30, 10 + 2 * i, labels[k], ("page", pid_of[k]),
                                    fill=TEAL if active else CARD, text_color=STYLE["nav_active_text"] if active else STYLE["nav_text"], bold=active, size=10)
        pages.append((pid, labels[key], vis))
        return pid, vis, nz

    def g(x, y, w, h):
        return (CX + x, round(top + (y - 64) * ys, 1), w, round(h * hs, 1))

    if layout != "classic":
        gap_ = 12
        mw_ = round(CW * 0.61)
        ctx = SimpleNamespace(table=table, fmts=fmts, title=title, date=date, rows_name=rows_name, new_page=new_page, g=g, tip=tip_pid, gap=gap_, main_w=mw_, side_w=CW - mw_ - gap_,
                              trend_f=fld_col(table, "Month") if date else None, insights=[x.partition("|")[::2] for x in (insights or [])],
                              d=lambda i: dims[min(i, len(dims) - 1)]["name"], m=lambda i: heads[i] if i < len(heads) else rows_name)
        LAYOUTS[layout](ctx)
    else:
        # ---- 1. Overview (F pattern: KPIs on top, the main trend wide on the left, details below)
        pid, vis, nz = new_page("overview", title)
        cards = [rows_name] + heads[:4]
        gap = 12
        cw = (CW - gap * (len(cards) - 1)) / len(cards)
        trend_f = fld_col(table, "Month") if date else None
        for i, name in enumerate(cards):
            pos = g(i * (cw + gap), 64, cw, 110)
            if date and name in heads[:3]:
                z = nz()
                nz(); nz()
                vis += v_kpi(pid, table, name, fmts[name], trend_f, "Month", *pos, z)
            else:
                vis.append(v_card(pid, table, name, fmts[name], *pos, nz()))
        main_w = round(CW * 0.61)
        side_w = CW - main_w - gap
        small = [d for d in dims if d["distinct"] <= 6]
        if date:
            vis.append(v_chart(pid, "lineChart", table, trend_f, "Month", m1, fmts[m1], f"{m1} by month", *g(0, 186, main_w, 232), nz(), sort_by_measure=False, tip=tip_pid))
        elif dims:
            vis.append(dim_chart(pid, dims[0], m1, g(0, 186, main_w, 232), nz()))
        used = {dims[0]["name"]} if (dims and not date) else set()
        if small:
            d = next((x for x in small if x["name"] not in used), small[0])
            vis.append(v_chart(pid, "donutChart", table, fld_col(table, d["name"]), d["name"], rows_name, fmts[rows_name], f"Share of rows by {d['name']}",
                               *g(main_w + gap, 186, side_w, 232), nz(), tip=tip_pid))
            used.add(d["name"])
        rest = [d for d in dims if d["name"] not in used]
        if rest:
            vis.append(dim_chart(pid, rest[0], m2 if m2 != m1 or len(heads) < 2 else m1, g(0, 430, side_w, 278), nz()))
        if len(rest) > 1:
            vis.append(dim_chart(pid, rest[1], m1, g(side_w + gap, 430, main_w, 278), nz()))

        # ---- 2. Breakdown: the main measure by each category, sizes varied
        if "breakdown" in keys:
            pid, vis, nz = new_page("breakdown", f"{m1} by category")
            slots = [g(0, 64, main_w, 296), g(main_w + gap, 64, side_w, 296), g(0, 372, side_w, 336), g(side_w + gap, 372, main_w, 336)]
            for slot, d in zip(slots, dims[:4]):
                vis.append(dim_chart(pid, d, m1, slot, nz()))

        # ---- 3. Compare
        if "compare" in keys:
            pid, vis, nz = new_page("compare", "Compare categories")
            cat = next((d for d in dims if 4 <= d["distinct"] <= 40), dims[0])
            vis.append(v_scatter(pid, table, fld_col(table, cat["name"]), cat["name"], m2, m1, rows_name, fmts, f"{m1} against {m2} by {cat['name']}",
                                 *g(0, 64, main_w, 296), nz(), tip=tip_pid))
            tm = dims[1] if len(dims) > 1 else dims[0]
            vis.append(v_treemap(pid, table, fld_col(table, tm["name"]), tm["name"], rows_name, fmts[rows_name], f"Rows by {tm['name']}",
                                 *g(main_w + gap, 64, side_w, 296), nz(), tip=tip_pid))
            other = [d for d in dims if d is not tm and d is not cat] or [d for d in dims if d is not tm] or dims
            slots = [g(0, 372, side_w, 336), g(side_w + gap, 372, main_w, 336)]
            for i, slot in enumerate(slots):
                vis.append(dim_chart(pid, other[i % len(other)], [m2, m3][i], slot, nz()))

        # ---- 4. Trends
        if "trends" in keys:
            pid, vis, nz = new_page("trends", "Trends over time")
            trend_measures = (list(heads[:3]) + [rows_name])[:4]
            slots = [g(0, 64, main_w, 296), g(main_w + gap, 64, side_w, 296), g(0, 372, side_w, 336), g(side_w + gap, 372, main_w, 336)]
            for slot, meas in zip(slots, trend_measures):
                kind = "areaChart" if meas == rows_name else "lineChart"
                vis.append(v_chart(pid, kind, table, trend_f, "Month", meas, fmts[meas], f"{meas} by month", *slot, nz(), sort_by_measure=False, tip=tip_pid))

        # ---- 5. Matrix: a sorted bar instead of a table first, then the matrices
        if "matrix" in keys:
            pid, vis, nz = new_page("matrix", "Measures in a table")
            mm = heads[:4] if heads else [rows_name]
            vis.append(dim_chart(pid, dims[0], m1, g(0, 64, side_w, 644), nz()))
            vis.append(v_matrix(pid, table, [dims[0]["name"]], [], [rows_name] + mm[:4], fmts, f"Measures by {dims[0]['name']}", *g(side_w + gap, 64, main_w, 296), nz()))
            if len(dims) > 1:
                vis.append(v_matrix(pid, table, [dims[0]["name"]], [dims[1]["name"]], [m1], fmts, f"{m1} by {dims[0]['name']} and {dims[1]['name']}",
                                    *g(side_w + gap, 372, main_w, 336), nz()))
            else:
                vis.append(v_table(pid, table, [dims[0]["name"]], mm[:3], "Summary", *g(side_w + gap, 372, main_w, 336), nz()))

    # ---- 6. Details
    pid, vis, nz = new_page("details", "Details")
    shown = [c["name"] for c in cols if c["kind"] != "empty"][:14]
    vis.append(v_table(pid, table, shown, [rows_name], "Rows", *g(0, 64, CW, 644), nz()))

    # ---- 7. About
    pid = pid_of["about"]
    pid, vis, nz = new_page("about", "About this report")
    body, head = {"fontSize": "13pt", "color": INK}, {"fontSize": "15pt", "fontWeight": "bold", "color": INK}
    runs = [(about or f"A report built from a table of {len(cols)} columns.", body), ("", None), ("How to use it", head),
            ("Open the Filters pane on the right (the funnel tab) to filter by category or date; the selection applies to every page. Click a bar, a slice or a point to cross-filter the other charts, "
             "and hover over one to see a small summary card.", body)]
    if source:
        runs += [("", None), ("Data source", head), (source, body)]
    runs += [("", None), ("Measures", head)]
    runs += [(f"{m[0]} = {m[1]}", body) for m in measures if m[3] in ("rows", "custom")]
    vis.append(v_textbox(pid, "about", *g(0, 64, CW, 644), nz(), runs, card=True))

    # ---- tooltip page (320 x 240): the three main measures for whatever the mouse is over
    tip_vis = []
    for i, name in enumerate([m1, m2, m3] if len({m1, m2, m3}) == 3 else list(dict.fromkeys([rows_name, m1, m2]))[:3]):
        tip_vis.append(container(tip_pid, f"tip-{i}", 12, 12 + i * 76, 296, 68, 10 + i, {
            "visualType": "cardVisual",
            "query": {"queryState": {"Data": {"projections": [proj(fld_meas(table, name), table, name, fmts[name])]}}},
            "visualContainerObjects": card_objects(), "drillFilterOtherVisuals": True}, scale=1))

    files = {
        "version.json": {"$schema": f"{SCHEMA}/versionMetadata/{V_VERSION}/schema.json", "version": "2.0.0"},
        "pages/pages.json": {"$schema": f"{SCHEMA}/pagesMetadata/{V_PAGES}/schema.json", "pageOrder": [p[0] for p in pages] + [tip_pid], "activePageName": pages[0][0]},
    }
    for pid, pname, visuals in pages:
        files[f"pages/{pid}/page.json"] = {"$schema": f"{SCHEMA}/page/{V_PAGE}/schema.json", "name": pid, "displayName": pname,
                                           "displayOption": "FitToPage", "height": PAGE_H, "width": PAGE_W}
        for v in visuals:
            files[f"pages/{pid}/visuals/{v['name']}/visual.json"] = v
    files[f"pages/{tip_pid}/page.json"] = {"$schema": f"{SCHEMA}/page/{V_PAGE}/schema.json", "name": tip_pid, "displayName": "Tooltip", "displayOption": "ActualSize",
                                           "height": 240, "width": 320, "type": "Tooltip", "visibility": "HiddenInViewMode",
                                           "pageBinding": {"name": oid("binding", "tooltip"), "type": "Tooltip"},
                                           "objects": {"pageSize": [{"properties": {"pageSizeTypes": tx("Tooltip"), "pageSizeWidth": num(320), "pageSizeHeight": num(240)}}]}}
    for v in tip_vis:
        files[f"pages/{tip_pid}/visuals/{v['name']}/visual.json"] = v
    report_filters = []
    for col, label, dd in sl:
        if col in strip or col in [d["name"] for d in side]:
            continue            # already a slicer in the header or the side panel
        f = {"name": oid("report-filter", col), "field": fld_col(table, col), "type": "Categorical" if dd else "Advanced", "howCreated": "User"}
        report_filters.append(f)
    return files, theme_json(), report_filters


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


def report_json(theme_name, filters=()):
    ver = IMPORT_VERSIONS
    return {"$schema": f"{SCHEMA}/report/{V_REPORT}/schema.json",
            "themeCollection": {"baseTheme": {"name": theme_name, "reportVersionAtImport": ver, "type": "SharedResources"},
                                "customTheme": {"name": CUSTOM_THEME, "reportVersionAtImport": ver, "type": "RegisteredResources"}},
            **({"filterConfig": {"filters": list(filters)}} if filters else {}),
            "objects": {"section": [{"properties": {"verticalAlignment": literal("Top")}}],
                        "outspacePane": [{"properties": {"expanded": {"expr": {"Literal": {"Value": "false"}}}}}]},
            "resourcePackages": [{"name": "SharedResources", "type": "SharedResources",
                                  "items": [{"name": theme_name, "path": f"BaseThemes/{theme_name}.json", "type": "BaseTheme"}]},
                                 {"name": "RegisteredResources", "type": "RegisteredResources",
                                  "items": [{"name": CUSTOM_THEME, "path": CUSTOM_THEME, "type": "CustomTheme"}]}],
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
    ap.add_argument("--geo", help='geographic columns for map visuals, "Column=Category,...": for example "State=StateOrProvince,Lat=Latitude,Lon=Longitude"')
    ap.add_argument("--rows-name", default="Rows", help="name of the row-count measure, for example Patients or Wines")
    ap.add_argument("--style", choices=sorted(STYLES), default="clean",
                    help="look of the report: laurel (black and gold, serif title), solar (indigo with a sun and an orbit of pages), basalt (light with a slim numbered rail), frost (airy ice blue, centred title), pastel (pink and lavender, floating side card), newsprint (a front page in black and red), storm (slate with a numbered rail), terminal (amber on black), garage (cream with a bottom dock),  clean (light, left menu), midnight (dark blue, filters in the header), sunset (brown and orange, tabs on top), "
                         "violet (purple, left menu), neon (black with cyan, tabs at the bottom), paper (cream, serif headline), bordeaux (burgundy rail on the right)")
    ap.add_argument("--layout", choices=["classic", "pulse", "flow", "lab", "board", "story", "mix", "atlas", "macro", "catalog", "climate", "pyramid", "bump", "people", "cycle", "quake"], default="classic",
                    help="which pages and charts: classic (bars, donut, scatter, matrices), pulse (time series: combo, heat map, stacked area, gauge), "
                         "flow (categories: funnel, ribbon, waterfall, 100%% stacked), lab (numeric columns: gauge, small multiples, scatter by group), "
                         "board (a ranked table with data bars, actual against target), story (insight cards beside the charts), mix (stacked columns and bars, air-quality bands), "
                         "climate (warming stripes, a day-by-month heat map, extremes), pyramid (age pyramids, a tile map, generations), bump (rank-over-time chart and a rank table), "
                         "people (laureates: ages, women, places, shared prizes), cycle (a monthly series split into cycles: one line per cycle, cycle-by-year heat map), "
                         "quake (a dot map of grid cells, a log-scale magnitude chart, depth classes, regions), "
                         "atlas (a tile-grid map and a dot map with a ranking), macro (latest values and changes, rates, cycles), catalog (a vehicle catalogue: brands, engines, electric)")
    ap.add_argument("--insight", action="append", help='for the story layout: an insight card as "BIG NUMBER|sentence", repeatable (six are used)')
    ap.add_argument("--measure", action="append", help='custom DAX measure "Name=DAX|format" ({T} = table name); custom measures are used first in cards and charts')
    ap.add_argument("--only-custom", action="store_true", help="build only the Rows measure and the --measure ones (no automatic Total/Average measures)")
    ap.add_argument("--about", help="text for the About page (what the data is)")
    ap.add_argument("--source", help="data source and licence, shown on the About page")
    ap.add_argument("--embed", action="store_true", help="store the data inside the model (CSV only) so the project does not depend on the file's location")
    ap.add_argument("--open", action="store_true", help="open the project in Power BI Desktop when done (Windows)")
    args = ap.parse_args()
    apply_style(args.style)

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
    if args.only_custom:
        metrics = []
    custom = parse_custom_measures(args.measure, table)
    measures = build_measures(table, args.rows_name, metrics, custom)
    clash = sorted({m[0] for m in measures} & {c["name"] for c in cols} | ({"Month", "Year"} & {m[0] for m in measures} if date else set()))
    if clash:
        sys.exit(f"measure name(s) {', '.join(clash)} are also column names; a column and a measure cannot share a name in one table. Rename the measure.")
    if args.embed:
        if src["kind"] != "csv":
            sys.exit("--embed works with CSV input only")
        buf = io.StringIO()
        df.to_csv(buf, index=False, quoting=csv.QUOTE_MINIMAL)
        src["embedded"] = base64.b64encode(gzip.compress(buf.getvalue().encode("utf-8"), mtime=0)).decode("ascii")
        src["delimiter"] = ","

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
    write_text(sm / "definition" / "tables" / f"{table}.tmdl", tmdl_table(table, cols, date, measures, m, dict(x.split("=", 1) for x in args.geo.split(",")) if args.geo else None))

    write_json(rp / "definition.pbir", {"$schema": "https://developer.microsoft.com/json-schemas/fabric/item/report/definitionProperties/2.0.0/schema.json",
                                        "version": "4.0", "datasetReference": {"byPath": {"path": f"../{name}.SemanticModel"}}})
    theme_file = find_base_theme()
    theme_name = theme_file.stem if theme_file else FALLBACK_THEME

    if theme_file:
        dest = rp / "StaticResources" / "SharedResources" / "BaseThemes" / theme_file.name
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(theme_file, dest)
    files, theme, report_filters = build_report(table, title, date, dims, metrics, measures, cols, args.about, args.source, args.layout, args.insight)
    write_json(rp / "definition" / "report.json", report_json(theme_name, report_filters))
    for rel, obj in files.items():
        write_json(rp / "definition" / rel, obj)
    write_json(rp / "StaticResources" / "RegisteredResources" / CUSTOM_THEME, theme)

    longest = max(len(str(f.resolve())) for f in out.rglob("*") if f.is_file())
    if longest > 240:
        print(f"WARNING: the longest file path is {longest} characters. Windows stops at 260 and Power BI Desktop will fail to open the project "
              f"(\"Znaleziono problemy / Cannot read ...visual.json\"). Generate it in a shorter folder, for example --out C:/pbi.")
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

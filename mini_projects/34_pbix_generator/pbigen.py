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
    CX = NAV_W + 16 if STYLE["nav"] in ("left", "split") else 16
    CW = {"left": 1280 - CX - 24, "right": 1280 - NAV_W - 32, "split": 1280 - CX - 16}.get(STYLE["nav"], 1280 - 32)


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
    paragraphs = [{"textRuns": [{"value": text, **({"textStyle": style} if style else {})}]} for text, style in runs]
    v = {"visualType": "textbox", "objects": {"general": [{"properties": {"paragraphs": paragraphs}}]}, "drillFilterOtherVisuals": True}
    if card:
        v["visualContainerObjects"] = card_objects()
    return container(page, key, x, y, w, h, z, v, scale=scale)


def v_shape(page, key, x, y, w, h, z, fill, hidden=False, border=False, radius=0):
    v = {"visualType": "basicShape",
         "objects": {"general": [{"properties": {"shapeType": tx("rectangle")}}],
                     "fill": [{"properties": {"show": flag(True), "fillColor": solid(fill)}}],
                     "line": [{"properties": {"show": flag(border), "lineColor": solid(EDGE), "weight": num(1)}}],
                     "rotation": [{"properties": {"shapeAngle": num(0)}}]},
         "visualContainerObjects": {"background": [{"properties": {"show": flag(False)}}], "border": [{"properties": {"show": flag(False)}}]},
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
    style = {"fontSize": f"{size}pt", "color": text_color, **({"fontWeight": "bold"} if bold else {})}
    bg = ({"show": flag(True), "color": solid(fill), "transparency": num(0)} if fill else {"show": flag(False)})
    v = {"visualType": "textbox",
         "objects": {"general": [{"properties": {"paragraphs": [{"textRuns": [{"value": label, "textStyle": style}], "horizontalTextAlignment": "center"}]}}]},
         "visualContainerObjects": {"background": [{"properties": bg}], "border": [{"properties": {"show": flag(False)}}],
                                    "padding": [{"properties": {"top": num(max(round(h * K / 2 - size * 1.4), 2), "L")}}],
                                    "visualLink": [{"properties": link}]},
         "drillFilterOtherVisuals": True}
    return [container(page, key, x, y, w, h, z, v, hidden=hidden)]


def v_card(page, table, measure, fmt, x, y, w, h, z):
    v = {"visualType": "cardVisual",
         "query": {"queryState": {"Data": {"projections": [proj(fld_meas(table, measure), table, measure, fmt)]}},
                   "sortDefinition": {"sort": [{"field": fld_meas(table, measure), "direction": "Descending"}], "isDefaultSort": True}},
         "visualContainerObjects": card_objects(), "drillFilterOtherVisuals": True}
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


def kind_objects(kind, legend=False, axis=None, units=None):
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
    if kind in ("lineChart", "areaChart", "stackedAreaChart", "ribbonChart", "lineClusteredColumnComboChart"):
        return {"categoryAxis": [{"properties": {**cat, "axisType": tx("Categorical")} if kind in ("ribbonChart",) else cat}],
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
    if kind == "pieChart":
        return {"legend": [{"properties": {"show": on, "position": tx("Right"), "labelColor": solid(MUTED)}}], "labels": [{"properties": lab}]}
    if kind == "multiRowCard":
        return {"dataLabels": [{"properties": {"color": solid(INK), "fontSize": num(15)}}], "cardTitle": [{"properties": {"color": solid(MUTED), "fontSize": num(9)}}],
                "card": [{"properties": {"barShow": off, "outline": tx("None")}}]}
    return chart_objects(kind)


def v_gen(page, key, kind, table, roles, fmts, title, x, y, w, h, z, tip=None, sort=None, legend=False, axis=None, units=None):
    """Any chart: roles = {role: [("c", column) | ("m", measure)]}; sort = ("c" | "m", name, "Ascending" | "Descending")."""
    state = {}
    for role, items in roles.items():
        projs = []
        for typ, name in items:
            if typ == "c":
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
    v = {"visualType": kind, "query": q, "objects": kind_objects(kind, legend, axis, units), "visualContainerObjects": vco, "drillFilterOtherVisuals": True}
    if not v["objects"]:
        del v["objects"]
    return container(page, f"{kind}-{key}", x, y, w, h, z, v)


def v_heat(page, table, rows_col, cols_col, measure, fmts, title, x, y, w, h, z):
    """A matrix whose cells are shaded by the measure (a heat map); the three colours come from the style."""
    lo, mid, hi = STYLE["heat"]

    def colour(c):
        return {"color": {"Literal": {"Value": "'" + c + "'"}}}
    rule = {"solid": {"color": {"expr": {"FillRule": {"Input": fld_meas(table, measure),
            "FillRule": {"linearGradient3": {"min": colour(lo), "mid": colour(mid), "max": colour(hi),
                                             "nullColoringStrategy": {"strategy": {"Literal": {"Value": "'asZero'"}}}}}}}}}}
    objects = grid_objects(12)
    objects["values"].append({"properties": {"backColor": rule}, "selector": {"data": [{"dataViewWildcard": {"matchingOption": 1}}], "metadata": f"{table}.{measure}"}})
    state = {"Rows": {"projections": [proj(fld_col(table, rows_col), table, rows_col, active=True)]},
             "Columns": {"projections": [proj(fld_col(table, cols_col), table, cols_col, active=True)]},
             "Values": {"projections": [proj(fld_meas(table, measure), table, measure, fmts[measure])]}}
    v = {"visualType": "pivotTable", "query": {"queryState": state}, "objects": objects, "visualContainerObjects": card_objects(title), "drillFilterOtherVisuals": True}
    return container(page, f"heat-{rows_col}-{cols_col}", x, y, w, h, z, v)


def kpi_row(c, pid, vis, nz, names, y=64, h=110, spark=3):
    """A row of KPI tiles; the first `spark` of them get a trend line when the data has a date."""
    cw = (CW - c.gap * (len(names) - 1)) / len(names)
    for i, name in enumerate(names):
        pos = c.g(i * (cw + c.gap), y, cw, h)
        if c.date and i < spark:
            z = nz()
            nz(), nz()
            vis += v_kpi(pid, c.table, name, c.fmts[name], c.trend_f, "Month", *pos, z)
        else:
            vis.append(v_card(pid, c.table, name, c.fmts[name], *pos, nz()))


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


def v_rank(page, table, cols, measures, bar, fmts, title, x, y, w, h, z, sort_by):
    """A ranked table: sorted by a measure, with data bars on one measure column."""
    projections = [proj(fld_col(table, cc), table, cc) for cc in cols] + [proj(fld_meas(table, m), table, m, fmts[m]) for m in measures]
    objs = grid_objects(13)
    lo, mid, hi = STYLE["heat"]
    shade = {"solid": {"color": {"expr": {"FillRule": {"Input": fld_meas(table, bar), "FillRule": {"linearGradient3": {
        "min": {"color": {"Literal": {"Value": "'" + lo + "'"}}}, "mid": {"color": {"Literal": {"Value": "'" + mid + "'"}}}, "max": {"color": {"Literal": {"Value": "'" + hi + "'"}}},
        "nullColoringStrategy": {"strategy": {"Literal": {"Value": "'asZero'"}}}}}}}}}}
    objs["values"].append({"properties": {"backColor": shade}, "selector": {"data": [{"dataViewWildcard": {"matchingOption": 1}}], "metadata": f"{table}.{bar}"}})
    v = {"visualType": "tableEx",
         "query": {"queryState": {"Values": {"projections": projections}}, "sortDefinition": {"sort": [{"field": fld_meas(table, sort_by), "direction": "Descending"}], "isDefaultSort": True}},
         "objects": objs, "visualContainerObjects": card_objects(title), "drillFilterOtherVisuals": True}
    return container(page, "rank-" + bar, x, y, w, h, z, v)


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


LAYOUT_PAGES = {
    "pulse": [("overview", "Overview"), ("rhythm", "Rhythm"), ("weather", "Weather"), ("seasons", "Seasons"), ("details", "Details"), ("about", "About")],
    "flow": [("overview", "Overview"), ("patients", "Patients"), ("stay", "Stay"), ("treatment", "Treatment"), ("details", "Details"), ("about", "About")],
    "lab": [("overview", "Overview"), ("chemistry", "Chemistry"), ("compare", "Compare"), ("bands", "Bands"), ("details", "Details"), ("about", "About")],
    "board": [("overview", "Overview"), ("teams", "Teams"), ("drivers", "Drivers"), ("trend", "Trend"), ("details", "Details"), ("about", "About")],
    "story": [("overview", "Overview"), ("study", "Study"), ("home", "Home"), ("habits", "Habits"), ("details", "Details"), ("about", "About")],
    "mix": [("overview", "Overview"), ("bands", "Bands"), ("seasons", "Seasons"), ("wind", "Wind"), ("details", "Details"), ("about", "About")],
}
LAYOUTS = {"pulse": layout_pulse, "flow": layout_flow, "lab": layout_lab, "board": layout_board, "story": layout_story, "mix": layout_mix}


def theme_json():
    call = {"INK": INK, "TEAL": TEAL, "ACCENT": ACCENT}[STYLE["callout"]]
    return {"name": f"pbigen {STYLE_NAME}", "dataColors": STYLE["series"], "background": CARD, "foreground": INK, "tableAccent": TEAL,
            "good": TEAL, "neutral": MUTED, "bad": ACCENT, "maximum": TEAL, "center": MUTED, "minimum": ACCENT,
            "textClasses": {"callout": {"fontSize": 26, "fontFace": STYLE["font"] if STYLE["font"] != "Segoe UI" else "Segoe UI Semibold", "color": call},
                            "title": {"fontSize": 12, "fontFace": "Segoe UI Semibold", "color": INK},
                            "header": {"fontSize": 12, "fontFace": "Segoe UI Semibold", "color": INK},
                            "label": {"fontSize": 10, "fontFace": "Segoe UI", "color": MUTED}},
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
        if layout in ("pulse", "mix") and not date:
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
    ap.add_argument("--rows-name", default="Rows", help="name of the row-count measure, for example Patients or Wines")
    ap.add_argument("--style", choices=sorted(STYLES), default="clean",
                    help="look of the report: clean (light, left menu), midnight (dark blue, filters in the header), sunset (brown and orange, tabs on top), "
                         "violet (purple, left menu), neon (black with cyan, tabs at the bottom), paper (cream, serif headline), bordeaux (burgundy rail on the right)")
    ap.add_argument("--layout", choices=["classic", "pulse", "flow", "lab", "board", "story", "mix"], default="classic",
                    help="which pages and charts: classic (bars, donut, scatter, matrices), pulse (time series: combo, heat map, stacked area, gauge), "
                         "flow (categories: funnel, ribbon, waterfall, 100%% stacked), lab (numeric columns: gauge, small multiples, scatter by group), "
                         "board (a ranked table with data bars, actual against target), story (insight cards beside the charts), mix (stacked columns and bars, air-quality bands)")
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
    write_text(sm / "definition" / "tables" / f"{table}.tmdl", tmdl_table(table, cols, date, measures, m))

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

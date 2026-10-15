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
    "clean": dict(nav="left", nav_w=170, NAVY="#1B2A49", TEAL="#0E7C86", ACCENT="#F2994A", INK="#2D3748", MUTED="#718096", BG="#F3F4F6", CARD="#FFFFFF",
                  EDGE="#E2E8F0", nav_text="#CBD5E0", nav_active_text="#FFFFFF", radius=8, border=1, dark=False, title_card=False, top_slicers=False,
                  callout="INK", series=["#0E7C86", "#A0AEC0", "#F2994A", "#1B2A49", "#5BB5BD", "#CBD5E0", "#F6C28B", "#4A5568"]),
    # dark navy, slicers in a strip at the top, tabs under the header (after a health-insurance dashboard)
    "midnight": dict(nav="top", NAVY="#081226", TEAL="#4F86B8", ACCENT="#A9C7EC", INK="#E8F0FB", MUTED="#8FA6C8", BG="#0C1B38", CARD="#142B52",
                     EDGE="#27487F", nav_text="#9DB4D6", nav_active_text="#0C1B38", radius=4, border=1, dark=True, title_card=False, top_slicers=True,
                     callout="INK", series=["#4F86B8", "#9DB8E0", "#9FD3C7", "#C3A6D8", "#E8C77B", "#6F9FD0", "#7FC4B4", "#D9A5B8"]),
    # dark brown with orange, title and tabs in one bar, thick orange card borders (after a YouTube analytics dashboard)
    "sunset": dict(nav="top", NAVY="#1A0F0A", TEAL="#F26B1D", ACCENT="#FFC27A", INK="#FDEBDD", MUTED="#D9A98A", BG="#2B1A12", CARD="#3A2418",
                   EDGE="#F26B1D", nav_text="#E8C3A5", nav_active_text="#1A0F0A", radius=0, border=2, dark=True, title_card=False, top_slicers=False,
                   callout="TEAL", series=["#F26B1D", "#FFB347", "#C2410C", "#E9A66B", "#9A5530", "#FFD8A8", "#7A3E1D", "#F7926B"]),
    # purple, left rail, round cards without borders, yellow accent (after an e-commerce sales dashboard)
    "violet": dict(nav="left", nav_w=150, NAVY="#1E0E3C", TEAL="#8E5CFF", ACCENT="#F5B700", INK="#F6F0FF", MUTED="#CDBBF0", BG="#2E1659", CARD="#432380",
                   EDGE="#6D3FC0", nav_text="#CDBBF0", nav_active_text="#1E0E3C", radius=16, border=0, dark=True, title_card=True, top_slicers=False,
                   callout="ACCENT", series=["#F5B700", "#35C9B1", "#FF6B9A", "#8EC5FF", "#B388FF", "#FFA45B", "#7BE0AD", "#E0D4FF"]),
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
    CX = NAV_W + 16 if STYLE["nav"] == "left" else 16
    CW = 1280 - CX - 24 if STYLE["nav"] == "left" else 1280 - 32


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


def grid_objects():
    """Colours for tables and matrices (on a dark card the default white cells would be wrong)."""
    head = {"fontColor": solid(INK), "backColor": solid(NAVY if STYLE["dark"] else "#EEF2F7")}
    body = {"fontColor": solid(INK), "backColor": solid(CARD), "fontColorPrimary": solid(INK), "backColorPrimary": solid(CARD),
            "fontColorSecondary": solid(INK), "backColorSecondary": solid(BG if STYLE["dark"] else "#F8FAFC")}
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
    spark = container(page, "spark-" + measure, x + 6, y + h * 0.42, w - 12, h * 0.55, z + 1, {
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


def theme_json():
    call = {"INK": INK, "TEAL": TEAL, "ACCENT": ACCENT}[STYLE["callout"]]
    return {"name": f"pbigen {STYLE_NAME}", "dataColors": STYLE["series"], "background": CARD, "foreground": INK, "tableAccent": TEAL,
            "good": TEAL, "neutral": MUTED, "bad": ACCENT, "maximum": TEAL, "center": MUTED, "minimum": ACCENT,
            "textClasses": {"callout": {"fontSize": 26, "fontFace": "Segoe UI Semibold", "color": call},
                            "title": {"fontSize": 12, "fontFace": "Segoe UI Semibold", "color": INK},
                            "header": {"fontSize": 12, "fontFace": "Segoe UI Semibold", "color": INK},
                            "label": {"fontSize": 10, "fontFace": "Segoe UI", "color": MUTED}},
            "visualStyles": {"page": {"*": {"background": [{"color": {"solid": {"color": BG}}, "transparency": 0}],
                                            "outspace": [{"color": {"solid": {"color": NAVY if STYLE["dark"] else "#E5E7EB"}}, "transparency": 0}]}}}}


NAV_W, CX, CW = 0, 16, 1248                           # content column on the 1280 grid, set by apply_style
apply_style("clean")


def build_report(table, title, date, dims, metrics, measures, cols, about=None, source=None):
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
    pid_of = {k: oid("page", k) for k in keys}
    tip_pid = oid("page", "tooltip")

    sl = []
    if date:
        sl.append((date["name"], date["name"], False))
    for d in dims[:3 if date else 4]:
        sl.append((d["name"], d["name"], True))
    sl = sl[:4]
    strip = [d["name"] for d in dims[:4]] if STYLE["top_slicers"] else []          # dropdowns in the header
    top = {"clean": 64, "violet": 64, "sunset": 66, "midnight": 108}[STYLE_NAME]     # where the content starts on the 720 grid
    ys, hs = (708 - top) / 644, (708 - top) / 644

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

        if STYLE["nav"] == "left":
            vis = [v_shape(pid, "nav-bg", 0, 0, NAV_W, 720, 1, NAVY),
                   v_textbox(pid, "nav-title", 14, 18, NAV_W - 24, 56, 2, [(title, {"fontSize": "13pt", "fontWeight": "bold", "color": "#FFFFFF"})])]
            for i, k in enumerate(keys):
                active = k == key
                vis += v_button(pid, f"nav-{k}", 12, 96 + i * 44, NAV_W - 24, 36, 10 + 2 * i, labels[k], ("page", pid_of[k]),
                                fill=ACCENT if STYLE_NAME == "violet" and active else (TEAL if active else None),
                                text_color=STYLE["nav_active_text"] if active and STYLE_NAME == "violet" else ("#FFFFFF" if active else STYLE["nav_text"]), bold=active)
            head = v_textbox(pid, "title", CX, 14, CW, 40, nz() + 100, [(heading, {"fontSize": "20pt", "fontWeight": "bold", "color": INK})], card=STYLE["title_card"])
            vis.append(head)
        else:
            hh = 56 if STYLE_NAME == "midnight" else 52
            vis = [v_shape(pid, "nav-bg", 0, 0, 1280, hh, 1, NAVY),
                   v_textbox(pid, "nav-title", 14, 8 if STYLE_NAME == "midnight" else 10, 300 if STYLE_NAME == "midnight" else 330, 40, 2,
                             [(title, {"fontSize": "14pt", "fontWeight": "bold", "color": INK if STYLE_NAME == "midnight" else TEAL})])]
            if STYLE_NAME == "midnight":           # tabs in a row under the header, dropdown filters in the header
                bw = 112
                for i, k in enumerate(keys):
                    active = k == key
                    vis += v_button(pid, f"nav-{k}", 16 + i * (bw + 6), 64, bw, 30, 10 + 2 * i, labels[k], ("page", pid_of[k]),
                                    fill=TEAL if active else CARD, text_color=STYLE["nav_active_text"] if active else STYLE["nav_text"], bold=active, size=10)
                sw = (1280 - 330 - 16 - 3 * 10) / 4
                for i, dn in enumerate(strip):
                    vis.append(v_slicer(pid, table, dn, dn, 330 + i * (sw + 10), 4, sw, 50, 40 + i, boxed=True))
            else:                                  # sunset: title and tabs share one bar
                bw = min(116, (1280 - 360 - 16) / len(keys) - 6)
                for i, k in enumerate(keys):
                    active = k == key
                    vis += v_button(pid, f"nav-{k}", 360 + i * (bw + 6), 11, bw, 30, 10 + 2 * i, labels[k], ("page", pid_of[k]),
                                    fill=TEAL if active else CARD, text_color=STYLE["nav_active_text"] if active else STYLE["nav_text"], bold=active, size=10)
        pages.append((pid, labels[key], vis))
        return pid, vis, nz

    def g(x, y, w, h):
        return (CX + x, round(top + (y - 64) * ys, 1), w, round(h * hs, 1))

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
        if col in strip:
            continue            # already a dropdown in the header
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
    ap.add_argument("--style", choices=sorted(STYLES), default="clean",
                    help="look of the report: clean (light, left menu), midnight (dark blue, filters in the header), sunset (brown and orange, tabs on top), violet (purple, left menu)")
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
    measures = build_measures(table, "Rows", metrics, custom)
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
    files, theme, report_filters = build_report(table, title, date, dims, metrics, measures, cols, args.about, args.source)
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

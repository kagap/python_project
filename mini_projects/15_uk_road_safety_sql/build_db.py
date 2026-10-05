"""Load the 2023 UK road casualty statistics (STATS19) into a SQLite database.

usage: python build_db.py [--cache DIR] [--out roads.db]

Source: Department for Transport road safety data, https://www.data.gov.uk/dataset/cb7ae6f0-4be6-4935-9277-47e5ce24a11f
Three CSV files (collisions 19.8 MB, vehicles 20.9 MB, casualties 11.0 MB) are downloaded once into the cache folder.
The CSVs contain numeric codes only; the label tables below follow the DfT STATS19 data guide for the
codes that appear in 2023. Codes whose meaning is not certain are labelled "Other / unclassified".
"""
import argparse
import sqlite3
import urllib.request
from pathlib import Path

import pandas as pd

BASE = "https://data.dft.gov.uk/road-accidents-safety-data/dft-road-casualty-statistics-{}-2023.csv"

SEVERITY = {1: "Fatal", 2: "Serious", 3: "Slight"}
WEEKDAY = {1: "Sunday", 2: "Monday", 3: "Tuesday", 4: "Wednesday", 5: "Thursday", 6: "Friday", 7: "Saturday"}
LIGHT = {1: "Daylight", 4: "Darkness - lights lit", 5: "Darkness - lights unlit",
         6: "Darkness - no lighting", 7: "Darkness - lighting unknown"}
WEATHER = {1: "Fine", 2: "Raining", 3: "Snowing", 4: "Fine with high winds", 5: "Raining with high winds",
           6: "Snowing with high winds", 7: "Fog or mist", 8: "Other", 9: "Unknown"}
AREA = {1: "Urban", 2: "Rural", 3: "Unallocated"}
# vehicle_type code -> (label, group)
VEHICLE = {1: ("Pedal cycle", "Pedal cycle"), 2: ("Motorcycle 50cc and under", "Motorcycle"),
           3: ("Motorcycle 125cc and under", "Motorcycle"), 4: ("Motorcycle 125-500cc", "Motorcycle"),
           5: ("Motorcycle over 500cc", "Motorcycle"), 8: ("Taxi / private hire car", "Car"), 9: ("Car", "Car"),
           10: ("Minibus", "Bus or coach"), 11: ("Bus or coach", "Bus or coach"), 16: ("Ridden horse", "Other"),
           17: ("Agricultural vehicle", "Other"), 18: ("Tram", "Other"), 19: ("Van (3.5t or under)", "Van"),
           20: ("Goods vehicle 3.5-7.5t", "Heavy goods"), 21: ("Goods vehicle over 7.5t", "Heavy goods"),
           22: ("Mobility scooter", "Other"), 23: ("Electric motorcycle", "Motorcycle"),
           90: ("Other vehicle", "Other"), 97: ("Motorcycle, unknown size", "Motorcycle"),
           98: ("Goods vehicle, unknown weight", "Heavy goods")}
CASUALTY_CLASS = {1: "Driver or rider", 2: "Passenger", 3: "Pedestrian"}

COLLISION_COLS = ["collision_index", "collision_severity", "number_of_vehicles", "number_of_casualties", "date",
                  "day_of_week", "time", "police_force", "local_authority_ons_district", "road_type",
                  "speed_limit", "light_conditions", "weather_conditions", "urban_or_rural_area",
                  "latitude", "longitude"]
VEHICLE_COLS = ["collision_index", "vehicle_reference", "vehicle_type", "sex_of_driver", "age_of_driver"]
CASUALTY_COLS = ["collision_index", "vehicle_reference", "casualty_reference", "casualty_class",
                 "sex_of_casualty", "age_of_casualty", "casualty_severity", "casualty_type"]


def lookup(mapping, key, value):
    return pd.DataFrame({key: list(mapping), value: list(mapping.values())})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", default="data")
    ap.add_argument("--out", default="roads.db")
    args = ap.parse_args()
    cache = Path(args.cache)
    cache.mkdir(exist_ok=True)
    for kind in ("collision", "vehicle", "casualty"):
        path = cache / f"dft_{kind}_2023.csv"
        if not path.exists():
            print("downloading", kind)
            urllib.request.urlretrieve(BASE.format(kind), path)

    collisions = pd.read_csv(cache / "dft_collision_2023.csv", usecols=COLLISION_COLS, low_memory=False)
    collisions["date"] = pd.to_datetime(collisions["date"], format="%d/%m/%Y").dt.strftime("%Y-%m-%d")
    vehicles = pd.read_csv(cache / "dft_vehicle_2023.csv", usecols=VEHICLE_COLS, low_memory=False)
    casualties = pd.read_csv(cache / "dft_casualty_2023.csv", usecols=CASUALTY_COLS, low_memory=False)

    veh_labels = pd.DataFrame({"vehicle_type": list(VEHICLE),
                               "vehicle_label": [v[0] for v in VEHICLE.values()],
                               "vehicle_group": [v[1] for v in VEHICLE.values()]})

    out = Path(args.out)
    out.unlink(missing_ok=True)
    con = sqlite3.connect(out)
    collisions.to_sql("collisions", con, index=False)
    vehicles.to_sql("vehicles", con, index=False)
    casualties.to_sql("casualties", con, index=False)
    lookup(SEVERITY, "severity", "severity_name").to_sql("severities", con, index=False)
    lookup(WEEKDAY, "day_of_week", "weekday_name").to_sql("weekdays", con, index=False)
    lookup(LIGHT, "light_conditions", "light_name").to_sql("light", con, index=False)
    lookup(WEATHER, "weather_conditions", "weather_name").to_sql("weather", con, index=False)
    lookup(AREA, "urban_or_rural_area", "area_name").to_sql("areas", con, index=False)
    lookup(CASUALTY_CLASS, "casualty_class", "class_name").to_sql("casualty_classes", con, index=False)
    veh_labels.to_sql("vehicle_types", con, index=False)
    con.execute("CREATE INDEX ix_veh ON vehicles (collision_index)")
    con.execute("CREATE INDEX ix_cas ON casualties (collision_index)")
    con.execute("CREATE UNIQUE INDEX ix_col ON collisions (collision_index)")
    con.commit()
    print(f"wrote {out}: {len(collisions):,} collisions, {len(vehicles):,} vehicles, {len(casualties):,} casualties")


if __name__ == "__main__":
    main()

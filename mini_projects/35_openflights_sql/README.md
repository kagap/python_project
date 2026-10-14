# The World's Airline Network

3,425 airports and 67,663 airline routes (a snapshot of about 2014) in plain SQL, with SQL only (degree and concentration with window functions, great-circle distances with the haversine formula, **recursive CTEs** that walk the network to count flights to anywhere and rebuild the shortest itinerary from Warsaw, and an audit of the data itself) on the OpenFlights database.
Python only runs the queries and draws the charts.

- [`openflights_queries.sql`](openflights_queries.sql): every query, runnable in any SQLite client (needs SQLite 3.35 or newer for the trigonometric functions)
- [`openflights.py`](openflights.py): the same queries plus the charts, as a script
- [`build_db.py`](build_db.py): downloads the data and builds the SQLite database (three small text files, about 4 MB, from the OpenFlights repository)
- [Rendered notebook with results and takeaways](https://kagap.github.io/notebooks/openflights.html)

The route data is a **past snapshot** (the file was last changed in 2017), and the source has some traps that the project handles or points out: airline codes shared by two airlines (so airlines are matched only on codes with one active airline), codeshare flights (excluded from the hub analysis), routes pointing at airports missing from the airport list (so airports are joined on their IATA code).

The data and the database are not stored in this repo; the build script fetches them. The data is [OpenFlights](https://openflights.org/data.html), under the Open Database License.

## Run it

```bash
pip install -r ../requirements.txt
python build_db.py --out openflights.db
OPENFLIGHTS_DB=openflights.db python openflights.py
```

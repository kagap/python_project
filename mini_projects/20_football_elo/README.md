# Elo ratings for Europe's top five leagues

A notebook-style analysis on real data from football-data.co.uk (five leagues, 2010/11 to 2024/25): every step is explained in the
[rendered notebook](https://kagap.github.io/notebooks/elo_football.html), together with the results and takeaways.

- [`elo_football.py`](elo_football.py): the whole analysis as a script (the same code as the notebook)
- `chart.png`: one of the charts it produces

The script downloads its data (about 9 MB of CSV files) into the folder named by the `FOOTBALL_DIR` environment variable the first time it runs,
so nothing needs to be prepared by hand.

## Run it

```bash
pip install -r ../requirements.txt
FOOTBALL_DIR=./football_data python elo_football.py
```

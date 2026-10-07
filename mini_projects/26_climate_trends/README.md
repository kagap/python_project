# Climate trends in twelve cities

A notebook-style analysis on real data from the Open-Meteo historical weather API (ERA5 reanalysis, 2010-2024), with a focus on seasonal decomposition, trends with confidence intervals, extremes with robust statistics and a count of significant results against what chance would give.
Every step is explained in the [rendered notebook](https://kagap.github.io/notebooks/climate_trends.html), together with the results and takeaways.

- [`climate_trends.py`](climate_trends.py): the whole analysis as a script (the same code as the notebook)
- `chart.png`: one of the charts it produces

The script downloads its data (about 2 MB from a rate-limited API, a few minutes on the first run) into the folder named by the `CLIMATE_DIR` environment variable the first time it runs,
so nothing needs to be prepared by hand.

## Run it

```bash
pip install -r ../requirements.txt
CLIMATE_DIR=./climate_data python climate_trends.py
```

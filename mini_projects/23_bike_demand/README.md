# Hourly bike-sharing demand forecasting

A notebook-style analysis on real data from the UCI Bike Sharing dataset (Capital Bikeshare, Washington D.C., 2011-2012):
forecasting the demand of every hour of tomorrow with gradient-boosted trees, honest time-based evaluation, conformal prediction intervals and an analysis of where the
forecast fails (holidays and Hurricane Sandy). Every step is explained in the
[rendered notebook](https://kagap.github.io/notebooks/bike_demand.html), together with the results and takeaways.

- [`bike_demand.py`](bike_demand.py): the whole analysis as a script (the same code as the notebook)
- `chart.png`: one of the charts it produces

The script downloads its data (about 0.3 MB) into the folder named by the `BIKE_DIR` environment variable the first time it runs,
so nothing needs to be prepared by hand.

## Run it

```bash
pip install -r ../requirements.txt
BIKE_DIR=./bike_data python bike_demand.py
```

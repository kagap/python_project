# Calibrating low-cost air quality sensors

A notebook-style analysis on real data from the UCI Air Quality dataset (a year of hourly sensor and analyser readings from an Italian city), with a focus on a missing-value code, cross-sensitivity, calibration tested six months later, and weekly re-calibration against a reference analyser.
Every step is explained in the [rendered notebook](https://kagap.github.io/notebooks/air_quality_calibration.html), together with the results and takeaways.

- [`air_quality_calibration.py`](air_quality_calibration.py): the whole analysis as a script (the same code as the notebook)
- `chart.png`: one of the charts it produces

The script downloads its data (about 1.5 MB zip) into the folder named by the `AIR_DIR` environment variable the first time it runs,
so nothing needs to be prepared by hand.

## Run it

```bash
pip install -r ../requirements.txt
AIR_DIR=./air_data python air_quality_calibration.py
```

# Credit card default risk model

A notebook-style analysis on real data from the UCI Default of Credit Card Clients dataset (30,000 clients of a Taiwanese bank), with a focus on honest probabilities (calibration), a cut-off chosen by the cost of mistakes, and an audit by sex, education, marital status and age.
Every step is explained in the [rendered notebook](https://kagap.github.io/notebooks/credit_default.html), together with the results and takeaways.

- [`credit_default.py`](credit_default.py): the whole analysis as a script (the same code as the notebook)
- `chart.png`: one of the charts it produces

The script downloads its data (about 5.5 MB zip) into the folder named by the `CREDIT_DIR` environment variable the first time it runs,
so nothing needs to be prepared by hand.

## Run it

```bash
pip install -r ../requirements.txt
CREDIT_DIR=./credit_data python credit_default.py
```

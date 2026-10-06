# Customer segmentation with K-means on bank clients

A notebook-style analysis on real data from UCI Bank Marketing (45,211 clients): every step is explained in the
[rendered notebook](https://kagap.github.io/notebooks/bank_segments.html), together with the results and takeaways.

- [`bank_segments.py`](bank_segments.py): the whole analysis as a script (the same code as the notebook)
- `chart.png`: one of the charts it produces

The script downloads its data (about 1 MB zip) into the folder named by the `BANK_DIR` environment variable the first time it runs,
so nothing needs to be prepared by hand.

## Run it

```bash
pip install -r ../requirements.txt
BANK_DIR=./bank_data python bank_segments.py
```

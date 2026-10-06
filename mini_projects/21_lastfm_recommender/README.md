# Music recommender on Last.fm listening data

A notebook-style analysis on real data from GroupLens HetRec 2011 Last.fm (1,892 users, 17,632 artists): every step is explained in the
[rendered notebook](https://kagap.github.io/notebooks/lastfm_recommender.html), together with the results and takeaways.

- [`lastfm_recommender.py`](lastfm_recommender.py): the whole analysis as a script (the same code as the notebook)
- `chart.png`: one of the charts it produces

The script downloads its data (about 2.6 MB zip) into the folder named by the `LASTFM_DIR` environment variable the first time it runs,
so nothing needs to be prepared by hand.

## Run it

```bash
pip install -r ../requirements.txt
LASTFM_DIR=./listening_data python lastfm_recommender.py
```

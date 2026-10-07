# SMS spam filter

A notebook-style analysis on real data from the UCI SMS Spam Collection (5,574 text messages), with a focus on duplicate removal before splitting, a high-precision cut-off, an error analysis and a stress test against simple disguises.
Every step is explained in the [rendered notebook](https://kagap.github.io/notebooks/sms_spam.html), together with the results and takeaways.

- [`sms_spam.py`](sms_spam.py): the whole analysis as a script (the same code as the notebook)
- `chart.png`: one of the charts it produces

The script downloads its data (about 0.2 MB zip) into the folder named by the `SMS_DIR` environment variable the first time it runs,
so nothing needs to be prepared by hand.

## Run it

```bash
pip install -r ../requirements.txt
SMS_DIR=./sms_data python sms_spam.py
```

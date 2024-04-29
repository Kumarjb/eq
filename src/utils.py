"""
Utility to calculate implied volatility using Black-Scholes model.
(can be improved)
"""

import numpy as np
import pyarrow as pa
from qablet.base.utils import Forwards, discounter_from_dataset
from qablet_contracts.timetable import TS_EVENT_SCHEMA, py_to_ts
from scipy.stats import norm

N = norm.cdf


def bs_opt(F, K, T, vol, is_call):
    d1 = (np.log(F / K) + (0.5 * vol**2) * T) / (vol * np.sqrt(T))
    d2 = d1 - vol * np.sqrt(T)
    if is_call:
        return F * N(d1) - K * N(d2)
    else:
        return K * N(-d2) - F * N(-d1)


def bs_vega(F, K, T, sigma):
    d1 = (np.log(F / K) + (0.5 * sigma**2) * T) / (sigma * np.sqrt(T))
    return F * N(d1) * np.sqrt(T)


def find_vol(target_value, F, K, T, is_call):
    MAX_ITERATIONS = 200
    PRECISION = 1.0e-6
    sigma = 0.5
    for i in range(0, MAX_ITERATIONS):
        price = bs_opt(F, K, T, sigma, is_call)
        vega = bs_vega(F, K, T, sigma)
        diff = target_value - price  # our root
        if abs(diff) < PRECISION:
            return sigma
        sigma = sigma + diff / vega  # f(x) / f'(x)
    return sigma


def iv_surface(ticker, model, dataset, strikes, expirations):
    # Create a timetable that pays forwards at given expirations
    events = [
        {
            "track": "",
            "time": dt,
            "op": "+",
            "quantity": 1,
            "unit": ticker,
        }
        for dt in expirations
    ]

    events_table = pa.RecordBatch.from_pylist(events, schema=TS_EVENT_SCHEMA)
    fwd_timetable = {"events": events_table, "expressions": {}}

    discounter = discounter_from_dataset(dataset)

    _, stats = model.price(fwd_timetable, dataset)
    # cashflows for track 0, all events
    cf = stats["CASHFLOW"][0]

    asset_fwds = Forwards(dataset["ASSETS"][ticker])

    iv_mat = np.zeros((len(expirations), len(strikes)))
    for i, exp in enumerate(expirations):
        prc_ts = dataset["PRICING_TS"]
        T = (py_to_ts(exp).value - prc_ts) / (365.25 * 24 * 3600 * 1e3)
        df = discounter.discount(T)
        fwd = asset_fwds.forward(T)

        is_call = strikes > fwd
        ic = is_call[..., None]

        event_cf = cf[i] / df
        KC = strikes[..., None]  # Turn into a column vector
        pay = np.where(ic, event_cf - KC, KC - event_cf)
        prices = np.maximum(pay, 0).mean(axis=1)

        iv_mat[i, :] = [
            find_vol(p, fwd, k, T, ic)
            for p, k, ic in zip(prices, strikes, is_call)
        ]

    return iv_mat

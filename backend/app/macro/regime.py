"""Regime overlays: map macro/sentiment features to an exposure multiplier in [floor, 1] for the base strategy.

  rule_overlay : transparent flags, no model. Each stressed condition cuts exposure by 25%.
  ml_overlay   : walk-forward regularised logistic regression that predicts whether the base strategy's next
                 `horizon`-day return is positive (meta-labelling). Strictly out-of-sample: the model only ever
                 trains on rows whose labels were fully known `horizon` days earlier, and is refit every
                 `retrain` days.

Both only scale the *size* of the existing long-short book; they never pick coins. The BTC gate still decides
whether the strategy trades at all.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from .features import expanding_z

# (feature, direction) – a flag is raised when the expanding z-score is above Z in the stressed direction
RULE_FLAGS = [("vix", +1, "VIX spike"), ("dxy_chg30", +1, "US dollar surging"), ("y10_chg30", +1, "10y yield surging"),
              ("btc_vol", +1, "BTC volatility high"), ("fng_chg14", -1, "Sentiment collapsing")]
FLOOR = 0.25
Z = 1.5


def rule_flags(feat: pd.DataFrame) -> pd.DataFrame:
    flags = {}
    for col, sign, label in RULE_FLAGS:
        if col in feat:
            flags[label] = (sign * expanding_z(feat[col]) > Z).fillna(False)
    return pd.DataFrame(flags, index=feat.index)


def rule_overlay(feat: pd.DataFrame) -> pd.Series:
    flags = rule_flags(feat)
    n = flags.sum(axis=1) if not flags.empty else pd.Series(0, index=feat.index)
    return (1 - 0.25 * n).clip(lower=FLOOR).rename("exposure")


def ml_overlay(feat: pd.DataFrame, strat_ret: pd.Series, gate: pd.Series, horizon: int = 5, retrain: int = 30,
               min_train: int = 365, lo: float = 0.40, hi: float = 0.55) -> tuple[pd.Series, dict]:
    """Returns (exposure series, diagnostics). Days before the first model, or with missing features, get 1.0."""
    cols = [c for c in feat.columns if feat[c].notna().sum() > min_train]
    X = feat[cols]
    r = strat_ret.reindex(X.index).fillna(0.0)
    fwd = r[::-1].rolling(horizon, min_periods=horizon).sum()[::-1].shift(-1)    # return over days t+1..t+h
    y = (fwd > 0).astype(float).where(fwd.notna())
    usable = gate.reindex(X.index).fillna(False) & X.notna().all(axis=1) & y.notna()
    expo = pd.Series(1.0, index=X.index, name="exposure")
    probs = pd.Series(np.nan, index=X.index)
    n = len(X)
    for start in range(min_train, n, retrain):
        train_end = start - horizon                                  # embargo: labels must be known at `start`
        tr = usable.iloc[:train_end]
        if tr.sum() < 100 or y.iloc[:train_end][tr].nunique() < 2:
            continue
        model = make_pipeline(StandardScaler(), LogisticRegression(C=0.1, max_iter=500))
        model.fit(X.iloc[:train_end][tr], y.iloc[:train_end][tr])
        block = slice(start, min(start + retrain, n))
        ok = X.iloc[block].notna().all(axis=1)
        if ok.any():
            p = model.predict_proba(X.iloc[block][ok])[:, 1]
            probs.loc[ok.index[ok]] = p
    scored = probs.dropna()
    expo.loc[scored.index] = ((scored - lo) / (hi - lo)).clip(FLOOR, 1.0)
    ev = usable & probs.notna()
    diag = {"features": cols, "scored_days": int(len(scored)), "oos_days_evaluated": int(ev.sum()),
            "oos_auc": float(roc_auc_score(y[ev], probs[ev])) if ev.sum() > 50 and y[ev].nunique() == 2 else None,
            "avg_exposure": float(expo[scored.index].mean()) if len(scored) else 1.0}
    return expo, diag

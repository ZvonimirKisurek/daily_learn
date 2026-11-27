"""Train, judge and serve the short-term Up/Down model.

    history (windows + snapshots + candles + Claude's daily view) ──► rows of FEATURES, label = did Up win?
                     │ split by TIME: oldest 80% of windows train, newest 20% judge (no peeking at the future)
                     ▼
    candidates: logistic regression (a straight line) and a small neural network (MLP 32-16, ReLU, Adam)
                     │ judged on the newest windows against two baselines that cost nothing:
                     │   the MARKET (Polymarket's own price) and the ANALYTIC random walk Phi(z)
                     ▼
    champion = best log loss, promoted only if it beats the current champion on the same newest windows
                     │ a BACKTEST replays the alert rule on those windows: >= 90% sure, edge over price + fee
                     ▼
    "proven" = the model beats the market's log loss AND its backtest made money on >= N bets. Until then every alert
    is labelled PAPER. That gate is the honest part: a model that only copies the crowd never earns real-money pings.

Weights are stored as plain numbers in JSON and run with numpy at serving time (no pickles, no sklearn needed to
predict). Retraining runs every few hours, so the model keeps learning as new windows resolve.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import numpy as np

from app.config import Settings, get_settings
from app.db import get_pool
from app.polymarket import fee_per_share
from app.bitcoin import candles as cdl
from app.updown import features as ft

log = logging.getLogger(__name__)
ROW_SPACING_S = 20  # at most one training row per window every 20 s: neighbouring rows are near-duplicates
BACKFILL_SPREAD = 0.01  # history has trade prices, not asks: assume paying 1c above them (conservative)


# ----------------------------------------------------------------------------------------------- the alert rule
def side_cost(price: float | None, rate: float, exponent: float) -> float | None:
    return None if price is None or not 0 < price < 1 else price + fee_per_share(price, rate, exponent)


def signal(p_up: float, up_cost: float | None, down_cost: float | None, secs_left: float, s: Settings) -> tuple[str, float, float] | None:
    """(side, P(side), cost of one share) when the model is >= the threshold on a side AND that beats the cost by the
    minimum edge AND there is still time to act. Shared by the live loop and the backtest, so both mean the same."""
    if secs_left < s.updown_alert_min_secs:
        return None
    for side, p, cost in (("up", p_up, up_cost), ("down", 1 - p_up, down_cost)):
        if cost is not None and p >= s.updown_alert_prob and p - cost >= s.updown_min_edge:
            return side, p, cost
    return None


# ----------------------------------------------------------------------------------------------- data
def load_rows(days: int, until: datetime | None = None) -> list[dict]:
    until = until or datetime.now(timezone.utc)
    with get_pool().connection() as conn:
        return conn.execute(
            """select s.slug, extract(epoch from s.at) as at, s.btc, s.up_price, s.up_ask, s.down_ask, s.source,
                      w.minutes, extract(epoch from w.start_at) as start, extract(epoch from w.end_at) as end_,
                      w.price_to_beat, w.basis, w.outcome, w.fee_rate, w.fee_exponent
               from updown_snapshots s join updown_windows w using (slug)
               where w.outcome is not null and w.price_to_beat is not null and w.start_at >= %s and w.end_at <= %s
                 and s.at >= w.start_at and s.at < w.end_at - interval '2 seconds'
               order by w.start_at, s.at""", (until - timedelta(days=days), until)).fetchall()


def load_regime() -> ft.Regime:
    with get_pool().connection() as conn:
        rows = conn.execute("select created_at, current_price, predicted_price_1d, confidence_1d, sentiment_score from bitcoin_predictions").fetchall()
    return ft.regime_from_rows(rows)


def matrix(rows: list[dict], candles: cdl.Series, regime: ft.Regime) -> dict:
    """Rows -> X, y and what evaluation needs (window, market price, costs, time left), thinned to ROW_SPACING_S."""
    X, y, win, pm, pa, up_cost, down_cost, left = [], [], [], [], [], [], [], []
    last: dict[str, float] = {}
    for r in rows:
        at = float(r["at"])
        if at - last.get(r["slug"], -1e9) < ROW_SPACING_S:
            continue
        last[r["slug"]] = at
        x, p_an = ft.build(at=at, start=float(r["start"]), end=float(r["end_"]), minutes=int(r["minutes"]), price=float(r["btc"]),
                           price_to_beat=float(r["price_to_beat"]), p_market=float(r["up_price"]), candles=candles, regime=regime)
        rate, expo = float(r["fee_rate"]), float(r["fee_exponent"])
        live = r["source"] == "live" and r["up_ask"] is not None
        ua = float(r["up_ask"]) if live else min(float(r["up_price"]) + BACKFILL_SPREAD, 0.99)
        da = float(r["down_ask"]) if live and r["down_ask"] is not None else min(1 - float(r["up_price"]) + BACKFILL_SPREAD, 0.99)
        X.append(x); y.append(1 if r["outcome"] == "up" else 0); win.append(float(r["start"]) + int(r["minutes"]) / 100)
        pm.append(float(r["up_price"])); pa.append(p_an); up_cost.append(side_cost(ua, rate, expo)); down_cost.append(side_cost(da, rate, expo))
        left.append(float(r["end_"]) - at)
    return {"X": np.array(X), "y": np.array(y), "window": np.array(win), "p_market": np.array(pm), "p_analytic": np.array(pa),
            "up_cost": up_cost, "down_cost": down_cost, "secs_left": np.array(left)}


# ----------------------------------------------------------------------------------------------- scoring
def scores(p: np.ndarray, y: np.ndarray) -> dict:
    p = np.clip(p, 1e-4, 1 - 1e-4)
    sure = np.maximum(p, 1 - p) >= 0.9
    return {"n": int(len(y)), "log_loss": round(float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p))), 5),
            "brier": round(float(np.mean((p - y) ** 2)), 5), "accuracy": round(float(np.mean((p >= 0.5) == (y == 1))), 4),
            "sure90_n": int(sure.sum()), "sure90_hit": round(float(np.mean((p[sure] >= 0.5) == (y[sure] == 1))), 4) if sure.any() else None}


def backtest(p: np.ndarray, d: dict, s: Settings) -> dict:
    """Replay the alert rule: the first qualifying moment of each window is one $1 bet at the cost then."""
    taken: set[tuple[float, str]] = set()
    bets = wins = 0
    pnl = 0.0
    for i in range(len(p)):
        sig = signal(float(p[i]), d["up_cost"][i], d["down_cost"][i], float(d["secs_left"][i]), s)
        if not sig or (d["window"][i], sig[0]) in taken:
            continue
        taken.add((d["window"][i], sig[0]))
        won = (sig[0] == "up") == (d["y"][i] == 1)
        bets += 1; wins += int(won)
        pnl += (1 / sig[2] - 1) if won else -1.0
    return {"bets": bets, "wins": wins, "hit_rate": round(wins / bets, 4) if bets else None, "pnl_per_dollar": round(pnl, 3),
            "roi": round(pnl / bets, 4) if bets else None}


# ----------------------------------------------------------------------------------------------- the networks, as data
def to_params(kind: str, mean: np.ndarray, std: np.ndarray, layers: list[tuple[np.ndarray, np.ndarray]]) -> dict:
    return {"kind": kind, "features": list(ft.FEATURES), "mean": mean.tolist(), "std": std.tolist(),
            "layers": [{"W": W.tolist(), "b": b.tolist()} for W, b in layers]}


def predict(params: dict, X: np.ndarray) -> np.ndarray:
    """Forward pass: standardise, ReLU hidden layers, sigmoid output. Same maths as sklearn's MLP / logistic regression."""
    h = (np.atleast_2d(X) - np.array(params["mean"])) / np.array(params["std"])
    layers = params["layers"]
    for i, layer in enumerate(layers):
        h = h @ np.array(layer["W"]) + np.array(layer["b"])
        if i < len(layers) - 1:
            h = np.maximum(h, 0)
    return 1 / (1 + np.exp(-np.clip(h[:, 0], -30, 30)))


def fit(kind: str, X: np.ndarray, y: np.ndarray) -> dict:
    from sklearn.linear_model import LogisticRegression
    from sklearn.neural_network import MLPClassifier

    mean, std = X.mean(axis=0), X.std(axis=0)
    std[std < 1e-9] = 1.0
    Z = (X - mean) / std
    if kind == "logistic":
        m = LogisticRegression(C=0.5, max_iter=2000).fit(Z, y)
        return to_params(kind, mean, std, [(m.coef_.T, m.intercept_)])
    m = MLPClassifier(hidden_layer_sizes=(32, 16), activation="relu", alpha=1e-3, learning_rate_init=1e-3, early_stopping=True,
                      validation_fraction=0.15, n_iter_no_change=10, max_iter=300, random_state=0).fit(Z, y)
    return to_params(kind, mean, std, list(zip(m.coefs_, m.intercepts_)))


# ----------------------------------------------------------------------------------------------- champion
def champion() -> dict | None:
    with get_pool().connection() as conn:
        return conn.execute("select version, kind, params, metrics, created_at, review from updown_models where is_champion").fetchone()


def train(settings: Settings | None = None, *, min_windows: int = 60) -> dict:
    """Train both candidates on the history, judge them on the newest windows, promote the better one if it beats the
    current champion there. Returns the report (also stored with the model)."""
    s = settings or get_settings()
    rows = load_rows(s.updown_train_days)
    if not rows:
        return {"status": "no_data", "note": "No resolved windows yet: run `updown-backfill` or let the live loop collect some."}
    first, last = min(float(r["start"]) for r in rows), max(float(r["end_"]) for r in rows)
    series = cdl.load(datetime.fromtimestamp(first - 86400 - 120, timezone.utc), datetime.fromtimestamp(last, timezone.utc))
    d = matrix(rows, series, load_regime())
    windows = np.unique(d["window"])
    if len(windows) < min_windows:
        return {"status": "too_little_data", "windows": int(len(windows)), "note": f"Need at least {min_windows} resolved windows to train."}
    cut = np.quantile(windows, 0.8)
    tr, va = d["window"] < cut, d["window"] >= cut
    if len(np.unique(d["y"][tr])) < 2:
        return {"status": "one_class", "note": "Training windows all resolved the same way; wait for more data."}

    base = {"market": scores(d["p_market"][va], d["y"][va]), "analytic": scores(d["p_analytic"][va], d["y"][va])}
    cands = {}
    for kind in ("logistic", "mlp"):
        params = fit(kind, d["X"][tr], d["y"][tr])
        p = predict(params, d["X"][va])
        cands[kind] = (params, scores(p, d["y"][va]), backtest(p, {k: (v[va] if isinstance(v, np.ndarray) else [x for x, m in zip(v, va) if m]) for k, v in d.items()}, s))
    kind = min(cands, key=lambda k: cands[k][1]["log_loss"])
    params, sc, bt = cands[kind]

    current = champion()
    cur_ll = None
    if current and current["params"].get("features") == list(ft.FEATURES):
        cur_ll = scores(predict(current["params"], d["X"][va]), d["y"][va])["log_loss"]
    promote = cur_ll is None or sc["log_loss"] <= cur_ll + 1e-4
    proven = bool(sc["log_loss"] < base["market"]["log_loss"] and bt["bets"] >= s.updown_trust_min_bets and bt["pnl_per_dollar"] > 0)
    metrics = {"windows_train": int(len(np.unique(d["window"][tr]))), "windows_validation": int(len(np.unique(d["window"][va]))),
               "rows_train": int(tr.sum()), "rows_validation": int(va.sum()), "model": sc, "baselines": base, "backtest": bt,
               "other": {k: v[1] for k, v in cands.items() if k != kind}, "champion_log_loss_before": cur_ll,
               "promoted": promote, "proven": proven, "alert_rule": {"prob": s.updown_alert_prob, "min_edge": s.updown_min_edge,
                                                                     "min_secs": s.updown_alert_min_secs}}
    version = f"{kind}-{datetime.now(timezone.utc):%Y%m%d-%H%M%S-%f}"
    with get_pool().connection() as conn, conn.transaction():
        if promote:
            conn.execute("update updown_models set is_champion = false where is_champion")
        conn.execute("insert into updown_models (version, kind, params, metrics, is_champion) values (%s, %s, %s, %s, %s)",
                     (version, kind, _json(params), _json(metrics), promote))
        conn.execute("""delete from updown_models where not is_champion and version not in
                        (select version from updown_models order by created_at desc limit 30)""")
    log.info("updown model %s: log loss %.4f (market %.4f), backtest %s, promoted=%s proven=%s", version, sc["log_loss"],
             base["market"]["log_loss"], bt, promote, proven)
    return {"status": "trained", "version": version, "kind": kind, **metrics}


def _json(v):
    """JSON for Postgres; numpy scalars (np.int64, np.float64) become plain numbers."""
    import json

    from psycopg.types.json import Jsonb
    return Jsonb(json.loads(json.dumps(v, default=lambda o: o.item() if hasattr(o, "item") else str(o))))


def describe(metrics: dict) -> str:
    """One paragraph a person can read: how the model did and whether it may send real-money pings."""
    if "model" not in metrics:
        return "No validation report for this model yet."
    m, mk, bt = metrics["model"], metrics["baselines"]["market"], metrics["backtest"]
    verdict = ("PROVEN: it beat the market's own odds and its alert rule made money in the backtest." if metrics["proven"] else
               "NOT PROVEN YET: alerts are sent as PAPER signals only (do not bet real money on them).")
    hit = f", right {bt['wins']} of {bt['bets']} times, {bt['pnl_per_dollar']:+.2f} per $1 bet" if bt["bets"] else ", no alerts would have fired"
    return (f"Trained on {metrics['windows_train']} windows, tested on the newest {metrics['windows_validation']}. Accuracy "
            f"{m['accuracy'] * 100:.1f}% (market {mk['accuracy'] * 100:.1f}%), log loss {m['log_loss']:.3f} vs the market's "
            f"{mk['log_loss']:.3f} (lower is better). Backtest of the >= {metrics['alert_rule']['prob'] * 100:.0f}% rule{hit}. {verdict}")



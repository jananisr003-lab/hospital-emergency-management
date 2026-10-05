"""
AI waiting-time prediction + overload (surge) detection.

Inputs (9): patients waiting, triage level (1 Critical, 2 Urgent, 3 Routine), doctors available,
doctor's average consult time, patients in treatment, available beds, department,
historical waiting time, current arrival rate.

Model: scikit-learn GradientBoostingRegressor.
Training data: wait_history.csv if you provide it (real hospital logs), otherwise a SYNTHETIC
queueing-style dataset so the demo works. Replace with real logs before relying on predictions.
CSV columns: waiting,triage,doctors,avg_consult,in_treatment,beds,arrival_rate,hist_wait,dept,wait_min
"""
import csv, os
import numpy as np

try:
    from sklearn.ensemble import GradientBoostingRegressor
    from sklearn.model_selection import train_test_split
except ImportError:  # falls back to the plain formula
    GradientBoostingRegressor = None

BASE = {"General Medicine": 10, "Cardiology": 15, "Pediatrics": 12, "Orthopedics": 14, "Gynecology": 15}
DEPTS = list(BASE)
FEATS = ["waiting", "triage", "doctors", "avg_consult", "in_treatment", "beds", "arrival_rate", "hist_wait"]
CSV = os.path.join(os.path.dirname(os.path.abspath(__file__)), "wait_history.csv")


def _truth(w, t, doc, avg, it, beds, arr, hist):
    """Queueing-style rule used to generate synthetic history and as fallback."""
    mult = np.select([t == 1, t == 2], [.05, .45], 1.0)       # higher priority skips the line
    soft = np.select([t == 1, t == 2], [.1, .6], 1.0)          # and feels surges and history less
    d = np.maximum(doc, .5)
    return (w * mult * avg / d + soft * (np.maximum(0, arr * avg / 60 / d - .7) * avg * 3 + (beds < 3) * 8 + .2 * hist)
            + it * avg * .15 / d * soft)


def _synth(n=6000, seed=7):
    r = np.random.default_rng(seed)
    dept = r.integers(0, len(DEPTS), n)
    D = dict(waiting=r.poisson(9, n), triage=r.choice([1, 2, 3], n, p=[.1, .3, .6]), doctors=r.integers(0, 7, n),
             avg_consult=np.array([BASE[DEPTS[i]] for i in dept]) * r.uniform(.8, 1.3, n),
             in_treatment=r.integers(0, 5, n), beds=r.integers(0, 40, n), arrival_rate=r.gamma(3, 2.5, n),
             hist_wait=np.array([BASE[DEPTS[i]] for i in dept]) * 2.2 * r.uniform(.7, 1.3, n), dept=dept)
    y = _truth(D["waiting"], D["triage"], D["doctors"], D["avg_consult"], D["in_treatment"], D["beds"],
               D["arrival_rate"], D["hist_wait"])
    return D, np.clip(y * r.lognormal(0, .12, n), 0, 240)


def _load():
    if os.path.exists(CSV):
        rows = list(csv.DictReader(open(CSV)))
        if len(rows) >= 200:
            D = {f: np.array([float(r[f]) for r in rows]) for f in FEATS}
            D["dept"] = np.array([DEPTS.index(r["dept"]) for r in rows])
            return D, np.array([float(r["wait_min"]) for r in rows]), "real hospital logs (wait_history.csv)"
    D, y = _synth()
    return D, y, "synthetic demo data (add wait_history.csv for real logs)"


class QueueAI:
    def __init__(self):
        D, y, self.source = _load()
        self.n = len(y)
        self.hist = {d: float(np.mean(D["hist_wait"][D["dept"] == i])) for i, d in enumerate(DEPTS)}
        self.m, self.mae, self.q, self.imp = None, None, (0, 0), {}
        if GradientBoostingRegressor:
            X = self._X(D)
            Xt, Xv, yt, yv = train_test_split(X, y, test_size=.2, random_state=0)
            self.m = GradientBoostingRegressor(n_estimators=250, max_depth=3, learning_rate=.08, random_state=0).fit(Xt, yt)
            res = yv - self.m.predict(Xv)
            self.mae = float(np.mean(np.abs(res)))
            self.q = (float(np.quantile(res, .1)), float(np.quantile(res, .9)))
            fi = self.m.feature_importances_
            self.imp = {**dict(zip(FEATS, map(float, fi[:len(FEATS)]))), "department": float(fi[len(FEATS):].sum())}

    @staticmethod
    def _X(D):
        return np.column_stack([D[f] for f in FEATS] + [(D["dept"] == i) for i in range(len(DEPTS))]).astype(float)

    def predict(self, waiting, triage, doctors, avg_consult, in_treatment, beds, dept, arrival_rate, hist_wait=None):
        hist = self.hist.get(dept, 20) if hist_wait is None else hist_wait
        D = {k: np.array([v], dtype=float) for k, v in dict(waiting=waiting, triage=triage, doctors=doctors, avg_consult=avg_consult,
             in_treatment=in_treatment, beds=beds, arrival_rate=arrival_rate, hist_wait=hist).items()}
        D["dept"] = np.array([DEPTS.index(dept)])
        w = float(self.m.predict(self._X(D))[0]) if self.m else float(_truth(*(D[f] for f in
            ["waiting", "triage", "doctors", "avg_consult", "in_treatment", "beds", "arrival_rate", "hist_wait"]))[0])
        w = max(0.0, w)
        cap = doctors * 60 / avg_consult if doctors > 0 else 0
        util = arrival_rate / cap if cap else 9
        load = "critical" if doctors == 0 or w >= 90 else "high" if w >= 45 or util >= .9 else "normal"
        return dict(wait=round(w), low=round(max(0, w + self.q[0])), high=round(w + self.q[1]),
                    utilization=round(min(util, 9), 2), capacity=round(cap, 1), load=load)


AI = QueueAI()

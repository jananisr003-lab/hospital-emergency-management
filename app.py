"""
CareDesk - Hospital Emergency + Appointment Platform (Flask + SQLite, single file)
Run:  pip install flask  ->  python app.py  ->  http://127.0.0.1:5000
Demo logins (password demo1234):  patient@demo.com | doctor@demo.com | admin@demo.com
Not a medical device. Does not diagnose.
"""
import sqlite3, datetime as dt, os, re, secrets
from functools import wraps
from flask import Flask, request, jsonify, session, Response
from werkzeug.security import generate_password_hash as H, check_password_hash as C
from ai_queue import AI, DEPTS

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-change-me")
app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax")
DB = "platform.db"
SLOTS = [f"{h:02d}:{m:02d}" for h in range(9, 17) for m in (0, 30)]
AVG = {"General Medicine": 10, "Cardiology": 15, "Pediatrics": 12, "Orthopedics": 14, "Gynecology": 15}
ETYPES = ["Accident", "Chest Pain", "Breathing Problem", "Injury", "Fever", "Pregnancy Emergency", "Other"]
DSTAT = ["Available", "Busy", "On Emergency Duty", "Offline"]


def q(sql, a=()):
    c = sqlite3.connect(DB); c.row_factory = sqlite3.Row
    try: return [dict(r) for r in c.execute(sql, a).fetchall()]
    finally: c.close()


def x(sql, a=()):
    c = sqlite3.connect(DB)
    try:
        cur = c.execute(sql, a); c.commit(); return cur.lastrowid
    finally: c.close()


def now(): return dt.datetime.now().strftime("%Y-%m-%d %H:%M")
def note(uid, m): x("insert into notes(user_id,msg,created) values(?,?,?)", (uid, m, now()))
def audit(a): x("insert into audit(who,action,created) values(?,?,?)", (session.get("email", "anon"), a, now()))


def need(*roles):
    def deco(f):
        @wraps(f)
        def w(*a, **k):
            if "uid" not in session: return jsonify(error="Please log in first."), 401
            if roles and session["role"] not in roles: return jsonify(error="Your role cannot do this."), 403
            return f(*a, **k)
        return w
    return deco


@app.errorhandler(ValueError)
def bad(e): return jsonify(error="Please check the values you entered."), 400


def I(v, lo=0, hi=10**6):
    v = int(v)
    if not lo <= v <= hi: raise ValueError
    return v


def init():
    c = sqlite3.connect(DB)
    c.executescript("""
    create table if not exists users(id integer primary key, name text, email text unique, pw text, role text, doctor_id int);
    create table if not exists hospitals(id integer primary key, name text, dist real, addr text, phone text, beds int, bed_cap int,
        icu int, icu_cap int, vent int, wait_min int, facilities text);
    create table if not exists doctors(id integer primary key, name text, dept text, hospital_id int, exp int, fee int, status text);
    create table if not exists appts(id integer primary key, user_id int, doctor_id int, day text, slot text, type text, reason text,
        token text, status text default 'booked', rx text, room text);
    create unique index if not exists uslot on appts(doctor_id,day,slot) where status in ('booked','in_progress');
    create table if not exists emergencies(id integer primary key, user_id int, etype text, severity text, symptoms text,
        location text, hospital_id int, created text, status text default 'active');
    create table if not exists notes(id integer primary key, user_id int, msg text, created text);
    create table if not exists chat(id integer primary key, appt_id int, sender text, text text, created text);
    create table if not exists audit(id integer primary key, who text, action text, created text);""")
    c.commit(); c.close()
    if q("select 1 from hospitals"): return
    for h in [("City General Hospital", 2.4, "12 Main Road", "+91 422 000 1001", 14, 40, 3, 12, 4, 18, "Trauma, ICU, Pharmacy, Blood bank, MRI"),
              ("St. Mary Medical Centre", 4.1, "8 Lake View Road", "+91 422 000 1002", 2, 30, 1, 8, 1, 35, "Maternity, ICU, Pharmacy"),
              ("Sunrise Heart Institute", 6.8, "45 Hill Street", "+91 422 000 1003", 0, 25, 0, 15, 0, 55, "Cardiac ICU, Cath lab, Pharmacy"),
              ("Green Valley Hospital", 9.3, "3 Park Avenue", "+91 422 000 1004", 22, 60, 6, 14, 7, 12, "Trauma, Pediatrics, ICU, X-ray, CT")]:
        x("insert into hospitals(name,dist,addr,phone,beds,bed_cap,icu,icu_cap,vent,wait_min,facilities) values(?,?,?,?,?,?,?,?,?,?,?)", h)
    for d in [("Dr. Meena Rao", "General Medicine", 1, 14, 500, "Available"), ("Dr. Arjun Pillai", "Cardiology", 3, 18, 900, "Busy"),
              ("Dr. Sana Iqbal", "Pediatrics", 4, 9, 600, "Available"), ("Dr. Vikram Nair", "Orthopedics", 1, 12, 800, "On Emergency Duty"),
              ("Dr. Kavya Menon", "Gynecology", 2, 11, 700, "Available"), ("Dr. Rahul Das", "General Medicine", 4, 6, 400, "Offline")]:
        x("insert into doctors(name,dept,hospital_id,exp,fee,status) values(?,?,?,?,?,?)", d)
    pw = H("demo1234")
    for n, e, r, did in [("Demo Patient", "patient@demo.com", "patient", None), ("Dr. Meena Rao", "doctor@demo.com", "doctor", 1),
                         ("Hospital Admin", "admin@demo.com", "admin", None), ("Asha K", "asha@demo.com", "patient", None),
                         ("Ravi M", "ravi@demo.com", "patient", None), ("Priya S", "priya@demo.com", "patient", None)]:
        x("insert into users(name,email,pw,role,doctor_id) values(?,?,?,?,?)", (n, e, pw, r, did))
    today = dt.date.today().isoformat()
    for i, (uid, did, s) in enumerate([(4, 1, "09:00"), (5, 1, "09:30"), (6, 1, "10:00"), (1, 1, "11:00"), (4, 3, "10:30")]):
        d = q("select dept from doctors where id=?", (did,))[0]["dept"]
        x("insert into appts(user_id,doctor_id,day,slot,type,reason,token,room) values(?,?,?,?,?,?,?,?)",
          (uid, did, today, s, "Video" if i % 2 else "In-person", "Routine check", f"{d[0]}{101+i}", secrets.token_hex(4)))
    x("insert into emergencies(user_id,etype,severity,symptoms,location,hospital_id,created) values(4,'Accident','Serious','Leg injury after fall','Near Main Road',1,?)", (now(),))


def hstat(h): return "full" if h["beds"] == 0 or h["icu"] == 0 and h["beds"] < 3 else "limited" if h["beds"] <= 3 or h["icu"] == 0 else "available"


def enrich(a):
    d = q("select * from doctors where id=?", (a["doctor_id"],))[0]
    a["doctor"], a["dept"] = d["name"], d["dept"]
    h = q("select * from hospitals where id=?", (d["hospital_id"],))[0]
    a["hospital"] = h["name"]
    if a["status"] == "booked":
        em = q("select count(*) n from emergencies where hospital_id=? and status='active'", (h["id"],))[0]["n"]
        ahead = q("select count(*) n from appts where doctor_id=? and day=? and status in('booked','in_progress') and slot<?",
                  (a["doctor_id"], a["day"], a["slot"]))[0]["n"]
        avg = AVG.get(d["dept"], 12)
        t = dt.datetime.strptime(a["day"] + " " + a["slot"], "%Y-%m-%d %H:%M") + dt.timedelta(minutes=5 * em)
        f = live_feats(d["dept"]); f.update(waiting=ahead)
        a.update(ahead=ahead, wait=AI.predict(**f, triage=3)["wait"], avg=avg, predicted=t.strftime("%I:%M %p"))
    return a


BASE = "select a.*, u.name patient from appts a join users u on u.id=a.user_id "


# ---------- auth ----------
@app.post("/api/register")
def register():
    d = request.get_json(silent=True) or {}
    n, e, p = (d.get("name") or "").strip(), (d.get("email") or "").strip().lower(), d.get("password") or ""
    if not n or not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", e) or len(p) < 8:
        return jsonify(error="Enter a name, a valid email and a password of 8+ characters."), 400
    if q("select 1 from users where email=?", (e,)): return jsonify(error="That email is already registered."), 409
    uid = x("insert into users(name,email,pw,role) values(?,?,?,'patient')", (n, e, H(p)))
    session.update(uid=uid, role="patient", email=e, name=n); audit("register")
    return jsonify(ok=True)


@app.post("/api/login")
def login():
    d = request.get_json(silent=True) or {}
    u = q("select * from users where email=?", ((d.get("email") or "").strip().lower(),))
    if not u or not C(u[0]["pw"], d.get("password") or ""): return jsonify(error="Email or password is incorrect."), 401
    u = u[0]; session.update(uid=u["id"], role=u["role"], email=u["email"], name=u["name"], did=u["doctor_id"]); audit("login")
    return jsonify(ok=True)


@app.post("/api/logout")
def logout(): audit("logout"); session.clear(); return jsonify(ok=True)


@app.get("/api/me")
def me(): return jsonify(user=None if "uid" not in session else dict(name=session["name"], role=session["role"], id=session["uid"]))


# ---------- public data ----------
@app.get("/api/hospitals")
def hospitals():
    qs, md, av = request.args.get("q", "").lower(), float(request.args.get("maxdist", 999)), request.args.get("avail")
    out = []
    for h in q("select * from hospitals order by dist"):
        h["status"] = hstat(h)
        h["doctors"] = q("select count(*) n from doctors where hospital_id=? and status='Available'", (h["id"],))[0]["n"]
        if qs in (h["name"] + h["addr"] + h["facilities"]).lower() and h["dist"] <= md and not (av and h["beds"] == 0):
            out.append(h)
    return jsonify(out)


@app.get("/api/doctors")
def doctors():
    return jsonify(q("select d.*, h.name hospital from doctors d join hospitals h on h.id=d.hospital_id order by d.name"))


@app.get("/api/queue/<int:did>")
def queue(did):
    d = q("select * from doctors where id=?", (did,))[0]
    today = dt.date.today().isoformat()
    n = q("select count(*) n from appts where doctor_id=? and day=? and status in('booked','in_progress')", (did, today))[0]["n"]
    em = q("select count(*) n from emergencies where hospital_id=? and status='active'", (d["hospital_id"],))[0]["n"]
    avg = AVG.get(d["dept"], 12)
    return jsonify(queue=n, avg=avg, emergencies=em, wait=round(n * avg * (1 + .1 * em)), status=d["status"])


@app.get("/api/slots")
def slots():
    did, day = I(request.args.get("doctor_id")), request.args.get("day", "")
    taken = {r["slot"] for r in q("select slot from appts where doctor_id=? and day=? and status in('booked','in_progress')", (did, day))}
    n = dt.datetime.now()
    return jsonify([s for s in SLOTS if s not in taken and not (day == n.strftime("%Y-%m-%d") and s <= n.strftime("%H:%M"))])


# ---------- appointments ----------
def make_slot_ok(did, day, slot):
    dt.date.fromisoformat(day)
    if day < dt.date.today().isoformat() or slot not in SLOTS or not q("select 1 from doctors where id=?", (did,)): raise ValueError


@app.post("/api/appointments")
@need("patient")
def book():
    d = request.get_json(silent=True) or {}
    did, day, slot = I(d.get("doctor_id")), d.get("day", ""), d.get("slot", "")
    make_slot_ok(did, day, slot)
    typ = d.get("type")
    if typ not in ("In-person", "Video"): raise ValueError
    dept = q("select dept from doctors where id=?", (did,))[0]["dept"]
    n = q("select count(*) n from appts a join doctors d on d.id=a.doctor_id where d.dept=? and a.day=?", (dept, day))[0]["n"]
    try:
        aid = x("insert into appts(user_id,doctor_id,day,slot,type,reason,token,room) values(?,?,?,?,?,?,?,?)",
                (session["uid"], did, day, slot, typ, (d.get("reason") or "")[:300], f"{dept[0]}{101+n}", secrets.token_hex(4)))
    except sqlite3.IntegrityError:
        return jsonify(error="That time was just taken. Pick another slot."), 409
    a = enrich(q(BASE + "where a.id=?", (aid,))[0])
    note(session["uid"], f"Appointment #{aid} confirmed: {a['doctor']}, {day} {slot}. Token {a['token']}."); audit(f"book {aid}")
    return jsonify(a)


@app.get("/api/appointments")
@need()
def appts():
    r, u = session["role"], session["uid"]
    if r == "patient": rows = q(BASE + "where a.user_id=? order by day desc, slot", (u,))
    elif r == "doctor": rows = q(BASE + "where a.doctor_id=? order by day, slot", (session["did"],))
    else: rows = q(BASE + "order by day desc, slot")
    return jsonify([enrich(a) for a in rows])


def mine(aid):
    a = q(BASE + "where a.id=?", (aid,))
    if not a: raise ValueError
    a = a[0]
    ok = session["role"] == "admin" or (session["role"] == "patient" and a["user_id"] == session["uid"]) or \
        (session["role"] == "doctor" and a["doctor_id"] == session.get("did"))
    if not ok: raise ValueError
    return a


@app.delete("/api/appointments/<int:aid>")
@need("patient", "admin")
def cancel(aid):
    a = mine(aid); x("update appts set status='cancelled' where id=?", (aid,))
    note(a["user_id"], f"Appointment #{aid} was cancelled."); audit(f"cancel {aid}"); return jsonify(ok=True)


@app.post("/api/appointments/<int:aid>/reschedule")
@need("patient")
def resched(aid):
    a, d = mine(aid), request.get_json(silent=True) or {}
    make_slot_ok(a["doctor_id"], d.get("day", ""), d.get("slot", ""))
    try: x("update appts set day=?, slot=? where id=?", (d["day"], d["slot"], aid))
    except sqlite3.IntegrityError: return jsonify(error="That time is taken. Pick another."), 409
    note(a["user_id"], f"Appointment #{aid} moved to {d['day']} {d['slot']}."); audit(f"reschedule {aid}"); return jsonify(ok=True)


@app.post("/api/appointments/<int:aid>/status")
@need("doctor")
def astatus(aid):
    a, d = mine(aid), request.get_json(silent=True) or {}
    if d.get("status") not in ("in_progress", "done"): raise ValueError
    x("update appts set status=?, rx=? where id=?", (d["status"], (d.get("rx") or "")[:1000], aid))
    if d["status"] == "done":
        note(a["user_id"], f"Consultation #{aid} finished. Check your prescription in the dashboard.")
        nxt = q(BASE + "where a.doctor_id=? and a.day=? and a.status='booked' order by slot limit 1", (a["doctor_id"], a["day"]))
        if nxt: note(nxt[0]["user_id"], "You are next in the queue. Please be ready.")
    audit(f"{d['status']} {aid}"); return jsonify(ok=True)


@app.get("/api/chat/<int:aid>")
@need("patient", "doctor")
def chat_get(aid): mine(aid); return jsonify(q("select * from chat where appt_id=? order by id", (aid,)))


@app.post("/api/chat/<int:aid>")
@need("patient", "doctor")
def chat_post(aid):
    mine(aid); t = ((request.get_json(silent=True) or {}).get("text") or "").strip()[:500]
    if not t: raise ValueError
    x("insert into chat(appt_id,sender,text,created) values(?,?,?,?)", (aid, session["role"], t, now())); return jsonify(ok=True)


# ---------- emergencies ----------
@app.post("/api/emergency")
@need("patient")
def emergency():
    d = request.get_json(silent=True) or {}
    if d.get("etype") not in ETYPES or d.get("severity") not in ("Critical", "Serious", "Moderate"): raise ValueError
    hid = I(d["hospital_id"]) if d.get("hospital_id") else None
    eid = x("insert into emergencies(user_id,etype,severity,symptoms,location,hospital_id,created) values(?,?,?,?,?,?,?)",
            (session["uid"], d["etype"], d["severity"], (d.get("symptoms") or "")[:500], (d.get("location") or "")[:200], hid, now()))
    note(session["uid"], f"Emergency request #{eid} sent. Staff have been alerted."); audit(f"emergency {eid} {d['severity']}")
    return jsonify(ok=True, id=eid)


@app.get("/api/emergencies")
@need()
def emergencies():
    r = session["role"]
    base = "select e.*, u.name patient, h.name hospital from emergencies e join users u on u.id=e.user_id left join hospitals h on h.id=e.hospital_id "
    if r == "patient": rows = q(base + "where e.user_id=? order by e.id desc", (session["uid"],))
    elif r == "doctor":
        hid = q("select hospital_id from doctors where id=?", (session["did"],))[0]["hospital_id"]
        rows = q(base + "where e.hospital_id=? order by e.id desc", (hid,))
    else: rows = q(base + "order by e.id desc")
    return jsonify(rows)


@app.post("/api/emergencies/<int:eid>/resolve")
@need("admin")
def resolve(eid):
    e = q("select * from emergencies where id=?", (eid,))[0]
    x("update emergencies set status='resolved' where id=?", (eid,)); note(e["user_id"], f"Emergency request #{eid} was handled by staff.")
    audit(f"resolve {eid}"); return jsonify(ok=True)


# ---------- staff ----------
@app.post("/api/doctor/status")
@need("doctor", "admin")
def dstatus():
    d = request.get_json(silent=True) or {}
    did = session.get("did") if session["role"] == "doctor" else I(d.get("doctor_id"))
    if d.get("status") not in DSTAT: raise ValueError
    x("update doctors set status=? where id=?", (d["status"], did)); audit(f"doctor {did} -> {d['status']}"); return jsonify(ok=True)


@app.post("/api/admin/hospital/<int:hid>")
@need("admin")
def hupdate(hid):
    d = request.get_json(silent=True) or {}; h = q("select * from hospitals where id=?", (hid,))[0]
    b, i, v, w = I(d["beds"], 0, h["bed_cap"]), I(d["icu"], 0, h["icu_cap"]), I(d["vent"], 0, 100), I(d["wait_min"], 0, 600)
    x("update hospitals set beds=?, icu=?, vent=?, wait_min=? where id=?", (b, i, v, w, hid)); audit(f"hospital {hid} updated"); return jsonify(ok=True)


@app.get("/api/admin/stats")
@need("admin")
def stats():
    hs = q("select * from hospitals"); today = dt.date.today().isoformat()
    return jsonify(patients=q("select count(*) n from users where role='patient'")[0]["n"],
                   emergencies=q("select count(*) n from emergencies where status='active'")[0]["n"],
                   beds=sum(h["beds"] for h in hs), icu=sum(h["icu"] for h in hs),
                   docs_av=q("select count(*) n from doctors where status='Available'")[0]["n"], docs=q("select count(*) n from doctors")[0]["n"],
                   queue=q("select count(*) n from appts where day=? and status in('booked','in_progress')", (today,))[0]["n"],
                   avg_wait=round(sum(h["wait_min"] for h in hs) / len(hs)), appts=q("select count(*) n from appts where status!='cancelled'")[0]["n"],
                   util=[dict(name=h["name"], pct=round(100 * (h["bed_cap"] - h["beds"]) / h["bed_cap"])) for h in hs],
                   audit=q("select * from audit order by id desc limit 8"))


# ---------- AI prediction & surge detection ----------
def live_feats(dept):
    """Build the model's live inputs for one department from the database."""
    ds = q("select * from doctors where dept=?", (dept,)); ids = [d["id"] for d in ds]
    if not ids: return {}
    ph, today = ",".join("?" * len(ids)), dt.date.today().isoformat()
    cnt = lambda st: q(f"select count(*) n from appts where doctor_id in ({ph}) and day=? and status in ({st})", (*ids, today))[0]["n"]
    hids = list({d["hospital_id"] for d in ds}); hp = ",".join("?" * len(hids))
    beds = q(f"select coalesce(sum(beds),0) n from hospitals where id in ({hp})", hids)[0]["n"]
    em = q(f"select count(*) n from emergencies where status='active' and hospital_id in ({hp})", hids)[0]["n"]
    total = q(f"select count(*) n from appts where doctor_id in ({ph}) and day=? and status!='cancelled'", (*ids, today))[0]["n"]
    n = dt.datetime.now(); hrs = max(1, n.hour + n.minute / 60 - 9)
    return dict(dept=dept, waiting=cnt("'booked'"), in_treatment=cnt("'in_progress'"), doctors=sum(d["status"] == "Available" for d in ds),
                avg_consult=AVG.get(dept, 12), beds=beds, arrival_rate=round((total + em) / hrs, 1), hist_wait=round(AI.hist.get(dept, 20), 1))


@app.get("/api/ai/live")
def ai_live():
    out = []
    for dept in DEPTS:
        f = live_feats(dept)
        if f: out.append(dict(f, pred={t: AI.predict(**f, triage=t) for t in (1, 2, 3)}))
    return jsonify(out)


@app.post("/api/ai/predict")
def ai_predict():
    d = request.get_json(silent=True) or {}
    if d.get("dept") not in DEPTS: raise ValueError
    return jsonify(AI.predict(waiting=I(d["waiting"], 0, 500), triage=I(d["triage"], 1, 3), doctors=I(d["doctors"], 0, 50),
                              avg_consult=I(d["avg_consult"], 1, 120), in_treatment=I(d["in_treatment"], 0, 200), beds=I(d["beds"], 0, 1000),
                              dept=d["dept"], arrival_rate=float(d["arrival_rate"]), hist_wait=float(d["hist_wait"])))


@app.get("/api/ai/model")
def ai_model(): return jsonify(source=AI.source, mae=AI.mae, rows=AI.n, imp=AI.imp, depts=DEPTS)


@app.get("/api/notifications")
@need()
def notes(): return jsonify(q("select * from notes where user_id=? order by id desc limit 30", (session["uid"],)))


@app.get("/")
def index(): return Response(PAGE.replace("__ETYPES__", str(ETYPES).replace("'", '"')).replace("__DSTAT__", str(DSTAT).replace("'", '"')), mimetype="text/html")


PAGE = r"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>CareDesk Hospital</title><link href="https://fonts.googleapis.com/css2?family=Atkinson+Hyperlegible:wght@400;700&display=swap" rel="stylesheet">
<style>
:root{--ink:#11283f;--mut:#566b80;--bg:#f3f7fb;--blue:#0b5cab;--green:#14804f;--red:#d0192a;--line:#d4dfea;--amb:#b87400}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:17px/1.5 "Atkinson Hyperlegible",system-ui,sans-serif}
header{background:#fff;border-bottom:1px solid var(--line);position:sticky;top:0;z-index:5;padding:10px 20px;display:flex;gap:18px;align-items:center;flex-wrap:wrap}
.logo{font-weight:700;font-size:21px;color:var(--blue);text-decoration:none;display:flex;gap:8px;align-items:center}.logo i{background:var(--red);color:#fff;border-radius:6px;width:28px;height:28px;display:grid;place-items:center;font-style:normal}
nav{display:flex;gap:4px;flex-wrap:wrap}nav a{padding:8px 12px;border-radius:8px;color:var(--ink);text-decoration:none;font-weight:700;font-size:15px}
nav a.on{background:#e2eefb;color:var(--blue)}nav a.em{background:var(--red);color:#fff}nav b{background:var(--red);color:#fff;border-radius:10px;padding:0 6px;font-size:12px;margin-left:3px}
main{max-width:1080px;margin:auto;padding:22px 18px}h1{font-size:34px;line-height:1.15;margin:0 0 10px}h2{margin:0 0 10px;font-size:22px}
.card{background:#fff;border:1px solid var(--line);border-radius:12px;padding:18px;margin-bottom:16px}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(210px,1fr));gap:14px}
.big{display:block;text-align:center;padding:20px;border-radius:12px;font-size:20px;font-weight:700;text-decoration:none;color:#fff;border:0;cursor:pointer;font-family:inherit}
.big.r{background:var(--red)}.big.b{background:var(--blue)}.big.g{background:var(--green)}
.btn{background:var(--blue);color:#fff;border:0;padding:11px 20px;border-radius:8px;font:700 16px inherit;cursor:pointer}.btn.s{padding:6px 12px;font-size:14px}.btn.o{background:#fff;color:var(--blue);border:2px solid var(--blue)}.btn.d{background:var(--red)}.btn.g{background:var(--green)}
label{display:block;font-weight:700;font-size:15px}input,select,textarea{width:100%;padding:10px;border:1px solid #9fb2c6;border-radius:8px;font:inherit;margin-top:4px;background:#fff}
:focus-visible{outline:3px solid #6fb1ee;outline-offset:2px}table{width:100%;border-collapse:collapse;font-size:15px}td,th{padding:9px 7px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}
.st{font-weight:700;white-space:nowrap}.st.full{color:var(--red)}.st.limited{color:var(--amb)}.st.available{color:var(--green)}
.warn{background:#fdecee;border:2px solid var(--red);color:#8a0f1b;padding:14px 16px;border-radius:10px;font-weight:700;margin:12px 0}
.info{background:#e8f2fd;border-left:5px solid var(--blue);padding:10px 14px;border-radius:6px;margin:10px 0;font-size:15px}
.tier{border-left:8px solid}.t1{border-color:var(--red)}.t2{border-color:var(--amb)}.t3{border-color:var(--green)}
.num{font-size:32px;font-weight:700;color:var(--blue)}.sm{color:var(--mut);font-size:14px}.err{color:var(--red);font-weight:700}.ok{color:var(--green);font-weight:700}
.slots{display:flex;flex-wrap:wrap;gap:8px;margin:10px 0}.slots button{padding:8px 14px;border:2px solid var(--blue);background:#fff;color:var(--blue);border-radius:8px;font:700 15px inherit;cursor:pointer}.slots button.sel{background:var(--blue);color:#fff}
.bar{height:12px;background:#e4ebf2;border-radius:6px;overflow:hidden}.bar i{display:block;height:100%;background:var(--blue)}
.chat{max-height:240px;overflow:auto;border:1px solid var(--line);border-radius:8px;padding:8px;margin:8px 0}.chat p{margin:4px 0}.chat .me{text-align:right;color:var(--blue)}
.row{display:flex;gap:10px;flex-wrap:wrap;align-items:end}.row>*{flex:1;min-width:140px}
@media(prefers-reduced-motion:reduce){*{animation:none!important;transition:none!important}}
</style></head><body>
<header><a class="logo" href="#/home"><i>+</i>CareDesk</a><nav id="nav"></nav></header><main id="app"></main>
<script>
const ETYPES=__ETYPES__,DSTAT=__DSTAT__,$=s=>document.querySelector(s),esc=s=>String(s??'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const api=async(u,m='GET',b)=>{const r=await fetch(u,{method:m,headers:{'Content-Type':'application/json'},body:b?JSON.stringify(b):undefined});let d={};try{d=await r.json()}catch(e){}return{ok:r.ok,s:r.status,d}};
const ST={available:'🟢 Available',limited:'🟡 Limited availability',full:'🔴 Full / critical'},badge=s=>`<span class="st ${s}">${ST[s]}</span>`;
const today=()=>new Date().toISOString().slice(0,10),page=()=>(location.hash||'#/home').split('/')[1]||'home',arg=()=>location.hash.split('/')[2];
const dsel=(x,o)=>o.map(v=>`<option ${v==x?'selected':''}>${v}</option>`).join('');
let me=null;
const EMW=`<div class="warn">⚠ Life-threatening emergency? Call your local emergency number now (India: 108 ambulance or 112) or go to the nearest emergency department immediately. Do not wait for an online response.</div>`;
function nav(){const L=[['home','Home'],['emergency','Emergency'],['book','Appointments'],['doctors','Doctors'],['consult','Consultation'],['hospitals','Hospitals'],['ai','AI Queue']];
 if(me)L.push(['dashboard',{patient:'Patient Dashboard',doctor:'Doctor Dashboard',admin:'Admin Dashboard'}[me.role]]);
 $('#nav').innerHTML=L.map(([k,t])=>`<a href="#/${k}" class="${k=='emergency'?'em':''} ${page()==k?'on':''}">${t}</a>`).join('')+(me?`<a href="#/notes">🔔<b id="nb"></b></a><a href="#" onclick="logout();return false">Log out</a>`:`<a href="#/login">Log in</a>`)}
async function logout(){await api('/api/logout','POST');me=null;location.hash='#/home';go()}
async function badgeN(){if(!me)return;const r=await api('/api/notifications'),s=+localStorage.seen||0,n=r.d.filter?r.d.filter(x=>x.id>s).length:0;const b=$('#nb');if(b)b.textContent=n||''}
const V={};
async function go(){const m=await api('/api/me');me=m.d.user;nav();$('#app').innerHTML='<p>Loading…</p>';try{await(V[page()]||V.home)()}catch(e){$('#app').innerHTML='<p class="err">Something went wrong. Please refresh and try again.</p>'}badgeN()}
addEventListener('hashchange',go);setInterval(()=>{badgeN();const t=document.activeElement.tagName;if(page()=='ai')aiLive();if(page()=='dashboard'&&t!='INPUT'&&t!='SELECT')V.dashboard()},5000);
const needLogin=()=>`<div class="card"><p class="err">Please log in to continue.</p><a class="btn" href="#/login">Log in or register</a></div>`;

V.home=async()=>{const hs=(await api('/api/hospitals')).d,open=hs.filter(h=>h.beds>0).length,wait=Math.min(...hs.map(h=>h.wait_min));
$('#app').innerHTML=`<h1>Fast help, clear information.</h1><p>See which hospitals can take emergencies right now, book a doctor, or see one online.</p>
<div class="grid"><a class="big r" href="#/emergency">🚑 Emergency Help</a><a class="big b" href="#/book">📅 Book Appointment</a><a class="big g" href="#/consult">💻 Consult a Doctor</a></div>
<div class="card" style="margin-top:16px"><h2>Right now</h2><div class="grid"><div><div class="num">${open}/${hs.length}</div>hospitals with emergency beds</div><div><div class="num">${wait} min</div>shortest ER wait</div>
<div><div class="num">${hs.reduce((a,h)=>a+h.doctors,0)}</div>doctors available</div><div><div class="num">108 · 112</div>ambulance · emergency</div></div></div>
<div class="card"><h2>Find a hospital</h2><div class="row"><label>Name, area or facility<input id="hq" placeholder="e.g. Trauma"></label><button class="btn" onclick="window.pq=$('#hq').value;location.hash='#/hospitals'">Search</button></div></div>
<h2>Which option is right for you?</h2><div class="grid"><div class="card tier t1"><b>Life-threatening</b><p>Not breathing, unconscious, heavy bleeding, chest pain with sweating. Call 108 / 112 now.</p></div>
<div class="card tier t2"><b>Urgent</b><p>Needs care today but is stable. Use Emergency Help to alert a hospital.</p></div><div class="card tier t3"><b>General</b><p>Check-ups and follow-ups. Book an appointment.</p></div></div>`};

V.emergency=async()=>{const hs=(await api('/api/hospitals')).d.sort((a,b)=>(b.beds>0)-(a.beds>0)||a.dist-b.dist);
$('#app').innerHTML=`<h1>Emergency management</h1>${EMW}<div class="card"><h2>Request emergency assistance</h2>${me?`<div class="grid">
<label>Emergency type<select id="et">${dsel('',ETYPES)}</select></label><label>Severity<select id="sv" onchange="sevw()"><option>Critical</option><option selected>Serious</option><option>Moderate</option></select></label>
<label>Hospital<select id="eh">${hs.map(h=>`<option value="${h.id}">${esc(h.name)} (${h.dist} km)${h.beds?'':' - full'}</option>`).join('')}</select></label></div>
<label style="margin-top:10px">Current symptoms (describe, do not self-diagnose)<textarea id="sy" rows="3" maxlength="500"></textarea></label>
<div class="row" style="margin-top:10px"><label>Location<input id="lo" placeholder="Address or landmark"></label><button class="btn o" onclick="geo()">📍 Share my location</button></div><div id="sw"></div>
<p><button class="btn d" onclick="sendE()">Request emergency help</button></p><div id="em"></div>`:needLogin()}</div>
<div class="card"><h2>Nearby hospitals</h2>${hTable(hs)}</div>`;sevw()};
function sevw(){const s=$('#sv');if($('#sw'))$('#sw').innerHTML=s&&s.value=='Critical'?`<div class="warn">CRITICAL: Call emergency services / go to the nearest emergency department immediately.</div>`:''}
function geo(){navigator.geolocation?navigator.geolocation.getCurrentPosition(p=>$('#lo').value=`GPS ${p.coords.latitude.toFixed(5)}, ${p.coords.longitude.toFixed(5)}`,()=>$('#em').innerHTML='<p class="err">Location blocked. Type your address instead.</p>'):0}
async function sendE(){const r=await api('/api/emergency','POST',{etype:$('#et').value,severity:$('#sv').value,hospital_id:$('#eh').value,symptoms:$('#sy').value,location:$('#lo').value});
$('#em').innerHTML=r.ok?`<p class="ok">Request #${r.d.id} sent. Staff are alerted. If life is at risk, call 108 / 112 now.</p>`:`<p class="err">${esc(r.d.error)}</p>`}
const hTable=hs=>`<div style="overflow-x:auto"><table><tr><th>Hospital</th><th>Distance</th><th>ER beds</th><th>ICU</th><th>Vent.</th><th>Doctors</th><th>Wait</th><th>Status</th></tr>${hs.map(h=>`<tr><td><b>${esc(h.name)}</b></td><td>${h.dist} km</td><td>${h.beds}</td><td>${h.icu}</td><td>${h.vent}</td><td>${h.doctors}</td><td>${h.wait_min} min</td><td>${badge(h.status)}</td></tr>`).join('')}</table></div>`;

V.hospitals=async()=>{$('#app').innerHTML=`<h1>Hospital availability</h1><div class="card row"><label>Search<input id="hq" value="${esc(window.pq||'')}" oninput="hl()"></label><label>Distance<select id="hd" onchange="hl()"><option value="999">Any</option><option value="3">Within 3 km</option><option value="5">Within 5 km</option><option value="10">Within 10 km</option></select></label>
<label><input type="checkbox" id="ha" onchange="hl()" style="width:auto"> Only with emergency beds</label></div><div id="hl"></div>`;window.pq='';hl()};
async function hl(){const hs=(await api(`/api/hospitals?q=${encodeURIComponent($('#hq').value)}&maxdist=${$('#hd').value}${$('#ha').checked?'&avail=1':''}`)).d;
$('#hl').innerHTML=hs.length?`<div class="card">${hTable(hs)}</div>`+hs.map(h=>`<details class="card"><summary><b>${esc(h.name)}</b> ${badge(h.status)}</summary><p>${esc(h.addr)}<br>Facilities: ${esc(h.facilities)}</p>
<a class="btn s" href="https://www.google.com/maps/dir/?api=1&destination=${encodeURIComponent(h.name+' '+h.addr)}" target="_blank" rel="noopener">Get directions</a> <a class="btn s o" href="tel:${h.phone.replace(/\s/g,'')}">Call ${esc(h.phone)}</a></details>`).join(''):'<p>No hospitals match. Try a wider search.</p>'}

V.doctors=async()=>{const ds=(await api('/api/doctors')).d,dep=[...new Set(ds.map(d=>d.dept))];window.dd=ds;
$('#app').innerHTML=`<h1>Doctors</h1><div class="card"><label>Department<select id="dp" onchange="dl()"><option value="">All</option>${dep.map(d=>`<option>${d}</option>`).join('')}</select></label></div><div class="grid" id="dl"></div>`;dl()};
function dl(){$('#dl').innerHTML=window.dd.filter(d=>!$('#dp').value||d.dept==$('#dp').value).map(d=>`<div class="card"><h2>${esc(d.name)}</h2>${esc(d.dept)}<br><span class="sm">${d.exp} years · ${esc(d.hospital)}</span><p>Fee ₹${d.fee}<br><b>${esc(d.status)}</b></p>
<a class="btn s" href="#/book/${d.id}">Book</a> <a class="btn s o" href="#/book/${d.id}">Consult online</a></div>`).join('')}

let B={};
V.book=async()=>{const ds=(await api('/api/doctors')).d;B={doc:+arg()||ds[0].id,slot:null};
$('#app').innerHTML=`<h1>Book an appointment</h1><div class="info">For general care. For urgent problems use <a href="#/emergency">Emergency</a>.</div>${me?'':needLogin()}<div class="card"><div class="grid">
<label>Department<select id="bd" onchange="bdept()"><option value="">All</option>${[...new Set(ds.map(d=>d.dept))].map(d=>`<option>${d}</option>`).join('')}</select></label>
<label>Doctor<select id="bk" onchange="bsl()">${ds.map(d=>`<option value="${d.id}" data-d="${d.dept}" ${d.id==B.doc?'selected':''}>${esc(d.name)} · ${d.dept}</option>`).join('')}</select></label>
<label>Date<input type="date" id="bt" min="${today()}" value="${today()}" onchange="bsl()"></label><label>Type<select id="by"><option>In-person</option><option>Video</option></select></label></div>
<div id="bq" class="info"></div><b>Available times</b><div class="slots" id="bs"></div><label>Reason<input id="br" maxlength="300"></label><p><button class="btn" onclick="bconf()">Confirm appointment</button></p><div id="bm"></div></div>`;bsl()};
function bdept(){const v=$('#bd').value;[...$('#bk').options].forEach(o=>o.hidden=v&&o.dataset.d!=v);const f=[...$('#bk').options].find(o=>!o.hidden);if(f)$('#bk').value=f.value;bsl()}
async function bsl(){B.slot=null;const id=$('#bk').value,[s,qq]=await Promise.all([api(`/api/slots?doctor_id=${id}&day=${$('#bt').value}`),api('/api/queue/'+id)]);
$('#bq').innerHTML=`Today's queue: <b>${qq.d.queue} patients</b> · Avg consult ${qq.d.avg} min · Doctor: <b>${qq.d.status}</b> · Est. wait for walk-in: <b>${qq.d.wait} min</b>`;
$('#bs').innerHTML=s.ok&&s.d.length?s.d.map(t=>`<button onclick="B.slot='${t}';document.querySelectorAll('#bs button').forEach(b=>b.classList.toggle('sel',b.textContent=='${t}'))">${t}</button>`).join(''):'<span>No free times. Try another date.</span>'}
async function bconf(){if(!B.slot){$('#bm').innerHTML='<p class="err">Choose a time slot first.</p>';return}
if(!confirm(`Book ${$('#bt').value} at ${B.slot}?`))return;
const r=await api('/api/appointments','POST',{doctor_id:$('#bk').value,day:$('#bt').value,slot:B.slot,type:$('#by').value,reason:$('#br').value});
if(!r.ok){$('#bm').innerHTML=`<p class="err">${esc(r.d.error)}</p>`;return bsl()}const a=r.d;
$('#bm').innerHTML=`<div class="card" style="border:2px solid var(--green)"><h2 class="ok">Appointment confirmed</h2><p>ID <b>#${a.id}</b> · Token <b>${a.token}</b><br>${esc(a.doctor)} · ${esc(a.hospital)}<br>${a.day} at ${a.slot} (${a.type})</p>
<p>Queue ahead: ${a.ahead} · Est. wait: ${a.wait} min · Predicted consultation: <b>${a.predicted}</b></p></div>`;bsl()}

async function chatBox(id){const m=(await api('/api/chat/'+id)).d;return`<div class="chat" id="ch${id}">${(m.map?m:[]).map(c=>`<p class="${c.sender==me.role?'me':''}"><b>${c.sender}:</b> ${esc(c.text)}</p>`).join('')||'<p class="sm">No messages yet.</p>'}</div>
<div class="row"><input id="ci${id}" placeholder="Type a message" maxlength="500"><button class="btn s" onclick="csend(${id})">Send</button></div>`}
async function csend(id){const t=$('#ci'+id).value;if(!t)return;await api('/api/chat/'+id,'POST',{text:t});openChat(id)}
async function openChat(id){$('#cp').innerHTML=`<div class="card"><h2>Chat for appointment #${id}</h2><div class="info">Online consultation does not replace emergency care. Not for emergencies.</div>${await chatBox(id)}
<p class="sm">Reports and prescriptions: the doctor adds the prescription when the visit ends (see dashboard). File upload is not included in this version.</p></div>`}
V.consult=async()=>{if(!me){$('#app').innerHTML='<h1>Online consultation</h1>'+needLogin();return}
const a=(await api('/api/appointments')).d.filter(x=>x.status!='cancelled');
$('#app').innerHTML=`<h1>Online consultation</h1>${EMW}<div class="card"><p>Pick a doctor on the <a href="#/doctors">Doctors</a> page and choose <b>Video</b> when booking.</p>
${a.length?`<table><tr><th>ID</th><th>Doctor / patient</th><th>When</th><th>Type</th><th></th></tr>${a.map(x=>`<tr><td>#${x.id}</td><td>${esc(me.role=='doctor'?x.patient:x.doctor)}</td><td>${x.day} ${x.slot}</td><td>${x.type}</td>
<td>${x.type=='Video'&&x.status!='done'?`<a class="btn s g" target="_blank" rel="noopener" href="https://meet.jit.si/CareDesk-${x.id}-${x.room}">Join video</a> `:''}<button class="btn s o" onclick="openChat(${x.id})">Chat</button></td></tr>`).join('')}</table>`:'<p>No consultations yet.</p>'}</div><div id="cp"></div>`};

V.dashboard=async()=>{if(!me){$('#app').innerHTML=needLogin();return}
const A=(await api('/api/appointments')).d,E=(await api('/api/emergencies')).d,act=A.filter(x=>x.status=='booked'||x.status=='in_progress'),done=A.filter(x=>x.status=='done');
const eT=E=>E.length?`<table><tr><th>#</th><th>Type</th><th>Severity</th><th>Hospital</th><th>Patient</th><th>Status</th><th></th></tr>${E.map(e=>`<tr><td>${e.id}</td><td>${e.etype}</td><td class="${e.severity=='Critical'?'err':''}">${e.severity}</td><td>${esc(e.hospital)}</td><td>${esc(e.patient)}</td><td>${e.status}</td>
<td>${me.role=='admin'&&e.status=='active'?`<button class="btn s g" onclick="resolveE(${e.id})">Mark handled</button>`:''}</td></tr>`).join('')}</table>`:'<p>None.</p>';
let h=`<h1>${{patient:'My health',doctor:'Doctor dashboard',admin:'Hospital admin'}[me.role]}</h1>`;
if(me.role=='patient')h+=`<div class="card"><h2>Upcoming appointments & queue</h2>${act.length?`<table><tr><th>ID</th><th>Doctor</th><th>When</th><th>Token</th><th>Queue</th><th></th></tr>${act.map(x=>`<tr><td>#${x.id}</td><td>${esc(x.doctor)}<br><span class="sm">${esc(x.hospital)}</span></td><td>${x.day} ${x.slot}<br>${x.type}</td><td><b>${x.token}</b></td>
<td>${x.day==today()?`${x.ahead} ahead · ~${x.wait} min<br>Predicted ${x.predicted}`:'Day of visit'}</td><td><button class="btn s o" onclick="resch(${x.id})">Reschedule</button> <button class="btn s d" onclick="canc(${x.id})">Cancel</button></td></tr>`).join('')}</table>`:'<p>No upcoming appointments. <a href="#/book">Book one</a>.</p>'}</div>
<div class="card"><h2>Past consultations & prescriptions</h2>${done.length?done.map(x=>`<p><b>${x.day}</b> ${esc(x.doctor)}<br>Prescription: ${esc(x.rx||'None recorded')}</p>`).join(''):'<p>None yet.</p>'}</div><div class="card"><h2>Emergency requests</h2>${eT(E)}</div>`;
if(me.role=='doctor'){const d=(await api('/api/doctors')).d.find(x=>x.name==me.name);
 h+=`<div class="card"><h2>My availability</h2><div class="row" style="align-items:center">${DSTAT.map(s=>`<button class="btn ${d&&d.status==s?'':'o'}" onclick="dst(0,'${s}')">${s}</button>`).join('')}</div></div>
 <div class="card"><h2>Patient queue</h2>${act.length?`<table><tr><th>Token</th><th>Patient</th><th>When</th><th>Reason</th><th></th></tr>${act.map(x=>`<tr><td><b>${x.token}</b></td><td>${esc(x.patient)}</td><td>${x.day} ${x.slot} ${x.type}</td><td>${esc(x.reason)}</td>
 <td>${x.status=='booked'?`<button class="btn s" onclick="ast(${x.id},'in_progress')">Start</button>`:`<button class="btn s g" onclick="fin(${x.id})">End + prescription</button>`}</td></tr>`).join('')}</table>`:'<p>Queue is empty.</p>'}</div>
 <div class="card"><h2>Emergency cases at my hospital</h2>${eT(E)}</div><div class="card"><h2>Consultation history</h2>${done.map(x=>`<p>${x.day} ${esc(x.patient)}: ${esc(x.rx||'-')}</p>`).join('')||'<p>None.</p>'}</div>`}
if(me.role=='admin'){const S=(await api('/api/admin/stats')).d,hs=(await api('/api/hospitals')).d,ds=(await api('/api/doctors')).d;
 h+=`<div class="grid">${[['Patients',S.patients],['Active emergencies',S.emergencies],['Free beds',S.beds],['Free ICU',S.icu],['Doctors available',S.docs_av+'/'+S.docs],['Today queue',S.queue],['Avg wait',S.avg_wait+' min'],['Appointments',S.appts]].map(([a,b])=>`<div class="card"><div class="num">${b}</div>${a}</div>`).join('')}</div>
 <div class="card"><h2>Bed utilization</h2>${S.util.map(u=>`<p>${esc(u.name)} · ${u.pct}%<div class="bar"><i style="width:${u.pct}%"></i></div></p>`).join('')}</div>
 <div class="card"><h2>Update hospital resources</h2>${hs.map(x=>`<div class="row" style="margin-bottom:10px"><b style="flex:2">${esc(x.name)}</b><label>ER beds<input id="b${x.id}" type="number" min="0" value="${x.beds}"></label><label>ICU<input id="i${x.id}" type="number" min="0" value="${x.icu}"></label>
 <label>Vent.<input id="v${x.id}" type="number" min="0" value="${x.vent}"></label><label>Wait min<input id="w${x.id}" type="number" min="0" value="${x.wait_min}"></label><button class="btn s" onclick="hup(${x.id})">Save</button></div>`).join('')}<div id="hm"></div></div>
 <div class="card"><h2>Doctor availability</h2>${ds.map(d=>`<div class="row" style="margin-bottom:6px"><span style="flex:2">${esc(d.name)}</span><select onchange="dst(${d.id},this.value)">${dsel(d.status,DSTAT)}</select></div>`).join('')}</div>
 <div class="card"><h2>Emergency cases</h2>${eT(E)}</div><div class="card"><h2>Audit log</h2>${S.audit.map(a=>`<p class="sm">${a.created} · ${esc(a.who)} · ${esc(a.action)}</p>`).join('')}</div>`}
$('#app').innerHTML=h};
async function canc(id){if(confirm('Cancel this appointment?')){await api('/api/appointments/'+id,'DELETE');V.dashboard()}}
async function resch(id){const day=prompt('New date (YYYY-MM-DD)'),slot=day&&prompt('New time (HH:MM, e.g. 10:30)');if(!slot)return;const r=await api(`/api/appointments/${id}/reschedule`,'POST',{day,slot});if(!r.ok)alert(r.d.error);V.dashboard()}
async function ast(id,s,rx){await api(`/api/appointments/${id}/status`,'POST',{status:s,rx});V.dashboard()}
function fin(id){const rx=prompt('Prescription / notes for the patient');if(rx!==null)ast(id,'done',rx)}
async function dst(id,s){await api('/api/doctor/status','POST',{doctor_id:id,status:s});V.dashboard()}
async function hup(id){const g=p=>+$('#'+p+id).value,r=await api('/api/admin/hospital/'+id,'POST',{beds:g('b'),icu:g('i'),vent:g('v'),wait_min:g('w')});$('#hm').innerHTML=r.ok?'<p class="ok">Saved.</p>':`<p class="err">${esc(r.d.error)} (cannot exceed hospital capacity)</p>`}
async function resolveE(id){await api(`/api/emergencies/${id}/resolve`,'POST');V.dashboard()}

const LV={normal:'🟢 Normal',high:'🟡 High load',critical:'🔴 Overloaded'},TN={1:'Critical',2:'Urgent',3:'Routine'};
V.ai=async()=>{const M=(await api('/api/ai/model')).d;
$('#app').innerHTML=`<h1>AI queue prediction & surge detection</h1><div class="info">A gradient-boosting model estimates waiting time from 9 inputs: patients waiting, triage level, doctors available, average consult time, patients in treatment, available beds, department, historical wait and arrival rate. It predicts queues only and never diagnoses. Training data: <b>${esc(M.source)}</b> (${M.rows} rows). Typical error: ${M.mae==null?'n/a (install scikit-learn)':'±'+M.mae.toFixed(1)+' min'}.</div><div id="aa"></div><div class="grid" id="al"></div>
<div class="card"><h2>What-if prediction</h2><div class="grid">`+[['waiting','Patients waiting',8],['doctors','Doctors available',2],['avg_consult','Avg consult (min)',12],['in_treatment','Patients in treatment',2],['beds','Available beds',10],['arrival_rate','Arrivals per hour',8],['hist_wait','Historical avg wait (min)',25]].map(([k,t,v])=>`<label>${t}<input id="w_${k}" type="number" step="any" min="0" value="${v}"></label>`).join('')+`<label>Triage level<select id="w_triage"><option value="1">1 Critical</option><option value="2">2 Urgent</option><option value="3" selected>3 Routine</option></select></label><label>Department<select id="w_dept">${dsel('',M.depts)}</select></label></div><p><button class="btn" onclick="wi()">Predict</button></p><div id="wr"></div></div>
<div class="card"><h2>What the model relies on</h2>${Object.entries(M.imp).sort((a,b)=>b[1]-a[1]).map(([k,v])=>`<p class="sm">${k} · ${Math.round(v*100)}%</p><div class="bar"><i style="width:${v*100}%"></i></div>`).join('')||'<p>Not available.</p>'}</div>`;aiLive()};
async function aiLive(){const L=(await api('/api/ai/live')).d,g=$('#al');if(!g)return;const bad=L.filter(f=>f.pred[3].load!='normal');
$('#aa').innerHTML=bad.length?`<div class="warn">Surge detected: ${bad.map(f=>f.dept+' ('+LV[f.pred[3].load].slice(3)+')').join(', ')}. Consider calling in extra doctors or redirecting routine patients.</div>`:'';
g.innerHTML=L.map(f=>`<div class="card"><h2>${f.dept}</h2><b>${LV[f.pred[3].load]}</b> <span class="sm">· ${f.pred[3].utilization*100|0}% of capacity in use</span>
<p class="sm">${f.waiting} waiting · ${f.in_treatment} in treatment · ${f.doctors} doctors free · ${f.beds} beds · ${f.arrival_rate}/hr arrivals · ${f.avg_consult} min/consult</p>
<table><tr><th>Priority</th><th>Predicted wait</th><th>Range</th></tr>${[1,2,3].map(t=>`<tr><td>${TN[t]}</td><td><b>${f.pred[t].wait} min</b></td><td class="sm">${f.pred[t].low}–${f.pred[t].high}</td></tr>`).join('')}</table></div>`).join('')}
async function wi(){const b={};['waiting','doctors','avg_consult','in_treatment','beds','arrival_rate','hist_wait','triage'].forEach(k=>b[k]=+$('#w_'+k).value);b.dept=$('#w_dept').value;const r=await api('/api/ai/predict','POST',b);
$('#wr').innerHTML=r.ok?`<div class="info"><b>Predicted wait: ${r.d.wait} min</b> (likely ${r.d.low}–${r.d.high}) · ${LV[r.d.load]} · ${r.d.utilization*100|0}% of ${r.d.capacity} patients/hr capacity</div>`:`<p class="err">${esc(r.d.error)}</p>`}

V.notes=async()=>{if(!me){$('#app').innerHTML=needLogin();return}const n=(await api('/api/notifications')).d;if(n.length)localStorage.seen=n[0].id;
$('#app').innerHTML=`<h1>Notifications</h1><div class="card">${n.map(x=>`<p><span class="sm">${x.created}</span><br>${esc(x.msg)}</p>`).join('')||'<p>Nothing new.</p>'}</div>`};

V.login=async()=>{$('#app').innerHTML=`<div class="card" style="max-width:440px;margin:auto"><h2>Log in or register</h2><label>Name (register only)<input id="ln"></label><label style="margin-top:8px">Email<input id="le" type="email"></label>
<label style="margin-top:8px">Password (8+ characters)<input id="lp" type="password"></label><p class="row"><button class="btn" onclick="lg('login')">Log in</button><button class="btn o" onclick="lg('register')">Register as patient</button></p><div id="lm"></div>
<p class="sm">Demo (password demo1234): patient@demo.com, doctor@demo.com, admin@demo.com</p></div>`};
async function lg(k){const r=await api('/api/'+k,'POST',{name:$('#ln').value,email:$('#le').value,password:$('#lp').value});if(r.ok){location.hash='#/dashboard';go()}else $('#lm').innerHTML=`<p class="err">${esc(r.d.error)}</p>`}
go();
</script></body></html>"""

if __name__ == "__main__":
    init()
    app.run(debug=True)

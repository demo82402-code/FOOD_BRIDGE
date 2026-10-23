from datetime import datetime, timedelta
import html
import random
import pandas as pd
import streamlit as st

from utils.database import (
    AREAS,
    CATEGORIES,
    CO2_PER_SERVING,
    STATUS_CLASS,
    STEPS,
    STORAGE,
    get_db,
    hp,
)

# ============================================================
# PAGE CONFIGURATION
# ============================================================
st.set_page_config(
    page_title="FOODBRIDGE // Surplus Dispatch Network",
    page_icon="◈",
    layout="wide",
    initial_sidebar_state="expanded",
)

esc = html.escape


# ============================================================
# LOAD EXTERNAL CSS
# ============================================================
def load_css(file_path="styles/main.css"):
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            st.markdown(f"<style>{f.read()}</style>", unsafe_allow_html=True)
    except FileNotFoundError:
        st.warning("styles/main.css not found. Verify directory structure.")


load_css()

# ============================================================
# STATE & DATABASE BINDING
# ============================================================
DB = get_db()

for k, v in {
    "page": "Home",
    "logged_in": False,
    "role": "",
    "user": "",
    "flash": None,
    "signup_role": "Donor",
}.items():
    st.session_state.setdefault(k, v)
S = st.session_state


# ============================================================
# CORE TELEMETRY & UI HELPERS
# ============================================================
def fmt(dt):
    return dt.strftime("%d %b · %H:%M") if dt else "—"


def time_left(dt):
    if not dt:
        return "—"
    s = int((dt - datetime.now()).total_seconds())
    if s <= 0:
        return "EXPIRED"
    h, m = s // 3600, (s % 3600) // 60
    return f"{h:02d}H:{m:02d}M" if h else f"{m:02d}M:{s % 60:02d}S"


def urgency(dt):
    s = (dt - datetime.now()).total_seconds()
    return "b-bad" if s < 3600 else "b-warn" if s < 3 * 3600 else "b-ok"


def log(message, kind="info"):
    DB["activity"].insert(
        0, {"time": datetime.now(), "message": message, "type": kind}
    )


def notify(to, msg):
    DB["notifications"].insert(
        0, {"to": to, "msg": msg, "time": datetime.now(), "dismissed": []}
    )


def my_notes():
    out = []
    for n in DB["notifications"]:
        if S.user in n["dismissed"]:
            continue
        if n["to"] in (S.user, f"role:{S.role}", "all"):
            out.append(n)
    return out


def current_user():
    return next((u for u in DB["users"] if u["name"] == S.user), None)


def flash(msg, kind="ok"):
    S.flash = (msg, kind)


def go(page):
    S.page = page


def login_as(email):
    u = next(x for x in DB["users"] if x["email"] == email)
    S.logged_in, S.role, S.user, S.page = True, u["role"], u["name"], "Dashboard"
    flash(f"AUTH.OK // Session established for {u['name']}")


def logout():
    S.logged_in, S.role, S.user, S.page = False, "", "", "Home"


def callout(text, kind="info"):
    st.markdown(
        f'<div class="callout c-{kind}">{text}</div>', unsafe_allow_html=True
    )


def badge(text, cls="b-ok"):
    return f'<span class="badge {cls}">{esc(text)}</span>'


def page_title(title, sub=""):
    st.markdown(
        f'<div class="ptitle"><h1>{title}</h1><p>// {esc(sub)}</p></div>',
        unsafe_allow_html=True,
    )


def dark_table(df):
    if df.empty:
        callout("NO RECORDS IN BUFFER.", "info")
        return
    t = (
        df.to_html(index=False, escape=True, border=0, classes="dtbl").replace(
            "\n", ""
        )
    )
    st.markdown(f'<div class="tbl-wrap">{t}</div>', unsafe_allow_html=True)


def bars(data, unit=""):
    if not data:
        callout("NO TELEMETRY DATA RECORDED YET.", "info")
        return
    mx = max(data.values()) or 1
    rows = "".join(
        f'<div class="brow"><div class="bl">{esc(str(k))}</div>'
        f'<div class="bt"><div class="bf" style="width:{max(v / mx * 100, 3):.0f}%"></div></div>'
        f'<div class="bv">{v:,}{unit}</div></div>'
        for k, v in data.items()
    )
    st.markdown(f'<div class="bars">{rows}</div>', unsafe_allow_html=True)


def tracker(d):
    idx = {
        "Pending Verification": 0,
        "Available": 1,
        "Claim Requested": 2,
        "Accepted": 3,
        "Picked Up": 4,
        "Completed": 5,
        "Sold Out": 5,
    }.get(d["status"])
    if idx is None:
        return
    parts = []
    for i, s in enumerate(STEPS):
        cls = "done" if (i < idx or idx == 5) else "now" if i == idx else ""
        mark = "✓" if cls == "done" else f"0{i + 1}"
        parts.append(
            f'<div class="step {cls}"><div class="dot">{mark}</div>'
            f'<div class="slabel">{s}</div></div>'
        )
    st.markdown(
        f'<div class="tracker">{"".join(parts)}</div>', unsafe_allow_html=True
    )


def _timer_body(end, label):
    st.markdown(
        f'<div class="timer">⟁ {label} // <b>{time_left(end)}</b></div>',
        unsafe_allow_html=True,
    )


try:
    live_timer = st.fragment(run_every=1)(_timer_body)
except Exception:
    live_timer = _timer_body


def update_stages():
    n = datetime.now()
    for d in DB["donations"]:
        if d["status"] == "Available" and d["verified"]:
            if d["remaining"] <= 0:
                d["status"] = "Sold Out"
                continue
            if (
                d["stage"] == "Discount Active"
                and d["discount_end"]
                and n >= d["discount_end"]
            ):
                d["stage"] = "Donation Priority"
                log(f"{d['id']} ({d['food_type']}) escalated to Donation Priority", "stage")
                notify(
                    "role:NGO",
                    f"ALERT // {d['food_type']} ({d['remaining']} units) open for zero-cost NGO claim.",
                )
                for w in d["watchers"]:
                    notify(
                        w,
                        f"WATCH_TRIGGER // {d['food_type']} is now open for NGO claim.",
                    )
            if n > d["deadline"]:
                d["status"] = "Expired"
                log(f"{d['id']} safety window elapsed", "expired")
                notify(
                    d["donor"],
                    f"EXPIRED // Listing {d['id']} ({d['food_type']}) passed safe consumption window.",
                )
        elif d["status"] == "Pending Verification" and n > d["deadline"]:
            d["status"] = "Expired"


def get_dn(id_):
    return next(d for d in DB["donations"] if d["id"] == id_)


def ngo_listings():
    return [
        d
        for d in DB["donations"]
        if d["status"] == "Available" and d["verified"] and d["remaining"] > 0
    ]


def deals():
    return [d for d in ngo_listings() if d["stage"] == "Discount Active"]


def platform_stats():
    D = DB["donations"]
    donated = sum(d["donated"] for d in D)
    sold = sum(d["sold"] for d in D)
    in_prog = sum(
        d["claim_qty"]
        for d in D
        if d["status"] in ("Claim Requested", "Accepted", "Picked Up")
    )
    wasted = sum(d["remaining"] for d in D if d["status"] == "Expired")
    revenue = sum(o["amount"] for o in DB["orders"])
    savings = sum(o["saved"] for o in DB["orders"])
    return {
        "donated": donated,
        "sold": sold,
        "rescued": donated + sold,
        "in_prog": in_prog,
        "wasted": wasted,
        "revenue": revenue,
        "savings": savings,
        "co2": (donated + sold) * CO2_PER_SERVING,
        "completed": len([d for d in D if d["status"] == "Completed"]),
    }


def render_flash():
    if S.flash:
        msg, kind = S.flash
        callout(msg, kind)
        S.flash = None


def status_badges(d):
    h = badge(d["status"], STATUS_CLASS.get(d["status"], "b-info"))
    if d["verified"]:
        h += badge("VERIFIED", "b-ok")
    else:
        h += badge("UNVERIFIED", "b-warn")
    if d["status"] == "Available":
        h += (
            badge("FLASH SALE", "b-info")
            if d["stage"] == "Discount Active"
            else badge("NGO PRIORITY", "b-warn")
        )
    h += badge("VEG" if d["veg"] else "NON-VEG", "b-ok" if d["veg"] else "b-bad")
    return h


def listing_info(d, show_price=True):
    if d.get("photo"):
        st.image(d["photo"], width=170)
    st.markdown(f"### ◈ {esc(d['food_type'])}")
    st.markdown(status_badges(d), unsafe_allow_html=True)
    st.markdown(
        f"**{d['remaining']} / {d['quantity']} UNITS** · {esc(d['venue'])}"
    )
    st.markdown(f"<span class='muted'>LOC // {esc(d['address'])}, {esc(d['area'])}</span>", unsafe_allow_html=True)
    if show_price:
        old = d["original_price"] / d["quantity"]
        st.markdown(
            f'<span class="price-old">₹{old:,.0f}</span>'
            f'<span class="price-new">₹{d["unit_price"]:,.0f}</span>'
            f' <span class="muted">/ UNIT · -{d["discount_percent"]}%</span>',
            unsafe_allow_html=True,
        )


def listing_details(d):
    st.markdown(f"**EXPIRY //** `{fmt(d['deadline'])}`")
    st.markdown(
        badge(f"TTL: {time_left(d['deadline'])}", urgency(d["deadline"])),
        unsafe_allow_html=True,
    )
    st.markdown(f"<span class='muted'>PREP // {fmt(d['prep_time'])}</span>", unsafe_allow_html=True)
    st.markdown(f"<span class='muted'>SPEC // {esc(d['storage'])} · {esc(d['category'])}</span>", unsafe_allow_html=True)
    st.markdown(f"<span class='muted'>COMM // {esc(d.get('contact', ''))}</span>", unsafe_allow_html=True)


# ---------- chat ----------
def _send_msg(did, key):
    t = S.get(key, "").strip()
    if not t:
        return
    DB["messages"].setdefault(did, []).append(
        {"from": S.user, "text": t, "time": datetime.now()}
    )
    d = get_dn(did)
    other = d["claimed_by"] if S.user == d["donor"] else d["donor"]
    if other:
        notify(other, f"COMMS // New transmission on {did} from {S.user}")
    S[key] = ""


def chat(d):
    msgs = DB["messages"].get(d["id"], [])
    with st.expander(f"◈ COMMS CHANNEL ({len(msgs)})"):
        for m in msgs:
            st.markdown(
                f'<div class="bubble"><b>{esc(m["from"])}</b> '
                f'<span class="muted">{fmt(m["time"])}</span><br>{esc(m["text"])}</div>',
                unsafe_allow_html=True,
            )
        key = f"msg_{d['id']}"
        st.text_input(
            "Message",
            key=key,
            label_visibility="collapsed",
            placeholder="Transmit pickup coordinates or ETA...",
        )
        st.button(
            "SEND ↗", key=f"send_{d['id']}", on_click=_send_msg, args=(d["id"], key)
        )


# ---------- actions ----------
def do_claim(d, eta, note):
    d.update(
        claimed_by=S.user,
        claim_qty=d["remaining"],
        claim_status="Pending Donor Approval",
        status="Claim Requested",
        pickup_eta=eta,
    )
    notify(
        d["donor"],
        f"CLAIM_REQ // {S.user} requested {d['food_type']} ({d['id']}).",
    )
    log(f"{S.user} requested claim on {d['id']}", "claim")
    if note:
        DB["messages"].setdefault(d["id"], []).append(
            {"from": S.user, "text": note, "time": datetime.now()}
        )
    flash(f"CLAIM DISPATCHED // Awaiting donor authorization for {d['id']}.")


def receipt_text(d):
    return (
        f"FOODBRIDGE // OFFICIAL HANDOVER MANIFEST\n{'=' * 42}\n"
        f"BATCH ID    : {d['id']}\n"
        f"PAYLOAD     : {d['food_type']}\n"
        f"ORIGIN      : {d['venue']}\n"
        f"RECIPIENT   : {d['claimed_by']}\n"
        f"VOLUME      : {d['claim_qty']} SERVINGS\n"
        f"DESTINATION : {d['beneficiaries'] or 'UNSPECIFIED'}\n"
        f"TIMESTAMP   : {fmt(datetime.now())}\n"
    )


# ============================================================
# SIDEBAR NAVIGATION
# ============================================================
update_stages()

NAV = {
    "Donor": [
        ("◈ Overview", "Dashboard"),
        ("❖ Dispatch Surplus", "Donate"),
        ("⟁ My Batches", "My Donations"),
        ("↗ Telemetry", "Impact"),
        ("● System Alerts", "Alerts"),
    ],
    "NGO": [
        ("◈ Overview", "Dashboard"),
        ("❖ Locate Surplus", "Find Food"),
        ("⟁ Active Claims", "My Claims"),
        ("↗ Telemetry", "Impact"),
        ("● System Alerts", "Alerts"),
    ],
    "Buyer": [
        ("◈ Overview", "Dashboard"),
        ("❖ Flash Market", "Deals"),
        ("⟁ Acquisitions", "My Orders"),
        ("↗ Telemetry", "Impact"),
        ("● System Alerts", "Alerts"),
    ],
    "Admin": [
        ("◈ Command Center", "Dashboard"),
        ("❖ Verify Batches", "Verification"),
        ("⟁ NGO Clearances", "NGO Approvals"),
        ("▣ Registry", "Users"),
        ("↗ Pipeline Matrix", "Analytics"),
        ("≡ Audit Stream", "Activity"),
        ("↗ Telemetry", "Impact"),
        ("● System Alerts", "Alerts"),
    ],
}
GUEST_NAV = [
    ("◈ Terminal Home", "Home"),
    ("❖ Authenticate", "Login"),
    ("↗ Register Node", "Signup"),
]

with st.sidebar:
    st.markdown("## ◈ FOODBRIDGE OS")
    st.caption("ALGORITHMIC SURPLUS DISPATCH v3.0")
    st.divider()
    if S.logged_in:
        st.markdown(f"**{esc(S.user)}**")
        st.markdown(badge(f"ROLE // {S.role}", "b-ok"), unsafe_allow_html=True)
        u = current_user()
        if S.role == "NGO" and u and not u["verified"]:
            st.markdown(
                badge("CLEARANCE PENDING", "b-warn"), unsafe_allow_html=True
            )
        st.write("")
        items = NAV[S.role]
    else:
        items = GUEST_NAV
    unread = len(my_notes()) if S.logged_in else 0
    for label, page in items:
        if page == "Alerts" and unread:
            label = f"● System Alerts [{unread}]"
        st.button(
            label,
            key=f"nav_{page}",
            use_container_width=True,
            on_click=go,
            args=(page,),
            type="primary" if S.page == page else "secondary",
        )
    if S.logged_in:
        st.divider()
        st.button(
            "TERMINATE SESSION ↗",
            key="logout_btn",
            use_container_width=True,
            on_click=logout,
        )
    st.divider()
    with st.expander("⚙ SIMULATION CONTROLS"):
        st.caption("LIVE DEMO OVERRIDES")
        if st.button(
            "⏩ Expire Discount Timers", key="ff_all", use_container_width=True
        ):
            for d in DB["donations"]:
                if d["stage"] == "Discount Active" and d["verified"]:
                    d["discount_end"] = datetime.now() - timedelta(seconds=1)
            update_stages()
            flash("OVERRIDE // All batches escalated to NGO Donation Priority.", "info")
            st.rerun()
        if st.button(
            "♻ Reset State Buffer", key="reset_db", use_container_width=True
        ):
            get_db.clear()
            flash("BUFFER RESET // Default state restored.", "info")
            st.rerun()


# ============================================================
# PAGE VIEWS
# ============================================================
def page_home():
    st.markdown(
        """<div class="hero">
        <h1>FOODBRIDGE NETWORK</h1>
        <p>Real-Time Surplus Recovery & Zero-Waste Redistribution Protocol.</p>
        <p style="font-size:14px;opacity:.75;margin-top:8px;">
        Connecting commercial food producers with flash-sale buyers during Stage-01 recovery, 
        and routing unclaimed inventory to verified NGOs in Stage-02 before spoilage thresholds.
        </p></div>""",
        unsafe_allow_html=True,
    )

    feats = [
        ("MODULE // 01", "Dispatch Surplus", "Log commercial surplus with automated safety TTL tracking.", "Donor"),
        ("MODULE // 02", "NGO Acquisition", "Verified relief agencies claim zero-cost priority batches.", "NGO"),
        ("MODULE // 03", "Flash Liquidation", "Acquire surplus inventory at 40-80% markdown before escalation.", "Buyer"),
        ("MODULE // 04", "Carbon Telemetry", "Live accounting of diverted mass, meals, and CO₂e metrics.", None),
    ]
    cols = st.columns(4)
    for c, (tag, t, p, role) in zip(cols, feats):
        with c:
            st.markdown(
                f'<div class="feature"><div class="f-tag">{tag}</div><h3>{t}</h3><p>{p}</p></div>',
                unsafe_allow_html=True,
            )
            if role:
                def _start(r=role):
                    S.signup_role = r
                    S.page = "Dashboard" if S.logged_in else "Signup"
                st.button(
                    f"INITIALIZE {role.upper()} ↗",
                    key=f"feat_{role}",
                    use_container_width=True,
                    on_click=_start,
                )
            else:
                st.button(
                    "VIEW TELEMETRY ↗",
                    key="feat_impact",
                    use_container_width=True,
                    on_click=go,
                    args=("Impact" if S.logged_in else "Login",),
                )

    st.write("")
    st.markdown("#### ⚡ INSTANT SANDBOX AUTHENTICATION")
    c = st.columns(4)
    for col, (lbl, em) in zip(
        c,
        [
            ("DONOR NODE", "donor@foodbridge.demo"),
            ("NGO NODE", "ngo@foodbridge.demo"),
            ("BUYER NODE", "buyer@foodbridge.demo"),
            ("ADMIN INSPECTOR", "admin@foodbridge.demo"),
        ],
    ):
        col.button(
            f"ACCESS // {lbl}",
            key=f"quick_{em}",
            use_container_width=True,
            on_click=login_as,
            args=(em,),
        )

    st.write("")
    st.markdown("#### ⟁ PROTOCOL EXECUTION PIPELINE")
    flow = [
        ("STAGE 01", "Surplus Logged", "Commercial kitchens input batch parameters & TTL"),
        ("STAGE 02", "Safety Audit", "Inspector verifies hygiene & storage compliance"),
        ("STAGE 03", "Flash Window", "Time-boxed markdown sale recovers base cost"),
        ("STAGE 04", "NGO Escalation", "Unsold inventory unlocks for zero-cost relief"),
        ("STAGE 05", "Dispatch & Handover", "Encrypted code verification & transit tracking"),
        ("STAGE 06", "Impact Ledger", "Immutable log of diverted waste & CO₂e saved"),
    ]
    for col, (n, t, p) in zip(st.columns(6), flow):
        col.markdown(
            f'<div class="flow-box"><div class="n">{n}</div><h4>{t}</h4><p>{p}</p></div>',
            unsafe_allow_html=True,
        )

    st.write("")
    st.markdown("#### ◈ LIVE NETWORK TELEMETRY")
    ps = platform_stats()
    items = [
        (len(DB["donations"]), "ACTIVE BATCHES"),
        (len(ngo_listings()), "READY FOR DISPATCH"),
        (ps["rescued"], "UNITS RESCUED"),
        (f"{ps['co2']:,.0f} KG", "CO₂e DIVERTED"),
    ]
    for col, (n, l) in zip(st.columns(4), items):
        col.markdown(
            f'<div class="stat"><div class="stat-number">{n}</div>'
            f'<div class="stat-label">{l}</div></div>',
            unsafe_allow_html=True,
        )


def page_login():
    page_title("Terminal Authentication", "Establish an encrypted session on the FoodBridge network.")
    _, mid, _ = st.columns([1, 2, 1])
    with mid:
        with st.form("login_form"):
            email = st.text_input("Operator Email")
            pw = st.text_input("Passkey", type="password")
            ok = st.form_submit_button("AUTHENTICATE ↗", use_container_width=True, type="primary")
        if ok:
            u = next(
                (x for x in DB["users"] if x["email"].lower() == email.strip().lower() and x["pw"] == hp(pw)),
                None,
            )
            if u:
                login_as(u["email"])
                st.rerun()
            else:
                callout("AUTH_ERR // Invalid credentials.", "bad")
        st.markdown("##### SANDBOX PRESETS")
        for lbl, em in [
            ("DONOR // donor@foodbridge.demo", "donor@foodbridge.demo"),
            ("RESTAURANT // restaurant@foodbridge.demo", "restaurant@foodbridge.demo"),
            ("NGO // ngo@foodbridge.demo", "ngo@foodbridge.demo"),
            ("BUYER // buyer@foodbridge.demo", "buyer@foodbridge.demo"),
            ("ADMIN // admin@foodbridge.demo", "admin@foodbridge.demo"),
        ]:
            st.button(lbl, key=f"lq_{em}", use_container_width=True, on_click=login_as, args=(em,))


def page_signup():
    page_title("Register Network Node", "Onboard as a commercial donor, verified NGO, or flash buyer.")
    _, mid, _ = st.columns([1, 2, 1])
    roles = ["Donor", "NGO", "Buyer"]
    with mid:
        role = st.selectbox(
            "Node Classification",
            roles,
            index=roles.index(S.signup_role) if S.signup_role in roles else 0,
            key="su_role",
        )
        with st.form("signup_form"):
            name = st.text_input("Entity / Organization Name *")
            email = st.text_input("Official Email *")
            c1, c2 = st.columns(2)
            pw = c1.text_input("Passkey *", type="password")
            cf = c2.text_input("Confirm Passkey *", type="password")
            phone = st.text_input("Contact Line *")
            loc = st.selectbox("Operating Sector", list(AREAS.keys()))
            reg = st.text_input("NGO Registration ID (Required for NGOs)") if role == "NGO" else ""
            ok = st.form_submit_button("INITIALIZE NODE ↗", use_container_width=True, type="primary")
        if ok:
            if not name or not email or not pw or not phone:
                callout("ERR // All mandatory fields must be populated.", "bad")
            elif "@" not in email:
                callout("ERR // Malformed email address.", "bad")
            elif len(pw) < 4:
                callout("ERR // Passkey minimum length is 4 characters.", "bad")
            elif pw != cf:
                callout("ERR // Passkey mismatch.", "bad")
            elif role == "NGO" and not reg:
                callout("ERR // NGO Registration ID required for compliance.", "bad")
            elif any(
                u["email"].lower() == email.strip().lower() or u["name"].lower() == name.strip().lower()
                for u in DB["users"]
            ):
                callout("ERR // Entity already registered.", "bad")
            else:
                DB["users"].append({
                    "name": name.strip(),
                    "email": email.strip(),
                    "pw": hp(pw),
                    "role": role,
                    "phone": phone,
                    "location": loc,
                    "verified": role != "NGO",
                    "reg": reg,
                    "created": datetime.now(),
                })
                log(f"Node registered: {name} ({role})", "signup")
                if role == "NGO":
                    notify("role:Admin", f"CLEARANCE_REQ // NGO '{name}' awaiting verification.")
                login_as(email.strip())
                if role == "NGO":
                    flash("NODE CREATED // Awaiting Admin clearance before claiming batches.", "warn")
                st.rerun()


def page_dashboard():
    page_title(f"Operator // {esc(S.user)}", f"{S.role} Command Console")
    D = DB["donations"]
    ps = platform_stats()

    if S.role == "Donor":
        mine = [d for d in D if d["donor"] == S.user]
        reqs = [d for d in mine if d["status"] == "Claim Requested"]
        if reqs:
            callout(f"ACTION REQUIRED // <b>{len(reqs)}</b> NGO claim request(s) awaiting authorization.", "warn")
            st.button("AUTHORIZE CLAIMS ↗", key="rev_req", on_click=go, args=("My Donations",))
        c = st.columns(5)
        c[0].metric("Total Batches", len(mine))
        c[1].metric("Active", len([d for d in mine if d["status"] == "Available"]))
        c[2].metric("In Transit", len([d for d in mine if d["status"] in ("Claim Requested", "Accepted", "Picked Up")]))
        c[3].metric("Units Donated", sum(d["donated"] for d in mine))
        c[4].metric("Recovered", f"₹{sum(o['amount'] for o in DB['orders'] if get_dn(o['donation_id'])['donor'] == S.user):,.0f}")
        st.markdown("#### RECENT DISPATCH MANIFEST")
        dark_table(
            pd.DataFrame([{
                "BATCH": d["id"],
                "PAYLOAD": d["food_type"],
                "UNITS": f"{d['remaining']}/{d['quantity']}",
                "PHASE": d["stage"],
                "STATUS": d["status"],
                "SAFE UNTIL": fmt(d["deadline"]),
            } for d in mine])
        )
        a, b = st.columns(2)
        a.button("❖ DISPATCH NEW SURPLUS", key="dash_don", type="primary", use_container_width=True, on_click=go, args=("Donate",))
        b.button("⟁ MANAGE BATCHES", key="dash_md", use_container_width=True, on_click=go, args=("My Donations",))

    elif S.role == "NGO":
        u = current_user()
        claims = [d for d in D if d["claimed_by"] == S.user]
        if not u["verified"]:
            callout("RESTRICTED // NGO clearance pending admin audit. Browse mode only.", "warn")
        c = st.columns(4)
        c[0].metric("Priority Batches", len([d for d in ngo_listings() if d["stage"] == "Donation Priority"]))
        c[1].metric("Active Claims", len(claims))
        c[2].metric("In Transit", len([d for d in claims if d["status"] in ("Accepted", "Picked Up")]))
        c[3].metric("Units Delivered", sum(d["donated"] for d in claims))
        st.markdown("#### ⟁ ZERO-COST PRIORITY QUEUE")
        pr = [d for d in ngo_listings() if d["stage"] == "Donation Priority"]
        if not pr:
            callout("No batches in zero-cost priority queue right now.", "info")
        for d in pr[:4]:
            with st.container(border=True):
                a, b = st.columns([4, 1])
                a.markdown(f"**◈ {esc(d['food_type'])}** · `{d['remaining']} UNITS` · {esc(d['area'])} · TTL: `{time_left(d['deadline'])}`")
                b.button("ACQUIRE ↗", key=f"dash_open_{d['id']}", on_click=go, args=("Find Food",))
        st.button("❖ SCAN ALL SECTORS", key="dash_find", type="primary", on_click=go, args=("Find Food",))

    elif S.role == "Buyer":
        my = [o for o in DB["orders"] if o["buyer"] == S.user]
        c = st.columns(4)
        c[0].metric("Live Flash Deals", len(deals()))
        c[1].metric("Acquisitions", len(my))
        c[2].metric("Units Purchased", sum(o["qty"] for o in my))
        c[3].metric("Capital Saved", f"₹{sum(o['saved'] for o in my):,.0f}")
        st.markdown("#### ⚡ EXPIRING FLASH LIQUIDATIONS")
        dl = sorted(deals(), key=lambda x: x["discount_end"])
        if not dl:
            callout("No active flash liquidations at this moment.", "info")
        for d in dl[:3]:
            with st.container(border=True):
                a, b = st.columns([4, 1])
                a.markdown(f"**◈ {esc(d['food_type'])}** · `₹{d['unit_price']:,.0f}/UNIT` (-{d['discount_percent']}%) · WINDOW: `{time_left(d['discount_end'])}`")
                b.button("ACQUIRE ↗", key=f"dash_buy_{d['id']}", on_click=go, args=("Deals",))

    else:
        pend = [d for d in D if not d["verified"] and d["status"] == "Pending Verification"]
        pend_ngo = [u for u in DB["users"] if u["role"] == "NGO" and not u["verified"]]
        c = st.columns(5)
        c[0].metric("Total Batches", len(D))
        c[1].metric("Awaiting Audit", len(pend))
        c[2].metric("NGO Clearances", len(pend_ngo))
        c[3].metric("Registered Nodes", len(DB["users"]))
        c[4].metric("Units Rescued", ps["rescued"])
        if pend:
            callout(f"AUDIT QUEUE // {len(pend)} batch(es) require safety verification.", "warn")
            st.button("OPEN VERIFICATION QUEUE ↗", key="adm_v", on_click=go, args=("Verification",))
        if pend_ngo:
            callout(f"CLEARANCE QUEUE // {len(pend_ngo)} NGO node(s) awaiting approval.", "warn")
            st.button("OPEN NGO CLEARANCES ↗", key="adm_n", on_click=go, args=("NGO Approvals",))
        st.markdown("#### RECENT TELEMETRY STREAM")
        for a in DB["activity"][:8]:
            st.markdown(f"`{fmt(a['time'])}` // {esc(a['message'])}")


# ---------- donor ----------
DONATE_KEYS = [
    "d_venue", "d_food", "d_cat", "d_qty", "d_veg", "d_prep", "d_safe",
    "d_storage", "d_photo", "d_area", "d_addr", "d_contact", "d_price",
    "d_disc", "d_dur", "d_safe1", "d_safe2", "d_notes",
]


def page_donate():
    page_title("Dispatch Surplus Payload", "Register commercial food surplus for flash recovery and NGO routing.")
    now = datetime.now()
    with st.container(border=True):
        st.markdown("#### 01 // PAYLOAD SPECIFICATION")
        c1, c2 = st.columns(2)
        with c1:
            venue = st.text_input("Origin Facility / Venue *", value=S.user, key="d_venue")
            food = st.text_input("Payload Description *", placeholder="e.g. Hyderabadi Veg Dum Biryani", key="d_food")
            cat = st.selectbox("Classification", CATEGORIES, key="d_cat")
            qty = st.number_input("Volume (Servings) *", 1, 5000, 50, key="d_qty")
            veg = st.checkbox("100% Vegetarian Payload", value=True, key="d_veg")
        with c2:
            prep_t = st.time_input("Preparation Timestamp", value=(now - timedelta(hours=1)).time(), key="d_prep")
            safe_h = st.slider("Safe Consumption TTL (Hours from now)", 1, 12, 4, key="d_safe")
            storage = st.selectbox("Thermal / Storage State", STORAGE, key="d_storage")
            photo = st.file_uploader("Visual Verification (Optional)", type=["png", "jpg", "jpeg"], key="d_photo")
            notes = st.text_area("Handling & Allergen Telemetry", key="d_notes", height=68)

    with st.container(border=True):
        st.markdown("#### 02 // GEOLOCATION & HANDOVER")
        c3, c4, c5 = st.columns(3)
        area = c3.selectbox("Operating Sector", list(AREAS.keys()), key="d_area")
        addr = c4.text_input("Dispatch Coordinates / Address *", key="d_addr")
        contact = c5.text_input("Comms Line (10-digit) *", placeholder="98100XXXXX", key="d_contact")

    with st.container(border=True):
        st.markdown("#### 03 // STAGE-01 FLASH LIQUIDATION PARAMETERS")
        c6, c7, c8 = st.columns(3)
        price = c6.number_input("Base Commercial Valuation (₹)", 0, 500000, 5000, step=100, key="d_price")
        disc = c7.slider("Markdown Rate (%)", 10, 90, 50, 5, key="d_disc")
        dur = c8.selectbox("Flash Window Duration", ["15 minutes", "30 minutes", "45 minutes", "60 minutes", "90 minutes"], index=2, key="d_dur")
        minutes = int(dur.split()[0])
        unit = round(price * (1 - disc / 100) / qty, 2)
        p1, p2, p3 = st.columns(3)
        p1.metric("Flash Unit Price", f"₹{unit:,.0f}")
        p2.metric("Net Recovery Target", f"₹{price * (1 - disc / 100):,.0f}")
        p3.metric("Escalation Trigger", f"T+{minutes}M post-audit")

    with st.container(border=True):
        st.markdown("#### 04 // COMPLIANCE ATTESTATION")
        s1 = st.checkbox("I attest that this payload is unconsumed and fit for human consumption.", key="d_safe1")
        s2 = st.checkbox("Containment meets FSSAI-equivalent thermal and hygiene standards.", key="d_safe2")

        if st.button("TRANSMIT BATCH TO NETWORK ↗", type="primary", use_container_width=True, key="d_submit"):
            if not food.strip() or not addr.strip() or not venue.strip():
                callout("ERR // Populate Payload Description, Venue, and Address.", "bad")
            elif not (contact.isdigit() and len(contact) == 10):
                callout("ERR // Comms Line must be a valid 10-digit number.", "bad")
            elif not (s1 and s2):
                callout("ERR // Both safety compliance attestations are required.", "bad")
            else:
                prep = datetime.combine(now.date(), prep_t)
                if prep > now:
                    prep -= timedelta(days=1)
                lat, lon = AREAS[area]
                nid = f"FB{len(DB['donations']) + 1:03d}"
                DB["donations"].append({
                    "id": nid, "donor": S.user, "venue": venue.strip(), "food_type": food.strip(), "category": cat,
                    "quantity": qty, "remaining": qty, "sold": 0, "donated": 0, "original_price": price,
                    "discount_percent": disc, "unit_price": unit, "discount_minutes": minutes, "discount_end": None,
                    "stage": "Discount Active", "status": "Pending Verification", "verified": False, "prep_time": prep,
                    "deadline": now + timedelta(hours=safe_h), "area": area, "address": addr.strip(),
                    "lat": lat + random.uniform(-0.004, 0.004), "lon": lon + random.uniform(-0.004, 0.004),
                    "storage": storage, "veg": veg, "contact": contact, "photo": photo.getvalue() if photo else None,
                    "claimed_by": "", "claim_qty": 0, "claim_status": "", "pickup_eta": "", "beneficiaries": "",
                    "watchers": [], "created": now, "notes": notes,
                })
                log(f"Batch {nid} logged by {S.user}", "donation")
                notify("role:Admin", f"AUDIT_REQ // Batch {nid} ({food.strip()}) awaiting safety verification.")
                for k in DONATE_KEYS:
                    S.pop(k, None)
                flash(f"BATCH {nid} LOGGED // Routed to Admin Inspector queue.")
                S.page = "My Donations"
                st.rerun()


def page_my_donations():
    page_title("My Dispatch Batches", "Monitor batch lifecycle, authorize NGO claims, and verify handovers.")
    mine = [d for d in DB["donations"] if d["donor"] == S.user]
    if not mine:
        callout("No batches logged under your operator ID.", "info")
        st.button("❖ DISPATCH SURPLUS", key="md_new", type="primary", on_click=go, args=("Donate",))
        return
    flt = st.selectbox("Filter Lifecycle State", ["All"] + sorted({d["status"] for d in mine}), key="md_flt")
    for d in reversed(mine):
        if flt != "All" and d["status"] != flt:
            continue
        with st.container(border=True):
            a, b = st.columns([3, 2])
            with a:
                st.markdown(f"`BATCH // {d['id']}`")
                listing_info(d)
            with b:
                listing_details(d)
                if d["status"] == "Available" and d["stage"] == "Discount Active":
                    live_timer(d["discount_end"], "FLASH WINDOW")
            tracker(d)
            st.caption(f"TELEMETRY // SOLD: {d['sold']} · DONATED: {d['donated']} · REMAINING: {d['remaining']}")

            if d["status"] == "Pending Verification":
                callout("AWAITING ADMIN SAFETY CLEARANCE.", "warn")
            if d["status"] in ("Pending Verification", "Available"):
                x, y = st.columns(2)
                if d["status"] == "Available" and d["stage"] == "Discount Active":
                    if x.button("⏩ ESCALATE TO NGO PRIORITY", key=f"end_{d['id']}"):
                        d["discount_end"] = datetime.now() - timedelta(seconds=1)
                        update_stages()
                        flash(f"{d['id']} escalated to NGO Donation Priority.")
                        st.rerun()
                if y.button("✕ ABORT BATCH", key=f"cancel_{d['id']}"):
                    d["status"] = "Cancelled"
                    log(f"{d['id']} aborted by donor", "cancel")
                    flash(f"{d['id']} aborted.", "warn")
                    st.rerun()
            if d["status"] == "Claim Requested":
                callout(f"CLAIM REQUEST // <b>{esc(d['claimed_by'])}</b> requested {d['claim_qty']} units. ETA: {esc(d['pickup_eta'] or 'N/A')}.", "warn")
                x, y = st.columns(2)
                if x.button("✓ AUTHORIZE HANDOVER", key=f"acc_{d['id']}", type="primary"):
                    d["status"], d["claim_status"] = "Accepted", "Accepted"
                    notify(d["claimed_by"], f"AUTHORIZED // {d['donor']} approved claim on {d['id']}.")
                    log(f"{d['id']} claim authorized", "accepted")
                    flash("Handover authorized.")
                    st.rerun()
                if y.button("✕ DECLINE", key=f"rej_{d['id']}"):
                    notify(d["claimed_by"], f"DECLINED // Claim on {d['id']} was declined.")
                    d.update(status="Available", claim_status="Rejected", claimed_by="", claim_qty=0)
                    log(f"{d['id']} claim declined", "rejected")
                    st.rerun()
            if d["status"] == "Accepted":
                callout(f"HANDOVER SCHEDULED // Recipient: <b>{esc(d['claimed_by'])}</b>", "info")
                if st.button("⟁ CONFIRM CUSTODY TRANSFER", key=f"pk_{d['id']}", type="primary"):
                    d["status"] = "Picked Up"
                    log(f"{d['id']} custody transferred", "pickup")
                    notify(d["claimed_by"], f"TRANSIT // {d['id']} marked in transit.")
                    st.rerun()
            if d["status"] == "Picked Up":
                if st.button("✓ VERIFY FINAL DISTRIBUTION", key=f"cp_{d['id']}", type="primary"):
                    d["status"] = "Completed"
                    d["donated"] += d["claim_qty"]
                    d["remaining"] -= d["claim_qty"]
                    log(f"{d['id']} distribution verified ({d['claim_qty']} units)", "completed")
                    flash("Batch lifecycle completed.")
                    st.rerun()
            orders = [o for o in DB["orders"] if o["donation_id"] == d["id"]]
            if orders:
                with st.expander(f"◈ FLASH ORDERS ({len(orders)})"):
                    for o in orders:
                        r1, r2 = st.columns([4, 1])
                        r1.markdown(
                            f"`{o['id']}` · {esc(o['buyer'])} · **{o['qty']} units** · ₹{o['amount']:,.0f} · TOKEN: `{o['code']}` · "
                            f"{badge(o['status'], 'b-ok' if o['status'] == 'Collected' else 'b-warn')}",
                            unsafe_allow_html=True,
                        )
                        if o["status"] != "Collected" and r2.button("VERIFY", key=f"col_{o['id']}"):
                            o["status"] = "Collected"
                            notify(o["buyer"], f"COLLECTED // Order {o['id']} verified.")
                            st.rerun()
            if d["claimed_by"] or d["status"] in ("Claim Requested", "Accepted", "Picked Up", "Completed"):
                chat(d)
            if d["status"] == "Completed":
                st.download_button("⬇ EXPORT MANIFEST", receipt_text(d), file_name=f"manifest_{d['id']}.txt", key=f"rc_{d['id']}")


# ---------- NGO ----------
def page_find_food():
    page_title("Surplus Radar", "Locate verified batches across sectors and initiate acquisition claims.")
    u = current_user()
    if not u["verified"]:
        callout("CLEARANCE PENDING // Browse mode active. Admin approval required to claim.", "warn")
    avail = ngo_listings()
    c = st.columns([2, 1, 1, 1, 1])
    q = c[0].text_input("Query Payload / Sector", placeholder="Biryani, Noida...", key="ff_q")
    cat = c[1].selectbox("Classification", ["All"] + sorted({d["category"] for d in avail}), key="ff_cat")
    stage = c[2].selectbox("Phase", ["All", "Donation Priority", "Discount Active"], key="ff_stage")
    sort = c[3].selectbox("Sort Priority", ["TTL Soonest", "Volume High", "Newest"], key="ff_sort")
    min_q = c[4].number_input("Min Units", 0, 5000, 0, key="ff_minq")
    veg = st.checkbox("Vegetarian Only", key="ff_veg")

    res = [
        d for d in avail
        if (not q or q.lower() in (d["food_type"] + d["area"] + d["venue"] + d["address"]).lower())
        and (cat == "All" or d["category"] == cat)
        and (stage == "All" or d["stage"] == stage)
        and d["remaining"] >= min_q
        and (not veg or d["veg"])
    ]
    res.sort(
        key=(lambda d: d["deadline"]) if sort.startswith("TTL") else (lambda d: -d["remaining"]) if sort.startswith("Volume") else (lambda d: d["created"]),
        reverse=sort == "Newest",
    )

    t1, t2 = st.tabs(["◈ RADAR FEED", "⟁ GEOSPATIAL MAP"])
    with t2:
        if res:
            st.map(pd.DataFrame([{"lat": d["lat"], "lon": d["lon"]} for d in res]))
            dark_table(pd.DataFrame([{"BATCH": d["id"], "PAYLOAD": d["food_type"], "SECTOR": d["area"], "UNITS": d["remaining"]} for d in res]))
        else:
            callout("No active coordinates in filter range.", "info")
    with t1:
        st.markdown(f"##### {len(res)} ACTIVE BATCH(ES) IN RANGE")
        for d in res:
            with st.container(border=True):
                a, b, c3 = st.columns([3, 2, 2])
                with a:
                    listing_info(d, show_price=False)
                with b:
                    listing_details(d)
                with c3:
                    if d["stage"] == "Discount Active":
                        live_timer(d["discount_end"], "NGO UNLOCK IN")
                        callout("STAGE-01 FLASH WINDOW ACTIVE.", "info")
                        watching = S.user in d["watchers"]
                        if st.button("✕ CLEAR WATCH" if watching else "● SET AUTO-ALERT", key=f"w_{d['id']}", use_container_width=True):
                            d["watchers"].remove(S.user) if watching else d["watchers"].append(S.user)
                            flash("Watch trigger updated.", "info")
                            st.rerun()
                    else:
                        callout(f"STAGE-02 UNLOCKED // {d['remaining']} UNITS AVAILABLE", "ok")
                        if u["verified"]:
                            eta = st.selectbox("Dispatch ETA", ["30 mins", "1 hour", "2 hours", "3 hours"], key=f"eta_{d['id']}")
                            note = st.text_input("Dispatch Note", key=f"note_{d['id']}")
                            if st.button("ACQUIRE BATCH ↗", key=f"claim_{d['id']}", type="primary", use_container_width=True):
                                do_claim(d, eta, note)
                                S.page = "My Claims"
                                st.rerun()
                        else:
                            st.button("CLEARANCE REQUIRED", key=f"lock_{d['id']}", disabled=True, use_container_width=True)


def page_my_claims():
    page_title("Active Acquisitions", "Track custody chain and log beneficiary distribution.")
    claims = [d for d in DB["donations"] if d["claimed_by"] == S.user]
    if not claims:
        callout("No active acquisitions.", "info")
        st.button("❖ LOCATE SURPLUS", key="mc_find", type="primary", on_click=go, args=("Find Food",))
        return
    for d in reversed(claims):
        with st.container(border=True):
            a, b = st.columns([3, 2])
            with a:
                st.markdown(f"`BATCH // {d['id']}`")
                listing_info(d, show_price=False)
            with b:
                listing_details(d)
                st.markdown(f"**DISPATCH ETA //** `{esc(d['pickup_eta'] or '—')}`")
            tracker(d)
            if d["status"] == "Claim Requested":
                callout("AWAITING DONOR AUTHORIZATION.", "warn")
            elif d["status"] == "Accepted":
                callout("AUTHORIZED // Proceed to origin coordinates.", "ok")
                if st.button("⟁ CONFIRM PAYLOAD PICKUP", key=f"np_{d['id']}", type="primary"):
                    d["status"] = "Picked Up"
                    notify(d["donor"], f"TRANSIT // {S.user} picked up {d['id']}.")
                    log(f"{S.user} picked up {d['id']}", "pickup")
                    st.rerun()
            elif d["status"] == "Picked Up":
                callout("IN TRANSIT // Log destination telemetry to close batch.", "info")
                ben = st.text_input("Distribution Sector & Beneficiary Count", key=f"ben_{d['id']}", placeholder="e.g. Rohini Sector 3 Camp — 80 beneficiaries")
                if st.button("✓ LOG DISTRIBUTION COMPLETE", key=f"nc_{d['id']}", type="primary"):
                    d["beneficiaries"] = ben
                    d["status"] = "Completed"
                    d["donated"] += d["claim_qty"]
                    d["remaining"] -= d["claim_qty"]
                    notify(d["donor"], f"COMPLETED // {S.user} distributed {d['claim_qty']} units from {d['id']}.")
                    log(f"{S.user} completed distribution of {d['id']}", "completed")
                    flash("Distribution logged to immutable ledger.")
                    st.rerun()
            elif d["status"] == "Completed":
                callout(f"VERIFIED // {d['claim_qty']} units distributed.", "ok")
                st.download_button("⬇ EXPORT MANIFEST", receipt_text(d), file_name=f"manifest_{d['id']}.txt", key=f"nr_{d['id']}")
            chat(d)


# ---------- buyer ----------
def page_deals():
    page_title("Flash Liquidation Market", "Acquire commercial surplus during Stage-01 markdown windows.")
    dl = sorted(deals(), key=lambda x: x["discount_end"])
    if not dl:
        callout("No active flash liquidations right now.", "info")
    for d in dl:
        with st.container(border=True):
            a, b, c = st.columns([3, 2, 2])
            with a:
                listing_info(d)
            with b:
                listing_details(d)
            with c:
                live_timer(d["discount_end"], "WINDOW CLOSES")
                qty = st.number_input("Units", 1, d["remaining"], min(2, d["remaining"]), key=f"bq_{d['id']}")
                pay = st.selectbox("Settlement Rail", ["UPI Instant", "Corporate Card", "Terminal Settlement"], key=f"bp_{d['id']}")
                st.markdown(f"**NET SETTLEMENT: `₹{qty * d['unit_price']:,.0f}`**")
                if st.button("EXECUTE ORDER ↗", key=f"buy_{d['id']}", type="primary", use_container_width=True):
                    if datetime.now() >= d["discount_end"]:
                        flash("ERR // Flash window elapsed.", "bad")
                        st.rerun()
                    code = str(random.randint(1000, 9999))
                    oid = f"OD{len(DB['orders']) + 1:03d}"
                    orig = d["original_price"] / d["quantity"]
                    DB["orders"].append({
                        "id": oid, "donation_id": d["id"], "buyer": S.user, "qty": qty,
                        "amount": round(qty * d["unit_price"], 2), "saved": round(qty * (orig - d["unit_price"]), 2),
                        "method": pay, "code": code, "status": "Confirmed", "time": datetime.now(),
                    })
                    d["remaining"] -= qty
                    d["sold"] += qty
                    if d["remaining"] <= 0:
                        d["status"] = "Sold Out"
                    notify(d["donor"], f"ORDER // {S.user} acquired {qty} units of {d['food_type']} ({oid}).")
                    log(f"{S.user} acquired {qty} units of {d['food_type']}", "sale")
                    flash(f"ORDER {oid} EXECUTED // Present token <b>{code}</b> at {esc(d['address'])}.")
                    S.page = "My Orders"
                    st.rerun()


def page_my_orders():
    page_title("Acquisition Ledger", "Your flash market orders and cryptographic handover tokens.")
    my = [o for o in DB["orders"] if o["buyer"] == S.user]
    if not my:
        callout("No acquisitions recorded.", "info")
        st.button("❖ OPEN FLASH MARKET", key="mo_deals", type="primary", on_click=go, args=("Deals",))
        return
    for o in reversed(my):
        d = get_dn(o["donation_id"])
        with st.container(border=True):
            a, b = st.columns([3, 1])
            a.markdown(f"`{o['id']}` · **◈ {esc(d['food_type'])}** × `{o['qty']} UNITS` · **₹{o['amount']:,.0f}** ({o['method']})")
            a.markdown(f"LOC // {esc(d['address'])}, {esc(d['area'])} · {badge(o['status'], 'b-ok' if o['status'] == 'Collected' else 'b-warn')}", unsafe_allow_html=True)
            a.caption(f"TIMESTAMP: {fmt(o['time'])} · SAVED: ₹{o['saved']:,.0f}")
            b.markdown(f'<div class="stat"><div class="stat-label">HANDOVER TOKEN</div><div class="stat-number">{o["code"]}</div></div>', unsafe_allow_html=True)


# ---------- admin ----------
def page_verification():
    page_title("Batch Safety Verification", "Audit incoming commercial surplus before network publication.")
    pend = [d for d in DB["donations"] if d["status"] == "Pending Verification"]
    if not pend:
        callout("VERIFICATION QUEUE CLEAR.", "ok")
        return
    for d in pend:
        with st.container(border=True):
            a, b = st.columns([3, 2])
            with a:
                st.markdown(f"`BATCH // {d['id']}`")
                listing_info(d)
                if d["notes"]:
                    st.caption(f"TELEMETRY NOTES: {d['notes']}")
            with b:
                listing_details(d)
                st.markdown(f"OPERATOR: **{esc(d['donor'])}** · FLASH WINDOW: `{d['discount_minutes']}M`")
            reason = st.text_input("Audit Rejection Code / Note", key=f"rr_{d['id']}")
            x, y = st.columns(2)
            if x.button("✓ AUTHORIZE & PUBLISH", key=f"ver_{d['id']}", type="primary", use_container_width=True):
                d.update(
                    verified=True, status="Available", stage="Discount Active",
                    discount_end=datetime.now() + timedelta(minutes=d["discount_minutes"]),
                )
                log(f"Inspector verified {d['id']}", "verification")
                notify(d["donor"], f"VERIFIED // {d['id']} is live on the network.")
                flash(f"{d['id']} authorized and published.")
                st.rerun()
            if y.button("✕ REJECT BATCH", key=f"arej_{d['id']}", use_container_width=True):
                d["status"] = "Rejected"
                log(f"Inspector rejected {d['id']}", "rejection")
                notify(d["donor"], f"REJECTED // {d['id']}. Reason: {reason or 'Non-compliance'}")
                st.rerun()


def page_ngo_approvals():
    page_title("NGO Node Clearances", "Audit NGO registration credentials before granting acquisition rights.")
    ngos = [u for u in DB["users"] if u["role"] == "NGO"]
    pend = [u for u in ngos if not u["verified"]]
    if not pend:
        callout("NO PENDING NGO CLEARANCES.", "ok")
    for u in pend:
        with st.container(border=True):
            a, b, c = st.columns([3, 1, 1])
            a.markdown(f"**{esc(u['name'])}** · `{esc(u['email'])}`  \nREG_ID: `{esc(u['reg'] or '—')}` · SECTOR: {esc(u['location'])} · COMM: `{esc(u['phone'])}`")
            if b.button("✓ GRANT CLEARANCE", key=f"na_{u['email']}", type="primary"):
                u["verified"] = True
                notify(u["name"], "CLEARANCE GRANTED // Node authorized for surplus acquisition.")
                log(f"NGO {u['name']} cleared", "verification")
                st.rerun()
            if c.button("✕ REJECT", key=f"nr_{u['email']}"):
                DB["users"].remove(u)
                st.rerun()
    st.markdown("#### AUTHORIZED NGO NODES")
    for u in [x for x in ngos if x["verified"]]:
        a, b = st.columns([4, 1])
        a.markdown(f"● **{esc(u['name'])}** · `{esc(u['reg'] or '—')}` · {esc(u['location'])}")
        if b.button("REVOKE", key=f"rv_{u['email']}"):
            u["verified"] = False
            st.rerun()


def page_users():
    page_title("Operator Registry", "Active network nodes and clearance states.")
    dark_table(pd.DataFrame([{
        "ENTITY": u["name"], "EMAIL": u["email"], "CLASS": u["role"],
        "SECTOR": u["location"], "CLEARANCE": "ACTIVE" if u["verified"] else "PENDING",
        "REGISTERED": fmt(u["created"]),
    } for u in DB["users"]]))


def page_analytics():
    page_title("Pipeline Matrix", "Network-wide batch distribution and category telemetry.")
    D = DB["donations"]
    c = st.columns(5)
    c[0].metric("Total Batches", len(D))
    c[1].metric("Live", len([d for d in D if d["status"] == "Available"]))
    c[2].metric("Claims", len([d for d in D if d["status"] == "Claim Requested"]))
    c[3].metric("Completed", len([d for d in D if d["status"] == "Completed"]))
    c[4].metric("Expired", len([d for d in D if d["status"] == "Expired"]))
    st.markdown("#### LIFECYCLE DISTRIBUTION")
    bars({s: len([d for d in D if d["status"] == s]) for s in
          ["Pending Verification", "Available", "Claim Requested", "Accepted", "Picked Up", "Completed", "Sold Out", "Expired", "Rejected", "Cancelled"]
          if any(d["status"] == s for d in D)})
    st.markdown("#### VOLUME BY CLASSIFICATION")
    cats = {}
    for d in D:
        cats[d["category"]] = cats.get(d["category"], 0) + d["quantity"]
    bars(cats, " U")


def page_activity():
    page_title("System Audit Stream", "Immutable chronological log of network events.")
    q = st.text_input("Filter Stream", placeholder="Query batch ID or operator...", key="act_q")
    rows = [a for a in DB["activity"] if q.lower() in a["message"].lower()]
    for a in rows[:60]:
        st.markdown(f"`{fmt(a['time'])}` · **[{a['type'].upper()}]** // {esc(a['message'])}")


def page_impact():
    page_title("Carbon & Impact Telemetry", "Real-time accounting of rescued mass, meals, and emissions avoided.")
    ps = platform_stats()
    c = st.columns(4)
    c[0].metric("Units Rescued", f"{ps['rescued']:,}")
    c[1].metric("NGO Relief Units", f"{ps['donated']:,}")
    c[2].metric("Flash Liquidated", f"{ps['sold']:,}")
    c[3].metric("CO₂e Diverted", f"{ps['co2']:,.0f} kg")
    c = st.columns(4)
    c[0].metric("Capital Recovered", f"₹{ps['revenue']:,.0f}")
    c[1].metric("Buyer Savings", f"₹{ps['savings']:,.0f}")
    c[2].metric("In Transit", f"{ps['in_prog']:,}")
    c[3].metric("Expired", f"{ps['wasted']:,}")

    D = DB["donations"]
    st.markdown("#### RESCUED VOLUME BY CLASSIFICATION")
    cats = {}
    for d in D:
        if d["donated"] + d["sold"]:
            cats[d["category"]] = cats.get(d["category"], 0) + d["donated"] + d["sold"]
    bars(cats, " U")


def page_alerts():
    page_title("System Alerts & TTL Monitor", "Real-time operational notifications and expiry countdowns.")
    notes = my_notes()
    if not notes:
        callout("NO UNREAD TRANSMISSIONS IN BUFFER.", "ok")
    else:
        if st.button("PURGE BUFFER", key="clr_all"):
            for n in notes:
                n["dismissed"].append(S.user)
            st.rerun()
        for i, n in enumerate(notes[:30]):
            a, b = st.columns([8, 1])
            a.markdown(f'<div class="callout c-info">{esc(n["msg"])} <span class="muted">// {fmt(n["time"])}</span></div>', unsafe_allow_html=True)
            if b.button("✕", key=f"dis_{i}_{id(n)}"):
                n["dismissed"].append(S.user)
                st.rerun()


# ============================================================
# ROUTER
# ============================================================
PAGES = {
    "Home": page_home, "Login": page_login, "Signup": page_signup, "Dashboard": page_dashboard,
    "Donate": page_donate, "My Donations": page_my_donations, "Find Food": page_find_food,
    "My Claims": page_my_claims, "Deals": page_deals, "My Orders": page_my_orders,
    "Verification": page_verification, "NGO Approvals": page_ngo_approvals, "Users": page_users,
    "Analytics": page_analytics, "Activity": page_activity, "Impact": page_impact, "Alerts": page_alerts,
}
ROLE_ONLY = {
    "Donate": {"Donor"}, "My Donations": {"Donor"}, "Find Food": {"NGO"}, "My Claims": {"NGO"},
    "Deals": {"Buyer"}, "My Orders": {"Buyer"}, "Verification": {"Admin"}, "NGO Approvals": {"Admin"},
    "Users": {"Admin"}, "Analytics": {"Admin"}, "Activity": {"Admin"},
}
PUBLIC = {"Home", "Login", "Signup"}

page = S.page if S.page in PAGES else "Home"
if page not in PUBLIC and not S.logged_in:
    page = "Login"
elif S.logged_in and page in ("Login", "Signup"):
    page = "Dashboard"
S.page = page

render_flash()
if page in ROLE_ONLY and S.role not in ROLE_ONLY[page]:
    callout("ACCESS DENIED // Insufficient clearance for this module.", "bad")
else:
    PAGES[page]()

st.markdown(
    '<div class="footer">FOODBRIDGE OS v3.0 // ALGORITHMIC SURPLUS DISPATCH & ZERO-WASTE NETWORK</div>',
    unsafe_allow_html=True,
)
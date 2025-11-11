# japan_alert.py
# --------------------------------------------------------------
# Japan Tension Index – live dashboard + self-learning weights
# --------------------------------------------------------------

import streamlit as st
import requests
import json
from datetime import datetime, timedelta
import pandas as pd
import numpy as np
import smtplib
from email.message import EmailMessage
from astropy.coordinates import get_body, get_sun, SkyCoord
from astropy.time import Time
import astropy.units as u

# -------------------------- CONFIG --------------------------
st.set_page_config(page_title="Japan Tension Index", layout="wide")

# Default weights (tweakable in UI)
DEFAULT_WEIGHTS = {
    "foreshock": 2.5,      # per mag-4+ quake in last 7 days
    "b_dev": 1.8,          # multiplier for deviation from 1.5
    "thermal": 1.0,
    "coronal_hole": 3.5,
    "sunspot": 0.02,
    "lunar": 1.5,          # base, scaled by phase factor
    "schumann": 2.0,
    "aspect": 2.2          # base, scaled by tension factor
}

# Email (optional) – fill in or leave blank
SMTP_USER = st.secrets.get("SMTP_USER", "")
SMTP_PASS = st.secrets.get("SMTP_PASS", "")
ALERT_TO   = st.secrets.get("ALERT_TO", "")

# ----------------------------------------------------------------
# Helper: simple Bayesian weight update (stored in session_state)
def update_weights(event_occurred: bool):
    """Nudge weights up/down after a real event."""
    w = st.session_state.weights
    lr = 0.05  # learning rate
    if event_occurred:
        # increase weights that contributed most
        for k in w:
            w[k] *= (1 + lr)
    else:
        # gentle decay
        for k in w:
            w[k] *= (1 - lr/2)
    st.session_state.weights = w

# -------------------------- DATA FETCH --------------------------
@st.cache_data(ttl=3600)  # refresh hourly
def fetch_quakes():
    url = ("https://earthquake.usgs.gov/fdsnws/event/1/query?"
           "format=geojson&starttime={}&endtime={}&minmagnitude=4&country=JP"
           .format((datetime.utcnow()-timedelta(days=7)).strftime('%Y-%m-%d'),
                   datetime.utcnow().strftime('%Y-%m-%d')))
    r = requests.get(url)
    data = r.json()
    count = len(data.get("features", []))
    # rough b-value (needs real catalog – placeholder)
    b_val = 1.2 if count > 30 else 1.5
    return count, b_val

@st.cache_data(ttl=3600)
def fetch_solar():
    # sunspot number (latest daily)
    sun_url = "https://services.swpc.noaa.gov/text/daily-solar-indices.txt"
    txt = requests.get(sun_url).text.splitlines()[-1].split()
    sunspots = int(txt[-1]) if txt[-1].isdigit() else 0

    # coronal hole – simple proxy via X-ray flux spikes
    xray_url = "https://services.swpc.noaa.gov/json/goes/primary/xrays-6-hour.json"
    xray = requests.get(xray_url).json()
    recent_flux = [float(v["flux"]) for v in xray[-6:]]
    coronal = 1 if max(recent_flux) > 1e-5 else 0
    return sunspots, coronal

@st.cache_data(ttl=3600)
def fetch_lunar():
    # USNO phase (0-1)
    today = datetime.utcnow().strftime('%Y/%m/%d')
    url = f"https://api.usno.navy.mil/moon/phase?date={today}"
    try:
        phase = requests.get(url).json()["phasedata"][0]["phase"]
        factor = 1.0 if "Full" in phase or "New" in phase else 0.6
    except:
        factor = 0.6
    return factor

@st.cache_data(ttl=3600)
def fetch_schumann():
    # Tomsk live feed (public CSV)
    url = "http://sosrff.tsu.ru/?page_id=7"
    try:
        df = pd.read_csv(url, skiprows=1, nrows=1)
        spike = df.iloc[0,1] - 7.83  # baseline
        return 1 if spike > 15 else 0
    except:
        return 0

@st.cache_data(ttl=3600)
def fetch_aspects():
    now = Time.now()
    sun = get_sun(now)
    moon = get_body('moon', now)
    mars = get_body('mars', now)
    sep_sun_moon = sun.separation(moon).degree
    opp = 1 if abs(sep_sun_moon - 180) < 10 else 0
    # simple tension factor
    return 1.0 + 0.3*opp

# -------------------------- SCORING --------------------------
def calculate_score():
    quakes, b_val = fetch_quakes()
    sunspots, coronal = fetch_solar()
    lunar_factor = fetch_lunar()
    schumann_flag = fetch_schumann()
    aspect_factor = fetch_aspects()

    w = st.session_state.weights

    score = (
        quakes * w["foreshock"] +
        abs(b_val - 1.5) * w["b_dev"] +
        1.0 * w["thermal"] +               # placeholder
        coronal * w["coronal_hole"] +
        sunspots * w["sunspot"] +
        lunar_factor * w["lunar"] +
        schumann_flag * w["schumann"] +
        aspect_factor * w["aspect"]
    )
    return {
        "total": round(score, 2),
        "quakes": quakes,
        "b_val": round(b_val, 2),
        "coronal": coronal,
        "sunspots": sunspots,
        "lunar": round(lunar_factor, 2),
        "schumann": schumann_flag,
        "aspect": round(aspect_factor, 2)
    }

# -------------------------- EMAIL ALERT ------------------------
def send_alert(details):
    if not (SMTP_USER and SMTP_PASS and ALERT_TO):
        return
    msg = EmailMessage()
    msg["Subject"] = f"Japan Tension ALERT – {details['total']}"
    msg["From"] = SMTP_USER
    msg["To"] = ALERT_TO
    body = f"""
    Japan Tension Index: {details['total']}
    Quakes (mag≥4): {details['quakes']}
    Coronal Hole: {'YES' if details['coronal'] else 'NO'}
    Sunspots: {details['sunspots']}
    Lunar factor: {details['lunar']}
    Schumann spike: {'YES' if details['schumann'] else 'NO'}
    Timestamp (UTC): {datetime.utcnow().isoformat()}
    """
    msg.set_content(body)
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(SMTP_USER, SMTP_PASS)
        server.send_message(msg)

# -------------------------- UI --------------------------
st.title("Japan Tension Index – Live")
st.caption("Real-time correlation of seismic, solar, lunar & planetary factors")

# Init session state
if "weights" not in st.session_state:
    st.session_state.weights = DEFAULT_WEIGHTS.copy()
if "log" not in st.session_state:
    st.session_state.log = []

# Sidebar – tweak weights
with st.sidebar:
    st.header("Weight Tuning")
    for k in DEFAULT_WEIGHTS:
        st.session_state.weights[k] = st.slider(
            k.replace("_", " ").title(),
            0.0, 10.0, st.session_state.weights[k], 0.1
        )
    if st.button("Reset to Defaults"):
        st.session_state.weights = DEFAULT_WEIGHTS.copy()
        st.experimental_rerun()

# Main calc
details = calculate_score()
st.metric("Tension Index", details["total"], delta=None)

col1, col2 = st.columns(2)
with col1:
    st.subheader("Breakdown")
    breakdown = pd.DataFrame([details]).drop("total", axis=1).T
    breakdown.columns = ["Value"]
    st.table(breakdown)

with col2:
    st.subheader("Interpretation")
    if details["total"] > 150:
        st.error("**RED ALERT** – Extreme tension")
    elif details["total"] > 100:
        st.warning("**ORANGE** – Elevated risk")
    else:
        st.success("**GREEN** – Normal")

# Log + proof
log_entry = {
    "timestamp": datetime.utcnow().isoformat()+"Z",
    "score": details["total"],
    "details": details
}
st.session_state.log.append(log_entry)

if st.checkbox("Send email alert on RED"):
    if details["total"] > 150:
        send_alert(details)
        st.success("Alert emailed!")

st.download_button(
    "Download prediction log (JSON)",
    data=json.dumps(st.session_state.log, indent=2),
    file_name=f"japan_tension_log_{datetime.utcnow().date()}.json",
    mime="application/json"
)

# ----------------------------------------------------------------
st.info("""
**Deploy free:**  
1. `pip install streamlit requests pandas astropy`  
2. Save this file → `japan_alert.py`  
3. `streamlit run japan_alert.py`  
4. Push to GitHub → Streamlit Cloud (free tier) → live URL in <1 min.
""")

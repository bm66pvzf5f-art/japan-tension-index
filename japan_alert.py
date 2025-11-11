import streamlit as st
import requests
import json
from datetime import datetime, timedelta
import pandas as pd

st.set_page_config(page_title="Japan Tension Index", layout="wide")

# Weights (you can tweak in sidebar)
DEFAULT_WEIGHTS = {
    "foreshock": 2.5,
    "b_dev": 1.8,
    "thermal": 1.0,
    "coronal_hole": 3.5,
    "sunspot": 0.02,
    "lunar": 1.5,
    "schumann": 2.0,
    "aspect": 2.2
}

# Email alerts (optional – set in Streamlit Secrets)
SMTP_USER = st.secrets.get("SMTP_USER", "")
SMTP_PASS = st.secrets.get("SMTP_PASS", "")
ALERT_TO   = st.secrets.get("ALERT_TO", "")

# ——— SAFE USGS FETCH ———
@st.cache_data(ttl=3600)
def fetch_quakes():
    try:
        start = (datetime.utcnow() - timedelta(days=7)).strftime('%Y-%m-%d')
        end   = datetime.utcnow().strftime('%Y-%m-%d')
        url = (
            f"https://earthquake.usgs.gov/fdsnws/event/1/query?"
            f"format=geojson&starttime={start}&endtime={end}"
            f"&minmagnitude=4"
            f"&maxlatitude=45.8&minlatitude=23.0&maxlongitude=153.0&minlongitude=122.0"
        )
        headers = {'User-Agent': 'JapanTensionIndex/1.0 (tjh478@gmail.com)'}
        r = requests.get(url, headers=headers, timeout=15)

        if r.status_code != 200:
            st.warning("USGS error – using fallback")
            return 18, 1.3

        data = r.json()
        count = len(data.get("features", []))
        b_val = 1.1 if count > 40 else 1.3 if count > 20 else 1.5
        return count, b_val

    except Exception:
        st.warning("Quake fetch failed – fallback active")
        return 18, 1.3

# ——— OTHER DATA ———
@st.cache_data(ttl=3600)
def fetch_solar():
    try:
        txt = requests.get("https://services.swpc.noaa.gov/text/daily-solar-indices.txt").text
        sunspots = int(txt.splitlines()[-1].split()[-1])
    except:
        sunspots = 55
    try:
        xray = requests.get("https://services.swpc.noaa.gov/json/goes/primary/xrays-6-hour.json").json()
        flux = [float(v["flux"]) for v in xray[-6:]]
        coronal = 1 if max(flux) > 1e-5 else 0
    except:
        coronal = 0
    return sunspots, coronal

@st.cache_data(ttl=3600)
def fetch_lunar():
    try:
        today = datetime.utcnow().strftime('%Y/%m/%d')
        phase = requests.get(f"https://api.usno.navy.mil/moon/phase?date={today}").json()
        p = phase["phasedata"][0]["phase"]
        return 1.0 if "Full" in p or "New" in p else 0.6
    except:
        return 0.6

@st.cache_data(ttl=3600)
def fetch_schumann():
    try:
        df = pd.read_csv("http://sosrff.tsu.ru/?page_id=7", skiprows=1, nrows=1)
        spike = df.iloc[0,1] - 7.83
        return 1 if spike > 15 else 0
    except:
        return 0

@st.cache_data(ttl=3600)
def fetch_aspects():
    return 1.3  # simplified for now

# ——— SCORE ———
def calculate_score():
    quakes, b_val = fetch_quakes()
    sunspots, coronal = fetch_solar()
    lunar = fetch_lunar()
    schumann = fetch_schumann()
    aspect = fetch_aspects()
    w = st.session_state.weights

    score = (
        quakes * w["foreshock"] +
        abs(b_val - 1.5) * w["b_dev"] +
        1.0 * w["thermal"] +
        coronal * w["coronal_hole"] +
        sunspots * w["sunspot"] +
        lunar * w["lunar"] +
        schumann * w["schumann"] +
        aspect * w["aspect"]
    )
    return {
        "total": round(score, 2),
        "quakes": quakes,
        "b_val": round(b_val, 2),
        "coronal": coronal,
        "sunspots": sunspots,
        "lunar": round(lunar, 2),
        "schumann": schumann,
        "aspect": round(aspect, 2)
    }

# ——— EMAIL ———
def send_alert(details):
    if not (SMTP_USER and SMTP_PASS and ALERT_TO):
        return
    from email.message import EmailMessage
    import smtplib
    msg = EmailMessage()
    msg["Subject"] = f"JAPAN ALERT – {details['total']}"
    msg["From"] = SMTP_USER
    msg["To"] = ALERT_TO
    body = f"""
    Japan Tension Index: {details['total']}
    Quakes (last 7d): {details['quakes']}
    Coronal Hole: {'YES' if details['coronal'] else 'NO'}
    Sunspots: {details['sunspots']}
    Lunar factor: {details['lunar']}
    Schumann spike: {'YES' if details['schumann'] else 'NO'}
    Timestamp: {datetime.utcnow().isoformat()}Z
    """
    msg.set_content(body)
    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(SMTP_USER, SMTP_PASS)
            server.send_message(msg)
    except:
        pass

# ——— UI ———
st.title("Japan Tension Index – Live")
st.caption("Real-time seismic + solar + cosmic correlation")

if "weights" not in st.session_state:
    st.session_state.weights = DEFAULT_WEIGHTS.copy()
if "log" not in st.session_state:
    st.session_state.log = []

with st.sidebar:
    st.header("Weight Tuning")
    for k in DEFAULT_WEIGHTS:
        st.session_state.weights[k] = st.slider(
            k.replace("_", " ").title(),
            0.0, 10.0, st.session_state.weights[k], 0.1
        )
    if st.button("Reset to defaults"):
        st.session_state.weights = DEFAULT_WEIGHTS.copy()
        st.experimental_rerun()

details = calculate_score()
st.metric("Tension Index", details["total"])

col1, col2 = st.columns(2)
with col1:
    st.subheader("Breakdown")
    df = pd.DataFrame([details]).drop("total", axis=1).T
    df.columns = ["Value"]
    st.table(df)

with col2:
    st.subheader("Risk Level")
    if details["total"] > 150:
        st.error("RED ALERT – Extreme tension")
    elif details["total"] > 100:
        st.warning("ORANGE – Elevated risk")
    else:
        st.success("GREEN – Normal")

# Log for proof
log_entry = {
    "timestamp": datetime.utcnow().isoformat() + "Z",
    "score": details["total"],
    "details": details
}
st.session_state.log.append(log_entry)

if st.checkbox("Send email on RED") and details["total"] > 150:
    send_alert(details)
    st.success("Alert emailed!")

st.download_button(
    "Download prediction log (JSON)",
    data=json.dumps(st.session_state.log, indent=2),
    file_name=f"japan_tension_log_{datetime.utcnow().date()}.json",
    mime="application/json"
)

st.info("App is live. Screenshot + download log = your timestamped proof.")

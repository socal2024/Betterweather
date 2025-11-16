import streamlit as st
import requests
import traceback
import json

# ---------------------------------------------------------
# Helper: Basic geocoder using Nominatim (OpenStreetMap)
# ---------------------------------------------------------
def geocode_location(location_text: str, debug=False):
    """
    Accepts city/state, ZIP, full address, or lat/long pair.
    Returns (lat, lon) or None, plus diagnostic info.
    """
    diagnostics = {}

    # ----------- Try lat/long directly -----------
    if "," in location_text:
        parts = [p.strip() for p in location_text.split(",")]
        if len(parts) == 2:
            try:
                lat = float(parts[0])
                lon = float(parts[1])
                diagnostics["method"] = "direct lat/lon parse"
                return (lat, lon), diagnostics
            except ValueError:
                diagnostics["latlon_parse_error"] = "Failed to parse lat/lon text"

    # ----------- Use Nominatim -----------
    url = "https://nominatim.openstreetmap.org/search"
    params = {
        "q": location_text,
        "format": "json",
        "limit": 1,
        "addressdetails": 1
    }

    headers = {
        "User-Agent": "NWS-Forecast-App/1.0 (contact@example.com)"
    }

    diagnostics["request_url"] = url
    diagnostics["request_params"] = params
    diagnostics["request_headers"] = headers

    try:
        resp = requests.get(url, params=params, headers=headers, timeout=10)
        diagnostics["status_code"] = resp.status_code
        diagnostics["response_headers"] = dict(resp.headers)

        if debug:
            st.write("### Raw Response Headers")
            st.json(dict(resp.headers))

        if resp.status_code != 200:
            diagnostics["http_error"] = f"HTTP {resp.status_code}"
            return None, diagnostics

        results = resp.json()
        diagnostics["raw_json"] = results

        if debug:
            st.write("### Raw JSON From Nominatim")
            st.json(results)

        if not results:
            diagnostics["error"] = "No results returned"
            return None, diagnostics

        lat = float(results[0]["lat"])
        lon = float(results[0]["lon"])
        diagnostics["method"] = "nominatim geocoding"

        return (lat, lon), diagnostics

    except Exception as e:
        diagnostics["exception"] = traceback.format_exc()
        return None, diagnostics


# ---------------------------------------------------------
# Streamlit UI
# ---------------------------------------------------------
st.set_page_config(page_title="Detailed NWS Forecast", layout="centered")

st.title("NWS Gridpoint Forecast Explorer")

st.write("""
Welcome!  
This tool provides highly detailed, location-specific forecasts from  
the **National Weather Service Gridpoint API**.

To begin, enter a location below.  
You can use a **city + state**, a **full address**, a **ZIP code**,  
or enter **latitude, longitude** directly.
""")

debug_mode = st.checkbox("Enable debug mode")

location_text = st.text_input(
    "Enter a location",
    placeholder="e.g., Seattle WA, 90210, 34.05 -118.25, or a full address",
)

submit = st.button("Find Location")


# ---------------------------------------------------------
# Processing
# ---------------------------------------------------------
if submit and location_text.strip():

    st.info("Processing your request…")

    with st.spinner("Resolving location…"):
        coords, diag = geocode_location(location_text.strip(), debug=debug_mode)

    st.write("---")

    # Show diagnostics if debug mode is enabled
    if debug_mode:
        st.write("### Diagnostics")
        st.json(diag)

    # Handle success
    if coords:
        lat, lon = coords
        st.success(f"Location resolved: **{lat:.4f}, {lon:.4f}**")
        st.session_state["lat"] = lat
        st.session_state["lon"] = lon
        st.write("You can now proceed to fetch the detailed NWS forecast.")

    # Handle failure
    else:
        st.error("❌ Could not resolve the location.")
        st.write("Here’s what we know:")
        st.json(diag)
        st.info("Try another format or enable debug mode for more details.")

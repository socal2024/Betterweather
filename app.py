import streamlit as st
import requests
import traceback
import json

# ---------------------------------------------------------
# Helper: US Census Geocoder
# ---------------------------------------------------------
def geocode_us_location(location_text: str, debug=False):
    """
    Geocodes a U.S. address, ZIP code, or city/state using the
    U.S. Census Geocoder (no API key required).
    Returns (lat, lon) or None and diagnostics.
    """
    diagnostics = {}

    # --- 1. Try direct lat/long parsing -----------------------
    if "," in location_text:
        parts = [p.strip() for p in location_text.split(",")]
        if len(parts) == 2:
            try:
                lat = float(parts[0])
                lon = float(parts[1])
                diagnostics["method"] = "direct lat/lon"
                return (lat, lon), diagnostics
            except ValueError:
                diagnostics["latlon_error"] = "Could not parse lat/lon"

    # --- 2. U.S. Census address geocoding ---------------------
    census_url = "https://geocoding.geo.census.gov/geocoder/locations/onelineaddress"
    params = {
        "address": location_text,
        "benchmark": "Public_AR_Current",
        "format": "json"
    }

    diagnostics["census_request_url"] = census_url
    diagnostics["census_params"] = params

    try:
        resp = requests.get(census_url, params=params, timeout=10)
        diagnostics["status_code"] = resp.status_code

        if resp.status_code != 200:
            diagnostics["http_error"] = f"HTTP {resp.status_code}"
            return None, diagnostics

        data = resp.json()
        diagnostics["raw_json"] = data

        result_list = data.get("result", {}).get("addressMatches", [])
        if result_list:
            # Take the first match
            match = result_list[0]
            coords = match["coordinates"]
            lat = coords["y"]
            lon = coords["x"]
            diagnostics["method"] = "census_address"
            return (lat, lon), diagnostics

    except Exception:
        diagnostics["exception"] = traceback.format_exc()
        return None, diagnostics

    # --- 3. ZIP or city fallback using Census "find" endpoint ---
    # ZIP & city/state geocoding:
    find_url = "https://geocoding.geo.census.gov/geocoder/locations/onelineaddress"
    params = {
        "address": location_text,
        "benchmark": "4",
        "format": "json"
    }

    diagnostics["fallback_request_url"] = find_url
    diagnostics["fallback_params"] = params

    try:
        resp = requests.get(find_url, params=params, timeout=10)
        diagnostics["fallback_status"] = resp.status_code

        if resp.status_code == 200:
            data = resp.json()
            diagnostics["fallback_json"] = data

            result_list = data.get("result", {}).get("addressMatches", [])
            if result_list:
                match = result_list[0]
                coords = match["coordinates"]
                diagnostics["method"] = "census_fallback"
                return (coords["y"], coords["x"]), diagnostics

    except Exception:
        diagnostics["fallback_exception"] = traceback.format_exc()

    # --- Final: No match ----------------------------------------
    diagnostics["error"] = "Unable to geocode with US Census"
    return None, diagnostics


# ---------------------------------------------------------
# Streamlit UI
# ---------------------------------------------------------
st.set_page_config(page_title="Detailed NWS Forecast")

st.title("NWS Gridpoint Forecast Explorer")

st.write("""
Welcome!  
Enter a **U.S. city**, **ZIP code**, **full address**, or **latitude, longitude**  
to retrieve detailed National Weather Service gridpoint forecasts.
""")

debug_mode = st.checkbox("Enable debug mode")

location_text = st.text_input(
    "Enter location",
    placeholder="e.g., 1040 Lavender Lane, La Canada CA 91011, or 34.05,-118.25",
)

submit = st.button("Find Location")


# ---------------------------------------------------------
# Processing
# ---------------------------------------------------------
if submit and location_text.strip():
    st.info("Processing…")

    with st.spinner("Resolving location…"):
        coords, diag = geocode_us_location(location_text.strip(), debug=debug_mode)

    st.write("---")

    if debug_mode:
        st.write("### Diagnostics")
        st.json(diag)

    if coords:
        lat, lon = coords
        st.success(f"Location resolved: **{lat:.5f}, {lon:.5f}**")

        st.session_state["lat"] = lat
        st.session_state["lon"] = lon
        st.write("Ready to fetch NWS gridpoint data.")
    else:
        st.error("Could not resolve the location.")
        st.json(diag)

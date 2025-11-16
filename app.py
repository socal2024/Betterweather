import streamlit as st
import requests

# ---------------------------------------------------------
# Helper: Basic geocoder using Nominatim (OpenStreetMap)
# ---------------------------------------------------------
def geocode_location(location_text: str):
    """
    Accepts city/state, ZIP, full address, or lat/long pair.
    Returns (lat, lon) or None.
    """
    # If the user typed "lat, lon", parse directly
    if "," in location_text:
        parts = [p.strip() for p in location_text.split(",")]
        if len(parts) == 2:
            try:
                lat = float(parts[0])
                lon = float(parts[1])
                return lat, lon
            except ValueError:
                pass  # fall through to full geocoding

    # Otherwise use Nominatim geocoding
    url = "https://nominatim.openstreetmap.org/search"
    params = {
        "q": location_text,
        "format": "json",
        "limit": 1,
        "addressdetails": 1
    }

    headers = {
        "User-Agent": "NWS-Forecast-App (contact@example.com)"
    }

    resp = requests.get(url, params=params, headers=headers)
    if resp.status_code != 200:
        return None

    results = resp.json()
    if not results:
        return None

    lat = float(results[0]["lat"])
    lon = float(results[0]["lon"])
    return lat, lon


# ---------------------------------------------------------
# Streamlit UI
# ---------------------------------------------------------
st.set_page_config(page_title="Detailed NWS Forecast", layout="centered")

st.title("NWS Gridpoint Forecast Explorer")

st.write("""
Welcome!  
This tool provides highly detailed, location-specific forecasts directly from  
the **National Weather Service Gridpoint API**, including temperature, dewpoint,  
cloud cover, wind details, precipitation probabilities, and much more.

To begin, enter a location below.  
You can use a **city + state**, a **full address**, a **ZIP code**,  
or enter **latitude, longitude** directly.
""")

# User input box
location_text = st.text_input(
    "Enter a location",
    placeholder="e.g., Seattle WA, 90210, 34.05 -118.25, or a full address",
)

submit = st.button("Find Location")

# When user submits
if submit and location_text.strip():
    with st.spinner("Resolving location..."):
        coords = geocode_location(location_text.strip())

    if coords:
        lat, lon = coords
        st.success(f"Location resolved: **{lat:.4f}, {lon:.4f}**")
        st.session_state["lat"] = lat
        st.session_state["lon"] = lon

        st.write("You can now proceed to retrieve the detailed NWS gridpoint forecast.")
    else:
        st.error("Couldn't resolve that location. Please try another format.")

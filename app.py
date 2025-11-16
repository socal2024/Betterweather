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
Enter a **full address**  
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


# ---------------------------------------------------------
# STEP TWO
# ---------------------------------------------------------

# ---------------------------------------------------------
# Helper: Safe GET with debugging and optional retry
# ---------------------------------------------------------
def safe_get(url, headers, debug=False, retries=1):
    diag = {"url": url}
    try:
        resp = requests.get(url, headers=headers, timeout=10)
        diag["status_code"] = resp.status_code
        diag["response_headers"] = dict(resp.headers)

        # Retry once if server-side error
        if resp.status_code >= 500 and retries > 0:
            if debug:
                st.warning(f"Retrying {url}")
            return safe_get(url, headers, debug, retries - 1)

        if resp.status_code != 200:
            diag["error"] = f"HTTP {resp.status_code}"
            return None, diag

        # Attempt JSON decoding
        try:
            data = resp.json()
            diag["success"] = True
            return data, diag
        except Exception:
            diag["error"] = "JSON decode failed"
            diag["exception"] = traceback.format_exc()
            return None, diag

    except Exception as e:
        diag["exception"] = traceback.format_exc()
        return None, diag


# ---------------------------------------------------------
# Main function: Fetch ALL NWS data from lat/lon
# ---------------------------------------------------------
def fetch_nws_from_latlon(lat, lon, debug=False):
    diagnostics = {}

    # NWS requires a genuine user-agent string
    headers = {"User-Agent": "NWS-Forecast-App/1.0 (contact@example.com)"}

    # -----------------------------------------------------
    # 1. Call the /points endpoint
    # -----------------------------------------------------
    points_url = f"https://api.weather.gov/points/{lat},{lon}"

    points_json, points_diag = safe_get(points_url, headers, debug)
    diagnostics["points"] = points_diag

    if points_json is None:
        return None, diagnostics

    props = points_json.get("properties", {})

    # Extract main metadata
    office = props.get("gridId")
    gridX = props.get("gridX")
    gridY = props.get("gridY")

    diagnostics["grid_info"] = {
        "office": office,
        "gridX": gridX,
        "gridY": gridY,
    }

    # Collect available URLs
    urls_to_fetch = {
        "forecast": props.get("forecast"),
        "forecast_hourly": props.get("forecastHourly"),
        "forecast_grid_data": props.get("forecastGridData"),
        "stations": props.get("observationStations"),
    }

    results = {"metadata": props}
    fetch_status = {}  # for user-facing success table

    # -----------------------------------------------------
    # 2. Fetch all available dependent endpoints
    # -----------------------------------------------------
    for key, url in urls_to_fetch.items():
        if url is None:
            fetch_status[key] = "Missing URL in metadata"
            continue

        data, diag = safe_get(url, headers, debug)
        diagnostics[key] = diag

        if data is not None:
            results[key] = data
            fetch_status[key] = "✔ Success"
        else:
            fetch_status[key] = f"❌ Failed ({diag.get('error', 'Unknown error')})"

    # -----------------------------------------------------
    # 3. Return dictionary containing:
    #    - detailed grid data
    #    - standard forecast
    #    - hourly forecast
    #    - stations
    #    - full metadata
    # -----------------------------------------------------
    results["fetch_status"] = fetch_status
    return results, diagnostics


# ---------------------------------------------------------
# Streamlit UI section to call the fetcher
# ---------------------------------------------------------
if "lat" in st.session_state and "lon" in st.session_state:
    st.write("### Retrieve Detailed NWS Forecast")

    debug_mode = st.checkbox("Enable NWS Debug Mode")

    if st.button("Fetch NWS Weather Data"):
        with st.spinner("Contacting NWS…"):
            nws_data, diag = fetch_nws_from_latlon(
                st.session_state["lat"],
                st.session_state["lon"],
                debug=debug_mode,
            )

        st.write("---")

        if nws_data is None:
            st.error("Failed to retrieve NWS data.")
            if debug_mode:
                st.json(diag)
        else:
            st.success("NWS data retrieved successfully!")

            # Save for future pages
            st.session_state["nws_data"] = nws_data

            # Show success indicators
            st.write("### Endpoint Fetch Status")
            st.json(nws_data["fetch_status"])

            # Optional debug dump
            if debug_mode:
                st.write("### Debug Diagnostics")
                st.json(diag)
else:
    st.info("Please resolve a location first.")

    # -----------------------------------------------------
    # STEP THREE
    # -----------------------------------------------------

from datetime import datetime, timedelta

# Ensure NWS data is loaded
if "nws_data" in st.session_state:

    forecast = st.session_state["nws_data"].get("forecast", {})
    periods = forecast.get("properties", {}).get("periods", [])

    if not periods:
        st.error("No forecast period data returned by NWS.")
    else:
        # Determine tomorrow's date (local time)
        tomorrow = (datetime.now()).date() + timedelta(days=1)

        # Try to find a forecast period matching tomorrow
        tomorrow_periods = []
        for p in periods:
            # Example startTime: "2025-01-17T06:00:00-08:00"
            try:
                start_date = datetime.fromisoformat(p["startTime"]).date()
                if start_date == tomorrow:
                    tomorrow_periods.append(p)
            except Exception:
                pass

        if not tomorrow_periods:
            st.warning("No specific forecast for tomorrow was found in the API data.")
        else:
            # Use the first matching period, usually "Tomorrow" or "Tomorrow Night"
            p = tomorrow_periods[0]

            name = p.get("name", "Tomorrow")
            short = p.get("shortForecast", "")
            temp = p.get("temperature")
            temp_unit = p.get("temperatureUnit", "F")
            wind = p.get("windSpeed", "")
            wind_dir = p.get("windDirection", "")
            detailed = p.get("detailedForecast", "")

            # Build a concise one-paragraph summary
            summary = (
                f"{name} is expected to bring {short.lower()}. "
                f"Temperatures will be around {temp}°{temp_unit}, "
                f"with winds from the {wind_dir} at {wind}. "
                f"{detailed}"
            )

            st.write("### Tomorrow's Weather Summary")
            st.write(summary)

            # Offer next steps to the user
            st.write("---")
            st.write("""
            You can request a more detailed forecast, explore specific weather data
            such as dewpoint or cloud cover, or ask a question about tomorrow's
            conditions.  
            For example, you can ask:
            - *“Will it be windy tomorrow afternoon?”*  
            - *“What’s the chance of precipitation tomorrow night?”*  
            - *“Show me the dewpoint trend tomorrow.”*
            """)
else:
    st.info("NWS data is not yet loaded. Fetch data first.")

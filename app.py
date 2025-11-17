# =========================================================
# Streamlit Hyperlocal Weather App (with TZ Fix, Debug, and Chat Fix)
# =========================================================

import streamlit as st
import requests
import traceback
import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import google.generativeai as genai

# Streamlit Config
st.set_page_config(page_title="Detailed Hyperlocal Weather Forecast")

debug_mode = st.checkbox("Enable Debug Mode (Developer Only)")
st.title("Detailed Hyperlocal Weather Forecasts")
st.write("Enter a **full address** to retrieve detailed National Weather Service gridpoint forecasts.")

# =========================================================
# Geocoding
# =========================================================

def geocode_us_location(location_text: str):
    diagnostics = {}

    # Allow latitude,longitude direct input
    if "," in location_text:
        parts = [p.strip() for p in location_text.split(",")]
        if len(parts) == 2:
            try:
                return (float(parts[0]), float(parts[1])), diagnostics
            except:
                diagnostics["error"] = "Could not parse lat/lon."

    url = "https://geocoding.geo.census.gov/geocoder/locations/onelineaddress"
    params = {
        "address": location_text,
        "benchmark": "Public_AR_Current",
        "format": "json",
    }

    try:
        resp = requests.get(url, params=params, timeout=10)
        if resp.status_code != 200:
            diagnostics["error"] = f"Geocoding API returned HTTP {resp.status_code}"
            return None, diagnostics

        data = resp.json()
        matches = data.get("result", {}).get("addressMatches", [])
        if matches:
            coords = matches[0]["coordinates"]
            return (coords["y"], coords["x"]), diagnostics

    except Exception:
        diagnostics["exception"] = traceback.format_exc()

    diagnostics["error"] = "Unable to geocode address."
    return None, diagnostics

# =========================================================
# Safe GET
# =========================================================

def safe_get(url, headers):
    diagnostics = {"url": url}
    try:
        resp = requests.get(url, headers=headers, timeout=10)
        diagnostics["status_code"] = resp.status_code

        if resp.status_code != 200:
            diagnostics["error"] = f"HTTP {resp.status_code}"
            return None, diagnostics

        try:
            return resp.json(), diagnostics
        except:
            diagnostics["error"] = "Failed to parse JSON."
            return None, diagnostics

    except Exception:
        diagnostics["exception"] = traceback.format_exc()
        return None, diagnostics

# =========================================================
# Fetch NWS Data
# =========================================================

def fetch_nws_from_latlon(lat, lon):
    diagnostics = {}
    headers = {"User-Agent": "NWS-Forecast-App/1.0 (contact@example.com)"}

    # Points endpoint
    points_url = f"https://api.weather.gov/points/{lat},{lon}"
    points_json, diag_points = safe_get(points_url, headers)
    diagnostics["points"] = diag_points

    if not points_json:
        return None, diagnostics

    props = points_json.get("properties", {})

    urls = {
        "forecast": props.get("forecast"),
        "forecast_hourly": props.get("forecastHourly"),
        "forecast_grid_data": props.get("forecastGridData"),
        "stations": props.get("observationStations"),
    }

    results = {"metadata": props, "fetch_status": {}}

    # Store the time zone for this forecast location
    results["time_zone"] = props.get("timeZone", "UTC")

    for key, url in urls.items():
        if not url:
            results["fetch_status"][key] = "Missing URL"
            continue

        data, diag = safe_get(url, headers)
        diagnostics[key] = diag

        if data:
            results[key] = data
            results["fetch_status"][key] = "✔ Success"
        else:
            results["fetch_status"][key] = "❌ Failed"

    return results, diagnostics

# =========================================================
# Reduce NWS Data (Hyperlocal 72h Window)
# =========================================================

def reduce_nws_data_for_llm(nws):
    """Keep hyperlocal gridpoint variables + 72h hourly + daily."""

    reduced = {}

    # --- DAILY ---
    daily = nws.get("forecast", {})
    reduced["daily"] = daily.get("properties", {}).get("periods", [])

    # --- HOURLY (72 HOURS) ---
    hourly = nws.get("forecast_hourly", {})
    hourly = nws.get("forecastHourly", hourly)
    periods = hourly.get("properties", {}).get("periods", [])

    tz = ZoneInfo(nws["time_zone"])
    local_now = datetime.now(tz)
    cutoff = local_now + timedelta(hours=72)

    filtered_hourly = []
    for p in periods:
        try:
            t = datetime.fromisoformat(p["startTime"])
            t_local = t.astimezone(tz)
            if t_local <= cutoff:
                filtered_hourly.append(p)
        except:
            pass

    reduced["hourly"] = filtered_hourly

    # --- GRID VARIABLES (Hyperlocal) ---
    grid = nws.get("forecast_grid_data", {})
    grid_props = grid.get("properties", {})

    keep_keys = [
        "temperature",
        "dewpoint",
        "relativeHumidity",
        "windSpeed",
        "windGust",
        "windDirection",
        "probabilityOfPrecipitation",
        "skyCover",
        "quantitativePrecipitation",
    ]

    grid_subset = {}
    for key in keep_keys:
        if key in grid_props:
            grid_subset[key] = grid_props[key]

    reduced["grid"] = grid_subset

    return reduced

# =========================================================
# User Input for Location
# =========================================================

location_text = st.text_input("Enter location", placeholder="e.g., 1 Main St, Huntington Beach, CA 92648")

if location_text.strip():
    st.info("Finding your location…")

    coords, diag = geocode_us_location(location_text.strip())

    if coords:
        lat, lon = coords
        st.success(f"Location resolved: **{lat:.5f}, {lon:.5f}**")

        st.session_state["lat"] = lat
        st.session_state["lon"] = lon

        st.info("Retrieving weather data…")
        nws_data, nws_diag = fetch_nws_from_latlon(lat, lon)

        if nws_data:
            st.session_state["nws_data"] = nws_data
            st.session_state["time_zone"] = nws_data["time_zone"]
            st.success(f"Weather data loaded (TZ = {nws_data['time_zone']})")
        else:
            st.error("Unable to retrieve NWS weather data.")
            if debug_mode:
                st.json(nws_diag)
    else:
        st.error("Could not resolve that location.")
        if debug_mode:
            st.json(diag)

# =========================================================
# Tomorrow Summary (with Local Time Zone)
# =========================================================

if "nws_data" in st.session_state:
    nws = st.session_state["nws_data"]
    tz = ZoneInfo(st.session_state["time_zone"])
    local_today = datetime.now(tz).date()
    local_tomorrow = local_today + timedelta(days=1)

    forecast = nws.get("forecast", {})
    periods = forecast.get("properties", {}).get("periods", [])

    tomorrow_periods = []
    for p in periods:
        try:
            t = datetime.fromisoformat(p["startTime"]).astimezone(tz)
            if t.date() == local_tomorrow:
                tomorrow_periods.append(p)
        except:
            pass

    if tomorrow_periods:
        p = tomorrow_periods[0]

        summary = (
            f"{p.get('name', 'Tomorrow')} will bring {p.get('shortForecast', '').lower()}. "
            f"Temperatures around {p.get('temperature')}°{p.get('temperatureUnit')}, "
            f"winds from the {p.get('windDirection')} at {p.get('windSpeed')}. "
            f"{p.get('detailedForecast')}"
        )

        st.write("### Tomorrow's Weather Summary")
        st.write(summary)

# =========================================================
# Ask a Weather Question — FIRST QUESTION ONLY
# =========================================================

if "nws_data" in st.session_state and not st.session_state.get("asked_initial_question", False):

    st.write("## Ask a Weather Question")
    user_query = st.text_input(
        "Ask a weather question",
        placeholder="e.g., Will it rain during my tennis match?",
    )

    if user_query:
        genai.configure(api_key=st.secrets["GEMINI_API_KEY"])
        model = genai.GenerativeModel("gemini-2.5-flash")

        reduced_data = reduce_nws_data_for_llm(st.session_state["nws_data"])
        nws_json_str = json.dumps(reduced_data)

        tz = ZoneInfo(st.session_state["time_zone"])
        today_str = datetime.now(tz).strftime("%A %B %d, %Y")

        system_prompt = (
            f"You are an expert meteorologist. Today is {today_str} in timezone {st.session_state['time_zone']}. "
            "Use the 72-hour hyperlocal dataset to answer accurately."
        )

        if debug_mode:
            st.write("### [DEBUG] First Gemini Call Diagnostics")
            st.write(f"Reduced JSON size: {len(nws_json_str)} chars")
            st.text(nws_json_str[:1000] + ("..." if len(nws_json_str) > 1000 else ""))

        try:
            st.write("### Answer")
            ans_box = st.empty()
            final_text = ""

            with st.spinner("Analyzing..."):
                response = model.generate_content(
                    [system_prompt, nws_json_str, f"User question: {user_query}"],
                    stream=True,
                )
                for chunk in response:
                    if hasattr(chunk, "text") and chunk.text:
                        final_text += chunk.text
                        ans_box.write(final_text)

            # Set up chat mode
            st.session_state["asked_initial_question"] = True
            st.session_state["weather_chat_history"] = [
                {"role": "user", "content": user_query},
                {"role": "assistant", "content": final_text},
            ]

        except Exception:
            st.error("Gemini request failed.")
            if debug_mode:
                st.write("### [DEBUG] Exception")
                st.code(traceback.format_exc())

# =========================================================
# CONTINUE CHAT MODE
# =========================================================

if st.session_state.get("asked_initial_question", False):

    st.write("## Continue Asking Weather Questions")

    history = st.session_state["weather_chat_history"]

    # Display chat
    for turn in history:
        with st.chat_message(turn["role"]):
            st.write(turn["content"])

    user_q = st.chat_input("Ask another weather question...")

    if user_q:
        history.append({"role": "user", "content": user_q})

        genai.configure(api_key=st.secrets["GEMINI_API_KEY"])
        model = genai.GenerativeModel("gemini-2.5-flash")

        reduced_data = reduce_nws_data_for_llm(st.session_state["nws_data"])
        nws_json_str = json.dumps(reduced_data)

        tz = ZoneInfo(st.session_state["time_zone"])
        today_str = datetime.now(tz).strftime("%A %B %d, %Y")

        # Construct transcript
        conv_lines = []
        for t in history:
            prefix = "User: " if t["role"] == "user" else "Assistant: "
            conv_lines.append(prefix + t["content"])
        conv_text = "\n".join(conv_lines)

        system_prompt = (
            f"You are an expert meteorologist. Today is {today_str} "
            f"in timezone {st.session_state['time_zone']}. Use the dataset and prior conversation."
        )

        gemini_input = [
            system_prompt,
            "Hyperlocal NWS dataset:",
            nws_json_str,
            "Conversation so far:",
            conv_text,
            f"New user question: {user_q}",
        ]

        with st.chat_message("assistant"):
            try:
                answer = ""
                ans_box = st.empty()

                with st.spinner("Analyzing..."):
                    response = model.generate_content(gemini_input, stream=True)

                    for chunk in response:
                        if hasattr(chunk, "text") and chunk.text:
                            answer += chunk.text
                            ans_box.write(answer)

                history.append({"role": "assistant", "content": answer})

            except Exception:
                st.error("Gemini request failed.")
                if debug_mode:
                    st.write("### [DEBUG] Exception")
                    st.code(traceback.format_exc())

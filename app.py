# =========================================================
# Revised Streamlit Weather App with Hyperlocal 72h Reduction
# =========================================================

import streamlit as st
import requests
import traceback
import json
from datetime import datetime, timedelta
import google.generativeai as genai

# Streamlit Setup
st.set_page_config(page_title="Detailed Hyperlocal Weather Forecast")

debug_mode = st.checkbox("Enable Debug Mode (Developer Only)")
st.title("Detailed Hyperlocal Weather Forecasts")
st.write("Enter a **full address** to retrieve detailed National Weather Service gridpoint forecasts.")

# =========================================================
# Helper: Geocoding using US Census API
# =========================================================

def geocode_us_location(location_text: str):
    diagnostics = {}

    # Allow direct lat,lon input
    if "," in location_text:
        parts = [p.strip() for p in location_text.split(",")]
        if len(parts) == 2:
            try:
                return (float(parts[0]), float(parts[1])), diagnostics
            except:
                diagnostics["error"] = "Could not parse lat/lon."

    census_url = "https://geocoding.geo.census.gov/geocoder/locations/onelineaddress"
    params = {
        "address": location_text,
        "benchmark": "Public_AR_Current",
        "format": "json",
    }

    try:
        resp = requests.get(census_url, params=params, timeout=10)
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
# Helper: Safe GET
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
# Fetch ALL NWS Data
# =========================================================

def fetch_nws_from_latlon(lat, lon):
    diagnostics = {}
    headers = {"User-Agent": "NWS-Forecast-App/1.0 (contact@example.com)"}

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
# NEW: Reduce NWS Data for Gemini (Hyperlocal 72h)
# =========================================================

def reduce_nws_data_for_llm(nws):
    """Keeps hyperlocal 2.5 km gridpoint forecast but trims unused bulk data."""

    reduced = {}

    # --- DAILY FORECAST ---
    daily = nws.get("forecast", {})
    reduced["daily"] = daily.get("properties", {}).get("periods", [])

    # --- HOURLY FORECAST (NEXT 72 HOURS) ---
    hourly = nws.get("forecast_hourly", nws.get("forecastHourly", {}))
    hourly_periods = hourly.get("properties", {}).get("periods", [])

    cutoff = datetime.utcnow() + timedelta(hours=72)
    filtered_hourly = []
    for p in hourly_periods:
        try:
            t = p["startTime"].replace("Z", "+00:00")
            if datetime.fromisoformat(t) <= cutoff:
                filtered_hourly.append(p)
        except:
            pass

    reduced["hourly"] = filtered_hourly

    # --- KEEP KEY GRIDPOINT VARIABLES ONLY ---
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
            st.success("Weather data loaded!")
        else:
            st.error("Unable to retrieve NWS weather data.")
            if debug_mode:
                st.json(nws_diag)
    else:
        st.error("Could not resolve that location.")
        if debug_mode:
            st.json(diag)

# =========================================================
# Tomorrow Summary
# =========================================================

if "nws_data" in st.session_state:
    forecast = st.session_state["nws_data"].get("forecast", {})
    periods = forecast.get("properties", {}).get("periods", [])

    if not periods:
        st.warning("No forecast period data available.")
    else:
        tomorrow = datetime.now().date() + timedelta(days=1)
        tomorrow_periods = []

        for p in periods:
            try:
                if datetime.fromisoformat(p["startTime"]).date() == tomorrow:
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
# Ask a Weather Question — SINGLE TURN
# =========================================================
if "nws_data" in st.session_state:
    st.write("## Ask a Weather Question")
    user_query = st.text_input(
        "Ask a weather question",
        placeholder="e.g., Will it rain during my tennis match?",
    )

    if user_query:
        genai.configure(api_key=st.secrets["GEMINI_API_KEY"])
        model = genai.GenerativeModel("gemini-2.5-flash")

        # Reduced hyperlocal dataset
        reduced_data = reduce_nws_data_for_llm(st.session_state["nws_data"])
        nws_json_str = json.dumps(reduced_data)

        today_str = datetime.now().strftime("%A %B %d, %Y")
        system_prompt = (
            f"You are an expert meteorologist. Today is {today_str}. "
            "Use the provided hyperlocal NWS dataset (next 72h) to answer accurately. "
            "Be quantitative when helpful."
        )

        # --- DEBUG: show diagnostics for first Gemini call ---
        if debug_mode:
            st.write("### [DEBUG] First Gemini Call Diagnostics")
            st.write(f"Length of reduced NWS JSON: {len(nws_json_str)} characters")
            st.write(f"User query: {user_query}")
            # Show a small preview of the JSON
            st.text(nws_json_str[:1000] + ("..." if len(nws_json_str) > 1000 else ""))

        try:
            response = model.generate_content(
                [system_prompt, nws_json_str, f"User question: {user_query}"],
                stream=True,
            )

            st.write("### Answer")
            ans_box = st.empty()
            final_text = ""

            for chunk in response:
                if hasattr(chunk, "text") and chunk.text:
                    final_text += chunk.text
                    ans_box.write(final_text)

            # Store that we've started Q&A and seed chat history
            st.session_state["asked_initial_question"] = True
            if "weather_chat_history" not in st.session_state:
                st.session_state["weather_chat_history"] = []
            st.session_state["weather_chat_history"].append(
                {"role": "user", "content": user_query}
            )
            st.session_state["weather_chat_history"].append(
                {"role": "assistant", "content": final_text}
            )

        except Exception as e:
            st.error("Gemini request failed on initial question.")
            if debug_mode:
                st.write("### [DEBUG] Exception in initial Gemini call")
                st.write(f"Exception type: {type(e).__name__}")
                st.code(traceback.format_exc())

# =========================================================
# CONTINUE ASKING — CHAT MODE WITH DIAGNOSTICS
# =========================================================
if st.session_state.get("asked_initial_question", False):
    st.write("## Continue Asking Weather Questions")

    if "weather_chat_history" not in st.session_state:
        st.session_state["weather_chat_history"] = []

    # Display chat history
    for turn in st.session_state["weather_chat_history"]:
        with st.chat_message(turn["role"]):
            st.write(turn["content"])

    user_q = st.chat_input("Ask another weather question...")

    if user_q:
        # Append new user message to history
        st.session_state["weather_chat_history"].append(
            {"role": "user", "content": user_q}
        )

        genai.configure(api_key=st.secrets["GEMINI_API_KEY"])
        model = genai.GenerativeModel("gemini-2.5-flash")

        # Recompute reduced hyperlocal dataset
        reduced_data = reduce_nws_data_for_llm(st.session_state["nws_data"])
        nws_json_str = json.dumps(reduced_data)

        # Build a plain-text conversation summary for Gemini
        conversation_lines = []
        for turn in st.session_state["weather_chat_history"]:
            prefix = "User: " if turn["role"] == "user" else "Assistant: "
            conversation_lines.append(prefix + turn["content"])
        conversation_text = "\n".join(conversation_lines)

        today_str = datetime.now().strftime("%A %B %d, %Y")
        system_prompt = (
            f"You are an expert meteorologist. Today is {today_str}. "
            "You will see: (1) a hyperlocal NWS dataset for the next 72h, "
            "(2) the prior conversation, and (3) the new user question. "
            "Use all of them to answer clearly and quantitatively."
        )

        # --- DEBUG: diagnostics for follow-up Gemini calls ---
        if debug_mode:
            st.write("### [DEBUG] Follow-up Gemini Call Diagnostics")
            st.write(f"Length of reduced NWS JSON: {len(nws_json_str)} characters")
            st.write(f"Conversation history length: {len(conversation_text)} characters")
            st.write(f"New user question: {user_q}")
            st.text("Conversation preview:\n" + conversation_text[:1000] +
                    ("..." if len(conversation_text) > 1000 else ""))

        # Construct content in a format Gemini expects (list of strings)
        gemini_input = [
            system_prompt,
            "NWS hyperlocal data (JSON):",
            nws_json_str,
            "Conversation so far:",
            conversation_text,
            f"New user question: {user_q}",
        ]

        with st.chat_message("assistant"):
            try:
                response = model.generate_content(gemini_input, stream=True)

                answer = ""
                ans_box = st.empty()
                for chunk in response:
                    if hasattr(chunk, "text") and chunk.text:
                        answer += chunk.text
                        ans_box.write(answer)

                # Save assistant reply back into history
                st.session_state["weather_chat_history"].append(
                    {"role": "assistant", "content": answer}
                )

            except Exception as e:
                st.error("Gemini request failed on follow-up question.")
                if debug_mode:
                    st.write("### [DEBUG] Exception in follow-up Gemini call")
                    st.write(f"Exception type: {type(e).__name__}")
                    st.code(traceback.format_exc())

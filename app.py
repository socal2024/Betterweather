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

# ---------------------------------------------------------
# STEP FOUR
# ---------------------------------------------------------

import google.generativeai as genai
from datetime import datetime

# ---------------------------------------------------------
# Gemini-Powered Weather Q&A
# ---------------------------------------------------------

st.write("## Ask a Weather Question")
st.write("""
You can ask detailed natural-language questions about the weather using
the full National Weather Service gridpoint dataset.  
Examples:
- *“Will my tennis tournament get rained out tomorrow afternoon?”*  
- *“Is fire risk higher than usual today?”*  
- *“What’s the best time to hike tomorrow?”*  
- *“Compare wind gust threats between today and tomorrow.”*  
""")

# Make sure NWS data is available
if "nws_data" not in st.session_state:
    st.info("Please fetch NWS data before asking a question.")
else:
    gemini_debug = st.checkbox("Enable Gemini Debug Mode")

    user_query = st.text_input(
        "Ask a weather question",
        placeholder="e.g., What is tomorrow's dewpoint trend?"
    )

    # Initialize Gemini
    try:
        genai.configure(api_key=st.secrets["GEMINI_API_KEY"])
        model_name = "gemini-2.5-flash"
        model = genai.GenerativeModel(model_name)
        if gemini_debug:
            st.write("### Gemini Configuration Debug")
            st.json({
                "configured": True,
                "model_name": model_name,
            })
    except Exception as e:
        st.error("Gemini API key not found or configuration failed.")
        if gemini_debug:
            st.write("### Gemini Configuration Error")
            st.code(traceback.format_exc())
        st.stop()

    if st.button("Get Answer") and user_query:
        with st.spinner("Analyzing weather data with Gemini…"):
            # Prepare system-style instructions
            today_str = datetime.now().strftime("%A %B %d, %Y")
            system_prompt = f"""
You are an expert meteorologist using detailed National Weather Service
gridpoint forecast data. Today is {today_str}. Use the provided dataset
to answer the user's question in a clear, natural, human-readable way.
Incorporate multiple weather variables such as temperature, dewpoint,
cloud cover, relative humidity, precipitation probability, wind speed,
wind gusts, hazards, and any other available fields.

Your job is to interpret numerical forecast data and deliver useful,
actionable judgments. You should mentally correlate variables, identify
time patterns, and provide deeper insight than a standard forecast.

Examples of the kinds of questions you can answer:
- "Will my tennis tournament get rained out?"
- "Is fire weather risk elevated today?"
- "What is tomorrow's weather forecast?"
- "When is the windiest period over the next 48 hours?"
- "Is it safe to hike tomorrow?"
- "Will it be more humid on Tuesday than Wednesday?"

ALWAYS ground your answer in the actual data. When relevant, cite specific
forecast periods, ranges, or time windows.
""".strip()

            # Convert NWS JSON to string (ensure serializable)
            try:
                nws_json_str = json.dumps(st.session_state["nws_data"])
            except Exception:
                st.error("Error converting NWS data to JSON.")
                if gemini_debug:
                    st.write("### JSON Serialization Error")
                    st.code(traceback.format_exc())
                st.stop()

            # Debug info about the payload we are about to send
            if gemini_debug:
                st.write("### Gemini Request Debug")
                st.json({
                    "today": today_str,
                    "nws_json_length_chars": len(nws_json_str),
                    "nws_top_level_keys": list(st.session_state["nws_data"].keys()),
                    "example_metadata_keys": list(
                        st.session_state["nws_data"]
                        .get("metadata", {})
                        .keys()
                    ) if "metadata" in st.session_state["nws_data"] else [],
                    "user_query": user_query,
                })

            # Construct the input to Gemini as a list of text parts
            # (google-generativeai does NOT use role-based dicts like OpenAI)
            contents = [
                system_prompt,
                "Here is the full NWS forecast dataset as JSON:",
                nws_json_str,
                f"User question: {user_query}",
            ]

            # Perform the streaming request
            try:
                response = model.generate_content(
                    contents,
                    stream=True,
                )

                st.write("### Answer")
                answer_container = st.empty()
                final_text = ""

                for chunk in response:
                    # chunk.text aggregates the text for that stream piece
                    if hasattr(chunk, "text") and chunk.text:
                        final_text += chunk.text
                        answer_container.write(final_text)

                if gemini_debug:
                    st.write("### Debug: Final Gemini Text")
                    st.code(final_text)

            except Exception as e:
                st.error(f"Gemini request failed: {e}")
                if gemini_debug:
                    st.write("### Gemini Exception Traceback")
                    st.code(traceback.format_exc())

# ---------------------------------------------------------
# STEP FIVE — Conversational Mode with Memory (Unlimited Follow-Ups)
# ---------------------------------------------------------

st.write("## Continue the Conversation")
st.write("""
Ask follow-up questions or explore the forecast further.  
This mode **remembers context**, so you can ask things like:
- “What about in the evening?”  
- “Will it still be windy later?”  
- “Compare that to tomorrow morning.”  
""")

# Ensure NWS data is present
if "nws_data" not in st.session_state:
    st.info("Fetch NWS data first to enable conversation mode.")
else:

    # Initialize chat history if needed
    if "chat_history" not in st.session_state:
        st.session_state["chat_history"] = []

    # Persistent input field state
    if "followup_question" not in st.session_state:
        st.session_state["followup_question"] = ""

    conv_debug = st.checkbox("Enable Conversation Debug Mode")

    # Reuse Gemini configuration
    try:
        genai.configure(api_key=st.secrets["GEMINI_API_KEY"])
        model = genai.GenerativeModel("gemini-2.5-flash")
    except Exception:
        st.error("Gemini API key missing or invalid.")
        if conv_debug:
            st.code(traceback.format_exc())
        st.stop()

    # -----------------------------------------------------
    # DISPLAY CHAT HISTORY (oldest → newest)
    # -----------------------------------------------------
    if st.session_state["chat_history"]:
        st.write("### Conversation History")
        for i, turn in enumerate(st.session_state["chat_history"], 1):
            st.markdown(f"**Q{i}:** {turn['user']}")
            st.markdown(f"**A{i}:** {turn['assistant']}")
            st.write("---")

    # -----------------------------------------------------
    # NEW QUESTION INPUT — always displayed at bottom
    # -----------------------------------------------------
    new_q = st.text_input(
        "Ask another question",
        value=st.session_state["followup_question"],
        key="followup_question",
        placeholder="e.g., What about later in the afternoon?",
    )

    ask = st.button("Ask Follow-Up")

    if ask and new_q.strip():
        with st.spinner("Thinking…"):

            # --- Build conversation context ---
            today_str = datetime.now().strftime("%A %B %d, %Y")
            system_prompt = f"""
You are an expert meteorologist using detailed National Weather Service
gridpoint forecast data. Today is {today_str}. Maintain context across
turns. Use the provided dataset to answer questions with insight.
""".strip()

            # Serialize dataset
            try:
                nws_json_str = json.dumps(st.session_state["nws_data"])
            except Exception:
                st.error("Failed to serialize NWS data.")
                if conv_debug:
                    st.code(traceback.format_exc())
                st.stop()

            # Build content list
            contents = [
                system_prompt,
                "Here is the full NWS dataset as JSON:",
                nws_json_str,
                "Conversation so far:"
            ]

            for turn in st.session_state["chat_history"]:
                contents.append(f"User: {turn['user']}")
                contents.append(f"Assistant: {turn['assistant']}")

            contents.append(f"User: {new_q}")

            # Debug info
            if conv_debug:
                st.write("### Conversation Debug")
                st.json({
                    "json_length": len(nws_json_str),
                    "num_past_turns": len(st.session_state["chat_history"]),
                    "new_question": new_q,
                })

            # --- STREAMING RESPONSE ---
            try:
                response = model.generate_content(
                    contents,
                    stream=True,
                )

                st.write("### Assistant Response")
                final_answer = ""
                answer_box = st.empty()

                for chunk in response:
                    if hasattr(chunk, "text") and chunk.text:
                        final_answer += chunk.text
                        answer_box.write(final_answer)

            except Exception as e:
                st.error("Gemini request failed.")
                if conv_debug:
                    st.code(traceback.format_exc())
                st.stop()

            # Store in history for future turns
            st.session_state["chat_history"].append({
                "user": new_q,
                "assistant": final_answer,
            })

            # Reset the input box so it's ready for the next follow-up
            st.session_state["followup_question"] = ""

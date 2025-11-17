import streamlit as st
import requests
import traceback
import json
from datetime import datetime, timedelta
import google.generativeai as genai

# =========================================================
# GLOBAL CONFIG & DEBUG TOGGLE
# =========================================================
st.set_page_config(page_title="Detailed Hyperlocal Weather Forecast")

debug_mode = st.checkbox("Enable Debug Mode (Developer Only)")

st.title("Detailed Hyperlocal Weather Forecasts")
st.write(
    """
Enter a **full address** to retrieve detailed National Weather Service gridpoint forecasts.
    """
)

# =========================================================
# HELPER: US Census Geocoder
# =========================================================
def geocode_us_location(location_text: str):
    """Geocode a U.S. address/ZIP/city using the Census Geocoder.
    Returns (lat, lon), diagnostics.
    """
    diagnostics = {"input": location_text}

    # 1) Try direct lat,lon
    if "," in location_text:
        parts = [p.strip() for p in location_text.split(",")]
        if len(parts) == 2:
            try:
                lat = float(parts[0])
                lon = float(parts[1])
                diagnostics["method"] = "direct_lat_lon"
                return (lat, lon), diagnostics
            except Exception:
                diagnostics["direct_parse_error"] = traceback.format_exc()

    # 2) Census one-line address
    census_url = "https://geocoding.geo.census.gov/geocoder/locations/onelineaddress"
    params = {
        "address": location_text,
        "benchmark": "Public_AR_Current",
        "format": "json",
    }
    diagnostics["census_url"] = census_url

    try:
        resp = requests.get(census_url, params=params, timeout=10)
        diagnostics["status_code"] = resp.status_code

        if resp.status_code != 200:
            diagnostics["error"] = f"Geocoding API returned HTTP {resp.status_code}"
            return None, diagnostics

        data = resp.json()
        diagnostics["raw_result_keys"] = list(data.get("result", {}).keys())
        matches = data.get("result", {}).get("addressMatches", [])

        if matches:
            coords = matches[0]["coordinates"]
            diagnostics["method"] = "census_address"
            return (coords["y"], coords["x"]), diagnostics

        diagnostics["error"] = "No address matches returned by geocoder."
        return None, diagnostics

    except Exception:
        diagnostics["exception"] = traceback.format_exc()
        return None, diagnostics


# =========================================================
# HELPER: Safe GET for NWS
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
            data = resp.json()
            diagnostics["success"] = True
            diagnostics["top_level_keys"] = list(data.keys())
            return data, diagnostics
        except Exception:
            diagnostics["error"] = "Failed to parse JSON response."
            diagnostics["exception"] = traceback.format_exc()
            return None, diagnostics

    except Exception:
        diagnostics["exception"] = traceback.format_exc()
        return None, diagnostics


# =========================================================
# HELPER: Fetch ALL NWS Data from Lat/Lon
# =========================================================
def fetch_nws_from_latlon(lat, lon):
    diagnostics = {"lat": lat, "lon": lon}

    headers = {"User-Agent": "NWS-Forecast-App/1.0 (contact@example.com)"}

    points_url = f"https://api.weather.gov/points/{lat},{lon}"
    points_json, points_diag = safe_get(points_url, headers)
    diagnostics["points"] = points_diag

    if points_json is None:
        diagnostics["error"] = "Failed to retrieve NWS points metadata."
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
            results["fetch_status"][key] = "Missing URL in metadata"
            continue

        data, diag = safe_get(url, headers)
        diagnostics[key] = diag

        if data:
            results[key] = data
            results["fetch_status"][key] = "✔ Success"
        else:
            results["fetch_status"][key] = f"❌ Failed"

    return results, diagnostics


# =========================================================
# LOCATION INPUT & AUTO-FETCH NWS
# =========================================================
location_text = st.text_input(
    "Enter location",
    placeholder="e.g., 1 Main St, Huntington Beach, CA 92648",
)

if location_text.strip():
    st.info("Finding your location…")
    coords, geo_diag = geocode_us_location(location_text.strip())

    if coords:
        lat, lon = coords
        st.success(f"Location resolved: **{lat:.5f}, {lon:.5f}**")
        st.session_state["lat"], st.session_state["lon"] = lat, lon

        st.info("Retrieving weather data from the National Weather Service…")
        nws_data, nws_diag = fetch_nws_from_latlon(lat, lon)

        if nws_data:
            st.session_state["nws_data"] = nws_data
            st.success("Weather data loaded successfully.")
            st.session_state["nws_diagnostics"] = nws_diag
        else:
            st.error("Unable to retrieve NWS weather data.")
            st.session_state["nws_diagnostics"] = nws_diag
    else:
        st.error("Could not resolve that location.")
        st.session_state["geo_diagnostics"] = geo_diag

# Show debug diagnostics at top-level if enabled
if debug_mode:
    st.write("### Debug: Geocoding & NWS Diagnostics")
    if "geo_diagnostics" in st.session_state:
        st.write("**Geocoding Diagnostics**")
        st.json(st.session_state["geo_diagnostics"])
    if "nws_diagnostics" in st.session_state:
        st.write("**NWS Diagnostics**")
        st.json(st.session_state["nws_diagnostics"])


# =========================================================
# TOMORROW SUMMARY
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
            except Exception:
                if debug_mode:
                    st.write("Skipped a period due to invalid startTime format.")

        if not tomorrow_periods:
            st.warning("No specific forecast for tomorrow was found.")
        else:
            p = tomorrow_periods[0]

            name = p.get("name", "Tomorrow")
            short = p.get("shortForecast", "")
            temp = p.get("temperature")
            temp_unit = p.get("temperatureUnit", "F")
            wind = p.get("windSpeed", "")
            wind_dir = p.get("windDirection", "")
            detailed = p.get("detailedForecast", "")

            summary = (
                f"{name} is expected to bring {short.lower()}. "
                f"Temperatures around {temp}°{temp_unit}, "
                f"with winds from the {wind_dir} at {wind}. "
                f"{detailed}"
            )

            st.write("### Tomorrow's Weather Summary")
            st.write(summary)


# =========================================================
# STEP: BUILD CLEAN NWS JSON FOR GEMINI (COMPRESSION POINT)
# =========================================================
def build_clean_nws_json():
    from datetime import datetime, timedelta

    raw = st.session_state.get("nws_data", {})
    cutoff = datetime.now().astimezone() + timedelta(hours=72)

    def within(dt_str):
        try:
            ts = dt_str.split("/")[0]
            return datetime.fromisoformat(ts) < cutoff
        except:
            return False

    # Filter forecast periods
    fp = raw.get("forecast", {}).get("properties", {}).get("periods", [])
    filtered_periods = [p for p in fp if within(p.get("startTime", "9999"))]

    # Filter hourly
    hourly = raw.get("forecast_hourly", {}).get("properties", {}).get("periods", [])
    filtered_hourly = [h for h in hourly if within(h.get("startTime", "9999"))]

    # Filter grid data
    grid = raw.get("forecast_grid_data", {}).get("properties", {})
    filtered_grid = {}
    for k, v in grid.items():
        if isinstance(v, dict) and "values" in v:
            filtered_vals = [x for x in v["values"] if within(x.get("validTime", "9999"))]
            if filtered_vals:
                newv = v.copy()
                newv["values"] = filtered_vals
                filtered_grid[k] = newv
        else:
            filtered_grid[k] = v

    clean = {
        "metadata": raw.get("metadata", {}),
        "forecast": {"properties": {"periods": filtered_periods}},
        "forecastHourly": {"properties": {"periods": filtered_hourly}},
        "forecastGridData": {"properties": filtered_grid},
        "stations": raw.get("stations", {}),
        "gridInfo": {
            "office": raw.get("metadata", {}).get("gridId"),
            "gridX": raw.get("metadata", {}).get("gridX"),
            "gridY": raw.get("metadata", {}).get("gridY"),
        }
    }

    return json.dumps(clean, ensure_ascii=False)():
    """Return only the clean JSON subset of the NWS data.
    Excludes diagnostics, error strings, and fetch-status details.
    This is the main compression step before sending to Gemini.
    """
    raw = st.session_state.get("nws_data", {})

    clean = {
        "metadata": raw.get("metadata", {}),
        "forecast": raw.get("forecast", {}),
        "forecastHourly": raw.get("forecast_hourly", raw.get("forecastHourly", {})),
        "forecastGridData": raw.get("forecast_grid_data", raw.get("forecastGridData", {})),
        "stations": raw.get("stations", {}),
        "gridInfo": {
            "office": raw.get("metadata", {}).get("gridId"),
            "gridX": raw.get("metadata", {}).get("gridX"),
            "gridY": raw.get("metadata", {}).get("gridY"),
        },
    }

    return json.dumps(clean, ensure_ascii=False)


# =========================================================
# PREPARE SEMANTIC SUMMARY (ONE-TIME, COMPRESSED CONTEXT)
# =========================================================
if "nws_data" in st.session_state and "nws_semantic_summary" not in st.session_state:
    st.write("## Preparing Weather Intelligence Model")
    st.info("Creating a compact internal summary for conversational weather reasoning…")

    try:
        genai.configure(api_key=st.secrets["GEMINI_API_KEY"])
    except Exception:
        st.error("Missing or invalid Gemini API key in st.secrets['GEMINI_API_KEY'].")
        if debug_mode:
            st.code(traceback.format_exc())
        # Don't proceed further
    else:
        model = genai.GenerativeModel("gemini-2.5-flash")

        with st.spinner("Analyzing detailed NWS data (compression step)…"):
            try:
                full_json_str = build_clean_nws_json()

                if debug_mode:
                    st.write("### Debug: Compression Input Diagnostics")
                    st.json(
                        {
                            "json_length_chars": len(full_json_str),
                            "json_sample_start": full_json_str[:500],
                        }
                    )

                summary_prompt = """
You are an expert meteorologist. I will provide a large, cleaned JSON dataset from
the National Weather Service gridpoint API.

Your job:
1. Read **all** data carefully.
2. Extract every weather-relevant variable and transform it into a compact,
   internal summary suitable for multi-turn question answering.
3. Include:
   - temperatures
   - dewpoint and humidity
   - cloud cover
   - precipitation probability
   - wind speed and gusts
   - timing relationships
   - hazards (fire, flood, marine, wind)
   - atmospheric patterns or notable transitions
4. Summarize the next 72 hours with enough fidelity to answer deep judgment
   questions like:
   - "Will my tennis tournament get rained out?"
   - "Is fire weather risk elevated?"
   - "What time will winds peak tomorrow?"
   - "Which day has higher humidity?"
5. Your output must be:
   - a single consolidated summary
   - ≤ 10,000 characters
   - rich enough for the model to reason from alone
                """.strip()

                response = model.generate_content(
                        [summary_prompt, full_json_str],(
                    [
                        {"role": "system", "content": summary_prompt},
                        {"role": "user", "content": full_json_str},
                    ],
                    stream=False,
                )

                semantic_summary = (response.text or "").strip()

                if not semantic_summary:
                    raise RuntimeError("Empty semantic summary returned from Gemini.")

                st.session_state["nws_semantic_summary"] = semantic_summary
                st.success("Semantic weather summary created successfully!")

                if debug_mode:
                    st.write("### Debug: Semantic Summary Diagnostics")
                    st.json(
                        {
                            "summary_length_chars": len(semantic_summary),
                            "summary_sample_start": semantic_summary[:500],
                        }
                    )

            except Exception:
                st.error("Failed to generate compressed semantic summary.")
                if debug_mode:
                    st.write("### Debug: Compression Step Exception")
                    st.code(traceback.format_exc())


# =========================================================
# ASK A WEATHER QUESTION — SINGLE TURN (USES FULL DATA)
# =========================================================
if "nws_data" in st.session_state:
    st.write("## Ask a Weather Question")
    st.write(
        """
You can ask detailed natural-language questions about the weather using
the full National Weather Service gridpoint dataset.
        """
    )

    user_query = st.text_input(
        "Ask a weather question",
        placeholder="e.g., What is tomorrow's dewpoint trend?",
        key="initial_weather_question",
    )

    if user_query:
        try:
            genai.configure(api_key=st.secrets["GEMINI_API_KEY"])
        except Exception:
            st.error("Gemini API key not found or configuration failed.")
            if debug_mode:
                st.write("### Debug: Gemini Config Error (Initial Question)")
                st.code(traceback.format_exc())
        else:
            model = genai.GenerativeModel("gemini-2.5-flash")
            today_str = datetime.now().strftime("%A %B %d, %Y")
            system_prompt = (
                f"You are an expert meteorologist. Today is {today_str}. "
                "Use the provided NWS dataset to answer in a clear, human way."
            )

            try:
                nws_json_str = json.dumps(st.session_state["nws_data"])

                if debug_mode:
                    st.write("### Debug: Initial Question Payload Diagnostics")
                    st.json(
                        {
                            "nws_json_length_chars": len(nws_json_str),
                            "user_query": user_query,
                        }
                    )

                with st.spinner("Analyzing weather data with Gemini…"):
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

                    st.session_state["asked_initial_question"] = True

            except Exception:
                st.error("Gemini request for the initial question failed.")
                if debug_mode:
                    st.write("### Debug: Initial Question Exception")
                    st.code(traceback.format_exc())


# =========================================================
# CONTINUE ASKING — ONLY IF USER HAS ASKED FIRST QUESTION
# AND SEMANTIC SUMMARY EXISTS
# =========================================================
if (
    st.session_state.get("asked_initial_question", False)
    and "nws_semantic_summary" in st.session_state
):
    st.write("## Continue Asking Weather Questions")

    if "weather_chat_history" not in st.session_state:
        st.session_state["weather_chat_history"] = []

    # Show existing history
    for turn in st.session_state["weather_chat_history"]:
        with st.chat_message(turn["role"]):
            st.write(turn["content"])

    user_q = st.chat_input("Ask another weather question...")

    if user_q:
        st.session_state["weather_chat_history"].append(
            {"role": "user", "content": user_q}
        )

        try:
            genai.configure(api_key=st.secrets["GEMINI_API_KEY"])
        except Exception:
            st.error("Gemini API key missing for conversational Q&A.")
            if debug_mode:
                st.write("### Debug: Gemini Config Error (Conversation)")
                st.code(traceback.format_exc())
        else:
            model = genai.GenerativeModel("gemini-2.5-flash")

            system_context = (
                f"You are an expert meteorologist. Today is {datetime.now().strftime('%A %B %d, %Y')}
"
                "Use the following compressed weather summary for all reasoning:

"
                f"{st.session_state['nws_semantic_summary']}

"
                "Your job: answer questions clearly and scientifically, include timing, trends, and actionable judgments."
            )

            full_context = [
                {"role": "system", "content": system_context}
            ] + st.session_state["weather_chat_history"]

            if debug_mode:
                st.write("### Debug: Conversation Model Input Diagnostics")
                st.json(
                    {
                        "num_turns": len(st.session_state["weather_chat_history"]),
                        "summary_length_chars": len(
                            st.session_state["nws_semantic_summary"]
                        ),
                        "latest_user_question": user_q,
                    }
                )

            with st.chat_message("assistant"):
                try:
                    with st.spinner(
                        "Analyzing compressed weather summary with Gemini…"
                    ):
                        response = model.generate_content(
                            full_context,
                            stream=True,
                        )

                        answer = ""
                        ans_box = st.empty()
                        for chunk in response:
                            if hasattr(chunk, "text") and chunk.text:
                                answer += chunk.text
                                ans_box.write(answer)

                        st.session_state["weather_chat_history"].append(
                            {"role": "assistant", "content": answer}
                        )

                except Exception:
                    st.error("Gemini request failed during conversational Q&A.")
                    if debug_mode:
                        st.write("### Debug: Conversation Exception")
                        st.code(traceback.format_exc())

# If user tries to converse before summary is ready
elif st.session_state.get("asked_initial_question", False) and "nws_data" in st.session_state and "nws_semantic_summary" not in st.session_state:
    st.info(
        "Semantic weather summary is still being prepared or failed. "
        "Turn on debug mode for more details if needed."
    )

import streamlit as st
import requests
import traceback
import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import google.generativeai as genai
from PIL import Image
import io

# =========================================================
# STREAMLIT CONFIG
# =========================================================
st.set_page_config(page_title="Detailed Hyperlocal Weather Forecast")

debug_mode = st.checkbox("Enable Debug Mode (Developer Only)")
st.title("Detailed Hyperlocal Weather Forecasts")
st.write("Enter a **full address** to retrieve detailed National Weather Service gridpoint forecasts.")


# =========================================================
# GEOCODING
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
# SAFE GET
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
        except Exception:
            diagnostics["error"] = "Failed to parse JSON."
            return None, diagnostics

    except Exception:
        diagnostics["exception"] = traceback.format_exc()
        return None, diagnostics


# =========================================================
# FETCH NWS DATA
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

    # Extract time zone
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
# REDUCE NWS DATA TO 72 HOURS + KEY VARIABLES
# =========================================================
def reduce_nws_data_for_llm(nws):
    reduced = {}

    # --- DAILY ---
    daily = nws.get("forecast", {})
    reduced["daily"] = daily.get("properties", {}).get("periods", [])

    # --- HOURLY (72 hours) ---
    hourly = nws.get("forecast_hourly", {})
    hourly = nws.get("forecastHourly", hourly)
    periods = hourly.get("properties", {}).get("periods", [])

    tz = ZoneInfo(nws["time_zone"])
    local_now = datetime.now(tz)
    cutoff = local_now + timedelta(hours=72)

    filtered_hourly = []
    for p in periods:
        try:
            t = datetime.fromisoformat(p["startTime"]).astimezone(tz)
            if t <= cutoff:
                filtered_hourly.append(p)
        except:
            pass

    reduced["hourly"] = filtered_hourly

    # --- GRID VARIABLES ---
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

    grid_subset = {k: grid_props[k] for k in keep_keys if k in grid_props}
    reduced["grid"] = grid_subset

    return reduced


# =========================================================
# RADAR + GOES IMAGE HELPERS
# =========================================================

def latlon_to_pixel_conus(lat, lon, img_width, img_height):
    # Valid for standard CONUS products
    lon_min, lon_max = -130, -60
    lat_max, lat_min = 55, 20

    x = (lon - lon_min) / (lon_max - lon_min) * img_width
    y = (lat_max - lat) / (lat_max - lat_min) * img_height

    return int(x), int(y)


def crop_around_location(img, lat, lon, crop_size=600):
    w, h = img.size
    px, py = latlon_to_pixel_conus(lat, lon, w, h)

    half = crop_size // 2
    left = max(px - half, 0)
    right = min(px + half, w)
    top = max(py - half, 0)
    bottom = min(py + half, h)

    return img.crop((left, top, right, bottom))


def fetch_radar_image():
    url = "https://radar.weather.gov/ridge/standard/CONUS_0.png"
    r = requests.get(url)
    return Image.open(io.BytesIO(r.content))


def fetch_goes_image():
    url = "https://cdn.star.nesdis.noaa.gov/GOES16/ABI/CONUS/GEOCOLOR/latest.jpg"
    r = requests.get(url)
    return Image.open(io.BytesIO(r.content))


def get_location_centered_weather_images(lat, lon, crop_size=600):
    radar = fetch_radar_image()
    goes = fetch_goes_image()

    radar_crop = crop_around_location(radar, lat, lon, crop_size)
    goes_crop = crop_around_location(goes, lat, lon, crop_size)

    return radar_crop, goes_crop


# =========================================================
# USER LOCATION INPUT
# =========================================================

location_text = st.text_input(
    "Enter location", 
    placeholder="e.g., 1 Main St, Huntington Beach, CA 92648"
)

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
# TOMORROW SUMMARY WITH TZ SUPPORT
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
# FIRST QUESTION TO GEMINI
# =========================================================

if "nws_data" in st.session_state and not st.session_state.get("asked_initial_question", False):

    st.write("## Ask a Weather Question")
    user_query = st.text_input("Ask a weather question", placeholder="e.g., Will it rain during my tennis match?")

    if user_query:
        genai.configure(api_key=st.secrets["GEMINI_API_KEY"])
        model = genai.GenerativeModel("gemini-2.5-flash")

        reduced_data = reduce_nws_data_for_llm(st.session_state["nws_data"])
        nws_json_str = json.dumps(reduced_data)

        # Radar + GOES images
        lat = st.session_state["lat"]
        lon = st.session_state["lon"]
        radar_img, goes_img = get_location_centered_weather_images(lat, lon)

        tz = ZoneInfo(st.session_state["time_zone"])
        today_str = datetime.now(tz).strftime("%A %B %d, %Y")

        system_prompt = (
            f"You are an expert meteorologist. Today is {today_str} in timezone {st.session_state['time_zone']}. "
            "Use the 72-hour hyperlocal dataset, radar, and satellite imagery to answer accurately."
        )

        if debug_mode:
            st.write("### [DEBUG] Gemini First Call Diagnostics")
            st.write(f"Reduced JSON chars: {len(nws_json_str)}")

        try:
            st.write("### Answer")
            ans_box = st.empty()
            final_text = ""

            with st.spinner("Analyzing..."):
                response = model.generate_content(
                    [
                        system_prompt,
                        "Hyperlocal NWS data (JSON):",
                        nws_json_str,
                        "Location-centered radar image:",
                        radar_img,
                        "Location-centered GOES GeoColor satellite image:",
                        goes_img,
                        f"User question: {user_query}",
                    ],
                    stream=True,
                )

                for chunk in response:
                    if hasattr(chunk, "text") and chunk.text:
                        final_text += chunk.text
                        ans_box.write(final_text)

            st.session_state["asked_initial_question"] = True
            st.session_state["weather_chat_history"] = [
                {"role": "user", "content": user_query},
                {"role": "assistant", "content": final_text},
            ]

        except Exception:
            st.error("Gemini request failed.")
            if debug_mode:
                st.code(traceback.format_exc())


# =========================================================
# CHAT MODE
# =========================================================

if st.session_state.get("asked_initial_question", False):

    st.write("## Continue Asking Weather Questions")

    history = st.session_state["weather_chat_history"]

    # Show chat history
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

        # Radar + GOES again for each question
        lat = st.session_state["lat"]
        lon = st.session_state["lon"]
        radar_img, goes_img = get_location_centered_weather_images(lat, lon)

        tz = ZoneInfo(st.session_state["time_zone"])
        today_str = datetime.now(tz).strftime("%A %B %d, %Y")

        conv = []
        for t in history:
            prefix = "User: " if t["role"] == "user" else "Assistant: "
            conv.append(prefix + t["content"])
        conv_text = "\n".join(conv)

        system_prompt = (
            f"You are an expert meteorologist. Today is {today_str} in timezone {st.session_state['time_zone']}. "
            "Use the NWS dataset, radar, satellite imagery, and conversation context."
        )

        gemini_input = [
            system_prompt,
            "Hyperlocal NWS data:",
            nws_json_str,
            "Radar image:",
            radar_img,
            "GOES GeoColor satellite image:",
            goes_img,
            "Conversation so far:",
            conv_text,
            f"New question: {user_q}",
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
                    st.code(traceback.format_exc())

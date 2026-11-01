import os
import re
import shutil
import subprocess
from html import escape as html_escape
from pathlib import Path
from urllib.parse import urlparse

import requests
import streamlit as st
import tempfile

# -----------------------------
# Configuration
# -----------------------------
BACKEND_URL = os.getenv("VIDEOMIND_BACKEND_URL", "http://localhost:8000")
DATA_DIR = Path(__file__).resolve().parent / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

SUPPORTED_UPLOADS = ["mp4", "mov", "mkv", "avi", "webm"]


# -----------------------------
# Helpers
# -----------------------------
def is_youtube_url(url: str) -> bool:
    try:
        host = urlparse(url).netloc.lower()
        return (
            "youtube.com" in host
            or "youtu.be" in host
            or "youtube-nocookie.com" in host
        )
    except Exception:
        return False


def clean_data_directory():
    """Remove all files so the session focuses on one video."""
    for path in DATA_DIR.iterdir():
        if path.is_file():
            try:
                path.unlink()
            except OSError:
                pass


def save_uploaded_video(uploaded_file) -> Path:
    clean_data_directory()

    if shutil.which("ffmpeg") is None:
        raise RuntimeError(
            "FFmpeg is required to process uploaded videos. "
            "Please install FFmpeg and make sure it is available in PATH."
        )

    # Save the original upload outside DATA_DIR so Pathway
    # does not detect the unprocessed file.
    suffix = Path(uploaded_file.name).suffix.lower() or ".mp4"

    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(uploaded_file.getbuffer())
        raw_path = Path(tmp.name)

    output_path = DATA_DIR / "video.mp4"

    try:
        command = [
            "ffmpeg",
            "-y",
            "-fflags", "+genpts",
            "-i", str(raw_path),

            # Keep the first video stream and optional audio stream
            "-map", "0:v:0",
            "-map", "0:a?",

            # Normalize video - faster processing
            "-c:v", "libx264",
            "-preset", "ultrafast",
            "-crf", "26",
            "-pix_fmt", "yuv420p",
            "-vf", "scale='min(1280,iw)':-2,fps=15",

            # Normalize audio
            "-c:a", "aac",
            "-ar", "48000",

            # Better MP4 compatibility
            "-movflags", "+faststart",
            "-avoid_negative_ts", "make_zero",

            str(output_path),
        ]

        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=600,
        )

        if result.returncode != 0:
            raise RuntimeError(
                "FFmpeg could not process the uploaded video.\n\n"
                + result.stderr[-3000:]
            )

        if not output_path.exists():
            raise RuntimeError("Video normalization failed: output file was not created.")

        return output_path

    finally:
        if raw_path.exists():
            raw_path.unlink()


def download_youtube_video(url: str) -> Path:
    """Download one YouTube video and normalize it to a TwelveLabs-friendly MP4."""
    clean_data_directory()

    if shutil.which("ffmpeg") is None:
        raise RuntimeError(
            "FFmpeg is required to process YouTube videos. "
            "Please install FFmpeg and make sure it is available in PATH."
        )

    # Download outside DATA_DIR so Pathway cannot process the raw AV1 file.
    temp_dir = Path(tempfile.mkdtemp(prefix="videomind_"))
    download_path = temp_dir / "youtube_raw.%(ext)s"
    output_path = DATA_DIR / "youtube_video.mp4"

    try:
        command = [
            "yt-dlp",
            "--no-playlist",
            "-f",
            "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
            "--merge-output-format",
            "mp4",
            "--no-part",
            "-o",
            str(download_path),
            url,
        ]

        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=600,
        )

        if result.returncode != 0:
            raise RuntimeError(
                "YouTube download failed.\n\n"
                + result.stderr[-3000:]
            )

        downloaded_files = list(temp_dir.glob("youtube_raw.*"))

        if not downloaded_files:
            raise RuntimeError("YouTube download completed, but no video file was found.")

        raw_video = downloaded_files[0]

        # Normalize the downloaded video for TwelveLabs.
        ffmpeg_command = [
            "ffmpeg",
            "-y",
            "-fflags", "+genpts",
            "-i", str(raw_video),

            "-map", "0:v:0",
            "-map", "0:a?",

            # TwelveLabs-friendly video
            "-c:v", "libx264",
            "-preset", "ultrafast",
            "-crf", "26",
            "-pix_fmt", "yuv420p",
            "-vf", "scale='min(1280,iw)':-2,fps=15",

            # Standard AAC audio
            "-c:a", "aac",
            "-ar", "48000",

            "-movflags", "+faststart",
            "-avoid_negative_ts", "make_zero",

            str(output_path),
        ]

        result = subprocess.run(
            ffmpeg_command,
            capture_output=True,
            text=True,
            timeout=1200,
        )

        if result.returncode != 0:
            raise RuntimeError(
                "FFmpeg could not normalize the YouTube video.\n\n"
                + result.stderr[-3000:]
            )

        if not output_path.exists():
            raise RuntimeError(
                "Video normalization failed: output file was not created."
            )

        return output_path

    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)

def backend_healthcheck() -> bool:
    try:
        requests.get(BACKEND_URL, timeout=3)
        return True
    except requests.RequestException:
        # Pathway may not expose GET /, so a connection failure is what matters.
        return False


# def ask_backend(question: str, history: list[dict]) -> str:
#     """
#     Send the current question plus recent conversation history.
#     This gives the LLM conversational context without changing app.py.
#     """
#     conversation = []
#
#     for message in history[-10:]:
#         role = message["role"]
#         content = message["content"]
#
#         if role == "user":
#             conversation.append(f"User: {content}")
#         else:
#             conversation.append(f"Assistant: {content}")
#
#     history_text = "\n".join(conversation)
#
#     prompt = f"""You are VideoMind, a helpful assistant answering questions about the currently processed video.
#
# Use the retrieved video context provided by the RAG system as the source of truth.
#
# Conversation history:
# {history_text if history_text else "(No previous conversation.)"}
#
# Current user question:
# {question}
#
# Answer naturally and concisely. Use previous conversation to resolve references such as "it", "he", "she", "that", or "the previous thing".
# Use retrieved video context as your primary source of truth.
#
# If the user asks for an estimate, approximate measurement, or visual judgment,
# provide a reasonable estimate when the video context supports one.
#
# Clearly distinguish:
# - Directly observed facts
# - Approximate visual estimates
# - Details that cannot be determined from the available video
#
# Never present an estimate as an exact measurement. If the object is not
# clearly visible or there is no reliable scale for comparison, explain
# the limitation instead of inventing a number.
#
# For short follow-up questions such as "estimate it", use the conversation
# history to identify the object being discussed.
# """
#
#     response = requests.post(
#         f"{BACKEND_URL}/v1/pw_ai_answer",
#         json={"prompt": prompt},
#         timeout=180,
#     )
#
#     response.raise_for_status()
#     data = response.json()
#
#     if isinstance(data, dict):
#         return str(data.get("response") or data.get("answer") or data)
#
#     return str(data)

def ask_backend(question: str, history: list[dict]) -> str:
    """
    Send the current question and recent conversation history
    to the backend without changing app.py.
    """
    conversation = []

    for message in history[-10:]:
        role = message["role"]
        content = message["content"]

        if role == "user":
            conversation.append(f"User: {content}")
        else:
            conversation.append(f"Assistant: {content}")

    history_text = "\n".join(conversation)

    prompt = f"""You are VideoMind, a helpful assistant answering questions
about the currently processed video.

Use the retrieved video context as your primary source of truth.

Conversation history:
{history_text if history_text else "(No previous conversation.)"}

Current user question:
{question}

Instructions:
- Answer naturally, clearly, and concisely.
- Use conversation history to resolve references such as "it", "that",
  "estimate it", or "the previous thing".
- For follow-up questions, identify the relevant object from the conversation.
- Distinguish directly observed facts from approximate visual estimates.
- If asked to estimate a size or measurement, provide a reasonable
  approximation only when the available visual evidence supports it.
- Never present an estimate as an exact measurement.
- If no reliable scale or sufficient visual evidence is available,
  explain the limitation and what additional information is needed.
- Do not invent visual details that are unsupported by the video context.
- If the retrieved context does not contain enough information to answer,
  say so honestly rather than fabricating an answer.
"""

    response = requests.post(
        f"{BACKEND_URL}/v1/pw_ai_answer",
        json={"prompt": prompt},
        timeout=600,
    )

    response.raise_for_status()

    data = response.json()
    return data.get("response", response.text).strip()


# -----------------------------
# Session state
# -----------------------------
if "messages" not in st.session_state:
    st.session_state.messages = []

if "video_path" not in st.session_state:
    st.session_state.video_path = None

if "video_source" not in st.session_state:
    st.session_state.video_source = None

if "processing" not in st.session_state:
    st.session_state.processing = False


# -----------------------------
# Page / UI
# -----------------------------
st.set_page_config(
    page_title="VideoMind",
    page_icon="🎬",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# Look & feel: a "study notebook" theme. Dotted paper, ink outlines, sticker-style
# shadows and highlighter yellow. Deliberately no hover effects anywhere.
st.markdown(
    """
    <style>
    @import url('https://fonts.googleapis.com/css2?family=Bricolage+Grotesque:opsz,wght@12..96,600..800&family=Figtree:wght@400;500;600;700&display=swap');

    :root {
        --paper: #F5F2FF;
        --ink: #1B1340;
        --soft: #4A4270;
        --muted: #6F688F;
        --violet: #6C4DFF;
        --lav: #E6E0FF;
        --sun: #FFE156;
        --sun-soft: #FFF1A1;
        --pink: #FF9CC2;
        --mint: #5BE8BC;
        --disp: 'Bricolage Grotesque', 'Trebuchet MS', sans-serif;
        --body: 'Figtree', 'Segoe UI', sans-serif;
    }

    .stApp {
        background-color: var(--paper);
        background-image: radial-gradient(#D5CCFF 1.3px, transparent 1.3px);
        background-size: 24px 24px;
        color: var(--ink);
        font-family: var(--body);
    }

    [data-testid="stDecoration"],
    [data-testid="stHeaderActionElements"],
    [data-testid="stSidebar"] { display: none !important; }

    header[data-testid="stHeader"] { background: transparent; }

    .block-container {
        max-width: 1000px;
        padding-top: 1rem;
        padding-bottom: 8rem;
    }

    .stApp [data-testid="stMarkdownContainer"],
    .stApp [data-testid="stMarkdownContainer"] li { color: var(--ink); }

    /* ---------- Top bar ---------- */
    .vm-topbar {
        display: flex;
        align-items: center;
        justify-content: space-between;
        padding: 28px 2px 10px;
    }
    .vm-brand { display: flex; align-items: center; gap: 13px; }
    .vm-title {
        font-family: var(--disp);
        font-weight: 800;
        font-size: 26px;
        letter-spacing: -0.8px;
        line-height: 1;
        color: var(--ink);
    }
    .vm-subtitle { color: var(--muted); font-size: 13px; margin-top: 4px; font-weight: 500; }
    .stApp a.vm-gh {
        width: 46px; height: 46px;
        border-radius: 50%;
        border: 2px solid var(--ink);
        background: #fff;
        box-shadow: 3px 3px 0 var(--ink);
        display: flex; align-items: center; justify-content: center;
        color: var(--ink) !important;
        text-decoration: none !important;
    }
    .vm-gh svg { width: 24px; height: 24px; fill: currentColor; }

    /* ---------- Landing hero ---------- */
    .vm-hero {
        display: grid;
        grid-template-columns: 1.05fr 1fr;
        gap: 46px;
        align-items: center;
        padding: 30px 0 22px;
    }
    .vm-h1 {
        font-family: var(--disp);
        font-weight: 800;
        font-size: clamp(40px, 6vw, 66px);
        line-height: 0.98;
        letter-spacing: -2.4px;
        color: var(--ink);
        margin-bottom: 20px;
    }
    .vm-lede {
        font-size: 18px;
        line-height: 1.55;
        color: var(--soft);
        max-width: 44ch;
        margin-bottom: 22px;
    }
    .vm-fmts { display: flex; flex-wrap: wrap; align-items: center; gap: 7px; }
    .vm-fmts-label { font-size: 13px; font-weight: 600; color: var(--muted); margin-right: 3px; }
    .vm-fmt {
        font-size: 12px; font-weight: 700;
        padding: 3px 9px;
        border: 1.5px solid var(--ink);
        border-radius: 999px;
        background: #fff;
        color: var(--ink);
    }
    .vm-fmt-yt { background: var(--pink); }

    /* Example player card */
    .vm-player {
        background: #fff;
        border: 2.5px solid var(--ink);
        border-radius: 22px;
        box-shadow: 8px 8px 0 var(--violet);
        padding: 14px 14px 16px;
        transform: rotate(1.4deg);
    }
    .vm-player-top {
        display: flex; align-items: center; justify-content: space-between;
        gap: 10px; margin-bottom: 11px;
    }
    .vm-player-file {
        font-size: 13px; font-weight: 600; color: var(--soft);
        white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
    }
    .vm-tag {
        font-size: 11px; font-weight: 700;
        background: var(--sun); color: var(--ink);
        border: 1.5px solid var(--ink); border-radius: 999px;
        padding: 1px 9px;
    }
    .vm-board {
        position: relative;
        height: 150px;
        border-radius: 14px;
        background-color: var(--ink);
        background-image:
            linear-gradient(rgba(255,255,255,.07) 1px, transparent 1px),
            linear-gradient(90deg, rgba(255,255,255,.07) 1px, transparent 1px);
        background-size: 26px 26px;
        display: flex; align-items: center; justify-content: center;
        overflow: hidden;
    }
    .vm-chalk {
        font-family: var(--disp);
        font-weight: 700;
        font-size: 22px;
        color: var(--sun);
        letter-spacing: -0.3px;
        text-align: center;
        padding: 0 14px;
    }
    .vm-play { position: absolute; right: 12px; bottom: 12px; width: 38px; height: 38px; }
    .vm-scrub { margin: 18px 4px 14px; }
    .vm-track {
        position: relative;
        height: 12px;
        border-radius: 999px;
        background: var(--lav);
        border: 2px solid var(--ink);
    }
    .vm-seg {
        position: absolute; top: -2px; bottom: -2px;
        background: #B9A9FF;
        border: 2px solid var(--ink);
        border-radius: 999px;
    }
    .vm-seg.hit { background: var(--sun); animation: vm-hit .25s 1.9s both; }
    .vm-head {
        position: absolute; top: -9px; bottom: -9px;
        width: 5px; margin-left: -2px;
        background: var(--ink);
        border-radius: 3px;
        left: 58%;
        animation: vm-scrub 1.7s .35s cubic-bezier(.2,.8,.2,1) both;
    }
    .vm-head::before {
        content: ""; position: absolute; top: -5px; left: -4px;
        width: 13px; height: 13px; border-radius: 50%;
        background: var(--pink); border: 2px solid var(--ink);
    }
    .vm-times {
        display: flex; justify-content: space-between;
        margin-top: 11px; font-size: 11px; font-weight: 600; color: var(--muted);
    }
    .vm-bubbles { display: flex; flex-direction: column; gap: 9px; }
    .vm-q, .vm-a {
        font-size: 13.5px; line-height: 1.45;
        padding: 9px 13px;
        border: 2px solid var(--ink);
        max-width: 92%;
        color: var(--ink);
    }
    .vm-q {
        align-self: flex-end;
        background: var(--sun-soft);
        border-radius: 16px 16px 4px 16px;
        font-weight: 600;
    }
    .vm-a {
        align-self: flex-start;
        background: #fff;
        border-radius: 16px 16px 16px 4px;
        animation: vm-pop .45s 2.1s cubic-bezier(.2,.9,.3,1.2) both;
    }
    @keyframes vm-scrub { from { left: 3%; } to { left: 58%; } }
    @keyframes vm-hit { from { background: #B9A9FF; } to { background: var(--sun); } }
    @keyframes vm-pop {
        from { opacity: 0; transform: translateY(8px) scale(.96); }
        to { opacity: 1; transform: none; }
    }
    @media (prefers-reduced-motion: reduce) {
        .vm-head, .vm-seg.hit, .vm-a { animation: none !important; }
    }

    /* ---------- Section headings, steps, prompts ---------- */
    .vm-section {
        font-family: var(--disp);
        font-weight: 800;
        font-size: 22px;
        letter-spacing: -0.5px;
        color: var(--ink);
        margin: 4px 2px 12px;
    }
    .vm-steps {
        display: grid;
        grid-template-columns: repeat(3, 1fr);
        gap: 18px;
        margin: 34px 0 10px;
    }
    .vm-step {
        border: 2px solid var(--ink);
        border-radius: 18px;
        box-shadow: 4px 4px 0 var(--ink);
        padding: 16px 18px 18px;
        color: var(--ink);
    }
    .vm-step.s1 { background: var(--sun); }
    .vm-step.s2 { background: var(--mint); }
    .vm-step.s3 { background: var(--pink); }
    .vm-step-n {
        width: 30px; height: 30px; border-radius: 50%;
        background: var(--ink); color: #fff;
        font-family: var(--disp); font-weight: 800; font-size: 16px;
        display: flex; align-items: center; justify-content: center;
        margin-bottom: 12px;
    }
    .vm-step-t { font-family: var(--disp); font-weight: 800; font-size: 20px; letter-spacing: -0.4px; margin-bottom: 5px; }
    .vm-step-d { font-size: 14.5px; line-height: 1.45; font-weight: 500; }

    .vm-asks { display: flex; flex-wrap: wrap; gap: 12px; margin: 6px 0 16px; }
    .vm-ask {
        background: #fff;
        border: 2px solid var(--ink);
        border-radius: 18px 18px 18px 4px;
        padding: 9px 15px;
        font-size: 14.5px; font-weight: 600;
        color: var(--ink);
    }
    .vm-ask:nth-child(even) { background: var(--lav); border-radius: 18px 18px 4px 18px; }

    /* ---------- Loaded-video state ---------- */
    .vm-now {
        display: flex; align-items: center; gap: 12px;
        background: #fff;
        border: 2px solid var(--ink);
        border-radius: 16px;
        box-shadow: 4px 4px 0 var(--ink);
        padding: 10px 14px;
        margin: 10px 0 14px;
    }
    .vm-now-icon {
        flex: 0 0 auto;
        width: 38px; height: 38px;
        border-radius: 11px;
        background: var(--violet);
        border: 2px solid var(--ink);
        display: flex; align-items: center; justify-content: center;
    }
    .vm-now-text { min-width: 0; flex: 1 1 auto; }
    .vm-now-label { font-size: 12px; font-weight: 600; color: var(--muted); }
    .vm-now-name {
        font-weight: 700; font-size: 15px; color: var(--ink);
        white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
    }
    .vm-live {
        flex: 0 0 auto;
        display: flex; align-items: center; gap: 7px;
        font-size: 13px; font-weight: 700; color: var(--ink);
        background: var(--mint);
        border: 2px solid var(--ink); border-radius: 999px;
        padding: 3px 12px 3px 10px;
    }
    .vm-dot { width: 8px; height: 8px; border-radius: 50%; background: var(--ink); display: inline-block; }

    .vm-empty {
        background: #fff;
        border: 2px dashed var(--ink);
        border-radius: 20px;
        padding: 22px 24px 18px;
        margin: 8px 0 18px;
    }
    .vm-empty-title { font-family: var(--disp); font-weight: 800; font-size: 26px; letter-spacing: -0.7px; color: var(--ink); }
    .vm-empty-sub { color: var(--soft); font-size: 15px; margin: 4px 0 14px; }
    .vm-quote {
        border-left: 5px solid var(--violet);
        background: var(--lav);
        border-radius: 4px 12px 12px 4px;
        padding: 8px 14px;
        margin-bottom: 8px;
        font-weight: 600; font-size: 14.5px; color: var(--ink);
    }

    /* ---------- Chat ---------- */
    [data-testid="stChatMessage"] {
        background: #fff;
        border: 2px solid var(--ink);
        border-radius: 18px;
        box-shadow: 4px 4px 0 var(--ink);
        padding: 14px 18px;
        margin: 14px 0;
    }
    [data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatarUser"]),
    [data-testid="stChatMessage"]:has([data-testid="chatAvatarIcon-user"]) {
        background: var(--sun-soft);
    }
    [data-testid="stChatMessageAvatarUser"],
    [data-testid="stChatMessageAvatarAssistant"],
    [data-testid="chatAvatarIcon-user"],
    [data-testid="chatAvatarIcon-assistant"] {
        border: 2px solid var(--ink);
        border-radius: 12px;
        color: var(--ink);
    }
    [data-testid="stChatMessageAvatarUser"], [data-testid="chatAvatarIcon-user"] { background: var(--sun) !important; }
    [data-testid="stChatMessageAvatarAssistant"], [data-testid="chatAvatarIcon-assistant"] { background: var(--violet) !important; color: #fff; }
    [data-testid="stChatMessage"] [data-testid="stMarkdownContainer"] {
        font-size: 15.5px; line-height: 1.65; color: var(--ink);
    }

    /* Composer */
    [data-testid="stBottom"] > div {
        background: linear-gradient(to top, var(--paper) 72%, transparent) !important;
    }
    [data-testid="stChatInput"] {
        background: #fff !important;
        border: 2.5px solid var(--ink) !important;
        border-radius: 18px !important;
        box-shadow: 5px 5px 0 var(--violet) !important;
        padding: 3px 5px !important;
    }
    [data-testid="stChatInput"] textarea {
        color: var(--ink) !important;
        background: transparent !important;
        font-family: var(--body) !important;
        font-size: 15.5px !important;
    }
    [data-testid="stChatInput"] textarea::placeholder { color: var(--muted) !important; }
    [data-testid="stChatInput"] button {
        background: var(--violet) !important;
        color: #fff !important;
        border: 2px solid var(--ink) !important;
        border-radius: 12px !important;
    }

    /* ---------- Tools row ---------- */
    .vm-dock-label {
        font-family: var(--disp);
        font-weight: 800; font-size: 19px; letter-spacing: -0.4px;
        color: var(--ink);
        margin: 14px 2px 8px;
    }

    .stApp .stButton > button,
    .stApp .stButton > button:hover,
    .stApp .stButton > button:focus:not(:active),
    .stApp [data-testid="stPopover"] > button,
    .stApp [data-testid="stPopover"] > button:hover,
    .stApp [data-testid="stPopover"] > button:focus:not(:active) {
        width: 100%;
        min-height: 50px;
        border-radius: 14px !important;
        border: 2px solid var(--ink) !important;
        background: #fff !important;
        color: var(--ink) !important;
        box-shadow: 4px 4px 0 var(--ink) !important;
        font-family: var(--body);
    }
    .stApp .stButton > button p,
    .stApp [data-testid="stPopover"] > button p { font-weight: 700; font-size: 15px; color: inherit; }
    .stApp [data-testid="stPopover"] { width: 100%; }

    .stApp .stButton > button:active,
    .stApp [data-testid="stPopover"] > button:active {
        transform: translate(3px, 3px);
        box-shadow: 1px 1px 0 var(--ink) !important;
    }
    .stApp button:focus-visible { outline: 3px solid var(--violet) !important; outline-offset: 3px; }

    /* Primary actions inside popovers */
    .stApp [data-testid="stPopoverBody"] .stButton > button,
    .stApp [data-testid="stPopoverBody"] .stButton > button:hover,
    .stApp [data-testid="stPopoverBody"] .stButton > button:focus:not(:active) {
        background: var(--violet) !important;
        color: #fff !important;
    }
    .stApp .stButton > button:disabled {
        background: var(--lav) !important;
        color: var(--muted) !important;
        box-shadow: none !important;
        opacity: .75;
    }

    [data-testid="stPopoverBody"] {
        background: #fff !important;
        border: 2.5px solid var(--ink) !important;
        border-radius: 18px !important;
        box-shadow: 6px 6px 0 var(--ink) !important;
        min-width: min(380px, 90vw);
    }
    [data-testid="stPopoverBody"] h3 {
        font-family: var(--disp); font-weight: 800; font-size: 22px;
        letter-spacing: -0.5px; color: var(--ink); padding: 0.2rem 0 0.6rem;
    }
    [data-testid="stFileUploaderDropzone"] {
        background: #FAF8FF !important;
        border: 2px dashed var(--ink) !important;
        border-radius: 14px !important;
    }
    [data-testid="stFileUploaderDropzone"] * { color: var(--ink) !important; }
    [data-testid="stFileUploaderDropzone"] button {
        background: #fff !important;
        border: 2px solid var(--ink) !important;
        border-radius: 10px !important;
    }
    .stTextInput [data-baseweb="input"],
    .stTextInput [data-baseweb="base-input"] {
        background: #FAF8FF !important;
        border: 2px solid var(--ink) !important;
        border-radius: 12px !important;
    }
    .stTextInput input { color: var(--ink) !important; background: transparent !important; }

    [data-testid="stAlert"] { border: 2px solid var(--ink); border-radius: 14px; }
    [data-testid="stCaptionContainer"],
    [data-testid="stCaptionContainer"] * { color: var(--muted) !important; font-weight: 500; }
    [data-testid="stSpinner"] p { color: var(--ink); font-weight: 600; }

    /* ---------- Mobile ---------- */
    @media (max-width: 760px) {
        .block-container { padding-left: 1rem; padding-right: 1rem; }
        .vm-hero { grid-template-columns: 1fr; gap: 30px; }
        .vm-steps { grid-template-columns: 1fr; }
        .vm-player { transform: none; }
        .vm-title { font-size: 23px; }
        .vm-subtitle { display: none; }
    }
    </style>
    """,
    unsafe_allow_html=True,
)

# Header
st.markdown(
    """
    <div class="vm-topbar">
        <div class="vm-brand">
            <svg width="48" height="48" viewBox="0 0 48 48" fill="none" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
                <rect x="5" y="5" width="39" height="34" rx="11" fill="#1B1340"/>
                <path d="M9 34 L8 44 L19 36 Z" fill="#6C4DFF" stroke="#1B1340" stroke-width="2.5" stroke-linejoin="round"/>
                <rect x="2" y="2" width="39" height="34" rx="11" fill="#6C4DFF" stroke="#1B1340" stroke-width="2.5"/>
                <path d="M16.5 12 L29.5 19 L16.5 26 Z" fill="#FFE156" stroke="#1B1340" stroke-width="2.2" stroke-linejoin="round"/>
            </svg>
            <div>
                <div class="vm-title">VideoMind</div>
                <div class="vm-subtitle">Chat with any video</div>
            </div>
        </div>
        <a class="vm-gh" href="https://github.com/Harshit-0021" target="_blank" rel="noopener" aria-label="Developer on GitHub">
            <svg viewBox="0 0 16 16" aria-hidden="true"><path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.013 8.013 0 0016 8c0-4.42-3.58-8-8-8z"/></svg>
        </a>
    </div>
    """,
    unsafe_allow_html=True,
)

# -----------------------------
# Video controls
# -----------------------------
def process_uploaded_from_ui(uploaded_file):
    try:
        with st.spinner("Uploading video..."):
            path = save_uploaded_video(uploaded_file)

        st.session_state.video_path = str(path)
        st.session_state.video_source = uploaded_file.name
        st.session_state.messages = []
        st.rerun()
    except Exception as e:
        st.error(f"Could not save video: {e}")


def process_youtube_from_ui(youtube_url):
    if not is_youtube_url(youtube_url):
        st.error("Please enter a valid YouTube URL.")
        return

    try:
        with st.spinner("Downloading YouTube video..."):
            path = download_youtube_video(youtube_url.strip())

        st.session_state.video_path = str(path)
        st.session_state.video_source = youtube_url.strip()
        st.session_state.messages = []
        st.rerun()
    except subprocess.TimeoutExpired:
        st.error("The YouTube download timed out.")
    except Exception as e:
        st.error(f"YouTube download failed:\n\n{e}")


# -----------------------------
# Composer actions (upload / YouTube / new video)
# -----------------------------
def render_tools(label: str):
    st.markdown(f'<div class="vm-dock-label">{label}</div>', unsafe_allow_html=True)

    a1, a2, spacer, a4 = st.columns([1.35, 1.35, 2.7, 1.2])

    with a1:
        with st.popover("＋  Upload video"):
            st.markdown("### Add video")
            uploaded_file = st.file_uploader(
                "Upload",
                type=SUPPORTED_UPLOADS,
                label_visibility="collapsed",
                help="Upload MP4, MOV, MKV, AVI, or WEBM.",
            )
            if uploaded_file is not None:
                if st.button("Upload & process", use_container_width=True):
                    process_uploaded_from_ui(uploaded_file)

    with a2:
        with st.popover("🔗  YouTube link"):
            st.markdown("### YouTube link")
            youtube_url = st.text_input(
                "Paste link",
                placeholder="https://youtube.com/...",
                label_visibility="collapsed",
            )
            if st.button(
                "Download & process",
                use_container_width=True,
                disabled=not youtube_url.strip(),
            ):
                process_youtube_from_ui(youtube_url)

    with a4:
        if st.session_state.video_path:
            if st.button("New video", use_container_width=True):
                st.session_state.messages = []
                st.session_state.video_path = None
                st.session_state.video_source = None
                st.rerun()


# -----------------------------
# Main chat
# -----------------------------
if not st.session_state.video_path:
    format_chips = "".join(
        f'<span class="vm-fmt">{html_escape(ext.upper())}</span>'
        for ext in SUPPORTED_UPLOADS
    )

    st.markdown(
        f"""
        <div class="vm-hero">
            <div class="vm-hero-copy">
                <div class="vm-h1" role="heading" aria-level="1">Don't rewatch it.<br>Ask it.</div>
                <div class="vm-lede">Drop in a lecture or paste a YouTube link. VideoMind finds the part that answers your question, so you can skip the scrubbing.</div>
                <div class="vm-fmts"><span class="vm-fmts-label">Works with</span>{format_chips}<span class="vm-fmt vm-fmt-yt">YouTube</span></div>
            </div>
            <div class="vm-player" aria-hidden="true">
                <div class="vm-player-top">
                    <span class="vm-player-file">lecture_12_neural_nets.mp4</span>
                    <span class="vm-tag">example</span>
                </div>
                <div class="vm-board">
                    <div class="vm-chalk">∂L/∂w = ∂L/∂y · ∂y/∂w</div>
                    <svg class="vm-play" viewBox="0 0 38 38" fill="none"><circle cx="19" cy="19" r="17" fill="#FFE156" stroke="#F5F2FF" stroke-width="2.5"/><path d="M15 12.5 L26 19 L15 25.5 Z" fill="#1B1340"/></svg>
                </div>
                <div class="vm-scrub">
                    <div class="vm-track">
                        <span class="vm-seg" style="left:6%;width:10%"></span>
                        <span class="vm-seg hit" style="left:44%;width:14%"></span>
                        <span class="vm-seg" style="left:72%;width:9%"></span>
                        <span class="vm-head"></span>
                    </div>
                    <div class="vm-times"><span>0:00</span><span>48:12</span></div>
                </div>
                <div class="vm-bubbles">
                    <div class="vm-q">Explain backprop like I'm five</div>
                    <div class="vm-a">It's the network checking its homework backwards: find the mistake at the end, then hand out the blame to every step that caused it.</div>
                </div>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    render_tools("Add a video to get started")

    st.markdown(
        """
        <div class="vm-steps">
            <div class="vm-step s1">
                <div class="vm-step-n">1</div>
                <div class="vm-step-t">Add a video</div>
                <div class="vm-step-d">Upload a file or paste a YouTube link using the buttons above.</div>
            </div>
            <div class="vm-step s2">
                <div class="vm-step-n">2</div>
                <div class="vm-step-t">Give it a minute</div>
                <div class="vm-step-d">VideoMind reads through the video so it can answer from it.</div>
            </div>
            <div class="vm-step s3">
                <div class="vm-step-n">3</div>
                <div class="vm-step-t">Start asking</div>
                <div class="vm-step-d">Type in the box at the bottom. Follow-up questions work too.</div>
            </div>
        </div>
        <div class="vm-section" style="margin-top:30px">Things students ask it</div>
        <div class="vm-asks">
            <div class="vm-ask">Summarize this lecture in 5 bullets</div>
            <div class="vm-ask">Quiz me on what I just watched</div>
            <div class="vm-ask">What formulas did they use?</div>
            <div class="vm-ask">Explain the last part like I'm new to this</div>
        </div>
        """,
        unsafe_allow_html=True,
    )
else:
    source_label = str(st.session_state.video_source or "Current video")
    if len(source_label) > 85:
        source_label = source_label[:82] + "..."

    st.markdown(
        f"""
        <div class="vm-now">
            <div class="vm-now-icon">
                <svg width="16" height="16" viewBox="0 0 16 16" aria-hidden="true"><path d="M4 2.5 L13 8 L4 13.5 Z" fill="#FFE156" stroke="#1B1340" stroke-width="1.6" stroke-linejoin="round"/></svg>
            </div>
            <div class="vm-now-text">
                <div class="vm-now-label">Chatting with</div>
                <div class="vm-now-name">{html_escape(source_label)}</div>
            </div>
            <div class="vm-live"><span class="vm-dot"></span>Loaded</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    empty_slot = st.empty()
    if not st.session_state.messages:
        empty_slot.markdown(
            """
            <div class="vm-empty">
                <div class="vm-empty-title">Your video is in. Ask away.</div>
                <div class="vm-empty-sub">Not sure where to start? Try one of these.</div>
                <div class="vm-quote">Summarize this video in 5 bullet points</div>
                <div class="vm-quote">Quiz me with 5 questions on this</div>
                <div class="vm-quote">List every term or formula mentioned</div>
            </div>
            """,
            unsafe_allow_html=True,
        )

    for message in st.session_state.messages:
        with st.chat_message(message["role"]):
            st.markdown(message["content"])

    question = st.chat_input("Ask anything about the video...")

    if question:
        empty_slot.empty()
        st.session_state.messages.append(
            {"role": "user", "content": question}
        )

        with st.chat_message("user"):
            st.markdown(question)

        with st.chat_message("assistant"):
            with st.spinner("Searching the video and generating an answer..."):
                try:
                    history_before_current = st.session_state.messages[:-1]
                    answer = ask_backend(question, history_before_current)
                    st.markdown(answer)

                    st.session_state.messages.append(
                        {"role": "assistant", "content": answer}
                    )

                except requests.HTTPError as e:
                    body = ""
                    try:
                        body = e.response.text
                    except Exception:
                        pass

                    error_message = (
                        f"Backend returned an error ({e.response.status_code}).\n\n"
                        f"{body or 'Check the Docker/Pathway terminal for the traceback.'}"
                    )
                    st.error(error_message)

                except requests.RequestException:
                    st.error(
                        "Could not connect to the Pathway backend. "
                        "Make sure the Docker container is running on port 8000."
                    )

                except Exception as e:
                    st.error(f"Something went wrong: {e}")

    render_tools("Video tools")

    if st.session_state.video_path and not st.session_state.messages:
        st.caption("Video loaded. Once Pathway finishes indexing it, you can start chatting.")

import os
import re
import shutil
import subprocess
from pathlib import Path
from urllib.parse import urlparse

import requests
import streamlit as st


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
    safe_name = Path(uploaded_file.name).name
    destination = DATA_DIR / safe_name

    with destination.open("wb") as f:
        f.write(uploaded_file.getbuffer())

    return destination


def download_youtube_video(url: str) -> Path:
    """Download one YouTube video as a single MP4 file."""
    clean_data_directory()

    output_path = DATA_DIR / "youtube_video.%(ext)s"

    command = [
        "yt-dlp",
        "--no-playlist",
        "-f",
        "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
        "--merge-output-format",
        "mp4",
        "--no-part",
        "-o",
        str(output_path),
        url,
    ]

    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=600,
    )

    if result.returncode != 0:
        raise RuntimeError(result.stderr[-3000:] or "yt-dlp failed.")

    # Find the final MP4 only.
    final_video = DATA_DIR / "youtube_video.mp4"

    if not final_video.exists():
        raise RuntimeError(
            "YouTube download finished, but youtube_video.mp4 was not created.\n\n"
            + (result.stderr[-2000:] or result.stdout[-2000:])
        )

    # Remove any leftover temporary/download files.
    for path in DATA_DIR.iterdir():
        if path.is_file() and path != final_video:
            try:
                path.unlink()
            except OSError:
                pass

    return final_video


def backend_healthcheck() -> bool:
    try:
        requests.get(BACKEND_URL, timeout=3)
        return True
    except requests.RequestException:
        # Pathway may not expose GET /, so a connection failure is what matters.
        return False


def ask_backend(question: str, history: list[dict]) -> str:
    """
    Send the current question plus recent conversation history.
    This gives the LLM conversational context without changing app.py.
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

    prompt = f"""You are VideoMind, a helpful assistant answering questions about the currently processed video.

Use the retrieved video context provided by the RAG system as the source of truth.

Conversation history:
{history_text if history_text else "(No previous conversation.)"}

Current user question:
{question}

Answer naturally and concisely. Use previous conversation to resolve references such as "it", "he", "she", "that", or "the previous thing".
Do not invent details that are not supported by the video context.
"""

    response = requests.post(
        f"{BACKEND_URL}/v1/pw_ai_answer",
        json={"prompt": prompt},
        timeout=180,
    )

    response.raise_for_status()
    data = response.json()

    if isinstance(data, dict):
        return str(data.get("response") or data.get("answer") or data)

    return str(data)


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
# Page
# -----------------------------
st.set_page_config(
    page_title="VideoMind",
    page_icon="🎬",
    layout="wide",
)

st.title("🎬 VideoMind")
st.caption("Multimodal Video RAG Assistant")

# Sidebar
with st.sidebar:
    st.header("Video")

    source = st.radio(
        "Choose video source",
        ["Upload video", "YouTube URL"],
        index=0,
    )

    if source == "Upload video":
        uploaded_file = st.file_uploader(
            "Upload a video",
            type=SUPPORTED_UPLOADS,
            help="The video will be placed in the Pathway data directory.",
        )

        if st.button(
            "📤 Process uploaded video",
            use_container_width=True,
            disabled=uploaded_file is None,
        ):
            try:
                with st.spinner("Saving video..."):
                    path = save_uploaded_video(uploaded_file)

                st.session_state.video_path = str(path)
                st.session_state.video_source = uploaded_file.name
                st.session_state.messages = []

                st.success(f"Added `{uploaded_file.name}`.")
                st.info(
                    "Pathway should detect the new file automatically. "
                    "Give it a moment before asking questions."
                )
            except Exception as e:
                st.error(f"Could not save video: {e}")

    else:
        youtube_url = st.text_input(
            "YouTube URL",
            placeholder="https://www.youtube.com/watch?v=...",
        )

        if st.button(
            "🔗 Download & process",
            use_container_width=True,
            disabled=not youtube_url.strip(),
        ):
            if not is_youtube_url(youtube_url):
                st.error("Please enter a valid YouTube URL.")
            else:
                try:
                    with st.spinner("Downloading YouTube video..."):
                        path = download_youtube_video(youtube_url.strip())

                    st.session_state.video_path = str(path)
                    st.session_state.video_source = youtube_url.strip()
                    st.session_state.messages = []

                    st.success(f"Downloaded `{path.name}`.")
                    st.info(
                        "Pathway should detect the new file automatically. "
                        "Give it a moment before asking questions."
                    )
                except subprocess.TimeoutExpired:
                    st.error("The YouTube download timed out.")
                except Exception as e:
                    st.error(f"YouTube download failed:\n\n{e}")

    st.divider()

    if st.session_state.video_source:
        st.subheader("Current video")
        st.write(st.session_state.video_source)

    if st.button("🗑️ New video / clear chat", use_container_width=True):
        st.session_state.messages = []
        st.session_state.video_path = None
        st.session_state.video_source = None
        st.rerun()

    st.divider()
    st.caption(f"Backend: {BACKEND_URL}")


# Main area
if not st.session_state.video_path:
    st.info("Upload a video or enter a YouTube URL from the sidebar to get started.")
else:
    st.success("Video loaded. Once Pathway finishes indexing it, you can start chatting.")

    # Show current chat history
    for message in st.session_state.messages:
        with st.chat_message(message["role"]):
            st.markdown(message["content"])

    question = st.chat_input("Ask anything about the video...")

    if question:
        # Show user message immediately
        st.session_state.messages.append(
            {"role": "user", "content": question}
        )

        with st.chat_message("user"):
            st.markdown(question)

        with st.chat_message("assistant"):
            with st.spinner("Searching the video and generating an answer..."):
                try:
                    # Send conversation history without duplicating the current question.
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

st.divider()
st.caption("VideoMind • Pathway + TwelveLabs + Groq")

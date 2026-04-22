import glob
import json
import os
import subprocess
import tempfile

import httpx
from faster_whisper import WhisperModel

_whisper_model: WhisperModel | None = None
_COOKIES_PATH = "/tmp/youtube-cookies.txt"

# Set to True the first time a yt-dlp call detects YouTube IP blocking.
# Skips all subsequent yt-dlp / pytubefix calls within the same process lifetime.
_youtube_blocked = False

_INVIDIOUS_INSTANCES = [
    "https://inv.nadeko.net",
    "https://invidious.privacyredirect.com",
    "https://yt.artemislena.eu",
    "https://invidious.nerdvpn.de",
    "https://iv.melmac.space",
]

_BLOCK_SIGNALS = ("sign in to confirm", "bot", "blocked", "403", "429", "not a bot")


def _mark_blocked_if_needed(stderr: str) -> None:
    global _youtube_blocked
    low = stderr.lower()
    if any(s in low for s in _BLOCK_SIGNALS):
        _youtube_blocked = True


def ytta_transcript(video_id: str, language: str) -> str | None:
    """Fetch captions via youtube_transcript_api. Uses cookies if present."""
    try:
        import requests
        from http.cookiejar import MozillaCookieJar
        from youtube_transcript_api import YouTubeTranscriptApi

        session = requests.Session()
        if os.path.exists(_COOKIES_PATH):
            jar = MozillaCookieJar(_COOKIES_PATH)
            jar.load(ignore_discard=True, ignore_expires=True)
            session.cookies = jar  # type: ignore[assignment]

        api = YouTubeTranscriptApi(http_client=session)
        fetched = api.fetch(video_id, languages=[language])
        return " ".join(s.text for s in fetched.snippets)
    except Exception:
        return None


def invidious_transcript(video_id: str, language: str) -> str | None:
    """Fetch captions via Invidious API — bypasses YouTube IP blocks entirely."""
    for instance in _INVIDIOUS_INSTANCES:
        try:
            with httpx.Client(timeout=10) as client:
                r = client.get(f"{instance}/api/v1/captions/{video_id}")
                if r.status_code != 200:
                    continue
                caps = r.json().get("captions", [])
                target = next(
                    (c for c in caps if c.get("languageCode", "").startswith(language)),
                    None,
                )
                if not target:
                    continue
                vtt_r = client.get(f"{instance}{target['url']}&format=vtt", timeout=15)
                if vtt_r.status_code != 200:
                    continue
                text = _parse_vtt_text(vtt_r.text)
                if text.strip():
                    return text
        except Exception:
            continue
    return None


def invidious_audio_whisper(video_id: str, language: str) -> str | None:
    """Stream audio via Invidious adaptive formats + transcribe with faster-whisper."""
    for instance in _INVIDIOUS_INSTANCES:
        try:
            with httpx.Client(timeout=15) as client:
                r = client.get(
                    f"{instance}/api/v1/videos/{video_id}",
                    params={"fields": "adaptiveFormats"},
                )
                if r.status_code != 200:
                    continue
                formats = r.json().get("adaptiveFormats", [])
                audio_formats = [
                    f for f in formats if f.get("type", "").startswith("audio/")
                ]
                if not audio_formats:
                    continue
                audio_formats.sort(key=lambda f: int(f.get("bitrate", 0)), reverse=True)
                audio_url = audio_formats[0].get("url")
                if not audio_url:
                    continue

            with tempfile.TemporaryDirectory() as tmpdir:
                audio_path = os.path.join(tmpdir, "audio.webm")
                with httpx.Client(timeout=300) as dl:
                    with dl.stream("GET", audio_url) as resp:
                        resp.raise_for_status()
                        with open(audio_path, "wb") as f:
                            for chunk in resp.iter_bytes(8192):
                                f.write(chunk)
                if os.path.exists(audio_path) and os.path.getsize(audio_path) > 0:
                    return _transcribe_audio_file(audio_path, language)
        except Exception:
            continue
    return None


def transcript_from_file(file_content: bytes, filename: str) -> str:
    """Parse a manually uploaded .txt or .json transcript file into plain text."""
    if filename.lower().endswith(".json"):
        try:
            data = json.loads(file_content.decode("utf-8"))
            if isinstance(data, list):
                items = data
            elif isinstance(data, dict) and isinstance(data.get("blocks"), list):
                items = data["blocks"]
            else:
                raise ValueError("JSON must be a list of {text:...} objects or an object with a 'blocks' array")
            return " ".join(
                item.get("text", "") for item in items if isinstance(item, dict)
            )
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            raise ValueError(f"Invalid JSON transcript: {e}") from e
    elif filename.lower().endswith(".txt"):
        try:
            return file_content.decode("utf-8").strip()
        except UnicodeDecodeError as e:
            raise ValueError(f"Could not decode .txt file as UTF-8: {e}") from e
    else:
        raise ValueError("Only .txt and .json transcript files are supported")


def _get_whisper() -> WhisperModel:
    global _whisper_model
    if _whisper_model is None:
        _whisper_model = WhisperModel("large-v3", device="cpu", compute_type="int8")
    return _whisper_model


def whisper_loaded() -> bool:
    return _whisper_model is not None


_YTDLP_BASE_ARGS = [
    "--js-runtimes", "node",
    "--remote-components", "ejs:github",
]


def _cookies_args() -> list[str]:
    return ["--cookies", _COOKIES_PATH] if os.path.exists(_COOKIES_PATH) else []


def _ytdlp_args() -> list[str]:
    return [*_YTDLP_BASE_ARGS, *_cookies_args()]


def extract_video_id(url: str) -> str:
    import re
    match = re.search(r"(?:v=|youtu\.be/)([A-Za-z0-9_-]{11})", url)
    return match.group(1) if match else url.split("/")[-1]


def ytdlp_transcript(url: str, language: str) -> str | None:
    if _youtube_blocked:
        return None
    with tempfile.TemporaryDirectory() as tmpdir:
        result = subprocess.run(
            [
                "yt-dlp",
                *_ytdlp_args(),
                "--write-subs", "--write-auto-subs",
                "--skip-download",
                "--sub-lang", language,
                "--sub-format", "vtt",
                "--output", f"{tmpdir}/%(id)s",
                url,
            ],
            capture_output=True, text=True, timeout=60,
        )
        _mark_blocked_if_needed(result.stderr)
        vtt_files = [f for f in os.listdir(tmpdir) if f.endswith(".vtt")]
        if not vtt_files:
            return None
        lang_files = [f for f in vtt_files if f".{language}." in f]
        vtt_path = os.path.join(tmpdir, (lang_files or vtt_files)[0])
        return _parse_vtt(vtt_path)


def _parse_vtt(path: str) -> str:
    lines = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("WEBVTT") or "-->" in line or line.isdigit():
                continue
            lines.append(line)
    return " ".join(lines)


def _parse_vtt_text(text: str) -> str:
    lines = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("WEBVTT") or "-->" in line or line.isdigit():
            continue
        lines.append(line)
    return " ".join(lines)


def _transcribe_audio_file(audio_path: str, language: str) -> str:
    model = _get_whisper()
    segments, _ = model.transcribe(audio_path, language=language, beam_size=5)
    return " ".join(seg.text for seg in segments)


def _ytdlp_download_audio(url: str, tmpdir: str) -> str | None:
    if _youtube_blocked:
        return None
    output_template = os.path.join(tmpdir, "audio.%(ext)s")
    result = subprocess.run(
        [
            "yt-dlp",
            *_ytdlp_args(),
            "--format", "bestaudio",
            "--output", output_template,
            url,
        ],
        capture_output=True, timeout=300,
    )
    _mark_blocked_if_needed(result.stderr.decode("utf-8", errors="ignore"))
    downloaded = glob.glob(os.path.join(tmpdir, "audio.*"))
    return downloaded[0] if downloaded else None


def _pytubefix_download_audio(url: str, tmpdir: str) -> str | None:
    if _youtube_blocked:
        return None
    try:
        from pytubefix import YouTube
        yt = YouTube(url, use_oauth=False, allow_oauth_cache=False)
        stream = yt.streams.filter(only_audio=True).order_by("abr").last()
        if not stream:
            return None
        out_file = stream.download(output_path=tmpdir, filename="audio_pytube")
        return out_file
    except Exception:
        return None


def whisper_transcript(url: str, language: str, progress_cb=None) -> str:
    """Download audio and transcribe with faster-whisper. Skipped if IP is blocked."""
    if _youtube_blocked:
        raise RuntimeError(
            "YouTube is blocking this VPS IP — yt-dlp/pytubefix audio download skipped. "
            "Upload a manual transcript via the dashboard instead."
        )
    with tempfile.TemporaryDirectory() as tmpdir:
        audio_path = _ytdlp_download_audio(url, tmpdir)
        if not audio_path:
            audio_path = _pytubefix_download_audio(url, tmpdir)
        if not audio_path:
            raise FileNotFoundError(
                "All audio download methods failed — YouTube is blocking this VPS IP. "
                "Upload a manual transcript via the dashboard."
            )
        if progress_cb:
            progress_cb("Transcribing with Whisper large-v3 (may take ~15 min)...")
        return _transcribe_audio_file(audio_path, language)


_YT_DATA_API_URL = "https://www.googleapis.com/youtube/v3"


def yt_api_video_info(video_id: str, api_key: str) -> dict:
    """Fetch video snippet (title, default language) + available caption track languages via Data API v3."""
    with httpx.Client(timeout=10) as client:
        r = client.get(
            f"{_YT_DATA_API_URL}/videos",
            params={"part": "snippet", "id": video_id, "key": api_key},
        )
        r.raise_for_status()
        items = r.json().get("items", [])
        if not items:
            return {}
        snippet = items[0].get("snippet", {})

        # caption track list — tells us which languages have captions
        cap_r = client.get(
            f"{_YT_DATA_API_URL}/captions",
            params={"part": "snippet", "videoId": video_id, "key": api_key},
        )
        caption_langs: list[str] = []
        if cap_r.status_code == 200:
            for item in cap_r.json().get("items", []):
                lang = item.get("snippet", {}).get("language", "")
                if lang:
                    caption_langs.append(lang)

        return {
            "title": snippet.get("title", ""),
            "default_language": snippet.get("defaultAudioLanguage") or snippet.get("defaultLanguage") or "",
            "caption_languages": caption_langs,
        }


def get_video_title(url: str, youtube_api_key: str = "") -> str:
    """Fetch video title. Uses YouTube Data API v3 first (reliable), falls back to yt-dlp."""
    video_id = extract_video_id(url)
    if youtube_api_key and video_id:
        try:
            info = yt_api_video_info(video_id, youtube_api_key)
            if info.get("title"):
                return info["title"]
        except Exception:
            pass

    result = subprocess.run(
        ["yt-dlp", *_ytdlp_args(), "--get-title", url],
        capture_output=True, text=True, timeout=30,
    )
    return result.stdout.strip() or url

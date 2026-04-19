import glob
import os
import subprocess
import tempfile
from faster_whisper import WhisperModel

_whisper_model: WhisperModel | None = None
_COOKIES_PATH = "/tmp/youtube-cookies.txt"


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
    """Return --cookies flag if the cookies file is present on disk."""
    return ["--cookies", _COOKIES_PATH] if os.path.exists(_COOKIES_PATH) else []


def _ytdlp_args() -> list[str]:
    """Base yt-dlp flags: JS runtime for n-challenge + cookies when available."""
    return [*_YTDLP_BASE_ARGS, *_cookies_args()]


def extract_video_id(url: str) -> str:
    import re
    match = re.search(r"(?:v=|youtu\.be/)([A-Za-z0-9_-]{11})", url)
    return match.group(1) if match else url.split("/")[-1]


def ytdlp_transcript(url: str, language: str) -> str | None:
    with tempfile.TemporaryDirectory() as tmpdir:
        sub_langs = f"{language},en" if language != "en" else "en"
        subprocess.run(
            [
                "yt-dlp",
                *_ytdlp_args(),
                "--write-subs", "--write-auto-subs",
                "--skip-download",
                "--sub-lang", sub_langs,
                "--sub-format", "vtt",
                "--output", f"{tmpdir}/%(id)s",
                url,
            ],
            capture_output=True, text=True, timeout=60,
        )
        vtt_files = [f for f in os.listdir(tmpdir) if f.endswith(".vtt")]
        if not vtt_files:
            return None
        vtt_path = os.path.join(tmpdir, vtt_files[0])
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


def _transcribe_audio_file(audio_path: str, language: str) -> str:
    model = _get_whisper()
    segments, _ = model.transcribe(audio_path, language=language, beam_size=5)
    return " ".join(seg.text for seg in segments)


def _ytdlp_download_audio(url: str, tmpdir: str) -> str | None:
    """Download best audio with yt-dlp (uses cookies if available). Returns path or None."""
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
    downloaded = glob.glob(os.path.join(tmpdir, "audio.*"))
    return downloaded[0] if downloaded else None


def _pytubefix_download_audio(url: str, tmpdir: str) -> str | None:
    """Download audio via pytubefix (alternative API path). Returns path or None."""
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
    """
    Download audio and transcribe with faster-whisper large-v3.
    Fallback order: yt-dlp (with cookies) -> pytubefix -> FileNotFoundError.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        audio_path = _ytdlp_download_audio(url, tmpdir)

        if not audio_path:
            # yt-dlp failed (likely IP-blocked) — try pytubefix
            audio_path = _pytubefix_download_audio(url, tmpdir)

        if not audio_path:
            raise FileNotFoundError(
                "All audio download methods failed — YouTube is blocking this VPS IP. "
                "Fix: upload YouTube cookies to /tmp/askrgv-ingestion/youtube-cookies.txt on the VPS. "
                "See plans/2026-04-19-ingestion-debug-plan.md for instructions."
            )

        if progress_cb:
            progress_cb("Transcribing with Whisper large-v3 (may take ~15 min)...")
        return _transcribe_audio_file(audio_path, language)


def get_video_title(url: str) -> str:
    result = subprocess.run(
        ["yt-dlp", *_ytdlp_args(), "--get-title", url],
        capture_output=True, text=True, timeout=30,
    )
    return result.stdout.strip() or url

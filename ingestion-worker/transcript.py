import os
import subprocess
import tempfile
from faster_whisper import WhisperModel

_whisper_model: WhisperModel | None = None


def _get_whisper() -> WhisperModel:
    global _whisper_model
    if _whisper_model is None:
        _whisper_model = WhisperModel("large-v3", device="cpu", compute_type="int8")
    return _whisper_model


def whisper_loaded() -> bool:
    return _whisper_model is not None


def extract_video_id(url: str) -> str:
    import re
    match = re.search(r"(?:v=|youtu\.be/)([A-Za-z0-9_-]{11})", url)
    return match.group(1) if match else url.split("/")[-1]


def ytdlp_transcript(url: str, language: str) -> str | None:
    with tempfile.TemporaryDirectory() as tmpdir:
        sub_langs = f"{language},en" if language != "en" else "en"
        result = subprocess.run(
            [
                "yt-dlp",
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


def whisper_transcript(url: str, language: str, progress_cb=None) -> str:
    with tempfile.TemporaryDirectory() as tmpdir:
        audio_path = os.path.join(tmpdir, "audio.webm")
        subprocess.run(
            ["yt-dlp", "--format", "bestaudio", "--output", audio_path, url],
            capture_output=True, timeout=300,
        )
        if progress_cb:
            progress_cb("Transcribing with Whisper large-v3 (may take ~15 min)...")
        model = _get_whisper()
        segments, _ = model.transcribe(audio_path, language=language, beam_size=5)
        return " ".join(seg.text for seg in segments)


def get_video_title(url: str) -> str:
    result = subprocess.run(
        ["yt-dlp", "--get-title", url],
        capture_output=True, text=True, timeout=30,
    )
    return result.stdout.strip() or url
